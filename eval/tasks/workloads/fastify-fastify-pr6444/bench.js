'use strict'
// Serve requests in process through Fastify with a handler that
// returns a web Response whose body is a ReadableStream, the path
// fastify/fastify#6444 changed (reply.js sending a web stream).
const { performance } = require('node:perf_hooks')
const Fastify = require('./fastify')

const app = Fastify({ logger: false })
const payload = JSON.stringify({ hello: 'world' })

app.get('/', function () {
  const stream = new ReadableStream({
    start (controller) {
      controller.enqueue(payload)
      controller.close()
    }
  })
  return new Response(stream, {
    status: 200,
    headers: { 'content-type': 'application/json; charset=utf-8' }
  })
})

const WARM = Number(process.env.WARM || 3000)
const MEASURE = Number(process.env.MEASURE || 10000)

async function work () {
  const response = await app.inject({ method: 'GET', url: '/' })
  if (response.statusCode !== 200) throw new Error('status ' + response.statusCode)
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
