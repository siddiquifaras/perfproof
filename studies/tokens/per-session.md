# Per-session tables

One table per session that is not a subagent transcript; subagent transcripts are in `tables.md` by agent type and in `sessions.csv` one by one.

## main/0b43a194 (lead sessions)

955 calls; input 442.73M (cache read 98.7%); output 970k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent writes: shell commands | 15.1% | 195k | 62.76M |
| user messages | 12.4% | 199k | 54.52M |
| agent writes: code and file contents (Edit, Write) | 9.1% | 97k | 41.43M |
| subagent reports | 8.5% | 169k | 36.04M |
| file reads and code search | 8.0% | 98k | 38.99M |
| harness reminders and attachments | 7.7% | 93k | 37.09M |
| system prompt and tool schemas (fixed prefix) | 6.3% | 59k | 35.95M |
| web and gh fetches | 6.0% | 66k | 30.10M |
| agent messages | 5.6% | 77k | 21.89M |
| agent writes: other tool calls | 4.4% | 57k | 16.16M |
| 10 smaller causes | 16.9% | 405k | 61.91M |

## t3-10-hand-runs-py-native-cd9978/e93ee6ec (T3.10 hand-runs)

257 calls; input 74.88M (cache read 98.7%); output 215k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 22.8% | 108k | 18.37M |
| agent writes: shell commands | 19.8% | 104k | 11.43M |
| system prompt and tool schemas (fixed prefix) | 14.4% | 55k | 14.48M |
| agent reasoning (thinking) | 7.9% | 64k | 4.16M |
| agent writes: code and file contents (Edit, Write) | 7.8% | 44k | 4.25M |
| harness reminders and attachments | 7.3% | 45k | 5.60M |
| other shell output | 6.5% | 32k | 5.19M |
| initial prompt and project instructions | 5.8% | 19k | 4.93M |
| perfproof CLI output | 3.7% | 19k | 2.95M |
| agent messages | 1.4% | 8k | 799k |
| 7 smaller causes | 2.5% | 19k | 1.73M |

## t3-10-hand-runs-netbox-e8755a/023af39a (T3.10 hand-runs)

250 calls; input 63.35M (cache read 98.7%); output 181k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent writes: shell commands | 21.2% | 91k | 10.31M |
| file reads and code search | 20.8% | 81k | 14.08M |
| system prompt and tool schemas (fixed prefix) | 16.2% | 55k | 14.00M |
| harness reminders and attachments | 10.0% | 47k | 6.48M |
| agent reasoning (thinking) | 7.1% | 52k | 2.97M |
| initial prompt and project instructions | 6.7% | 19k | 4.73M |
| agent writes: code and file contents (Edit, Write) | 6.6% | 30k | 3.20M |
| other shell output | 3.6% | 19k | 2.33M |
| perfproof CLI output | 2.6% | 11k | 1.71M |
| agent messages | 2.0% | 9k | 933k |
| 9 smaller causes | 3.3% | 25k | 1.75M |

## t3-11-pr-study-0f488d/eb9ef3ba (T3.11 PR study)

191 calls; input 46.68M (cache read 97.4%); output 155k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent writes: shell commands | 19.7% | 78k | 6.74M |
| file reads and code search | 17.7% | 77k | 8.58M |
| system prompt and tool schemas (fixed prefix) | 16.9% | 55k | 10.52M |
| other shell output | 12.6% | 52k | 6.20M |
| harness reminders and attachments | 7.5% | 36k | 3.48M |
| initial prompt and project instructions | 6.5% | 18k | 3.51M |
| agent reasoning (thinking) | 5.8% | 45k | 1.52M |
| agent writes: code and file contents (Edit, Write) | 4.5% | 19k | 1.57M |
| subagent reports | 3.0% | 18k | 1.29M |
| agent messages | 2.0% | 9k | 629k |
| 8 smaller causes | 3.8% | 22k | 1.44M |

## t1-3-decision-rule-abaccd/218043c3 (task worker sessions)

163 calls; input 46.85M (cache read 97.9%); output 186k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 22.4% | 84k | 11.75M |
| system prompt and tool schemas (fixed prefix) | 15.6% | 57k | 10.63M |
| agent writes: shell commands | 15.0% | 65k | 5.45M |
| agent writes: code and file contents (Edit, Write) | 8.1% | 25k | 3.34M |
| agent reasoning (thinking) | 7.9% | 77k | 1.05M |
| user messages | 5.6% | 40k | 2.42M |
| initial prompt and project instructions | 5.6% | 17k | 3.06M |
| agent messages | 4.2% | 21k | 1.42M |
| harness reminders and attachments | 3.5% | 18k | 1.69M |
| other shell output | 2.6% | 15k | 1.22M |
| 11 smaller causes | 9.4% | 57k | 3.86M |

## main/1851917c (lead sessions)

124 calls; input 34.35M (cache read 98.0%); output 180k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent writes: code and file contents (Edit, Write) | 17.5% | 59k | 4.78M |
| system prompt and tool schemas (fixed prefix) | 15.7% | 59k | 8.13M |
| web and gh fetches | 12.3% | 42k | 5.12M |
| agent reasoning (thinking) | 11.0% | 76k | 1.40M |
| agent writes: shell commands | 10.0% | 40k | 2.39M |
| initial prompt and project instructions | 9.0% | 28k | 3.80M |
| file reads and code search | 8.7% | 51k | 3.18M |
| harness reminders and attachments | 5.2% | 41k | 1.69M |
| user messages | 2.3% | 17k | 721k |
| agent messages | 2.2% | 9k | 483k |
| 8 smaller causes | 6.1% | 26k | 1.97M |

## t0-2-ci-56fd31/e6e541c6 (task worker sessions)

62 calls; input 7.84M (cache read 97.7%); output 58k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 27.8% | 57k | 3.78M |
| agent writes: shell commands | 15.4% | 21k | 596k |
| initial prompt and project instructions | 12.4% | 16k | 1.05M |
| agent reasoning (thinking) | 11.4% | 22k | 228k |
| agent messages | 7.2% | 12k | 244k |
| other shell output | 6.9% | 17k | 499k |
| harness reminders and attachments | 4.8% | 9k | 347k |
| file reads and code search | 3.6% | 5k | 305k |
| agent writes: code and file contents (Edit, Write) | 2.9% | 3k | 125k |
| web and gh fetches | 2.5% | 6k | 175k |
| 7 smaller causes | 5.1% | 10k | 304k |

## codex/X3-nb/ad716acd (codex exec)

27 calls; input 1.39M (cache read 94.7%); output 7k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 54.7% | 40k | 721k |
| system prompt and tool schemas (fixed prefix) | 19.3% | 11k | 269k |
| initial prompt and project instructions | 14.1% | 8k | 189k |
| git diffs and logs | 4.3% | 3k | 55k |
| agent writes: shell commands | 3.9% | 4k | 46k |
| agent reasoning (thinking) | 2.2% | 2k | 25k |
| other shell output | 1.0% | 5k | 9k |
| test, lint and type-check output | 0.4% | 756 | 3k |
| agent messages | 0.2% | 175 | 2k |

## codex/X1-f2p/b0656a72 (codex exec)

29 calls; input 1.17M (cache read 96.4%); output 9k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 28.0% | 11k | 329k |
| file reads and code search | 26.1% | 12k | 296k |
| git diffs and logs | 18.1% | 8k | 205k |
| agent writes: shell commands | 9.3% | 7k | 94k |
| initial prompt and project instructions | 7.6% | 3k | 85k |
| test, lint and type-check output | 7.2% | 3k | 81k |
| agent reasoning (thinking) | 2.2% | 2k | 22k |
| other shell output | 0.9% | 827 | 8k |
| agent messages | 0.6% | 389 | 6k |

## t3-11-pr-study-0f488d/runs-C2-pico/801de4af (T3.11 PR study)

43 calls; input 4.69M (cache read 97.2%); output 67k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent writes: shell commands | 28.4% | 34k | 715k |
| agent reasoning (thinking) | 24.8% | 29k | 757k |
| file reads and code search | 19.1% | 35k | 1.26M |
| system prompt and tool schemas (fixed prefix) | 9.9% | 23k | 1.03M |
| agent writes: code and file contents (Edit, Write) | 4.9% | 5k | 163k |
| other shell output | 4.7% | 13k | 231k |
| initial prompt and project instructions | 3.2% | 5k | 227k |
| agent messages | 1.7% | 1k | 27k |
| harness reminders and attachments | 1.7% | 4k | 89k |
| git diffs and logs | 1.3% | 5k | 43k |
| 2 smaller causes | 0.3% | 1k | 12k |

## t3-11-pr-study-0f488d/runs-C3-nb/1e4bdca6 (T3.11 PR study)

55 calls; input 4.26M (cache read 97.5%); output 41k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 20.7% | 29k | 1.57M |
| agent reasoning (thinking) | 17.0% | 15k | 352k |
| agent writes: shell commands | 16.4% | 14k | 380k |
| file reads and code search | 12.2% | 23k | 566k |
| agent writes: code and file contents (Edit, Write) | 10.7% | 9k | 229k |
| other shell output | 9.2% | 15k | 452k |
| initial prompt and project instructions | 4.5% | 5k | 273k |
| harness reminders and attachments | 3.3% | 6k | 136k |
| profiler output | 2.3% | 4k | 117k |
| git diffs and logs | 1.9% | 4k | 47k |
| 4 smaller causes | 1.6% | 2k | 33k |

## t3-11-pr-study-0f488d/runs-C1-f2p/1eb903fd (T3.11 PR study)

37 calls; input 2.89M (cache read 96.6%); output 53k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| agent reasoning (thinking) | 32.5% | 28k | 442k |
| agent writes: shell commands | 25.2% | 22k | 370k |
| file reads and code search | 16.9% | 30k | 641k |
| system prompt and tool schemas (fixed prefix) | 11.7% | 23k | 865k |
| other shell output | 4.8% | 7k | 206k |
| initial prompt and project instructions | 3.9% | 5k | 191k |
| harness reminders and attachments | 1.9% | 4k | 54k |
| agent writes: code and file contents (Edit, Write) | 1.8% | 2k | 10k |
| agent messages | 1.1% | 519 | 6k |
| user messages | 0.1% | 271 | 270 |
| 2 smaller causes | 0.1% | 352 | 2k |

## codex/X2-pico/1084120d (codex exec)

11 calls; input 355k (cache read 91.5%); output 4k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 36.2% | 11k | 130k |
| file reads and code search | 35.5% | 14k | 112k |
| git diffs and logs | 11.4% | 6k | 35k |
| initial prompt and project instructions | 9.8% | 3k | 31k |
| agent writes: shell commands | 4.8% | 3k | 12k |
| agent reasoning (thinking) | 1.6% | 898 | 4k |
| agent messages | 0.4% | 195 | 844 |
| other shell output | 0.2% | 186 | 518 |

## t3-11-pr-study-0f488d/runs-C4-f2p/1035262f (T3.11 PR study)

13 calls; input 674k (cache read 93.3%); output 9k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| file reads and code search | 34.9% | 25k | 188k |
| agent writes: shell commands | 20.5% | 6k | 19k |
| system prompt and tool schemas (fixed prefix) | 15.8% | 23k | 294k |
| other shell output | 9.8% | 6k | 58k |
| initial prompt and project instructions | 8.4% | 5k | 62k |
| agent reasoning (thinking) | 5.2% | 1k | 3k |
| agent messages | 3.9% | 183 | 523 |
| harness reminders and attachments | 1.5% | 966 | 5k |

## main/914436a8 (lead sessions)

1 calls; input 36k (cache read 0.0%); output 1k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 61.0% | 24k | 0 |
| initial prompt and project instructions | 30.0% | 12k | 0 |
| agent reasoning (thinking) | 7.6% | 0 | 0 |
| agent messages | 1.4% | 0 | 0 |

## main/f3595707 (lead sessions)

3 calls; input 129k (cache read 84.7%); output 2k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| initial prompt and project instructions | 56.0% | 15k | 30k |
| system prompt and tool schemas (fixed prefix) | 27.4% | 28k | 79k |
| agent reasoning (thinking) | 8.1% | 225 | 19 |
| agent messages | 5.1% | 218 | 34 |
| agent writes: shell commands | 2.4% | 196 | 197 |
| harness reminders and attachments | 0.5% | 135 | 67 |
| file reads and code search | 0.4% | 115 | 116 |
| user messages | 0.2% | 47 | 0 |

## t3-11-pr-study-0f488d/frames2py-base/779eba19 (T3.11 PR study)

1 calls; input 26k (cache read 0.0%); output 753.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 67.6% | 19k | 0 |
| initial prompt and project instructions | 25.6% | 7k | 0 |
| agent messages | 3.5% | 0 | 0 |
| agent reasoning (thinking) | 3.3% | 0 | 0 |

## codex/perfproof/9e04a1fd (codex exec)

2 calls; input 32k (cache read 69.3%); output 2k.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 65.2% | 11k | 22k |
| initial prompt and project instructions | 28.1% | 5k | 290 |
| agent reasoning (thinking) | 2.6% | 160 | 0 |
| agent messages | 2.1% | 22 | 0 |
| agent writes: shell commands | 1.0% | 162 | 0 |
| file reads and code search | 1.0% | 320 | 0 |

## main/507824b6 (lead sessions)

1 calls; input 36k (cache read 73.9%); output 453.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| initial prompt and project instructions | 80.3% | 12k | 2k |
| system prompt and tool schemas (fixed prefix) | 10.2% | 24k | 24k |
| agent reasoning (thinking) | 6.1% | 0 | 0 |
| agent messages | 3.4% | 0 | 0 |

## t3-11-pr-study-0f488d/frames2py-base/7c69f80f (T3.11 PR study)

2 calls; input 56k (cache read 89.1%); output 193.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| initial prompt and project instructions | 60.1% | 5k | 5k |
| system prompt and tool schemas (fixed prefix) | 33.0% | 23k | 45k |
| agent writes: shell commands | 3.0% | 98 | 72 |
| agent messages | 2.6% | 0 | 0 |
| harness reminders and attachments | 0.7% | 67 | 0 |
| other shell output | 0.4% | 41 | 0 |
| agent reasoning (thinking) | 0.0% | 0 | 0 |

## codex/perfproof/8c446956 (codex exec)

1 calls; input 17k (cache read 41.5%); output 5.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 72.2% | 12k | 7k |
| initial prompt and project instructions | 27.8% | 5k | 0 |
| agent messages | 0.0% | 0 | 0 |
| agent reasoning (thinking) | 0.0% | 0 | 0 |

## codex/perfproof/64c157a6 (codex exec)

1 calls; input 15k (cache read 71.6%); output 5.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| system prompt and tool schemas (fixed prefix) | 70.5% | 11k | 11k |
| initial prompt and project instructions | 29.5% | 5k | 178 |
| agent messages | 0.0% | 0 | 0 |
| agent reasoning (thinking) | 0.0% | 0 | 0 |

## t3-11-pr-study-0f488d/frames2py-base/7a7c5f8a (T3.11 PR study)

1 calls; input 2k (cache read 22.8%); output 48.

| cause | share of cost | ingested | re-read from cache |
|---|---:|---:|---:|
| initial prompt and project instructions | 65.1% | 1k | 0 |
| system prompt and tool schemas (fixed prefix) | 28.8% | 1k | 535 |
| agent reasoning (thinking) | 4.2% | 0 | 0 |
| agent messages | 1.9% | 0 | 0 |
