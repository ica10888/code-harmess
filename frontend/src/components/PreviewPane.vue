<script setup>
// 预览标签：把工作流日志里的超链接页面内嵌进来（iframe）。每个链接一个浏览器式的小标签，可切换；有刷新和新标签页打开。
import { computed, ref, watch } from 'vue'
import { NButton, NIcon, NInput } from 'naive-ui'
import { motion } from 'motion-v'
import { CloseOutline, CompassOutline, GlobeOutline, LinkOutline, OpenOutline, ReloadOutline } from '@vicons/ionicons5'

const props = defineProps({ links: { type: Array, default: () => [] } })

const closed = ref(new Set()) // 用户关掉的标签；链接本身来自工作流日志，关掉只是不再显示
const tabs = computed(() => props.links.filter((l) => !closed.value.has(l)))
const selected = ref(null)
const reloads = ref({}) // 每个链接的刷新次数；改它就让对应的 iframe 重新加载

// 标签变了（新的运行产出了链接，或关掉了一个）：当前选中的没了，就看最新的一个
watch(
  tabs,
  (list) => {
    if (!list.includes(selected.value)) selected.value = list[list.length - 1] ?? null
  },
  { immediate: true },
)
// 换了一批链接（新会话或新的运行）：已经不在列表里的「关闭记录」清掉，免得下次同地址出现时一直被藏着
watch(
  () => props.links,
  (links) => (closed.value = new Set([...closed.value].filter((l) => links.includes(l)))),
)

/** 关掉一个标签：关的是当前页，就切到它旁边的那个。 */
function close(link) {
  const i = tabs.value.indexOf(link)
  closed.value = new Set(closed.value).add(link)
  if (selected.value === link) selected.value = tabs.value[i] ?? tabs.value[i - 1] ?? null
}

/** 标签上显示的短名字：主机:端口 + 路径；不是合法地址就原样显示。 */
function tabTitle(link) {
  try {
    const u = new URL(link)
    return u.host + (u.pathname === '/' ? '' : u.pathname)
  } catch {
    return link
  }
}

/** 从外面（工作流进度里点了链接）打开一个页面：被关掉过的重新显示，并切到它。 */
function open(link) {
  const next = new Set(closed.value)
  next.delete(link)
  closed.value = next
  selected.value = link
}
defineExpose({ open })

const spinning = ref(false) // 刷新图标转一圈：点一下就转，转完（0.6 秒）复位
function refresh() {
  reloads.value[selected.value] = (reloads.value[selected.value] ?? 0) + 1
  spinning.value = true
  setTimeout(() => (spinning.value = false), 600)
}

// 被嵌页面和本页面同源时，不能同时给 allow-scripts 和 allow-same-origin，否则它能摘掉沙箱；跨源则可以，页面才能正常用 localStorage 等
function sandboxFor(link) {
  const base = 'allow-scripts allow-forms allow-popups allow-modals'
  try {
    return new URL(link).origin === location.origin ? base : `${base} allow-same-origin`
  } catch {
    return base
  }
}
</script>

<template>
  <div class="preview-pane">
    <div v-if="!tabs.length" class="empty">
      <n-icon class="empty-icon" :size="48"><CompassOutline /></n-icon>
      <div>工作流日志里出现超链接后，页面会显示在这里</div>
    </div>
    <template v-else>
      <!-- 浏览器式标签行：每个链接一个，点击切换；页面都保持加载，切换不丢状态 -->
      <div class="browser-tabs" role="tablist">
        <div
          v-for="l in tabs"
          :key="l"
          role="tab"
          class="btab"
          :class="{ active: l === selected }"
          :aria-selected="l === selected"
          :title="l"
          @click="selected = l"
          @auxclick.middle.prevent="close(l)"
        >
          <!-- 选中块：所有标签共用同一个 layout-id，切换时 motion-v 让它滑到新标签上（借鉴 Inspira UI 的 Animated Tabs） -->
          <motion.span
            v-if="l === selected"
            layout-id="preview-pill"
            class="pill"
            :transition="{ type: 'spring', stiffness: 400, damping: 32 }"
          />
          <n-icon class="globe"><GlobeOutline /></n-icon>
          <span class="name">{{ tabTitle(l) }}</span>
          <button type="button" class="x" :aria-label="`关闭 ${tabTitle(l)}`" @click.stop="close(l)">
            <n-icon><CloseOutline /></n-icon>
          </button>
        </div>
      </div>
      <div class="bar">
        <n-button size="small" class="act refresh" title="刷新当前页面" @click="refresh">
          <template #icon><n-icon :class="{ spin: spinning }"><ReloadOutline /></n-icon></template>
          刷新
        </n-button>
        <n-input :value="selected" size="small" readonly class="url">
          <template #prefix><n-icon><LinkOutline /></n-icon></template>
        </n-input>
        <n-button size="small" class="act open" tag="a" :href="selected" target="_blank" rel="noopener noreferrer">
          <template #icon><n-icon><OpenOutline /></n-icon></template>
          新标签页打开
        </n-button>
      </div>
      <div class="frames">
        <iframe
          v-for="l in tabs"
          v-show="l === selected"
          :key="`${l}-${reloads[l] ?? 0}`"
          class="frame"
          :src="l"
          :sandbox="sandboxFor(l)"
          :title="`预览 ${l}`"
        />
      </div>
      <div class="tip">如果页面空白，可能是目标网站禁止被内嵌，请用“新标签页打开”。</div>
    </template>
  </div>
</template>

<style scoped>
.preview-pane {
  container-type: inline-size; /* 按自身宽度适配窄框 */
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 8px 12px;
}
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.url {
  flex: 1;
  min-width: 0;
}
.browser-tabs {
  display: flex;
  gap: 2px;
  overflow-x: auto;
  border-bottom: 1px solid #e4e7ed;
}
.btab {
  position: relative; /* 选中块（.pill）绝对定位在它里面 */
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  max-width: 240px;
  padding: 6px 8px 6px 12px;
  border-radius: 6px 6px 0 0;
  color: #606266;
  font-size: 12px;
  cursor: pointer;
  transition: color 0.2s ease, background 0.2s ease;
}
.btab:not(.active):hover {
  background: #eef2f6;
  color: #2f4a63;
}
/* 选中块：浅冷蓝渐变，滑动靠 motion-v */
.pill {
  position: absolute;
  inset: 0;
  z-index: 0;
  border-radius: 6px 6px 0 0;
  background: linear-gradient(135deg, #e3edf5, #c9dcec);
}
.globe,
.name,
.x {
  position: relative;
  z-index: 1; /* 盖在选中块上面 */
}
.globe {
  transition: transform 0.3s ease;
}
.btab:hover .globe {
  transform: rotate(20deg) scale(1.2);
}
.name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.x {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: none;
  color: inherit;
  font-size: 12px;
  cursor: pointer;
}
.x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.25s ease, background 0.2s ease;
}
.x:hover {
  background: rgba(127, 140, 153, 0.35);
  transform: rotate(90deg);
}
.btab.active {
  color: #1c2733;
}
.bar {
  margin: 8px 0;
}
/* 刷新图标点击转一圈；新标签页图标悬停时向右上飘出一点 */
.spin {
  animation: spin 0.6s ease;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.open :deep(.n-icon) {
  transition: transform 0.25s ease;
}
.open:hover :deep(.n-icon) {
  transform: translate(2px, -2px) scale(1.15);
}
.refresh:hover :deep(.n-icon) {
  filter: drop-shadow(0 0 4px #3d6b8c);
}
/* 空状态的指南针图标缓慢上下浮动（Inspira UI 的 Float 思路） */
.empty-icon {
  display: block;
  margin: 0 auto 8px;
  color: #4f7f99;
  animation: float 3s ease-in-out infinite;
}
@keyframes float {
  0%,
  100% {
    transform: translateY(0) rotate(-6deg);
  }
  50% {
    transform: translateY(-8px) rotate(6deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .spin,
  .empty-icon {
    animation: none;
  }
  .globe,
  .x,
  .open :deep(.n-icon) {
    transition: none;
  }
  .btab:hover .globe,
  .x:hover,
  .open:hover :deep(.n-icon) {
    transform: none;
  }
}
.frames {
  flex: 1;
  min-height: 0;
  display: flex;
}
.frame {
  flex: 1;
  min-height: 0;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #fff;
}
.empty,
.tip {
  color: #909399;
  font-size: 12px;
  text-align: center;
}
.empty {
  margin-top: 48px;
  font-size: 14px;
}
.tip {
  margin-top: 6px;
}
@container (max-width: 520px) {
  .act :deep(.n-button__content) {
    display: none; /* 按钮只留图标，地址栏才有地方 */
  }
  .act :deep(.n-button__icon) {
    margin: 0;
  }
  .bar {
    flex-wrap: wrap;
  }
  .url {
    order: 3;
    flex-basis: 100%;
  }
  .btab {
    max-width: 140px;
  }
}
</style>
