<script setup>
// 输入框下方的计数文字；点击弹出「会话统计」：请求和工具记录的平均值。每次点开都重新取一次，所以总是最新的。
import { h, ref } from 'vue'
import { NButton, NDataTable, NModal, NPopover } from 'naive-ui'
import { getRequestContent, getSessionStats, getSessionStatsDetail } from '../api'

const props = defineProps({
  sessionId: { type: String, required: true },
  label: { type: String, required: true }, // 计数文字，例如「3 次请求 · 1 轮 · 2 次工具」
})

const stats = ref(null)
const detail = ref(null) // 明细列表；点「明细」按钮时才取
const showDetail = ref(false)

// 展开的请求内容：{ 请求 id: { input, output } | 'loading' | 'error' }；第一次展开时才取，因为内容很大
const contents = ref({})

async function loadContent(row) {
  if (contents.value[row.id]) return
  contents.value[row.id] = 'loading'
  try {
    contents.value[row.id] = await getRequestContent(props.sessionId, row.id)
  } catch {
    contents.value[row.id] = 'error'
  }
}

const pretty = (v) => JSON.stringify(v, null, 2)
const sec = (v) => v.toFixed(2)

// 表格列：n-data-table 用列配置而不是 <el-table-column>；type: 'expand' 的列放展开箭头，
// expandable 返回 false 的行（工具没有路径）不显示箭头
const requestColumns = [
  { type: 'expand', renderExpand: renderRequestContent },
  { title: '响应时间（秒）', key: 'seconds', minWidth: 90, render: (r) => sec(r.seconds) },
  { title: '输入 token', key: 'input_tokens', minWidth: 80 },
  { title: '输出 token', key: 'output_tokens', minWidth: 80 },
]
const toolColumns = [
  { type: 'expand', expandable: (x) => !!x.paths?.length, renderExpand: (x) => h('div', { class: 'json' }, x.paths.map((p) => h('div', p))) },
  { title: '工具类型', key: 'name', minWidth: 90 },
  { title: '耗时（秒）', key: 'seconds', minWidth: 80, render: (x) => sec(x.seconds) },
  { title: '读文件', key: 'files_read', minWidth: 70 },
  { title: '写文件', key: 'files_written', minWidth: 70 },
]
const turnColumns = [
  { type: 'expand', renderExpand: renderTurn },
  { title: '轮 #', key: 'id', width: 64 },
  { title: '耗时（秒）', key: 'seconds', minWidth: 90, render: (r) => sec(r.seconds) },
  { title: '请求次数', key: 'requests', minWidth: 80 },
  { title: '工具次数', key: 'tools', minWidth: 80 },
]

// 展开一个请求：第一次展开才取内容（很大）
function renderRequestContent(r) {
  const c = contents.value[r.id]
  if (!c?.input) return h('div', { class: 'json' }, c === 'error' ? '加载失败' : '加载中…')
  return h('div', { class: 'json' }, [
    h('div', { class: 'group' }, '输入'),
    h('pre', pretty(c.input)),
    h('div', { class: 'group' }, '输出'),
    h('pre', pretty(c.output)),
  ])
}

// 展开一轮：这一轮里的请求表和工具表
function renderTurn(row) {
  return h('div', { class: 'expand' }, [
    h('div', { class: 'group' }, `请求（${row.request_list.length}）`),
    h(NDataTable, {
      data: row.request_list,
      columns: requestColumns,
      rowKey: (r) => r.id,
      size: 'small',
      scrollX: 360,
      'onUpdate:expandedRowKeys': (keys) => keys.forEach((id) => loadContent({ id })),
    }),
    h('div', { class: 'group' }, `工具（${row.tool_list.length}）`),
    h(NDataTable, { data: row.tool_list, columns: toolColumns, rowKey: (x) => x.id, size: 'small', scrollX: 360 }),
  ])
}

async function openDetail() {
  contents.value = {}
  showDetail.value = true
  detail.value = null
  try {
    detail.value = await getSessionStatsDetail(props.sessionId)
  } catch {
    detail.value = { turns: [] }
  }
}

async function load() {
  try {
    stats.value = await getSessionStats(props.sessionId)
  } catch {
    stats.value = null
  }
}

// 没有记录时后端给 null，显示成「-」
const num = (v, digits = 2, unit = '') => (v == null ? '-' : `${v.toFixed(digits)}${unit}`)
</script>

<template>
  <n-popover trigger="click" placement="top" :width="260" @update:show="(show) => show && load()">
    <template #trigger>
      <span class="counts" role="button" title="点击查看会话统计"><span class="full">{{ label }}</span><span class="short">统计</span></span>
    </template>
    <div class="title">
      <span>会话统计<span class="title-counts">（{{ label }}）</span></span>
      <n-button size="small" text type="primary" @click="openDetail">明细</n-button>
    </div>
    <template v-if="stats">
      <div class="group">轮（平均）</div>
      <div class="line"><span>响应时间</span><span>{{ num(stats.turn.avg_seconds, 2, ' 秒') }}</span></div>
      <div class="line"><span>请求次数</span><span>{{ num(stats.turn.avg_requests) }}</span></div>
      <div class="line"><span>工具使用次数</span><span>{{ num(stats.turn.avg_tools) }}</span></div>
      <div class="group">请求（平均）</div>
      <div class="line"><span>响应时间</span><span>{{ num(stats.request.avg_seconds, 2, ' 秒') }}</span></div>
      <div class="line"><span>输入 token</span><span>{{ num(stats.request.avg_input_tokens, 0) }}</span></div>
      <div class="line"><span>输出 token</span><span>{{ num(stats.request.avg_output_tokens, 0) }}</span></div>
      <div class="group">工具（平均）</div>
      <div class="line"><span>使用时间</span><span>{{ num(stats.tool.avg_seconds, 2, ' 秒') }}</span></div>
      <div class="line"><span>读文件数</span><span>{{ num(stats.tool.avg_files_read) }}</span></div>
      <div class="line"><span>写文件数</span><span>{{ num(stats.tool.avg_files_written) }}</span></div>
    </template>
    <div v-else class="line">加载中…</div>
  </n-popover>

  <n-modal v-model:show="showDetail" preset="card" title="会话统计明细（最近的在前）" class="stats-dialog" :style="{ width: 'min(640px, 94vw)' }">
    <n-data-table
      v-if="detail"
      :data="detail.turns"
      :columns="turnColumns"
      :row-key="(row) => row.id"
      size="small"
      :max-height="420"
      :scroll-x="400"
    />
    <div v-else>加载中…</div>
  </n-modal>
</template>

<!-- 表格展开内容是用 h() 渲染的，不带 scoped 标记，所以这几条样式放在全局块里，限定在对话框内 -->
<style>
.stats-dialog .group {
  margin: 8px 0 4px;
  font-size: 12px;
  color: #909399;
}
.stats-dialog .json {
  font-size: 12px;
  word-break: break-all;
}
.stats-dialog .json pre {
  margin: 0 0 8px;
  max-height: 240px;
  overflow: auto; /* 输入是整段对话，可能很长：框内滚动，窄屏也不撑破对话框 */
  white-space: pre-wrap;
  background: #f5f7fa;
  padding: 6px 8px;
  border-radius: 4px;
}
.stats-dialog .expand {
  padding: 4px 8px 8px 24px;
}
</style>

<style scoped>
.counts {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  cursor: pointer;
}
/* 默认显示完整计数；窄屏只留「统计」两个字，把位置让给同一行的其他控件 */
.short {
  display: none;
}
/* 弹窗标题里始终带上计数：弹窗挂在 body 上，查不到输入区有多宽，输入区只剩「统计」时靠它补上 */
.title-counts {
  font-size: 12px;
  font-weight: normal;
  color: #909399;
}
/* 输入区放不下完整计数时，只留「统计」两个字（和 ModelPicker 的压缩顺序配合） */
@container composer (max-width: 600px) {
  .full {
    display: none;
  }
  .short {
    display: inline;
  }
}
.counts:hover {
  color: #409eff;
}
.title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  margin-bottom: 8px;
}
.group {
  margin: 8px 0 4px;
  font-size: 12px;
  color: #909399;
}
.line {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  line-height: 1.8;
}
/* 窄屏（手机 390px）：对话框内边距收小；表格列放不下时在表格内横向滚动（scroll-x） */
@media (max-width: 600px) {
  :global(.stats-dialog .n-card__content) {
    padding: 8px 12px 16px;
  }
}
</style>
