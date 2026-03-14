# Web集群管理系统

## 简介

Web集群管理系统是一个用于管理分布式Web服务器节点的工具，采用客户端-服务端架构设计。服务端提供图形化管理界面，用于监控集群节点、分发任务、更新程序；客户端部署在各个服务器节点上，接收服务端指令并执行任务、上报监控数据。

## 主要功能

- **节点管理**: 实时查看节点状态（在线/离线）、操作系统信息、最后心跳时间
- **节点分组**: 支持创建分组、将节点添加到分组、按分组批量操作
- **任务管理**: 日志清理（按日期）、文件备份（压缩客户端目录发送到服务端）
- **文件传输**: 向单个节点传输任意类型文件
- **客户端更新**: 支持全量更新和增量更新，可按节点、分组或全部节点推送
- **批量分发**: 向多个节点或分组同时分发文件
- **远程命令**: 在远程节点执行命令，支持快捷命令
- **性能监控**: 实时监控CPU、内存、磁盘使用率，支持阈值告警
- **操作日志**: 详细记录所有操作，支持按IP分类存储

## 项目结构

```
web_cluster_manager/
├── client_new/                    # 客户端目录
│   ├── client_main.py             # 主入口文件
│   ├── config.json.example        # 配置文件示例
│   ├── requirements.txt           # 依赖包
│   ├── start.bat                  # Windows启动脚本
│   └── core/                      # 核心模块
│       ├── address_pool.py        # 地址池管理（白名单验证）
│       ├── task_executor.py       # 任务执行器（日志清理、备份、远程命令）
│       ├── system_monitor.py      # 系统监控（CPU、内存、磁盘）
│       └── client_updater.py      # 客户端更新器（增量更新、回滚）
│
├── server_new/                    # 服务端目录
│   ├── server_main.py             # 主入口文件
│   ├── server.py                  # 兼容入口
│   ├── config.json.example        # 配置文件示例
│   ├── requirements.txt           # 依赖包
│   ├── start.bat                  # Windows启动脚本
│   ├── core/                      # 核心模块
│   │   ├── node_manager.py        # 节点管理（状态、分组）
│   │   ├── network_manager.py     # 网络通信（命令、监控、文件传输）
│   │   ├── logger.py              # 日志管理（按IP分类存储）
│   │   └── update_manager.py      # 更新管理（版本、增量更新包）
│   └── gui/                       # 图形界面模块
│       └── server_gui.py          # 主界面（9个功能标签页）
│
├── .gitignore                     # Git忽略配置
├── LICENSE                        # MIT许可证
└── README.md                      # 项目说明文档
```

## 网络通信架构

```
┌─────────────────┐                    ┌─────────────────┐
│    服务端        │                    │    客户端        │
│  (Server GUI)   │                    │  (Client)       │
├─────────────────┤                    ├─────────────────┤
│ 命令端口: 8888   │◄────心跳/注册──────│                 │
│ 监控端口: 8889   │◄────监控数据───────│                 │
│                 │────发送命令───────►│ 监听端口: 8887   │
│                 │────传输文件───────►│                 │
│                 │────推送更新───────►│                 │
└─────────────────┘                    └─────────────────┘
```

- **服务端命令端口 (8888)**: 接收客户端心跳和注册请求
- **服务端监控端口 (8889)**: 接收客户端上报的监控数据
- **客户端监听端口 (8887)**: 接收服务端下发的命令、文件、更新

## 快速开始

### 环境要求

- Python 3.8+
- 支持系统: Windows / Linux / macOS

### 安装依赖

```bash
# 客户端
cd client_new
pip install -r requirements.txt

# 服务端
cd server_new
pip install -r requirements.txt
```

### 配置系统

首次使用前，需要复制配置文件模板：

```bash
# 客户端
cp client_new/config.json.example client_new/config.json

# 服务端
cp server_new/config.json.example server_new/config.json
```

> **注意**: `config.json` 包含敏感信息（服务器地址等），已被 `.gitignore` 忽略，不会被上传到仓库。

#### 客户端配置 (`client_new/config.json`)

```json
{
    "server_addresses": ["127.0.0.1", "192.168.1.100"],
    "client_listen_port": 8887,
    "server_command_port": 8888,
    "server_monitor_port": 8889,
    "backup_path": "./backup",
    "web_app_path": "./web_app"
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `server_addresses` | 允许连接的服务端IP白名单 | - |
| `client_listen_port` | 客户端监听端口 | 8887 |
| `server_command_port` | 服务端命令端口 | 8888 |
| `server_monitor_port` | 服务端监控端口 | 8889 |
| `backup_path` | 备份文件存储路径 | ./backup |
| `web_app_path` | Web应用文件路径 | ./web_app |

#### 服务端配置 (`server_new/config.json`)

```json
{
    "server": {
        "host": "0.0.0.0",
        "command_port": 8888,
        "monitor_port": 8889,
        "heartbeat_interval": 5
    },
    "monitoring": {
        "cpu_threshold": 80,
        "memory_threshold": 80,
        "disk_threshold": 90
    },
    "alerts": {
        "enabled": true,
        "email": ""
    }
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `server.command_port` | 命令端口 | 8888 |
| `server.monitor_port` | 监控端口 | 8889 |
| `monitoring.cpu_threshold` | CPU告警阈值(%) | 80 |
| `monitoring.memory_threshold` | 内存告警阈值(%) | 80 |
| `monitoring.disk_threshold` | 磁盘告警阈值(%) | 90 |

### 运行系统

#### 启动服务端

```bash
cd server_new
python server_main.py
# 或双击 start.bat (Windows)
```

#### 启动客户端

```bash
cd client_new
python client_main.py
# 或双击 start.bat (Windows)
```

#### 验证连接

1. 服务端GUI启动后，在"节点管理"标签页中应能看到客户端节点
2. 客户端控制台显示连接成功信息
3. 服务端"性能监控"标签页显示客户端系统指标

## 服务端界面说明

服务端GUI包含以下9个标签页：

| 标签页 | 功能 |
|--------|------|
| **节点管理** | 查看节点列表、状态、操作系统；支持右键快捷操作 |
| **节点分组** | 创建/删除分组；添加/移除节点到分组 |
| **任务管理** | 日志清理（按日期）；文件备份（压缩发送到服务端） |
| **文件传输** | 向单个节点传输文件，保存到客户端"Transfer Files"目录 |
| **客户端更新** | 创建更新包、检查版本、推送更新（全量/增量） |
| **批量分发** | 向多个节点或分组批量分发文件 |
| **远程命令** | 在远程节点执行命令，提供快捷命令按钮 |
| **性能监控** | 实时监控CPU/内存/磁盘，支持阈值告警 |
| **操作日志** | 查看详细操作日志 |

## 客户端功能模块

### 地址池管理 (`address_pool.py`)

管理允许连接的服务端IP白名单，拒绝未授权连接请求，增强系统安全性。

### 任务执行器 (`task_executor.py`)

- **日志清理**: 按日期删除客户端日志文件
- **文件备份**: 压缩客户端目录（排除backup、log等）发送到服务端
- **文件更新**: 接收并保存服务端下发的文件
- **远程命令**: 执行系统命令（含安全检查，禁止危险命令）
- **系统信息**: 获取CPU、内存、磁盘等详细信息

### 系统监控 (`system_monitor.py`)

收集并上报系统性能指标：
- CPU使用率
- 内存使用率和总量
- 磁盘使用率和总量
- 操作系统和主机名

### 客户端更新器 (`client_updater.py`)

- **版本管理**: 维护本地版本信息
- **增量更新**: 只更新变化的文件
- **全量更新**: 完整替换所有文件
- **原子操作**: 更新前自动备份，失败自动回滚
- **配置保留**: 更新时保留用户配置文件

## 服务端功能模块

### 节点管理 (`node_manager.py`)

- 维护节点状态（在线/离线）
- 心跳超时检测（30秒）
- 节点分组管理
- 持久化分组配置

### 网络管理 (`network_manager.py`)

- 双端口监听（命令端口、监控端口）
- 命令发送与响应处理
- 文件传输（支持大文件，128KB缓冲）
- 备份文件接收
- 并发操作支持

### 日志管理 (`logger.py`)

- 按日期记录操作日志
- 按IP分类存储日志文件
- 支持日志清理

### 更新管理 (`update_manager.py`)

- 创建更新包（只包含运行必需文件）
- 版本比较与增量更新清单
- MD5文件校验
- 更新包管理（创建、删除、列表）

## 故障排除

### 常见问题

#### 端口被占用

检查端口是否被占用：
```bash
# Windows
netstat -ano | findstr "8887"
netstat -ano | findstr "8888"
netstat -ano | findstr "8889"

# Linux
netstat -tlnp | grep -E "8887|8888|8889"
```

#### 连接失败

1. 确保客户端配置中的 `server_addresses` 包含正确的服务端IP
2. 检查防火墙设置，确保端口开放
3. 验证网络连通性（ping、telnet）

#### 配置文件错误

- 确保config.json格式正确，符合JSON规范
- 字符串必须用双引号 `"`
- 最后一个配置项后面不能有逗号
- 使用UTF-8无BOM编码保存

#### GUI启动失败

- 确保已安装所有依赖：`pip install -r requirements.txt`
- 检查Python版本（推荐Python 3.8+）
- 确保Tkinter库已安装

## 开发说明

### 添加新功能

- 在 `core/` 目录下创建新的模块类
- 遵循现有类的设计模式
- 保持高内聚低耦合

### 扩展客户端功能

- **添加新任务类型**: 在 `task_executor.py` 中添加新的任务处理方法
- **扩展监控指标**: 在 `system_monitor.py` 中添加新的监控数据收集方法
- **修改网络协议**: 在 `client_main.py` 中修改消息处理逻辑

### 扩展服务端功能

- **修改界面**: 在 `server_gui.py` 中相应标签页添加控件
- **扩展协议**: 在 `network_manager.py` 中添加新的消息类型
- **添加新模块**: 在 `core/` 目录下创建新的模块类

### 代码风格

- 遵循PEP 8代码规范
- 使用面向对象设计
- 每个模块类独立完成特定功能

## 安全建议

1. **地址池配置**: 始终配置 `server_addresses`，只允许可信的服务端连接
2. **网络隔离**: 将客户端部署在内网环境，避免直接暴露在公网
3. **定期更新**: 及时更新客户端和服务端程序
4. **权限控制**: 以最小权限运行程序
5. **日志审计**: 定期检查操作日志，发现异常行为

## 依赖说明

```
psutil>=5.9.0      # 系统监控
pyinstaller>=6.0.0 # 打包为可执行文件
```

## 许可证

本项目基于MIT许可证发布，详见 [LICENSE](LICENSE) 文件。
