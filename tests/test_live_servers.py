"""The same handshakes against real ASGI servers, over a real socket.

WebsocketCommunicator talks ASGI directly, so it cannot show what a browser
receives. These tests start Daphne and Uvicorn and connect with the
`websockets` client, which reports the HTTP status of a refused upgrade and
the close code of a refused connection the way a browser would see them.
"""
import asyncio
import os
import socket
import subprocess
import sys
import time

import pytest
import websockets
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken

pytestmark = pytest.mark.live

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_command(name, port):
    if name == "daphne":
        return [sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(port), "tests.live_asgi:app"]
    return [sys.executable, "-m", "uvicorn", "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning", "tests.live_asgi:app"]


@pytest.fixture(scope="module", params=["daphne", "uvicorn"])
def server(request):
    port = free_port()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([ROOT, os.path.join(ROOT, "src")]))
    proc = subprocess.Popen(server_command(request.param, port), cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode())
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError(f"{request.param} did not start")
    yield request.param, f"ws://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


def token_for(username):
    User = get_user_model()
    User.objects.filter(username=username).delete()
    user = User.objects.create_user(username=username, password="x")
    return str(RefreshToken.for_user(user).access_token)


async def open_and_read(url, subprotocols):
    async with websockets.connect(url, subprotocols=subprotocols, open_timeout=5) as ws:
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=3)
        except websockets.ConnectionClosed:
            first = None
        return ws.subprotocol, first, ws.close_code


@pytest.mark.parametrize("path", ["/ws/async/", "/ws/sync/"])
def test_valid_token_over_the_wire(server, path):
    _, base = server
    token = token_for("live-alice")
    subprotocol, first, _ = asyncio.run(open_and_read(base + path, ["jwt", token]))
    assert subprotocol == "jwt"
    assert '"live-alice"' in first


@pytest.mark.parametrize("path", ["/ws/async/", "/ws/sync/"])
def test_refusal_code_reaches_the_client(server, path):
    _, base = server

    async def go():
        async with websockets.connect(base + path, subprotocols=["jwt", "bad.token.value"], open_timeout=5) as ws:
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), timeout=3)
            return ws.subprotocol, ws.close_code

    subprotocol, code = asyncio.run(go())
    assert subprotocol == "jwt"
    assert code == 4401


def test_close_before_accept_is_an_http_403(server):
    """The reason `refuse()` accepts first: this code never reaches a client."""
    _, base = server

    async def go():
        with pytest.raises(websockets.InvalidStatus) as exc:
            async with websockets.connect(base + "/ws/close-before-accept/", subprotocols=["jwt", "x"], open_timeout=5):
                pass
        return exc.value.response.status_code

    assert asyncio.run(go()) == 403
