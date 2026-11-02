// 上传文件的前端辅助：上传前的本地检查、大小显示、错误文案。纯函数，方便测试。

// 和后端 config.ini 的 [storage] max_upload_mb 保持一致；前端先拦一道，省得白传一个大文件才被拒绝。
// 真正的限制在后端，这里改了不算数。
export const MAX_UPLOAD_MB = 20

/** 选好的文件能不能传。返回 null 表示可以，否则是要提示给用户的原因。 */
export function checkUpload(file) {
  if (!file) return '请先选择一个文件'
  if (file.size === 0) return '这是个空文件，无法上传'
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `文件太大，最大 ${MAX_UPLOAD_MB} MB`
  return null
}

export function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const BY_STATUS = {
  400: '文件不符合要求',
  404: '找不到当前会话，请刷新页面',
  413: '文件太大',
}

/** axios 抛出的错误 → 给用户看的一句话。后端给了原因就用后端的。 */
export function uploadErrorMessage(err) {
  const res = err?.response
  if (!res) return '网络出错，请检查连接后重试'
  return res.data?.detail || BY_STATUS[res.status] || `上传失败（${res.status}）`
}
