<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>报表导出管理</h2>
        <p class="page-desc">维护报表任务，围绕报表名称、统计范围、统计周期、导出格式做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记报表任务</button>
        <button class="btn" type="button" @click="downloadBundle">打包下载选中报表</button>
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
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th class="check-col">
            <input type="checkbox" :checked="allSelected" @change="toggleAll" />
          </th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td class="check-col">
            <input
              type="checkbox"
              :checked="selected.has(Number(row.id))"
              @change="toggleSelect(Number(row.id))"
            />
          </td>
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无报表导出数据，可先登记报表任务</td>
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

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/report'
const columns = ["报表名称", "统计范围", "统计周期", "导出格式", "任务状态", "生成时间", "失败原因"]
const actions = ["生成报表", "重试任务", "下载报表"]
const statuses = ["排队中", "生成中", "已完成", "已失败"]
const stats = [{"label": "排队任务", "value": 0}, {"label": "生成中任务", "value": 0}, {"label": "失败任务", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const selected = ref<Set<number>>(new Set())

const allSelected = computed(
  () => rows.value.length > 0 && rows.value.every((row) => selected.value.has(Number(row.id))),
)

function toggleSelect(id: number) {
  const next = new Set(selected.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selected.value = next
}

function toggleAll() {
  selected.value = allSelected.value ? new Set() : new Set(rows.value.map((row) => Number(row.id)))
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '报表任务登记入口尚未接入审批流'
}

function filenameFrom(response: Response, fallback: string): string {
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  return match ? decodeURIComponent(match[1]) : fallback
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function downloadFile(path: string, fallback: string) {
  const response = await request(path)
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.detail ?? '报表文件下载失败，请稍后重试')
  }
  saveBlob(await response.blob(), filenameFrom(response, fallback))
}

async function downloadRow(row: Row) {
  errorMessage.value = ''
  try {
    await downloadFile(`${ENDPOINT}/${row.id}/download`, `报表-${row.id}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表文件下载失败'
  }
}

async function downloadBundle() {
  if (!selected.value.size) {
    errorMessage.value = '请先勾选要打包下载的报表任务'
    return
  }
  errorMessage.value = ''
  try {
    const ids = [...selected.value].join(',')
    await downloadFile(`${ENDPOINT}/bundle?ids=${ids}`, '报表打包.zip')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表打包下载失败'
  }
}

async function runAction(action: string, row: Row) {
  if (action === '下载报表') {
    await downloadRow(row)
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    await reload()
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '报表导出动作未生效，请稍后重试')
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表导出操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('报表任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报表导出列表读取失败'
  }
}

onMounted(reload)
</script>
