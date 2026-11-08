# 设计系统 - 应用管理系统

**版本**: 1.0.0
**最后更新**: 2026-03-23
**维护者**: 开发团队

---

## 设计原则

1. **专业简洁** - 避免过度装饰，专注于功能和可用性
2. **一致性优先** - 所有页面使用统一的视觉语言
3. **响应式设计** - 在所有设备上提供良好体验
4. **无障碍性** - 遵循 WCAG AA 标准

---

## 颜色系统

### 主色调

```css
/* 品牌色 */
--primary-color: #409EFF;        /* 沿用原蓝色，通过 n-config-provider 的 themeOverrides 设置（Naive UI 默认是绿色） */
--primary-light: #79BBFF;
--primary-dark: #337ECC;

/* 功能色 */
--success-color: #67C23A;        /* 成功/确认 */
--warning-color: #E6A23C;        /* 警告/管理员 */
--danger-color: #F56C6C;         /* 错误/危险操作 */
--info-color: #909399;           /* 信息/次要 */
```


## 排版系统

### 字体家族

```css
/* 主字体栈 */
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             "Helvetica Neue", Arial, "Noto Sans", sans-serif,
             "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol",
             "Noto Color Emoji";

/* 中文优化 */
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             "PingFang SC", "Microsoft YaHei", "微软雅黑", sans-serif;
```

### 字体比例（1.25 Major Third）

| 用途 | 大小 | 行高 | 字重 | 使用场景 |
|------|------|------|------|----------|
| H1 | 24px | 1.2 | 600 | 页面主标题、品牌名称 |
| H2 | 20px | 1.3 | 600 | 区块标题 |
| H3 | 18px | 1.3 | 600 | 卡片标题 |
| Body | 14px | 1.5 | 400 | 正文、表单标签 |
| Small | 12px | 1.5 | 400 | 辅助文本、说明 |
| Caption | 16px | 1.5 | 400 | 副标题、描述 |

### 使用规则

- **标题**: 使用 `font-weight: 600`（半粗体）
- **正文**: 使用 `font-weight: 400`（常规）
- **行高**: 正文 1.5，标题 1.2-1.3
- **颜色**: 主标题 `#303133`，副标题 `#909399`

---

## 间距系统

### 基础单位：8px

```css
/* 间距比例 */
--spacing-xs: 4px;    /* 0.5x */
--spacing-sm: 8px;    /* 1x */
--spacing-md: 16px;   /* 2x */
--spacing-lg: 24px;   /* 3x */
--spacing-xl: 32px;   /* 4x */
--spacing-xxl: 48px;  /* 6x */
```

### 使用规则

- **组件内边距**: 16px (md)
- **卡片内边距**: 20px
- **表单项间距**: 16px (md)
- **区块间距**: 24px (lg)
- **页面边距**: 20px

---

## 布局系统

### 响应式断点

```css
/* 移动端 */
@media (max-width: 767px) {
  /* 手机 */
}

/* 平板 */
@media (min-width: 768px) and (max-width: 1023px) {
  /* 平板 */
}

/* 桌面 */
@media (min-width: 1024px) {
  /* 桌面 */
}

/* 大屏 */
@media (min-width: 1440px) {
  /* 大屏 */
}
```

### 容器宽度

- **登录卡片**: `max-width: 400px; width: 100%`
- **内容区域**: `max-width: 1200px; width: 100%`
- **表单**: `max-width: 600px; width: 100%`

### 使用规则

- 使用 `max-width` + `width: 100%` 实现响应式
- 添加 `padding: 20px` 防止边缘溢出
- 移动端优先设计

---

## 组件规范

### 按钮

```vue
<!-- 主要操作 -->
<n-button type="primary" class="action-button">
  确认
</n-button>

<!-- 次要操作 -->
<n-button>取消</n-button>

<!-- 危险操作 -->
<n-button type="error">删除</n-button>

<!-- 管理员操作 -->
<n-button type="warning">管理员操作</n-button>
```

**规则**:
- 主要操作使用 `type="primary"`
- 危险操作使用 `type="error"`（Naive UI 叫 error，不是 danger）
- 管理员相关使用 `type="warning"`
- 全宽按钮添加 `class="action-button"` 并在 CSS 中定义 `width: 100%`

### 卡片

```vue
<n-card class="content-card" title="卡片标题">
  <!-- 内容 -->
</n-card>
```

**规则**:
- 使用 `box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08)` 阴影
- 标题使用 18px，字重 600
- 内边距 20px

### 表单

```vue
<n-form :model="form" :rules="rules" ref="formRef" label-placement="left" label-width="80">
  <n-form-item label="标签" path="field">
    <n-input v-model:value="form.field" placeholder="请输入..." />
  </n-form-item>
</n-form>
```

**规则**:
- 标签宽度 80px（桌面）
- 移动端使用 `label-placement="top"`
- 必填项使用 `required: true` 规则

### 标签

```vue
<!-- 信息标签 -->
<n-tag>标签</n-tag>

<!-- 管理员标签 -->
<n-tag type="warning">管理员</n-tag>

<!-- 状态标签 -->
<n-tag type="success">成功</n-tag>
<n-tag type="error">失败</n-tag>
```

---

## 页面模板

### 登录页面

```vue
<template>
  <div class="login-page">
    <n-card class="login-card">
      <template #header>
        <div class="card-header">
          <div class="brand-logo">📱</div>
          <h1 class="brand-title">应用管理系统</h1>
          <p class="login-subtitle">登录</p>
        </div>
      </template>
      <!-- 表单内容 -->
    </n-card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  padding: 20px;
}
.login-card {
  max-width: 400px;
  width: 100%;
}
</style>
```

### 内容页面

```vue
<template>
  <div class="page-container">
    <div class="page-header">
      <h1>页面标题</h1>
    </div>
    <div class="page-content">
      <!-- 内容 -->
    </div>
  </div>
</template>

<style scoped>
.page-container {
  padding: 20px;
  background: #f5f7fa;
  min-height: 100vh;
}
.page-header {
  margin-bottom: 24px;
}
.page-content {
  background: #fff;
  padding: 20px;
  border-radius: 4px;
}
</style>
```

---

## 图标系统

### 品牌图标

- **应用 Logo**: 📱 (U+1F4F1)
- 可以替换为自定义 SVG 图标

### Ionicons 图标

使用 `@vicons/ionicons5`，用 `n-icon` 包一层：

```vue
<script setup>
import { NIcon } from 'naive-ui'
import { Add, Close, RefreshOutline } from '@vicons/ionicons5'
</script>

<template>
  <n-icon><Add /></n-icon>
</template>
```

按钮里的图标放在 `#icon` 插槽：`<n-button><template #icon><n-icon><Add /></n-icon></template></n-button>`。

### 全局提示与确认

消息和二次确认从 `src/utils/feedback.js` 引入，不要在组件里另造弹层：

```js
import { message, confirm } from '../utils/feedback'

message.success('已保存')
if (await confirm({ title: '删除', content: '确定删除？', positiveText: '删除' })) { /* ... */ }
```

---

## 动效规范

### 过渡时间

```css
/* 快速 */
--transition-fast: 150ms;

/* 标准 */
--transition-base: 300ms;

/* 慢速 */
--transition-slow: 500ms;
```

### 缓动函数

```css
/* 进入 */
--ease-out: cubic-bezier(0.215, 0.61, 0.355, 1);

/* 退出 */
--ease-in: cubic-bezier(0.55, 0.055, 0.675, 0.19);

/* 移动 */
--ease-in-out: cubic-bezier(0.645, 0.045, 0.355, 1);
```

### 使用规则

- 仅对 `transform` 和 `opacity` 添加动画
- 避免对布局属性（width, height, top, left）添加动画
- 使用 `transition: transform 300ms ease-out, opacity 300ms ease-out`

---

## 无障碍性

### 对比度要求

- **正文文本**: 4.5:1（WCAG AA）
- **大文本（18px+）**: 3:1（WCAG AA）
- **UI 组件**: 3:1（WCAG AA）

### 触摸目标

- **最小尺寸**: 44px × 44px
- **推荐尺寸**: 48px × 48px

### 键盘导航

- 所有交互元素可通过 Tab 键访问
- 使用 `focus-visible` 显示焦点状态
- 不使用 `outline: none` 除非提供替代方案

---

## 反模式（避免使用）

### ❌ 不要使用

1. **紫色/紫罗兰渐变背景** - AI 生成痕迹
2. **所有内容居中** - 缺乏层级
3. **内联样式** - 难以维护
4. **固定宽度（无响应式）** - 移动端体验差
5. **装饰性渐变** - 过度设计
6. **图标在彩色圆圈中** - AI 模板痕迹
7. **3 列特性网格** - 通用 SaaS 模板

### ✅ 推荐使用

1. **浅灰色背景** `#F5F7FA` - 专业、干净
2. **清晰的视觉层级** - 标题、正文、辅助文本
3. **CSS 类** - 可维护、可复用
4. **响应式设计** - `max-width` + 断点
5. **微妙的阴影** - `0 2px 12px rgba(0, 0, 0, 0.08)`
6. **功能性图标** - Ionicons 图标库（`@vicons/ionicons5`）
7. **任务导向布局** - 清晰的操作流程

---

## 设计审查清单

在提交代码前，检查以下项目：

- [ ] 使用了正确的颜色系统（无紫色渐变）
- [ ] 实现了响应式设计（移动端测试）
- [ ] 添加了品牌标识（logo + 应用名称）
- [ ] 使用了 CSS 类（无内联样式）
- [ ] 遵循了排版层级（标题 24px，正文 14px）
- [ ] 按钮使用了正确的类型（primary/warning/danger）
- [ ] 触摸目标 >= 44px
- [ ] 对比度符合 WCAG AA 标准
- [ ] 所有交互元素可键盘访问

---

## 更新日志

### v1.0.0 (2026-03-23)

- 初始版本
- 定义颜色系统
- 定义排版系统
- 定义间距系统
- 定义组件规范
- 添加页面模板
- 添加设计审查清单

---

## 参考资源

- [Naive UI 文档](https://www.naiveui.com/en-US/components/button)
- [Ionicons（图标库）](https://ionic.io/ionicons)
- [WCAG 2.1 指南](https://www.w3.org/WAI/WCAG21/quickref/)
- [Material Design 色彩系统](https://material.io/design/color/)
- [设计审查报告](.gstack/design-reports/design-audit-localhost-2026-03-23.md)

---

**维护说明**: 本文档应随着设计系统的演进而更新。任何对设计系统的修改都应该在这里记录。
