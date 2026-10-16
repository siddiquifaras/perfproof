"""Fetch 100,000 timestamptz values through Django's PostgreSQL backend.

Every value goes through the timestamptz loader Django registers on
the connection, the class the reference change replaces. Needs the
local PostgreSQL container the task's setup starts on port 54329.
Prints the row count so both trees can be compared for equal output.
"""

import sys

import django
from django.conf import settings
from django.db import connection

settings.configure(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": "127.0.0.1",
            "PORT": "54329",
            "USER": "postgres",
            "NAME": "postgres",
        }
    },
    USE_TZ=True,
)
django.setup()

with connection.cursor() as cursor:
    cursor.execute(
        "SELECT timestamptz '2026-01-01' + i * interval '1 second' "
        "FROM generate_series(1, 100000) AS i"
    )
    rows = cursor.fetchall()
sys.stdout.write(f"{len(rows)} {rows[-1][0].isoformat()}\n")
