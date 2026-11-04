<script setup>
// 工作区文件浏览：列出本会话工作区的所有文件，点一个就弹窗查看内容。路径由后端固定为本会话的工作区，这里只传相对路径。
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listWorkspaceFiles, readWorkspaceFile } from '../api'

const props = defineProps({ sessionId: { type: String, required: true } })

const open = ref(false)
const tree = ref([]) // el-tree 的数据：目录有 children，文件没有；path 是相对工作区的路径

/** 把后端的扁平路径列表（a/b.txt）转成树；目录排在文件前面，同类按名字排序。 */
function buildTree(files) {
  const root = []
  for (const { path } of files) {
    const parts = path.split('/')
    let level = root
    parts.forEach((name, i) => {
      const isFile = i === parts.length - 1
      let node = level.find((n) => n.label === name && !!n.children === !isFile)
      if (!node) {
        node = { label: name, path: parts.slice(0, i + 1).join('/'), ...(isFile ? {} : { children: [] }) }
        level.push(node)
      }
      level = node.children
    })
  }
  const sort = (nodes) => {
    nodes.sort((a, b) => !!b.children - !!a.children || a.label.localeCompare(b.label))
    nodes.forEach((n) => n.children && sort(n.children))
  }
  sort(root)
  return root
}
const viewVisible = ref(false)
const viewing = ref(null) // { path, content, truncated }

async function toggle() {
  open.value = !open.value
  if (!open.value) return
  try {
    tree.value = buildTree(await listWorkspaceFiles(props.sessionId))
  } catch {
    ElMessage.error('读取文件列表失败')
  }
}

/** 点目录由 el-tree 自己展开/折叠；只有点文件才打开内容。 */
function onNodeClick(node) {
  if (!node.children) view(node.path)
}

async function view(path) {
  try {
    viewing.value = await readWorkspaceFile(props.sessionId, path)
    viewVisible.value = true
  } catch {
    ElMessage.error('读取文件失败')
  }
}
</script>

<template>
  <div class="ws">
    <el-button size="small" link type="primary" @click="toggle">{{ open ? '收起工作区文件' : '查看工作区文件' }}</el-button>
    <div v-if="open" class="list">
      <div v-if="!tree.length" class="empty">工作区还没有文件</div>
      <el-tree
        v-else
        :data="tree"
        node-key="path"
        :expand-on-click-node="true"
        :props="{ label: 'label', children: 'children' }"
        @node-click="onNodeClick"
      >
        <template #default="{ data }">
          <span :class="data.children ? 'dir' : 'file'">{{ data.children ? '📁' : '📄' }} {{ data.label }}</span>
        </template>
      </el-tree>
    </div>

    <el-dialog v-model="viewVisible" :title="viewing?.path" width="640px" append-to-body>
      <pre class="content">{{ viewing?.content }}</pre>
      <div v-if="viewing?.truncated" class="tip">文件较大，只显示了前面一部分</div>
    </el-dialog>
  </div>
</template>

<style scoped>
.list {
  margin: 4px 0 0;
  font-weight: 400;
  font-size: 13px;
}
.list :deep(.el-tree) {
  background: transparent;
}
.file {
  color: #337ecc;
}
.empty {
  color: #909399;
}
.content {
  max-height: 60vh;
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
}
.tip {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
}
</style>
