#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import platform
import psutil


class SystemMonitor:
    """系统监控器"""
    def __init__(self):
        self.monitoring = False
        self.lock = threading.Lock()
    
    def get_system_info(self):
        """获取系统信息"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            # Windows系统使用C盘，Linux使用/
            if platform.system() == 'Windows':
                disk = psutil.disk_usage('C:')
            else:
                disk = psutil.disk_usage('/')
            
            return {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_total': memory.total,
                'memory_used': memory.used,
                'disk_percent': disk.percent,
                'disk_total': disk.total,
                'disk_used': disk.used,
                'os': platform.system(),
                'os_version': platform.version(),
                'hostname': platform.node()
            }
        except Exception as e:
            return {'error': str(e)}
    
    def start_monitoring(self):
        with self.lock:
            self.monitoring = True
    
    def stop_monitoring(self):
        with self.lock:
            self.monitoring = False
    
    def is_monitoring(self):
        with self.lock:
            return self.monitoring