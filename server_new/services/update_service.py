#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""更新服务 — 版本管理、更新推送。"""

import base64
from typing import Any

from core.node_manager import NodeManager
from core.network_manager import NetworkManager
from core.update_manager import UpdateManager


class UpdateService:
    """客户端更新的业务编排层。"""

    def __init__(self, node_manager: NodeManager,
                 network: NetworkManager, update_manager: UpdateManager) -> None:
        self._nm = node_manager
        self._net = network
        self._um = update_manager

    def get_version_info(self) -> dict[str, Any]:
        return self._um.get_version_info()

    def get_current_version(self) -> str:
        return self._um.get_current_version()

    def create_package(self, source_dir: str, version: str,
                       release_notes: str = '') -> dict[str, Any]:
        return self._um.create_update_package(source_dir, version, release_notes)

    def check_client_versions(self, target_ips: list[str]) -> dict[str, Any]:
        """检查多个客户端的版本。"""
        current_version = self._um.get_current_version()
        results: dict[str, Any] = {}
        for ip in target_ips:
            result = self._net.check_client_version(ip)
            if result and result.get('status') == 'success':
                client_version = result.get('version', 'unknown')
                results[ip] = {
                    'version': client_version,
                    'is_latest': client_version == current_version
                }
            else:
                results[ip] = {'error': '无法获取版本'}
        return {
            'status': 'success',
            'current_version': current_version,
            'results': results
        }

    def push_full_update(self, target_ips: list[str]) -> dict[str, Any]:
        """全量更新推送到多个节点。"""
        update_data = self._um.get_update_package()
        if not update_data:
            return {'status': 'error', 'message': '更新包不存在，请先创建更新包'}

        new_version = self._um.get_current_version()
        results = self._net.push_update_to_multiple(target_ips, update_data, new_version, 'full')

        success_count = sum(1 for r in results.values() if r and r.get('status') == 'success')
        fail_count = len(results) - success_count
        return {
            'status': 'success' if fail_count == 0 else 'partial',
            'version': new_version,
            'results': results,
            'success_count': success_count,
            'fail_count': fail_count
        }

    def push_smart_update(self, target_ips: list[str]) -> dict[str, Any]:
        """智能增量更新 —— 按文件差异推送。"""
        new_version = self._um.get_current_version()
        all_results: dict[str, Any] = {}

        for ip in target_ips:
            manifest_result = self._net.get_client_files_manifest(ip)
            if not manifest_result or manifest_result.get('status') != 'success':
                all_results[ip] = {'status': 'error', 'message': '无法获取文件清单'}
                continue

            client_manifest = manifest_result.get('manifest', {})
            update_manifest = self._um.get_update_manifest(None, client_manifest)
            if not update_manifest.get('need_update'):
                all_results[ip] = {'status': 'success', 'message': '已是最新版本'}
                continue

            files_to_update = update_manifest.get('files_to_update', [])
            files_to_delete = update_manifest.get('files_to_delete', [])

            update_data: dict[str, str] = {}
            for file_path in files_to_update:
                content = self._um.get_file_content(file_path)
                if content:
                    update_data[file_path] = base64.b64encode(content).decode('utf-8')

            if not update_data:
                all_results[ip] = {'status': 'error', 'message': '没有需要更新的文件'}
                continue

            result = self._net.push_update_to_client(ip, update_data, new_version, 'incremental')
            all_results[ip] = result if result else {'status': 'error', 'message': '无响应'}

        success_count = sum(1 for r in all_results.values() if r.get('status') == 'success')
        return {
            'status': 'success' if success_count == len(target_ips) else 'partial',
            'version': new_version,
            'results': all_results,
            'success_count': success_count,
            'fail_count': len(target_ips) - success_count
        }
