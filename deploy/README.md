# 安全部署准备

1. 由内部 CA 为管理域名签发证书，将证书链保存为 `deploy/certs/server.crt`，私钥保存为 `deploy/certs/server.key`。
2. 复制 `.env.example` 为 `.env`，设置内网可解析的 `WCM_PUBLIC_HOST`。
3. 创建 `secrets/jwt_secret.txt`（至少 32 个随机字节）和 `secrets/bootstrap_admin_password.txt`（至少 12 位随机密码）。这些目录已被 Git 忽略。
4. 运行 `docker compose config` 检查配置，再运行 `docker compose up -d --build`。
5. 在每台 Agent 设备安装内部 CA，并通过 `WCM_CA_CERT` 指向 CA 文件。禁止使用跳过证书校验的参数。

服务端容器不会挂载 Docker Socket。Docker 容器由各宿主机上的原生 Agent 通过本机 Docker CLI 管理。
