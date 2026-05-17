# Port Redirect CLI

一个轻量级的 TCP 端口转发 CLI 工具。监听本地端口，将入站 TCP 流量透明转发到目标 IP:Port。基于 Python asyncio 实现，零外部依赖。

## 功能特性

- **TCP 端口转发** — 将本地端口流量透明转发到任意目标地址
- **前台/后台运行** — 支持 `--daemon` 后台守护进程模式，关闭终端后持续运行
- **进程管理** — 查看、停止、重启已启动的转发服务
- **JSON 配置文件** — 支持从配置文件批量启动多个转发
- **日志管理** — 自动日志记录，支持日志轮转
- **零依赖** — 纯 Python 3.8+ 标准库实现

## 安装

```bash
# 使用 uv（推荐）
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .

# 或使用 pip
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

安装后可直接使用 `port-redirect` 命令。

## 使用方式

### 启动转发

```bash
# 前台运行
port-redirect start 8080 192.168.1.100 80 --name web-proxy

# 后台运行（关闭终端后持续执行）
port-redirect start 8080 192.168.1.100 80 --name web-proxy --daemon
```

### 管理转发服务

```bash
# 列出所有转发服务
port-redirect list

# 查看日志
port-redirect logs web-proxy

# 重启
port-redirect restart web-proxy

# 停止
port-redirect stop web-proxy
```

### 从配置文件批量启动

创建配置文件 `~/.port_redirect/config.json`：

```json
{
  "proxies": [
    {
      "name": "ssh-tunnel",
      "listen_port": 2222,
      "target_host": "10.0.0.1",
      "target_port": 22,
      "daemon": true,
      "log_level": "INFO"
    },
    {
      "name": "web-tunnel",
      "listen_port": 8080,
      "target_host": "10.0.0.1",
      "target_port": 80,
      "daemon": true,
      "log_level": "INFO"
    }
  ]
}
```

```bash
# 批量启动
port-redirect apply --daemon

# 指定配置文件路径
port-redirect apply --config /path/to/config.json --daemon
```

## 命令参考

| 命令 | 功能 |
|------|------|
| `start <port> <host> <target_port>` | 启动端口转发 |
| `stop <name>` | 停止转发服务 |
| `list` | 列出所有转发服务 |
| `restart <name>` | 重启转发服务 |
| `logs <name>` | 查看转发日志 |
| `apply` | 从 JSON 配置文件批量启动 |

### start 参数

| 参数 | 说明 |
|------|------|
| `listen_port` | 本地监听端口 |
| `target_host` | 目标主机 IP 或域名 |
| `target_port` | 目标端口 |
| `--name, -n` | 转发服务名称（默认: proxy-<端口>） |
| `--daemon, -d` | 后台运行模式 |
| `--log-level` | 日志级别: DEBUG/INFO/WARNING/ERROR |

## Demo

```bash
# 启动一个 SSH 转发（后台）
port-redirect start 20001 10.0.0.1 22 --name ssh-proxy --daemon

# 启动一个 HTTP 转发（后台）
port-redirect start 20002 10.0.0.1 8080 --name web-proxy --daemon

# 查看运行状态
port-redirect list

# 输出示例:
# Name                 Local              Target                 PID      Status     Created
# ----------------------------------------------------------------------------------------------------
# ssh-proxy            0.0.0.0:20001      10.0.0.1:22            12345    running    2026-05-16 16:00:00
# web-proxy            0.0.0.0:20002      10.0.0.1:8080          12346    running    2026-05-16 16:00:01

# 测试转发是否正常
curl -v http://localhost:20002/

# 查看日志
port-redirect logs web-proxy

# 停止服务
port-redirect stop ssh-proxy
port-redirect stop web-proxy
```

## 数据存储

所有运行时数据存储在 `~/.port_redirect/` 目录下：

```
~/.port_redirect/
├── state.json          # 运行中的转发服务状态
├── config.json         # 用户配置文件（可选）
├── logs/               # 日志文件目录
│   ├── ssh-proxy.log
│   └── web-proxy.log
└── *.pid               # PID 文件
```

## 技术架构

```
port_redirect/
├── cli.py       # 命令行入口 + 子命令处理
├── proxy.py     # asyncio TCP 代理引擎
├── daemon.py    # 后台进程管理（双 fork）
├── config.py    # JSON 状态/配置读写
├── __init__.py  # 版本信息
└── __main__.py  # python -m 入口
```

- **代理引擎**: 基于 `asyncio.start_server`，每个连接创建双向数据中继
- **后台进程**: 双 fork 脱离终端，PID 文件追踪，SIGTERM/SIGKILL 停止
- **状态管理**: JSON 文件原子写入，支持端口冲突检测和孤儿进程识别