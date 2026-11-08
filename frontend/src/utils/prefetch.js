// 空闲时预取还没用到的异步组件：首屏渲染完再下载，用户切到工作区 / 预览 / 统计时已在缓存里。
// import() 只下载并缓存模块，不会渲染；失败（断网等）静默忽略，真正用到时 defineAsyncComponent 会再试。
export function prefetchWhenIdle(loaders) {
  const run = () => loaders.forEach((load) => load().catch(() => {}))
  if ('requestIdleCallback' in window) requestIdleCallback(run, { timeout: 3000 })
  else setTimeout(run, 1500)
}
