<script setup>
// 发送按钮左侧的两个选择：模型（下拉框）和推理强度（水平滑块，四档，带刻度和文字）。
// 可选值来自后端 /api/options/，这里不写死；选择记在浏览器里，下次打开还是它。
import { computed, h, onMounted, ref, watch } from 'vue'
import { NSelect, NSlider, NTooltip } from 'naive-ui'
import { message } from '../utils/feedback'
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
    message.warning('模型选项加载失败，将使用默认模型发送')
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
// n-select 要的是 { label, value } 列表；下拉项里额外显示 hint，选中后显示在框里的那一份只要名字
const modelOptions = computed(() => options.value?.models ?? [])
const renderModel = (option, selected) =>
  selected ? option.label : h('div', { class: 'model-option' }, [h('span', option.label), h('span', { class: 'hint' }, option.hint)])
const currentEffort = computed(() => efforts.value[effortSlider.value])
</script>

<template>
  <div v-if="options && choice" class="model-picker">
    <n-select
      v-model:value="model"
      :options="modelOptions"
      :render-label="renderModel"
      :disabled="disabled"
      :consistent-menu-width="false"
      class="model"
      aria-label="模型"
    />

    <div class="effort">
      <span class="effort-title">推理强度</span>
      <n-tooltip placement="top">
        <template #trigger>
          <n-slider
            v-model:value="effortSlider"
            :min="0"
            :max="Math.max(efforts.length - 1, 1)"
            :step="1"
            :marks="marks"
            :tooltip="false"
            :disabled="disabled"
            class="slider"
            aria-label="推理强度"
          />
        </template>
        {{ currentEffort?.hint }}
      </n-tooltip>
    </div>
  </div>
</template>

<!-- 下拉项是弹到 body 上的，scoped 样式管不到，所以单独放一个全局块 -->
<style>
.model-option .hint {
  margin-left: 12px;
  color: #909399;
  font-size: 12px;
}
</style>

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
.effort {
  display: flex;
  align-items: center;
  gap: 12px;
}
/* 下拉框：悬停 / 展开时边框和光晕变冷蓝；滑块：悬停时圆点放大发光，拖动时再放大一点 */
.model :deep(.n-base-selection) {
  transition: box-shadow 0.25s ease, transform 0.25s ease;
}
.model:hover :deep(.n-base-selection) {
  box-shadow: 0 0 0 1px #3d6b8c, 0 0 8px rgba(61, 107, 140, 0.45);
}
.model:active :deep(.n-base-selection) {
  transform: scale(0.97);
}
.slider :deep(.n-slider-handle) {
  transition: transform 0.2s ease, box-shadow 0.25s ease;
}
.slider:hover :deep(.n-slider-handle) {
  transform: scale(1.3);
  box-shadow: 0 0 8px rgba(61, 107, 140, 0.7);
}
.slider:active :deep(.n-slider-handle) {
  transform: scale(1.5);
}
.effort-title {
  transition: color 0.25s ease;
}
.effort:hover .effort-title {
  color: #3d6b8c;
}
@media (prefers-reduced-motion: reduce) {
  .model :deep(.n-base-selection),
  .slider :deep(.n-slider-handle) {
    transition: none;
  }
  .model:active :deep(.n-base-selection),
  .slider:hover :deep(.n-slider-handle),
  .slider:active :deep(.n-slider-handle) {
    transform: none;
  }
}
.effort-title {
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
}
/* 滑块：宽度固定；刻度文字挂在轨道下方（不占布局高度），所以轨道本身和下拉框垂直居中对齐；
   刻度要的空间由 ChatView 的 .actions 在底部留出 */
.slider {
  width: 180px;
  margin: 0 12px;
}
.slider :deep(.n-slider-mark) {
  font-size: 12px;
  white-space: nowrap;
}
/* 按输入区（ChatView 的 .composer）的宽度逐级压缩，保证「选文件 / 统计 / 模型 / 强度 / 发送」始终在一行：
   滑块变短 → 隐藏「推理强度」标题 →（SessionStats 同时把计数缩成「统计」）→ 下拉框和滑块变窄 → 再窄 → 极窄 */
@container composer (max-width: 760px) {
  .slider {
    width: 130px;
  }
}
@container composer (max-width: 680px) {
  .effort-title {
    display: none;
  }
}
@container composer (max-width: 500px) {
  .model-picker {
    gap: 8px;
  }
  .model {
    width: 120px;
  }
  .slider {
    width: 100px;
    margin: 0 10px;
  }
  .slider :deep(.n-slider-mark) {
    font-size: 10px;
  }
}
@container composer (max-width: 400px) {
  .model {
    width: 80px;
  }
  .slider {
    width: 60px;
    margin: 0 8px;
  }
}
@container composer (max-width: 330px) {
  .model {
    width: 64px;
  }
  .slider {
    width: 48px;
    margin: 0 6px;
  }
}
/* 输入区只有 300px 左右（宽度被两侧栏挤到最窄）：刻度文字只剩小圆点，不再显示，避免挤在一起 */
@container composer (max-width: 290px) {
  .slider :deep(.n-slider-marks) {
    display: none;
  }
  .model-picker {
    gap: 4px;
  }
  .model {
    width: 56px;
  }
  .slider {
    width: 48px;
    margin: 0 6px;
  }
}
/* 选好了文件时，输入区左边多了一个「✕」方块（约 44px），右边的模型 / 滑块要再让出这么多 */
@container composer (max-width: 440px) {
  .model {
    width: 72px;
  }
  .slider {
    width: 56px;
    margin: 0 6px;
  }
}
@container composer (max-width: 340px) {
  .model {
    width: 56px;
  }
  .slider {
    width: 40px;
  }
}
</style>
