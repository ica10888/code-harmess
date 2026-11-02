// 上传前的本地检查：node --test，不需要浏览器。
import assert from 'node:assert/strict'
import test from 'node:test'
import { MAX_UPLOAD_MB, checkUpload, formatSize, uploadErrorMessage } from './upload.js'

const file = (name, size) => ({ name, size })

test('正常文件通过检查', () => {
  assert.equal(checkUpload(file('a.txt', 1024)), null)
})

test('刚好等于上限也通过，超过一个字节就拒绝', () => {
  const limit = MAX_UPLOAD_MB * 1024 * 1024
  assert.equal(checkUpload(file('edge.bin', limit)), null)
  assert.match(checkUpload(file('big.bin', limit + 1)), new RegExp(`${MAX_UPLOAD_MB} MB`))
})

test('空文件不允许上传，并说明原因', () => {
  assert.match(checkUpload(file('empty.txt', 0)), /空/)
})

test('没有选文件就不检查，返回提示', () => {
  assert.match(checkUpload(undefined), /选择/)
})

test('文件大小用人能读懂的单位显示', () => {
  assert.equal(formatSize(0), '0 B')
  assert.equal(formatSize(512), '512 B')
  assert.equal(formatSize(2048), '2.0 KB')
  assert.equal(formatSize(5 * 1024 * 1024), '5.0 MB')
})

test('后端返回的错误原因优先显示给用户', () => {
  const err = { response: { status: 413, data: { detail: '文件太大，最大 20 MB' } } }
  assert.equal(uploadErrorMessage(err), '文件太大，最大 20 MB')
})

test('后端没给原因时，按状态码给一句能看懂的话', () => {
  assert.match(uploadErrorMessage({ response: { status: 413, data: {} } }), /太大/)
  assert.match(uploadErrorMessage({ response: { status: 404, data: {} } }), /会话/)
})

test('网络断了（没有响应）给出网络提示', () => {
  assert.match(uploadErrorMessage(new Error('Network Error')), /网络/)
})
