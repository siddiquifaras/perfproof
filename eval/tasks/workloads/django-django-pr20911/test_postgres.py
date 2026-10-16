"""Settings for Django's own test runner against the task's PostgreSQL.

Copied into ``tests/`` and used as ``runtests.py --settings
test_postgres``: Django's ``tests/test_sqlite.py`` with the databases
pointed at the local PostgreSQL container on port 54329.
"""

from test_sqlite import *  # noqa: F403

_DB = {
    "ENGINE": "django.db.backends.postgresql",
    "HOST": "127.0.0.1",
    "PORT": "54329",
    "USER": "postgres",
}

DATABASES = {
    "default": {**_DB, "NAME": "django"},
    "other": {**_DB, "NAME": "django_other"},
}
