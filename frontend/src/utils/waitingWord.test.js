import test from 'node:test'
import assert from 'node:assert/strict'
import { WAITING_WORDS, pickWord, revealStep, totalSteps } from './waitingWord.js'

test('词表有 200 个左右的词，且没有重复', () => {
  assert.ok(WAITING_WORDS.length > 150)
  assert.equal(new Set(WAITING_WORDS).size, WAITING_WORDS.length)
})

test('pickWord 按随机数取词，边界也不越界', () => {
  assert.equal(pickWord(() => 0), WAITING_WORDS[0])
  assert.equal(pickWord(() => 0.999999), WAITING_WORDS[WAITING_WORDS.length - 1])
})

test('revealStep：先一个字一个字露出，再一个点一个点露出', () => {
  assert.equal(revealStep('Cook', 0), '')
  assert.equal(revealStep('Cook', 2), 'Co')
  assert.equal(revealStep('Cook', 4), 'Cook')
  assert.equal(revealStep('Cook', 5), 'Cook.')
  assert.equal(revealStep('Cook', 7), 'Cook...')
  assert.equal(revealStep('Cook', 99), 'Cook...')
})

test('totalSteps 对应完整显示', () => {
  assert.equal(revealStep('Beboppin’', totalSteps('Beboppin’')), 'Beboppin’...')
})

test('pickWord 可以避开上一个词', () => {
  assert.notEqual(pickWord(() => 0, WAITING_WORDS[0]), WAITING_WORDS[0])
  assert.notEqual(pickWord(() => 0.999999, WAITING_WORDS[WAITING_WORDS.length - 1]), WAITING_WORDS[WAITING_WORDS.length - 1])
})
