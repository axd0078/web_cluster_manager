#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import time
import json
from pathlib import Path
from typing import Any


class NodeManager:
    """节点管理器"""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, list[str]] = {}
        self.node_groups: dict[str, str] = {}
        self.lock = threading.Lock()
        self._load_groups()

    def _load_groups(self) -> None:
        config_path = Path(__file__).parent.parent / 'node_groups.json'
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.groups = data.get('groups', {})
                    self.node_groups = data.get('node_groups', {})
            except Exception:
                pass

    def _save_groups(self) -> None:
        config_path = Path(__file__).parent.parent / 'node_groups.json'
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'groups': self.groups,
                    'node_groups': self.node_groups
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存分组配置失败: {e}")

    def add_node(self, ip: str, os_info: str, node_info: dict[str, Any]) -> None:
        with self.lock:
            self.nodes[ip] = {
                'os': os_info,
                'status': 'online',
                'last_heartbeat': time.time(),
                'info': node_info
            }

    def update_heartbeat(self, ip: str, os_info: str | None = None,
                         node_info: dict[str, Any] | None = None) -> None:
        with self.lock:
            if ip not in self.nodes:
                self.nodes[ip] = {
                    'os': os_info or 'Unknown',
                    'status': 'online',
                    'last_heartbeat': time.time(),
                    'info': node_info or {}
                }
            else:
                self.nodes[ip]['last_heartbeat'] = time.time()
                self.nodes[ip]['status'] = 'online'
                if os_info:
                    self.nodes[ip]['os'] = os_info
                if node_info:
                    self.nodes[ip]['info'] = node_info

    def get_online_nodes(self) -> list[str]:
        with self.lock:
            current_time = time.time()
            online = []
            for ip, node in self.nodes.items():
                if current_time - node['last_heartbeat'] < 30:
                    online.append(ip)
                else:
                    node['status'] = 'offline'
            return online

    def get_all_nodes(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return dict(self.nodes)

    def create_group(self, group_name: str) -> bool:
        with self.lock:
            if group_name not in self.groups:
                self.groups[group_name] = []
                self._save_groups()
                return True
            return False

    def delete_group(self, group_name: str) -> bool:
        with self.lock:
            if group_name in self.groups:
                for ip in self.groups[group_name]:
                    if ip in self.node_groups:
                        del self.node_groups[ip]
                del self.groups[group_name]
                self._save_groups()
                return True
            return False

    def add_node_to_group(self, ip: str, group_name: str) -> bool:
        with self.lock:
            if group_name not in self.groups:
                self.groups[group_name] = []
            if ip not in self.groups[group_name]:
                self.groups[group_name].append(ip)
            self.node_groups[ip] = group_name
            self._save_groups()
            return True

    def remove_node_from_group(self, ip: str) -> bool:
        with self.lock:
            if ip in self.node_groups:
                group_name = self.node_groups[ip]
                if group_name in self.groups and ip in self.groups[group_name]:
                    self.groups[group_name].remove(ip)
                del self.node_groups[ip]
                self._save_groups()
                return True
            return False

    def get_group_nodes(self, group_name: str) -> list[str]:
        with self.lock:
            return self.groups.get(group_name, [])

    def get_all_groups(self) -> dict[str, list[str]]:
        with self.lock:
            return dict(self.groups)

    def get_node_group(self, ip: str) -> str | None:
        with self.lock:
            return self.node_groups.get(ip)
