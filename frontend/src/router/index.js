import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(),
  // 路由懒加载：页面组件按需下载
  routes: [{ path: '/', component: () => import('../views/ChatView.vue') }],
})
