'use strict'

// Compile a small schema many times, then serialise large arrays of
// numbers, integers and strings. Prints the best of five rounds in ms.
const build = require('./index.js')

const schema = {
  type: 'object',
  properties: {
    nums: { type: 'array', items: { type: 'number' } },
    ints: { type: 'array', items: { type: 'integer' } },
    strs: { type: 'array', items: { type: 'string' } }
  }
}
const N = 20000
const doc = {
  nums: Array.from({ length: N }, (_, i) => i * 1.5),
  ints: Array.from({ length: N }, (_, i) => i),
  strs: Array.from({ length: N }, (_, i) => 'item-' + i)
}
const tiny = { type: 'object', properties: { a: { type: 'string' } } }

function best (fn, rounds) {
  let min = Infinity
  for (let r = 0; r < rounds; r++) {
    const t0 = process.hrtime.bigint()
    fn()
    const t = Number(process.hrtime.bigint() - t0) / 1e6
    if (t < min) min = t
  }
  return min
}

const stringify = build(schema)
stringify(doc)
const serialise = best(() => {
  for (let i = 0; i < 20; i++) stringify(doc)
}, 5)
const compile = best(() => {
  for (let i = 0; i < 50; i++) build(tiny)
}, 5)
console.log(`serialise ${serialise.toFixed(2)} ms, build ${compile.toFixed(2)} ms`)
