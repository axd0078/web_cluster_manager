# Web 集群管理系统

一个用于管理分布式 Web 服务器节点的工具。提供实时监控、任务分发、远程命令、文件传输和客户端更新等功能。

项目正在进行 **架构升级**（Tkinter 桌面应用 → Web 应用），新老两套系统可并行运行。

---

## 目录

- [快速开始（新系统 Web 版）](#快速开始新系统-web-版)
- [快速开始（旧系统桌面版）](#快速开始旧系统桌面版)
- [项目结构](#项目结构)
- [验证清单](#验证清单)

---

## 快速开始（新系统 Web 版）

> 新系统：FastAPI + Vue 3 + WebSocket，浏览器访问。

### 环境要求

| 组件 | 版本 |
|------|------|
| Python | 3.8+ |
| Node.js | 18+（前端开发需 npm） |

### 一键启动（Windows）

```batch
双击 setup.bat      ← 首次运行：安装依赖 + 编译前端
双击 start-dev.bat  ← 启动系统（自动打开 3 个窗口）
```

浏览器打开 `http://localhost:5173`，账号 `admin` / `admin123`。

关闭系统：双击 `stop.bat` 或直接关闭窗口。

### 手动启动（分步调试用）

```bash
# 终端 1：服务端（http://localhost:8000）
cd server
pip install -r requirements.txt          # 首次
uvicorn main:app --reload --port 8000

# 终端 2：Agent
cd agent
pip install -r requirements.txt          # 首次
python main.py

# 终端 3：前端（http://localhost:5173）
cd web-ui
npm install                              # 首次
npm run dev
```

### 配置

**Agent** 连接参数在 `agent/config.json`：
```json
{
    "server_url": "ws://localhost:8000",
    "node_id": "agent-001",
    "token": "dev-agent-token-change-in-production"
}
```

**服务端** 默认配置即可用，高级参数通过环境变量覆盖：
```bash
set PORT=8000
set JWT_SECRET=your-secret
set AGENT_TOKEN=your-agent-token
```

### 登录验证

1. `http://localhost:5173` → 登录页
2. `admin` / `admin123` → 仪表盘显示在线节点
3. 节点管理 → Agent 状态实时更新
4. 点击「系统信息」→ 返回 CPU、内存等详情

---

## 旧系统（桌面版，备查）

> Tkinter GUI + Raw TCP，功能完整，保留作为备份参考。

启动方式：

```bash
cd server_new && cp config.json.example config.json && pip install -r requirements.txt
cd client_new && cp config.json.example config.json && pip install -r requirements.txt

# 终端 1
cd server_new && python server_main.py

# 终端 2
cd client_new && python client_main.py
```

端口：客户端 8887，服务端 8888/8889。

---

## 项目结构

```
web_cluster_manager/
│
├── setup.bat                         # ★ 一键安装依赖（首次运行）
├── start-dev.bat                     # ★ 一键启动新系统
├── stop.bat                          # ★ 一键停止所有进程
├── start-server.bat                  #    单独启动服务端
├── start-agent.bat                   #    单独启动 Agent
├── start-webui.bat                   #    单独启动前端
│
├── server/                           # 新服务端（FastAPI + WebSocket）
│   ├── main.py                       #   入口
│   ├── config.py / database.py       #   配置 + SQLite
│   ├── api/                          #   REST + WebSocket 路由
│   ├── models/ + schemas/            #   ORM + Pydantic
│   └── core/                         #   JWT + WebSocket 连接管理
│
├── agent/                            # 新 Agent（WebSocket，独立运行）
│   ├── main.py                       #   入口
│   ├── config.json                   #   连接配置
│   ├── install.ps1                   #   Windows 安装脚本
│   └── modules/                      #   自包含模块
│       ├── system_monitor.py         #   系统监控（CPU/内存/磁盘）
│       ├── task_executor.py          #   任务执行（命令/备份）
│       └── protocol.py               #   协议常量
│
├── web-ui/                           # 新前端（Vue 3 + Element Plus）
│   └── src/
│       ├── views/                    #   Login / Dashboard / Nodes
│       ├── api/                      #   Axios 封装
│       ├── composables/              #   useWebSocket
│       └── components/layout/        #   AppLayout
│
├── server_new/                       # 旧服务端（Tkinter，备份参考）
├── client_new/                       # 旧客户端（Raw TCP，备份参考）
└── .claude/                          # 设计文档
    ├── architecture-redesign-plan.md
    ├── refactor-plan.md
    └── startup-simplification-plan.md
```

---

## 验证清单

新系统启动后，按以下步骤验证全链路：

- [ ] 服务端 `uvicorn main:app` 无报错启动
- [ ] `http://localhost:8000/docs` 可访问 OpenAPI 文档
- [ ] `POST /api/v2/auth/login` 返回有效 JWT Token
- [ ] 无 Token 访问 `GET /api/v2/nodes` 返回 401
- [ ] 前端 `npm run dev` 无报错，`npm run build` 无 TS 错误
- [ ] 浏览器 `http://localhost:5173` 显示登录页
- [ ] 使用 admin/admin123 登录后跳转仪表盘
- [ ] `python agent/main.py` 启动后在仪表盘看到在线节点
- [ ] 节点管理页可向 Agent 发送「系统信息」命令并显示结果
- [ ] 关闭 Agent → 仪表盘显示节点离线
- [ ] 重连 Agent → 仪表盘恢复在线

## 依赖

```txt
# 新系统
fastapi>=0.115.0          # Web 框架
uvicorn[standard]>=0.30.0 # ASGI 服务器
sqlalchemy[asyncio]>=2.0  # ORM
aiosqlite>=0.20.0         # SQLite 异步驱动
python-jose>=3.3.0        # JWT
passlib[bcrypt]>=1.7.4    # 密码哈希
websockets>=12.0          # WebSocket
psutil>=5.9.0             # 系统监控

# 前端
Vue 3 + Vite + Element Plus + Pinia + Axios
```

## 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。
