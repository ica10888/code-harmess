import { gzipSync } from 'node:zlib'
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建后给大于 1 kB 的 js / css / html 各写一份 .gz，交给 nginx（gzip_static on）直接发，不用每次请求现压
function gzipAssets() {
  let outDir = 'dist'
  const walk = (dir) =>
    readdirSync(dir).flatMap((name) => {
      const p = join(dir, name)
      return statSync(p).isDirectory() ? walk(p) : [p]
    })
  return {
    name: 'gzip-assets',
    apply: 'build',
    configResolved: (config) => {
      outDir = config.build.outDir
    },
    closeBundle() {
      for (const file of walk(outDir)) {
        if (!/\.(js|css|html)$/.test(file)) continue
        const buf = readFileSync(file)
        if (buf.length > 1024) writeFileSync(`${file}.gz`, gzipSync(buf, { level: 9 }))
      }
    },
  }
}

// 第三方库按「更新频率 / 用途」分片：业务代码改动不会让它们的缓存失效
function vendorChunk(id) {
  if (!id.includes('node_modules')) return
  if (/[\/]node_modules[\/](vue|@vue|vue-router)[\/]/.test(id)) return 'vue'
  if (/[\/]node_modules[\/](markdown-it|mdurl|uc\.micro|entities|linkify-it)[\/]/.test(id)) return 'markdown'
  if (/[\/]node_modules[\/](prismjs|vue-prism-editor)[\/]/.test(id)) return 'editor'
  if (/[\/]node_modules[\/]axios[\/]/.test(id)) return 'axios'
}

export default defineConfig({
  plugins: [vue(), gzipAssets()],
  build: {
    rollupOptions: { output: { manualChunks: vendorChunk } },
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
