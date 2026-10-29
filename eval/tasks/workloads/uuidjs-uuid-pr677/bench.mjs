// Stringify many 16-byte arrays to UUID strings. Prints the best of
// five rounds in ms.
import stringify from './src/stringify.js'

const arrays = Array.from({ length: 256 }, (_, i) =>
  Uint8Array.from({ length: 16 }, (_, j) => {
    const b = (i * 31 + j * 17) & 0xff
    // Version 4 and the RFC variant, so the result validates.
    if (j === 6) return (b & 0x0f) | 0x40
    if (j === 8) return (b & 0x3f) | 0x80
    return b
  })
)
const N = 400000

function round() {
  let n = 0
  for (let i = 0; i < N; i++) {
    n += stringify(arrays[i & 255]).length
  }
  return n
}

let min = Infinity
for (let r = 0; r < 5; r++) {
  const t0 = process.hrtime.bigint()
  round()
  const t = Number(process.hrtime.bigint() - t0) / 1e6
  if (t < min) min = t
}
console.log(min.toFixed(1), 'ms best of 5')
