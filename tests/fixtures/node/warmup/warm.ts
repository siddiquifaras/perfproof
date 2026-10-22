import { Hono } from './src/hono'

const app = new Hono()
app.get('/id/:id', (c) => {
  const name = c.req.query('name') ?? ''
  const tags = c.req.queries('tag')?.length ?? 0
  return c.text(`${c.req.param('id')} ${name} ${tags}`, 200, {
    'x-powered-by': 'hono',
  })
})
app.post('/json', async (c) => {
  const body = await c.req.json()
  return c.json(body, 200)
})

let total = 0
async function once(): Promise<void> {
  const get = await app.request('/id/1?name=bun&tag=a&tag=b%20c&x=%E4%BD%A0')
  total += (await get.text()).length
  const post = await app.request('/json', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{"hello":"world"}',
  })
  total += (await post.text()).length
}

const mode = process.env.MODE ?? 'bench'
if (mode === 'curve') {
  const BATCH = Number(process.env.BATCH ?? 500)
  const BATCHES = Number(process.env.BATCHES ?? 60)
  const out: number[] = []
  for (let b = 0; b < BATCHES; b++) {
    const t0 = performance.now()
    for (let i = 0; i < BATCH; i++) await once()
    out.push(((performance.now() - t0) / BATCH) * 1000)
  }
  console.log(JSON.stringify(out.map((x) => Number(x.toFixed(2)))))
} else {
  const WARM = Number(process.env.WARM ?? 0)
  const MEASURE = Number(process.env.MEASURE ?? 5000)
  for (let i = 0; i < WARM; i++) await once()
  const t0 = performance.now()
  for (let i = 0; i < MEASURE; i++) await once()
  const seconds = (performance.now() - t0) / 1000 / MEASURE
  console.log(`PERFPROOF {"value": ${seconds.toFixed(9)}}`)
}
if (total < 0) console.error(total)
