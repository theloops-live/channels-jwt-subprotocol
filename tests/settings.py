"""Settings shared by the test process and the live ASGI servers it starts."""
import os
import tempfile

import django
from django.conf import settings


def configure():
    if settings.configured:
        return
    db = os.environ.get("CJS_TEST_DB")
    if not db:
        db = os.path.join(tempfile.mkdtemp(prefix="cjs-"), "test.sqlite3")
        os.environ["CJS_TEST_DB"] = db
    settings.configure(
        SECRET_KEY="test-secret-key-that-is-long-enough-for-hs256-signing",
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "rest_framework",
            "channels",
        ],
        # A file, not ":memory:": database_sync_to_async runs queries in a
        # worker thread, and the live servers are separate processes.
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": db}},
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    )
    django.setup()
