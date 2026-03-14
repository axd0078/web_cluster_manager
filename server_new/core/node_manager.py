#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import time
import json
from pathlib import Path


class NodeManager:
    """节点管理器"""
    def __init__(self):
        self.nodes = {}  # {ip: {os, status, last_heartbeat, info}}
        self.groups = {}  # {group_name: [ip1, ip2, ...]}
        self.node_groups = {}  # {ip: group_name} 节点所属分组
        self.lock = threading.Lock()
        self._load_groups()
    
    def _load_groups(self):
        """从配置文件加载分组信息"""
        config_path = Path(__file__).parent.parent / 'node_groups.json'
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.groups = data.get('groups', {})
                    self.node_groups = data.get('node_groups', {})
            except:
                pass
    
    def _save_groups(self):
        """保存分组信息到配置文件"""
        config_path = Path(__file__).parent.parent / 'node_groups.json'
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'groups': self.groups,
                    'node_groups': self.node_groups
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存分组配置失败: {e}")
    
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
    
    def create_group(self, group_name):
        """创建新分组"""
        with self.lock:
            if group_name not in self.groups:
                self.groups[group_name] = []
                self._save_groups()
                return True
            return False
    
    def delete_group(self, group_name):
        """删除分组"""
        with self.lock:
            if group_name in self.groups:
                # 移除该分组中节点的分组关联
                for ip in self.groups[group_name]:
                    if ip in self.node_groups:
                        del self.node_groups[ip]
                del self.groups[group_name]
                self._save_groups()
                return True
            return False
    
    def add_node_to_group(self, ip, group_name):
        """将节点添加到分组"""
        with self.lock:
            if group_name not in self.groups:
                self.groups[group_name] = []
            if ip not in self.groups[group_name]:
                self.groups[group_name].append(ip)
            self.node_groups[ip] = group_name
            self._save_groups()
            return True
    
    def remove_node_from_group(self, ip):
        """从分组中移除节点"""
        with self.lock:
            if ip in self.node_groups:
                group_name = self.node_groups[ip]
                if group_name in self.groups and ip in self.groups[group_name]:
                    self.groups[group_name].remove(ip)
                del self.node_groups[ip]
                self._save_groups()
                return True
            return False
    
    def get_group_nodes(self, group_name):
        """获取分组中的所有节点"""
        with self.lock:
            return self.groups.get(group_name, [])
    
    def get_all_groups(self):
        """获取所有分组"""
        with self.lock:
            return dict(self.groups)
    
    def get_node_group(self, ip):
        """获取节点所属分组"""
        with self.lock:
            return self.node_groups.get(ip)