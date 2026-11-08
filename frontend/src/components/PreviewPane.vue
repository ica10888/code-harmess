<script setup>
// 预览标签：把工作流日志里的超链接页面内嵌进来（iframe）。每个链接一个浏览器式的小标签，可切换；有刷新和新标签页打开。
import { computed, ref, watch } from 'vue'

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

const refresh = () => (reloads.value[selected.value] = (reloads.value[selected.value] ?? 0) + 1)

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
    <div v-if="!tabs.length" class="empty">工作流日志里出现超链接后，页面会显示在这里</div>
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
          <span class="name">{{ tabTitle(l) }}</span>
          <button type="button" class="x" :aria-label="`关闭 ${tabTitle(l)}`" @click.stop="close(l)">×</button>
        </div>
      </div>
      <div class="bar">
        <el-button size="small" title="刷新当前页面" @click="refresh">🔄 刷新</el-button>
        <el-input :model-value="selected" size="small" readonly class="url" />
        <el-button size="small" tag="a" :href="selected" target="_blank" rel="noopener noreferrer">新标签页打开</el-button>
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
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  max-width: 240px;
  padding: 6px 8px 6px 14px;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 6px 6px 0 0;
  background: #f5f7fa;
  color: #606266;
  font-size: 12px;
  cursor: pointer;
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
  font-size: 14px;
  line-height: 16px;
  cursor: pointer;
}
.x:hover {
  background: #dcdfe6;
  color: #303133;
}
.btab.active {
  border-color: #e4e7ed;
  background: #fff;
  color: #409eff;
  position: relative;
  top: 1px; /* 盖住下面那条分隔线，看起来和页面连在一起 */
}
.bar {
  margin: 8px 0;
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
