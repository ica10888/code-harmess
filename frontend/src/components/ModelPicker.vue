<script setup>
// 发送按钮左侧的两个选择：模型（下拉框）和推理强度（水平滑块，四档，带刻度和文字）。
// 可选值来自后端 /api/options/，这里不写死；选择记在浏览器里，下次打开还是它。
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getOptions } from '../api'
import { effortFromIndex, effortIndex, resolveChoice } from '../utils/options'

const STORAGE_KEY = 'chat-model-choice'

// v-model：{ model, effort }；选项还没加载出来时是 null
const choice = defineModel({ type: Object, default: null })
defineProps({ disabled: { type: Boolean, default: false } })

const options = ref(null) // 后端给的 { models, efforts, default_model, default_effort }

onMounted(async () => {
  try {
    options.value = await getOptions()
  } catch {
    ElMessage.warning('模型选项加载失败，将使用默认模型发送')
    return
  }
  let saved = null
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY))
  } catch {
    // 存的内容坏了就当没存过
  }
  choice.value = resolveChoice(options.value, saved)
})

// 用户改了就记下来；还没加载出来（choice 是 null）时不写，免得把有效的旧选择覆盖掉
watch(choice, (value) => value && localStorage.setItem(STORAGE_KEY, JSON.stringify(value)), { deep: true })

const model = computed({
  get: () => choice.value?.model,
  set: (value) => (choice.value = { ...choice.value, model: value }),
})

// 滑块的值是下标 0..3，对应从弱到强的四档；标签上显示强度的中文名
const efforts = computed(() => options.value?.efforts ?? [])
const effortSlider = computed({
  get: () => effortIndex(efforts.value, choice.value?.effort),
  set: (index) => (choice.value = { ...choice.value, effort: effortFromIndex(efforts.value, index) }),
})
const marks = computed(() => Object.fromEntries(efforts.value.map((e, i) => [i, e.label])))
const currentEffort = computed(() => efforts.value[effortSlider.value])
</script>

<template>
  <div v-if="options && choice" class="model-picker">
    <el-select v-model="model" :disabled="disabled" class="model" aria-label="模型">
      <el-option v-for="m in options.models" :key="m.value" :label="m.label" :value="m.value">
        <span>{{ m.label }}</span>
        <span class="hint">{{ m.hint }}</span>
      </el-option>
    </el-select>

    <div class="effort">
      <span class="effort-title">推理强度</span>
      <el-tooltip :content="currentEffort?.hint" placement="top">
        <el-slider
          v-model="effortSlider"
          :min="0"
          :max="Math.max(efforts.length - 1, 1)"
          :step="1"
          :marks="marks"
          :show-tooltip="false"
          :disabled="disabled"
          class="slider"
          aria-label="推理强度"
        />
      </el-tooltip>
    </div>
  </div>
</template>

<style scoped>
.model-picker {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}
.model {
  width: 160px;
}
.hint {
  margin-left: 12px;
  color: #909399;
  font-size: 12px;
}
.effort {
  display: flex;
  align-items: center;
  gap: 12px;
}
.effort-title {
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
}
/* 滑块：宽度固定；刻度文字在轨道下方，所以给底部留出位置，同时让轨道本身和下拉框垂直居中对齐 */
.slider {
  width: 180px;
  margin: 0 12px;
}
.slider :deep(.el-slider__marks-text) {
  font-size: 12px;
  white-space: nowrap;
}
/* 窄屏：压缩下拉框和滑块，隐藏「推理强度」标题，让发送这一行仍在一行内 */
@media (max-width: 768px) {
  .model-picker {
    gap: 8px;
  }
  .model {
    width: 92px;
  }
  .effort-title {
    display: none;
  }
  .slider {
    width: 84px;
    margin: 0 10px;
  }
  .slider :deep(.el-slider__marks-text) {
    font-size: 10px;
  }
}
</style>
