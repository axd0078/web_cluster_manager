# 生产部署、可信更新与终端安全要求

## Server

1. 使用内部 CA 为管理域名签发证书，将证书链放在 `deploy/certs/server.crt`，私钥放在 `deploy/certs/server.key`。
2. 复制 `.env.example` 为 `.env`，设置 `WCM_PUBLIC_HOST`。
3. 创建 Git 忽略的 `secrets/jwt_secret.txt` 与 `secrets/bootstrap_admin_password.txt`。
4. 创建 `secrets/update_keys`，只放经核验的 `<key_id>.pub` Ed25519 公钥；
   离线签名私钥不得放在仓库、Server 或 Agent 上。
5. 确保容器内 uid 10001 可以读取该目录，再运行 `docker compose config`。
6. 启动部署，确认 `/run/secrets/update_keys` 为只读挂载。
7. 确认应用端口没有直接发布到宿主机公网接口，所有浏览器、Agent 与 Broker 流量都经过 Nginx TLS。

生产模式会拒绝不安全配置：必须关闭 DEBUG、启用 Secure Cookie、提供 JWT Secret，并且所有 CORS Origin 必须使用 HTTPS。终端启用时同样强制 HTTPS/WSS。

## 可信 Agent 更新引导

更新包由 `tools/update_package.py` 在离线发布机生成。私钥必须在仓库之外，并
由秘密管理器临时提供口令；Server 与 Agent 只部署同一
`<key_id>.pub`。首次启用可信更新是一次手工 bootstrap，不能通过旧的原地
覆盖更新器完成：

- Windows（提升的 PowerShell）：新版 `agent/install.ps1` 必须提供
  `-UpdatePublicKey <key-id.pub>`。程序安装到
  `%ProgramFiles%\WebClusterAgent`，凭据和 spool 位于
  `%ProgramData%\WebClusterAgent`，可信公钥位于
  `%ProgramFiles%\WebClusterAgent\trusted_update_keys`。
- Linux：新版 `agent/install.sh` 的第 4 个参数是 `<key-id.pub>`。程序和
  release 位于 `/opt/web-cluster-agent`，运行数据和 spool 位于
  `/var/lib/web-cluster-agent`，可信公钥位于
  `/opt/web-cluster-agent/trusted_update_keys`。

安装器创建稳定 Agent launcher，以及独立的
`WebClusterAgentUpdater`/`web-cluster-agent-updater` 特权任务或服务。
普通 Agent 账号只能写 `update-spool`，不能修改可信公钥、release 或活动版本
指针。确认 bootstrap 后才设置 Agent 侧：

```text
WCM_ENABLE_AGENT_UPDATES=true
```

默认布局可通过 `WCM_UPDATE_SPOOL_DIR`、`WCM_UPDATE_TRUSTED_KEYS_DIR` 和
`WCM_UPDATE_ACTIVE_POINTER` 显式覆盖，但生产部署应保持这些路径由
root/SYSTEM 所有。更新助手无网络监听，只处理固定 spool 中由 Agent 完整
接收的包，并再次执行签名、清单、路径、大小和文件哈希验证。

## 发布与回滚

1. 为目标 OS、规范化 CPU 架构和 Python ABI 构建独立 `.wcmupd`，先离线
   运行 `verify`。
2. 管理员登录后完成 5 分钟二次认证，再上传签名包。
3. 解析节点、分组或全部在线目标；存在协议或环境不兼容节点时不得创建任务。
4. 多节点发布至少选择一个 canary，并至少保留一个非 canary 节点。canary 全部通过健康确认后，任务停在
   `awaiting_approval`。
5. 管理员重新确认二次认证并批准，其余节点才按最多四个一批继续。
6. 激活后 120 秒内未收到匹配 release ID 的健康确认，更新助手自动切回上一
   release；任务停止扩大范围。
7. 失败或暂停节点由管理员明确重试；已成功节点可创建手动回滚任务。

Agent 最多保留当前 release 和前两个已验证 release。轮换签名密钥时，先通过
受控 bootstrap 把新公钥分发到 Server 和全部 Agent，确认后再用新私钥签名；
旧版本仍需回滚时不得提前删除旧公钥。Docker 容器不是更新目标，Docker
宿主机只更新宿主操作系统上的 Agent。

## 终端开关

终端默认关闭。逐节点发布顺序：

1. 部署 schema v8 Server。
2. 更新普通 Agent 到 terminal v3。
3. 在 Agent 配置 version 2 的 `terminal_profiles`。
4. 安装并验证独立 Broker。
5. 发布带 Xterm.js 的前端。
6. 设置 `WCM_ENABLE_LOW_TERMINAL=true`，逐节点验证普通别名。
7. 最后设置 `WCM_ENABLE_PRIVILEGED_TERMINAL=true`。

建议保留：

```text
WCM_TERMINAL_TICKET_TTL_SECONDS=60
WCM_TERMINAL_IDLE_TIMEOUT_SECONDS=600
WCM_TERMINAL_MAX_LIFETIME_SECONDS=1800
WCM_TERMINAL_MAX_GLOBAL_PRIVILEGED=16
```

## Agent

Agent 使用独立低权限账户运行。只有确需 Docker 管理的宿主机才允许 Agent 账户访问 Docker CLI。启用普通终端时，Agent 只能运行 version 2 `terminal_profiles` 中的固定 argv。

生产 Agent 必须使用：

```text
wss://管理域名/ws/agent
https://管理域名/api/v2
```

并通过 `WCM_CA_CERT` 或 `--ca-cert` 验证内部 CA。

## Broker

Broker 是独立特权组件，不得与 Agent 共用令牌或 WebSocket：

- Linux：root systemd service，安装脚本为 `broker/install.sh`。
- Windows：SYSTEM Scheduled Task，安装脚本为 `broker/install.ps1`。
- 凭据文件权限必须只允许 root/SYSTEM。
- Server 显示的 Broker 原始凭据只复制一次；轮换后旧连接会立即断开。

Broker 只固定启动宿主机 Shell，不进入 Docker 容器。Broker 离线时 Server 不会回退到 Agent。

## 回滚

紧急回滚优先：

1. 设置 `WCM_ENABLE_LOW_TERMINAL=false`。
2. 设置 `WCM_ENABLE_PRIVILEGED_TERMINAL=false`。
3. 撤销所有 Broker 凭据。
4. 重启 Server 并确认活动终端进程已结束。

schema v7 的终端表及 schema v8 的更新表均为 additive，可保留。若必须回退
到旧应用，使用经过验证的降级脚本把 `user` 映射为旧 `viewer`，不得映射为
旧 `operator`。可信更新紧急停止时应同时设置
`WCM_ENABLE_AGENT_UPDATES=false`，取消未激活的发布任务；正在原子切换的
节点完成健康确认或回滚后再结束。
