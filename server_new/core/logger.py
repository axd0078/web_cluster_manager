#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime
from pathlib import Path


class Logger:
    """日志管理器"""
    def __init__(self, log_dir='logs'):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
    
    def log(self, target_ip, command, file_or_folder=None):
        """记录操作日志"""
        log_file = self.log_dir / f"{datetime.date.today()}.txt"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        log_entry = f"[{timestamp}] IP: {target_ip}, 命令: {command}"
        if file_or_folder:
            log_entry += f", 文件/文件夹: {file_or_folder}"
        log_entry += "\n"
        
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    
    def log_operation(self, operation_type, target_ip, details):
        """记录操作日志到操作日志文件（按IP创建文件夹）"""
        try:
            # 为每个IP创建单独的文件夹
            ip_log_dir = self.log_dir / target_ip
            ip_log_dir.mkdir(parents=True, exist_ok=True)
            
            # 在IP文件夹下创建操作日志文件
            operation_log_file = ip_log_dir / f"operation_{datetime.date.today()}.txt"
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] 操作类型: {operation_type}, 详情: {details}\n"
            with open(operation_log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            print(f"记录操作日志失败: {e}")
    
    def clean_log(self, log_date, target_ip=None):
        """清理指定日期的日志"""
        try:
            # log_date格式：YYYY-MM-DD
            deleted_files = []
            
            # 如果指定了IP，只删除该IP的日志
            if target_ip:
                ip_log_dir = self.log_dir / target_ip
                if ip_log_dir.exists():
                    operation_log_file = ip_log_dir / f"operation_{log_date}.txt"
                    if operation_log_file.exists():
                        operation_log_file.unlink()
                        deleted_files.append(str(operation_log_file))
            else:
                # 如果没有指定IP，删除所有IP文件夹下的操作日志
                if self.log_dir.exists():
                    for ip_dir in self.log_dir.iterdir():
                        if ip_dir.is_dir():
                            operation_log_file = ip_dir / f"operation_{log_date}.txt"
                            if operation_log_file.exists():
                                operation_log_file.unlink()
                                deleted_files.append(str(operation_log_file))
            
            if deleted_files:
                return {'status': 'success', 'message': f'日志文件已删除: {", ".join(deleted_files)}'}
            else:
                return {'status': 'error', 'message': f'指定日期的日志文件不存在: {log_date}'}
        except Exception as e:
            return {'status': 'error', 'message': f'清理日志失败: {str(e)}'}