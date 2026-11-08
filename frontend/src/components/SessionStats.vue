<script setup>
// 输入框下方的计数文字；点击弹出「会话统计」：请求和工具记录的平均值。每次点开都重新取一次，所以总是最新的。
import { ref } from 'vue'
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
// 没有路径的工具行不显示展开箭头
const toolRowClass = ({ row }) => (row.paths?.length ? '' : 'no-expand')

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
  <el-popover trigger="click" placement="top" :width="260" @show="load">
    <template #reference>
      <span class="counts" role="button" title="点击查看会话统计"><span class="full">{{ label }}</span><span class="short">统计</span></span>
    </template>
    <div class="title">
      <span>会话统计<span class="title-counts">（{{ label }}）</span></span>
      <el-button size="small" link type="primary" @click="openDetail">明细</el-button>
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
  </el-popover>

  <el-dialog v-model="showDetail" title="会话统计明细（最近的在前）" width="min(640px, 94vw)" class="stats-dialog" append-to-body>
    <el-table v-if="detail" :data="detail.turns" row-key="id" size="small" max-height="420">
      <!-- 点左边的箭头展开：这一轮里的请求和工具 -->
      <el-table-column type="expand" width="40">
        <template #default="{ row }">
          <div class="expand">
            <div class="group">请求（{{ row.request_list.length }}）</div>
            <el-table :data="row.request_list" row-key="id" size="small" @expand-change="loadContent">
              <el-table-column type="expand" width="36">
                <template #default="{ row: r }">
                  <div v-if="contents[r.id]?.input" class="json">
                    <div class="group">输入</div>
                    <pre>{{ pretty(contents[r.id].input) }}</pre>
                    <div class="group">输出</div>
                    <pre>{{ pretty(contents[r.id].output) }}</pre>
                  </div>
                  <div v-else class="json">{{ contents[r.id] === 'error' ? '加载失败' : '加载中…' }}</div>
                </template>
              </el-table-column>
              <el-table-column min-width="90" label="响应时间（秒）"><template #default="{ row: r }">{{ r.seconds.toFixed(2) }}</template></el-table-column>
              <el-table-column min-width="80" prop="input_tokens" label="输入 token" />
              <el-table-column min-width="80" prop="output_tokens" label="输出 token" />
            </el-table>
            <div class="group">工具（{{ row.tool_list.length }}）</div>
            <el-table :data="row.tool_list" row-key="id" size="small" :row-class-name="toolRowClass">
              <el-table-column type="expand" width="36">
                <template #default="{ row: x }">
                  <div class="json">
                    <div v-for="p in x.paths" :key="p">{{ p }}</div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column min-width="90" prop="name" label="工具类型" />
              <el-table-column min-width="80" label="耗时（秒）"><template #default="{ row: x }">{{ x.seconds.toFixed(2) }}</template></el-table-column>
              <el-table-column min-width="70" prop="files_read" label="读文件" />
              <el-table-column min-width="70" prop="files_written" label="写文件" />
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="id" label="轮 #" width="64" />
      <el-table-column min-width="90" label="耗时（秒）"><template #default="{ row }">{{ row.seconds.toFixed(2) }}</template></el-table-column>
      <el-table-column min-width="80" prop="requests" label="请求次数" />
      <el-table-column min-width="80" prop="tools" label="工具次数" />
    </el-table>
    <div v-else>加载中…</div>
  </el-dialog>
</template>

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
/* 窄屏输入框下只剩「统计」，所以把计数文字补在弹窗标题后面 */
.title-counts {
  display: none;
  font-size: 12px;
  font-weight: normal;
  color: #909399;
}
@media (max-width: 768px) {
  .full {
    display: none;
  }
  .short {
    display: inline;
  }
  .title-counts {
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
.json {
  font-size: 12px;
  word-break: break-all;
}
.json pre {
  margin: 0 0 8px;
  max-height: 240px;
  overflow: auto; /* 输入是整段对话，可能很长：框内滚动，窄屏也不撑破对话框 */
  white-space: pre-wrap;
  background: #f5f7fa;
  padding: 6px 8px;
  border-radius: 4px;
}
:deep(.no-expand .el-table__expand-column .cell) {
  visibility: hidden;
}
.expand {
  padding: 4px 8px 8px 24px;
}
.line {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  line-height: 1.8;
}
/* 窄屏（手机 390px）：对话框两侧留边、内边距收小；表格列放不下时在表格内横向滚动 */
@media (max-width: 600px) {
  :global(.stats-dialog) {
    margin: 4vh auto;
  }
  :global(.stats-dialog .el-dialog__body) {
    padding: 8px 12px 16px;
  }
}
</style>
