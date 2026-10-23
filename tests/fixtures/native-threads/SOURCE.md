# native-threads fixture

Stacks of a small multi-threaded C program, sampled by macOS `sample`
and folded by `perfproof.profilers.native`. Every file but `SOURCE.md`
is untouched tool output or the program source.

| File | What |
|---|---|
| `threads.c` | the program: four pthreads, each calling `process_entry` (which calls `checksum`) in a loop for 6 s |
| `threads.sample.txt` | `sample`'s own report, as written |
| `threads.folded.txt` | the folded stacks `perfproof.profilers.native` wrote from it |

- Date: 2026-10-23.
- OS: macOS 15.7.7 (24G720), arm64, M4 MacBook Pro.
- `sample`: the one shipped with macOS 15.7.7; it prints no version.
- Compiler: Apple clang version 17.0.0 (clang-1700.6.3.2).
- Build: `clang -O1 -g -fno-omit-frame-pointer threads.c -o threads`.
- Command: `python -m perfproof.profilers.native record --output
  threads.folded.txt --root <dir holding threads.c> -- <dir>/threads`,
  at perfproof commit `b4c499b`. The recorder keeps `sample`'s report
  beside the output; it is the `threads.sample.txt` here.
- Project root for tests: this directory (the folded file names
  `threads.c` without a directory, so the file must exist under the
  root); the binary is `threads`.
- `sample` replaces user paths with `/Users/USER/*` in its report.
- The worker threads' stacks all start `thread_start;_pthread_start`,
  as every pthread's does. The main thread's stack starts at `dyld`
  and has no such frames, so they are not a common prefix.

SHA-256 of the files as written:

```
de4c81f696da06ec70b8124c3ab7ce6fdbc706b46c0d4e2de0b5385e9c8427cf  threads.c
fb33494d6e2aa8314a796296f5eb310ec5f71035b752c083e41bdc94e7afa264  threads.folded.txt
99e735b8c56651b86f83c8f3bd6a092f48b098ffa2f4aab66c7461d6cf411a43  threads.sample.txt
```
