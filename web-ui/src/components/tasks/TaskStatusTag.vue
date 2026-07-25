<script setup lang="ts">
import { computed } from 'vue'
import { TASK_STATUS_LABELS, TASK_STATUS_TYPES } from './taskPresentation'

const props = defineProps<{ status: string }>()
const label = computed(() => TASK_STATUS_LABELS[props.status] || props.status)
const tagType = computed(() => TASK_STATUS_TYPES[props.status] || 'info')
</script>

<template>
  <el-tag :type="tagType">{{ label }}</el-tag>
</template>
