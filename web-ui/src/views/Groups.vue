<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { nodesApi } from '../api/nodes'
import { useNodeStore } from '../stores/nodes'
import { useUserStore } from '../stores/user'

const nodeStore = useNodeStore()
const userStore = useUserStore()
const groups = ref<any[]>([])
const dialogVisible = ref(false)
const form = ref({ name: '', description: '', color: '#409eff' })

onMounted(async () => {
  const res = await nodesApi.listGroups()
  groups.value = res.data
})

async function createGroup() {
  await nodesApi.createGroup(form.value)
  dialogVisible.value = false
  form.value = { name: '', description: '', color: '#409eff' }
  const res = await nodesApi.listGroups()
  groups.value = res.data
}

async function deleteGroup(id: string) {
  await nodesApi.deleteGroup(id)
  const res = await nodesApi.listGroups()
  groups.value = res.data
}
</script>

<template>
  <div>
    <h2>分组管理</h2>
    <el-button v-if="userStore.hasPermission('groups.manage')" type="primary" @click="dialogVisible = true" style="margin-bottom:16px">创建分组</el-button>

    <el-table :data="groups" stripe>
      <el-table-column prop="name" label="分组名称" width="200" />
      <el-table-column prop="description" label="描述" />
      <el-table-column prop="color" label="颜色" width="100">
        <template #default="{ row }">
          <el-tag :color="row.color" size="small" style="color:#fff">{{ row.color }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="node_count" label="节点数" width="100" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button v-if="userStore.hasPermission('groups.manage')" size="small" type="danger" @click="deleteGroup(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="创建分组" width="400px">
      <el-form :model="form">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如: web-servers" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" />
        </el-form-item>
        <el-form-item label="颜色">
          <el-color-picker v-model="form.color" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="createGroup">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>
