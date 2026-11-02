// 模型 / 推理强度选择的纯逻辑：滑块下标 ↔ 强度值、默认值兜底、发送时带的字段。方便测试，不碰 DOM。
//
// 可选值不在前端写死：由后端 /api/options/ 给（那里是写死的枚举），前端只展示和选择。

/** 强度在列表里的下标（滑块用）；找不到当 0。 */
export function effortIndex(efforts, value) {
  const i = efforts.findIndex((e) => e.value === value)
  return i < 0 ? 0 : i
}

/** 滑块下标 → 强度值；越界取两端。 */
export function effortFromIndex(efforts, index) {
  const clamped = Math.min(Math.max(index, 0), efforts.length - 1)
  return efforts[clamped].value
}

/**
 * 决定当前选哪个：之前存的选择还在枚举里就沿用，否则用后端给的默认值，默认值也不在列表里就取第一项。
 * 这样官方下线某个模型后，页面不会拿着失效的值去发请求。
 */
export function resolveChoice(server, saved) {
  const pick = (list, savedValue, defaultValue) => {
    const has = (v) => list.some((x) => x.value === v)
    if (savedValue && has(savedValue)) return savedValue
    if (has(defaultValue)) return defaultValue
    return list[0].value
  }
  return {
    model: pick(server.models, saved?.model, server.default_model),
    effort: pick(server.efforts, saved?.effort, server.default_effort),
  }
}

/** 发送消息时多带的两个字段；选项还没加载出来就一个都不带，让后端用默认值（不能发 undefined 或空串）。 */
export function buildSendOptions(choice) {
  const out = {}
  if (choice?.model) out.model = choice.model
  if (choice?.effort) out.effort = choice.effort
  return out
}
