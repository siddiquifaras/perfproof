# Skill eval `r10-node`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 1 of 3
- Trigger set: 0 of 0 matched
- Verdict accepted: 2 of 2; wrong KEEP: 0
- Result line correct: 3 of 3
- Hotspot in a real `perfproof profile` output and named: top15 0 of 1
- Fix passes tests, no locked file touched (prove: no code changed): 2 of 3
- Tree clean: 3 of 3
- Wording violations: none
- Cost $2.56, 94 agent turns, 1040s of `claude` wall time (1034s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-fastify-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | ok | 18 | $0.57 | 107s | 6.0 |
| real-fastify-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 15 | $0.32 | 429s | 7.4 |
| real-nest-nothing-to-keep | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | - | FAIL | ok | ok | 61 | $1.67 | 504s | 6.1 |

## Failures

- `real-fastify-hidden-fix`
  - hotspot: routeHandler is not in the first 15 of hotspots or frames_by_total of a saved profile: ['(garbage collector)', 'wrapSafe', '(program)', '_parse', '(anonymous)', 'work', 'Response.writeHead', 'Request', 'nextTick', 'OutgoingMessage', 'compileForInternalLoader', 'parse', 'safeWriteHead', 'RegExp: [^\\t\\x20-\\x7e\\x80-\\xff]', '(anonymous)', 'processTicksAndRejections', 'runMicrotasks', 'Reply.send', '(anonymous)', 'onSendEnd', 'doInject', '(anonymous)', 'preSerializationHook', 'preSerializationHookEnd', 'next', 'emit', '(garbage collector)', 'handleResolve', '(anonymous)', 'preHandlerCallback', 'writev', '(program)', 'writeLatin1String', 'addRequest', 'compileForInternalLoader', 'wrapSafe', 'ClientRequest', '(garbage collector)', 'runMicrotasks', 'socketOnData', 'socketOnData', '(anonymous)', 'removeSocket', 'keepSocketAlive', '_addHeaderLine', 'processTicksAndRejections', 'runMicrotasks', 'emit', '(anonymous)', 'work', 'req', 'clearBuffer', 'Writable.uncork', 'ClientRequest', 'doWrite', 'Socket._writeGeneric', 'Reply.send', 'onSendEnd', 'parserOnHeadersComplete', 'end']
- `real-nest-nothing-to-keep`
  - fix: perfproof/pipes-concat fails the tests:     at async bundleAndLoadConfigFile (file:///Users/ppeval/eval-work/work/r10-node/repos/real-nest-nothing-to-keep/.eval-tools/node_modules/vite/dist/node/chunks/node.js:36040:18)
    at async loadConfigFromFile (file:///Users/ppeval/eval-work/work/r10-node/repos/real-nest-nothing-to-keep/.eval-tools/node_modules/vite/dist/node/chunks/node.js:36001:42)
    at async resolveConfig (file:///Users/ppeval/eval-work/work/r10-node/repos/real-nest-nothing-to-keep/.eval-tools/node_modules/vite/dist/node/chunks/node.js:35617:22)
    at async _createServer (file:///Users/ppeval/eval-work/work/r10-node/repos/real-nest-nothing-to-keep/.eval-tools/node_modules/vite/dist/node/chunks/node.js:25777:65)
    at async createViteServer (file:///Users/ppeval/eval-work/work/r10-node/repos/real-nest-nothing-to-keep/.eval-tools/node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:8835:17)
    at async createVitest (file:///Users/ppeval/eval-work/work/r10-node/repos/real-nest-nothing-to-keep/.eval-tools/node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:14221:18) {
  errors: [Getter/Setter]
}
