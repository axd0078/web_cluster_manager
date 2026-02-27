# Web集群管理客户端

## 简介
这是Web集群管理工具的客户端程序，接收服务端指令，执行任务，上报监控信息。采用模块化架构设计，代码按功能分类，便于维护和扩展。

## 架构概述
项目采用模块化设计，主要分为以下模块：
- **core/**: 核心业务逻辑模块
  - `address_pool.py`: 地址池管理类，管理允许连接的服务器地址
  - `task_executor.py`: 任务执行器类，处理各种任务（日志清理、文件备份等）
  - `system_monitor.py`: 系统监控类，收集和上报系统性能数据
- **utils/**: 工具模块（预留扩展）
- **入口文件**:
  - `client_main.py`: 新的主入口点（推荐）
  - `client.py`: 兼容性入口点，保持向后兼容

## 文件说明
- `client_main.py` - 客户端主程序（新入口点）
- `client.py` - 兼容性入口点（保持向后兼容）
- `config.json` - 配置文件
- `config.json.example` - 配置文件示例
- `requirements.txt` - Python依赖包
- `start.bat` - 启动脚本（Windows）
- `build.bat` - 打包脚本（Windows）
- `core/` - 核心业务逻辑模块目录
- `utils/` - 工具模块目录（预留）

## 安装与运行

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置
编辑 `config.json` 文件：
- `server_addresses`: 服务端IP地址列表（必须包含服务端的IP）
- `client_listen_port`: 客户端监听端口（默认8887）
- `server_command_port`: 服务端命令端口（默认8888）
- `server_monitor_port`: 服务端监控端口（默认8889）
- `backup_path`: 备份文件存储路径（默认./backup）
- `web_app_path`: Web应用文件路径（默认./web_app）

### 3. 运行（两种方式）
**推荐使用新入口点：**
```bash
python client_main.py
```

**或使用兼容性入口点：**
```bash
python client.py
```

**Windows用户可直接双击：**
- `start.bat` - 启动客户端

## 功能
- **地址池管理**: 只允许预设的服务器地址连接，增强安全性
- **任务执行**: 接收并执行服务端下发的各种任务（日志清理、文件备份等）
- **文件更新**: 接收服务端下发的文件更新，支持Web应用文件更新
- **系统监控**: 实时收集CPU、内存、磁盘使用率等系统指标并上报
- **心跳机制**: 定期向服务端发送心跳，保持连接状态

## 模块详细说明

### core/address_pool.py
管理允许连接的服务器地址白名单，拒绝未授权连接请求，增强系统安全性。

### core/task_executor.py
处理服务端下发的各种任务：
- **日志清理**: 清理指定目录的日志文件
- **文件备份**: 备份指定目录的文件到备份路径
- **文件更新**: 更新Web应用文件
- **自定义任务**: 支持扩展其他任务类型

### core/system_monitor.py
收集系统性能指标：
- CPU使用率
- 内存使用率
- 磁盘使用率
- 网络状态
定期向服务端上报监控数据，支持阈值告警。

## 配置详解

### config.json 配置项
```json
{
    "server_addresses": [
        "127.0.0.1",
        "192.168.1.100"
    ],
    "client_listen_port": 8887,
    "server_command_port": 8888,
    "server_monitor_port": 8889,
    "backup_path": "./backup",
    "web_app_path": "./web_app",
    "heartbeat_interval": 30,
    "monitor_interval": 10
}
```

- `server_addresses`: 允许连接的服务端IP地址列表，只接受列表中的连接请求
- `client_listen_port`: 客户端监听端口，服务端通过此端口连接客户端
- `server_command_port`: 服务端命令端口，客户端连接此端口接收命令
- `server_monitor_port`: 服务端监控端口，客户端连接此端口上报监控数据
- `backup_path`: 备份文件存储目录，自动创建
- `web_app_path`: Web应用文件目录，自动创建
- `heartbeat_interval`: 心跳间隔（秒），默认30秒
- `monitor_interval`: 监控数据上报间隔（秒），默认10秒

## 故障排除

### JSON配置文件错误

如果运行时出现 `JSONDecodeError` 错误，可能是配置文件格式有问题：

1. **验证配置文件格式**
   ```bash
   python validate_config.py
   ```
   或指定配置文件路径：
   ```bash
   python validate_config.py config.json
   ```

2. **常见问题**
   - **BOM标记**: 如果文件包含BOM（UTF-8 BOM），可能导致解析失败。使用文本编辑器（如Notepad++）将文件另存为"UTF-8无BOM"格式
   - **格式错误**: 确保JSON格式正确，注意：
     - 字符串必须用双引号 `"`，不能用单引号 `'`
     - 最后一个配置项后面不能有逗号
     - 数组和对象必须正确闭合
   - **编码问题**: 确保文件使用UTF-8编码保存

3. **重新创建配置文件**
   如果配置文件损坏，可以：
   - 复制 `config.json.example` 为 `config.json`
   - 根据实际情况修改配置项

### 其他常见问题

- **配置文件不存在**: 确保 `config.json` 文件在客户端程序同一目录下
- **端口被占用**: 检查端口是否被其他程序占用
- **连接失败**: 检查服务端IP地址是否正确，网络是否连通
- **权限不足**: 确保有权限创建备份目录和Web应用目录

### 日志查看
客户端日志默认输出到控制台，重要操作会记录到日志文件中（如果配置了日志文件）。

## 开发说明
项目采用模块化设计，便于功能扩展：
- 添加新任务类型：在core/task_executor.py中添加新的任务处理方法
- 扩展监控指标：在core/system_monitor.py中添加新的监控数据收集方法
- 修改网络协议：在client_main.py中修改消息处理逻辑
- 添加新模块：在core/目录下创建新的模块类

## 安全建议
1. **地址池配置**: 始终配置`server_addresses`，只允许可信的服务端连接
2. **网络隔离**: 将客户端部署在内网环境，避免直接暴露在公网
3. **定期更新**: 及时更新客户端程序，修复安全漏洞
4. **权限控制**: 以最小权限运行客户端程序，避免权限过高带来的风险