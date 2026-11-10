"""ASGI entry point for the live-server tests: `daphne tests.live_asgi:app`."""
from tests.settings import configure

configure()

from tests.apps import application  # noqa: E402

app = application()
