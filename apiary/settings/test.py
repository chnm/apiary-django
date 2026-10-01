from . import *

# The hermetic image build has no PostgreSQL; the live deployment exercises it.
DATABASES = {
    alias: {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    for alias in DATABASES
}

# The test client speaks plain HTTP; redirect-to-TLS is the edge's contract.
SECURE_SSL_REDIRECT = False
