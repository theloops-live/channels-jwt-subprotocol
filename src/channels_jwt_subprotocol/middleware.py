"""ASGI middleware that authenticates a WebSocket from a JWT sent as a subprotocol.

A browser cannot set an ``Authorization`` header on a WebSocket handshake. The
two places left for a token are the query string and the
``Sec-WebSocket-Protocol`` header. The query string is part of the request line,
which access logs, proxies and CDNs record by default. A subprotocol travels as
a request header, which they do not.

The client offers two subprotocols, a fixed marker and the token right after it::

    new WebSocket(url, ["jwt", accessToken])

The middleware finds the marker, takes the value that follows it as the token,
and puts the resolved user in ``scope["user"]``. It also records the marker in
``scope["jwt_subprotocol"]`` so the consumer can echo it back on ``accept()``:
the server has to answer with one of the offered values, and it must be the
marker, never the token.
"""
from django.core.exceptions import ImproperlyConfigured

DEFAULT_MARKER = "jwt"
SCOPE_KEY = "jwt_subprotocol"


def token_from_subprotocols(subprotocols, marker=DEFAULT_MARKER):
    """Return the value offered right after ``marker``, or ``None``.

    ``None`` when the marker is absent, when nothing follows it, or when the
    value after it is the marker again. Other subprotocols the client offers
    before the marker are ignored, so a client can still negotiate an
    application protocol alongside the token.
    """
    values = [str(p) for p in (subprotocols or [])]
    try:
        index = values.index(marker)
    except ValueError:
        return None
    if index + 1 >= len(values):
        return None
    token = values[index + 1]
    if not token or token == marker:
        return None
    return token


def _anonymous_user():
    from django.contrib.auth.models import AnonymousUser

    return AnonymousUser()


def _simplejwt_resolver():
    """Build the default resolver on top of SimpleJWT's ``JWTAuthentication``.

    It reuses the class that already authenticates the REST API, so the signing
    key, the expiry, the token type and the user's ``is_active`` flag are
    checked by the same code in both places.
    """
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        from rest_framework_simplejwt.exceptions import (
            AuthenticationFailed,
            InvalidToken,
            TokenError,
        )
    except ImportError as exc:  # pragma: no cover - exercised without simplejwt
        raise ImproperlyConfigured(
            "JWTSubprotocolMiddleware needs djangorestframework-simplejwt for "
            "its default token check. Install it, or pass get_user=..."
        ) from exc

    from channels.db import database_sync_to_async

    @database_sync_to_async
    def resolve(raw_token):
        backend = JWTAuthentication()
        try:
            return backend.get_user(backend.get_validated_token(raw_token))
        except (AuthenticationFailed, InvalidToken, TokenError):
            return _anonymous_user()

    return resolve


class JWTSubprotocolMiddleware:
    """Populate ``scope["user"]`` from a JWT offered as a WebSocket subprotocol.

    ``get_user`` is an async callable that takes the raw token and returns a
    user, or ``AnonymousUser`` for a token it refuses. Leave it out to validate
    with SimpleJWT.

    When the client offers the marker, the token decides ``scope["user"]``: a
    missing or refused token gives ``AnonymousUser`` even if an earlier
    middleware had already set a user. When the client does not offer the
    marker at all, an existing ``scope["user"]`` is left alone, so this can sit
    inside ``AuthMiddlewareStack`` for apps that also use session cookies.
    """

    def __init__(self, inner, marker=DEFAULT_MARKER, get_user=None):
        if not marker:
            raise ValueError("marker must be a non-empty string")
        self.inner = inner
        self.marker = marker
        self.get_user = get_user or _simplejwt_resolver()

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            subprotocols = scope.get("subprotocols") or []
            if self.marker in [str(p) for p in subprotocols]:
                scope = dict(scope)
                scope[SCOPE_KEY] = self.marker
                token = token_from_subprotocols(subprotocols, self.marker)
                scope["user"] = await self.get_user(token) if token else _anonymous_user()
            elif "user" not in scope:
                scope = dict(scope)
                scope["user"] = _anonymous_user()
        return await self.inner(scope, receive, send)
