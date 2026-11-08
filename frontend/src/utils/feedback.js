// 全局提示和确认框：Naive UI 的 message / dialog 需要 provider 包裹才能用，
// 这里用 createDiscreteApi 脱离组件树，让组件里可以直接 message.success(...) / await confirm(...)
import { createDiscreteApi } from 'naive-ui'

// 主色沿用原来的蓝色（Naive UI 默认是绿色）
export const themeOverrides = {
  common: {
    primaryColor: '#409EFF',
    primaryColorHover: '#79BBFF',
    primaryColorPressed: '#337ECC',
    primaryColorSuppl: '#79BBFF',
  },
}

const { message, dialog } = createDiscreteApi(['message', 'dialog'], {
  configProviderProps: { themeOverrides },
})

export { message }

// 较长的错误说明（比如 frontmatter 不合格）多停一会儿，并带关闭按钮
export const LONG = { duration: 6000, closable: true }

/** 二次确认：点确定返回 true；点取消、关闭、按 Esc、点遮罩都返回 false。 */
export function confirm({ title, content, positiveText = '确定', negativeText = '取消' }) {
  return new Promise((resolve) => {
    dialog.warning({
      title,
      content,
      positiveText,
      negativeText,
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
      onEsc: () => resolve(false),
      onMaskClick: () => resolve(false),
    })
  })
}
