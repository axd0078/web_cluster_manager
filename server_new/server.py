#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web集群管理服务端（兼容性入口）
提供节点管理、任务分发、性能监控等功能

注意：此文件为兼容性入口点，实际实现在core目录、gui目录和server_main.py中
"""

from server_main import main


if __name__ == '__main__':
    main()