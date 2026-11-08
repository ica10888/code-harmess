---
name: frontend-refactor
description: 重构 Vue 3 前端（组件拆分、组合式函数抽取、样式整理、UI 库用法规范化）。支持 Element Plus、Arco Design Vue、Naive UI。用户要整理、拆分、简化前端代码，或在这些 UI 库之间对齐/迁移用法时使用。
---

# 前端重构（Vue 3 + 常见 UI 库）

原则：**行为不变、改动要小、清晰优先于功能**。重构不是加功能，也不是重新设计（视觉设计用 `frontend-design`）。

## 官方文档

先确认项目用的是哪个库（看 `frontend/package.json`），只按它的文档写，不混用。

| 库 | 官方文档 | 包名 |
| --- | --- | --- |
| Element Plus | <https://element-plus.org/> | `element-plus` |
| Arco Design Vue | <https://arco.design/vue/docs/start> | `@arco-design/web-vue` |
| Naive UI | <https://www.naiveui.com/en-US/components/button> | `naive-ui` |
| Vue 3 | <https://vuejs.org/> | `vue` |

组件的 props / 事件 / 插槽以文档为准，不凭记忆写；不确定就查文档（WebFetch 走代理 `http://127.0.0.1:7890`）。

## 流程

1. **读**：读完要改的文件及其调用方，写下当前行为（交互、接口调用、样式）。
2. **定范围**：一次只做一类重构（见下），不顺手夹带别的改动。
3. **小步改**：每步之后确认能构建：`cd frontend && npm run build`。
4. **验证**：构建通过后，让用户手动 `npm run dev` 看页面；不要把 `npm run dev` 当后台任务跑。
5. **汇报**：说明改了什么、没改什么、哪些没验证。



## 各 UI 库的写法要点

### Element Plus
- 按需用组件；消息提示用 `ElMessage` / `ElMessageBox`，不自造弹层。
- 表单用 `el-form` + `rules` 校验；双向绑定 `v-model`。
- 主题用 CSS 变量 `--el-*` 覆盖，不改库内样式。

### Arco Design Vue
- 全局注册或按需引入后统一一种方式；消息用 `Message` / `Modal`。
- 表单用 `a-form` + `field` / `rules`；组件前缀 `a-`。
- 主题用 `--color-*` / `--primary-*` 等 CSS 变量，或官方主题配置。

### Naive UI
- 组件前缀 `n-`；消息、对话框需要 `n-message-provider` / `n-dialog-provider` 包裹后用 `useMessage()` / `useDialog()`。
- 主题用 `n-config-provider` 的 `theme-overrides`，不写全局覆盖样式。
- 表单用 `n-form` + `rules`，`path` 对应字段。

## 在库之间迁移

仅在用户明确要求时做：

1. 先列出用到的组件清单（搜索 `el-` / `a-` / `n-` 前缀），逐个对照目标库文档确认有无等价组件、props 差异。
2. 按组件逐个替换，不一次性全改；每换一类就构建一次。
3. 命名和 API 差异大的（表单校验、表格列配置、消息服务）单独处理，并在汇报里列出。
4. 迁移完成后清理旧库的依赖和全局注册（改 `package.json` 前先问用户）。

## 禁止

- 不改行为、不加新功能、不改后端接口。
- 不新增依赖（包括新 UI 库），未经询问不得 `npm install`。
- 不混用多个 UI 库的同类组件。
- 不改 `docs/*.pdf`、不提交 `.env`。
