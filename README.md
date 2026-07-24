# Web 集群管理系统

管理分布式 Web 服务器节点的工具。FastAPI + Vue 3 + WebSocket，浏览器访问。

## 项目结构

```
web_cluster_manager/
├── server/                 # FastAPI 服务端
│   ├── main.py             #   入口
│   ├── api/                #   REST + WebSocket 路由
│   ├── models/             #   ORM 模型
│   ├── schemas/            #   Pydantic 模型
│   └── core/               #   JWT / 连接管理 / 任务引擎
│
├── agent/                  # 被管节点 Agent
│   ├── main.py             #   入口（WebSocket 客户端）
│   ├── config.py           #   配置
│   ├── connection.py       #   WebSocket 连接 + 自动重连
│   ├── executor.py         #   命令执行器
│   ├── monitor.py          #   系统监控
│   └── updater.py          #   自更新
│
├── web-ui/                 # Vue 3 前端
│   └── src/
│       ├── views/          #   页面（Dashboard / Nodes / Login 等）
│       ├── api/            #   Axios 封装
│       ├── stores/         #   Pinia 状态
│       └── composables/    #   useWebSocket
│
├── scripts/                # 启动脚本
│   ├── setup.bat           #   首次安装依赖
│   ├── start-dev.bat       #   一键启动全部
│   ├── start-server.bat    #   单独启动服务端
│   ├── start-agent.bat     #   单独启动 Agent
│   ├── start-webui.bat     #   单独启动前端
│   └── stop.bat            #   停止全部
│
├── docs/                   # 设计文档
├── .gitignore
├── LICENSE
└── README.md
```

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+

### 一键启动

```
双击 scripts\setup.bat       ← 首次运行：安装依赖
双击 scripts\start-dev.bat   ← 启动系统
```

首次启动前必须设置至少 12 位的管理员密码，系统不再提供默认密码：

```powershell
$env:WCM_BOOTSTRAP_ADMIN_PASSWORD = "请替换为随机长密码"
scripts\start-dev.bat
```

登录后在“系统设置”生成一次性 Agent 注册令牌。生产环境必须使用
内部 CA 签发的 HTTPS/WSS，参见 `deploy/README.md`。

### 手动启动

```bash
# 服务端
cd server && uvicorn main:app --port 8000

# Agent（首次运行需要 WCM_ENROLLMENT_TOKEN）
cd agent && python main.py --server ws://localhost:8000/ws/agent --api http://localhost:8000/api/v2

# 前端
cd web-ui && npm run dev
```

### 关闭

```
双击 scripts\stop.bat
```

## 依赖

| 组件 | 依赖 |
|------|------|
| 服务端 | fastapi, uvicorn, sqlalchemy[asyncio], aiosqlite, python-jose, passlib[bcrypt], websockets |
| Agent | websockets, psutil, requests |
| 前端 | Vue 3, Element Plus, Pinia, Axios, ECharts |

## Docker 容器管理

Agent 在 Docker 宿主机原生运行，通过本机 Docker CLI 发现容器并上报指标。
管理员可以查看日志及执行启动、停止、重启；系统不开放容器内 `exec`，服务端也不会挂载 Docker Socket。

服务端生产部署可使用根目录 `docker-compose.yml`，证书与 Secret 的准备方式见 `deploy/README.md`。

远程命令与终端默认关闭。确需启用时，服务端和对应 Agent 都必须设置
`WCM_ENABLE_REMOTE_COMMANDS=true`，Agent 还必须通过
`WCM_REMOTE_COMMAND_ALLOWLIST` 配置逗号分隔的精确命令白名单；不支持任意前缀匹配。

## 许可证

MIT License
