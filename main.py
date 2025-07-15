import os
import platform
import re
import json
import time
import psutil
import socket
import logging
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_apscheduler import APScheduler
from aliyunsdkcore.client import AcsClient
from aliyunsdkalidns.request.v20150109.DescribeDomainsRequest import DescribeDomainsRequest
from aliyunsdkalidns.request.v20150109.DescribeDomainRecordsRequest import DescribeDomainRecordsRequest
from aliyunsdkalidns.request.v20150109.DeleteDomainRecordRequest import DeleteDomainRecordRequest
from aliyunsdkalidns.request.v20150109.AddDomainRecordRequest import AddDomainRecordRequest
from aliyunsdkalidns.request.v20150109.UpdateDomainRecordRequest import UpdateDomainRecordRequest
import requests
import concurrent.futures
from gevent.pywsgi import WSGIServer
from logging.handlers import TimedRotatingFileHandler

CONFIG_FILE = "config.json"
DDNS_CONFIG_FILE = "ddns_config.json"  # 保存 DDNS 记录
LOG_FILE = "app.log"

app = Flask(__name__)
app.config['SCHEDULER_API_ENABLED'] = True
app.config['SCHEDULER_TIMEZONE'] = 'Asia/Shanghai'
scheduler = APScheduler()
scheduler.init_app(app)

# 设置日志（保留7天，每天切一个新文件）
log_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",       # 每天凌晨切割
    interval=1,            # 间隔1天
    backupCount=7,         # 最多保留7个文件
    encoding="utf-8"
)

log_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# 控制台输出
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))
logger.addHandler(console_handler)

scheduler.start()
logger.info("APScheduler started")

from flask import session

app.secret_key = os.urandom(24)  # 确保有 session 支持

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/login", methods=["GET", "POST"])
def login():
    config = load_config()
    admin_password = config.get("admin_password", "")
    if request.method == "POST":
        password = request.form.get("password")
        if password == admin_password:
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("home")
            return redirect(next_url)
        else:
            error = "密码错误"
            return render_template("login.html", error=error)
    return render_template("login.html", error=None)

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


def get_public_ip_by_interface(iface_name):
    """
    使用 curl 绑定网卡获取公网IP
    """
    public_ip_apis = [
        "https://myip.ipip.net",
        "https://ddns.oray.com/checkip",
        "https://4.ipw.cn"
    ]
    for api in public_ip_apis:
        try:
            result = subprocess.run(
                ["curl", "--interface", iface_name, "--silent", "--max-time", "5", api],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if result.returncode == 0:
                ip = extract_ip(result.stdout)
                if ip:
                    logger.info(f"通过网卡 {iface_name} 从 {api} 获取公网IP: {ip}")
                    return ip
                else:
                    logger.warning(f"{api} 返回无有效IP: {result.stdout}")
            else:
                logger.error(f"curl 访问 {api} 失败: {result.stderr.strip()}")
        except Exception as e:
            logger.error(f"调用 curl 出错: {e}")
    logger.error("所有API获取公网IP失败")
    return None

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    # 默认配置
    return {
        "admin_password": "123456"
    }


def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_ddns_config():
    if os.path.exists(DDNS_CONFIG_FILE):
        with open(DDNS_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_ddns_config(data):
    with open(DDNS_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_aliyun_client():
    config = load_config()
    access_key = config.get("aliyun_access_key")
    access_secret = config.get("aliyun_access_secret")
    if not access_key or not access_secret:
        return None
    return AcsClient(access_key, access_secret, "cn-hangzhou")

def list_domains(client):
    """
    获取所有域名列表
    """
    try:
        request = DescribeDomainsRequest()
        request.set_accept_format("json")
        response = client.do_action_with_exception(request)
        result = json.loads(response.decode("utf-8"))
        domains = result.get("Domains", {}).get("Domain", [])
        return [d["DomainName"] for d in domains]
    except Exception as e:
        logger.error(f"获取域名列表失败: {e}")
        return []

def list_records(client, domain_name, page=1, page_size=20):
    """
    获取指定域名的解析记录（支持分页）
    """
    try:
        request = DescribeDomainRecordsRequest()
        request.set_accept_format("json")
        request.set_DomainName(domain_name)
        request.set_PageNumber(page)
        request.set_PageSize(page_size)

        response = client.do_action_with_exception(request)
        result = json.loads(response.decode("utf-8"))

        total_count = result.get("TotalCount", 0)
        records = result.get("DomainRecords", {}).get("Record", [])
        return records, total_count
    except Exception as e:
        logger.error(f"获取解析记录失败: {e}")
        return [], 0

def delete_record(client, record_id):
    """
    删除指定解析记录
    """
    try:
        request = DeleteDomainRecordRequest()
        request.set_accept_format("json")
        request.set_RecordId(record_id)
        response = client.do_action_with_exception(request)
        result = json.loads(response.decode("utf-8"))
        return result.get("RequestId", "") != ""
    except Exception as e:
        logger.error(f"删除记录失败: {e}")
        return False

def get_network_interfaces():
    """
    获取系统所有网卡信息
    """
    interfaces = []
    AF_PACKET = getattr(socket, 'AF_PACKET', None)
    AF_LINK = getattr(socket, 'AF_LINK', None)

    for iface_name, addrs in psutil.net_if_addrs().items():
        ip_list = []
        mac = ""
        for addr in addrs:
            if (AF_PACKET and addr.family == AF_PACKET) or (AF_LINK and addr.family == AF_LINK):
                mac = addr.address
            elif addr.family == socket.AF_INET:
                ip_list.append(addr.address)
        display_name = iface_name
        interfaces.append({
            "name": iface_name,
            "display_name": display_name,
            "ip_list": ip_list,
            "mac": mac
        })
    return interfaces

def get_ip_for_ddns(iface_name, ip_mode):
    """
    根据网卡和 IP 类型获取当前 IP
    """
    if ip_mode == "interface_ip":
        addrs = psutil.net_if_addrs().get(iface_name, [])
        for addr in addrs:
            if addr.family == socket.AF_INET:
                return addr.address
        return None
    elif ip_mode == "public_ip":
        return get_public_ip_by_interface(iface_name)
    return None

def ddns_update_job():
    """
    定时更新DDNS解析记录
    """
    logger.info("开始执行DDNS更新任务...")
    client = get_aliyun_client()
    if not client:
        logger.warning("阿里云 AccessKey 未配置，跳过更新")
        return

    ddns_config = load_ddns_config()
    for record_id, info in ddns_config.items():
        domain = info.get("domain")
        rr = info.get("rr")
        iface = info.get("interface")
        ip_mode = info.get("ip_mode")
        last_ip = info.get("last_ip")

        current_ip = get_interface_ip(iface) if ip_mode == "interface_ip" else get_public_ip_by_interface(iface)

        if not current_ip:
            logger.warning(f"无法获取 {iface} 的IP，跳过 {rr}.{domain}")
            continue

        if current_ip == last_ip:
            logger.info(f"{rr}.{domain} IP未变化({current_ip})")
            continue

        try:
            req = UpdateDomainRecordRequest()
            req.set_accept_format("json")
            req.set_RecordId(record_id)
            req.set_RR(rr)
            req.set_Type("A")
            req.set_Value(current_ip)
            client.do_action_with_exception(req)
            logger.info(f"更新 {rr}.{domain} 成功，新IP: {current_ip}")

            # 更新配置
            ddns_config[record_id]["last_ip"] = current_ip
            ddns_config[record_id]["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_ddns_config(ddns_config)
        except Exception as e:
            logger.error(f"更新 {rr}.{domain} 失败: {e}")

def get_interface_ip(iface_name):
    """
    获取指定网卡的本地IP
    """
    addrs = psutil.net_if_addrs().get(iface_name, [])
    for addr in addrs:
        if addr.family == socket.AF_INET:
            logger.info(f"通过网卡 {iface_name} 获取本地IP: {addr.address}")
            return addr.address
    return None

# 定时任务：默认每10分钟检查一次
# 添加定时任务
config = load_config()
interval = config.get("check_interval", 600)
scheduler.add_job(
    id='ddns_update',
    func=ddns_update_job,
    trigger='interval',
    seconds=interval
)
logger.info(f"DDNS 定时任务已注册，每 {interval} 秒执行一次")
@app.route("/")
@login_required
def home():
    return redirect(url_for("records"))
from datetime import datetime

@app.route("/records", methods=["GET"])
@login_required
def records():
    client = get_aliyun_client()
    if not client:
        return redirect(url_for("settings"))

    domains = list_domains(client)
    selected_domain = request.args.get("domain") or (domains[0] if domains else None)

    # 分页参数
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))

    records, total_count = list_records(client, selected_domain, page, page_size) if selected_domain else ([], 0)

    total_pages = (total_count + page_size - 1) // page_size  # 计算总页数

    interfaces = get_network_interfaces()
    ddns_config = load_ddns_config()

    # 补充记录信息
    for r in records:
        record_id = r["RecordId"]
        timestamp_ms = r.get("UpdateTimestamp")
        r["update_time"] = datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if timestamp_ms else "-"
        r["ddns_enabled"] = record_id in ddns_config
        r["ddns_interface"] = ddns_config.get(record_id, {}).get("interface", "-")
        r["ddns_ip_mode"] = "网卡IP" if ddns_config.get(record_id, {}).get("ip_mode") == "interface_ip" else "互联网出口IP"

    return render_template(
        "records.html",
        domains=domains,
        selected_domain=selected_domain,
        dns_records=records,
        interfaces=interfaces,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        total_count=total_count
    )


@app.route("/refresh_records", methods=["POST"])
@login_required
def refresh_records():
    client = get_aliyun_client()
    if not client:
        return jsonify({"success": False, "message": "请先配置阿里云 AccessKey"})

    data = request.json
    domain_name = data.get("domain")
    page = int(data.get("page", 1))
    page_size = int(data.get("page_size", 20))

    if not domain_name:
        return jsonify({"success": False, "message": "缺少域名参数"})

    try:
        records, total_count = list_records(client, domain_name, page=page, page_size=page_size)


        # 加载 DDNS 配置
        ddns_config = load_ddns_config()

        # 处理记录数据
        for r in records:
            record_id = r["RecordId"]

            # 🆕 转换 UpdateTimestamp
            timestamp_ms = r.get("UpdateTimestamp")
            if timestamp_ms:
                dt = datetime.fromtimestamp(timestamp_ms / 1000)
                r["update_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                r["update_time"] = "-"

            if record_id in ddns_config:
                ddns_info = ddns_config[record_id]
                r["ddns_enabled"] = True
                r["ddns_interface"] = ddns_info.get("interface")
                r["ddns_ip_mode"] = "网卡IP" if ddns_info.get("ip_mode") == "interface_ip" else "互联网出口IP"
            else:
                r["ddns_enabled"] = False
                r["ddns_interface"] = "-"
                r["ddns_ip_mode"] = "-"

        return jsonify({
            "success": True,
            "records": records,
            "total_count": total_count,
            "page": page,
            "page_size": page_size
        })

    except Exception as e:
        logger.error(f"刷新解析记录失败: {e}")
        return jsonify({"success": False, "message": str(e)})

@app.route("/delete_record/<record_id>", methods=["POST"])
@login_required
def delete_record_route(record_id):
    client = get_aliyun_client()
    if not client:
        return jsonify({"success": False, "message": "阿里云 AccessKey 未配置"})

    success = delete_record(client, record_id)
    if success:
        logger.info(f"已从阿里云删除记录: {record_id}")

        # 🆕 同步删除 ddns_config 中的配置
        ddns_config = load_ddns_config()
        if record_id in ddns_config:
            del ddns_config[record_id]
            save_ddns_config(ddns_config)
            logger.info(f"已从 ddns_config.json 删除记录: {record_id}")

        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "删除解析记录失败"})



@app.route("/add_record", methods=["POST"])
@login_required
def add_record():
    client = get_aliyun_client()
    if not client:
        return jsonify({"success": False, "message": "请先配置阿里云 AccessKey"})
    try:
        data = request.json
        domain_name = data.get("domain")
        rr = data.get("rr")
        record_type = data.get("type")
        value = data.get("value")
        ttl = data.get("ttl", 600)

        req = AddDomainRecordRequest()
        req.set_accept_format("json")
        req.set_DomainName(domain_name)
        req.set_RR(rr)
        req.set_Type(record_type)
        req.set_Value(value)
        req.set_TTL(int(ttl))

        response = client.do_action_with_exception(req)
        result = json.loads(response.decode("utf-8"))
        record_id = result.get("RecordId")

        # 如果启用DDNS，保存配置
        if data.get("enable_ddns"):
            ddns_config = load_ddns_config()
            ddns_config[record_id] = {
                "domain": domain_name,
                "rr": rr,
                "interface": data.get("ddns_interface"),
                "ip_mode": data.get("ddns_ip_mode")  # "interface_ip" or "public_ip"
            }
            save_ddns_config(ddns_config)

        logger.info("新增记录返回: %s", response)
        return jsonify({"success": True})
    except Exception as e:
        logger.error("新增解析记录失败:", e)
        return jsonify({"success": False, "message": str(e)})

def extract_ip(text):
    """
    提取文本中的IPv4地址
    """
    match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", text)
    if match:
        return match.group(1)
    return None


@app.route("/get_ip", methods=["POST"])
@login_required
def get_ip():
    data = request.json
    iface_name = data.get("interface")
    ip_mode = data.get("ip_mode")

    if ip_mode == "interface_ip":
        # 直接返回网卡IP
        addrs = psutil.net_if_addrs().get(iface_name, [])
        for addr in addrs:
            if addr.family == socket.AF_INET:
                return jsonify({"success": True, "ip": addr.address})
        return jsonify({"success": False, "message": "未找到网卡IP"})

    elif ip_mode == "public_ip":
        # 通过指定网卡访问外部获取出口IP
        ip = get_public_ip_by_interface(iface_name)
        if ip:
            return jsonify({"success": True, "ip": ip})
        else:
            return jsonify({"success": False, "message": "无法通过网卡获取出口IP"})

    return jsonify({"success": False, "message": "无效的IP模式"})



@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    config = load_config()
    password_error = None
    password_success = None

    if request.method == "POST":
        # 更新阿里云配置
        config["aliyun_access_key"] = request.form["aliyun_access_key"]
        config["aliyun_access_secret"] = request.form["aliyun_access_secret"]
        config["check_interval"] = int(request.form["check_interval"])

        # 检查是否修改了密码
        current_pwd = request.form.get("current_password")
        new_pwd = request.form.get("new_password")
        confirm_pwd = request.form.get("confirm_password")

        if current_pwd or new_pwd or confirm_pwd:
            if current_pwd != config.get("admin_password"):
                password_error = "原密码不正确"
            elif not new_pwd:
                password_error = "新密码不能为空"
            elif new_pwd != confirm_pwd:
                password_error = "新密码与确认密码不一致"
            else:
                config["admin_password"] = new_pwd
                password_success = "密码已成功更新"

        save_config(config)
        return render_template("settings.html", config=config, password_error=password_error, password_success=password_success)

    return render_template("settings.html", config=config)



@app.route("/logs", methods=["GET"])
@login_required
def logs():
    try:
        with open(LOG_FILE, "r") as f:
            log_content = f.read()
        return render_template("logs.html", log_content=log_content)
    except Exception as e:
        return f"无法读取日志文件: {e}", 500


if __name__ == "__main__":

    http_server = WSGIServer(('0.0.0.0', 11151), app)
    http_server.serve_forever()
