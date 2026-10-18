## Exact usage by group

| group | sessions | calls | input | cache read share of input | output logged | output used | cost units | cost: cache read / write / uncached / output |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| task workers (general-purpose subagents) | 32 | 4641 | 1.44B | 94.8% | 345k | 5.21M | 255.71M | 53.4% / 36.4% / 0.0% / 10.2% |
| stats-auditor subagents | 24 | 1716 | 322.41M | 94.5% | 120k | 3.27M | 68.80M | 44.3% / 32.0% / 0.0% / 23.7% |
| lead sessions | 5 | 1084 | 477.28M | 98.6% | 1.15M | 1.15M | 66.14M | 71.2% / 20.1% / 0.0% / 8.7% |
| reviewer subagents | 100 | 2198 | 123.20M | 92.6% | 80k | 1.94M | 32.48M | 35.1% / 35.0% / 0.0% / 29.8% |
| T3.10 hand-runs | 2 | 507 | 138.24M | 98.7% | 396k | 396k | 19.32M | 70.6% / 19.1% / 0.0% / 10.3% |
| T3.11 PR study | 8 | 343 | 59.28M | 97.3% | 325k | 325k | 10.61M | 54.4% / 30.3% / 0.0% / 15.3% |
| task worker sessions | 2 | 225 | 54.70M | 97.9% | 244k | 244k | 8.89M | 60.3% / 26.0% / 0.0% / 13.7% |
| pooled | 173 | 10714 | 2.62B | 95.7% | 2.66M | 12.53M | 461.94M | 54.2% / 32.3% / 0.0% / 13.6% |

Output used: 7982 of 10714 calls have a partial logged output count and use a derived one; 2732 calls have a final count.

## Share of pooled cost by group

| group | share of pooled cost |
|---|---:|
| task workers (general-purpose subagents) | 55.4% |
| stats-auditor subagents | 14.9% |
| lead sessions | 14.3% |
| reviewer subagents | 7.0% |
| T3.10 hand-runs | 4.2% |
| T3.11 PR study | 2.3% |
| task worker sessions | 1.9% |

The pooled mix is mostly perfproof's own development and review, not perfproof-driven optimisation runs.

## Pooled mix beside the hand-runs

| sessions | calls | cache read | cache write | output | fixed prefix | initial prompt | file reads and search | shell commands written | thinking | perfproof CLI | test output | profiler | git |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| pooled, all Claude Code sessions | 10714 | 54.2% | 32.3% | 13.6% | 7.5% | 4.5% | 21.7% | 13.1% | 12.2% | 0.2% | 0.6% | 0.0% | 2.1% |
| T3.10 hand-runs together | 507 | 70.6% | 19.1% | 10.3% | 15.2% | 6.2% | 21.9% | 20.4% | 7.6% | 3.2% | 0.0% | 0.0% | 0.6% |
| t3-10-hand-runs-netbox-e8755a/023af39a (T3.10 hand-run) | 250 | 70.6% | 19.2% | 10.2% | 16.2% | 6.7% | 20.8% | 21.2% | 7.1% | 2.6% | 0.0% | 0.0% | 0.2% |
| t3-10-hand-runs-py-native-cd9978/e93ee6ec (T3.10 hand-run) | 257 | 70.6% | 19.1% | 10.3% | 14.4% | 5.8% | 22.8% | 19.8% | 7.9% | 3.7% | 0.0% | 0.0% | 1.0% |

## Pooled causes

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 21.7% | 7.57M | 599.85M |
| agent writes: code and file contents (Edit, Write) | 14.5% | 2.47M | 320.10M |
| agent writes: shell commands | 13.1% | 3.46M | 255.84M |
| agent reasoning (thinking) | 12.2% | 5.51M | 175.22M |
| system prompt and tool schemas (fixed prefix) | 7.5% | 2.32M | 305.02M |
| other shell output | 6.9% | 2.38M | 183.57M |
| initial prompt and project instructions | 4.5% | 1.56M | 136.22M |
| harness reminders and attachments | 4.5% | 1.26M | 128.24M |
| agent writes: subagent briefs and final reports | 2.1% | 343k | 32.24M |
| git diffs and logs | 2.1% | 1.32M | 50.68M |
| user messages | 1.9% | 337k | 62.52M |
| subagent reports | 1.9% | 372k | 54.81M |
| web and gh fetches | 1.8% | 373k | 55.69M |
| hook feedback | 1.8% | 376k | 44.87M |
| agent messages | 1.1% | 180k | 28.65M |
| agent writes: other tool calls | 0.7% | 86k | 18.84M |
| test, lint and type-check output | 0.6% | 152k | 14.22M |
| edit and write acknowledgements | 0.5% | 97k | 12.35M |
| agent writes: read, search and fetch requests | 0.4% | 80k | 9.75M |
| other tool results | 0.2% | 45k | 7.37M |
| perfproof CLI output | 0.2% | 62k | 6.19M |
| profiler output | 0.0% | 7k | 502k |

Ingested once: 30.35M tokens in total, 15.91M of them tool results, messages, reminders and prompts. Re-read from the cache: 2.50B tokens.

## Causes by group

### task workers (general-purpose subagents)

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 23.3% | 2.52M | 343.86M |
| agent writes: code and file contents (Edit, Write) | 22.7% | 2.17M | 261.02M |
| agent writes: shell commands | 9.7% | 1.11M | 103.80M |
| system prompt and tool schemas (fixed prefix) | 8.6% | 1.27M | 189.38M |
| other shell output | 8.3% | 846k | 119.48M |
| agent reasoning (thinking) | 7.8% | 1.74M | 77.33M |
| initial prompt and project instructions | 4.5% | 497k | 71.60M |
| harness reminders and attachments | 4.0% | 474k | 56.77M |
| hook feedback | 3.2% | 374k | 44.56M |
| agent writes: subagent briefs and final reports | 1.6% | 219k | 15.66M |
| git diffs and logs | 1.1% | 204k | 14.70M |
| web and gh fetches | 1.1% | 119k | 15.24M |
| subagent reports | 1.0% | 95k | 14.09M |
| test, lint and type-check output | 0.9% | 122k | 12.50M |
| edit and write acknowledgements | 0.8% | 88k | 10.49M |
| agent writes: read, search and fetch requests | 0.4% | 41k | 5.25M |
| user messages | 0.3% | 65k | 4.17M |
| agent messages | 0.2% | 25k | 2.05M |
| agent writes: other tool calls | 0.1% | 10k | 1.22M |
| other tool results | 0.1% | 15k | 1.27M |
| perfproof CLI output | 0.1% | 12k | 777k |
| profiler output | 0.0% | 3k | 385k |

### stats-auditor subagents

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent reasoning (thinking) | 32.3% | 2.11M | 69.86M |
| file reads and code search | 27.9% | 2.06M | 116.41M |
| agent writes: shell commands | 20.4% | 1.11M | 44.02M |
| other shell output | 4.9% | 470k | 18.11M |
| git diffs and logs | 4.8% | 334k | 19.75M |
| harness reminders and attachments | 2.7% | 199k | 10.60M |
| initial prompt and project instructions | 2.6% | 162k | 11.78M |
| agent writes: subagent briefs and final reports | 1.4% | 34k | 946k |
| system prompt and tool schemas (fixed prefix) | 1.2% | 94k | 6.97M |
| web and gh fetches | 1.0% | 55k | 3.68M |
| subagent reports | 0.3% | 33k | 1.08M |
| agent writes: read, search and fetch requests | 0.3% | 15k | 797k |
| test, lint and type-check output | 0.1% | 9k | 548k |
| agent messages | 0.1% | 4k | 105k |
| user messages | 0.0% | 4k | 156k |
| other tool results | 0.0% | 347 | 8k |

### lead sessions

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent writes: shell commands | 14.6% | 235k | 65.16M |
| user messages | 11.5% | 216k | 55.24M |
| agent writes: code and file contents (Edit, Write) | 9.8% | 156k | 46.20M |
| file reads and code search | 8.0% | 150k | 42.17M |
| subagent reports | 7.7% | 169k | 36.04M |
| harness reminders and attachments | 7.5% | 135k | 38.78M |
| system prompt and tool schemas (fixed prefix) | 7.2% | 195k | 44.19M |
| web and gh fetches | 6.6% | 108k | 35.22M |
| agent messages | 5.3% | 86k | 22.37M |
| agent writes: other tool calls | 4.2% | 63k | 16.62M |
| agent writes: subagent briefs and final reports | 4.0% | 65k | 14.39M |
| agent reasoning (thinking) | 4.0% | 303k | 4.00M |
| initial prompt and project instructions | 3.9% | 95k | 20.73M |
| other shell output | 3.3% | 62k | 16.95M |
| other tool results | 1.1% | 20k | 5.59M |
| agent writes: read, search and fetch requests | 0.7% | 11k | 3.33M |
| edit and write acknowledgements | 0.3% | 6k | 1.67M |
| git diffs and logs | 0.2% | 2k | 926k |
| perfproof CLI output | 0.1% | 2k | 542k |
| test, lint and type-check output | 0.1% | 2k | 524k |

### reviewer subagents

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 25.0% | 2.37M | 41.67M |
| agent reasoning (thinking) | 24.8% | 1.02M | 12.54M |
| agent writes: shell commands | 14.0% | 572k | 6.84M |
| git diffs and logs | 8.3% | 735k | 12.78M |
| initial prompt and project instructions | 7.9% | 683k | 14.07M |
| other shell output | 7.9% | 820k | 12.65M |
| agent writes: subagent briefs and final reports | 4.7% | 7k | 36k |
| harness reminders and attachments | 2.9% | 285k | 4.22M |
| system prompt and tool schemas (fixed prefix) | 2.7% | 340k | 7.25M |
| web and gh fetches | 0.9% | 84k | 1.28M |
| agent writes: read, search and fetch requests | 0.3% | 12k | 246k |
| perfproof CLI output | 0.2% | 18k | 215k |
| subagent reports | 0.2% | 23k | 125k |
| test, lint and type-check output | 0.1% | 13k | 136k |
| agent messages | 0.1% | 3k | 31k |
| user messages | 0.0% | 1k | 7k |
| other tool results | 0.0% | 202 | 564 |

### T3.10 hand-runs

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 21.9% | 189k | 32.45M |
| agent writes: shell commands | 20.4% | 195k | 21.75M |
| system prompt and tool schemas (fixed prefix) | 15.2% | 110k | 28.49M |
| harness reminders and attachments | 8.5% | 92k | 12.08M |
| agent reasoning (thinking) | 7.6% | 116k | 7.13M |
| agent writes: code and file contents (Edit, Write) | 7.2% | 74k | 7.45M |
| initial prompt and project instructions | 6.2% | 38k | 9.66M |
| other shell output | 5.2% | 51k | 7.52M |
| perfproof CLI output | 3.2% | 30k | 4.65M |
| agent messages | 1.7% | 17k | 1.73M |
| subagent reports | 1.2% | 21k | 1.39M |
| git diffs and logs | 0.6% | 7k | 874k |
| agent writes: other tool calls | 0.4% | 4k | 467k |
| user messages | 0.2% | 5k | 307k |
| other tool results | 0.2% | 4k | 207k |
| agent writes: subagent briefs and final reports | 0.1% | 2k | 64k |
| edit and write acknowledgements | 0.1% | 1k | 120k |
| hook feedback | 0.0% | 173 | 36k |
| agent writes: read, search and fetch requests | 0.0% | 163 | 7k |

### T3.11 PR study

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent writes: shell commands | 20.6% | 153k | 8.23M |
| file reads and code search | 17.6% | 190k | 11.24M |
| system prompt and tool schemas (fixed prefix) | 16.4% | 195k | 14.33M |
| other shell output | 10.8% | 94k | 7.15M |
| agent reasoning (thinking) | 10.4% | 119k | 3.08M |
| initial prompt and project instructions | 6.1% | 53k | 4.26M |
| harness reminders and attachments | 6.0% | 50k | 3.76M |
| agent writes: code and file contents (Edit, Write) | 4.7% | 35k | 1.97M |
| subagent reports | 2.2% | 19k | 1.30M |
| agent messages | 1.9% | 12k | 686k |
| git diffs and logs | 1.5% | 20k | 696k |
| agent writes: subagent briefs and final reports | 0.6% | 3k | 342k |
| agent writes: other tool calls | 0.3% | 2k | 167k |
| user messages | 0.3% | 4k | 175k |
| profiler output | 0.2% | 4k | 117k |
| web and gh fetches | 0.1% | 678 | 80k |
| other tool results | 0.1% | 965 | 48k |
| edit and write acknowledgements | 0.1% | 1k | 32k |
| agent writes: read, search and fetch requests | 0.0% | 203 | 14k |
| test, lint and type-check output | 0.0% | 189 | 1k |

### task worker sessions

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 19.4% | 89k | 12.06M |
| system prompt and tool schemas (fixed prefix) | 17.6% | 115k | 14.41M |
| agent writes: shell commands | 15.1% | 86k | 6.05M |
| agent reasoning (thinking) | 8.4% | 98k | 1.28M |
| agent writes: code and file contents (Edit, Write) | 7.3% | 29k | 3.46M |
| initial prompt and project instructions | 6.7% | 33k | 4.11M |
| user messages | 4.8% | 42k | 2.47M |
| agent messages | 4.7% | 33k | 1.67M |
| harness reminders and attachments | 3.7% | 27k | 2.03M |
| other shell output | 3.3% | 33k | 1.72M |
| agent writes: subagent briefs and final reports | 2.1% | 12k | 794k |
| git diffs and logs | 2.0% | 20k | 953k |
| subagent reports | 1.5% | 13k | 792k |
| agent writes: other tool calls | 0.9% | 6k | 366k |
| test, lint and type-check output | 0.8% | 5k | 505k |
| other tool results | 0.5% | 5k | 250k |
| web and gh fetches | 0.5% | 6k | 192k |
| hook feedback | 0.4% | 2k | 278k |
| agent writes: read, search and fetch requests | 0.2% | 745 | 109k |
| edit and write acknowledgements | 0.1% | 521 | 42k |
| perfproof CLI output | 0.0% | 69 | 2k |

## Drill-down, pooled

### file reads and code search

| source | share of the category's cost | ingested |
|---|---:|---:|
| Read | 40.2% | 3.00M |
| sh:sed | 24.7% | 1.65M |
| sh:cat | 16.4% | 1.45M |
| sh:grep | 13.3% | 1.07M |
| sh:ls | 2.5% | 178k |
| sh:wc | 1.1% | 94k |
| sh:head | 0.6% | 49k |
| sh:find | 0.4% | 12k |

### agent writes: shell commands

| source | share of the category's cost | ingested |
|---|---:|---:|
| all | 100.0% | 3.46M |

### git diffs and logs

| source | share of the category's cost | ingested |
|---|---:|---:|
| sh:git | 100.0% | 1.32M |

### test, lint and type-check output

| source | share of the category's cost | ingested |
|---|---:|---:|
| sh:uv | 97.4% | 148k |
| sh:time | 2.3% | 3k |
| sh:cargo | 0.1% | 437 |
| sh:: | 0.1% | 83 |

### Size of results

| cause | result size | results | share of the cause's ingest |
|---|---|---:|---:|
| file reads and code search | under 2k chars | 2331 | 9.8% |
| file reads and code search | 2k to 10k | 1433 | 37.4% |
| file reads and code search | 10k to 40k | 437 | 42.2% |
| file reads and code search | over 40k | 40 | 10.5% |
| git diffs and logs | under 2k chars | 334 | 8.7% |
| git diffs and logs | 2k to 10k | 224 | 35.1% |
| git diffs and logs | 10k to 40k | 103 | 56.2% |
| git diffs and logs | over 40k | 0 | 0.0% |
| test, lint and type-check output | under 2k chars | 459 | 71.9% |
| test, lint and type-check output | 2k to 10k | 29 | 28.1% |
| test, lint and type-check output | 10k to 40k | 0 | 0.0% |
| test, lint and type-check output | over 40k | 0 | 0.0% |
| web and gh fetches | under 2k chars | 323 | 19.8% |
| web and gh fetches | 2k to 10k | 122 | 52.2% |
| web and gh fetches | 10k to 40k | 16 | 20.3% |
| web and gh fetches | over 40k | 2 | 7.7% |
| subagent reports | under 2k chars | 751 | 69.5% |
| subagent reports | 2k to 10k | 57 | 26.0% |
| subagent reports | 10k to 40k | 6 | 4.5% |
| subagent reports | over 40k | 0 | 0.0% |
| harness reminders and attachments | under 2k chars | 11344 | 76.3% |
| harness reminders and attachments | 2k to 10k | 202 | 21.6% |
| harness reminders and attachments | 10k to 40k | 7 | 2.1% |
| harness reminders and attachments | over 40k | 0 | 0.0% |

File reads of a path already read in the same session: 285 reads, 691k tokens ingested.

## What the agent wrote in file and shell calls

| call kind | calls | characters | share of its family |
|---|---:|---:|---:|
| Write | 522 | 3118147 | 69.9% |
| Edit | 1269 | 1339928 | 30.1% |
| shell: heredoc or python -c script | 1955 | 3979014 | 64.7% |
| shell: short command | 6917 | 1884934 | 30.7% |
| shell: other long command (over 600 chars) | 296 | 282216 | 4.6% |

## Sensitivity to shell-command classification

| cause | first command | match anywhere | range |
|---|---:|---:|---:|
| file reads and code search | 21.7% | 24.0% | 21.7% to 24.0% |
| git diffs and logs | 2.1% | 3.4% | 2.1% to 3.4% |
| test, lint and type-check output | 0.6% | 2.7% | 0.6% to 2.7% |
| web and gh fetches | 1.8% | 1.9% | 1.8% to 1.9% |
| perfproof CLI output | 0.2% | 0.2% | 0.2% to 0.2% |
| profiler output | 0.0% | 0.2% | 0.0% to 0.2% |
| other shell output | 6.9% | 0.9% | 0.9% to 6.9% |

The first column classifies by the command that runs first in the line; the second by the first rule that matches anywhere in its text. Only shell commands differ between the two.

## Calibration and checks

Claude Code: 2388 gaps between consecutive calls. A ratio of 0 means the category never appeared in a fitted gap; its items are then sized at the framing overhead only and the per-call rescale absorbs the difference.

| category | tokens per character | characters per token |
|---|---:|---:|
| test, lint and type-check output | 0.423 | 2.37 |
| profiler output | 0.516 | 1.94 |
| perfproof CLI output | 0.387 | 2.59 |
| file reads and code search | 0.410 | 2.44 |
| git diffs and logs | 0.433 | 2.31 |
| web and gh fetches | 0.356 | 2.81 |
| subagent reports | 0.370 | 2.71 |
| other shell output | 0.432 | 2.31 |
| edit and write acknowledgements | 0.086 | 11.60 |
| other tool results | 0.343 | 2.91 |
| user messages | 0.298 | 3.36 |
| harness reminders and attachments | 0.342 | 2.92 |
| hook feedback | 0.412 | 2.43 |
| initial prompt and project instructions | 0.342 | 2.92 |
| visible reply text (output) | 0.377 | 2.65 |
| tool-call inputs (output) | 0.567 | 1.76 |

Framing overhead per appended item: 37.8 tokens. Thinking kept in context within a turn: 1.00. Error on the growth of input: 9.9% in sample, 10.6% on held-out sessions (5 folds); predicted over observed total 1.007.

Per-call rescale so estimated context matches exact input: mean 0.996, mean absolute log 0.131; 2 calls where input shrank.

Derivation check on 2388 calls with a final output count: mean absolute error 23% of true output, derived total 1.13 of true total.

Models seen, as sessions that used each model (a session can use several, so the counts overlap): claude-fable-5-1 9, claude-haiku-4-5-20251001 2, claude-opus-5-5 148, claude-sonnet-5-5 19.

## codex exec sessions (tokens, unweighted)

6 sessions, 71 calls, input 2.98M of which cached 94.3%, output 22k (reasoning 7k). Input and output are exact usage; the split below is estimated. Share is of all tokens, cached input included, because the cached-input discount is not known here.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 40.0% | 67k | 1.13M |
| system prompt and tool schemas (fixed prefix) | 25.8% | 66k | 768k |
| initial prompt and project instructions | 11.3% | 28k | 306k |
| git diffs and logs | 10.5% | 18k | 295k |
| agent writes: shell commands | 6.1% | 13k | 151k |
| test, lint and type-check output | 3.0% | 4k | 84k |
| agent reasoning (thinking) | 2.1% | 5k | 50k |
| other shell output | 0.8% | 6k | 17k |
| agent messages | 0.4% | 780 | 9k |

codex exec: 65 gaps between consecutive calls. A ratio of 0 means the category never appeared in a fitted gap; its items are then sized at the framing overhead only and the per-call rescale absorbs the difference.

| category | tokens per character | characters per token |
|---|---:|---:|
| test, lint and type-check output | 0.261 | 3.83 |
| profiler output | 0.000 | n/a |
| perfproof CLI output | 0.000 | n/a |
| file reads and code search | 0.260 | 3.85 |
| git diffs and logs | 0.275 | 3.63 |
| web and gh fetches | 0.000 | n/a |
| subagent reports | 0.000 | n/a |
| other shell output | 0.286 | 3.50 |
| edit and write acknowledgements | 0.000 | n/a |
| other tool results | 0.000 | n/a |
| user messages | 0.000 | n/a |
| harness reminders and attachments | 0.000 | n/a |
| hook feedback | 0.000 | n/a |
| initial prompt and project instructions | 0.275 | 3.63 |
| visible reply text (output) | 0.259 | 3.86 |
| tool-call inputs (output) | 0.345 | 2.90 |

Framing overhead per appended item: 0.0 tokens. Thinking kept in context within a turn: 0.84. Error on the growth of input: 11.4% in sample, 20.8% on held-out sessions (5 folds); predicted over observed total 1.046.
