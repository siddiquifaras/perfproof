// Deterministic CPU-bound target for the real noise traces: generate a
// fixed record set from an integer LCG, round-trip it through JSON,
// sort it by three keys and print a checksum of the order.
"use strict";

const N = 20000;
let state = 12345;

function next() {
  state = (Math.imul(state, 1103515245) + 12345) >>> 0;
  return state;
}

const records = [];
for (let i = 0; i < N; i++) {
  records.push({
    id: i,
    group: next() % 997,
    score: next() % 1000003,
    name: "item-" + (next() % 100000),
  });
}

const parsed = JSON.parse(JSON.stringify(records));
parsed.sort((a, b) => a.group - b.group || a.score - b.score || a.id - b.id);

let sum = 0;
for (let i = 0; i < parsed.length; i++) {
  sum = (Math.imul(sum, 31) + parsed[i].id) >>> 0;
}
process.stdout.write("checksum " + sum + "\n");
