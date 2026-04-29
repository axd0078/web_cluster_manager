#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""目标节点解析工具。

统一处理"所有在线 / 指定节点 / 按分组"三种选择模式。
"""


def resolve_targets(mode: str,
                    single_ip: str,
                    group_name: str,
                    node_manager) -> tuple[list[str], str | None]:
    """根据选择模式解析目标节点 IP 列表。

    Returns:
        (targets, error_msg) — error_msg 为 None 表示成功。
    """
    if mode == "all":
        return node_manager.get_online_nodes(), None
    if mode == "selected":
        if not single_ip:
            return [], "请选择节点IP"
        return [single_ip], None
    if mode == "group":
        if not group_name:
            return [], "请选择分组"
        return node_manager.get_group_nodes(group_name), None
    return [], "未知的选择模式"
