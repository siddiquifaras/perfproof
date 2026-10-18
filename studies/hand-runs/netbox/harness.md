# Harness, specs and by-hand tools

Exactly the files that ran, except one redaction: NetBox's upstream test password in the `nbtestconfig.py` diff is replaced by a placeholder. The harness lives on branch `perfproof/bench` of the NetBox clone, on top of `v4.7.3` (`3cd2c1d`), the way the skill would have to put it (findings F-04):

| Commit | What |
|---|---|
| `ca51bb61e` | perfproof: api-mix benchmark harness |
| `784751191` | perfproof: fixed Host header so response URLs don't carry the port |
| `afb2e6a2b` | perfproof: single-endpoint mixes for the device and cable lists |

The specs were passed to `perfproof bench define --spec`. The tools under "By-hand tools" are the steps no CLI command covers; they lived in `scratch/tools/` and are not part of perfproof. Paths under ``<worktree>/scratch`` are this machine's scratch directory. The configs read the database password and secret key from gitignored files there; the values appear nowhere in this study.

## Harness (final, v2)

### `perfproof-bench/api_mix.py`

```python
"""Drive NetBox's REST API with a fixed GET mix and report its time.

Starts gunicorn (one worker, one thread) from the tree this file sits
in, waits for /api/status/, runs the mix once as warmup, then
``passes`` times measured, at concurrency 1. Prints one line
``PERFPROOF {"value": <seconds>}``: the sum of the measured request
latencies, server startup excluded. Per-request medians go to stderr.
With ``--output``, the first measured pass's response bodies are
written as canonical JSON for output comparison.
"""

import argparse
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = HERE.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str) -> tuple[float, bytes]:
    # A fixed Host keeps the absolute URLs NetBox writes into responses
    # independent of the port this run happened to get.
    req = urllib.request.Request(url, headers={"Host": "netbox.bench"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        if r.status != 200:
            raise SystemExit(f"{url}: HTTP {r.status}")
    return time.perf_counter() - t0, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", default=str(HERE / "mix.json"))
    ap.add_argument("--output")
    ap.add_argument("--only", help="run only requests containing this")
    args = ap.parse_args()
    mix = json.loads(Path(args.mix).read_text())
    reqs = [r for r in mix["requests"] if not args.only or args.only in r]
    port = free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE)
    env["NETBOX_CONFIGURATION"] = "nbconfig"
    server = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "--workers", "1", "--threads",
         "1", "--bind", f"127.0.0.1:{port}", "--chdir",
         str(TREE / "netbox"), "netbox.wsgi"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                get(base + "/api/status/")
                break
            except OSError:
                if server.poll() is not None or time.monotonic() > deadline:
                    err = server.stderr.read().decode() if server.stderr else ""
                    raise SystemExit("server did not start\n" + err[-2000:])
                time.sleep(0.2)
        for _ in range(mix["warmup_passes"]):
            for r in reqs:
                get(base + r)
        lat: dict[str, list[float]] = {r: [] for r in reqs}
        bodies: dict[str, object] = {}
        for p in range(mix["passes"]):
            for r in reqs:
                dt, body = get(base + r)
                lat[r].append(dt)
                if p == 0:
                    bodies[r] = json.loads(body)
        total = sum(sum(v) for v in lat.values())
        for r, v in lat.items():
            print(f"{statistics.median(v) * 1e3:9.1f} ms  {r}",
                  file=sys.stderr)
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(bodies, sort_keys=True, indent=1))
        print("PERFPROOF " + json.dumps({"value": round(total, 4)}))
        return 0
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)


if __name__ == "__main__":
    sys.exit(main())
```

### `perfproof-bench/mix.json`

```json
{
  "passes": 3,
  "warmup_passes": 1,
  "requests": [
    "/api/dcim/devices/?limit=50",
    "/api/dcim/interfaces/?limit=50",
    "/api/ipam/ip-addresses/?limit=50",
    "/api/ipam/prefixes/?limit=50",
    "/api/dcim/sites/?limit=50",
    "/api/dcim/cables/?limit=50",
    "/api/ipam/vlans/?limit=50",
    "/api/dcim/devices/1/"
  ]
}
```

### `perfproof-bench/mix-devices.json`

```json
{
  "passes": 20,
  "warmup_passes": 3,
  "requests": [
    "/api/dcim/devices/?limit=50"
  ]
}
```

### `perfproof-bench/mix-cables.json`

```json
{
  "passes": 10,
  "warmup_passes": 2,
  "requests": [
    "/api/dcim/cables/?limit=50"
  ]
}
```

### `perfproof-bench/nbconfig.py`

```python
"""NetBox config for perfproof runs: local containers only."""

import os
from pathlib import Path

_SECRETS = Path(os.environ["NB_SECRETS_DIR"])

ALLOWED_HOSTS = ["*"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "netbox",
        "USER": "netbox",
        "PASSWORD": (_SECRETS / ".nbdbpw").read_text().strip(),
        "HOST": "127.0.0.1",
        "PORT": "55432",
        "CONN_MAX_AGE": 300,
    }
}
REDIS = {
    "tasks": {
        "HOST": "127.0.0.1",
        "PORT": 56379,
        "DATABASE": 0,
        "SSL": False,
    },
    "caching": {
        "HOST": "127.0.0.1",
        "PORT": 56379,
        "DATABASE": 1,
        "SSL": False,
    },
}
SECRET_KEY = (_SECRETS / ".nbsecret").read_text().strip()
EXEMPT_VIEW_PERMISSIONS = ["*"]
LOGIN_REQUIRED = False
DEBUG = False
```

### v1 to v2: the fix for the false REJECT (findings F-02)

```diff
@@ -32,8 +32,11 @@ def free_port() -> int:
 
 
 def get(url: str) -> tuple[float, bytes]:
+    # A fixed Host keeps the absolute URLs NetBox writes into responses
+    # independent of the port this run happened to get.
+    req = urllib.request.Request(url, headers={"Host": "netbox.bench"})
     t0 = time.perf_counter()
-    with urllib.request.urlopen(url, timeout=60) as r:
+    with urllib.request.urlopen(req, timeout=60) as r:
         body = r.read()
         if r.status != 200:
             raise SystemExit(f"{url}: HTTP {r.status}")
```

### `perfproof-bench/nbtestconfig.py`

NetBox's `netbox/netbox/configuration_testing.py` at `v4.7.3` with the Postgres and Redis host and port changed to the containers and the password read from the scratch file:

```diff
@@ -1,3 +1,8 @@
+import os
+from pathlib import Path
+
+_SECRETS = Path(os.environ['NB_SECRETS_DIR'])
+
 ###################################################################
 #  This file serves as a base configuration for testing purposes  #
 #  only. It is not intended for production use.                   #
@@ -9,9 +14,9 @@
     'default': {
         'NAME': 'netbox',
         'USER': 'netbox',
-        'PASSWORD': '<upstream test value, not shown>',
-        'HOST': 'localhost',
-        'PORT': '',
+        'PASSWORD': (_SECRETS / '.nbdbpw').read_text().strip(),
+        'HOST': '127.0.0.1',
+        'PORT': '55432',
         'CONN_MAX_AGE': 300,
     }
 }
@@ -27,16 +32,16 @@
 
 REDIS = {
     'tasks': {
-        'HOST': 'localhost',
-        'PORT': 6379,
+        'HOST': '127.0.0.1',
+        'PORT': 56379,
         'USERNAME': '',
         'PASSWORD': '',
         'DATABASE': 0,
         'SSL': False,
     },
     'caching': {
-        'HOST': 'localhost',
-        'PORT': 6379,
+        'HOST': '127.0.0.1',
+        'PORT': 56379,
         'USERNAME': '',
         'PASSWORD': '',
         'DATABASE': 1,
```

## Specs

`api-mix` and `api-mix-2` are the same JSON with a new name: the second was needed because `bench define` refuses the same spec with a changed harness (findings F-13).

### `specs/api-mix.json`

```json
{
 "schema_version": 2,
 "name": "api-mix",
 "command": [
  "<worktree>/scratch/netbox/.venv/bin/python",
  "perfproof-bench/api_mix.py",
  "--output",
  "perfproof-out/responses.json"
 ],
 "cwd": ".",
 "env": {
  "NB_SECRETS_DIR": "<worktree>/scratch",
  "NETBOX_CONFIGURATION": "nbtestconfig",
  "PYTHONPATH": "../perfproof-bench"
 },
 "metric": {
  "kind": "reported",
  "direction": "lower_is_better",
  "unit": "s"
 },
 "correctness": {
  "command": [
   "<worktree>/scratch/netbox/.venv/bin/python",
   "manage.py",
   "test",
   "--keepdb",
   "--noinput",
   "dcim.tests.test_api.CableTestCase",
   "dcim.tests.test_api.CableTerminationTestCase"
  ],
  "cwd": "netbox",
  "timeout_seconds": 900,
  "outputs": [
   {
    "path": "perfproof-out/responses.json",
    "compare": "text"
   }
  ]
 },
 "warmup": 1,
 "budget": {
  "max_rounds": 20,
  "max_seconds": 1800
 },
 "looks": {
  "n0": 10,
  "k": 10,
  "max_looks": 2
 },
 "min_effect": 0.03,
 "alpha": 0.05,
 "timeout_seconds": 240,
 "harness": [
  "perfproof-bench/api_mix.py"
 ],
 "oracle": [
  "netbox/dcim/tests/test_api.py",
  "netbox/utilities/testing/api.py"
 ],
 "inputs": [
  "perfproof-bench/mix.json"
 ],
 "config": [
  "perfproof-bench/nbconfig.py",
  "perfproof-bench/nbtestconfig.py"
 ],
 "guardrails": [
  {
   "metric": "peak_memory",
   "direction": "lower_is_better",
   "unit": "bytes",
   "allowance": 0.1,
   "allowance_source": "default"
  },
  {
   "metric": "p99_latency",
   "direction": "lower_is_better",
   "unit": "s",
   "allowance": 0.05,
   "allowance_source": "target_class"
  },
  {
   "metric": "peak_gpu_memory",
   "direction": "lower_is_better",
   "unit": "bytes",
   "applicable": false,
   "reason": "no GPU: NetBox is a CPU-only web service"
  }
 ],
 "objective": "request_latency_sum"
}
```

### `specs/devices-list.json`

```json
{
 "schema_version": 2,
 "name": "devices-list",
 "command": [
  "<worktree>/scratch/netbox/.venv/bin/python",
  "perfproof-bench/api_mix.py",
  "--output",
  "perfproof-out/responses.json",
  "--mix",
  "perfproof-bench/mix-devices.json"
 ],
 "cwd": ".",
 "env": {
  "NB_SECRETS_DIR": "<worktree>/scratch",
  "NETBOX_CONFIGURATION": "nbtestconfig",
  "PYTHONPATH": "../perfproof-bench"
 },
 "metric": {
  "kind": "reported",
  "direction": "lower_is_better",
  "unit": "s"
 },
 "correctness": {
  "command": [
   "<worktree>/scratch/netbox/.venv/bin/python",
   "manage.py",
   "test",
   "--keepdb",
   "--noinput",
   "dcim.tests.test_api.DeviceTestCase",
   "dcim.tests.test_filtersets.DeviceTestCase"
  ],
  "cwd": "netbox",
  "timeout_seconds": 900,
  "outputs": [
   {
    "path": "perfproof-out/responses.json",
    "compare": "text"
   }
  ]
 },
 "warmup": 1,
 "budget": {
  "max_rounds": 20,
  "max_seconds": 1800
 },
 "looks": {
  "n0": 10,
  "k": 10,
  "max_looks": 2
 },
 "min_effect": 0.03,
 "alpha": 0.05,
 "timeout_seconds": 240,
 "harness": [
  "perfproof-bench/api_mix.py"
 ],
 "oracle": [
  "netbox/dcim/tests/test_api.py",
  "netbox/dcim/tests/test_filtersets.py",
  "netbox/utilities/testing/api.py",
  "netbox/utilities/testing/filtersets.py"
 ],
 "inputs": [
  "perfproof-bench/mix-devices.json"
 ],
 "config": [
  "perfproof-bench/nbconfig.py",
  "perfproof-bench/nbtestconfig.py"
 ],
 "guardrails": [
  {
   "metric": "peak_memory",
   "direction": "lower_is_better",
   "unit": "bytes",
   "allowance": 0.1,
   "allowance_source": "default"
  },
  {
   "metric": "p99_latency",
   "direction": "lower_is_better",
   "unit": "s",
   "allowance": 0.05,
   "allowance_source": "target_class"
  },
  {
   "metric": "peak_gpu_memory",
   "direction": "lower_is_better",
   "unit": "bytes",
   "applicable": false,
   "reason": "no GPU: NetBox is a CPU-only web service"
  }
 ],
 "objective": "request_latency_sum"
}
```

### `specs/cables-list-3.json`

```json
{
 "schema_version": 2,
 "name": "cables-list",
 "command": [
  "<worktree>/scratch/netbox/.venv/bin/python",
  "perfproof-bench/api_mix.py",
  "--output",
  "perfproof-out/responses.json",
  "--mix",
  "perfproof-bench/mix-cables.json"
 ],
 "cwd": ".",
 "env": {
  "NB_SECRETS_DIR": "<worktree>/scratch",
  "NETBOX_CONFIGURATION": "nbtestconfig",
  "PYTHONPATH": "../perfproof-bench"
 },
 "metric": {
  "kind": "reported",
  "direction": "lower_is_better",
  "unit": "s"
 },
 "correctness": {
  "command": [
   "<worktree>/scratch/netbox/.venv/bin/python",
   "manage.py",
   "test",
   "--keepdb",
   "--noinput",
   "dcim.tests.test_api.CableTestCase",
   "dcim.tests.test_api.CableTerminationTestCase"
  ],
  "cwd": "netbox",
  "timeout_seconds": 900,
  "outputs": [
   {
    "path": "perfproof-out/responses.json",
    "compare": "text"
   }
  ]
 },
 "warmup": 1,
 "budget": {
  "max_rounds": 20,
  "max_seconds": 1800
 },
 "looks": {
  "n0": 10,
  "k": 10,
  "max_looks": 2
 },
 "min_effect": 0.03,
 "alpha": 0.05,
 "timeout_seconds": 240,
 "harness": [
  "perfproof-bench/api_mix.py"
 ],
 "oracle": [
  "netbox/dcim/tests/test_api.py",
  "netbox/utilities/testing/api.py"
 ],
 "inputs": [
  "perfproof-bench/mix-cables.json"
 ],
 "config": [
  "perfproof-bench/nbconfig.py",
  "perfproof-bench/nbtestconfig.py"
 ],
 "guardrails": [
  {
   "metric": "peak_memory",
   "direction": "lower_is_better",
   "unit": "bytes",
   "allowance": 0.1,
   "allowance_source": "default"
  },
  {
   "metric": "p99_latency",
   "direction": "lower_is_better",
   "unit": "s",
   "allowance": 0.05,
   "allowance_source": "target_class"
  },
  {
   "metric": "peak_gpu_memory",
   "direction": "lower_is_better",
   "unit": "bytes",
   "applicable": false,
   "reason": "no GPU: NetBox is a CPU-only web service"
  }
 ],
 "objective": "request_latency_sum"
}
```

## By-hand tools

### `tools/qcount.py`

```python
"""Count SQL queries per request in-process; show repeated SQL shapes."""
import collections, json, os, re, sys, time
tree = sys.argv[1]
sys.path.insert(0, os.path.join(tree, "netbox"))
sys.path.insert(0, os.path.join(tree, "perfproof-bench"))
os.environ.setdefault("NETBOX_CONFIGURATION", "nbconfig")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")
import django
django.setup()
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
reqs = sys.argv[2:] or json.load(open(os.path.join(tree, "perfproof-bench/mix.json")))["requests"]
c = Client(SERVER_NAME="localhost")
for r in reqs:
    c.get(r)  # warm
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter(); resp = c.get(r); dt = time.perf_counter() - t0
    shapes = collections.Counter(re.sub(r"\b\d+\b", "N", q["sql"])[:140] for q in ctx.captured_queries)
    top = shapes.most_common(1)[0]
    print(f"{resp.status_code} {len(ctx.captured_queries):4d} queries {dt*1e3:7.1f} ms  {r}")
    if top[1] > 3:
        print(f"      repeated x{top[1]}: {top[0]}")
```

### `tools/qshapes.py`

```python
"""Per-table query counts and DB time for one request, in-process."""
import collections, os, re, sys, time
tree, url = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(tree, "netbox")); sys.path.insert(0, os.path.join(tree, "perfproof-bench"))
os.environ.setdefault("NETBOX_CONFIGURATION", "nbconfig"); os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")
import django; django.setup()
from django.db import connection
from django.test import Client
c = Client(SERVER_NAME="localhost")
c.get(url)
n = collections.Counter(); t = collections.Counter()
def wrap(execute, sql, params, many, context):
    m = re.search(r'FROM "(\w+)"', sql); k = m.group(1) if m else sql[:30]
    t0 = time.perf_counter()
    try: return execute(sql, params, many, context)
    finally: n[k] += 1; t[k] += time.perf_counter() - t0
reps = 5; walls = []
for _ in range(reps):
    with connection.execute_wrapper(wrap):
        t0 = time.perf_counter(); r = c.get(url); walls.append(time.perf_counter() - t0)
walls.sort()
print(f"status {r.status_code}; per request: median {walls[reps//2]*1e3:.1f} ms wall, {sum(n.values())/reps:.0f} queries, {sum(t.values())/reps*1e3:.1f} ms in execute")
for k, v in n.most_common(8):
    print(f"  {v/reps:5.0f} x {k:32s} {t[k]/reps*1e3:7.1f} ms")
```

### `tools/qstack.py`

```python
"""Print the project-code stack of the Nth query matching a pattern."""
import os, sys, traceback
tree, url, pat, nth = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
sys.path.insert(0, os.path.join(tree, "netbox")); sys.path.insert(0, os.path.join(tree, "perfproof-bench"))
os.environ.setdefault("NETBOX_CONFIGURATION", "nbconfig"); os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")
import django; django.setup()
from django.db import connection
from django.test import Client
seen = [0]
def wrap(execute, sql, params, many, context):
    if pat in sql:
        seen[0] += 1
        if seen[0] == nth:
            print(sql[:300]); print("".join(f"  {f.filename.split(tree)[-1]}:{f.lineno} {f.name}\n" for f in traceback.extract_stack() if tree + "/netbox/" in f.filename and "qstack" not in f.filename))
    return execute(sql, params, many, context)
c = Client(SERVER_NAME="localhost")
with connection.execute_wrapper(wrap):
    c.get(url)
print("matching queries:", seen[0])
```

### `tools/inproc_profile.py`

```python
"""Profile NetBox request handling in-process (Django test client) with pyinstrument."""
import json, os, sys
tree = sys.argv[1]; reqs = sys.argv[2:]
sys.path.insert(0, os.path.join(tree, "netbox")); sys.path.insert(0, os.path.join(tree, "perfproof-bench"))
os.environ.setdefault("NETBOX_CONFIGURATION", "nbconfig"); os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")
import django; django.setup()
from django.test import Client
from pyinstrument import Profiler
reqs = reqs or json.load(open(os.path.join(tree, "perfproof-bench/mix.json")))["requests"]
c = Client(SERVER_NAME="localhost")
for r in reqs: c.get(r)
p = Profiler(interval=0.0005)
p.start()
for _ in range(3):
    for r in reqs: c.get(r)
p.stop()
s = p.last_session
root = s.root_frame()
tot = root.time
# aggregate self time by (file:function), project vs library
agg = {}
def walk(f):
    key = (f.file_path_short or "?", f.function, f.line_no)
    agg[key] = agg.get(key, 0) + f.time - sum(ch.time for ch in f.children)
    for ch in f.children: walk(ch)
walk(root)
print(f"profiled {tot:.2f}s, 3 passes x {len(reqs)} requests")
print("top self time:")
for (fp, fn, ln), t in sorted(agg.items(), key=lambda kv: -kv[1])[:12]:
    print(f"  {t/tot*100:5.1f}%  {fn}  {fp}:{ln}")
# total time in project frames by function (inclusive, outermost occurrence only)
inc = {}
def walk2(f, stack):
    key = (f.file_path_short or "?", f.function, f.line_no)
    proj = "/netbox/netbox/" in (f.file_path or "") and ".venv" not in (f.file_path or "")
    if proj and key not in stack:
        inc[key] = inc.get(key, 0) + f.time
    for ch in f.children: walk2(ch, stack | ({key} if proj else set()))
walk2(root, frozenset())
print("project functions, inclusive:")
for (fp, fn, ln), t in sorted(inc.items(), key=lambda kv: -kv[1])[:15]:
    print(f"  {t/tot*100:5.1f}%  {fn}  {fp}:{ln}")
```

### `tools/ab_urls.py`

```python
"""Interleave two URLs in-process, N rounds, random order; median ms and query counts."""
import os, random, statistics, sys, time
tree, a, b, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
sys.path.insert(0, os.path.join(tree, "netbox")); sys.path.insert(0, os.path.join(tree, "perfproof-bench"))
os.environ.setdefault("NETBOX_CONFIGURATION", "nbconfig"); os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")
import django; django.setup()
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
c = Client(SERVER_NAME="localhost")
t = {a: [], b: []}; q = {}
for u in (a, b):
    c.get(u)
    with CaptureQueriesContext(connection) as ctx: c.get(u)
    q[u] = len(ctx.captured_queries)
rng = random.Random(0)
for _ in range(n):
    for u in rng.sample([a, b], 2):
        t0 = time.perf_counter(); c.get(u); t[u].append(time.perf_counter() - t0)
for u in (a, b):
    print(f"{statistics.median(t[u])*1e3:7.1f} ms median of {n}, {q[u]:3d} queries  {u}")
print(f"ratio of medians a/b: {statistics.median(t[a])/statistics.median(t[b]):.2f}")
```

### `tools/count_calls.py`

```python
"""Count calls to BaseFilterSet.get_filters during warm requests, in-process."""
import os, sys
tree, url, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, os.path.join(tree, "netbox")); sys.path.insert(0, os.path.join(tree, "perfproof-bench"))
os.environ.setdefault("NETBOX_CONFIGURATION", "nbconfig"); os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")
import django; django.setup()
from django.test import Client
from netbox.filtersets import BaseFilterSet
calls = [0]
orig = BaseFilterSet.get_filters.__func__
def counted(cls):
    calls[0] += 1
    return orig(cls)
BaseFilterSet.get_filters = classmethod(counted)
c = Client(SERVER_NAME="localhost")
c.get(url); calls[0] = 0
for _ in range(n): c.get(url)
print(f"{calls[0]} get_filters calls over {n} warm requests  ({tree.rsplit('/',1)[-1]})")
```

### `tools/samples.py`

```python
"""Per-arm medians and ranges from a compare run's samples.npz."""
import sys
import numpy as np
z = np.load(sys.argv[1])
c = z["candidate"].astype(bool)
for name, m in (("baseline", ~c), ("candidate", c)):
    v = z["value"][m]
    print(f"{name:9s} n={v.size:2d} median {np.median(v):.3f} s  min {v.min():.3f}  max {v.max():.3f}  max_rss {np.median(z['max_rss_bytes'][m])/2**20:.0f} MiB")
```

### `tools/norm_diff.py`

```python
"""Compare two response files byte for byte, then with http://127.0.0.1:<port> replaced by http://HOST."""
import re, sys
raw = [open(p, "rb").read() for p in sys.argv[1:3]]
print("sizes:", [len(r) for r in raw], "raw identical:", raw[0] == raw[1])
norm = [re.sub(rb"http://127\.0\.0\.1:\d+", b"http://HOST", r) for r in raw]
print("normalized sizes:", [len(n) for n in norm], "normalized identical:", norm[0] == norm[1])
```

### `tools/bb_diff.sh`

```bash
#!/bin/bash
# Run the harness twice on the same tree; diff raw, then with the port normalized.
T=$1; S=$2
for i in 1 2; do NB_SECRETS_DIR=$S $S/netbox/.venv/bin/python $T/perfproof-bench/api_mix.py --output $S/bb/run$i.json 2>/dev/null | grep PERFPROOF; done
cmp -s $S/bb/run1.json $S/bb/run2.json && echo "raw: identical" || echo "raw: differ, $(diff $S/bb/run1.json $S/bb/run2.json | grep -c '^<') lines"
diff $S/bb/run1.json $S/bb/run2.json | sed -n 2,4p
python3 -c "import re,sys;a,b=[re.sub(r'http://127\.0\.0\.1:\d+','http://HOST',open(p).read()) for p in sys.argv[1:]];print('port-normalized:', 'identical' if a==b else 'differ')" $S/bb/run1.json $S/bb/run2.json
```

### `step.sh` (wraps every command in the transcripts: wall time, load average before and after)

```bash
#!/bin/bash
# usage: step.sh LOGFILE LABEL CMD...   (runs CMD, appends a record to LOGFILE)
log=$1; label=$2; shift 2
out=$(mktemp "${log}.XXXX")
{ echo "### $label"; echo "start: $(date '+%F %T')"; echo "load-before: $(uptime | sed 's/.*load averages*: //')"; printf '$'; printf ' %q' "$@"; echo; } >> "$log"
t0=$(python3 -c 'import time;print(time.time())')
"$@" > "$out" 2>&1; rc=$?
t1=$(python3 -c 'import time;print(time.time())')
{ echo '```'; cat "$out"; echo '```'; echo "exit: $rc"; echo "wall: $(python3 -c "print(round($t1-$t0,1))")s"; echo "load-after: $(uptime | sed 's/.*load averages*: //')"; echo; } >> "$log"
tail -n ${TAILN:-80} "$out"; echo "[exit $rc, wall $(python3 -c "print(round($t1-$t0,1))")s, load $(uptime | sed 's/.*load averages*: //')]"
rm -f "$out"
```
