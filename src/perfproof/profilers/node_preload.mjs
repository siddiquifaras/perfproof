// Preload for `python -m perfproof.profilers.node run`; not for
// direct use. Node loads it with `--import` (through NODE_OPTIONS, so
// it also reaches the Node processes and worker threads a launcher such
// as `tsx` or `npx` starts) and it samples the process with V8's own
// profiler, through the inspector, from before the first user module
// runs until the process exits.
//
// It records what `--cpu-prof` leaves out: for every script the
// source-map URL V8 saw, which loaders such as tsx put inline, so the
// converter can turn a position in transformed code into one in the
// TypeScript the project wrote. It also adds up each call-tree node's
// own time in microseconds from the samples' time deltas, so the file
// holds the call tree only, not one entry per sample.
//
// One file per process and thread, written when the process exits. A
// process killed by a signal writes nothing.
import fs from 'node:fs'
import inspector from 'node:inspector'
import path from 'node:path'
import { threadId } from 'node:worker_threads'

const dir = process.env.PERFPROOF_NODE_PROFILE_DIR
const interval = Number(process.env.PERFPROOF_NODE_INTERVAL_US || 1000)

if (dir) {
  const session = new inspector.Session()
  session.connect()
  const scripts = new Map()
  session.on('Debugger.scriptParsed', ({ params }) => {
    scripts.set(params.scriptId, {
      url: params.url,
      sourceMapURL: params.sourceMapURL || '',
    })
  })
  session.post('Debugger.enable')
  session.post('Profiler.enable')
  session.post('Profiler.setSamplingInterval', { interval })
  session.post('Profiler.start')

  process.on('exit', () => {
    try {
      finish()
    } catch {
      // A profiler must not change the program's exit status.
    }
  })

  function finish() {
    let profile = null
    // A session in the same thread answers synchronously, which is the
    // only way to finish inside an exit handler.
    session.post('Profiler.stop', (error, result) => {
      if (!error) profile = result.profile
    })
    if (profile === null) return
    const own = new Map()
    const { samples, timeDeltas, nodes } = profile
    for (let i = 0; i < samples.length; i++) {
      // A sample stands for the time until the next one.
      const span = i + 1 < samples.length ? timeDeltas[i + 1] : timeDeltas[i]
      own.set(samples[i], (own.get(samples[i]) || 0) + Math.max(span, 0))
    }
    const used = {}
    for (const node of nodes) {
      node.selfMicros = own.get(node.id) || 0
      const id = node.callFrame.scriptId
      if (scripts.has(id)) used[id] = scripts.get(id)
    }
    const meta = {
      pid: process.pid,
      threadId,
      argv: process.argv,
      execArgv: process.execArgv,
      node: process.version,
      intervalUs: interval,
      durationUs: profile.endTime - profile.startTime,
    }
    const name = `${process.pid}-${threadId}.nodeprof.json`
    const body =
      `{"meta":${JSON.stringify(meta)},"scripts":${JSON.stringify(used)},` +
      `"nodes":[${nodes.map((n) => JSON.stringify(n)).join(',\n')}]}\n`
    fs.writeFileSync(path.join(dir, name), body)
  }
}
