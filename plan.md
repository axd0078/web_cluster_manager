# Web Cluster Manager 安全加固与 Docker 容器支持

## 总体方案

- 以外层 `server/`、`agent/`、`web-ui/` 为唯一运行代码；不自动删除未跟踪的嵌套旧副本。
- Docker 采用“宿主 Agent 管理容器”：Agent 原生运行在 Windows/Linux Docker 宿主机，容器作为宿主节点的子资源，不在每个容器内安装 Agent。
- Docker 首版仅支持发现、状态/资源查看、受限日志，以及管理员执行启动、停止、重启；不提供容器 `exec`。
- 遗留远程命令/终端默认关闭；仅在 `WCM_ENABLE_REMOTE_COMMANDS=true` 时向管理员开放预定义白名单任务，所有操作进入审计日志。

## 身份、网络与数据安全

- 删除匿名 `/auth/register`；新增仅管理员可用的用户管理接口，服务端限定角色。
- 登录改为 `Secure + HttpOnly + SameSite` Cookie；增加 CSRF Cookie/Header 校验、登出、刷新与会话失效处理。前端移除 `localStorage` Token 和默认 `admin/admin123` 表单值。
- 首次启动仅在用户表为空且提供一次性管理员环境密钥/Secret 时创建管理员；缺失时拒绝生产启动。JWT、Cookie、初始管理员密码均从 Secret 文件读取，禁止默认值。
- 新增短时、单次使用的管理员签发注册令牌：
  - `POST /api/v2/agent-enrollment-tokens`：管理员生成令牌，原文仅返回一次。
  - `POST /api/v2/agents/enroll`：Agent 以注册令牌完成首次入网，获得节点 ID 与独立、可撤销/轮换的 Agent 凭据。
  - Agent 凭据仅存哈希；Agent 在本地以 Windows ACL/Linux `0600` 保存凭据，安装命令通过环境变量读取注册令牌，不放入命令行。
- Agent WebSocket 改用 `Authorization: Bearer` 独立凭据，不再使用节点 UUID 或 URL query Token。前端与终端 WebSocket 使用登录 Cookie、Origin 校验和角色校验后才加入广播。
- 增加登录/注册令牌接口限流、严格 CORS/Trusted Host、安全响应头、审计日志；清理并忽略 `.claude/` 中的凭据文件。现有泄露 Token 需立即在外部服务轮换。
- 修复更新与上传：文件名/路径强制限制在目标目录、限制大小、校验 ZIP 解包路径与 SHA-256 清单；更新仅在 Agent 确认成功后标记完成。

## Agent、协议与 Docker

- 引入版本化 Agent 消息契约，统一服务端与 Agent 的任务、监控、文件、更新、终端及 Docker 消息；所有响应携带 `request_id` 与明确成功/失败状态。
- 修复 WebSocket 数据库事务：认证使用短会话，监控/任务结果使用独立短事务立即提交；断连和后台 stale 检查正确标记节点离线。
- 修复 Agent 重注册与退避：凭据失效时清理本地凭据、等待退避并要求新的注册令牌，不再高速重连。
- 新增 Docker 运行时模块，使用本机 Docker CLI 的参数数组调用（`shell=False`、超时、无任意拼接命令），兼容 Linux Docker Engine 与 Windows Docker Desktop。
- Agent 定期上报 Docker 能力、容器清单和资源快照；服务端按“宿主节点 + Docker container ID”幂等同步容器。
- 新增容器资源、容器指标、注册令牌、Agent 凭据的数据表；容器与宿主节点级联关联。既有节点保留为宿主节点，移除 IP 作为身份唯一约束，旧 Agent 必须重新注册。
- Docker 生命周期请求仅允许 `start`、`stop`、`restart`；日志读取限制条数和字节数。服务端不挂载 Docker Socket，只有已授权宿主 Agent 接触本机 Docker Engine。

## UI、部署与迁移

- 节点页改为树形表格：宿主节点展开显示 Docker 容器，展示镜像、状态、资源、最后更新时间；管理员可查看日志并执行带确认框的生命周期操作。
- 新增 Agent 注册令牌界面：管理员生成、复制一次性安装环境变量/命令，查看凭据状态并撤销。
- WebSocket 在登录状态建立后连接，并实时更新 Pinia 节点/容器状态。
- 修复 TypeScript project reference、Node 类型依赖、隐式 `any`，使 `npm run build` 成为发布门槛；生产静态服务增加 Vue history 路由回退。
- 引入 Alembic 版本化迁移：升级前自动备份 SQLite；已有用户、节点、分组、任务、指标保留，旧节点标记为需重新注册。
- 提供 `docker-compose`：应用仅暴露给 Nginx TLS 代理，外部仅发布 `443`；内部 CA 证书、JWT 与首次管理员密码通过挂载 Secret 注入。Agent 使用 `wss://` 且校验内部 CA；仅显式开发模式允许 localhost HTTP/WS。

## 测试与验收

- 新增 pytest：迁移/备份、Cookie+CSRF、匿名提权拒绝、注册令牌过期/单次使用/撤销、WebSocket 未认证拒绝、重复 Agent 连接、实时指标立即可见、离线状态、任务 retry。
- 新增 Docker Runtime 模拟测试：容器发现、状态同步、日志限制、仅允许三种生命周期操作、Docker 不可用时降级。
- 新增 Server + Agent 端到端测试：注册宿主、同步容器、启动/停止/重启、任务确认、审计记录；Docker 集成测试仅在具备 Docker Engine 的环境执行。
- 前端执行类型检查和生产构建；验证树形容器展示、权限控制、登录后 WebSocket、SPA 深链接。
- 发布验收：匿名用户无法创建管理员或连接 WebSocket；旧 UUID 不能冒充 Agent；HTTPS/WSS 与内部 CA 校验通过；Docker 容器操作不支持 `exec`；所有临时凭据和默认密码均不可用。

## 已确定的默认约束

- 保留现有 SQLite 数据并原地迁移。
- 使用内部 CA 证书部署 HTTPS/WSS。
- Docker Agent 原生部署于宿主机，要求目标运行账户具备 Docker CLI 权限；不支持通过挂载 Docker Socket 在服务端直接控制容器。
- 容器作为宿主节点的子资源显示，不单独作为可注册 Agent。
