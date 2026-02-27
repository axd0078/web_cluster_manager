#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading


class AddressPool:
    """服务端地址池管理器"""
    def __init__(self, allowed_addresses):
        self.allowed_addresses = set(allowed_addresses)
        self.lock = threading.Lock()
    
    def is_allowed(self, address):
        """检查地址是否在允许列表中"""
        with self.lock:
            return address in self.allowed_addresses
    
    def add_address(self, address):
        """添加允许的地址"""
        with self.lock:
            self.allowed_addresses.add(address)
    
    def remove_address(self, address):
        """移除地址"""
        with self.lock:
            self.allowed_addresses.discard(address)