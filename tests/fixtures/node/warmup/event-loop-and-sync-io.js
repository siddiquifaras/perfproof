const fs = require('node:fs')
const { monitorEventLoopDelay, performance } = require('node:perf_hooks')
const h = monitorEventLoopDelay({ resolution: 1 })
h.enable()

function blocker(ms) {
  const end = performance.now() + ms
  while (performance.now() < end);
}

async function main() {
  // sync I/O after the first tick
  await new Promise((r) => setTimeout(r, 5))
  for (let i = 0; i < 3; i++) fs.readFileSync(__filename)
  // blocking work
  for (let i = 0; i < 5; i++) {
    blocker(40)
    await new Promise((r) => setTimeout(r, 5))
  }
  // allocation heavy
  let keep = 0
  for (let i = 0; i < 2e6; i++) {
    const o = { a: i, b: [i, i + 1], c: 'x' + i }
    keep += o.b.length + o.c.length
  }
  h.disable()
  console.log('eld p99 ms', (h.percentile(99) / 1e6).toFixed(1), 'max ms', (h.max / 1e6).toFixed(1), keep)
}
main()
