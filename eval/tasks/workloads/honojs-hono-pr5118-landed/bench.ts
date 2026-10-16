// Serve 100,000 in-process requests through a Hono app whose handlers
// pass a header record to c.text and c.json, the path the change
// rewrote (iterating that record). Copied to the tree root and bundled
// with esbuild, so './src/hono' is the tree's own source. Prints a
// checksum of the bodies so both trees can be compared for equal
// output.
import { Hono } from './src/hono'

const app = new Hono()
app.get('/id/:id', (c) => {
  const name = c.req.query('name') ?? ''
  return c.text(`${c.req.param('id')} ${name}`, 200, {
    'x-powered-by': 'hono',
    'cache-control': 'no-store',
  })
})
app.post('/json', async (c) => {
  const body = await c.req.json()
  return c.json(body, 200, { 'x-powered-by': 'hono' })
})

let total = 0
for (let i = 0; i < 50000; i++) {
  const get = await app.request('/id/1?name=bun')
  total += (await get.text()).length
  const post = await app.request('/json', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{"hello":"world"}',
  })
  total += (await post.text()).length
}
console.log(total)
