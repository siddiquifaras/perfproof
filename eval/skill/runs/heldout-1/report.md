# Skill eval `heldout-1`

Skill `b8a6ec5ab299`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 0 of 4
- Trigger set: 0 of 0 matched
- Verdict accepted: 0 of 0; wrong KEEP: 0
- Result line correct: 4 of 4
- Hotspot in a real `perfproof profile` output and named: top15 1 of 4
- Fix passes tests, no locked file touched (prove: no code changed): 4 of 4
- Tree clean: 4 of 4
- Wording violations: direction_word 1, leak 2
- Cost $4.92, 109 agent turns, 804s of `claude` wall time (794s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heldout-dask-dask-pr11899-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | ok | 18 | $0.55 | 127s | 3.1 |
| heldout-dgraph-io-badger-pr2269-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | 1 FAIL | 45 | $3.04 | 337s | 4.0 |
| heldout-fastify-fast-json-stringify-pr900-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | 1 FAIL | 22 | $0.65 | 158s | 13.1 |
| heldout-valyala-fasthttp-pr1945-hidden-fix | FAIL | - | REJECT (performance) | ok | FAIL | ok | ok | 1 FAIL | 24 | $0.67 | 183s | 7.1 |

## Skipped (not scored)

- `heldout-apache-arrow-rs-pr11143-hidden-fix`: setup `cargo bench -p parquet --features arrow,test_common,experimental --bench arrow_r` exited 1: info: syncing channel updates for 1.99.0-aarch64-apple-darwin
error: could not create temp file /Users/ppeval/.rustup/tmp/kdfdt_mztswcz2cy_file: Operation not permitted (os error 1)
- `heldout-apache-datafusion-pr26790-hidden-fix`: setup `cargo build --profile release-nonlto -p datafusion-physical-plan --example eval_` exited 1: info: syncing channel updates for 1.99.1-aarch64-apple-darwin
error: could not create temp file /Users/ppeval/.rustup/tmp/7reykt3skjhvmg5s_file: Operation not permitted (os error 1)
- `heldout-numpy-numpy-pr30810-hidden-fix`: setup `uv pip install --exclude-newer 2026-02-11 .` exited 1:   cause: The build backend returned an error
  cause: Call to `mesonpy.build_wheel` failed (exit status: 1)

         [stdout]

         meson-python: error: Could not find the specified meson: "vendored-meson/meson/meson.py"

hint: Build failures usually indicate a problem with the package or the build environment
- `heldout-rolldown-rolldown-pr10678-hidden-fix`: setup `npx --yes pnpm@11.17.0 --filter rolldown run build-native:release` exited 1:     }
  }
}

Node.js v24.13.0
$ tsc -p ./tsconfig.check.json
[ELIFECYCLE] Command failed with exit code 1.
error TS18003: No inputs were found in config file '/Users/ppeval/eval-work/work/heldout-1/repos/heldout-rolldown-rolldown-pr10678-hidden-fix/packages/rolldown/tsconfig.check.json'. Specified 'include' paths were '["dist/**/*.d.*ts"]' and 'exclude' paths were '[]'.

## Failures

- `heldout-dask-dask-pr11899-hidden-fix`
  - hotspot: HighLevelGraph.cull is in the profile but never named as the cost
- `heldout-dgraph-io-badger-pr2269-hidden-fix`
  - hotspot: valueLog.rewrite is not in the first 15 of hotspots or frames_by_total of a saved profile: ['runtime.pthread_cond_signal', 'runtime.madvise', 'runtime.usleep', 'logFile.writeEntry', 'y.ValueStruct.Encode', 'Iterator.fill', 'skl.Arena.putKey', 'runtime.pthread_cond_wait', 'skl.node.key', 'table.Builder.append', 'y.SafeCopy', 'Txn.modify', 'logFile.zeroNextEntry', 'Txn.Get', 'main.main.func2.1', 'runtime.pthread_cond_signal', 'DB.doWrites.func1', 'DB.writeRequests', 'DB.writeToLSM', 'memTable.Put', 'runtime.madvise', 'main.main', 'DB.View', 'main.main.func2', 'skl.Skiplist.Put', 'skl.newNode', 'logFile.writeEntry', 'runtime.stealWork', 'runtime.usleep', 'y.ValueStruct.Encode', 'logFile.writeEntry', 'skl.Arena.putKey', 'y.ValueStruct.Encode', 'runtime.madvise', 'runtime.usleep', 'Txn.modify', 'table.Builder.append', 'skl.node.key', 'runtime.pthread_cond_signal', 'logFile.encodeEntry', 'table.Builder.compressData', 'table.Builder.addHelper', 'logFile.zeroNextEntry', 'WriteBatch.Set', 'logFile.encodeEntry', 'DB.doWrites.func1', 'DB.writeRequests', 'DB.writeToLSM', 'memTable.Put', 'skl.Skiplist.Put', 'skl.newNode', 'logFile.writeEntry', 'Open.func5', 'DB.flushMemtable', 'DB.handleMemTableFlush', 'buildL0Table', 'table.Builder.addInternal', 'table.Builder.Add', 'y.ValueStruct.Encode', 'skl.Arena.putKey']
  - leak: {"command": "mkdir -p .perfproof/harness/bench .perfproof/specs && cat > .perfproof/harness/bench/main.go <<'EOF'\n// perfproof workload: mode \"write\" bulk...
- `heldout-fastify-fast-json-stringify-pr900-hidden-fix`
  - direction_word: It is the per-character loop for strings shorter than 42 characters, so its cap is at most 1.27x.
- `heldout-valyala-fasthttp-pr1945-hidden-fix`
  - hotspot: delAllArgs is not in the first 15 of hotspots or frames_by_total of a saved profile: ['runtime.madvise', 'hash/fnv.(*sum64a).Write', 'validHeaderValueByte', 'headerScanner.next', 'RequestHeader.parseHeaders', 'github.com/valyala/bytebufferpool.(*ByteBuffer).Reset', 'RequestHeader.AppendBytes', 'main.main', 'readRawHeaders', 'runtime.mallocgc', 'runtime.pthread_cond_signal', 'runtime.memmove', 'appendHeaderLine', 'peekArgStr', 'internal/bytealg.IndexString', 'main.main', 'runtime.madvise', 'Request.Read', 'Request.ReadLimitBody', 'hash/fnv.(*sum64a).Write', 'RequestHeader.Read', 'RequestHeader.readLoop', 'RequestHeader.tryRead', 'RequestHeader.parse', 'RequestHeader.parseHeaders', 'Request.WriteTo', 'writeBufio', 'Request.Write', 'validHeaderValueByte', 'RequestHeader.AppendBytes', 'runtime.madvise', 'hash/fnv.(*sum64a).Write', 'runtime.memmove', 'runtime.pthread_cond_signal', 'RequestHeader.parseHeaders', 'runtime.(*mcache).refill', 'isValidMethod', 'readRawHeaders', 'runtime.memequal', 'RequestHeader.peek', 'headerScanner.next', 'cookieScanner.next', 'bytes.LastIndex', 'unescape', 'runtime.memmove', 'main.main', 'hash/fnv.(*sum64a).Write', 'runtime.madvise', 'Request.Read', 'Request.ReadLimitBody', 'RequestHeader.Read', 'RequestHeader.readLoop', 'RequestHeader.tryRead', 'RequestHeader.parse', 'Request.WriteTo', 'writeBufio', 'Request.Write', 'RequestHeader.parseHeaders', 'argsScanner.next', 'Request.PostArgs', 'runtime.madvise', 'hash/fnv.(*sum64a).Write', 'runtime.pthread_cond_signal', 'internal/bytealg.IndexByte', 'bufio.(*Reader).reset', 'RequestHeader.parseHeaders', 'cookieScanner.next', 'argsScanner.next', 'validHeaderValueByte', 'decodeArgAppend', 'normalizeHeaderKey', 'appendQuotedPath', 'runtime.memmove', 'runtime.kevent', 'cookieScanner.next', 'main.main', 'runtime.madvise', 'hash/fnv.(*sum64a).Write', 'Request.Read', 'Request.ReadLimitBody', 'RequestHeader.Read', 'RequestHeader.readLoop', 'RequestHeader.tryRead', 'RequestHeader.parse', 'RequestHeader.parseHeaders', 'argsScanner.next', 'Args.ParseBytes', 'Request.WriteTo', 'writeBufio', 'Request.Write', 'runtime.kevent', 'runtime.pthread_cond_timedwait_relative_np', 'hash/fnv.(*sum64a).Write', 'runtime.asyncPreempt', 'runtime.usleep', 'runtime.pthread_cond_wait', 'runtime.pthread_cond_signal', 'runtime.pthread_kill', 'runtime.(*gcWork).tryGetObjFast', 'runtime.madvise', 'runtime.kevent', 'hash/fnv.(*sum64a).Write', 'main.main', 'runtime.pthread_cond_timedwait_relative_np', 'runtime.asyncPreempt', 'runtime.usleep', 'runtime.pthread_cond_wait', 'runtime.pthread_cond_signal', 'runtime.pthread_kill', 'runtime.(*gcWork).tryGetObjFast', 'runtime.madvise', 'runtime.kevent', 'runtime.pthread_kill', 'runtime.pthread_cond_wait', 'runtime.asyncPreempt', 'runtime.usleep', 'hash/fnv.(*sum64a).Write', 'runtime.madvise', 'runtime.(*unwinder).valid', 'runtime.pthread_cond_timedwait_relative_np', 'runtime.pthread_cond_signal', 'runtime.kevent', 'runtime.pthread_kill', 'runtime.pthread_cond_wait', 'hash/fnv.(*sum64a).Write', 'main.main', 'runtime.asyncPreempt', 'runtime.usleep', 'runtime.madvise', 'runtime.(*unwinder).valid', 'runtime.pthread_cond_timedwait_relative_np', 'runtime.pthread_cond_signal', 'runtime.kevent', 'hash/fnv.(*sum64a).Write', 'runtime.asyncPreempt', 'runtime.pthread_kill', 'runtime.madvise', 'runtime.pthread_cond_signal', 'runtime.pthread_cond_wait', 'runtime.usleep', 'runtime.pthread_cond_timedwait_relative_np', 'runtime.(*mspan).init', 'runtime.(*unwinder).next', 'runtime.(*mcache).releaseAll', 'runtime.(*mheap).allocNeedsZero', 'runtime.chunkIdx.l1', 'Response.Reset', 'runtime.systemstack', 'runtime.kevent', 'main.main', 'hash/fnv.(*sum64a).Write', 'runtime.(*mheap).alloc.func1', 'runtime.(*mheap).allocSpan', 'runtime.asyncPreempt', 'runtime.pthread_kill', 'runtime.madvise', 'runtime.pthread_cond_signal', 'runtime.pthread_cond_wait', 'runtime.usleep', 'runtime.pthread_cond_timedwait_relative_np', 'runtime.lock2', 'runtime.mallocgc']
  - leak: {"command": "head -3 go.mod && mkdir -p .perfproof/harness/wl && cat > .perfproof/harness/wl/main.go <<'EOF'\n// Workloads driven through the public API, the...
