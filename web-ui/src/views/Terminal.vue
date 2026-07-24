<script setup lang="ts">
import { ref } from 'vue'
import { useNodeStore } from '../stores/nodes'

const nodeStore = useNodeStore()
const command = ref('')
const output = ref('')

async function execute() {
  // Placeholder — full Xterm.js terminal in Phase 2
  output.value += `\n> ${command.value}\n(远程命令执行功能将在第二阶段通过 Xterm.js 实现)\n`
  command.value = ''
}
</script>

<template>
  <div>
    <h2>远程终端</h2>
    <el-card>
      <p>选择目标节点执行命令。完整 Web 终端体验 (Xterm.js) 将在第二阶段实现。</p>
      <el-select v-model="command" placeholder="选择目标节点" style="width:300px;margin-right:12px" filterable>
        <el-option v-for="n in nodeStore.onlineNodes" :key="n.id" :label="`${n.ip} (${n.hostname || '?'})`" :value="n.ip" />
      </el-select>
      <el-input
        v-model="command"
        placeholder="输入命令..."
        @keydown.enter="execute"
        style="margin-top:12px"
      >
        <template #append>
          <el-button @click="execute">执行</el-button>
        </template>
      </el-input>
      <pre style="background:#1e1e1e;color:#d4d4d4;padding:12px;margin-top:12px;min-height:200px;border-radius:4px">{{ output || '等待命令...' }}</pre>
    </el-card>
  </div>
</template>
