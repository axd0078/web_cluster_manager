<script setup lang="ts">
import { useWebSocket } from './composables/useWebSocket'

// Connect WebSocket when user is authenticated
useWebSocket()
</script>

<template>
  <router-view />
</template>

<style>
html, body, #app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Microsoft YaHei', Arial, sans-serif;
}
</style>
