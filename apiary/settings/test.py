from . import *

# The hermetic image build has no PostgreSQL; the live deployment exercises it.
DATABASES = {
    alias: {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    for alias in DATABASES
}

# The test client speaks plain HTTP; redirect-to-TLS is the edge's contract.
SECURE_SSL_REDIRECT = False

# SQLite stands in for PostgreSQL here, so its lack of table comments is expected.
SILENCED_SYSTEM_CHECKS = [*SILENCED_SYSTEM_CHECKS, "models.W046"]
