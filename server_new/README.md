# Web集群管理服务端

## 简介
这是Web集群管理工具的服务端程序，提供图形界面用于管理集群节点、分发任务、监控性能等。采用模块化架构设计，代码按功能分类，便于维护和扩展。

## 架构概述
项目采用模块化设计，主要分为以下模块：
- **core/**: 核心业务逻辑模块
  - `node_manager.py`: 节点管理类，管理集群节点状态
  - `network_manager.py`: 网络管理类，处理网络通信
  - `logger.py`: 日志管理类，提供日志记录功能
- **gui/**: 图形界面模块
  - `server_gui.py`: 服务端主界面，包含节点管理、任务管理、性能监控等标签页
- **入口文件**:
  - `server_main.py`: 新的主入口点（推荐）
  - `server.py`: 兼容性入口点，保持向后兼容

## 文件说明
- `server_main.py` - 服务端主程序（新入口点）
- `server.py` - 兼容性入口点（保持向后兼容）
- `config.json` - 配置文件
- `requirements.txt` - Python依赖包
- `start.bat` - 启动脚本（Windows）
- `build.bat` - 打包脚本（Windows）
- `core/` - 核心业务逻辑模块目录
- `gui/` - 图形界面模块目录

## 安装与运行

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置
编辑 `config.json` 文件，设置端口和监控阈值。

### 3. 运行（两种方式）
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

## 配置说明

`config.json` 配置项：
- `server.command_port`: 命令端口（默认8888）
- `server.monitor_port`: 监控端口（默认8889）
- `monitoring.cpu_threshold`: CPU告警阈值（默认80%）
- `monitoring.memory_threshold`: 内存告警阈值（默认80%）

## 功能
- **节点管理**: 显示和管理集群节点，支持添加、删除、查看节点状态
- **任务管理**: 执行日志清理、文件备份等任务，支持批量操作
- **程序更新**: 更新客户端程序文件，支持文件传输和版本管理
- **性能监控**: 监控多个客户端系统性能（CPU、内存、磁盘），实时告警
- **操作日志**: 查看详细的操作日志记录，支持按时间和IP筛选

## 模块详细说明

### core/node_manager.py
管理所有客户端节点，维护节点状态（在线/离线），处理节点心跳和状态更新。

### core/network_manager.py
处理所有网络通信，包括命令发送、监控数据接收、文件传输等。

### core/logger.py
提供日志记录功能，支持日志轮转和按IP地址分类存储。

### gui/server_gui.py
Tkinter图形界面，包含以下标签页：
1. **节点管理**: 显示节点列表和状态
2. **任务管理**: 执行清理和备份任务
3. **程序更新**: 上传和分发客户端程序
4. **性能监控**: 实时监控客户端性能指标
5. **操作日志**: 查看历史操作记录

## 故障排除

### 常见问题

1. **端口被占用**
   - 检查8888和8889端口是否被其他程序占用
   - 可以在config.json中修改端口号

2. **连接失败**
   - 确保客户端IP地址已添加到配置中
   - 检查防火墙设置，确保端口开放

3. **GUI启动失败**
   - 确保已安装所有依赖：`pip install -r requirements.txt`
   - 检查Python版本（推荐Python 3.8+）

4. **配置文件错误**
   - 确保config.json格式正确
   - 可参考config.json.example创建配置文件

### 日志查看
服务端日志默认不保存到文件，所有操作记录显示在GUI的"操作日志"标签页中。

## 开发说明
项目采用模块化设计，便于功能扩展：
- 添加新功能：在core/目录下创建新的模块类
- 修改界面：在gui/server_gui.py中相应标签页添加控件
- 扩展协议：在core/network_manager.py中添加新的消息类型