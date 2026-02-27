#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web集群管理服务端 - 主入口文件
提供节点管理、任务分发、性能监控等功能
"""

import tkinter as tk

from gui.server_gui import ServerGUI


def main():
    root = tk.Tk()
    app = ServerGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()