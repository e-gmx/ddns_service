# ddns\_service

一个基于 **Flask** 的轻量级 Web 管理工具，用于 **阿里云 DNS (AliDNS) 动态域名解析 (DDNS)**。
能够定期检测本机出口 IP（支持指定网卡或公网 IP），并自动更新到阿里云 DNS 解析记录中，同时提供 Web 界面进行管理。

---

## ✨ 功能特性

* 🔑 **阿里云 AccessKey 配置**（通过 Web 界面设置并保存到 `config.json`）
* 🌐 **支持多域名解析管理**：新增 / 删除 / 查看域名记录
* 🔄 **自动 DDNS 更新**：支持定时检测 IP（默认 600 秒一次，可配置）
* 🖥️ **网卡选择**：可选本地网卡 IP 或公网出口 IP
* 📊 **Web 界面**：

  * 解析记录管理（records.html）
  * 日志查看（logs.html，支持实时刷新）
  * 配置管理（settings.html）
  * 登录页面（login.html)
* 📝 **运行日志**记录到 `app.log`

---

## 📦 环境依赖

见 `requirements.txt`：

```
Flask
aliyun-python-sdk-core
aliyun-python-sdk-alidns
psutil
requests
flask_apscheduler
gevent
```

---

## 🚀 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/e-gmx/ddns_service.git
cd ddns_service
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置阿里云账号

修改 `config.json`（或在 Web 界面填写后保存）：

```json
{
    "aliyun_access_key": "你的AccessKeyID",
    "aliyun_access_secret": "你的AccessKeySecret",
    "domain": "example.com",
    "check_interval": 600
}
```

### 4. 运行服务

```bash
python main.py
```

默认运行在 [http://127.0.0.1:11151](http://127.0.0.1:11151)

---

## 🖥️ 使用方法

1. 打开浏览器访问 [http://127.0.0.1:11151](http://127.0.0.1:11151)
2. 在 **设置页面** 填写 AccessKey 和查询间隔
3. 在 **解析记录页面**：

   * 查看已有解析记录
   * 新增 A / CNAME 记录
   * 启用 **动态域名解析 (DDNS)**，并选择网卡和 IP 模式（网卡IP / 出口IP）
4. 在 **日志页面** 查看运行日志和 DDNS 更新情况

---

## 🐳 Docker 部署

项目内包含 `Dockerfile`，可通过以下方式构建镜像：

```bash
docker build -t ddns_service .
docker run --name ddns_service --network host -d ddns_service 
```

可直接拉取镜像进行部署
```bash
docker pull registry.cn-hangzhou.aliyuncs.com/egmx/ddns_service:1.0
docker run --name ddns_service --network host -d registry.cn-hangzhou.aliyuncs.com/egmx/ddns_service:1.0
```

---

## 📁 配置文件说明

* `config.json`：保存阿里云 AccessKey、域名、查询间隔
* `ddns_config.json`：保存已启用 DDNS 的解析记录及其绑定网卡
* `app.log`：运行日志文件

---

## 🔒 注意事项

* **AccessKey** 建议使用 **子账号的专用 RAM 用户**，避免主账号泄露风险
* 默认使用 Flask 自带开发服务器，不推荐直接用于生产环境
