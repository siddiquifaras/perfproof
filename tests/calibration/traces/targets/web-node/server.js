// Minimal Node HTTP service, the web-node trace target. Mirrors
// targets/web/app.py: /health, POST /echo, GET /cpu/<n> (sha256 chain),
// GET /db/<customer> (indexed count and sum over 20000 orders in an
// in-memory SQLite). Usage: node server.js <port>
"use strict";

const crypto = require("node:crypto");
const http = require("node:http");
const { DatabaseSync } = require("node:sqlite");

const db = new DatabaseSync(":memory:");
db.exec(
  "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer INTEGER, amount REAL)",
);
db.exec("CREATE INDEX orders_customer ON orders (customer)");
let state = 20261201;
function next() {
  state = (Math.imul(state, 1103515245) + 12345) >>> 0;
  return state;
}
const insert = db.prepare("INSERT INTO orders (customer, amount) VALUES (?, ?)");
for (let i = 0; i < 20000; i++) {
  insert.run(next() % 500, (next() % 1000000) / 10000);
}
const query = db.prepare(
  "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM orders WHERE customer = ?",
);

function send(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(text),
  });
  res.end(text);
}

const server = http.createServer((req, res) => {
  const url = req.url || "/";
  if (req.method === "GET" && url === "/health") {
    return send(res, 200, { status: "ok" });
  }
  if (req.method === "POST" && url === "/echo") {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const body = JSON.parse(Buffer.concat(chunks).toString());
      send(res, 200, { keys: Object.keys(body).length, body });
    });
    return undefined;
  }
  let m = /^\/cpu\/(\d+)$/.exec(url);
  if (req.method === "GET" && m) {
    let h = Buffer.from("seed");
    for (let i = 0; i < Number(m[1]); i++) {
      h = crypto.createHash("sha256").update(h).digest();
    }
    return send(res, 200, { digest: h.toString("hex") });
  }
  m = /^\/db\/(\d+)$/.exec(url);
  if (req.method === "GET" && m) {
    const row = query.get(Number(m[1]) % 500);
    return send(res, 200, {
      orders: row.n,
      total: Math.round(row.total * 100) / 100,
    });
  }
  return send(res, 404, { detail: "Not Found" });
});

process.on("SIGTERM", () => server.close(() => process.exit(0)));
server.listen(Number(process.argv[2]), "127.0.0.1");
