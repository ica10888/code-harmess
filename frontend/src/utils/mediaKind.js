// 按后缀判断工作区文件是图片、视频还是音频（和后端 services.MEDIA_TYPES 保持一致）
const IMAGE = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico', 'avif']
const VIDEO = ['mp4', 'm4v', 'webm', 'ogv', 'mov', 'mkv']
const AUDIO = ['mp3', 'wav', 'ogg', 'oga', 'opus', 'm4a', 'aac', 'flac']

/** 'image' | 'video' | 'audio' | null */
export function mediaKind(path) {
  const ext = path.split('.').pop().toLowerCase()
  return IMAGE.includes(ext) ? 'image' : VIDEO.includes(ext) ? 'video' : AUDIO.includes(ext) ? 'audio' : null
}
