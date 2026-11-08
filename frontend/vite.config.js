import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        // 第三方库拆出去：业务代码改动不会让它们的缓存失效
        manualChunks(id) {
          if (id.includes('prismjs') || id.includes('vue-prism-editor')) return 'editor'
        },
      },
    },
  },
  server: {
    port: 5173,
    // /api 转发给 Django；SSE 也走这里，所以浏览器看到的是同源，session cookie 自然带上
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
