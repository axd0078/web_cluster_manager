# Web集群管理系统

## 简介
Web集群管理系统是一个用于管理分布式Web服务器节点的工具，包含服务端和客户端两部分。服务端提供图形化管理界面，用于监控集群节点、分发任务、更新程序；客户端部署在各个服务器节点上，接收服务端指令并执行任务、上报监控数据。

系统采用模块化架构设计，代码按功能分类，便于维护和扩展。

## 系统架构
项目采用模块化设计，主要分为服务端和客户端两个部分：

### 客户端模块结构
- **core/**: 核心业务逻辑模块
  - `address_pool.py`: 地址池管理类，管理允许连接的服务器地址
  - `task_executor.py`: 任务执行器类，处理各种任务（日志清理、文件备份等）
  - `system_monitor.py`: 系统监控类，收集和上报系统性能数据
- **utils/**: 工具模块（预留扩展）
- **入口文件**:
  - `client_main.py`: 新的主入口点（推荐）
  - `client.py`: 兼容性入口点，保持向后兼容

### 服务端模块结构
- **core/**: 核心业务逻辑模块
  - `node_manager.py`: 节点管理类，管理集群节点状态
  - `network_manager.py`: 网络管理类，处理网络通信
  - `logger.py`: 日志管理类，提供日志记录功能
- **gui/**: 图形界面模块
  - `server_gui.py`: 服务端主界面，包含节点管理、任务管理、性能监控等标签页
- **入口文件**:
  - `server_main.py`: 新的主入口点（推荐）
  - `server.py`: 兼容性入口点，保持向后兼容

### 网络通信架构
- 客户端通过TCP连接服务端
- 服务端监听命令端口（默认8888）和监控端口（默认8889）
- 客户端监听端口（默认8887）接收服务端连接
- 心跳机制保持连接状态，定期上报监控数据

## 快速开始

### 环境要求
- Python 3.8+
- Windows/Linux/macOS（客户端支持跨平台，服务端GUI基于Tkinter）

### 安装依赖

#### 客户端依赖安装
进入客户端目录 `client_new/`：
```bash
cd client_new
pip install -r requirements.txt
```

#### 服务端依赖安装
进入服务端目录 `server_new/`：
```bash
cd server_new
pip install -r requirements.txt
```

### 配置系统

#### 客户端配置
编辑 `client_new/config.json` 文件：
- `server_addresses`: 服务端IP地址列表（必须包含服务端的IP）
- `client_listen_port`: 客户端监听端口（默认8887）
- `server_command_port`: 服务端命令端口（默认8888）
- `server_monitor_port`: 服务端监控端口（默认8889）
- `backup_path`: 备份文件存储路径（默认./backup）
- `web_app_path`: Web应用文件路径（默认./web_app）
- `heartbeat_interval`: 心跳间隔（秒），默认30秒
- `monitor_interval`: 监控数据上报间隔（秒），默认10秒

#### 服务端配置
编辑 `server_new/config.json` 文件：
- `server.command_port`: 命令端口（默认8888）
- `server.monitor_port`: 监控端口（默认8889）
- `monitoring.cpu_threshold`: CPU告警阈值（默认80%）
- `monitoring.memory_threshold`: 内存告警阈值（默认80%）

### 运行系统

#### 启动服务端
进入服务端目录 `server_new/`：

**推荐使用新入口点：**
```bash
python server_main.py
```

**或使用兼容性入口点：**
```bash
python server.py
```

**Windows用户可直接双击：**
- `start.bat` - 启动服务端

#### 启动客户端
进入客户端目录 `client_new/`：

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

#### 验证连接
1. 服务端GUI启动后，在"节点管理"标签页中应能看到客户端节点
2. 客户端控制台显示连接成功信息
3. 服务端"性能监控"标签页显示客户端系统指标

## 详细配置说明

### 客户端配置详解

`client_new/config.json` 完整配置项：

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

### 服务端配置详解

`server_new/config.json` 配置项：

```json
{
    "server": {
        "command_port": 8888,
        "monitor_port": 8889
    },
    "monitoring": {
        "cpu_threshold": 80,
        "memory_threshold": 80
    }
}
```

- `server.command_port`: 命令端口（默认8888），用于发送命令到客户端
- `server.monitor_port`: 监控端口（默认8889），用于接收客户端监控数据
- `monitoring.cpu_threshold`: CPU告警阈值（默认80%），超过此值触发告警
- `monitoring.memory_threshold`: 内存告警阈值（默认80%），超过此值触发告警

## 功能特性

### 客户端功能
- **地址池管理**: 只允许预设的服务器地址连接，增强安全性
- **任务执行**: 接收并执行服务端下发的各种任务（日志清理、文件备份等）
- **文件更新**: 接收服务端下发的文件更新，支持Web应用文件更新
- **系统监控**: 实时收集CPU、内存、磁盘使用率等系统指标并上报
- **心跳机制**: 定期向服务端发送心跳，保持连接状态

### 服务端功能
- **节点管理**: 显示和管理集群节点，支持添加、删除、查看节点状态
- **任务管理**: 执行日志清理、文件备份等任务，支持批量操作
- **程序更新**: 更新客户端程序文件，支持文件传输和版本管理
- **性能监控**: 监控多个客户端系统性能（CPU、内存、磁盘），实时告警
- **操作日志**: 查看详细的操作日志记录，支持按时间和IP筛选

## 模块详细说明

### 客户端模块

#### core/address_pool.py
管理允许连接的服务器地址白名单，拒绝未授权连接请求，增强系统安全性。

#### core/task_executor.py
处理服务端下发的各种任务：
- **日志清理**: 清理指定目录的日志文件
- **文件备份**: 备份指定目录的文件到备份路径
- **文件更新**: 更新Web应用文件
- **自定义任务**: 支持扩展其他任务类型

#### core/system_monitor.py
收集系统性能指标：
- CPU使用率
- 内存使用率
- 磁盘使用率
- 网络状态
定期向服务端上报监控数据，支持阈值告警。

### 服务端模块

#### core/node_manager.py
管理所有客户端节点，维护节点状态（在线/离线），处理节点心跳和状态更新。

#### core/network_manager.py
处理所有网络通信，包括命令发送、监控数据接收、文件传输等。

#### core/logger.py
提供日志记录功能，支持日志轮转和按IP地址分类存储。

#### gui/server_gui.py
Tkinter图形界面，包含以下标签页：
1. **节点管理**: 显示节点列表和状态
2. **任务管理**: 执行清理和备份任务
3. **程序更新**: 上传和分发客户端程序
4. **性能监控**: 实时监控客户端性能指标
5. **操作日志**: 查看历史操作记录

## 故障排除

### 常见问题

#### 端口被占用
- 检查8887、8888和8889端口是否被其他程序占用
- 可以在config.json中修改端口号

#### 连接失败
- 确保客户端配置中的`server_addresses`包含正确的服务端IP地址
- 检查防火墙设置，确保端口开放
- 验证网络连通性（ping、telnet）

#### 配置文件错误
- 确保config.json格式正确，符合JSON规范
- 可参考config.json.example创建配置文件
- 使用文本编辑器（如Notepad++）将文件另存为"UTF-8无BOM"格式

#### GUI启动失败
- 确保已安装所有依赖：`pip install -r requirements.txt`
- 检查Python版本（推荐Python 3.8+）
- 确保Tkinter库已安装（通常Python自带）

### 客户端特定问题

#### JSON配置文件错误
如果运行时出现 `JSONDecodeError` 错误：
1. **验证配置文件格式**
   ```bash
   python validate_config.py
   ```
   或指定配置文件路径：
   ```bash
   python validate_config.py config.json
   ```

2. **常见问题**
   - **BOM标记**: 如果文件包含BOM（UTF-8 BOM），可能导致解析失败。使用文本编辑器将文件另存为"UTF-8无BOM"格式
   - **格式错误**: 确保JSON格式正确，注意：
     - 字符串必须用双引号 `"`，不能用单引号 `'`
     - 最后一个配置项后面不能有逗号
     - 数组和对象必须正确闭合
   - **编码问题**: 确保文件使用UTF-8编码保存

3. **重新创建配置文件**
   - 复制 `config.json.example` 为 `config.json`
   - 根据实际情况修改配置项

#### 其他客户端问题
- **配置文件不存在**: 确保 `config.json` 文件在客户端程序同一目录下
- **权限不足**: 确保有权限创建备份目录和Web应用目录
- **日志查看**: 客户端日志默认输出到控制台，重要操作会记录到日志文件中（如果配置了日志文件）

### 服务端特定问题
- **日志查看**: 服务端日志默认不保存到文件，所有操作记录显示在GUI的"操作日志"标签页中
- **节点不显示**: 检查客户端是否正常运行，网络是否连通

## 开发说明

项目采用模块化设计，便于功能扩展：

### 添加新功能
- 在core/目录下创建新的模块类
- 遵循现有类的设计模式

### 扩展客户端功能
- **添加新任务类型**: 在`core/task_executor.py`中添加新的任务处理方法
- **扩展监控指标**: 在`core/system_monitor.py`中添加新的监控数据收集方法
- **修改网络协议**: 在`client_main.py`中修改消息处理逻辑

### 扩展服务端功能
- **修改界面**: 在`gui/server_gui.py`中相应标签页添加控件
- **扩展协议**: 在`core/network_manager.py`中添加新的消息类型
- **添加新模块**: 在core/目录下创建新的模块类

### 代码结构约定
- 每个模块类应独立完成特定功能
- 使用面向对象设计，保持高内聚低耦合
- 遵循PEP 8代码风格规范

## 安全建议

1. **地址池配置**: 始终配置`server_addresses`，只允许可信的服务端连接
2. **网络隔离**: 将客户端部署在内网环境，避免直接暴露在公网
3. **定期更新**: 及时更新客户端和服务端程序，修复安全漏洞
4. **权限控制**: 以最小权限运行客户端和服务端程序，避免权限过高带来的风险
5. **日志审计**: 定期检查操作日志，发现异常行为
6. **通信加密**: 考虑在敏感环境中使用SSL/TLS加密网络通信

## 许可证

本项目基于MIT许可证发布，详见LICENSE文件。