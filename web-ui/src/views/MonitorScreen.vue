<script setup lang="ts">
import { ref } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const { connected, lastMessage, send } = useWebSocket()

const testPayload = ref('')

function sendTest() {
  send({ type: 'ping', payload: { time: new Date().toISOString() } })
}
</script>

<template>
  <div style="padding: 20px">
    <h2>WebSocket 连接测试</h2>
    <el-card>
      <p>连接状态: <el-tag :type="connected ? 'success' : 'danger'">{{ connected ? '已连接' : '未连接' }}</el-tag></p>
      <p>最新消息: {{ lastMessage }}</p>
      <el-button @click="sendTest">发送 Ping</el-button>
    </el-card>
  </div>
</template>
