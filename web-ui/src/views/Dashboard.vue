<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useNodeStore } from '../stores/nodes'
import * as echarts from 'echarts'

const nodeStore = useNodeStore()
const cpuChartRef = ref<HTMLDivElement>()
let cpuChart: echarts.ECharts | null = null

onMounted(async () => {
  await Promise.all([nodeStore.fetchNodes(), nodeStore.fetchHealth()])

  if (cpuChartRef.value) {
    cpuChart = echarts.init(cpuChartRef.value)
    cpuChart.setOption({
      title: { text: 'CPU 负载趋势', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'time', name: '时间' },
      yAxis: { type: 'value', name: '%', max: 100 },
      series: [{ type: 'line', name: '平均 CPU', smooth: true, data: [], areaStyle: { opacity: 0.15 } }],
    })
  }
})

onUnmounted(() => { cpuChart?.dispose() })
</script>

<template>
  <div class="dashboard">
    <h2>集群概览</h2>

    <!-- Health Score -->
    <el-row :gutter="16" style="margin-bottom: 20px">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="健康评分" :value="nodeStore.health?.health_score ?? '—'">
            <template #suffix>/ 100</template>
          </el-statistic>
          <el-progress
            :percentage="nodeStore.health?.health_score ?? 0"
            :status="(nodeStore.health?.health_score ?? 100) >= 80 ? 'success' : 'warning'"
          />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="在线节点" :value="nodeStore.health?.online_nodes ?? '—'">
            <template #suffix>/ {{ nodeStore.health?.total_nodes ?? 0 }}</template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="活跃告警" :value="nodeStore.health?.alerts_active ?? 0">
            <template #suffix>条</template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="平均 CPU" :value="nodeStore.health?.avg_cpu?.toFixed(1) ?? '—'">
            <template #suffix>%</template>
          </el-statistic>
        </el-card>
      </el-col>
    </el-row>

    <!-- Charts -->
    <el-row :gutter="16">
      <el-col :span="24">
        <el-card>
          <div ref="cpuChartRef" style="height: 300px" />
        </el-card>
      </el-col>
    </el-row>

    <!-- Quick stats row -->
    <el-row :gutter="16" style="margin-top: 20px">
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>资源使用</template>
          <el-space :size="24">
            <div>CPU: {{ nodeStore.health?.avg_cpu?.toFixed(1) ?? '—' }}%</div>
            <div>内存: {{ nodeStore.health?.avg_memory?.toFixed(1) ?? '—' }}%</div>
            <div>磁盘: {{ nodeStore.health?.avg_disk?.toFixed(1) ?? '—' }}%</div>
          </el-space>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>节点状态分布</template>
          <el-space :size="24">
            <el-tag type="success">在线: {{ nodeStore.health?.online_nodes ?? 0 }}</el-tag>
            <el-tag type="danger">离线: {{ nodeStore.health?.offline_nodes ?? 0 }}</el-tag>
            <el-tag type="info">维护: {{ nodeStore.health?.maintenance_nodes ?? 0 }}</el-tag>
          </el-space>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header>最近告警</template>
          <el-empty v-if="!nodeStore.health?.alerts_active" description="暂无告警" :image-size="40" />
          <el-alert v-else title="有活跃告警" type="warning" :closable="false" show-icon />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.dashboard h2 { margin: 0 0 16px; font-size: 22px; }
</style>
