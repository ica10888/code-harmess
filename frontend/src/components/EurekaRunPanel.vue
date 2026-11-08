<script setup>
// eureka 提示条下面的「工作流进度」：展示后端 runner 一个阶段一个阶段执行的情况（阶段状态 + 插件通过 webhook 打印的文字）。
// 刷新页面后，后端消息里带着 run 的 id 和状态，没跑完的会继续拉取，直到结束。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getEurekaRun } from '../api'
import { STATUS_LABEL, isFinished, nextDelay, progressSummary, shouldPoll, splitLinks, stageIcon } from '../utils/eurekaRun'

// update：每次拉到进度都通知上层（预览页靠它收集链接）；finished：亲眼看到它从未结束变成 done 时通知一次（预览页靠它跳转）
const emit = defineEmits(['update', 'finished'])
const props = defineProps({
  sessionId: { type: String, required: true },
  runId: { type: Number, required: true },
  initialStatus: { type: String, default: 'pending' },
})

const run = ref(null) // 后端返回的完整进度；还没取到时是 null
const failedToLoad = ref(false)
const open = ref(true)

const summary = computed(() => progressSummary(run.value))
const status = computed(() => run.value?.status ?? props.initialStatus)

let timer = null
let attempt = 0
let stopped = false
let sawUnfinished = !isFinished(props.initialStatus) // 刷新页面时已经结束的运行，不算「刚完成」，不触发跳转

async function refresh() {
  try {
    run.value = await getEurekaRun(props.sessionId, props.runId)
    failedToLoad.value = false
    emit('update', run.value)
    if (run.value.status === 'done' && sawUnfinished) emit('finished', run.value)
    sawUnfinished = sawUnfinished && !isFinished(run.value.status)
  } catch {
    failedToLoad.value = true // 网络抖一下不要紧，下一轮再试；结束前不会放弃
  }
  if (!stopped && shouldPoll(run.value)) timer = setTimeout(refresh, nextDelay(attempt++))
}

onMounted(refresh)
onBeforeUnmount(() => {
  stopped = true // 切换会话 / 离开页面：停止拉取，不留悬空的定时器
  clearTimeout(timer)
})
</script>

<template>
  <div class="run" :class="status" role="region" aria-label="工作流进度">
    <button class="head" type="button" :aria-expanded="open" @click="open = !open">
      <span class="title">工作流</span>
      <el-tag size="small" :type="stageIcon(status).kind">{{ STATUS_LABEL[status] ?? status }}</el-tag>
      <span class="summary">{{ summary.text }}</span>
      <span class="arrow">{{ open ? '▾' : '▸' }}</span>
    </button>

    <div v-show="open" class="body">
      <p v-if="failedToLoad && !run" class="note">进度暂时取不到，稍后会自动重试……</p>
      <ol v-if="run" class="stages">
        <li v-for="s in run.stages" :key="s.key" :class="s.status">
          <div class="row">
            <el-tag size="small" :type="stageIcon(s.status).kind" effect="plain">{{ STATUS_LABEL[s.status] ?? s.status }}</el-tag>
            <span class="stage-title">{{ s.title }}</span>
          </div>
          <ul v-if="s.logs.length" class="logs">
            <li v-for="(line, i) in s.logs" :key="i">
              <template v-for="(part, j) in splitLinks(line)" :key="j">
                <a v-if="part.href" :href="part.href" target="_blank" rel="noopener noreferrer">{{ part.text }}</a>
                <template v-else>{{ part.text }}</template>
              </template>
            </li>
          </ul>
          <p v-if="s.error" class="error">{{ s.error }}</p>
        </li>
      </ol>
      <p v-if="run?.status === 'failed' && run.error" class="error">失败原因：{{ run.error }}</p>
    </div>
  </div>
</template>

<style scoped>
.run {
  align-self: flex-start;
  width: 70%;
  max-width: 560px;
  margin-top: 6px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
}
.run.failed {
  border-color: #fbc4c4;
}
.run.done {
  border-color: #c2e7b0;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: none;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: #303133;
}
.title {
  font-weight: 600;
}
.summary {
  flex: 1;
  color: #909399;
}
.arrow {
  color: #909399;
}
.body {
  padding: 0 12px 10px;
}
.stages {
  margin: 0;
  padding: 0;
  list-style: none;
}
.stages > li {
  padding: 6px 0;
  border-top: 1px dashed #ebeef5;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.stage-title {
  color: #303133;
}
.pending .stage-title {
  color: #909399;
}
.logs {
  margin: 4px 0 0 4px;
  padding-left: 12px;
  color: #606266;
  border-left: 2px solid #ebeef5;
  list-style: none;
  white-space: pre-wrap;
  word-break: break-word;
}
.error {
  margin: 4px 0 0;
  color: #f56c6c;
  white-space: pre-wrap;
  word-break: break-word;
}
.note {
  margin: 6px 0;
  color: #909399;
}
</style>
