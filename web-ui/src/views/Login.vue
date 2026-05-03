<template>
  <div class="login-container">
    <el-card class="login-card">
      <h2 class="login-title">集群管理系统</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="0">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码" :prefix-icon="Lock" show-password
            @keyup.enter="handleLogin" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" style="width: 100%" @click="handleLogin">
            登 录
          </el-button>
        </el-form-item>
      </el-form>
      <p v-if="error" class="login-error">{{ error }}</p>
      <p class="login-hint">默认账号: admin / admin123</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import { login } from '../api/auth'
import { useUserStore } from '../stores/user'

const router = useRouter()
const userStore = useUserStore()

const form = ref({ username: '', password: '' })
const loading = ref(false)
const error = ref('')

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    const res = await login(form.value)
    const { access_token, refresh_token, user } = res.data
    userStore.setAuth(access_token, refresh_token, user.username, user.role)
    router.push('/dashboard')
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex; align-items: center; justify-content: center;
  height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.login-card { width: 400px; padding: 8px; }
.login-title { text-align: center; margin-bottom: 24px; color: #303133; }
.login-error { color: #f56c6c; text-align: center; margin-top: -8px; }
.login-hint { color: #c0c4cc; text-align: center; margin-top: 8px; font-size: 13px; }
</style>
