// Write log lines through Nest's ConsoleLogger with timestamps on, the
// path nestjs/nest#10823 changed (formatting the timestamp, which
// built an Intl.DateTimeFormat per line). The output goes to a
// discarded stream. Fixed warm-up and window; prints mean seconds
// per line.
import 'reflect-metadata'
import { ConsoleLogger } from './packages/common/services/console-logger.service'

const WARM = Number(process.env.WARM ?? 50000)
const MEASURE = Number(process.env.MEASURE ?? 250000)
const logger = new ConsoleLogger('Bench', { timestamp: true })
const written: string[] = []
const realWrite = process.stdout.write.bind(process.stdout)
;(process.stdout as unknown as { write: (s: string) => boolean }).write = (
  s: string
) => {
  written.length = 0
  written.push(s)
  return true
}

let checksum = 0
function work(i: number): number {
  logger.log(`request ${i} handled`)
  return written[0]?.length ?? 0
}

for (let i = 0; i < WARM; i++) checksum += work(i)
const start = performance.now()
for (let i = 0; i < MEASURE; i++) checksum += work(i)
const seconds = (performance.now() - start) / 1000 / MEASURE
process.stderr.write(`checksum ${checksum > 0}\n`)
realWrite(`PERFPROOF {"value": ${seconds.toPrecision(12)}}\n`)
