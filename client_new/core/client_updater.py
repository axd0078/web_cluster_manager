#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端更新器
支持版本检查、增量更新、原子更新、回滚机制
"""

import os
import json
import hashlib
import shutil
import zipfile
import tempfile
import time
from pathlib import Path
from datetime import datetime


class ClientUpdater:
    """客户端更新器 - 处理版本检查、增量更新、原子更新"""

    def __init__(self, client_dir=None, backup_dir=None):
        """
        初始化更新器

        Args:
            client_dir: 客户端目录，默认为当前模块的父目录
            backup_dir: 备份目录，默认为client_dir/backup/updates
        """
        if client_dir:
            self.client_dir = Path(client_dir)
        else:
            self.client_dir = Path(__file__).parent.parent

        if backup_dir:
            self.backup_dir = Path(backup_dir)
        else:
            self.backup_dir = self.client_dir / 'backup' / 'updates'

        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # 本地版本文件
        self.version_file = self.client_dir / 'version.json'
        self.local_version = self._load_local_version()

        # 更新锁文件
        self.lock_file = self.client_dir / '.update_lock'

    def _load_local_version(self):
        """加载本地版本信息"""
        if self.version_file.exists():
            try:
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass

        return {
            'version': '1.0.0',
            'last_update': None,
            'update_history': []
        }

    def _save_local_version(self):
        """保存本地版本信息"""
        with open(self.version_file, 'w', encoding='utf-8') as f:
            json.dump(self.local_version, f, indent=2, ensure_ascii=False)

    def get_local_version(self):
        """获取本地版本号"""
        return self.local_version.get('version', '1.0.0')

    def calculate_file_md5(self, file_path):
        """计算文件MD5值"""
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()

    def get_local_files_manifest(self, exclude_dirs=None):
        """
        生成本地文件清单

        Args:
            exclude_dirs: 排除的目录列表

        Returns:
            dict: 文件清单 {相对路径: md5}
        """
        if exclude_dirs is None:
            exclude_dirs = ['backup', 'Transfer Files', '__pycache__', 'log', 'updates', '__pycache__']

        manifest = {}

        for root, dirs, files in os.walk(self.client_dir):
            # 排除指定目录
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]

            for file in files:
                # 跳过隐藏文件和临时文件
                if file.startswith('.') or file.endswith('.tmp') or file.endswith('.bak'):
                    continue

                file_path = Path(root) / file
                try:
                    rel_path = file_path.relative_to(self.client_dir)
                    md5 = self.calculate_file_md5(file_path)
                    manifest[str(rel_path).replace('\\', '/')] = md5
                except Exception:
                    continue

        return manifest

    def create_backup(self):
        """
        创建更新前的备份

        Returns:
            Path: 备份目录路径
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = self.backup_dir / f'backup_{timestamp}'
        backup_path.mkdir(parents=True, exist_ok=True)

        # 备份关键文件
        exclude_dirs = ['backup', 'Transfer Files', '__pycache__', 'log', 'updates']

        for root, dirs, files in os.walk(self.client_dir):
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]

            for file in files:
                if file.startswith('.') or file.endswith('.tmp'):
                    continue

                file_path = Path(root) / file
                rel_path = file_path.relative_to(self.client_dir)
                target_path = backup_path / rel_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, target_path)

        # 保存备份信息
        backup_info = {
            'timestamp': timestamp,
            'version': self.get_local_version(),
            'created_at': datetime.now().isoformat()
        }
        with open(backup_path / 'backup_info.json', 'w', encoding='utf-8') as f:
            json.dump(backup_info, f, indent=2)

        return backup_path

    def rollback(self, backup_path=None):
        """
        回滚到之前的版本

        Args:
            backup_path: 备份路径，如果为None则使用最新的备份

        Returns:
            dict: 回滚结果
        """
        try:
            if backup_path:
                backup_path = Path(backup_path)
            else:
                # 查找最新的备份
                backups = sorted(self.backup_dir.glob('backup_*'), reverse=True)
                if not backups:
                    return {'status': 'error', 'message': '没有可用的备份'}
                backup_path = backups[0]

            if not backup_path.exists():
                return {'status': 'error', 'message': f'备份不存在: {backup_path}'}

            # 读取备份信息
            backup_info_file = backup_path / 'backup_info.json'
            if backup_info_file.exists():
                with open(backup_info_file, 'r', encoding='utf-8') as f:
                    backup_info = json.load(f)
            else:
                backup_info = {}

            # 恢复文件
            exclude_dirs = ['backup', 'Transfer Files', '__pycache__', 'log', 'updates']

            for root, dirs, files in os.walk(backup_path):
                dirs[:] = [d for d in dirs if d not in exclude_dirs]

                for file in files:
                    if file == 'backup_info.json':
                        continue

                    file_path = Path(root) / file
                    rel_path = file_path.relative_to(backup_path)
                    target_path = self.client_dir / rel_path
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, target_path)

            # 恢复版本信息
            if backup_info.get('version'):
                self.local_version['version'] = backup_info['version']
                self._save_local_version()

            return {
                'status': 'success',
                'message': f'已回滚到版本 {backup_info.get("version", "unknown")}',
                'backup_path': str(backup_path)
            }

        except Exception as e:
            return {'status': 'error', 'message': f'回滚失败: {str(e)}'}

    def apply_update(self, update_data, new_version, update_type='incremental'):
        """
        应用更新（原子操作）

        Args:
            update_data: 更新数据（bytes或dict）
            new_version: 新版本号
            update_type: 更新类型 'incremental' 或 'full'

        Returns:
            dict: 更新结果
        """
        # 检查是否正在进行更新
        if self.lock_file.exists():
            return {'status': 'error', 'message': '正在执行其他更新操作'}

        try:
            # 创建锁文件
            with open(self.lock_file, 'w') as f:
                f.write(f'Update started at {datetime.now().isoformat()}')

            # 创建备份
            backup_path = self.create_backup()

            try:
                if update_type == 'full':
                    # 全量更新：解压整个包
                    result = self._apply_full_update(update_data)
                else:
                    # 增量更新：逐个文件更新
                    result = self._apply_incremental_update(update_data)

                if result['status'] == 'success':
                    # 更新版本信息
                    self.local_version['version'] = new_version
                    self.local_version['last_update'] = datetime.now().isoformat()
                    self.local_version['update_history'].append({
                        'version': new_version,
                        'timestamp': datetime.now().isoformat(),
                        'type': update_type
                    })
                    # 保留最近10条更新记录
                    self.local_version['update_history'] = self.local_version['update_history'][-10:]
                    self._save_local_version()

                    return result
                else:
                    # 更新失败，回滚
                    self.rollback(backup_path)
                    return result

            except Exception as e:
                # 发生异常，回滚
                self.rollback(backup_path)
                return {'status': 'error', 'message': f'更新失败，已回滚: {str(e)}'}

        finally:
            # 删除锁文件
            if self.lock_file.exists():
                self.lock_file.unlink()

    def _apply_full_update(self, update_data):
        """应用全量更新"""
        try:
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # 保存压缩包
                zip_path = temp_path / 'update.zip'
                with open(zip_path, 'wb') as f:
                    f.write(update_data)

                # 解压
                extract_dir = temp_path / 'extracted'
                with zipfile.ZipFile(zip_path, 'r') as zipf:
                    zipf.extractall(extract_dir)

                # 复制文件到客户端目录（跳过config.json，保留用户配置）
                exclude_dirs = ['backup', 'Transfer Files', '__pycache__', 'log', 'updates']
                exclude_files = ['config.json']

                for root, dirs, files in os.walk(extract_dir):
                    dirs[:] = [d for d in dirs if d not in exclude_dirs]

                    for file in files:
                        # 跳过配置文件
                        if file in exclude_files:
                            continue

                        file_path = Path(root) / file
                        rel_path = file_path.relative_to(extract_dir)
                        target_path = self.client_dir / rel_path
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file_path, target_path)

                return {'status': 'success', 'message': '全量更新完成'}

        except Exception as e:
            return {'status': 'error', 'message': f'全量更新失败: {str(e)}'}

    def _apply_incremental_update(self, update_data):
        """应用增量更新"""
        try:
            # update_data 应该是 dict: {文件路径: 文件内容(bytes)}
            updated_files = []
            skipped_files = []
            failed_files = []
            preserve_config_files = {'config.json'}  # 保留用户自定义的配置文件

            for file_path, content in update_data.items():
                # 跳过用户配置文件（保留本地配置）
                if file_path in preserve_config_files:
                    skipped_files.append(file_path)
                    continue

                try:
                    target_path = self.client_dir / file_path
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    with open(target_path, 'wb') as f:
                        f.write(content)

                    updated_files.append(file_path)
                except Exception as e:
                    failed_files.append(f'{file_path}: {str(e)}')

            if failed_files:
                return {
                    'status': 'error',
                    'message': f'部分文件更新失败: {"; ".join(failed_files[:5])}',
                    'updated_files': updated_files,
                    'failed_files': failed_files,
                    'skipped_files': skipped_files
                }

            return {
                'status': 'success',
                'message': f'增量更新完成，更新了 {len(updated_files)} 个文件，保留 {len(skipped_files)} 个配置文件',
                'updated_files': updated_files,
                'skipped_files': skipped_files
            }

        except Exception as e:
            return {'status': 'error', 'message': f'增量更新失败: {str(e)}'}

    def delete_files(self, files_to_delete):
        """删除指定的文件"""
        deleted = []
        failed = []

        for file_path in files_to_delete:
            try:
                target_path = self.client_dir / file_path
                if target_path.exists():
                    target_path.unlink()
                    deleted.append(file_path)
            except Exception as e:
                failed.append(f'{file_path}: {str(e)}')

        return {'deleted': deleted, 'failed': failed}

    def list_backups(self):
        """列出所有备份"""
        backups = []
        for backup_dir in sorted(self.backup_dir.glob('backup_*'), reverse=True):
            info_file = backup_dir / 'backup_info.json'
            if info_file.exists():
                with open(info_file, 'r', encoding='utf-8') as f:
                    info = json.load(f)
                backups.append({
                    'path': str(backup_dir),
                    'version': info.get('version', 'unknown'),
                    'timestamp': info.get('timestamp', 'unknown'),
                    'created_at': info.get('created_at', 'unknown')
                })
        return backups

    def cleanup_old_backups(self, keep_count=5):
        """清理旧备份，只保留最近的几个"""
        backups = sorted(self.backup_dir.glob('backup_*'), reverse=True)
        removed = []

        for backup in backups[keep_count:]:
            try:
                shutil.rmtree(backup)
                removed.append(str(backup))
            except:
                pass

        return removed
