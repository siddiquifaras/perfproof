# Node fixtures

Captured from real runs, untouched. Tool versions: Node v24.13.0 (nvm,
macOS arm64, Darwin 24.6.0), tsx v4.23.1, esbuild 0.28.1. Captured on 2026-10-22.

The workload is `wl.ts` (committed here, sha256
`e117abae550954a6fa1f6ac5f3ccabe4eebd225b7d4e9775fb27fd1f308ab437`;
place it at the Hono checkout's root): a Hono
app on `honojs/hono` commit `b0bf4d2bf6725845cb47b2bd968bfb26ece82c07`
(the parent of `perf(context): skip Headers creation when there are no
headers to merge`, #5122), served in process through `app.request`,
`N=1500` iterations of one GET with a query string and one POST with a
JSON body. The project root, also the working directory, was

```
/Users/faras/code/perfproof/.claude/worktrees/agent-aacddb73ec949121b/scratch/node/hono
```

| File | How it was made |
|---|---|
| `hono-tsx.nodeprof.json` | `python -m perfproof.profilers.node run --output fx/tsx.folded -- node --import tsx wl.ts`; the file the preload wrote to `fx/tsx.folded.raw/` |
| `hono-tsx.cpuprofile` | `node --import tsx --cpu-prof --cpu-prof-dir=fx/rawts wl.ts` |
| `hono-bundle.cpuprofile` | `esbuild wl.ts --bundle --platform=node --format=esm --sourcemap --outfile=dist/wl.mjs`, then `node --cpu-prof --cpu-prof-dir=fx/rawjs dist/wl.mjs` |

`hono-tsx.nodeprof.json` holds the inline source maps `tsx` gave V8
for every file it compiled. Both `.cpuprofile` files are what Node
wrote; the two `.cpuprofile` files carry no source-map information.


The project root above is lexical in the tests (the fixtures hold
absolute paths); the checkout is not committed, so the test that needs
the files on disk skips elsewhere. esbuild 0.28.1 and tsx 4.23.1 were installed by
`npm install --prefix .eval-tools --before 2026-07-24 esbuild vitest tsx`
(the versions are read from the installed binaries after the fact).

## warmup/

The raw output behind `docs/method.md` "Warm-up (Node)", same host,
Node and Hono commit as above. `warm.ts` is the driver: put it at the
root of the Hono checkout, then (esbuild-bundled with
`esbuild warm.ts --bundle --platform=node --format=esm --outfile=dist/warm.mjs`):

| File | Command (repeated) |
|---|---|
| `curve.jsonl` | 12 times: `MODE=curve BATCHES=60 BATCH=500 node dist/warm.mjs`, one line each (microseconds per iteration of two requests, per batch of 500) |
| `bench-warm-0.txt` | 15 times: `MODE=bench WARM=0 MEASURE=10000 node dist/warm.mjs` (the runs alternated with the next file's) |
| `bench-warm-12000.txt` | 15 times: `MODE=bench WARM=12000 MEASURE=10000 node dist/warm.mjs` |
| `event-loop-and-sync-io.js` | `node event-loop-and-sync-io.js` (prints the event-loop delay p99 and max; `node --trace-sync-io` shows the `readFileSync` calls) |


## versions/

`tiny.js` (one hot function) profiled with
`python -m perfproof.profilers.node run --output out -- <node> tiny.js`
on each Node, and the top three hotspots (`summarise_file`, self share,
function, file:line) written to `<version>.top3.txt`: Node v18.20.8,
v20.20.2 and v24.13.0 from nvm. `hot tiny.js:1` leads on each.

`warmup/event-loop-and-sync-io.out` is the output of
`node warmup/event-loop-and-sync-io.js` (Node v24.13.0, one run).
