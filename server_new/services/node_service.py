#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点服务 — 节点查询、分组管理。"""

from typing import Any

from core.node_manager import NodeManager


class NodeService:
    """节点管理的业务编排层。"""

    def __init__(self, node_manager: NodeManager) -> None:
        self._nm = node_manager

    def get_online_nodes(self) -> list[str]:
        return self._nm.get_online_nodes()

    def get_all_nodes(self) -> dict[str, dict[str, Any]]:
        return self._nm.get_all_nodes()

    def get_node_info(self, ip: str) -> dict[str, Any]:
        return self._nm.get_all_nodes().get(ip, {})

    # ── 分组 ──────────────────────────────────────────

    def create_group(self, group_name: str) -> dict[str, Any]:
        if self._nm.create_group(group_name):
            return {'status': 'success', 'message': f"分组 '{group_name}' 创建成功"}
        return {'status': 'error', 'message': f"分组 '{group_name}' 已存在"}

    def delete_group(self, group_name: str) -> dict[str, Any]:
        if self._nm.delete_group(group_name):
            return {'status': 'success', 'message': f"分组 '{group_name}' 已删除"}
        return {'status': 'error', 'message': f"分组 '{group_name}' 不存在"}

    def add_node_to_group(self, ip: str, group_name: str) -> dict[str, Any]:
        if self._nm.add_node_to_group(ip, group_name):
            return {'status': 'success', 'message': f"节点 {ip} 已添加到分组 '{group_name}'"}
        return {'status': 'error', 'message': '添加失败'}

    def remove_node_from_group(self, ip: str) -> dict[str, Any]:
        if self._nm.remove_node_from_group(ip):
            return {'status': 'success', 'message': f"节点 {ip} 已从分组移除"}
        return {'status': 'error', 'message': '移除失败'}

    def get_group_nodes(self, group_name: str) -> list[str]:
        return self._nm.get_group_nodes(group_name)

    def get_all_groups(self) -> dict[str, list[str]]:
        return self._nm.get_all_groups()
