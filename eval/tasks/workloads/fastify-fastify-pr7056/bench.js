'use strict'
// Serve requests in process through Fastify with request logging on,
// the path fastify/fastify#7056 changed (the per-request child logger).
const { Writable } = require('node:stream')
const { performance } = require('node:perf_hooks')
const Fastify = require('./fastify')

const sink = new Writable({ write (chunk, encoding, done) { done() } })
const app = Fastify({ logger: { level: 'info', stream: sink } })
app.get('/item/:id', async (request) => ({ id: request.params.id, ok: true }))

const WARM = Number(process.env.WARM || 6000)
const MEASURE = Number(process.env.MEASURE || 15000)

async function work () {
  const response = await app.inject({ method: 'GET', url: '/item/7' })
  return response.payload.length
}

async function main () {
  await app.ready()
  let checksum = 0
  for (let i = 0; i < WARM; i++) checksum += await work()
  const start = performance.now()
  for (let i = 0; i < MEASURE; i++) checksum += await work()
  const seconds = (performance.now() - start) / 1000 / MEASURE
  console.error('checksum', checksum)
  process.stdout.write(`PERFPROOF {"value": ${seconds.toPrecision(12)}}\n`)
}

main()
