#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import time


class NodeManager:
    """节点管理器"""
    def __init__(self):
        self.nodes = {}  # {ip: {os, status, last_heartbeat, info}}
        self.lock = threading.Lock()
    
    def add_node(self, ip, os_info, node_info):
        with self.lock:
            self.nodes[ip] = {
                'os': os_info,
                'status': 'online',
                'last_heartbeat': time.time(),
                'info': node_info
            }
    
    def update_heartbeat(self, ip, os_info=None, node_info=None):
        with self.lock:
            if ip not in self.nodes:
                # 如果节点不存在，添加新节点
                self.nodes[ip] = {
                    'os': os_info or 'Unknown',
                    'status': 'online',
                    'last_heartbeat': time.time(),
                    'info': node_info or {}
                }
            else:
                # 更新现有节点
                self.nodes[ip]['last_heartbeat'] = time.time()
                self.nodes[ip]['status'] = 'online'
                if os_info:
                    self.nodes[ip]['os'] = os_info
                if node_info:
                    self.nodes[ip]['info'] = node_info
    
    def get_online_nodes(self):
        with self.lock:
            current_time = time.time()
            online = []
            for ip, node in self.nodes.items():
                if current_time - node['last_heartbeat'] < 30:  # 30秒超时
                    online.append(ip)
                else:
                    node['status'] = 'offline'
            return online
    
    def get_all_nodes(self):
        with self.lock:
            return dict(self.nodes)