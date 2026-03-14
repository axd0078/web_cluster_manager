#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
服务端更新管理器
支持版本管理、增量更新、批量推送
"""

import os
import json
import hashlib
import zipfile
import threading
import time
from pathlib import Path
from datetime import datetime


class UpdateManager:
    """更新管理器 - 管理客户端版本和更新包"""

    def __init__(self, updates_dir=None):
        """
        初始化更新管理器

        Args:
            updates_dir: 更新包存放目录，默认为server_new/updates
        """
        if updates_dir:
            self.updates_dir = Path(updates_dir)
        else:
            self.updates_dir = Path(__file__).parent.parent / 'updates'

        self.updates_dir.mkdir(parents=True, exist_ok=True)

        # 版本信息文件
        self.version_file = self.updates_dir / 'version.json'
        self._lock = threading.Lock()

        # 加载版本信息
        self.version_info = self._load_version_info()

    def _load_version_info(self):
        """加载版本信息"""
        if self.version_file.exists():
            try:
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass

        # 默认版本信息
        return {
            'current_version': '1.0.0',
            'min_version': '1.0.0',
            'release_date': datetime.now().strftime('%Y-%m-%d'),
            'release_notes': '初始版本',
            'files': {}
        }

    def _save_version_info(self):
        """保存版本信息"""
        with open(self.version_file, 'w', encoding='utf-8') as f:
            json.dump(self.version_info, f, indent=2, ensure_ascii=False)

    def get_current_version(self):
        """获取当前最新版本"""
        return self.version_info.get('current_version', '1.0.0')

    def get_version_info(self):
        """获取完整版本信息"""
        return self.version_info.copy()

    def _compare_versions(self, v1, v2):
        """
        比较两个版本号

        Returns:
            -1: v1 < v2
            0: v1 == v2
            1: v1 > v2
        """
        try:
            parts1 = [int(x) for x in v1.split('.')]
            parts2 = [int(x) for x in v2.split('.')]
        except:
            return 0

        # 补齐版本号长度
        max_len = max(len(parts1), len(parts2))
        parts1.extend([0] * (max_len - len(parts1)))
        parts2.extend([0] * (max_len - len(parts2)))

        for p1, p2 in zip(parts1, parts2):
            if p1 < p2:
                return -1
            elif p1 > p2:
                return 1
        return 0

    def calculate_file_md5(self, file_path):
        """计算文件MD5值"""
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()

    def create_update_package(self, source_dir, version, release_notes='', exclude_dirs=None):
        """
        创建更新包（只包含运行必需的文件）

        Args:
            source_dir: 源目录（客户端代码目录）
            version: 新版本号
            release_notes: 更新说明
            exclude_dirs: 排除的目录列表（已废弃）

        Returns:
            dict: 创建结果
        """
        with self._lock:
            try:
                source_path = Path(source_dir)
                if not source_path.exists():
                    return {'status': 'error', 'message': f'源目录不存在: {source_dir}'}

                # 只保留运行客户端必需的文件
                include_files = [
                    'client_main.py',
                    'config.json',
                    'requirements.txt',
                    'start.bat'
                ]

                # 创建版本目录
                version_dir = self.updates_dir / f'v{version}'
                version_dir.mkdir(parents=True, exist_ok=True)

                # 文件清单
                files_manifest = {}

                # 复制必需的单个文件
                for file_name in include_files:
                    src_file = source_path / file_name
                    if src_file.exists():
                        target_file = version_dir / file_name
                        target_file.parent.mkdir(parents=True, exist_ok=True)

                        # 复制文件
                        with open(src_file, 'rb') as src, open(target_file, 'wb') as dst:
                            dst.write(src.read())

                        # 计算MD5
                        md5 = self.calculate_file_md5(src_file)
                        files_manifest[file_name] = {
                            'md5': md5,
                            'size': src_file.stat().st_size
                        }

                # 复制core目录（排除__pycache__）
                core_src = source_path / 'core'
                core_dst = version_dir / 'core'
                if core_src.exists():
                    core_dst.mkdir(parents=True, exist_ok=True)

                    for root, dirs, files in os.walk(core_src):
                        # 排除__pycache__
                        dirs[:] = [d for d in dirs if d not in ['__pycache__']]

                        for file in files:
                            file_path = Path(root) / file
                            rel_path = file_path.relative_to(source_path)
                            target_path = version_dir / rel_path
                            target_path.parent.mkdir(parents=True, exist_ok=True)

                            # 复制文件
                            with open(file_path, 'rb') as src, open(target_path, 'wb') as dst:
                                dst.write(src.read())

                            # 计算MD5
                            md5 = self.calculate_file_md5(file_path)
                            files_manifest[str(rel_path).replace('\\', '/')] = {
                                'md5': md5,
                                'size': file_path.stat().st_size
                            }

                # 更新版本信息
                self.version_info['current_version'] = version
                self.version_info['release_date'] = datetime.now().strftime('%Y-%m-%d')
                self.version_info['release_notes'] = release_notes
                self.version_info['files'] = files_manifest
                self._save_version_info()

                # 创建压缩包（只包含必需文件）
                zip_path = self.updates_dir / f'client_v{version}.zip'
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    # 添加单个文件
                    for file_name in include_files:
                        file_path = version_dir / file_name
                        if file_path.exists():
                            zipf.write(file_path, file_name)

                    # 添加core目录
                    core_dir = version_dir / 'core'
                    if core_dir.exists():
                        for root, dirs, files in os.walk(core_dir):
                            dirs[:] = [d for d in dirs if d not in ['__pycache__']]
                            for file in files:
                                file_path = Path(root) / file
                                arcname = file_path.relative_to(version_dir)
                                zipf.write(file_path, arcname)

                return {
                    'status': 'success',
                    'message': f'更新包创建成功: v{version}',
                    'version': version,
                    'files_count': len(files_manifest),
                    'package_path': str(zip_path)
                }

            except Exception as e:
                return {'status': 'error', 'message': f'创建更新包失败: {str(e)}'}

    def get_update_manifest(self, client_version=None, client_files=None):
        """
        获取更新清单（增量更新）

        Args:
            client_version: 客户端当前版本
            client_files: 客户端现有文件清单 {相对路径: md5}

        Returns:
            dict: 需要更新的文件列表
        """
        try:
            current_version = self.version_info.get('current_version', '1.0.0')
            min_version = self.version_info.get('min_version', '1.0.0')
            server_files = self.version_info.get('files', {})

            # 如果客户端版本已经是最新
            if client_version == current_version:
                return {
                    'status': 'success',
                    'need_update': False,
                    'message': '已是最新版本',
                    'current_version': current_version
                }

            # 检查客户端版本是否太老（低于最小支持版本）
            if client_version and self._compare_versions(client_version, min_version) < 0:
                return {
                    'status': 'success',
                    'need_update': True,
                    'update_type': 'full',
                    'current_version': current_version,
                    'min_version': min_version,
                    'client_version': client_version,
                    'message': f'客户端版本过旧({client_version})，需要全量更新到最低支持版本{min_version}以上',
                    'force_full': True
                }

            # 如果没有提供客户端文件清单，返回全量更新
            if not client_files:
                return {
                    'status': 'success',
                    'need_update': True,
                    'update_type': 'full',
                    'current_version': current_version,
                    'files': list(server_files.keys()),
                    'message': '需要全量更新'
                }

            # 增量更新：比较文件MD5
            files_to_update = []
            files_to_delete = []

            # 检查需要更新或新增的文件
            for file_path, file_info in server_files.items():
                if file_path not in client_files:
                    files_to_update.append(file_path)
                elif client_files.get(file_path) != file_info.get('md5'):
                    files_to_update.append(file_path)

            # 检查需要删除的文件（客户端有但服务端没有）
            for file_path in client_files:
                if file_path not in server_files:
                    files_to_delete.append(file_path)

            return {
                'status': 'success',
                'need_update': len(files_to_update) > 0 or len(files_to_delete) > 0,
                'update_type': 'incremental',
                'current_version': current_version,
                'files_to_update': files_to_update,
                'files_to_delete': files_to_delete,
                'message': f'需要更新 {len(files_to_update)} 个文件，删除 {len(files_to_delete)} 个文件'
            }

        except Exception as e:
            return {'status': 'error', 'message': f'获取更新清单失败: {str(e)}'}

    def get_file_content(self, file_path, version=None):
        """
        获取文件内容

        Args:
            file_path: 文件相对路径
            version: 版本号，默认为当前版本

        Returns:
            bytes: 文件内容，失败返回None
        """
        try:
            if version:
                version_dir = self.updates_dir / f'v{version}'
            else:
                version = self.version_info.get('current_version', '1.0.0')
                version_dir = self.updates_dir / f'v{version}'

            file_full_path = version_dir / file_path
            if file_full_path.exists():
                with open(file_full_path, 'rb') as f:
                    return f.read()
            return None
        except:
            return None

    def get_update_package(self, version=None):
        """
        获取完整更新包

        Args:
            version: 版本号，默认为当前版本

        Returns:
            bytes: 压缩包内容，失败返回None
        """
        try:
            if version:
                zip_path = self.updates_dir / f'client_v{version}.zip'
            else:
                version = self.version_info.get('current_version', '1.0.0')
                zip_path = self.updates_dir / f'client_v{version}.zip'

            if zip_path.exists():
                with open(zip_path, 'rb') as f:
                    return f.read()
            return None
        except:
            return None

    def list_versions(self):
        """列出所有可用版本"""
        versions = []
        for item in self.updates_dir.iterdir():
            if item.is_dir() and item.name.startswith('v'):
                versions.append(item.name[1:])  # 去掉'v'前缀
        return sorted(versions, reverse=True)

    def delete_version(self, version):
        """删除指定版本的更新包"""
        import shutil
        try:
            version_dir = self.updates_dir / f'v{version}'
            zip_path = self.updates_dir / f'client_v{version}.zip'

            if version_dir.exists():
                shutil.rmtree(version_dir)
            if zip_path.exists():
                zip_path.unlink()

            return {'status': 'success', 'message': f'版本 v{version} 已删除'}
        except Exception as e:
            return {'status': 'error', 'message': f'删除失败: {str(e)}'}
