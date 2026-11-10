# channels-jwt-subprotocol

Authenticate [Django Channels](https://channels.readthedocs.io/) WebSockets with a JWT sent as a
**subprotocol**, so the token never appears in the URL.

A browser cannot set an `Authorization` header on a WebSocket handshake. The usual workaround puts
the token in the query string, `wss://example.com/ws/?token=…`, and the query string is part of the
request line that nginx, load balancers and CDNs write to their access logs by default. An access
token lives for minutes; a log line lives for months.

The `Sec-WebSocket-Protocol` header is the other place a browser lets you put a value, and access
logs do not record it by default. This package reads the token from there, validates it with
[SimpleJWT](https://github.com/jazzband/djangorestframework-simplejwt) (or any check you supply),
and handles the two handshake details that otherwise fail silently:

- **The server must echo a subprotocol the client offered.** Echo nothing and the browser drops the
  connection; echo the token and it ends up in a response header. The middleware records a fixed
  marker, and the consumer helpers echo the marker, never the token.
- **A close code sent before `accept()` never reaches the client.** Daphne and Uvicorn answer the
  upgrade with an HTTP 403 instead, and the browser sees code 1006, the same as a dropped network.
  `refuse()` accepts first and then closes, so the client receives `4401` and knows to renew its
  token.

## Install

```bash
pip install "channels-jwt-subprotocol[simplejwt]"
```

Python 3.10+, Django 4.2+, Channels 4.0+. Leave out `[simplejwt]` if you validate tokens yourself.

## Use

**Client.** Offer the marker `jwt`, then the token:

```js
const ws = new WebSocket('wss://example.com/ws/feed/', ['jwt', accessToken])
```

A JWT is made of base64url segments and dots, which are all valid subprotocol characters. An invalid
value makes the constructor throw a `SyntaxError`, so treat a throw like a failed connect.

**ASGI.** Wrap your WebSocket router:

```python
# asgi.py
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

from channels_jwt_subprotocol import JWTSubprotocolMiddleware  # noqa: E402
from myapp.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        JWTSubprotocolMiddleware(URLRouter(websocket_urlpatterns))
    ),
})
```

**Consumer.** Use the mixin's `accept_authenticated()` and `refuse()` instead of calling `accept()`
and `close()` yourself:

```python
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels_jwt_subprotocol import AsyncJWTSubprotocolMixin


class FeedConsumer(AsyncJWTSubprotocolMixin, AsyncJsonWebsocketConsumer):
    async def connect(self):
        if not self.is_authenticated():
            return await self.refuse()          # accept, then close with 4401
        await self.accept_authenticated()       # echoes "jwt", never the token
        await self.send_json({"type": "connected"})
```

`JWTSubprotocolMixin` is the same for the synchronous `WebsocketConsumer` and
`JsonWebsocketConsumer`.

**Client, on close.** Because the server accepts before it refuses, `onopen` fires for a socket that is
about to be closed. Treat the connection as live when the server's own first message arrives, and
renew the token on `4401`:

```js
ws.onmessage = (e) => {
  if (JSON.parse(e.data).type === 'connected') markLive()
}
ws.onclose = (e) => {
  if (e.code === 4401) return renewTokenThenReconnect()   // refused: a new token may help
  scheduleRetry()                                         // network: back off and retry
}
```

If a freshly renewed token is refused too, back off instead of renewing again, or a deactivated
account turns into a loop against your refresh endpoint.

## Behaviour

| Client offers | `scope["user"]` | Consumer with `refuse()` |
|---|---|---|
| `["jwt", <valid access token>]` | the token's user | accepted, echoes `jwt` |
| `["jwt", <expired, refresh, garbage token>]` | `AnonymousUser` | accepted with `jwt`, closed `4401` |
| `["jwt"]` | `AnonymousUser` | accepted with `jwt`, closed `4401` |
| nothing, or no `jwt` marker | unchanged, or `AnonymousUser` if unset | accepted, closed `4401` |

The token is the value **immediately after** the marker, so a client can offer an application
protocol too: `['graphql-transport-ws', 'jwt', token]`. If you do, accept with that protocol yourself,
as the server may echo only one value.

When the marker is offered, the token decides the user, even if an earlier middleware already set
one. When it is not, an existing `scope["user"]` is left alone, so the middleware can sit inside
`AuthMiddlewareStack` for apps that also use session cookies.

The default check is SimpleJWT's `JWTAuthentication`, the class that already guards your REST API:
same signing key, expiry, token type and `is_active` rule, no second implementation to drift.

## Options

```python
JWTSubprotocolMiddleware(inner, marker="jwt", get_user=None)
```

- `marker`: the fixed value the client offers before the token. Use the same one in the client.
- `get_user`: an async callable `get_user(raw_token) -> user`. Return `AnonymousUser()` to refuse.
  Use it for a non-SimpleJWT token, or to add your own checks.

```python
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def get_user(raw_token):
    user = verify_with_your_identity_provider(raw_token)
    return user or AnonymousUser()


application = JWTSubprotocolMiddleware(router, get_user=get_user)
```

The refusal code is `CLOSE_UNAUTHENTICATED` (`4401`). Override `close_unauthenticated_code` on the
consumer, or pass `refuse(code=...)`.

## What this does not do

- **It authenticates the handshake only.** A socket opened with a token that has two minutes left
  stays open after the token expires. Renew near-expiry tokens before connecting, and close sockets
  yourself if your app needs revocation to take effect immediately.
- **It is not encryption.** Use `wss://`. The point is that the token is not written to logs by
  default, not that it is hidden from anything on the connection.
- **Check your own logging.** A proxy configured to log request headers will log this one too.

## Tests

```bash
pip install -e ".[test]"
pytest
```

The suite includes live tests that start Daphne and Uvicorn on a local port and connect with the
`websockets` client, which is how the HTTP 403 for a close before `accept()`, and the `4401` that
arrives after accepting, are checked against real servers rather than assumed. They pass on Daphne
4.0 and 4.2, Uvicorn 0.30 and 0.54, Channels 4.0 and 4.3.

## Background

Written for the dashboard of [TheLoops](https://theloops.live), where one socket per tab pushes the
status of 24/7 streams. The long version, including the nginx and idle-timeout parts, is in
[Django Channels: JWT auth without the token in the URL](https://theloops.live/engineering/django-channels-jwt-websocket).

## License

MIT
