<script setup>
import { NConfigProvider } from 'naive-ui'
import { themeOverrides } from './utils/feedback'
</script>

<template>
  <!-- abstract：不多包一层 div，保持 #app 的高度布局 -->
  <n-config-provider abstract :theme-overrides="themeOverrides">
    <router-view />
  </n-config-provider>
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
  -webkit-text-size-adjust: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  color: #303133;
}

/* 手机：地址栏伸缩时用动态视口高度；输入框字号 16px 以上，iOS 聚焦时才不会自动放大 */
@media (max-width: 768px) {
  #app {
    height: 100dvh;
  }
  .n-input__input-el,
  .n-input__textarea-el {
    font-size: 16px !important;
  }
  .n-message {
    max-width: 92vw;
    min-width: 0 !important;
  }
}
</style>
