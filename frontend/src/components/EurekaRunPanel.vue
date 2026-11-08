<script setup>
// eureka 提示条下面的「工作流进度」：展示后端 runner 一个阶段一个阶段执行的情况（阶段状态 + 插件通过 webhook 打印的文字）。
// 刷新页面后，后端消息里带着 run 的 id 和状态，没跑完的会继续拉取，直到结束。
// 特效（借鉴 Inspira UI 的 Border Beam / Tracing Beam / Animated List）：运行中紫青流光环绕边框并带进度条；
// 每个阶段一个状态图标（等待是空心圆、进行中旋转、完成打钩弹出、失败抖动）；阶段之间用一条线串起来，完成的部分被「点亮」；
// 阶段和日志行依次滑入；全部完成后边框定格成绿色光晕。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NIcon, NTag } from 'naive-ui'
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  ChevronForward,
  EllipseOutline,
  GitNetworkOutline,
  SyncOutline,
} from '@vicons/ionicons5'
import { getEurekaRun } from '../api'
import { STATUS_LABEL, isFinished, jumpDelayMs, nextDelay, progressSummary, shouldPoll, splitLinks, stageIcon } from '../utils/eurekaRun'

// open-link：点了日志里的链接，交给上层在预览页新建页面显示；update：每次拉到进度都通知上层（预览页靠它收集链接）；finished：亲眼看到它从未结束变成 done 时通知一次（预览页靠它跳转）
const emit = defineEmits(['update', 'finished', 'open-link'])
const props = defineProps({
  sessionId: { type: String, required: true },
  runId: { type: Number, required: true },
  initialStatus: { type: String, default: 'pending' },
  gap: { type: Number, default: 0 }, // 面板的入场延迟（秒），由上层按提示框顺序算出；通知「完成」要等它和动画一起播完
})

const run = ref(null) // 后端返回的完整进度；还没取到时是 null
const failedToLoad = ref(false)
const open = ref(true)

// stageIcon 的 kind 沿用旧名字（danger / info），Naive UI 的 tag 叫 error / default
const tagType = (s) => ({ danger: 'error', info: 'default' }[stageIcon(s).kind] ?? stageIcon(s).kind)

const summary = computed(() => progressSummary(run.value))
const status = computed(() => run.value?.status ?? props.initialStatus)
const percent = computed(() => (summary.value.total ? Math.round((summary.value.done / summary.value.total) * 100) : 0))
// 阶段状态 → 图标；进行中的图标由样式让它转起来
const STAGE_ICON = { done: CheckmarkCircleOutline, failed: AlertCircleOutline, running: SyncOutline }
const iconOf = (s) => STAGE_ICON[s] ?? EllipseOutline

let timer = null
let finishTimer = null
let attempt = 0
let stopped = false
let sawUnfinished = !isFinished(props.initialStatus) // 刷新页面时已经结束的运行，不算「刚完成」，不触发跳转

async function refresh() {
  try {
    run.value = await getEurekaRun(props.sessionId, props.runId)
    failedToLoad.value = false
    emit('update', run.value)
    if (run.value.status === 'done' && sawUnfinished) {
      // 状态一变 done 就通知，上层会立刻切走页面，面板的动画还没播完：等动画播完再通知
      const finishedRun = run.value
      finishTimer = setTimeout(() => !stopped && emit('finished', finishedRun), jumpDelayMs(props.gap, finishedRun.stages.length))
    }
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
  clearTimeout(finishTimer)
})
</script>

<template>
  <div class="run" :class="status" role="region" aria-label="工作流进度">
    <button class="head" type="button" :aria-expanded="open" @click="open = !open">
      <n-icon class="flow" :size="16"><GitNetworkOutline /></n-icon>
      <span class="title">工作流</span>
      <n-tag size="small" :type="tagType(status)">{{ STATUS_LABEL[status] ?? status }}</n-tag>
      <span class="summary">{{ summary.text }}</span>
      <n-icon class="arrow" :class="{ open }" :size="14"><ChevronForward /></n-icon>
    </button>
    <!-- 进度条：宽度随完成的阶段数过渡；运行中表面有一道高光不停扫过 -->
    <div class="progress" aria-hidden="true"><div class="fill" :style="{ width: `${percent}%` }" /></div>

    <div v-show="open" class="body">
      <p v-if="failedToLoad && !run" class="note">进度暂时取不到，稍后会自动重试……</p>
      <ol v-if="run" class="stages">
        <li v-for="(s, i) in run.stages" :key="s.key" :class="s.status" :style="{ animationDelay: `calc(var(--gap, 0s) + ${i * 0.1}s)` }">
          <div class="row">
            <!-- :key 绑状态：状态一变，图标重新挂载，入场动画（打钩弹出 / 失败抖动）就再播一次 -->
            <n-icon :key="s.status" class="state" :size="18"><component :is="iconOf(s.status)" /></n-icon>
            <n-tag size="small" :type="tagType(s.status)">{{ STATUS_LABEL[s.status] ?? s.status }}</n-tag>
            <span class="stage-title">{{ s.title }}</span>
          </div>
          <ul v-if="s.logs.length" class="logs">
            <li v-for="(line, i) in s.logs" :key="i" class="log">
              <template v-for="(part, j) in splitLinks(line)" :key="j">
                <a v-if="part.href" :href="part.href" @click.prevent="emit('open-link', part.href)">{{ part.text }}</a>
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
/* 光束的角度：注册成角度类型才能被动画插值（不支持的浏览器里光束不转，边框仍是静态的） */
@property --run-angle {
  syntax: '<angle>';
  inherits: false;
  initial-value: 0deg;
}
.run {
  position: relative;
  align-self: flex-start;
  width: 70%;
  max-width: 560px;
  margin-top: 6px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  animation: run-in 0.5s cubic-bezier(0.2, 0.9, 0.3, 1.2) var(--gap, 0s) backwards;
  transition: border-color 0.4s ease, box-shadow 0.4s ease;
}
/* 流光：只露出 2px 的边（mask 挖空中间），一段紫青渐变沿边框转圈；只在进行中 / 等待时出现 */
.run::before {
  content: '';
  position: absolute;
  inset: -1px;
  padding: 2px;
  border-radius: 8px;
  background: conic-gradient(from var(--run-angle), transparent 0 65%, #7c5cff 80%, #3dd6ff 95%, transparent 100%);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.4s ease;
}
.run.running::before,
.run.pending::before {
  opacity: 1;
  animation: run-beam 2.6s linear infinite;
}
/* 终态：边框和光晕定格，动画结束后保留 */
.run.failed {
  border-color: #fbc4c4;
  box-shadow: 0 0 12px rgba(245, 108, 108, 0.25);
}
.run.done {
  border-color: #c2e7b0;
  box-shadow: 0 0 12px rgba(103, 194, 58, 0.3);
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
.flow {
  color: #7c5cff;
}
.run.done .flow {
  color: #67c23a;
}
.run.failed .flow {
  color: #f56c6c;
}
.run.running .flow {
  animation: flow-pulse 1.4s ease-in-out infinite;
}
/* 箭头：收起时朝右，展开时转 90° 朝下，悬停时向前挪一点 */
.arrow {
  color: #909399;
  transition: transform 0.3s ease;
}
.arrow.open {
  transform: rotate(90deg);
}
.head:hover .arrow:not(.open) {
  transform: translateX(3px);
}
.head:hover .arrow.open {
  transform: rotate(90deg) translateX(3px);
}
.progress {
  height: 3px;
  margin: 0 12px;
  border-radius: 2px;
  background: #ebeef5;
  overflow: hidden;
}
.fill {
  position: relative;
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, #7c5cff, #3dd6ff);
  transition: width 0.6s ease;
}
.run.done .fill {
  background: #67c23a;
}
.run.failed .fill {
  background: #f56c6c;
}
.run.running .fill::after,
.run.pending .fill::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.8), transparent);
  animation: fill-sweep 1.4s linear infinite;
}
.body {
  padding: 6px 12px 10px;
}
.stages {
  margin: 0;
  padding: 0;
  list-style: none;
}
.stages > li {
  position: relative;
  padding: 6px 0;
  border-top: 1px dashed #ebeef5;
  animation: stage-in 0.4s ease-out backwards;
}
/* 串起阶段的竖线：穿过各阶段的图标；完成的阶段这段线是绿色（被点亮），其余是灰色 */
.stages > li::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #ebeef5;
  transition: background 0.5s ease;
}
.stages > li.done::before {
  background: #95d475;
}
.stages > li.running::before {
  background: linear-gradient(180deg, #95d475, #7c5cff);
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.state {
  position: relative;
  z-index: 1; /* 盖在竖线上 */
  flex-shrink: 0;
  border-radius: 50%;
  background: #fff;
  color: #c0c4cc;
  animation: state-in 0.45s cubic-bezier(0.2, 0.9, 0.3, 1.5);
}
.done .state {
  color: #67c23a;
}
.failed .state {
  color: #f56c6c;
  animation: state-in 0.45s cubic-bezier(0.2, 0.9, 0.3, 1.5), run-shake 0.5s ease 0.2s;
}
.running .state {
  color: #7c5cff;
  animation: state-spin 1.1s linear infinite;
}
.stage-title {
  color: #303133;
}
.pending .stage-title {
  color: #909399;
}
.logs {
  margin: 4px 0 0 26px;
  padding-left: 12px;
  color: #606266;
  border-left: 2px solid #ebeef5;
  list-style: none;
  white-space: pre-wrap;
  word-break: break-word;
}
.log {
  animation: log-in 0.3s ease-out;
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
@keyframes run-in {
  0% {
    transform: translateY(10px) scale(0.97);
    opacity: 0;
  }
  100% {
    transform: none;
    opacity: 1;
  }
}
@keyframes run-beam {
  to {
    --run-angle: 360deg;
  }
}
@keyframes run-shake {
  0%,
  100% {
    transform: translateX(0);
  }
  25% {
    transform: translateX(-4px);
  }
  75% {
    transform: translateX(4px);
  }
}
@keyframes flow-pulse {
  50% {
    transform: scale(1.25);
    filter: drop-shadow(0 0 4px #7c5cff);
  }
}
@keyframes fill-sweep {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(100%);
  }
}
@keyframes stage-in {
  0% {
    transform: translateX(-12px);
    opacity: 0;
  }
}
@keyframes state-in {
  0% {
    transform: scale(0);
  }
}
@keyframes state-spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes log-in {
  0% {
    transform: translateX(-8px);
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .run,
  .run::before,
  .run.running::before,
  .run.pending::before,
  .run.running .flow,
  .fill::after,
  .stages > li,
  .state,
  .failed .state,
  .running .state,
  .log {
    animation: none;
  }
  .arrow,
  .fill,
  .stages > li::before {
    transition: none;
  }
  .head:hover .arrow:not(.open) {
    transform: none;
  }
  .head:hover .arrow.open {
    transform: rotate(90deg);
  }
}
@media (max-width: 768px) {
  .run {
    width: 90%;
  }
}
</style>
