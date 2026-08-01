# Web Cluster Manager

面向内网 Windows、Linux 与 Docker 宿主机的集中管理系统。Server 使用 FastAPI，前端使用 Vue 3，设备侧使用主动连接 Server 的 Agent。Docker 容器只作为宿主机资源展示和受控管理对象，不是 Agent、文件传输、任务或终端的直接目标。

当前版本：`4.0.0`，数据库 schema：`8`。

## 已实现功能

- 双身份模型：`user` 与 `admin`，旧 `operator/viewer/未知角色` 在 v7 迁移时统一降权为 `user`。
- 集中权限表：后端 API、WebSocket、任务、菜单和前端路由使用同一组权限标识。
- 管理员二次认证：重新输入密码后获得绑定当前登录 `sid` 的 5 分钟高权限授权。
- 节点、实时指标、告警、分组和 Docker 宿主机容器清单。
- 五类 Agent 任务：健康检查、日志清理、文件备份、服务重启和固定别名批量命令。
- 分块文件上传、多 Agent 分发、续传、逐节点重试和 Agent 沙箱落盘。
- 可信 Agent 更新中心：Ed25519 签名包、精确环境匹配、512 KiB 分块续传、
  人工 canary 批准、逐节点重试和自动/手动回滚。
- 安全远程终端：
  - 普通用户只能运行 `task_profiles.json` 中的固定低风险 `terminal_profiles`；
  - 管理员经二次认证和一次性票据连接独立 root/SYSTEM Broker；
  - Server 与 Agent/Broker 使用临时 X25519、HKDF-SHA256 和 ChaCha20-Poly1305 加密终端帧；
  - 终端正文不写入 SQLite、审计详情或应用日志。

## 组件

```text
server/       FastAPI、schema v8、权限决策、任务/文件/终端/更新编排
agent/        普通设备 Agent；监控、任务、文件接收、固定别名终端、可信更新
broker/       独立特权 Broker；Linux PTY / Windows ConPTY
web-ui/       Vue 3、Element Plus、Xterm.js
deploy/       Nginx TLS 反向代理与生产部署说明
tests/        后端、Agent、安全协议及运行时回归测试
tools/        离线 Ed25519 密钥、Agent 发布包构建与验签工具
```

Agent 与 Broker 是两个独立进程、两套凭据和两条 WebSocket。Broker 离线时，管理员终端不会回退到普通 Agent。

## 本地开发

要求 Python 3.10+、Node.js 18+。

首次启动前必须提供至少 12 位的管理员密码；项目没有默认密码：

```powershell
$env:WCM_BOOTSTRAP_ADMIN_PASSWORD = "请替换为随机长密码"
scripts\setup.bat
scripts\start-dev.bat
```

单独启动：

```powershell
cd server
uvicorn main:app --host 127.0.0.1 --port 8000

cd ..\web-ui
npm run dev
```

系统启动后，在管理员菜单完成 5 分钟二次认证，再到“系统设置”签发一次性 Agent 注册令牌。生产环境部署见 [deploy/README.md](deploy/README.md)。

## 可信 Agent 更新包

更新目标只能是注册的 Windows/Linux Agent，包括运行在 Docker 宿主机上的
Agent；更新中心不会更新 Docker 容器、Server、前端或 Broker。现有 Agent
必须先用新版安装器完成一次手工 bootstrap，安装可信公钥、稳定 launcher 和
root/SYSTEM 更新助手，之后才允许远程发布。

签名私钥只保存在隔离的离线发布机且必须位于本仓库之外。下面使用秘密管理器
向环境变量临时注入口令；不要把示例占位值或真实口令写进脚本：

```powershell
$env:WCM_UPDATE_SIGNING_KEY_PASSWORD = "<从秘密管理器临时注入>"
python tools\update_package.py keygen `
  --private-key "E:\WCM-offline-keys\agent-update.key" `
  --public-key-dir "secrets\update_keys"
```

`keygen` 创建口令加密的 Ed25519 私钥，并把可分发的
`<key_id>.pub` 写入可信公钥目录。工具会拒绝在本仓库内创建或使用私钥，也不
会输出私钥内容。公钥虽不保密，仍应经受控渠道部署；Docker Server 从
`secrets/update_keys` 只读挂载，Agent 公钥由新版安装器一次性安装。

为每个 `OS + CPU 架构 + Python ABI` 分别生成锁定依赖、离线 wheelhouse 和
`.wcmupd`。示例：

```powershell
python -m pip install pip-tools
pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host `
  --output-file build\requirements.lock agent\requirements.txt
python -m pip download --only-binary=:all: --require-hashes `
  -r build\requirements.lock -d build\wheelhouse

python tools\update_package.py build `
  --source agent `
  --requirements-lock build\requirements.lock `
  --wheelhouse build\wheelhouse `
  --private-key "E:\WCM-offline-keys\agent-update.key" `
  --output build\wcm-agent-4.1.0-windows-x86_64-cp312.wcmupd `
  --version 4.1.0 `
  --release-id 4.1.0-windows-x86_64-cp312 `
  --target-os windows `
  --target-arch x86_64 `
  --python-abi cp312 `
  --min-updater-version 2.0.0

python tools\update_package.py verify `
  --package build\wcm-agent-4.1.0-windows-x86_64-cp312.wcmupd `
  --trusted-keys-dir secrets\update_keys
$env:WCM_UPDATE_SIGNING_KEY_PASSWORD = $null
```

包内只包含规范化的 `manifest.json`、Base64 Ed25519 `manifest.sig` 和
`payload/*`。工具拒绝绝对路径、盘符、UNC、`..`、Windows 保留名和大小写
冲突、符号链接、重解析点、特殊文件、未锁定依赖和空 wheelhouse；完整格式和操作流程见
[tools/README.md](tools/README.md) 与 [使用指南.md](使用指南.md)。

## Agent 低权限终端

复制对应平台示例为 `agent_data/task_profiles.json`，格式版本必须为 `2`。`terminal_profiles` 只允许固定 `argv`、1–300 秒超时和输出上限，不接受浏览器参数、工作目录、环境变量或 Shell 拼接。

Server 与 Agent 默认都关闭终端。完成配置后分别显式设置：

```text
# Server
WCM_ENABLE_LOW_TERMINAL=true

# Agent
WCM_ENABLE_LOW_TERMINAL=true
```

旧 Agent 可继续使用监控、任务和文件传输，但终端会显示为协议不兼容。

## 管理员 Broker

1. 管理员完成二次认证。
2. 为已注册节点调用 Broker 凭据轮换接口，原文只显示一次。
3. 在目标宿主机安装 `broker/requirements.txt`。
4. Linux 以 root 运行 `broker/install.sh`；Windows 使用提升的 PowerShell 运行 `broker/install.ps1`，安装任务最终以 SYSTEM 启动。
5. Server 显式设置 `WCM_ENABLE_PRIVILEGED_TERMINAL=true`。

生产环境只允许 HTTPS/WSS。开发环境仅允许回环地址使用 `http/ws`。

## 安全边界

- `user` 可查看集群状态、确认告警、运行低风险任务、无覆盖文件传输和固定别名终端，只查看本人历史。
- `admin` 普通登录额外可查看全局历史、审计、用户、设置和 Broker 状态。
- `admin + step-up` 才可执行正式日志删除、服务重启、批量命令、完整 Shell、容器日志/控制、文件覆盖、节点/分组/用户/凭据/规则/更新/设置写操作和审计导出。
- 角色、密码、禁用状态变化及退出会提升 `token_version` 并关闭用户 WebSocket。
- 终端票据 60 秒过期、只能使用一次、绑定用户、节点、模式和当前登录 `sid`，不会放在 URL。
- 高权限终端每个管理员和每个节点最多一个，全局最多 16 个；空闲 10 分钟、最长 30 分钟。

## 验证

```powershell
pytest -q

cd web-ui
npm test
npm run build
npm audit
```

详细操作步骤见 [使用指南.md](使用指南.md)，安全记录见 [SECURITY_REVIEW.md](SECURITY_REVIEW.md)。

## License

MIT
