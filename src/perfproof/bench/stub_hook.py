"""Capture and replay one Python function inside a benchmark process.

`perfproof.bench.stub` loads this file by path into the benchmark's own
interpreter through a generated ``sitecustomize.py``, so it imports only
the standard library and must run on any CPython 3.10 or later. It is
never imported as part of perfproof.

Two modes, chosen by the configuration file the hook is given:

``record``
    The target function is wrapped. Each call's arguments are pickled
    before the call and again after it, and its result or exception is
    pickled; the call is written to ``events-<pid>.jsonl`` with its
    pickles in ``blobs-<pid>.bin``. A call whose arguments do not
    pickle, whose result does not pickle, or that changed its own
    arguments is counted, with the reason, and is not replayable. Calls
    that open a file for writing, delete or rename a path, connect a
    socket or start a process are marked with that side effect, which a
    replay skips.
``replay``
    The wrapper looks each call up by the BLAKE2 digest of its pickled
    arguments. A recorded call returns its recorded result (unpickled
    afresh each time) without running the function. A call that was not
    recorded, or that cannot be replayed, runs the real function and
    its time is measured. The wrapper times itself: ``stats-<pid>.json``
    holds the seconds spent looking up and unpickling
    (``overhead_seconds``), the seconds spent inside real calls
    (``fallthrough_seconds``), and how long installing the hook took,
    so the caller can take them out of a measurement and be left with
    the time of everything but the function.

Pickled data is read only by the benchmark's own process in replay
mode, from files perfproof wrote in an earlier run of the same
benchmark.
"""

from __future__ import annotations

import atexit
import functools
import hashlib
import importlib.abc
import inspect
import json
import mmap
import os
import pickle
import statistics
import struct
import sys
import threading
import time
from typing import Any

_WRITE_MODES = frozenset("wax+")
_SIDE_EFFECT_EVENTS = frozenset(
    {
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.mkdir",
        "os.system",
        "os.truncate",
        "shutil.rmtree",
        "socket.connect",
        "socket.sendto",
        "subprocess.Popen",
    }
)
_CALIBRATION_BATCHES = 5
_CALIBRATION_CALLS = 1000
_SLOT = struct.Struct("<5q")
"""Per process: calls, replayed, real calls, real-call ns, hook ns."""
_HEADER = 16
"""Bytes before the slots: the next free slot, and processes lost."""
_SLOTS = 4096


def _digest(data: bytes) -> str:
    """Return the lookup key of a call's pickled arguments."""
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _clock(kind: str) -> Any:
    """Return the nanosecond clock for a metric: wall or process CPU."""
    return time.process_time_ns if kind == "cpu" else time.perf_counter_ns


def _nothing() -> None:
    """Stand-in target while the wrapper's cost is calibrated."""


def _resolve(module: Any, qualname: str) -> tuple[Any, str, str]:
    """Find the owner and kind of a function by its qualified name.

    Returns the object holding the attribute, its name there and how it
    is stored: ``plain``, ``staticmethod`` or ``classmethod``.
    """
    owner = module
    *path, name = qualname.split(".")
    for part in path:
        owner = getattr(owner, part)
    raw = (
        owner.__dict__[name]
        if hasattr(owner, "__dict__") and name in owner.__dict__
        else getattr(owner, name)
    )
    kind = "plain"
    if isinstance(raw, staticmethod):
        kind = "staticmethod"
    elif isinstance(raw, classmethod):
        kind = "classmethod"
    return owner, name, kind


def _unwrap(owner: Any, name: str, kind: str) -> Any:
    """Return the plain function stored under ``name``."""
    raw = (
        owner.__dict__[name]
        if name in getattr(owner, "__dict__", {})
        else (getattr(owner, name))
    )
    if kind in ("staticmethod", "classmethod"):
        return raw.__func__
    return raw


class _Hook:
    """State of the hook in one process."""

    def __init__(
        self, config: dict[str, Any], started_ns: int, *, probe: bool = False
    ) -> None:
        self.config = config
        self.mode: str = config["mode"]
        self.module_name: str = config["module"]
        self.qualname: str = config["qualname"]
        self.directory: str = config["dir"]
        self.stats_dir: str = config.get("stats_dir", self.directory)
        self.clock = _clock(config.get("clock", "wall"))
        self.started_ns = started_ns
        self.patch_ns = 0
        self.lock = threading.Lock()
        self.local = threading.local()
        self.status = "module never imported"
        self.root_pid = os.getpid()
        # Shared with forked children, which each own one slot, so their
        # calls and hook time are counted, not lost with the child.
        self.shared = mmap.mmap(-1, _HEADER + _SLOTS * _SLOT.size)
        struct.pack_into("<qq", self.shared, 0, 1, 0)
        self.slot: int | None = 0
        self.pending: int | None = None
        self._open()
        fixed = config.get("untimed_ns")
        self.untimed_ns = (
            0
            if probe
            else int(fixed)
            if fixed is not None
            else self._calibrate()
        )

    def _open(self) -> None:
        """Start this process's counters and files."""
        self.pid = os.getpid()
        self.calls = 0
        self.replayed = 0
        self.fallthrough = 0
        self.fallthrough_ns = 0
        self.overhead_ns = 0
        self.unpicklable_args = 0
        self.unpicklable_result = 0
        self.mutating = 0
        self.effect_calls = 0
        self.effect_names: set[str] = set()
        self.seen: set[str] = set()
        self.offset = 0
        self.index: dict[str, tuple[bool, str | None, bytes]] | None = None
        if self.mode == "record":
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            self.events = os.open(
                os.path.join(self.directory, f"events-{self.pid}.jsonl"),
                flags,
                0o600,
            )
            self.blobs = os.open(
                os.path.join(self.directory, f"blobs-{self.pid}.bin"),
                flags,
                0o600,
            )

    def before_fork(self) -> None:
        """Reserve the slot the next child will count in."""
        if self.mode != "replay":
            return
        taken: int = struct.unpack_from("<q", self.shared, 0)[0]
        if taken >= _SLOTS:
            self.pending = None
            return
        struct.pack_into("<q", self.shared, 0, taken + 1)
        self.pending = taken

    def after_fork(self) -> None:
        """Give a forked child its own counters and files."""
        if self.mode == "record":
            os.close(self.events)
            os.close(self.blobs)
        self.slot = self.pending
        self._open()

    def _bump(self, values: tuple[int, int, int, int, int]) -> None:
        """Add to this process's slot; lost if none."""
        if self.slot is None:
            lost: int = struct.unpack_from("<q", self.shared, 8)[0]
            struct.pack_into("<q", self.shared, 8, lost + 1)
            return
        base = _HEADER + self.slot * _SLOT.size
        now = _SLOT.unpack_from(self.shared, base)
        _SLOT.pack_into(
            self.shared,
            base,
            *(a + b for a, b in zip(now, values, strict=True)),
        )

    def _slot_values(self, slot: int) -> tuple[int, ...]:
        """Read one slot."""
        return _SLOT.unpack_from(self.shared, _HEADER + slot * _SLOT.size)

    def _calibrate(self) -> int:
        """Measure what one replayed call costs outside its own timing.

        An empty function is called through a probe wrapper built by the
        same code and with the same bookkeeping. The loop's total time
        per call, less what the wrapper timed itself, is the cost of
        wrapper entry, argument packing, the depth check and the counter
        updates that no timestamp inside it can see. The median over
        batches is added to the overhead for every call.
        """
        probe = _Hook(self.config, 0, probe=True)
        probe.index = {
            _digest(pickle.dumps(((), {}), protocol=4)): (
                True,
                None,
                pickle.dumps(("return", None), protocol=4),
            )
        }
        wrapper = probe._replaying(_nothing)
        clock = self.clock
        costs: list[float] = []
        for _ in range(_CALIBRATION_BATCHES):
            before = probe._slot_values(0)[4]
            started = clock()
            for _ in range(_CALIBRATION_CALLS):
                wrapper()
            total = clock() - started
            accounted = probe._slot_values(0)[4] - before
            costs.append(max(0, total - accounted) / _CALIBRATION_CALLS)
        return int(statistics.median(costs))

    # -- patching -----------------------------------------------------

    def patch(self, module: Any) -> None:
        """Wrap the target once its module has loaded."""
        began = time.perf_counter_ns()
        try:
            if module.__name__ == "__main__":
                self.status = "defined in __main__, which a replay cannot load"
                return
            owner, name, kind = _resolve(module, self.qualname)
            fn = _unwrap(owner, name, kind)
            if inspect.isgeneratorfunction(fn) or inspect.iscoroutinefunction(
                fn
            ):
                self.status = (
                    "a generator or coroutine function; its work happens "
                    "after the call returns"
                )
                return
            wrapper = self._wrap(fn)
            stored: Any = wrapper
            if kind == "staticmethod":
                stored = staticmethod(wrapper)
            elif kind == "classmethod":
                stored = classmethod(wrapper)
            setattr(owner, name, stored)
            self.status = "wrapped"
        except Exception as exc:
            self.status = f"not wrapped: {type(exc).__name__}: {exc}"
        finally:
            self.patch_ns += time.perf_counter_ns() - began

    def _wrap(self, fn: Any) -> Any:
        """Build the recording or replaying wrapper for ``fn``."""
        if self.mode == "record":
            return self._recording(fn)
        return self._replaying(fn)

    # -- record -------------------------------------------------------

    def _recording(self, fn: Any) -> Any:
        hook = self

        @functools.wraps(fn)
        def recording(*args: Any, **kwargs: Any) -> Any:
            if getattr(hook.local, "busy", False):
                return fn(*args, **kwargs)
            hook.local.busy = True
            try:
                try:
                    before = pickle.dumps((args, kwargs), protocol=4)
                except Exception:
                    before = None
                call: dict[str, Any] = {"effects": set[str]()}
                hook.local.call = call
            finally:
                hook.local.busy = False
            try:
                value = fn(*args, **kwargs)
            except BaseException as exc:
                hook.local.call = None
                hook._finish(before, args, kwargs, call, "raise", exc)
                raise
            hook.local.call = None
            hook._finish(before, args, kwargs, call, "return", value)
            return value

        return recording

    def _finish(
        self,
        before: bytes | None,
        args: Any,
        kwargs: Any,
        call: dict[str, Any],
        kind: str,
        value: Any,
    ) -> None:
        """Write one finished call, or count why it was not kept."""
        self.local.busy = True
        try:
            with self.lock:
                self.calls += 1
            if before is None:
                with self.lock:
                    self.unpicklable_args += 1
                return
            try:
                after = pickle.dumps((args, kwargs), protocol=4)
                result = pickle.dumps((kind, value), protocol=4)
            except Exception:
                with self.lock:
                    self.unpicklable_result += 1
                return
            key = _digest(before)
            effects = sorted(call["effects"])
            mutated = after != before
            with self.lock:
                if mutated:
                    self.mutating += 1
                if effects:
                    self.effect_calls += 1
                    self.effect_names.update(effects)
                if key in self.seen:
                    return
                self.seen.add(key)
                offset = self.offset
                os.write(self.blobs, result)
                self.offset += len(result)
            line = json.dumps(
                {
                    "key": key,
                    "offset": offset,
                    "length": len(result),
                    "mutated": mutated,
                    "effects": effects,
                    "blob": f"blobs-{self.pid}.bin",
                }
            )
            with self.lock:
                os.write(self.events, line.encode() + b"\n")
        finally:
            self.local.busy = False

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        """Note a side effect of the call in progress on this thread."""
        call = getattr(self.local, "call", None)
        if call is None:
            return
        if event == "open":
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            written = (
                isinstance(mode, str) and bool(_WRITE_MODES & set(mode))
            ) or (
                isinstance(flags, int)
                and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            )
            if written:
                call["effects"].add("file written")
        elif event in _SIDE_EFFECT_EVENTS:
            call["effects"].add(event)

    # -- replay -------------------------------------------------------

    def _load_index(self) -> dict[str, tuple[bool, str | None, bytes]]:
        """Read every recorded call into memory, on the first call."""
        index: dict[str, tuple[bool, str | None, bytes]] = {}
        blobs: dict[str, bytes] = {}
        for entry in sorted(os.listdir(self.directory)):
            if not (entry.startswith("events-") and entry.endswith(".jsonl")):
                continue
            with open(os.path.join(self.directory, entry), "rb") as f:
                for raw in f:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    name = event["blob"]
                    if name not in blobs:
                        with open(
                            os.path.join(self.directory, name), "rb"
                        ) as g:
                            blobs[name] = g.read()
                    start = event["offset"]
                    data = blobs[name][start : start + event["length"]]
                    replayable = not event["mutated"]
                    index.setdefault(
                        event["key"],
                        (
                            replayable,
                            ",".join(event["effects"]) or None,
                            data,
                        ),
                    )
        return index

    def _replaying(self, fn: Any) -> Any:
        hook = self
        clock = self.clock

        @functools.wraps(fn)
        def replaying(*args: Any, **kwargs: Any) -> Any:
            if getattr(hook.local, "depth", 0):
                return fn(*args, **kwargs)
            began = clock()
            index = hook.index
            if index is None:
                index = hook.index = hook._load_index()
            entry = None
            try:
                key = _digest(pickle.dumps((args, kwargs), protocol=4))
                entry = index.get(key)
            except Exception:
                key = ""
            if entry is not None and entry[0]:
                kind, value = pickle.loads(entry[2])  # noqa: S301
                spent = clock() - began
                with hook.lock:
                    hook._bump((1, 1, 0, 0, spent))
                if kind == "raise":
                    raise value
                return value
            lookup = clock() - began
            hook.local.depth = 1
            began = clock()
            try:
                return fn(*args, **kwargs)
            finally:
                spent = clock() - began
                hook.local.depth = 0
                with hook.lock:
                    hook._bump((1, 0, 1, spent, lookup))

        return replaying

    # -- exit ---------------------------------------------------------

    def close(self) -> None:
        """Write the counters, at exit.

        In replay mode only the process that installed the hook writes,
        with every forked child's slot added in; in record mode each
        process writes its own.
        """
        if self.mode == "replay":
            if os.getpid() != self.root_pid:
                return
            used: int = struct.unpack_from("<q", self.shared, 0)[0]
            lost: int = struct.unpack_from("<q", self.shared, 8)[0]
            rows = [self._slot_values(i) for i in range(min(used, _SLOTS))]
            calls, replayed, real, real_ns, hook_ns = (
                sum(r[i] for r in rows) for i in range(5)
            )
            stats: dict[str, Any] = {
                "mode": self.mode,
                "pid": self.root_pid,
                "status": self.status,
                "calls": calls,
                "replayed": replayed,
                "fallthrough": real,
                "fallthrough_seconds": real_ns / 1e9,
                "overhead_seconds": (hook_ns + self.untimed_ns * calls) / 1e9,
                "untimed_ns": self.untimed_ns,
                "processes": len(rows),
                "processes_lost": lost,
                "install_seconds": self.patch_ns / 1e9 + self.install_ns / 1e9,
            }
        else:
            if os.getpid() != self.pid:
                return
            stats = {
                "mode": self.mode,
                "pid": self.pid,
                "status": self.status,
                "calls": self.calls,
                "unpicklable_args": self.unpicklable_args,
                "unpicklable_result": self.unpicklable_result,
                "mutating_calls": self.mutating,
                "effect_calls": self.effect_calls,
                "effects": sorted(self.effect_names),
            }
        name = f"stats-{os.getpid()}-{time.monotonic_ns()}.json"
        path = os.path.join(self.stats_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f)

    install_ns = 0


class _Loader(importlib.abc.Loader):
    """Runs a module with its own loader, then patches the target."""

    def __init__(self, inner: Any, hook: _Hook) -> None:
        self._inner = inner
        self._hook = hook

    def create_module(self, spec: Any) -> Any:
        """Delegate module creation."""
        create = getattr(self._inner, "create_module", None)
        return None if create is None else create(spec)

    def exec_module(self, module: Any) -> None:
        """Run the module, then wrap the target."""
        self._inner.exec_module(module)
        self._hook.patch(module)

    def __getattr__(self, name: str) -> Any:
        """Delegate everything else, such as ``get_source``."""
        return getattr(self._inner, name)


class _Finder(importlib.abc.MetaPathFinder):
    """Finds the target module with the other finders and adopts it."""

    def __init__(self, hook: _Hook) -> None:
        self._hook = hook
        self._busy = threading.local()

    def find_spec(
        self, fullname: str, path: Any = None, target: Any = None
    ) -> Any:
        """Find the module, wrapping its loader if it is the target."""
        if fullname != self._hook.module_name or getattr(
            self._busy, "on", False
        ):
            return None
        self._busy.on = True
        try:
            spec = None
            for finder in sys.meta_path:
                find = getattr(finder, "find_spec", None)
                if finder is self or find is None:
                    continue
                spec = find(fullname, path, target)
                if spec is not None:
                    break
        finally:
            self._busy.on = False
        if spec is not None and spec.loader is not None:
            spec.loader = _Loader(spec.loader, self._hook)
        return spec


def install(config_path: str, started_ns: int) -> None:
    """Install the hook in this process.

    Parameters
    ----------
    config_path : str
        JSON file with ``mode`` (``record`` or ``replay``), ``module``,
        ``qualname``, ``dir`` (where calls are written or read),
        ``stats_dir`` (where the counters go; ``dir`` when absent),
        ``clock`` (``wall`` or ``cpu``) and optionally ``untimed_ns``, a
        fixed per-call cost that replaces the calibration.
    started_ns : int
        ``time.perf_counter_ns()`` when the generated
        ``sitecustomize`` began, so the time spent installing is known.
    """
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    hook = _Hook(config, started_ns)
    existing = sys.modules.get(hook.module_name)
    if existing is not None:
        hook.patch(existing)
    else:
        sys.meta_path.insert(0, _Finder(hook))
    if hook.mode == "record":
        sys.addaudithook(hook.audit)
    atexit.register(hook.close)
    os.register_at_fork(
        before=hook.before_fork, after_in_child=hook.after_fork
    )
    hook.install_ns = time.perf_counter_ns() - started_ns
