<script setup>
// 页面最右侧的插件框：竖向复选框列表，每个框对应一个插件（skill）；下方两个按钮：增加、删除。
// 插件属于「当前会话」：后端把它们存在这个会话自己的目录里，切换会话就换一份列表。
//   勾选 / 取消勾选：立即保存到后端。
//   增加：选一个 .md 文件上传（任何 .md 都可以，没有别的要求），文件名就是插件名。
//   删除：删除当前「已勾选」的插件（先二次确认）。
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { addPlugin, deletePlugin, listPlugins, setPluginEnabled } from '../api'
import { checkSkillFile, checkedPlugins, pluginErrorMessage, removeByName, replaceByName } from '../utils/plugins'

// 当前会话的 id；还没有会话时是 null，插件框显示为空、按钮不可点
const props = defineProps({ sessionId: { type: String, default: null } })

const collapsed = ref(false) // 收起时只剩一条窄边，内容只是隐藏，状态不丢
const plugins = ref([])
const loading = ref(false)
const busy = ref(false) // 增加 / 删除进行中，期间按钮和复选框都不可点，免得并发操作

const checkedIds = computed(() => checkedPlugins(plugins.value))

// 会话变了就重新加载。快速连续切换时，先发出的请求可能后返回：只认「最后一次切换」对应的结果
let loadToken = 0
watch(
  () => props.sessionId,
  async (id) => {
    const token = ++loadToken
    plugins.value = []
    if (!id) return
    loading.value = true
    try {
      const list = await listPlugins(id)
      if (token === loadToken) plugins.value = list
    } catch (err) {
      if (token === loadToken) ElMessage.error(pluginErrorMessage(err))
    } finally {
      if (token === loadToken) loading.value = false
    }
  },
  { immediate: true },
)

// 点复选框：先问后端，成功后才更新界面；失败时复选框自己弹回原状（因为我们用的是服务器返回的状态）
async function toggle(plugin, enabled) {
  busy.value = true
  try {
    plugins.value = replaceByName(plugins.value, await setPluginEnabled(props.sessionId, plugin.name, enabled))
  } catch (err) {
    ElMessage.error(pluginErrorMessage(err))
  } finally {
    busy.value = false
  }
}

// el-upload 的 before-upload 返回 false：不走它自带的上传，由我们自己传，这样能复用检查和统一的错误提示
async function onPick(file) {
  const problem = checkSkillFile(file)
  if (problem) {
    ElMessage.warning(problem)
    return false
  }
  if (!props.sessionId) {
    ElMessage.warning('请先选择或新建一个会话')
    return false
  }
  busy.value = true
  try {
    const created = await addPlugin(props.sessionId, file)
    plugins.value = [...plugins.value, created].sort((a, b) => a.name.localeCompare(b.name))
    ElMessage.success(`已添加插件 ${created.name}`)
  } catch (err) {
    ElMessage.error(pluginErrorMessage(err))
  } finally {
    busy.value = false
  }
  return false
}

async function removeChecked() {
  const targets = plugins.value.filter((p) => p.enabled)
  if (!targets.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除已勾选的 ${targets.length} 个插件？\n${targets.map((p) => p.name).join('、')}`,
      '删除插件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 点了取消
  }
  busy.value = true
  const failed = []
  for (const plugin of targets) {
    try {
      await deletePlugin(props.sessionId, plugin.name)
      plugins.value = removeByName(plugins.value, plugin.name) // 删成功一个就从列表拿掉一个，中途失败也不会和后端对不上
    } catch (err) {
      failed.push(`${plugin.name}：${pluginErrorMessage(err)}`)
    }
  }
  busy.value = false
  if (failed.length) ElMessage.error(`有 ${failed.length} 个删除失败：${failed.join('；')}`)
  else ElMessage.success(`已删除 ${targets.length} 个插件`)
}
</script>

<template>
  <aside class="plugins" :class="{ collapsed }" aria-label="插件">
    <div class="header">
      <h3 v-show="!collapsed" class="title">插件(Skills)</h3>
      <el-button circle size="small" :title="collapsed ? '展开插件框' : '收起插件框'" @click="collapsed = !collapsed">
        {{ collapsed ? '‹' : '›' }}
      </el-button>
    </div>

    <div v-show="!collapsed" v-loading="loading" class="list">
      <el-empty v-if="!loading && !plugins.length" description="这个会话还没有插件" :image-size="60" />
      <el-checkbox
        v-for="p in plugins"
        :key="p.name"
        :model-value="p.enabled"
        :disabled="busy"
        class="item"
        @change="(value) => toggle(p, value)"
      >
        <span class="name">{{ p.name }}</span>
      </el-checkbox>
    </div>

    <div v-show="!collapsed" class="buttons">
      <el-upload
        :show-file-list="false"
        :auto-upload="true"
        :before-upload="onPick"
        :disabled="busy || !sessionId"
        :multiple="false"
        accept=".md"
        class="add"
      >
        <el-button :disabled="busy || !sessionId">增加</el-button>
      </el-upload>
      <el-button type="danger" plain :disabled="busy || !checkedIds.length" @click="removeChecked">删除</el-button>
    </div>
  </aside>
</template>

<style scoped>
.plugins {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  padding: 16px;
  border-left: 1px solid #e4e7ed;
  overflow: hidden;
  transition: width 0.2s;
}
.plugins.collapsed {
  width: 44px;
  padding: 16px 6px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.collapsed .header {
  justify-content: center;
}
.title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
/* 列表占满剩下的高度，插件多了就在列表里滚动；按钮始终固定在底部 */
.list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
/* 竖向排列：每个复选框独占一行 */
.item {
  display: flex;
  align-items: flex-start;
  height: auto;
  margin: 0 0 8px;
  white-space: normal;
}
.item :deep(.el-checkbox__label) {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.4;
}
.name {
  font-size: 14px;
  word-break: break-all;
}
.buttons {
  display: flex;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid #e4e7ed;
}
.buttons .add {
  display: inline-flex;
}
</style>
