# Agent 更新包离线工具

`update_package.py` 在离线发布机生成 Ed25519 密钥和 `.wcmupd` 包，并可使用
Server 相同的信任目录规则进行完整验签。签名私钥必须位于本仓库之外；工具会
拒绝仓库内的私钥路径，也不会输出私钥内容。

工具需要 Python 3.10+ 和 `cryptography>=45.0.0`。建议在隔离的发布环境
使用，并从秘密管理器注入至少 16 字节的私钥口令：

```powershell
$env:WCM_UPDATE_SIGNING_KEY_PASSWORD = "<从秘密管理器临时注入>"
python tools\update_package.py keygen `
  --private-key "E:\WCM-offline-keys\agent-update.key" `
  --public-key-dir "secrets\update_keys"
```

私钥使用口令加密的 PKCS#8 PEM。公钥文件名是原始公钥 SHA-256 前 32 个
十六进制字符，即 `<key_id>.pub`。如果只需从现有私钥重新导出公钥：

```powershell
python tools\update_package.py export-public-key `
  --private-key "E:\WCM-offline-keys\agent-update.key" `
  --public-key-dir "secrets\update_keys"
```

构建命令必须同时提供带哈希的 `requirements.lock` 和仅含 `.whl` 的非空
wheelhouse。锁文件中的每个有效依赖行都必须使用 `==` 精确版本并至少带一个
SHA-256 哈希；URL、本地引用和未锁定版本会被拒绝：

```powershell
python tools\update_package.py build `
  --source agent `
  --requirements-lock build\requirements.lock `
  --wheelhouse build\wheelhouse `
  --private-key "E:\WCM-offline-keys\agent-update.key" `
  --output build\agent-linux-x86_64-cp312.wcmupd `
  --version 4.1.0 `
  --release-id 4.1.0-linux-x86_64-cp312 `
  --target-os linux `
  --target-arch x86_64 `
  --python-abi cp312 `
  --min-updater-version 2.0.0 `
  --entrypoint main.py
```

`amd64` 会规范化为 `x86_64`，`arm64` 会规范化为 `aarch64`。工具默认忽略
源目录中的 `.git`、虚拟环境、`__pycache__`、`agent_data`、backup 和日志
目录，避免把运行凭据、缓存或备份放入发布包；依赖锁和 wheelhouse 只能通过
独立参数加入。输出路径不能位于源目录内，已有输出不会被覆盖。

包结构固定为：

```text
manifest.json
manifest.sig
payload/main.py
payload/requirements.lock
payload/wheelhouse/*.whl
payload/...
```

`manifest.json` 使用 UTF-8、键排序、无多余空白的 canonical JSON，字段固定为
`schema_version=1`、`component=agent`、`version`、`release_id`、
`created_at`、`key_id`、`target`、`min_updater_version`、`entrypoint` 和
`files`。`manifest.sig` 是 canonical 清单的 Ed25519 签名 Base64。每个
`files` 记录包含相对路径、字节数和 SHA-256。

构建会先用对应公钥完成一次自验，再原子提交 `.wcmupd`。交付前仍应从最终
存储位置重新验证：

```powershell
python tools\update_package.py verify `
  --package build\agent-linux-x86_64-cp312.wcmupd `
  --trusted-keys-dir secrets\update_keys
$env:WCM_UPDATE_SIGNING_KEY_PASSWORD = $null
```

验证拒绝未知 key ID、签名/哈希错误、未登记或缺失文件、绝对路径、盘符、
UNC、`..`、Windows 保留设备名、大小写路径冲突、符号链接、特殊文件、
非规范 ZIP 路径、异常压缩比以及超出 100 MiB 包大小、500 MiB 展开大小或
10,000 条目的包。500 MiB 与 10,000 条目预算同时统计 wheel 内部文件，wheel
内部也会再次检查路径、文件类型和压缩比。

查看所有参数：

```powershell
python tools/update_package.py --help
python tools/update_package.py build --help
```

完整发布流程及密钥轮换要求见项目根目录 `README.md` 和 `deploy/README.md`。
本地无副作用的临时往返自测：

```powershell
python tools/selftest_update_package.py
```
