<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>报表导出管理</h2>
        <p class="page-desc">维护报表任务，围绕报表名称、统计范围、统计周期、导出格式做登记、生成与下载。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!selectedIds.size" @click="packageRows">
          打包下载（{{ selectedIds.size }}）
        </button>
        <button class="btn" type="button" @click="exportRows">导出报表导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>任务状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th class="row-check"><input type="checkbox" :checked="allSelected" :disabled="!rows.length" @change="toggleAll" /></th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>失败原因</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td class="row-check">
            <input
              type="checkbox"
              :checked="selectedIds.has(Number(row.id))"
              :disabled="row.status !== '已完成'"
              @change="toggleOne(Number(row.id))"
            />
          </td>
          <td v-for="column in columns" :key="column">
            <RouterLink v-if="column === '报表名称'" class="link" :to="`/report/${row.id}`">{{ row[column] ?? '—' }}</RouterLink>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td>{{ row['失败原因'] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-if="row.status === '排队中'"
              class="link"
              type="button"
              @click="runAction('生成报表', row)"
            >
              生成报表
            </button>
            <button
              v-if="row.status === '已失败'"
              class="link"
              type="button"
              @click="runAction('重试任务', row)"
            >
              重试任务
            </button>
            <button
              v-if="row.status === '已完成'"
              class="link"
              type="button"
              @click="downloadRow(row)"
            >
              下载报表
            </button>
            <RouterLink class="link" :to="`/report/${row.id}`">详情</RouterLink>
            <span v-if="row.status === '生成中'">生成中…</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无报表导出数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条报表导出记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/report'
const columns = ['报表名称', '统计范围', '统计周期', '导出格式', '任务状态', '生成时间']
const statuses = ['排队中', '生成中', '已完成', '已失败']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const selectedIds = ref(new Set<number>())

const stats = computed(() => [
  { label: '排队任务', value: countByStatus('排队中') },
  { label: '生成中任务', value: countByStatus('生成中') },
  { label: '完成任务', value: countByStatus('已完成') },
  { label: '失败任务', value: countByStatus('已失败') },
])

const selectableRows = computed(() => rows.value.filter((row) => row.status === '已完成'))
const allSelected = computed(
  () => selectableRows.value.length > 0 &&
    selectableRows.value.every((row) => selectedIds.value.has(Number(row.id))),
)

function countByStatus(status: string): number {
  return rows.value.filter((row) => row.status === status).length
}

function toggleOne(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selectedIds.value = next
}

function toggleAll() {
  selectedIds.value = allSelected.value
    ? new Set()
    : new Set(selectableRows.value.map((row) => Number(row.id)))
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

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

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '报表动作未生效，请稍后重试')
    }
    selectedIds.value = new Set()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表导出操作失败'
  }
}

async function downloadRow(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/download`)
    if (!response.ok) {
      throw new Error(await readErrorMessage(response, '报表文件下载失败'))
    }
    await saveBlob(response)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表文件下载失败'
  }
}

async function packageRows() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/package`, {
      method: 'POST',
      body: JSON.stringify({ ids: [...selectedIds.value] }),
    })
    if (!response.ok) {
      throw new Error(await readErrorMessage(response, '报表打包下载失败'))
    }
    await saveBlob(response)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表打包下载失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(
    Object.entries(filters.value).filter(([, value]) => value !== '') as [string, string][],
  ).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('报表任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const visibleIds = new Set(rows.value.map((row: Row) => Number(row.id)))
    selectedIds.value = new Set([...selectedIds.value].filter((id) => visibleIds.has(id)))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表导出列表读取失败'
  }
}

onMounted(reload)
</script>
