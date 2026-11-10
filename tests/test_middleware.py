import asyncio
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from channels_jwt_subprotocol import (
    SCOPE_KEY,
    JWTSubprotocolMiddleware,
    token_from_subprotocols,
)

from .apps import application


def run(coro):
    return asyncio.run(coro)


def make_user(username, active=True):
    User = get_user_model()
    User.objects.filter(username=username).delete()
    return User.objects.create_user(username=username, password="x", is_active=active)


def access_for(user):
    return str(RefreshToken.for_user(user).access_token)


async def handshake(path, subprotocols, app=None):
    comm = WebsocketCommunicator(app or application(), path, subprotocols=subprotocols)
    connected, subprotocol = await comm.connect()
    frames = []
    # receive_nothing() waits without cancelling the app; receive_output()
    # would cancel it on a timeout.
    while not await comm.receive_nothing(timeout=0.3):
        frames.append(await comm.receive_output())
        if frames[-1]["type"] == "websocket.close":
            break
    if not frames or frames[-1]["type"] != "websocket.close":
        await comm.disconnect()
    return connected, subprotocol, frames


def close_code(frames):
    for f in frames:
        if f["type"] == "websocket.close":
            return f.get("code")
    return None


# --- token_from_subprotocols -------------------------------------------------

@pytest.mark.parametrize(
    "offered, expected",
    [
        (["jwt", "a.b.c"], "a.b.c"),
        (["graphql-ws", "jwt", "a.b.c"], "a.b.c"),
        (["jwt", "a.b.c", "extra"], "a.b.c"),
        (["a.b.c", "jwt"], None),
        (["jwt"], None),
        (["jwt", "jwt"], None),
        (["jwt", ""], None),
        ([], None),
        (None, None),
        (["bearer", "a.b.c"], None),
    ],
)
def test_token_is_the_value_after_the_marker(offered, expected):
    assert token_from_subprotocols(offered) == expected


def test_custom_marker():
    assert token_from_subprotocols(["bearer", "t"], marker="bearer") == "t"
    assert token_from_subprotocols(["jwt", "t"], marker="bearer") is None


def test_empty_marker_is_refused():
    with pytest.raises(ValueError):
        JWTSubprotocolMiddleware(lambda *a: None, marker="", get_user=lambda t: None)


# --- with SimpleJWT, through a consumer ---------------------------------------

@pytest.mark.parametrize("path", ["/ws/async/", "/ws/sync/"])
def test_valid_token_is_accepted_and_only_the_marker_is_echoed(path):
    user = make_user("alice")
    token = access_for(user)
    connected, subprotocol, frames = run(handshake(path, ["jwt", token]))
    assert connected is True
    assert subprotocol == "jwt"
    assert token not in str(subprotocol)
    sent = [f for f in frames if f["type"] == "websocket.send"]
    assert sent and '"alice"' in sent[0]["text"]
    assert close_code(frames) is None


@pytest.mark.parametrize("path", ["/ws/async/", "/ws/sync/"])
def test_bad_token_is_accepted_then_closed_with_4401(path):
    connected, subprotocol, frames = run(handshake(path, ["jwt", "not.a.jwt"]))
    assert connected is True
    assert subprotocol == "jwt"
    assert [f["type"] for f in frames] == ["websocket.close"]
    assert close_code(frames) == 4401


def test_expired_token_is_refused():
    user = make_user("bob")
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=-timedelta(minutes=1))
    _, _, frames = run(handshake("/ws/async/", ["jwt", str(token)]))
    assert close_code(frames) == 4401


def test_refresh_token_is_not_an_access_token():
    user = make_user("carol")
    refresh = str(RefreshToken.for_user(user))
    _, _, frames = run(handshake("/ws/async/", ["jwt", refresh]))
    assert close_code(frames) == 4401


def test_inactive_user_is_refused():
    user = make_user("dave", active=False)
    _, _, frames = run(handshake("/ws/async/", ["jwt", access_for(user)]))
    assert close_code(frames) == 4401


def test_marker_without_token_is_refused():
    connected, subprotocol, frames = run(handshake("/ws/async/", ["jwt"]))
    assert subprotocol == "jwt"
    assert close_code(frames) == 4401


def test_no_subprotocols_refused_without_echo():
    connected, subprotocol, frames = run(handshake("/ws/async/", None))
    assert connected is True
    assert subprotocol is None
    assert close_code(frames) == 4401


# --- custom resolver and stacking ---------------------------------------------

def test_custom_get_user():
    seen = []

    async def get_user(token):
        seen.append(token)
        user = await sync_to_async(make_user)("erin")
        return user if token == "letmein" else AnonymousUser()

    app = application(get_user=get_user, marker="bearer")
    ok = run(handshake("/ws/async/", ["bearer", "letmein"], app=app))
    bad = run(handshake("/ws/async/", ["bearer", "nope"], app=app))
    assert ok[1] == "bearer" and close_code(ok[2]) is None
    assert bad[1] == "bearer" and close_code(bad[2]) == 4401
    assert seen == ["letmein", "nope"]


def _capture_scope():
    captured = {}

    async def inner(scope, receive, send):
        captured.update(scope)

    return captured, inner


def test_existing_user_kept_when_marker_not_offered():
    captured, inner = _capture_scope()
    mw = JWTSubprotocolMiddleware(inner, get_user=lambda t: None)
    sentinel = object()
    run(mw({"type": "websocket", "subprotocols": [], "user": sentinel}, None, None))
    assert captured["user"] is sentinel
    assert SCOPE_KEY not in captured


def test_offered_marker_overrides_existing_user_even_when_refused():
    captured, inner = _capture_scope()

    async def refuse(token):
        return AnonymousUser()

    mw = JWTSubprotocolMiddleware(inner, get_user=refuse)
    run(mw({"type": "websocket", "subprotocols": ["jwt", "x"], "user": object()}, None, None))
    assert isinstance(captured["user"], AnonymousUser)
    assert captured[SCOPE_KEY] == "jwt"


def test_http_scope_passes_through_untouched():
    captured, inner = _capture_scope()
    mw = JWTSubprotocolMiddleware(inner, get_user=lambda t: None)
    scope = {"type": "http", "path": "/"}
    run(mw(scope, None, None))
    assert "user" not in captured


def test_caller_scope_is_not_mutated():
    captured, inner = _capture_scope()

    async def get_user(token):
        return AnonymousUser()

    mw = JWTSubprotocolMiddleware(inner, get_user=get_user)
    scope = {"type": "websocket", "subprotocols": ["jwt", "x"]}
    run(mw(scope, None, None))
    assert "user" not in scope and SCOPE_KEY not in scope
