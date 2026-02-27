#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web集群管理客户端（兼容性入口）
接收服务端指令，执行任务，上报监控信息

注意：此文件为兼容性入口点，实际实现在core目录和client_main.py中
"""

from client_main import Client
from pathlib import Path


def main():
    # 获取配置文件路径（修改为当前目录下的config.json）
    config_path = Path(__file__).parent / 'config.json'
    
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        print("请确保config.json文件存在于客户端目录中")
        return
    
    client = Client(config_path)
    client.start()


if __name__ == '__main__':
    main()