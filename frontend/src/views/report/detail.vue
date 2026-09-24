<template>
  <section class="page" data-module="report-detail">
    <header class="page-head">
      <div>
        <h2>报表任务详情</h2>
        <p class="page-desc">展示任务配置、生成结果与失败原因；动作入口与列表页一致。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/report">返回列表</RouterLink>
      </div>
    </header>

    <div v-if="entry" class="detail-panel">
      <dl class="detail-grid">
        <div v-for="field in detailFields" :key="field" class="detail-item">
          <dt>{{ field }}</dt>
          <dd>{{ entry[field] ?? '—' }}</dd>
        </div>
        <div class="detail-item">
          <dt>文件名</dt>
          <dd>{{ entry['文件名'] ?? '—' }}</dd>
        </div>
        <div class="detail-item">
          <dt>文件大小</dt>
          <dd>{{ fileSizeText }}</dd>
        </div>
        <div class="detail-item detail-item--full">
          <dt>失败原因</dt>
          <dd :class="{ 'error-text': entry['失败原因'] }">{{ entry['失败原因'] ?? '无' }}</dd>
        </div>
      </dl>

      <div class="page-actions">
        <button v-if="entry.status === '排队中'" class="btn primary" type="button" @click="runAction('生成报表')">
          生成报表
        </button>
        <button v-if="entry.status === '已失败'" class="btn primary" type="button" @click="runAction('重试任务')">
          重试任务
        </button>
        <button v-if="entry.status === '已完成'" class="btn primary" type="button" @click="download">
          下载报表
        </button>
        <span v-if="entry.status === '生成中'" class="page-desc">任务生成中，请稍后刷新…</span>
      </div>
    </div>

    <div v-else-if="!errorMessage" class="empty-state">任务加载中…</div>
    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Entry = Record<string, string | number | boolean | null>

const route = useRoute()
const ENDPOINT = '/api/report'
const detailFields = ['报表名称', '统计范围', '统计周期', '导出格式', '任务状态', '生成时间']

const entry = ref<Entry | null>(null)
const errorMessage = ref('')

const fileSizeText = computed(() => {
  const size = entry.value?.['文件大小']
  return typeof size === 'number' && size > 0 ? `${size} 字节` : '—'
})

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail || fallback
  } catch {
    return fallback
  }
}

function saveBlob(response: Response) {
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const filename = utf8Match
    ? decodeURIComponent(utf8Match[1])
    : `报表-${Date.now()}`
  return response.blob().then((blob) => {
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  })
}

async function load() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${route.params.id}`)
    if (!response.ok) {
      throw new Error(await readErrorMessage(response, '报表任务详情读取失败'))
    }
    entry.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表任务详情读取失败'
  }
}

async function runAction(action: string) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${route.params.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '报表动作未生效，请稍后重试')
    }
    await load()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表导出操作失败'
  }
}

async function download() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${route.params.id}/download`)
    if (!response.ok) {
      throw new Error(await readErrorMessage(response, '报表文件下载失败'))
    }
    await saveBlob(response)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表文件下载失败'
  }
}

onMounted(load)
</script>
