r"""The side-effect guard: the `side_effects` preflight and the reset.

This is "Side effects" in ``docs/method.md``. A benchmark that writes
to a real database or calls a paid API is not safe to run blindly, so
before anything executes in a tree, `side_effects_gate` looks for
service addresses and refuses the run when one names a host that isn't
local, unless the spec opts in with ``allow_remote_services``. It is
the first gate in precedence, so the caller runs it, and stops on a
failure, before any reset, warmup, round or correctness check.

What it is for, as "What the guard is for" in ``docs/method.md`` puts
it: catching a benchmark that would reach a real service by mistake.
It reads addresses the way the common clients do and refuses anything
it can't parse cleanly. It is not a sandbox: an address built at run
time or spelled to fool a parser is outside what it promises.

Where it looks:

- The environment the commands run with. Every command of a
  comparison gets `benchmark_environment`: a few base variables from
  perfproof's own environment (`BASE_ENV_VARIABLES` and ``LC_*``)
  overlaid with the spec's ``env``, so perfproof's own credentials and
  service addresses never reach the benchmark. The caller passes that
  environment to the gate and the spec's ``env`` is checked too. A
  variable is a service address when its name, in upper case, is one
  of `SERVICE_VARIABLES` or ends with one of `SERVICE_SUFFIXES`
  (``DATABASE_URL``, ``REDIS_URL``, ``DB_HOST``, ``KAFKA_SERVERS``,
  ``PG_DSN``). The value of any other variable is still searched for
  URLs whose scheme is one of `SERVICE_SCHEMES` or `WEB_SCHEMES`, so
  ``OPENAI_API_BASE=https://api.openai.com/v1`` is refused too.
- The argv of the benchmark, correctness, reset and setup commands:
  URLs with a service or web scheme in any argument (except an
  ``https`` URL on a public package registry in a ``setup`` command,
  `PACKAGE_REGISTRY_HOSTS`), keyword DSNs
  (``host=...``, ``service=...``), ``NAME=value`` arguments whose name
  is a service variable (``env PGHOST=h ...``), and ``--name=value``
  options whose name is one too or one of the config keys below.
- The spec's declared ``config`` files, read as UTF-8 text line by
  line, a byte order mark ignored. A key is listed when it is a
  service variable name (``.`` and ``-`` read as ``_``) or one of
  ``host(s)``, ``hostname``, ``hostaddr``, ``server(s)``,
  ``endpoint``, ``address`` and ``dsn``. Every listed key on a line is
  read, as ``KEY=value``, ``key: value``, ``- key: value`` or
  ``"key": "value"``, at the start of the line or inside one-line
  JSON, flow mappings and dicts (``{"host": "h"}``,
  ``db: {host: h}``). URLs with a service scheme are read anywhere on
  a line. Web URLs are read only under a listed key, so package
  indexes, project links and documentation URLs under ``url``,
  ``uri`` or ``homepage`` don't refuse a run, while
  ``endpoint: https://api.openai.com/v1`` does. Comment lines (``#``,
  ``;``, ``//``) are skipped. ``null``, ``None``, ``~`` and ``false``
  are empty. A mapping opened at the end of a line (pretty-printed
  JSON, a Python dict) is empty too, because its entries have keys of
  their own on later lines. A list or tuple opened at the end of a
  line is read item by item on the lines that follow, until it
  closes; one that never closes, or holds a nested list, fails closed.
  A listed key whose value is computed (``${VAR}``,
  ``os.environ.get(...)``), a mapping on the same line, a nested list,
  or a list left open on the line fails closed.
- Names that list who may call the application rather than what it
  connects to are not read: ``ALLOWED_HOSTS``,
  ``CSRF_TRUSTED_ORIGINS`` and ``CORS_ALLOWED_ORIGINS``. A schemeless
  value under a ``*_URL`` or ``*_URI`` name that reads as a relative
  path (``LOGIN_URL = "login"``) names no host.

A URL found in free text is the whole token from its scheme to the
next whitespace, or to the closing quote of a quoted value, so nothing
after a quote or bracket inside it is cut off. Its host is read as the
clients read it:

- Web schemes (`WEB_SCHEMES`) follow the WHATWG URL standard: the
  authority ends at the first ``/``, ``?`` or ``#``, and the host
  follows its last ``@``. So ``http://localhost/a@b`` is local. A
  backslash before that point is refused, because clients disagree:
  WHATWG reads ``http://localhost\@h/`` as ``localhost``, curl, httpx
  and urllib as ``h``. The scheme must be followed by
  exactly ``//``: ``https:h``, ``https:/h`` and other slash runs,
  which curl and Node read as host ``h``, are refused.
- Every other scheme follows libpq: the user info ends at an ``@``
  found before the first ``/``, so ``postgres://localhost?@h/db``
  names ``h``. An ``@`` after the first ``/``, which SQLAlchemy can
  read as the end of a password, is refused, as is more than one.
- An authority holding a quote, ``<``, ``>``, a backtick, ``#`` (for
  service schemes), a backslash, whitespace or a control character is
  refused: that is where parsers disagree.

What a value may hold: a URL (``postgresql://u@h:5432/db``, several
hosts as ``h1,h2``, a ``host`` or ``hostaddr`` query parameter, a
``jdbc:`` prefix, a percent-encoded socket path as the host), a
keyword DSN (libpq ``host=h port=5432``, or ``Server=tcp:h,1433;``
connection strings), a Go MySQL DSN (``u@tcp(h:3306)/db``), or a
comma- or space-separated list of ``host``, ``host:port``,
``[v6]:port`` or socket paths. Every value is parsed by index or with
anchored patterns that don't backtrack, so the guard's cost is linear
in what it reads.

A host is local when it is exactly ``localhost`` (any case, no trailing
dot, which a resolver may send to DNS), a loopback address
(``127.0.0.0/8``, ``::1``, ``::ffff:127.0.0.1``), the unspecified
address (``0.0.0.0``, ``::``, which connect to this host), a Unix
socket path (``/...`` or abstract ``@...``), a service URL with no host
(the client's default local socket), a ``unix``, ``file`` or ``sqlite``
scheme, or one of the container hostnames the caller started for this
run. Nothing is resolved: a lookup would itself reach the network and
its answer can change. So everything else is not local, including this
machine's own name and LAN addresses, link-local addresses, ``*.local``
and ``host.docker.internal``. A value that cannot be parsed, or a
config file that cannot be read, fails closed with the same refusal.
``PGSERVICE``, ``PGSERVICEFILE``, ``PGSYSCONFDIR`` and a ``service``
key in a URL query or DSN make libpq read its host from a service file
the guard doesn't read, so they fail closed too. Values are never
echoed, because they can carry passwords; findings name the variable,
file or argument and the host.

Stated limit, row 14 of ``docs/failure-modes.md``: a service configured
only where the guard doesn't read it is not seen. That covers files
not declared as ``config``, addresses under keys not listed above
(a nested table's own keys are read only when they are), values on
the lines after their key (YAML block lists and scalars), XML elements
(``<host>h</host>``), PDO-style strings (``mysql:host=h;dbname=d``)
outside a service variable, command options such as ``--host h`` or
``-h h``, ``*_PORT`` variables (a port names no host), proxies, a
cloud SDK's default endpoint reached through a region variable
(``AWS_REGION`` and the like) with credentials under ``HOME``, and
addresses the benchmark builds at run time.

`run_reset` runs the spec's reset command before an execution. A reset
that fails stops the run: it raises `ResetError` carrying a failed
`side_effects` gate with the command's output, for the record.
"""

import contextlib
import errno
import ipaddress
import os
import re
import signal
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Collection, Iterable, Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import IO, Final, cast

from perfproof.bench.lock import IGNORED_NAMES
from perfproof.schema import BenchmarkSpec, CommandOutput, GateResult

__all__ = [
    "BASE_ENV_VARIABLES",
    "MAX_FINDINGS",
    "OUTPUT_TAIL_BYTES",
    "PACKAGE_REGISTRY_HOSTS",
    "PROGRAM_ERRNOS",
    "SERVICE_SCHEMES",
    "SERVICE_SUFFIXES",
    "SERVICE_VARIABLES",
    "WEB_SCHEMES",
    "ResetError",
    "benchmark_environment",
    "is_service_variable",
    "points_at_tree",
    "run_reset",
    "side_effects_gate",
    "without_own_environment",
]

PROGRAM_ERRNOS: Final = frozenset(
    {
        errno.ENOENT,
        errno.ENOEXEC,
        errno.EACCES,
        errno.ENOTDIR,
        errno.ELOOP,
        errno.EISDIR,
        errno.ETXTBSY,
        errno.ENAMETOOLONG,
    }
)
"""``execve`` errors that point at the program in the tree. CPython puts
the executable in ``filename`` for every ``execve`` error, so E2BIG,
ENOMEM, ENFILE, EMFILE and EAGAIN carry it too, though they are faults
of the spec's environment or the host, not of the tree."""

BASE_ENV_VARIABLES: Final = frozenset(
    {
        "HOME",
        "LANG",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TZ",
        "USER",
    }
)
"""Variables of perfproof's own environment every command inherits,
besides the ``LC_*`` locale variables; all else comes from the spec."""
SERVICE_VARIABLES: Final = frozenset(
    {"DATABASE_URL", "REDIS_URL", "PGHOST", "PGHOSTADDR"}
)
"""Variable names that hold a service address whatever their suffix."""
SERVICE_SUFFIXES: Final = (
    "_URL",
    "_URI",
    "_DSN",
    "_HOST",
    "_HOSTS",
    "_HOSTADDR",
    "_ADDR",
    "_ADDRESS",
    "_ENDPOINT",
    "_SERVER",
    "_SERVERS",
)
"""Name endings that mark a variable as a service address."""
SERVICE_SCHEMES: Final = frozenset(
    {
        "amqp",
        "amqps",
        "bolt",
        "cassandra",
        "clickhouse",
        "cockroachdb",
        "couchdb",
        "elasticsearch",
        "kafka",
        "mariadb",
        "memcached",
        "mongodb",
        "mqtt",
        "mssql",
        "mysql",
        "nats",
        "neo4j",
        "oracle",
        "pgsql",
        "postgres",
        "postgresql",
        "redis",
        "rediss",
        "sqlserver",
        "valkey",
    }
)
"""URL schemes, before any ``+driver`` part, that name a service
wherever they appear."""
WEB_SCHEMES: Final = frozenset({"http", "https", "ws", "wss"})
"""URL schemes checked in every variable and argument, and in config
files under a listed key."""
PACKAGE_REGISTRY_HOSTS: Final = frozenset(
    {
        "crates.io",
        "files.pythonhosted.org",
        "index.crates.io",
        "index.rubygems.org",
        "proxy.golang.org",
        "pypi.org",
        "registry.npmjs.org",
        "registry.yarnpkg.com",
        "repo.maven.apache.org",
        "repo1.maven.org",
        "rubygems.org",
        "static.crates.io",
        "sum.golang.org",
    }
)
"""Hosts of public package registries. An ``https`` URL on exactly one,
with no user info, is allowed in a ``setup`` argument and nowhere else
("Side effects" in ``docs/method.md``)."""
MAX_FINDINGS: Final = 20
"""Most findings a gate's detail lists; the rest are counted."""
OUTPUT_TAIL_BYTES: Final = 16 * 1024
"""Bytes kept from the end of each output stream of a failed reset."""

_CONFIG_KEYS: Final = frozenset(
    {
        "ADDRESS",
        "DSN",
        "ENDPOINT",
        "HOST",
        "HOSTADDR",
        "HOSTNAME",
        "HOSTS",
        "SERVER",
        "SERVERS",
    }
)
_NOT_TARGETS: Final = frozenset(
    {"ALLOWED_HOSTS", "CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS"}
)
"""Names that end like a service address but list who may call the
application (Django and NetBox settings), not what it connects to."""
_URL_SUFFIXES: Final = ("_URL", "_URI")
_EMPTY_WORDS: Final = frozenset({"", "null", "none", "~", "false"})
_BLOCK_OPENERS: Final = frozenset("{[(")
_LIST_CLOSERS: Final[Mapping[str, str]] = MappingProxyType(
    {"[": "]", "(": ")"}
)
_REST_BLANK: Final = re.compile(r"[\s,]*\Z")
_COMMENT_STARTS: Final = ("#", ";", "//")
_SERVICE_PROBLEM: Final = "names a connection service the guard doesn't read"
_INDIRECT_VARIABLES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "PGSERVICE": _SERVICE_PROBLEM,
        "PGSERVICEFILE": "names a service file the guard doesn't read",
        "PGSYSCONFDIR": "names a service file directory the guard "
        "doesn't read",
    }
)
_SERVICE: Final = "\x00service"
"""Stands for a ``service`` key among parsed hosts; never a host."""
_LOCAL_SCHEMES: Final = frozenset({"file", "sqlite", "sqlite3", "unix"})
_DSN_HOST_KEYS: Final = frozenset(
    {"host", "hostaddr", "hostname", "address", "addr", "networkaddress"}
)
_DSN_SERVER_KEYS: Final = frozenset({"server", "datasource"})
_QUERY_HOST_KEYS: Final = frozenset({"host", "hostaddr"})
_SCHEME: Final = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_SCHEME_IN_TEXT: Final = re.compile(
    r"(?<![A-Za-z0-9+.-])(?:jdbc:)?([A-Za-z][A-Za-z0-9+.-]*):"
)
_WHITESPACE: Final = re.compile(r"\s")
_TRAILING_PUNCTUATION: Final = ".,;:)]}"
_VALUE_END: Final = frozenset(" \t\r\n\f\v,;:)]}>")
_FORBIDDEN_IN_AUTHORITY: Final = frozenset("'\"<>`#\\")
_GO_DSN: Final = re.compile(
    r"^(?:[^@()]*@)?(tcp|tcp4|tcp6|udp|unix)\(([^()]*)\)(?:/.*)?$"
)
_DSN_KEY_CHARS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ \t"
)
_DSN_HOST_KEY: Final = re.compile(
    r"(?:^|[\s;])(?:host|hostaddr|server|data source|service)\s*=",
    re.IGNORECASE,
)
_HOSTNAME: Final = re.compile(
    r"^[A-Za-z0-9_]([A-Za-z0-9_.-]*[A-Za-z0-9_])?\.?$"
)
_PORT: Final = re.compile(r"^[0-9]{0,5}$")
_CONFIG_KEY: Final = re.compile(
    r"\s*(?:export\s+|-\s+)?[\"']?([A-Za-z_][A-Za-z0-9_.-]*)[\"']?\s*[:=]"
)
_KEY_IN_LINE: Final = re.compile(
    r"(?:^|(?<=[\s{,\[(]))([\"']?)([A-Za-z_][A-Za-z0-9_.-]*)\1\s*[:=]"
)
_INLINE_TOKEN: Final = re.compile(r"[^\s,;}\])#]*")
_BRACKET: Final = re.compile(r"[\[\](){}]")
_PATH_CHARS: Final = re.compile(r"[A-Za-z0-9._~%/:-]+")
_ASSIGNMENT: Final = re.compile(
    r"^(--?)?([A-Za-z_][A-Za-z0-9_.-]*)=(.*)$", re.DOTALL
)
_SQLSERVER_PREFIX: Final = re.compile(r"^(?:tcp|np|lpc):", re.IGNORECASE)
_SQLSERVER_LOCAL: Final = frozenset({".", "(local)", "(localdb)"})


class ResetError(RuntimeError):
    """The spec's reset command failed, so the run must stop.

    Attributes
    ----------
    gate : GateResult
        A failed `side_effects` gate whose ``output`` holds the reset's
        argv, exit status and output tails, ready for the record.
    """

    def __init__(self, gate: GateResult) -> None:
        super().__init__(gate.detail)
        self.gate = gate


def is_service_variable(name: str) -> bool:
    """Say whether a variable or config key names a service address.

    Parameters
    ----------
    name : str
        Environment variable name or config key; compared in upper
        case.

    Returns
    -------
    bool
        True for a name in `SERVICE_VARIABLES` or ending in one of
        `SERVICE_SUFFIXES`, except ``ALLOWED_HOSTS``,
        ``CSRF_TRUSTED_ORIGINS`` and ``CORS_ALLOWED_ORIGINS``, which
        list who may call an application rather than what it connects
        to.
    """
    upper = name.upper()
    if upper in _NOT_TARGETS:
        return False
    return upper in SERVICE_VARIABLES or upper.endswith(SERVICE_SUFFIXES)


def _host_problem(host: str, containers: frozenset[str]) -> str | None:
    """Return why ``host`` is not local, or ``None`` when it is.

    ``host`` is one parsed endpoint: a name, an IP literal with any
    brackets removed, or a socket path.
    """
    if host == _SERVICE:
        return _SERVICE_PROBLEM
    if host.startswith(("/", "@")):
        return None
    lowered = host.lower()
    if lowered == "localhost" or lowered in containers:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME.fullmatch(host):
            return "holds a host that cannot be parsed"
        return f"points at {host}, which is not local"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_unspecified:
        return None
    return f"points at {host}, which is not local"


def _endpoint(item: str) -> str | None:
    """Return the host of ``host``, ``host:port`` or ``[v6]:port``.

    A socket path is returned as it is. ``None`` when ``item`` is none
    of these.
    """
    if item.startswith(("/", "@")):
        return item
    if item.startswith("["):
        inside, bracket, port = item[1:].partition("]")
        if not bracket or (port and not port.startswith(":")):
            return None
        if not _PORT.fullmatch(port[1:]):
            return None
        return _ip_literal(inside, version=6)
    if item.count(":") > 1:
        return _ip_literal(item, version=6)
    host, _, port = item.partition(":")
    if not host or not _PORT.fullmatch(port):
        return None
    return host


def _ip_literal(text: str, *, version: int) -> str | None:
    """Return ``text`` if it is an IP address of ``version``."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    return text if address.version == version else None


def _endpoints(items: Iterable[str]) -> list[str] | None:
    """Parse endpoints, skipping empty items; ``None`` if any fails."""
    hosts: list[str] = []
    for item in items:
        if not item:
            continue
        host = _endpoint(item)
        if host is None:
            return None
        hosts.append(host)
    return hosts


def _scheme_parts(scheme: str) -> set[str]:
    """Split a scheme such as ``postgresql+psycopg`` into its parts."""
    return set(scheme.lower().split("+"))


def _first_of(text: str, stops: str) -> int:
    """Return where the first of ``stops`` is in text, or its end."""
    end = len(text)
    for stop in stops:
        index = text.find(stop, 0, end)
        if index != -1:
            end = index
    return end


def _clean_authority(authority: str) -> bool:
    """Say whether an authority holds nothing parsers read differently.

    Quotes, ``<``, ``>``, backticks, ``#``, backslashes, whitespace and
    control characters are where client parsers disagree on where the
    host starts or ends.
    """
    return not any(
        c in _FORBIDDEN_IN_AUTHORITY or c.isspace() or not c.isprintable()
        for c in authority
    )


def _web_hostport(rest: str) -> tuple[str, str] | None:
    r"""Split a web URL after ``scheme:`` into host and port.

    Read as the WHATWG URL standard reads ``http``, ``https``, ``ws``
    and ``wss``: the authority ends at the first ``/``, ``?`` or ``#``,
    and the host follows its last ``@``. A backslash before that point
    is refused: WHATWG ends the authority there, while curl, httpx and
    urllib read on to an ``@`` after it. The scheme must be
    followed by exactly ``//``, because the standard also accepts
    ``https:h``, ``https:/h`` and any run of slashes and backslashes,
    which other clients read differently. A web URL must name a host.
    The tail is empty: no query key of a web URL names a host.
    """
    slashes = len(rest) - len(rest.lstrip("/\\"))
    if slashes != 2 or not rest.startswith("//"):
        return None
    rest = rest[2:]
    end = _first_of(rest, "/?#")
    authority = rest[:end]
    if not _clean_authority(authority):
        return None
    hostport = authority.rpartition("@")[2]
    return (hostport, "") if hostport else None


def _service_hostport(rest: str) -> tuple[str, str] | None:
    """Split a service URL after ``scheme:`` into host list and tail.

    Read as libpq reads it: the user info is everything before an
    ``@`` found before the first ``/``, and the host list ends at the
    first ``/`` or ``?``. So ``postgres://localhost?@h/db`` names host
    ``h``. An ``@`` after the first ``/`` is refused, because
    SQLAlchemy can still read it as the end of a password; so is more
    than one ``@``.
    """
    if not rest.startswith("//"):
        return None
    rest = rest[2:]
    slash = rest.find("/")
    segment = rest if slash == -1 else rest[:slash]
    if segment.count("@") > 1 or (slash != -1 and "@" in rest[slash:]):
        return None
    if not _clean_authority(segment):
        return None
    after_user = segment.rpartition("@")[2]
    end = _first_of(after_user, "?")
    tail = after_user[end:] + ("" if slash == -1 else rest[slash:])
    return after_user[:end], tail


def _url_hosts(url: str) -> list[str] | None:
    """Return the hosts a URL connects to; ``None`` if unparseable.

    Web schemes follow `_web_hostport`, every other scheme
    `_service_hostport`. Several hosts may be listed as ``h1,h2``. A
    ``host`` or ``hostaddr`` query key adds a host, and a ``service``
    key stands for one read from a service file.
    """
    match = _SCHEME.match(url)
    if match is None:
        return None
    parts = _scheme_parts(match.group(1))
    rest = url[match.end() :]
    if parts & _LOCAL_SCHEMES:
        return [] if rest.startswith("//") else None
    web = bool(parts & WEB_SCHEMES)
    split = _web_hostport(rest) if web else _service_hostport(rest)
    if split is None:
        return None
    hostport, tail = split
    items = [urllib.parse.unquote(i) for i in hostport.split(",")]
    hosts = _endpoints(items) if hostport else []
    if hosts is None:
        return None
    if web:
        return hosts
    query = tail.partition("?")[2].partition("#")[0]
    for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        if key.lower() == "service":
            hosts.append(_SERVICE)
        elif key.lower() in _QUERY_HOST_KEYS:
            more = _endpoints(value.split(","))
            if more is None:
                return None
            hosts.extend(more)
    return hosts


def _is_registry_url(url: str) -> bool:
    """Say whether ``url`` is ``https`` on exactly a package registry.

    The host must be one of `PACKAGE_REGISTRY_HOSTS`, spelled as the
    list does (any case), with no user info and the default port, and
    the URL must parse as `_web_hostport` reads it.
    """
    match = _SCHEME.match(url)
    if match is None or match.group(1).lower() != "https":
        return False
    if any(c.isspace() or not c.isprintable() for c in url):
        return False
    rest = url[match.end() :]
    if _web_hostport(rest) is None:
        return False
    body = rest[2:]
    authority = body[: _first_of(body, "/?#")]
    name, _, port = authority.partition(":")
    return (
        "@" not in authority
        and name.lower() in PACKAGE_REGISTRY_HOSTS
        and port in ("", "443")
    )


def _whole_value(text: str) -> str:
    """Return the value of a ``NAME=value`` argument, else the argument.

    A registry URL is exempt only when it is all of that: any text
    around it (a space, tab, newline or NUL after it can make a client
    read another host) keeps it under the ordinary check.
    """
    match = _ASSIGNMENT.match(text)
    return text if match is None else match.group(3)


def _starts_like_dsn(text: str) -> bool:
    """Say whether text opens with ``key =``, as a keyword DSN does.

    A key is letters, digits, ``_`` and the spaces of ``Data Source``.
    Scanned by index, so a long run of spaces costs linear time.
    """
    start = len(text) - len(text.lstrip())
    if start == len(text) or not (
        text[start].isascii() and (text[start].isalpha() or text[start] == "_")
    ):
        return False
    end = start
    while end < len(text) and text[end] in _DSN_KEY_CHARS:
        end += 1
    return end < len(text) and text[end] == "="


def _dsn_pairs(value: str) -> Iterator[tuple[str, str] | None]:
    """Yield the ``(key, value)`` pairs of a keyword DSN, in order.

    Keys are lower case with spaces removed; quoted values are
    unquoted, with backslash escapes resolved. Pairs are separated by
    whitespace or ``;``. ``None`` is yielded, and the scan stops, at
    anything else. One pass by index: linear in the length.
    """
    n = len(value)
    position = 0
    while True:
        while position < n and (
            value[position].isspace() or value[position] == ";"
        ):
            position += 1
        if position == n:
            return
        if not (
            value[position].isascii()
            and (value[position].isalpha() or value[position] == "_")
        ):
            yield None
            return
        start = position
        while position < n and value[position] in _DSN_KEY_CHARS:
            position += 1
        if position == n or value[position] != "=":
            yield None
            return
        key = "".join(value[start:position].split()).lower()
        position += 1
        while position < n and value[position].isspace():
            position += 1
        if position < n and value[position] in "'\"":
            quote = value[position]
            position += 1
            chars: list[str] = []
            while position < n and value[position] != quote:
                if value[position] == "\\" and position + 1 < n:
                    position += 1
                chars.append(value[position])
                position += 1
            if position == n:
                yield None
                return
            position += 1
            raw = "".join(chars)
        else:
            start = position
            while position < n and not (
                value[position].isspace() or value[position] == ";"
            ):
                position += 1
            raw = value[start:position]
        yield key, raw


def _dsn_hosts(value: str) -> list[str] | None:
    """Return the hosts of a keyword DSN; ``None`` if unparseable.

    Covers libpq's ``host=h hostaddr=a port=5432`` and ``Key=value;``
    connection strings (``Host``, ``Server``, ``Data Source``). No host
    key means the client's default, a local socket.
    """
    hosts: list[str] = []
    for pair in _dsn_pairs(value):
        if pair is None:
            return None
        key, raw = pair
        if key == "service":
            more = [_SERVICE]
        elif key in _DSN_HOST_KEYS:
            more = _endpoints(raw.split(","))
        elif key in _DSN_SERVER_KEYS:
            more = _server_host(raw)
        else:
            continue
        if more is None:
            return None
        hosts.extend(more)
    return hosts


def _server_host(raw: str) -> list[str] | None:
    """Parse a SQL Server ``Server`` value such as ``tcp:host,1433``."""
    host = _SQLSERVER_PREFIX.sub("", raw).partition(",")[0]
    host = host.partition("\\")[0]
    if host.lower() in _SQLSERVER_LOCAL:
        return []
    return _endpoints([host])


def _is_relative_url(text: str) -> bool:
    """Say whether a schemeless value reads as a relative URL path.

    ``login``, ``accounts/login/``, ``/login`` and ``./login`` name no
    host. A value whose first segment has a dot or a colon
    (``db.example.com``, ``db:5432``, ``api.example.com/v1``) is still
    read as a host, and so is anything with other characters.
    """
    if not _PATH_CHARS.fullmatch(text):
        return False
    if text.startswith(("/", ".")):
        return True
    first = text.partition("/")[0]
    return "." not in first and ":" not in first


def _value_hosts(value: str, *, url_name: bool = False) -> list[str] | None:
    """Return the hosts a service address value names.

    Parameters
    ----------
    value : str
        The value.
    url_name : bool, optional
        Whether the value's name ends in ``_URL`` or ``_URI``, so a
        schemeless relative path such as ``login`` names no host.

    Returns
    -------
    list of str or None
        Hosts and socket paths, empty when the value names no network
        host (empty, a local scheme, a URL with no host, a relative
        URL); ``None`` when the value is none of the forms in the
        module docstring.
    """
    text = value.strip()
    if not text:
        return []
    if text.lower().startswith("jdbc:"):
        text = text[len("jdbc:") :]
    scheme = _SCHEME.match(text)
    if url_name and scheme is None and _is_relative_url(text):
        return []
    if (
        scheme is not None
        and _scheme_parts(scheme.group(1)) & WEB_SCHEMES
        and not text.startswith("//", scheme.end())
    ):
        return None
    if scheme is not None and text.startswith("//", scheme.end()):
        hosts: list[str] = []
        for url in re.split(r",\s*(?=[A-Za-z][A-Za-z0-9+.-]*://)", text):
            more = _url_hosts(url.strip())
            if more is None:
                return None
            hosts.extend(more)
        return hosts
    go = _GO_DSN.match(text)
    if go is not None:
        if go.group(1) == "unix":
            return []
        return _endpoints([go.group(2)])
    if _starts_like_dsn(text):
        return _dsn_hosts(text)
    if scheme is not None and _scheme_parts(scheme.group(1)) & _LOCAL_SCHEMES:
        return []
    return _endpoints(re.split(r"[,\s]+", text))


def _problems_of_value(
    value: str,
    containers: frozenset[str],
    *,
    url_name: bool = False,
) -> list[str] | str:
    """Return the problem of each non-local host in a value.

    A string instead of a list means the value cannot be parsed.
    ``url_name`` is as in `_value_hosts`.
    """
    hosts = _value_hosts(value, url_name=url_name)
    if hosts is None:
        return "cannot be parsed as a service address"
    problems: list[str] = []
    for host in hosts:
        problem = _host_problem(host, containers)
        if problem is not None and problem not in problems:
            problems.append(problem)
    return problems


def _urls_in(text: str, schemes: frozenset[str]) -> Iterator[str]:
    """Yield the URLs found anywhere in text with one of ``schemes``.

    A URL is the whole token from its scheme to the next whitespace,
    or to the closing quote when the token opens a quoted value and the
    quote ends it (whitespace or a delimiter follows), so a quote or
    bracket inside it stays in the URL for the parser to refuse;
    trailing sentence punctuation is dropped from an unquoted
    token. A service scheme counts only when followed by ``//``; a web
    scheme counts in any form, so ``https:h`` is read and refused. A
    match inside a URL already yielded is skipped.
    """
    done = 0
    space = -1
    for match in _SCHEME_IN_TEXT.finditer(text):
        start = match.start()
        if start < done:
            continue
        parts = _scheme_parts(match.group(1))
        if not parts & schemes:
            continue
        if not parts & WEB_SCHEMES and not text.startswith("//", match.end()):
            continue
        if space < start:
            # The first whitespace at or after an earlier start is also
            # the first after this one, so each search covers new text.
            found = _WHITESPACE.search(text, start)
            space = len(text) if found is None else found.start()
        end = space
        quote = text[start - 1] if start and text[start - 1] in "'\"" else ""
        if quote:
            closing = text.find(quote, start)
            after = text[closing + 1 : closing + 2]
            if closing != -1 and (not after or after in _VALUE_END):
                end = closing
            token = text[start:end]
        else:
            token = text[start:end].rstrip(_TRAILING_PUNCTUATION)
        done = end
        yield token


_ALL_SCHEMES: Final = SERVICE_SCHEMES | WEB_SCHEMES


class _Findings:
    """Problems found, in order, without repeats.

    The first `MAX_FINDINGS` are kept; later distinct ones are only
    counted, so collecting stays linear in the number found.
    """

    def __init__(self, containers: frozenset[str]) -> None:
        self.containers = containers
        self.items: list[str] = []
        self.more = 0
        self._seen: set[str] = set()

    def add(self, where: str, problem: str) -> None:
        item = f"{where} {problem}"
        if item in self._seen:
            return
        self._seen.add(item)
        if len(self.items) < MAX_FINDINGS:
            self.items.append(item)
        else:
            self.more += 1

    def value(
        self,
        where: str,
        value: str,
        *,
        url_name: bool = False,
    ) -> None:
        """Check a value known to be a service address."""
        problems = _problems_of_value(
            value, self.containers, url_name=url_name
        )
        if isinstance(problems, str):
            self.add(where, problems)
            return
        for problem in problems:
            self.add(where, problem)

    def text(
        self,
        where: str,
        text: str,
        schemes: frozenset[str] = _ALL_SCHEMES,
        *,
        registry_ok: bool = False,
    ) -> None:
        """Check the URLs with one of ``schemes`` in free text.

        With ``registry_ok``, a URL on a package registry is skipped.
        """
        whole = _whole_value(text)
        for url in _urls_in(text, schemes):
            if registry_ok and url == whole and _is_registry_url(url):
                continue
            self.value(where, url)


def _check_environment(
    findings: _Findings, label: str, env: Mapping[str, str]
) -> None:
    """Check every variable of one environment."""
    for name in sorted(env):
        value = env[name]
        where = f"{label} {name}"
        upper = name.upper()
        indirect = _INDIRECT_VARIABLES.get(upper)
        if upper in _NOT_TARGETS:
            continue
        if indirect is not None and value:
            findings.add(where, indirect)
        elif is_service_variable(name):
            findings.value(
                where, value, url_name=upper.endswith(_URL_SUFFIXES)
            )
        else:
            findings.text(where, value)


def _is_keyword_dsn(arg: str) -> bool:
    """Say whether an argument is a keyword DSN naming a host."""
    return (
        "://" not in arg
        and _starts_like_dsn(arg)
        and _DSN_HOST_KEY.search(arg) is not None
    )


def _check_commands(findings: _Findings, spec: BenchmarkSpec) -> None:
    """Check the argv of the benchmark, checks, reset and setup."""
    commands = [("command", spec.command, False)]
    if spec.correctness.command is not None:
        commands.append(
            ("correctness command", spec.correctness.command, False)
        )
    if spec.correctness.coverage_command is not None:
        commands.append(
            ("coverage command", spec.correctness.coverage_command, False)
        )
    if spec.reset is not None:
        commands.append(("reset command", spec.reset.command, False))
    if spec.setup is not None:
        commands.extend(
            (f"setup command {i}", argv, True)
            for i, argv in enumerate(spec.setup.commands)
        )
    for label, argv, registry_ok in commands:
        for index, arg in enumerate(argv):
            where = f"{label} argument {index}"
            findings.text(where, arg, registry_ok=registry_ok)
            if _is_keyword_dsn(arg):
                findings.value(where, arg)
            _check_assignment(findings, where, arg, registry_ok=registry_ok)


def _check_assignment(
    findings: _Findings, where: str, arg: str, *, registry_ok: bool = False
) -> None:
    """Check a ``NAME=value`` or ``--name=value`` argument.

    With ``registry_ok``, a value that is a package registry URL passes.
    """
    match = _ASSIGNMENT.match(arg)
    if match is None:
        return
    dashes, name, value = match.groups()
    if registry_ok and _is_registry_url(value):
        return
    key = re.sub(r"[.-]", "_", name).upper()
    indirect = _INDIRECT_VARIABLES.get(key)
    if indirect is not None and value:
        findings.add(where, indirect)
    elif is_service_variable(key):
        findings.value(where, value, url_name=key.endswith(_URL_SUFFIXES))
    elif dashes and key in _CONFIG_KEYS:
        findings.value(where, value)


def _config_files(root: Path, declared: str) -> Iterator[tuple[str, str]]:
    """Yield ``(relative path, problem)`` or ``(relative path, "")``.

    An empty problem means the file is there to be read.
    """
    top = root / declared
    if not top.exists():
        yield declared, "is missing, so it cannot be checked"
        return
    if not top.is_dir():
        yield declared, ""
        return
    for dirpath, dirnames, filenames in os.walk(top):
        base = Path(dirpath).relative_to(root).as_posix()
        for name in sorted(dirnames):
            if name in IGNORED_NAMES:
                dirnames.remove(name)
            elif os.path.islink(os.path.join(dirpath, name)):
                dirnames.remove(name)
                yield f"{base}/{name}", "is a directory symlink, not read"
        dirnames.sort()
        for name in sorted(filenames):
            if name not in IGNORED_NAMES:
                yield f"{base}/{name}", ""


def _config_value(raw: str) -> str:
    """Take a config value out of its quotes, list or trailing comment.

    A quoted value followed by nothing but a comma, semicolon or
    comment is returned unquoted; an unquoted one loses a trailing
    comment (``#`` or ``;`` after whitespace) and comma; a one-line
    list becomes a comma-separated value. Anything else is returned as
    it is, for the parser to accept or refuse. Scanned by index, so
    linear in the length.
    """
    value = raw.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        if end != -1:
            after = value[end + 1 :].lstrip()
            if after[:1] in (",", ";"):
                after = after[1:].lstrip()
            if not after or after[0] in "#;":
                return value[1:end]
    cut = len(value)
    for index in range(1, len(value)):
        if value[index] in "#;" and value[index - 1].isspace():
            cut = index
            break
    value = value[:cut].rstrip().rstrip(",").strip()
    if value.lower() in _EMPTY_WORDS or value in _BLOCK_OPENERS:
        return ""
    if value.startswith("[") and value.endswith("]"):
        return _list_items(value[1:-1])
    return value


def _list_items(text: str) -> str:
    """Join a one-line list's items, unquoted, without empty words."""
    items = (i.strip().strip("'\"") for i in text.split(","))
    return ",".join(i for i in items if i.lower() not in _EMPTY_WORDS)


def _config_key(name: str) -> str:
    """Normalize a config key: upper case, ``.`` and ``-`` as ``_``."""
    return re.sub(r"[.-]", "_", name).upper()


def _check_key(findings: _Findings, label: str, name: str, value: str) -> None:
    """Check one config value under its key, if the key is listed."""
    key = _config_key(name)
    if is_service_variable(key):
        findings.value(label, value, url_name=key.endswith(_URL_SUFFIXES))
    elif key in _CONFIG_KEYS:
        findings.value(label, value)


def _is_listed_key(name: str) -> bool:
    """Say whether a config key is read as a service address."""
    key = _config_key(name)
    return is_service_variable(key) or key in _CONFIG_KEYS


def _inline_value(line: str, start: int) -> str | None:
    """Read the value after a key inside a line.

    Scanned by index from ``start``, so the line is never copied per
    key, and a list is scanned only to the next bracket, so the work
    for a whole line stays linear.

    Returns
    -------
    str or None
        The quoted string, a one-line list as comma-separated items, or
        the bare token up to a delimiter; ``""`` when the line ends or a
        comment starts, for ``null``, ``None``, ``~`` and ``false``, and
        for a mapping or list opened at the end of the line, whose
        entries are checked on their own lines. ``None`` for a mapping
        on the line, a nested list, or an unclosed quote or list, which
        can't be read here.
    """
    index = start
    while index < len(line) and line[index].isspace():
        index += 1
    if index == len(line) or line[index] == "#":
        return ""
    first = line[index]
    if first in _BLOCK_OPENERS and _REST_BLANK.match(line, index + 1):
        return ""
    if first in "'\"":
        end = line.find(first, index + 1)
        return None if end == -1 else line[index + 1 : end]
    if first in "[(":
        closer = "]" if first == "[" else ")"
        found = _BRACKET.search(line, index + 1)
        if found is None or found.group(0) != closer:
            return None
        return _list_items(line[index + 1 : found.start()])
    if first == "{":
        return None
    match = _INLINE_TOKEN.match(line, index)
    token = "" if match is None else match.group(0)
    return "" if token.lower() in _EMPTY_WORDS else token


def _list_opened_at(line: str, index: int) -> str | None:
    """Return the closer of a list opened at the end of a line, if any.

    ``index`` is where the value after a key starts. A ``[`` or ``(``
    followed by nothing but whitespace and commas opens a list whose
    items are on the following lines.
    """
    while index < len(line) and line[index].isspace():
        index += 1
    if index == len(line) or not _REST_BLANK.match(line, index + 1):
        return None
    return _LIST_CLOSERS.get(line[index])


def _check_line(
    findings: _Findings, where: str, line: str
) -> tuple[str, str] | None:
    """Check every listed key and service URL on one config line.

    A comment line (``#``, ``;`` or ``//`` first) is skipped: no client
    reads it.

    Returns
    -------
    tuple of (str, str) or None
        The key and the closing bracket when a listed key opens a list
        at the end of the line, so the caller reads its items on the
        following lines; ``None`` otherwise. A mapping opened at the end
        of a line needs nothing: its entries have keys of their own.
    """
    if line.lstrip().startswith(_COMMENT_STARTS):
        return None
    findings.text(where, line, SERVICE_SCHEMES)
    opened: tuple[str, str] | None = None
    match = _CONFIG_KEY.match(line)
    if match is not None and _is_listed_key(match.group(1)):
        name = match.group(1)
        value = _config_value(line[match.end() :])
        if value:
            _check_key(findings, f"{where} ({name})", name, value)
        else:
            closer = _list_opened_at(line, match.end())
            opened = None if closer is None else (name, closer)
    for key in _KEY_IN_LINE.finditer(line):
        name = key.group(2)
        if not _is_listed_key(name):
            continue
        label = f"{where} ({name})"
        value = _inline_value(line, key.end())
        if value is None:
            findings.add(label, "holds a value that cannot be parsed")
        elif value:
            _check_key(findings, label, name, value)
        else:
            closer = _list_opened_at(line, key.end())
            opened = None if closer is None else (name, closer)
    return opened


def _check_list_line(
    findings: _Findings, where: str, line: str, opened: tuple[str, str]
) -> tuple[tuple[str, str] | None, bool]:
    """Read one line of a list opened at the end of an earlier line.

    The line's bare items, up to the list's closing bracket, are checked
    under the key that opened the list; blank and comment lines are
    skipped. A line that opens a mapping ends the reading: its entries
    have keys of their own, so the caller checks it as an ordinary
    line. A nested list can't be read here and fails closed.

    Returns
    -------
    tuple of (tuple of (str, str) or None, bool)
        The list still open after this line, or ``None`` once it closes
        or can't be read; and whether the line was consumed, ``False``
        when the caller should check it as an ordinary line.
    """
    name, closer = opened
    text = line.strip()
    if not text or text.startswith(_COMMENT_STARTS):
        return opened, True
    if text[0] == "{":
        return None, False
    label = f"{where} ({name})"
    end = text.find(closer)
    part = text if end == -1 else text[:end]
    if any(c in part for c in "[({"):
        findings.add(label, "holds a value that cannot be parsed")
        return None, True
    items = _list_items(_config_value(part))
    if items:
        _check_key(findings, label, name, items)
    return (opened if end == -1 else None), True


def _check_config(findings: _Findings, root: Path, rel: str) -> None:
    """Check one config file line by line.

    A list a listed key opens at the end of a line is read on the lines
    that follow until it closes; one that never closes fails closed.
    """
    path = root / rel
    opened: tuple[str, str] | None = None
    opened_at = ""
    try:
        with path.open(encoding="utf-8-sig", errors="strict") as handle:
            for number, line in enumerate(handle, start=1):
                where = f"config {rel} line {number}"
                if opened is not None:
                    opened, consumed = _check_list_line(
                        findings, where, line, opened
                    )
                    if consumed:
                        continue
                opened = _check_line(findings, where, line)
                if opened is not None:
                    opened_at = f"{where} ({opened[0]})"
        if opened is not None:
            findings.add(opened_at, "opens a list that never closes")
    except UnicodeDecodeError:
        findings.add(f"config {rel}", "cannot be read as UTF-8 text")
    except OSError as exc:
        findings.add(f"config {rel}", f"cannot be read: {exc.strerror or exc}")


def side_effects_gate(
    spec: BenchmarkSpec,
    tree_root: Path,
    environ: Mapping[str, str],
    *,
    container_hosts: Collection[str] = (),
) -> GateResult:
    """Refuse a run that would reach a service that isn't local.

    Run it on each tree before anything runs there: no reset, warmup,
    round or correctness check starts until it has passed or been
    waived. It only reads; it starts no process and resolves no name.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force: its ``env``, command argv and ``config``
        files are checked, and ``allow_remote_services`` waives the
        gate.
    tree_root : Path
        Root of the tree whose declared config files are read.
    environ : Mapping[str, str]
        The environment the benchmark, correctness check and reset
        will actually run with: ``benchmark_environment(spec,
        os.environ)``. Its variables are checked along with
        ``spec.env``; one whose value is the spec's is reported once,
        as the spec's.
    container_hosts : Collection of str, optional
        Hostnames of containers perfproof started for this run, which
        count as local. Compared case-insensitively.

    Returns
    -------
    GateResult
        Gate ``side_effects``. Passed when every service address found
        is local. Otherwise failed, or waived with the spec's opt-in,
        listing up to `MAX_FINDINGS` findings (where, and the host, or
        that the value could not be parsed or the file read) and
        counting the rest. Linear in the size of the environments,
        argv and config files.
    """
    containers = frozenset(h.lower() for h in container_hosts)
    findings = _Findings(containers)
    _check_environment(findings, "spec env", spec.env)
    rest = {k: v for k, v in environ.items() if spec.env.get(k) != v}
    _check_environment(findings, "environment", rest)
    _check_commands(findings, spec)
    files = 0
    for declared in spec.config:
        for rel, problem in _config_files(tree_root, declared):
            if problem:
                findings.add(f"config {rel}", problem)
            else:
                files += 1
                _check_config(findings, tree_root, rel)
    if not findings.items:
        return GateResult(
            gate="side_effects",
            status="passed",
            detail=(
                f"every service address is local: {len(spec.env)} spec "
                f"env and {len(rest)} other environment variables, the "
                f"commands and {files} config files checked"
            ),
        )
    more = findings.more
    detail = "; ".join(findings.items) + (f"; and {more} more" if more else "")
    if spec.allow_remote_services:
        return GateResult(
            gate="side_effects",
            status="waived",
            detail=f"allow_remote_services: {detail}",
        )
    return GateResult(gate="side_effects", status="failed", detail=detail)


def _tail(handle: IO[bytes]) -> tuple[str, bool]:
    """Return a stream's tail and whether it was cut."""
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - OUTPUT_TAIL_BYTES))
    data = handle.read()
    return data.decode("utf-8", errors="replace"), size > OUTPUT_TAIL_BYTES


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill a reset and everything it started in its session."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def benchmark_environment(
    spec: BenchmarkSpec, process_env: Mapping[str, str]
) -> dict[str, str]:
    """Build the environment every command of a comparison runs with.

    Per "Side effects" in ``docs/method.md``: the variables of
    `BASE_ENV_VARIABLES` and every ``LC_*`` variable present in
    ``process_env``, overlaid with ``spec.env``. Nothing else from
    perfproof's own environment (an API base URL, a cloud token)
    reaches the benchmark. perfproof's own virtual environment (the
    one ``uvx`` runs it in) is taken off ``PATH``, so ``python`` and
    other commands resolve as in the user's shell, not to perfproof's
    interpreter; the user's own virtual environment stays.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force; its ``env`` wins over the base variables.
    process_env : Mapping[str, str]
        perfproof's own environment, usually ``os.environ``.

    Returns
    -------
    dict of str to str
        The environment for the benchmark, correctness check and reset,
        and the one `side_effects_gate` checks.
    """
    base = {
        name: value
        for name, value in process_env.items()
        if name in BASE_ENV_VARIABLES or name.startswith("LC_")
    }
    if "PATH" in base:
        base["PATH"] = without_own_environment(base["PATH"], process_env)
    return {**base, **spec.env}


_TOOL_ENV_PARTS: Final = frozenset({"archive-v0", "environments-v2"})


def _own_prefix(process_env: Mapping[str, str]) -> Path | None:
    """Return perfproof's own environment when it is a uv tool one.

    That is ``sys.prefix`` only when it positively sits in uv's cache
    (``uvx``'s ephemeral environments) or its tool directory
    (``uv tool install``), by the usual directory names or by
    ``UV_CACHE_DIR`` and ``UV_TOOL_DIR`` in ``process_env``. A
    project's virtual environment that holds perfproof, activated or
    not, and any prefix that cannot be told is the user's: ``None``.
    """
    if sys.prefix == sys.base_prefix:
        return None
    own = Path(sys.prefix).resolve()
    if _TOOL_ENV_PARTS & set(own.parts) or (
        "tools" in own.parts and "uv" in own.parts
    ):
        return own
    for name in ("UV_CACHE_DIR", "UV_TOOL_DIR"):
        root = process_env.get(name)
        if root and own.is_relative_to(Path(root).resolve()):
            return own
    return None


def without_own_environment(path: str, process_env: Mapping[str, str]) -> str:
    """Drop perfproof's own environment's ``bin`` from ``path``.

    Parameters
    ----------
    path : str
        A ``PATH`` value.
    process_env : Mapping of str to str
        perfproof's own environment, read for ``UV_CACHE_DIR`` and
        ``UV_TOOL_DIR``.

    Returns
    -------
    str
        ``path`` without the entries inside perfproof's own virtual
        environment; unchanged unless perfproof runs from a uv tool or
        uvx environment.

    Notes
    -----
    ``uvx`` puts its tool environment first on ``PATH``, so a spec
    command ``python`` would run perfproof's interpreter, not the
    project's. Commands resolve as they would in the user's shell:
    only entries inside perfproof's own uv tool environment
    (`_own_prefix`) go; the user's virtual environment, perfproof
    installed in it or not, and everything else stay.
    """
    own = _own_prefix(process_env)
    if own is None:
        return path
    kept = [
        entry
        for entry in path.split(os.pathsep)
        if not entry or not Path(entry).resolve().is_relative_to(own)
    ]
    return os.pathsep.join(kept)


def points_at_tree(exc: OSError, *names: str) -> bool:
    """Say whether a launch error is the tree's fault, not the host's.

    Only an error in `PROGRAM_ERRNOS` whose ``filename`` is the program
    or the working directory points at the tree. Anything else (EMFILE,
    EAGAIN, ENOMEM, E2BIG, a failed log file) is a fault of the host or
    of perfproof's own process.

    Parameters
    ----------
    exc : OSError
        The error from starting the process.
    *names : str
        The program and working directory, as given to ``Popen``.

    Returns
    -------
    bool
        True if the error names one of ``names`` with a program errno.
    """
    # A failed chdir reports the object given as ``cwd=``, often a
    # `Path`, not the string in ``names``.
    filename: object = exc.filename
    if isinstance(filename, os.PathLike):
        filename = os.fspath(cast("os.PathLike[str]", filename))
    return exc.errno in PROGRAM_ERRNOS and filename in names


def run_reset(
    spec: BenchmarkSpec, tree_root: Path, process_env: Mapping[str, str]
) -> None:
    """Run the spec's reset command before one execution.

    Call it before every execution, warmup included, after
    `side_effects_gate` has passed or been waived. The reset runs
    without a shell in ``tree_root / spec.reset.cwd``, with
    ``benchmark_environment(spec, process_env)``, the environment the
    benchmark runs with and the gate checked, so it restores the same
    services the benchmark uses. Its output goes to temporary files,
    not memory, and only the tails are kept.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force; nothing runs when ``spec.reset`` is
        ``None``.
    tree_root : Path
        Root of the tree the next execution runs in.
    process_env : Mapping[str, str]
        perfproof's own environment, usually ``os.environ``; only its
        base variables are passed on.

    Raises
    ------
    ResetError
        If the reset cannot be started (exit status 127), exits
        non-zero, is killed by a signal, or runs past
        ``spec.reset.timeout_seconds`` (its whole session is then
        killed). The error's ``gate`` is a failed `side_effects` gate
        with the argv, exit status and the last `OUTPUT_TAIL_BYTES` of
        each stream.
    OSError
        If perfproof's own process cannot start it (descriptors,
        processes or memory exhausted, an oversized environment): a
        host fault, blamed on neither tree, left to the caller.
    """
    reset = spec.reset
    if reset is None:
        return
    argv = list(reset.command)
    env = benchmark_environment(spec, process_env)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        timed_out = False
        try:
            process = subprocess.Popen(
                argv,
                cwd=tree_root / reset.cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        except OSError as exc:
            if not points_at_tree(exc, argv[0], str(tree_root / reset.cwd)):
                raise
            err.write(str(exc).encode("utf-8", errors="replace"))
            code = 127
            detail = (
                f"reset command could not be started: {exc.strerror or exc}"
            )
        else:
            try:
                code = process.wait(timeout=reset.timeout_seconds)
            except subprocess.TimeoutExpired:
                _kill_group(process)
                code = process.wait()
                timed_out = True
            if timed_out:
                detail = (
                    f"reset command timed out after {reset.timeout_seconds} "
                    "s and was killed"
                )
            elif code < 0:
                detail = f"reset command was killed by signal {-code}"
            else:
                detail = f"reset command exited {code}"
        if code == 0 and not timed_out:
            return
        stdout, cut_out = _tail(out)
        stderr, cut_err = _tail(err)
    output = CommandOutput(
        command=reset.command,
        exit_code=code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        truncated=cut_out or cut_err,
    )
    raise ResetError(
        GateResult(
            gate="side_effects",
            status="failed",
            detail=f"{detail}; the run stops",
            output=output,
        )
    )
