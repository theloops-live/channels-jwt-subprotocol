"""Consumer helpers: echo the marker on accept, and refuse with a code that arrives.

Two handshake details decide whether the browser gets what you meant:

* If the client offered subprotocols, the server must answer with one of them.
  Answer with none and the browser fails the connection (close code 1006).
  Answer with the token and the token now sits in a response header.
* A ``close()`` before ``accept()`` is not a WebSocket close frame. Daphne and
  Uvicorn answer the upgrade with an HTTP 403, so the close code never reaches
  the client, which sees 1006 again, the same as a dropped network. To send a
  code the client can act on, accept first and close second.

Accepting before refusing admits nothing as long as the consumer joins no
group and sends no frame first, but the browser's ``onopen`` does fire. Mark
the client as connected on a message the server sends after a successful
accept, not on ``onopen``.
"""
from .middleware import SCOPE_KEY

#: Close code for "this token is not good, renew it and reconnect".
CLOSE_UNAUTHENTICATED = 4401


def _offered_marker(scope):
    return scope.get(SCOPE_KEY)


class _Base:
    close_unauthenticated_code = CLOSE_UNAUTHENTICATED

    def is_authenticated(self):
        user = self.scope.get("user")
        return bool(user is not None and getattr(user, "is_authenticated", False))


class AsyncJWTSubprotocolMixin(_Base):
    """For ``AsyncWebsocketConsumer`` and ``AsyncJsonWebsocketConsumer``.

    Usage::

        class Feed(AsyncJWTSubprotocolMixin, AsyncJsonWebsocketConsumer):
            async def connect(self):
                if not self.is_authenticated():
                    return await self.refuse()
                await self.accept_authenticated()
    """

    async def accept_authenticated(self, headers=None):
        """Accept, echoing the marker if the client offered it."""
        # ``headers`` is passed only when given: Channels 4.0's accept() has no
        # such parameter.
        kwargs = {"headers": headers} if headers else {}
        await self.accept(subprotocol=_offered_marker(self.scope), **kwargs)

    async def refuse(self, code=None):
        """Accept, then close with ``code`` (4401 by default)."""
        await self.accept(subprotocol=_offered_marker(self.scope))
        await self.close(code=code or self.close_unauthenticated_code)


class JWTSubprotocolMixin(_Base):
    """For the synchronous ``WebsocketConsumer`` and ``JsonWebsocketConsumer``."""

    def accept_authenticated(self, headers=None):
        """Accept, echoing the marker if the client offered it."""
        kwargs = {"headers": headers} if headers else {}
        self.accept(subprotocol=_offered_marker(self.scope), **kwargs)

    def refuse(self, code=None):
        """Accept, then close with ``code`` (4401 by default)."""
        self.accept(subprotocol=_offered_marker(self.scope))
        self.close(code=code or self.close_unauthenticated_code)
