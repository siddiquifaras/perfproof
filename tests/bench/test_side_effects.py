import errno
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from perfproof.bench.side_effects import (
    BASE_ENV_VARIABLES,
    MAX_FINDINGS,
    OUTPUT_TAIL_BYTES,
    ResetError,
    benchmark_environment,
    is_service_variable,
    run_reset,
    side_effects_gate,
)
from perfproof.schema import (
    BenchmarkSpec,
    Diagnostics,
    MachineFingerprint,
    RunRecord,
    TreeEnvironment,
    Verdict,
)

MEMBERSHIP = Path(__file__).resolve().parents[2] / "examples" / "membership"
PYTHON = sys.executable


def make_spec(**update: Any) -> BenchmarkSpec:
    fields: dict[str, Any] = {
        "name": "orders",
        "command": ["python", "bench.py"],
        "metric": {
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": ["python", "-m", "pytest", "-q"],
            "timeout_seconds": 60.0,
        },
        "warmup": 1,
        "budget": {"max_rounds": 20, "max_seconds": 60.0},
        "looks": {"n0": 10, "k": 10, "max_looks": 2},
        "timeout_seconds": 30.0,
        "harness": ["bench.py"],
    }
    fields.update(update)
    return BenchmarkSpec.model_validate(fields)


def gate_for(
    value: str,
    name: str = "DATABASE_URL",
    *,
    containers: tuple[str, ...] = (),
) -> tuple[str, str]:
    spec = make_spec(env={name: value})
    result = side_effects_gate(spec, Path("."), {}, container_hosts=containers)
    return result.status, result.detail


@pytest.fixture
def no_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("a process was started")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)


# Variable names


@pytest.mark.parametrize(
    "name",
    [
        "DATABASE_URL",
        "REDIS_URL",
        "database_url",
        "DB_HOST",
        "REDIS_HOSTS",
        "API_URL",
        "MONGO_URI",
        "PG_DSN",
        "PG_HOSTADDR",
        "CACHE_ADDR",
        "BROKER_ADDRESS",
        "S3_ENDPOINT",
        "SMTP_SERVER",
        "KAFKA_BOOTSTRAP_SERVERS",
        "PGHOST",
        "PGHOSTADDR",
        "DOCKER_HOST",
    ],
)
def test_service_variable_names(name: str) -> None:
    assert is_service_variable(name)
    status, detail = gate_for("db.example.com", name)
    assert status == "failed"
    assert detail == (
        f"spec env {name} points at db.example.com, which is not local"
    )


@pytest.mark.parametrize(
    "name", ["DB_PORT", "PATH", "HOSTNAME", "URL_PREFIX", "DATABASE"]
)
def test_other_names_hold_no_bare_host(name: str) -> None:
    assert not is_service_variable(name)
    assert gate_for("db.example.com", name)[0] == "passed"


def test_service_scheme_found_in_any_variable() -> None:
    status, detail = gate_for(
        "--db postgresql://db.example.com/orders", "BENCH_ARGS"
    )
    assert status == "failed"
    assert "spec env BENCH_ARGS points at db.example.com" in detail


@pytest.mark.parametrize(
    ("name", "value", "host"),
    [
        ("OPENAI_API_BASE", "https://api.openai.com/v1", "api.openai.com"),
        ("HOMEPAGE", "https://example.com/docs", "example.com"),
        ("STRIPE_BASE", "see https://api.stripe.com/v1", "api.stripe.com"),
        ("EVENTS", "wss://stream.example.com/feed", "stream.example.com"),
    ],
)
def test_web_urls_in_any_variable_fail(
    name: str, value: str, host: str
) -> None:
    status, detail = gate_for(value, name)
    assert status == "failed"
    assert detail == f"spec env {name} points at {host}, which is not local"


@pytest.mark.parametrize(
    "value", ["http://localhost:8000/health", "https://127.0.0.1:8443/v1"]
)
def test_local_web_urls_pass(value: str) -> None:
    assert gate_for(value, "OPENAI_API_BASE")[0] == "passed"
    spec = make_spec(command=["python", "bench.py", "--url", value])
    assert side_effects_gate(spec, Path("."), {}).status == "passed"


@pytest.mark.parametrize(
    "value",
    [
        # libpq ends the user info only at "/": the host is evil.com.
        "postgres://localhost?@evil.com/db",
        "postgres://localhost:5432?@evil.com/db",
        # A web authority ends at "#" or "?", before the "@".
        "https://evil.com#@localhost/",
        "https://evil.com?@localhost/",
        # The host follows the last "@" of a web authority.
        "https://localhost@evil.com/",
        "https://u@localhost:1@evil.com/",
    ],
)
def test_urls_are_read_as_their_clients_read_them(value: str) -> None:
    assert gate_for(value) == (
        "failed",
        "spec env DATABASE_URL points at evil.com, which is not local",
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost/a@b",
        "http://localhost:8000/?next=u@x.com",
        "https://evil.com:443@localhost/",
        "https://localhost/#frag@x",
    ],
)
def test_at_signs_after_a_web_authority_pass(value: str) -> None:
    assert gate_for(value)[0] == "passed"


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://user@localhost?@evil.com/db",
        "mysql://root:pw@localhost?@evil.com/orders",
        "mysql+pymysql://root@localhost:3306?@evil.com/orders",
        "postgresql://localhost/db?x=1@evil.com",
        "mysql://localhost:3306/db?u=@evil.com",
        "postgresql://a@evil.com@localhost/db",
        "http://local host/",
        "https://localhost'@evil.invalid/",
        'https://localhost"@evil.invalid/',
        "https://local<host>/",
        "https://localhost`@evil.invalid/",
        "https://local\thost/",
        "https:evil.com",
        "https:/evil.com",
        "https:///evil.com",
        "https:\\\\evil.com",
        "https://\\evil.com",
        "http://localhost\\@evil.invalid/",
        "http://evil.com\\@localhost/",
        "https://localhost\\evil.invalid/",
        "https://",
    ],
)
def test_authority_tricks_fail_closed(value: str) -> None:
    status, detail = gate_for(value)
    assert status == "failed"
    assert detail == (
        "spec env DATABASE_URL cannot be parsed as a service address"
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_URL", "postgresql:///orders?service=prod"),
        ("DATABASE_URL", "postgresql://localhost/orders?service=prod"),
        ("PG_DSN", "service=prod dbname=orders"),
        ("PG_DSN", "host=localhost service='prod'"),
    ],
)
def test_service_keys_fail_closed(name: str, value: str) -> None:
    assert gate_for(value, name) == (
        "failed",
        f"spec env {name} names a connection service the guard doesn't read",
    )


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("PGSERVICEFILE", "names a service file the guard doesn't read"),
        (
            "PGSYSCONFDIR",
            "names a service file directory the guard doesn't read",
        ),
    ],
)
def test_service_file_variables_fail_closed(name: str, problem: str) -> None:
    assert gate_for("/home/bench/pg", name) == (
        "failed",
        f"spec env {name} {problem}",
    )


# Local and non-local hosts


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://user:pw@localhost:5432/orders",
        "postgresql://LOCALHOST/orders",
        "postgres://127.0.0.1:5432/orders",
        "redis://127.0.0.2:6379/0",
        "mysql://root@0.0.0.0/orders",
        "localhost",
        "localhost:6379",
        "127.0.0.1",
        "localhost:9092,127.0.0.1:9093",
        "http://localhost:9200,http://127.0.0.1:9201",
        "postgresql://localhost:5432,127.0.0.1:5433/orders",
        "jdbc:postgresql://localhost/orders",
        "host=localhost port=5432 dbname=orders",
        "host='127.0.0.1' dbname=orders",
        r"host='local\host' dbname='it\'s'",
        "dbname=orders user=bench",
        "Server=tcp:localhost,1433;Database=orders",
        "Server=(local);Database=orders",
        "Host=localhost;Port=5432;Database=orders",
        "user:pw@tcp(127.0.0.1:3306)/orders",
        "sqlite:///data/orders.db",
        "sqlite:orders.db",
        "file:///srv/data.db",
        "postgresql:///orders",
        "",
    ],
)
def test_local_hosts_pass(value: str) -> None:
    assert gate_for(value) == (
        "passed",
        "every service address is local: 1 spec env and 0 other environment "
        "variables, the commands and 0 config files checked",
    )


@pytest.mark.parametrize(
    ("value", "host"),
    [
        ("postgresql://db.example.com:5432/orders", "db.example.com"),
        ("postgresql://user:pw@10.0.0.5/orders", "10.0.0.5"),
        ("redis://192.168.1.20:6379/0", "192.168.1.20"),
        ("https://api.anthropic.com", "api.anthropic.com"),
        ("mongodb+srv://cluster0.example.net/orders", "cluster0.example.net"),
        (
            "postgresql://localhost:5432,db.example.com:5433/o",
            "db.example.com",
        ),
        ("http://localhost:9200,http://es.example.com:9200", "es.example.com"),
        ("postgresql:///orders?host=db.example.com", "db.example.com"),
        ("postgresql://localhost/orders?hostaddr=10.1.2.3", "10.1.2.3"),
        ("jdbc:mysql://db.example.com:3306/orders", "db.example.com"),
        ("host=db.example.com port=5432 dbname=orders", "db.example.com"),
        ("host=localhost,db.example.com dbname=orders", "db.example.com"),
        ("Server=tcp:sql.example.com,1433;Database=o", "sql.example.com"),
        ("Data Source=sql.example.com;Initial Catalog=o", "sql.example.com"),
        ("user:pw@tcp(db.example.com:3306)/orders", "db.example.com"),
        ("db.example.com:5432", "db.example.com"),
        ("kafka-1:9092,localhost:9093", "kafka-1"),
        ("tcp://docker.example.com:2376", "docker.example.com"),
        ("localhost.", "localhost."),
        ("localhost.example.com", "localhost.example.com"),
        ("host.docker.internal", "host.docker.internal"),
        ("127.1", "127.1"),
        ("db:5432", "db"),
    ],
)
def test_non_local_hosts_fail(value: str, host: str) -> None:
    status, detail = gate_for(value)
    assert status == "failed"
    assert f"DATABASE_URL points at {host}, which is not local" in detail


@pytest.mark.parametrize(
    "value",
    [
        "::1",
        "[::1]:5432",
        "0:0:0:0:0:0:0:1",
        "::",
        "::ffff:127.0.0.1",
        "[::ffff:127.0.0.1]:6379",
        "postgresql://[::1]:5432/orders",
        "redis://[::1]/0",
        "host=::1 dbname=orders",
    ],
)
def test_ipv6_loopback_passes(value: str) -> None:
    assert gate_for(value)[0] == "passed"


@pytest.mark.parametrize(
    ("value", "host"),
    [
        ("2001:db8::1", "2001:db8::1"),
        ("[2001:db8::1]:5432", "2001:db8::1"),
        ("postgresql://[2001:db8::1]:5432/orders", "2001:db8::1"),
        ("fe80::1%lo0", "fe80::1%lo0"),
        ("::ffff:10.0.0.5", "::ffff:10.0.0.5"),
    ],
)
def test_ipv6_non_local_fails(value: str, host: str) -> None:
    status, detail = gate_for(value)
    assert status == "failed"
    assert f"points at {host}, which is not local" in detail


@pytest.mark.parametrize(
    "value",
    [
        "/var/run/postgresql",
        "@abstract-socket",
        "postgresql:///orders?host=/var/run/postgresql",
        "postgresql://%2Fvar%2Frun%2Fpostgresql/orders",
        "unix:///tmp/redis.sock",
        "unix:/tmp/redis.sock",
        "redis+unix:///tmp/redis.sock",
        "http+unix://%2Fvar%2Frun%2Fdocker.sock/info",
        "user@unix(/tmp/mysql.sock)/orders",
        "mysql://root@localhost/orders?unix_socket=/tmp/mysql.sock",
        "host=/var/run/postgresql dbname=orders",
    ],
)
def test_unix_sockets_pass(value: str) -> None:
    assert gate_for(value)[0] == "passed"


def test_container_started_for_the_run_is_local() -> None:
    value = "postgresql://postgres@orders-db:5432/orders"
    assert gate_for(value)[0] == "failed"
    assert gate_for(value, containers=("Orders-DB",))[0] == "passed"
    assert gate_for(value, containers=("other-db",))[0] == "failed"


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://localhost#@db.example.com/orders",
        "postgres://u:pa/ss@db.example.com/orders",
        "postgresql://${DB_HOST}/orders",
        "${DB_HOST}",
        "db.example.com:http",
        "[::1",
        "[10.0.0.1]:5432",
        "host='db.example.com",
        "https:example.com",
    ],
)
def test_unparseable_values_fail_closed(value: str) -> None:
    status, detail = gate_for(value)
    assert status == "failed"
    assert "DATABASE_URL" in detail
    assert detail.startswith("spec env DATABASE_URL ")
    assert "cannot be parsed" in detail


def test_values_are_never_echoed() -> None:
    marker = "do-not-echo"
    for value in (
        f"postgresql://bench:{marker}@db.example.com/orders",
        f"postgres://bench:{marker}/x@db.example.com/orders",
        f"host=db.example.com password={marker}",
    ):
        status, detail = gate_for(value)
        assert status == "failed"
        assert marker not in detail


def test_connection_service_fails_closed() -> None:
    status, detail = gate_for("orders", "PGSERVICE")
    assert status == "failed"
    assert detail == (
        "spec env PGSERVICE names a connection service the guard doesn't read"
    )


# Where the guard looks


BASE_PART = {
    "PATH": os.environ["PATH"],
    "HOME": "/home/bench",
    "USER": "bench",
    "LOGNAME": "bench",
    "SHELL": "/bin/sh",
    "TMPDIR": "/home/bench/scratch",
    "TERM": "xterm",
    "TZ": "UTC",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LC_CTYPE": "C.UTF-8",
}
PROCESS_ENV = {
    **BASE_PART,
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "GITHUB_API_URL": "https://api.github.com",
    "GITHUB_SERVER_URL": "https://github.com",
    "DATABASE_URL": "postgresql://db.example.com/orders",
    "CUDA_VISIBLE_DEVICES": "0",
    "AWS_SECRET_ACCESS_KEY": "not-a-real-key",
    "PYTHONPATH": "/elsewhere",
}


def test_benchmark_environment_is_the_base_and_the_spec() -> None:
    spec = make_spec(env={"SPEC_VALUE": "1", "TZ": "Europe/London"})
    env = benchmark_environment(spec, PROCESS_ENV)
    assert env == {**BASE_PART, "SPEC_VALUE": "1", "TZ": "Europe/London"}
    assert set(BASE_ENV_VARIABLES) <= set(env)
    assert benchmark_environment(make_spec(), {}) == {}


def test_perfproof_own_service_addresses_never_reach_the_gate() -> None:
    spec = make_spec()
    env = benchmark_environment(spec, PROCESS_ENV)
    result = side_effects_gate(spec, Path("."), env)
    assert result.status == "passed"
    assert result.detail == (
        "every service address is local: 0 spec env and 11 other "
        "environment variables, the commands and 0 config files checked"
    )


def test_non_local_spec_env_still_fails_the_gate() -> None:
    spec = make_spec(env={"DATABASE_URL": "postgresql://db.example.com/o"})
    env = benchmark_environment(spec, {**BASE_PART, "DATABASE_URL": ""})
    result = side_effects_gate(spec, Path("."), env)
    assert result.status == "failed"
    assert result.detail == (
        "spec env DATABASE_URL points at db.example.com, which is not local"
    )


def test_given_environment_is_checked_beyond_the_spec() -> None:
    spec = make_spec(env={"DATABASE_URL": "postgresql://localhost/orders"})
    environ = {
        "DATABASE_URL": "postgresql://db.example.com/orders",
        "HOME": "/home/bench",
    }
    result = side_effects_gate(spec, Path("."), environ)
    assert result.status == "failed"
    assert result.detail == (
        "environment DATABASE_URL points at db.example.com, which is not local"
    )
    local = {"REDIS_URL": "redis://localhost:6379/0"}
    assert side_effects_gate(spec, Path("."), local).status == "passed"


def test_command_arguments_are_checked() -> None:
    spec = make_spec(
        command=["python", "bench.py", "--db=postgresql://db.example.com/o"],
        correctness={
            "command": ["psql", "host=pg.example.com dbname=orders"],
            "timeout_seconds": 60.0,
        },
        reset={
            "command": ["psql", "redis://cache.example.com:6379/0"],
            "timeout_seconds": 10.0,
        },
    )
    result = side_effects_gate(spec, Path("."), {})
    assert result.status == "failed"
    assert result.detail.split("; ") == [
        "command argument 2 points at db.example.com, which is not local",
        "correctness command argument 1 points at pg.example.com, which "
        "is not local",
        "reset command argument 1 points at cache.example.com, which is "
        "not local",
    ]


def test_setup_commands_are_checked_too() -> None:
    spec = make_spec(
        setup={
            "commands": [
                ["uv", "sync"],
                ["pip", "install", "--index-url=https://pkgs.example.com"],
                ["psql", "redis://cache.example.com:6379/0"],
            ],
            "timeout_seconds": 60.0,
        },
    )
    result = side_effects_gate(spec, Path("."), {})
    assert result.status == "failed"
    assert result.detail.split("; ") == [
        "setup command 1 argument 2 points at pkgs.example.com, which is "
        "not local",
        "setup command 2 argument 1 points at cache.example.com, which is "
        "not local",
    ]


def test_sql_in_a_command_is_not_a_dsn() -> None:
    spec = make_spec(
        reset={
            "command": [
                "psql",
                "host=localhost dbname=orders",
                "-c",
                "UPDATE orders SET held = false WHERE (id = 1)",
            ],
            "timeout_seconds": 10.0,
        },
    )
    assert side_effects_gate(spec, Path("."), {}).status == "passed"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("rel", "text", "where"),
    [
        (
            ".env",
            "# local overrides\nexport DATABASE_URL=postgresql://"
            "db.example.com/orders\n",
            "config .env line 2 (DATABASE_URL)",
        ),
        (
            "settings.yaml",
            "database:\n  host: db.example.com  # primary\n  port: 5432\n",
            "config settings.yaml line 2 (host)",
        ),
        (
            "settings.json",
            '{\n  "redis_url": "redis://cache.example.com:6379/0",\n}\n',
            "config settings.json line 2",
        ),
        (
            "app.toml",
            '[kafka]\nbootstrap-servers = ["localhost:9092", '
            '"kafka.example.com:9092"]\n',
            "config app.toml line 2 (bootstrap-servers)",
        ),
        (
            "app.ini",
            "[db]\nbackup = mysql://db.example.com/orders\n",
            "config app.ini line 2",
        ),
        (
            "settings.py",
            "DATABASES = {'default': {\n    'HOST': 'db.example.com',\n}}\n",
            "config settings.py line 2 (HOST)",
        ),
    ],
)
def test_config_files_are_read(
    tmp_path: Path, rel: str, text: str, where: str
) -> None:
    write(tmp_path, rel, text)
    spec = make_spec(config=[rel])
    result = side_effects_gate(spec, tmp_path, {})
    assert result.status == "failed"
    assert where in result.detail
    assert "example.com, which is not local" in result.detail


def test_local_config_passes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "conf/app.yaml",
        "database:\n  host: localhost\n  port: 5432\n"
        "cache_url: redis://127.0.0.1:6379/0\n"
        'broker_url: "amqp://localhost:5672" # local broker\n'
        "hosts: ['localhost', '127.0.0.1']\n"
        "socket: /var/run/postgresql\n"
        "# docs: https://example.com/setup\n",
    )
    write(tmp_path, "conf/nested/.env", "PGHOST=/var/run/postgresql\n")
    write(tmp_path, "pytest.ini", "[pytest]\naddopts = -q\n")
    spec = make_spec(config=["conf", "pytest.ini"])
    result = side_effects_gate(spec, tmp_path, {})
    assert result.status == "passed"
    assert result.detail.endswith("the commands and 3 config files checked")


def test_config_in_a_directory_is_read(tmp_path: Path) -> None:
    write(tmp_path, "conf/a/b/db.env", "DB_HOST=db.example.com\n")
    result = side_effects_gate(make_spec(config=["conf"]), tmp_path, {})
    assert result.status == "failed"
    assert result.detail == (
        "config conf/a/b/db.env line 1 (DB_HOST) points at db.example.com, "
        "which is not local"
    )


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        ("missing", "config app.yaml is missing, so it cannot be checked"),
        ("binary", "config app.yaml cannot be read as UTF-8 text"),
        ("template", "config app.yaml line 1 (host) holds a host that "),
        (
            "computed",
            "config app.yaml line 1 (HOST) holds a host that cannot be",
        ),
        ("nested", "config app.yaml line 1 (host) holds a host that cannot"),
    ],
)
def test_unreadable_config_fails_closed(
    tmp_path: Path, setup: str, problem: str
) -> None:
    if setup == "binary":
        (tmp_path / "app.yaml").write_bytes(b"host: \xff\xfe\n")
    elif setup == "template":
        write(tmp_path, "app.yaml", "host: ${DB_HOST}\n")
    elif setup == "computed":
        text = "'HOST': os.environ.get('DB_HOST', 'localhost'),\n"
        write(tmp_path, "app.yaml", text)
    elif setup == "nested":
        write(tmp_path, "app.yaml", '"host": {"primary": "db"}\n')
    result = side_effects_gate(make_spec(config=["app.yaml"]), tmp_path, {})
    assert result.status == "failed"
    assert result.detail.startswith(problem)


def test_findings_beyond_the_limit_are_counted() -> None:
    env = {f"DB{i:02d}_HOST": f"db{i:02d}.example.com" for i in range(25)}
    result = side_effects_gate(make_spec(env=env), Path("."), {})
    assert result.status == "failed"
    parts = result.detail.split("; ")
    assert len(parts) == MAX_FINDINGS + 1
    assert parts[-1] == f"and {25 - MAX_FINDINGS} more"


def test_many_findings_are_counted_once_each(tmp_path: Path) -> None:
    lines = [f"DB{i}_HOST=db{i}.example.com\n" for i in range(5000)]
    write(tmp_path, "hosts.env", "".join(lines))
    spec = make_spec(config=["hosts.env"])
    result = side_effects_gate(spec, tmp_path, {})
    parts = result.detail.split("; ")
    assert len(parts) == MAX_FINDINGS + 1
    assert parts[0] == (
        "config hosts.env line 1 (DB0_HOST) points at db0.example.com, "
        "which is not local"
    )
    assert parts[-1] == f"and {5000 - MAX_FINDINGS} more"


# Values inside a line and NAME=value arguments


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ('{"host": "evil.com", "port": 5432}\n', "host"),
        ("- host: evil.com\n", "host"),
        ("db: {host: evil.com, port: 5432}\n", "host"),
        ("[{'name': 'main', 'HOST': 'evil.com'}]\n", "HOST"),
        ('{"hosts": ["localhost", "evil.com"]}\n', "hosts"),
        ('{"db": {"redis_url": "redis://evil.com:6379"}}\n', "redis_url"),
        ('{"api_url": "https://evil.com/v1"}\n', "api_url"),
    ],
)
def test_listed_keys_inside_a_line_are_read(
    tmp_path: Path, text: str, key: str
) -> None:
    write(tmp_path, "app.json", text)
    result = side_effects_gate(make_spec(config=["app.json"]), tmp_path, {})
    assert result.status == "failed"
    assert (
        f"config app.json line 1 ({key}) points at evil.com, which is not "
        "local"
    ) in result.detail.split("; ")


@pytest.mark.parametrize(
    "text",
    [
        '{"host": "localhost", "port": 5432}\n',
        "- host: 127.0.0.1\n",
        "db: {host: localhost, port: 5432}\n",
        '{"hosts": ["localhost", "::1"]}\n',
        '{"homepage": "https://example.com", "docs": "https://x.org"}\n',
    ],
)
def test_local_values_inside_a_line_pass(tmp_path: Path, text: str) -> None:
    write(tmp_path, "app.json", text)
    result = side_effects_gate(make_spec(config=["app.json"]), tmp_path, {})
    assert result.status == "passed"


@pytest.mark.parametrize(
    "text", ['{"host": "evil.com\n', '{"hosts": ["evil.com",\n']
)
def test_unclosed_values_inside_a_line_fail_closed(
    tmp_path: Path, text: str
) -> None:
    write(tmp_path, "app.json", text)
    result = side_effects_gate(make_spec(config=["app.json"]), tmp_path, {})
    assert result.status == "failed"
    assert "holds a value that cannot be parsed" in result.detail


@pytest.mark.parametrize(
    ("argv", "problem"),
    [
        (
            ["env", "PGHOST=evil.com", "python", "bench.py"],
            "command argument 1 points at evil.com, which is not local",
        ),
        (
            ["python", "bench.py", "--db-host=evil.com"],
            "command argument 2 points at evil.com, which is not local",
        ),
        (
            ["python", "bench.py", "--host=evil.com"],
            "command argument 2 points at evil.com, which is not local",
        ),
        (
            ["env", "PGSERVICE=prod", "python", "bench.py"],
            "command argument 1 names a connection service the guard "
            "doesn't read",
        ),
        (
            ["curl", "https://api.stripe.com/v1/charges"],
            "command argument 1 points at api.stripe.com, which is not local",
        ),
    ],
)
def test_assignments_and_web_urls_in_argv_fail(
    argv: list[str], problem: str
) -> None:
    result = side_effects_gate(make_spec(command=argv), Path("."), {})
    assert result.status == "failed"
    assert result.detail == problem


def test_local_assignments_in_argv_pass() -> None:
    argv = ["env", "PGHOST=localhost", "MODE=fast", "python", "bench.py"]
    result = side_effects_gate(make_spec(command=argv), Path("."), {})
    assert result.status == "passed"


@pytest.mark.parametrize(
    "text",
    [
        "https://localhost'@evil.invalid/",
        "--url=https://localhost'@evil.invalid/",
        "fetch('https://localhost'@evil.invalid/')",
        "curl https:evil.invalid",
        "curl https:/evil.invalid",
        "see <https://local<host>/>",
    ],
)
def test_urls_in_free_text_are_whole_tokens(text: str) -> None:
    for where, spec in (
        ("command argument 1", make_spec(command=["python", text])),
        ("spec env NOTES", make_spec(env={"NOTES": text})),
    ):
        result = side_effects_gate(spec, Path("."), {})
        assert result.status == "failed"
        assert (
            result.detail == f"{where} cannot be parsed as a service address"
        )


@pytest.mark.parametrize(
    ("text", "host"),
    [
        ("curl 'https://evil.invalid/v1' -d x", "evil.invalid"),
        ('url="https://evil.invalid/a b"', "evil.invalid"),
        ("(see https://evil.invalid).", "evil.invalid"),
        ("https://localhost/?next=https://x.invalid", None),
        ("https://localhost/a@b, http://127.0.0.1/?u=a@b.c", None),
    ],
)
def test_url_tokens_in_free_text(text: str, host: str | None) -> None:
    result = side_effects_gate(
        make_spec(command=["sh", "-c", text]), Path("."), {}
    )
    if host is None:
        assert result.status == "passed"
    else:
        assert result.detail == (
            f"command argument 2 points at {host}, which is not local"
        )


@pytest.mark.parametrize(
    "argv",
    [
        ["psql", "service=prod"],
        ["psql", "service=prod dbname=orders"],
        ["psql", "dbname=orders service = prod"],
    ],
)
def test_service_in_an_argv_dsn_fails_closed(argv: list[str]) -> None:
    result = side_effects_gate(make_spec(command=argv), Path("."), {})
    assert result.detail == (
        "command argument 1 names a connection service the guard doesn't read"
    )


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("endpoint: postgres://db.example.com/x\n", "endpoint"),
        ("host: db.example.com  # primary\n", "host"),
        ("endpoint: https://api.example.com\n", "endpoint"),
        ("servers: [https://otel.example.com:4318]\n", "servers"),
        ("address: https://api.openai.com/v1\n", "address"),
        ("address: 10.0.0.5:8080\n", "address"),
        ("dsn: host=db.example.com dbname=x\n", "dsn"),
    ],
)
def test_bare_address_keys_are_listed(
    tmp_path: Path, text: str, key: str
) -> None:
    write(tmp_path, "app.yaml", text)
    result = side_effects_gate(make_spec(config=["app.yaml"]), tmp_path, {})
    assert result.status == "failed"
    assert f"config app.yaml line 1 ({key}) points at " in result.detail


def test_byte_order_mark_does_not_hide_line_one(tmp_path: Path) -> None:
    (tmp_path / "app.env").write_bytes("﻿DB_HOST=db.example.com\n".encode())
    result = side_effects_gate(make_spec(config=["app.env"]), tmp_path, {})
    assert result.detail == (
        "config app.env line 1 (DB_HOST) points at db.example.com, which is "
        "not local"
    )


BACKSLASH_URL = "http://localhost\\@evil.invalid/"


def test_backslash_before_the_host_is_refused_everywhere(
    tmp_path: Path,
) -> None:
    unparseable = "cannot be parsed as a service address"
    assert gate_for(BACKSLASH_URL, "API_URL") == (
        "failed",
        f"spec env API_URL {unparseable}",
    )
    assert gate_for(f"see {BACKSLASH_URL}", "NOTES") == (
        "failed",
        f"spec env NOTES {unparseable}",
    )
    argv = make_spec(command=["curl", BACKSLASH_URL])
    assert side_effects_gate(argv, Path("."), {}).detail == (
        f"command argument 1 {unparseable}"
    )
    write(tmp_path, "app.yaml", f"api_url: {BACKSLASH_URL}\n")
    config = make_spec(config=["app.yaml"])
    assert side_effects_gate(config, tmp_path, {}).detail == (
        f"config app.yaml line 1 (api_url) {unparseable}"
    )


NETBOX_SHAPED = """\
ALLOWED_HOSTS = ['netbox.example.com']
DATABASE = {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': 'netbox',
    'USER': '',
    'PASSWORD': '',
    'HOST': 'localhost',
    'PORT': '',
    'CONN_MAX_AGE': 300,
}
REDIS = {
    'tasks': {
        'HOST': 'localhost',
        'PORT': 6379,
        'SSL': False,
    },
}
EMAIL = {
    'SERVER': 'localhost',
    'PORT': 25,
}
CSRF_TRUSTED_ORIGINS = (
    # 'https://hostname.example.com',
)
HTTP_PROXIES = {
    # 'http': 'http://10.10.1.10:3128',
}
INTERNAL_IPS = ('127.0.0.1', '::1')
RELEASE_CHECK_URL = None
# RELEASE_CHECK_URL = 'https://api.github.com/repos/netbox-community/x'
"""


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (
            "pyproject.toml",
            '[[tool.uv.index]]\nname = "pytorch"\n'
            'url = "https://download.pytorch.org/whl/cpu"\n'
            '[[tool.poetry.source]]\nname = "private"\n'
            'url = "https://pypi.example.com/simple/"\n',
        ),
        (
            "setup.cfg",
            "[metadata]\nurl = https://github.com/org/proj\n"
            "project_urls =\n"
            "    Bug Tracker = https://github.com/org/proj/issues\n",
        ),
        (
            "package.json",
            '{\n  "repository": {\n    "type": "git",\n'
            '    "url": "https://github.com/org/proj.git"\n  },\n'
            '  "bugs": {\n    "url": "https://github.com/org/proj/issues"\n'
            '  },\n  "homepage": "https://github.com/org/proj#readme"\n}\n',
        ),
        (
            "empty.yaml",
            "url: null\nhost: ~\nDB_HOST: None\napi_url: false\n"
            'servers: ["localhost", null]\n',
        ),
        (
            "commented.conf",
            "# url: https://example.com\n; host = db.example.com\n"
            '  // "host": "db.example.com"\n'
            "# DATABASE_URL=postgresql://db.example.com/x\n",
        ),
        (
            "settings.py",
            "ALLOWED_HOSTS = ['netbox.example.com']\n"
            "CSRF_TRUSTED_ORIGINS = ['https://netbox.example.com']\n"
            'CORS_ALLOWED_ORIGINS = ["https://app.example.com"]\n'
            'LOGIN_URL = "login"\nLOGIN_REDIRECT_URL = "/"\n'
            "STATIC_URL = 'static/'\nMEDIA_URL = './media/'\n",
        ),
        (
            "pretty.json",
            '{\n  "server": {\n    "host": "localhost",\n'
            '    "port": 8080\n  },\n  "hosts": [\n    "localhost"\n'
            '  ],\n  "url": "https://api.example.com"\n}\n',
        ),
        ("configuration.py", NETBOX_SHAPED),
    ],
    ids=[
        "package-indexes",
        "setup-cfg",
        "package-json",
        "empty-values",
        "comments",
        "django",
        "pretty-json",
        "netbox",
    ],
)
def test_everyday_project_files_pass(
    tmp_path: Path, rel: str, text: str
) -> None:
    write(tmp_path, rel, text)
    result = side_effects_gate(make_spec(config=[rel]), tmp_path, {})
    assert result.status == "passed", result.detail


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (
            NETBOX_SHAPED.replace("'HOST': 'localhost'", "'HOST': 'db.x'", 1),
            "(HOST) points at db.x, which is not local",
        ),
        (
            "api_url: https://api.example.com/v1\n",
            "(api_url) points at api.example.com, which is not local",
        ),
        (
            "url: postgres://db.example.com/x\n",
            "points at db.example.com, which is not local",
        ),
        (
            '{"server": {"host": "evil.com"}}\n',
            "(host) points at evil.com, which is not local",
        ),
        ("hosts: [[localhost], [evil.com]]\n", "(hosts) holds a value"),
        ("API_URL = 'api.example.com/v1'\n", "(API_URL) holds a host"),
    ],
    ids=["netbox-remote", "api-url", "service-url", "flow", "nested", "path"],
)
def test_real_targets_in_project_files_still_fail(
    tmp_path: Path, text: str, problem: str
) -> None:
    write(tmp_path, "app.conf", text)
    result = side_effects_gate(make_spec(config=["app.conf"]), tmp_path, {})
    assert result.status == "failed"
    assert problem in result.detail


def test_django_names_and_relative_urls_in_env_pass() -> None:
    env = {
        "ALLOWED_HOSTS": "netbox.example.com",
        "CSRF_TRUSTED_ORIGINS": "https://netbox.example.com",
        "CORS_ALLOWED_ORIGINS": "https://app.example.com",
        "LOGIN_URL": "accounts/login/",
    }
    result = side_effects_gate(make_spec(env=env), Path("."), {})
    assert result.status == "passed"
    assert gate_for("api.example.com/v1", "API_URL")[0] == "failed"


SPACES = " " * 50_000


@pytest.mark.parametrize(
    "line",
    [
        "host: [" * 16_000,
        "host: [" * 16_000 + "]",
        "DB_URL: [" * 16_000 + "]",
        "hosts: [a, " * 16_000 + "]",
    ],
    ids=["host", "closed-at-end", "service-name", "open-lists"],
)
def test_open_brackets_are_linear(tmp_path: Path, line: str) -> None:
    write(tmp_path, "app.conf", line + "\n")
    spec = make_spec(config=["app.conf"])
    start = time.perf_counter()
    result = side_effects_gate(spec, tmp_path, {})
    # About 0.07 s on an M4 and 15 s for the quadratic scan this
    # replaced; the budget leaves room for emulated x86 under load.
    assert time.perf_counter() - start < 2.0
    assert result.status == "failed"


def _seconds_for(tmp_path: Path, text: str) -> float:
    """Return the fastest of three gate runs over one config file."""
    write(tmp_path, "app.json", text)
    spec = make_spec(config=["app.json"])
    times: list[float] = []
    for _ in range(3):
        start = time.perf_counter()
        side_effects_gate(spec, tmp_path, {})
        times.append(time.perf_counter() - start)
    return min(times)


def test_minified_json_scales_linearly(tmp_path: Path) -> None:
    # Every key opens a mapping. Copying the rest of the line per key
    # grew 63x for 8x the size (0.06 s to 3.9 s); linear grows 8x.
    def minified(n: int) -> str:
        return "{" + '"host":{"a":1},' * n + '"x":1}\n'

    small = _seconds_for(tmp_path, minified(16_000))
    large = _seconds_for(tmp_path, minified(128_000))
    assert large / small < 20


@pytest.mark.parametrize(
    ("text", "where"),
    [
        (
            '{\n  "hosts": [\n    "localhost",\n'
            '    "db.prod.example.com"\n  ]\n}\n',
            "config app.conf line 4 (hosts)",
        ),
        (
            "DB_HOSTS = [\n    'localhost',  # primary\n"
            "    'db.prod.example.com',\n]\n",
            "config app.conf line 3 (DB_HOSTS)",
        ),
        (
            "SERVERS = (\n    'localhost', 'db.prod.example.com',\n)\n",
            "config app.conf line 2 (SERVERS)",
        ),
        (
            "hosts = [\n  # comment\n\n  'db.prod.example.com']\n",
            "config app.conf line 4 (hosts)",
        ),
    ],
    ids=["json", "black", "tuple", "close-on-item-line"],
)
def test_lists_opened_at_the_end_of_a_line_are_read(
    tmp_path: Path, text: str, where: str
) -> None:
    write(tmp_path, "app.conf", text)
    result = side_effects_gate(make_spec(config=["app.conf"]), tmp_path, {})
    assert result.detail == (
        f"{where} points at db.prod.example.com, which is not local"
    )


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        (
            "DB_HOSTS = [\n    'localhost',\n",
            "config app.conf line 1 (DB_HOSTS) opens a list that never closes",
        ),
        (
            '"hosts": [\n  ["db.prod.example.com"]\n]\n',
            "config app.conf line 2 (hosts) holds a value that cannot be "
            "parsed",
        ),
        (
            '"servers": [\n  {\n    "host": "db.prod.example.com"\n  }\n]\n',
            "config app.conf line 3 (host) points at db.prod.example.com, "
            "which is not local",
        ),
    ],
    ids=["never-closed", "nested", "list-of-mappings"],
)
def test_open_lists_that_cannot_be_read_fail_closed(
    tmp_path: Path, text: str, detail: str
) -> None:
    write(tmp_path, "app.conf", text)
    result = side_effects_gate(make_spec(config=["app.conf"]), tmp_path, {})
    assert result.detail == detail


def test_local_lists_opened_at_the_end_of_a_line_pass(
    tmp_path: Path,
) -> None:
    text = (
        '{\n  "hosts": [\n    "localhost",\n    "127.0.0.1"\n  ],\n'
        '  "servers": [\n    {"host": "localhost"}\n  ]\n}\n'
        "DB_HOSTS = (\n    'localhost',\n    None,\n)\nTZ = 'UTC'\n"
    )
    write(tmp_path, "app.conf", text)
    result = side_effects_gate(make_spec(config=["app.conf"]), tmp_path, {})
    assert result.status == "passed", result.detail


@pytest.mark.parametrize(
    "line",
    [
        f"host:{SPACES}x{SPACES}y",
        f'host: "localhost"{SPACES}y',
        f"{{host{SPACES}x",
        f"dsn = host=localhost{SPACES}x{SPACES}=",
        f"DB_HOST=localhost{SPACES}#{SPACES};",
        f"# postgresql://{SPACES}",
        f"{SPACES}host{SPACES}",
    ],
    ids=["key", "quoted", "inline", "dsn", "comment", "url", "bare"],
)
def test_long_whitespace_in_config_is_linear(
    tmp_path: Path, line: str
) -> None:
    write(tmp_path, "app.conf", line + "\n")
    spec = make_spec(config=["app.conf"])
    start = time.perf_counter()
    side_effects_gate(spec, tmp_path, {})
    assert time.perf_counter() - start < 0.5


@pytest.mark.parametrize(
    "value",
    [
        f"a{SPACES}b",
        f"host=localhost{SPACES}x",
        f"k{SPACES}=v",
        f"{SPACES}=",
        f"https://{SPACES}",
        f"x{SPACES}https:{SPACES}postgresql://",
        "'https://a'" * 10_000,
        '"postgresql://a",' * 2_000,
        "https:" * 10_000,
    ],
    ids=[
        "words",
        "dsn",
        "key",
        "no-key",
        "url",
        "schemes",
        "quoted-urls",
        "quoted-service-urls",
        "bare-schemes",
    ],
)
def test_long_whitespace_in_env_and_argv_is_linear(value: str) -> None:
    spec = make_spec(
        command=["python", value, f"PGHOST={value}"],
        env={"DATABASE_URL": value, "NOTES": value},
    )
    start = time.perf_counter()
    side_effects_gate(spec, Path("."), {})
    assert time.perf_counter() - start < 0.5


# The opt-in


def test_opt_in_waives_and_records_the_findings() -> None:
    spec = make_spec(
        env={"DATABASE_URL": "postgresql://db.example.com/orders"},
        allow_remote_services=True,
    )
    result = side_effects_gate(spec, Path("."), {})
    assert result.status == "waived"
    assert result.detail == (
        "allow_remote_services: spec env DATABASE_URL points at "
        "db.example.com, which is not local"
    )


def test_opt_in_with_only_local_addresses_passes() -> None:
    spec = make_spec(
        env={"DATABASE_URL": "postgresql://localhost/orders"},
        allow_remote_services=True,
    )
    assert side_effects_gate(spec, Path("."), {}).status == "passed"


# The remote-database example


def load_example(name: str) -> BenchmarkSpec:
    text = (MEMBERSHIP / name).read_text(encoding="utf-8")
    return BenchmarkSpec.model_validate(json.loads(text))


@pytest.mark.usefixtures("no_processes")
def test_remote_database_example_is_refused_before_anything_runs() -> None:
    spec = load_example("spec-remote-db.json")
    result = side_effects_gate(spec, MEMBERSHIP, {})
    assert result.status == "failed"
    assert result.detail == (
        "spec env DATABASE_URL points at db.example.com, which is not local"
    )


@pytest.mark.usefixtures("no_processes")
def test_local_example_passes() -> None:
    spec = load_example("spec.json")
    result = side_effects_gate(spec, MEMBERSHIP, {"HOME": "/home/bench"})
    assert result.status == "passed"


# The reset command


def reset_spec(code: str, **reset: Any) -> BenchmarkSpec:
    fields: dict[str, Any] = {
        "command": [PYTHON, "-c", code],
        "timeout_seconds": 30.0,
    }
    fields.update(reset)
    return make_spec(env={"SPEC_VALUE": "from-spec"}, reset=fields)


@pytest.mark.usefixtures("no_processes")
def test_no_reset_runs_nothing(tmp_path: Path) -> None:
    run_reset(make_spec(), tmp_path, {})


def test_reset_runs_in_its_directory_with_the_benchmark_env(
    tmp_path: Path,
) -> None:
    (tmp_path / "db").mkdir()
    code = (
        "import json, os, pathlib; "
        "pathlib.Path('state').write_text(json.dumps(dict(os.environ)))"
    )
    spec = reset_spec(code, cwd="db")
    for _ in range(2):
        run_reset(spec, tmp_path, PROCESS_ENV)
    state = json.loads((tmp_path / "db" / "state").read_text())
    if sys.platform == "darwin":
        # macOS adds this to every process it starts; perfproof doesn't.
        state.pop("__CF_USER_TEXT_ENCODING", None)
    assert state == {**BASE_PART, "SPEC_VALUE": "from-spec"}


def test_failed_reset_stops_with_its_output(tmp_path: Path) -> None:
    code = (
        "import sys; print('dropping tables'); "
        "print('connection refused', file=sys.stderr); sys.exit(3)"
    )
    spec = reset_spec(code)
    with pytest.raises(ResetError) as caught:
        run_reset(spec, tmp_path, dict(os.environ))
    gate = caught.value.gate
    assert gate.gate == "side_effects"
    assert gate.status == "failed"
    assert gate.detail == "reset command exited 3; the run stops"
    assert str(caught.value) == gate.detail
    assert gate.output is not None
    assert gate.output.command == (PYTHON, "-c", code)
    assert gate.output.exit_code == 3
    assert not gate.output.timed_out
    assert gate.output.stdout == "dropping tables\n"
    assert gate.output.stderr == "connection refused\n"
    assert not gate.output.truncated


def test_failed_reset_goes_into_the_record(tmp_path: Path) -> None:
    spec = reset_spec("raise SystemExit(1)")
    with pytest.raises(ResetError) as caught:
        run_reset(spec, tmp_path, dict(os.environ))
    t0 = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    machine_missing = {
        name: "not probed in this test"
        for name in MachineFingerprint.model_fields
        if name not in ("captured_at", "missing")
    }
    env_fields = (
        "runtime_kind",
        "runtime_executable",
        "runtime_version",
        "linked_libraries",
        "env_names",
        "dependency_locks",
    )
    env_missing: dict[str, str] = dict.fromkeys(
        env_fields, "not probed in this test"
    )
    record = RunRecord(
        run_id="reset-failed",
        perfproof_version="0.1.0",
        started_at=t0,
        finished_at=t0,
        spec=spec,
        baseline_commit="0" * 40,
        order_seed=1,
        verdict=Verdict(
            outcome="REJECT",
            reason="integrity",
            rule=1,
            gate="side_effects",
            rounds=0,
            looks=0,
            procedure="t_log_ratio",
            calibration="uncalibrated",
            diagnostics=Diagnostics(procedure_uncalibrated=True),
        ),
        gates=(caught.value.gate,),
        machines=(
            MachineFingerprint(captured_at=t0, missing=machine_missing),
        ),
        environments=tuple(
            TreeEnvironment(version=arm, missing=env_missing)
            for arm in ("baseline", "candidate")
        ),
        samples_path=None,
    )
    loaded = RunRecord.model_validate_json(record.model_dump_json())
    output = loaded.gates[0].output
    assert output is not None
    assert output.exit_code == 1


def test_reset_output_keeps_only_the_tail(tmp_path: Path) -> None:
    code = (
        "import sys; sys.stdout.write('x' * 100000 + 'END'); "
        "sys.stdout.flush(); sys.exit(1)"
    )
    with pytest.raises(ResetError) as caught:
        run_reset(reset_spec(code), tmp_path, dict(os.environ))
    output = caught.value.gate.output
    assert output is not None
    assert output.truncated
    assert len(output.stdout) == OUTPUT_TAIL_BYTES
    assert output.stdout.endswith("xEND")
    assert output.stderr == ""


def test_reset_killed_by_a_signal(tmp_path: Path) -> None:
    code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    with pytest.raises(ResetError) as caught:
        run_reset(reset_spec(code), tmp_path, dict(os.environ))
    gate = caught.value.gate
    assert (
        gate.detail == "reset command was killed by signal 15; the run stops"
    )
    assert gate.output is not None
    assert gate.output.exit_code == -15


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_reset_timeout_kills_its_whole_session(tmp_path: Path) -> None:
    code = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    spec = reset_spec(code, timeout_seconds=1.0)
    start = time.monotonic()
    with pytest.raises(ResetError) as caught:
        run_reset(spec, tmp_path, dict(os.environ))
    assert time.monotonic() - start < 10
    gate = caught.value.gate
    assert gate.detail == (
        "reset command timed out after 1.0 s and was killed; the run stops"
    )
    assert gate.output is not None
    assert gate.output.timed_out
    assert gate.output.exit_code == -9
    assert gate.output.stdout == "started\n"
    child = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(child)


def test_reset_that_cannot_start(tmp_path: Path) -> None:
    spec = make_spec(
        reset={"command": ["./no-such-reset"], "timeout_seconds": 5.0}
    )
    with pytest.raises(ResetError) as caught:
        run_reset(spec, tmp_path, dict(os.environ))
    gate = caught.value.gate
    assert gate.detail.startswith("reset command could not be started: ")
    assert gate.output is not None
    assert gate.output.exit_code == 127
    assert "no-such-reset" in gate.output.stderr


@pytest.mark.parametrize("code", [errno.EMFILE, errno.EAGAIN, errno.E2BIG])
def test_reset_launch_fault_of_the_host_is_not_a_reset_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def popen(argv: list[str], **_kwargs: object) -> subprocess.Popen[bytes]:
        raise OSError(code, os.strerror(code), argv[0])

    monkeypatch.setattr("perfproof.bench.side_effects.subprocess.Popen", popen)
    spec = reset_spec("pass")
    with pytest.raises(OSError, match=os.strerror(code)):
        run_reset(spec, tmp_path, dict(os.environ))


def test_reset_program_that_cannot_be_found_is_a_reset_failure(
    tmp_path: Path,
) -> None:
    spec = reset_spec("pass", command=["/no/such/program"])
    with pytest.raises(ResetError) as caught:
        run_reset(spec, tmp_path, dict(os.environ))
    assert caught.value.gate.output is not None
    assert caught.value.gate.output.exit_code == 127


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_reset_working_directory_that_is_unusable_is_a_reset_failure(
    tmp_path: Path, kind: str
) -> None:
    (tmp_path / "file").write_text("a file, not a directory")
    spec = reset_spec(
        "pass", cwd="no-such-dir" if kind == "missing" else "file"
    )
    with pytest.raises(ResetError) as caught:
        run_reset(spec, tmp_path, dict(os.environ))
    assert caught.value.gate.output is not None
    assert caught.value.gate.output.exit_code == 127


REGISTRY_ARGS = [
    ["pip", "install", "--index-url=https://pypi.org/simple", "pkg"],
    ["pip", "install", "--index-url", "https://pypi.org/simple/", "pkg"],
    ["pip", "download", "https://files.pythonhosted.org/packages/x.whl"],
    ["npm", "install", "--registry=https://registry.npmjs.org/"],
    ["env", "UV_INDEX_URL=https://pypi.org/simple", "uv", "sync"],
    ["cargo", "fetch", "--registry-url", "https://index.crates.io/"],
]


@pytest.mark.parametrize("argv", REGISTRY_ARGS)
def test_a_package_registry_url_is_allowed_in_setup_only(
    argv: list[str],
) -> None:
    in_setup = make_spec(setup={"commands": [argv], "timeout_seconds": 60.0})
    assert side_effects_gate(in_setup, Path("."), {}).status == "passed"
    in_benchmark = make_spec(command=argv)
    assert side_effects_gate(in_benchmark, Path("."), {}).status == "failed"
    in_test = make_spec(correctness={"command": argv, "timeout_seconds": 60.0})
    assert side_effects_gate(in_test, Path("."), {}).status == "failed"


@pytest.mark.parametrize(
    "argv",
    [
        ["pip", "install", "--index-url=https://mirror.example.com/simple"],
        ["pip", "install", "--index-url=http://pypi.org/simple"],
        ["pip", "install", "--index-url=https://user:tok@pypi.org/simple"],
        ["pip", "install", "https://pypi.org.example.com/simple"],
        ["pip", "install", "--index-url=https://pypi.org:8443/simple"],
        ["pip", "install", "--index-url=https://pypi.org\\@evil.example/"],
        ["curl", "https://api.openai.com/v1/models"],
    ],
)
def test_other_remote_hosts_stay_refused_in_setup(argv: list[str]) -> None:
    spec = make_spec(setup={"commands": [argv], "timeout_seconds": 60.0})
    assert side_effects_gate(spec, Path("."), {}).status == "failed"


def test_an_unparseable_value_stays_refused_in_setup() -> None:
    spec = make_spec(
        setup={
            "commands": [["tool", "--host=http:/pypi.org"]],
            "timeout_seconds": 60.0,
        }
    )
    assert side_effects_gate(spec, Path("."), {}).status == "failed"


@pytest.mark.parametrize(
    "sep", ["\t", "\n", "\r", " ", "\x00", "\x0b", "\x0c"]
)
@pytest.mark.parametrize(
    "template",
    [
        "https://pypi.org{sep}.attacker.example/simple",
        "--index-url=https://pypi.org{sep}.attacker.example/simple",
        "https://pypi.org/simple{sep}",
        "{sep}https://pypi.org/simple",
    ],
)
def test_a_registry_url_with_anything_around_it_is_not_exempt(
    sep: str, template: str
) -> None:
    arg = template.format(sep=sep)
    spec = make_spec(
        setup={"commands": [["pip", "install", arg]], "timeout_seconds": 60.0}
    )
    assert side_effects_gate(spec, Path("."), {}).status == "failed"


def test_a_registry_url_in_the_reset_command_stays_refused() -> None:
    spec = make_spec(
        reset={
            "command": ["pip", "download", "https://pypi.org/simple/x"],
            "timeout_seconds": 10.0,
        }
    )
    assert side_effects_gate(spec, Path("."), {}).status == "failed"
