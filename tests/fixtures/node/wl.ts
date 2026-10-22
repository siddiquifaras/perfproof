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

const N = Number(process.env.N ?? 20000)
let total = 0
const t0 = performance.now()
for (let i = 0; i < N; i++) {
  const get = await app.request('/id/1?name=bun&tag=a&tag=b%20c&x=%E4%BD%A0')
  total += (await get.text()).length
  const post = await app.request('/json', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{"hello":"world"}',
  })
  total += (await post.text()).length
}
console.error('ms', (performance.now() - t0).toFixed(0))
console.log(total)
