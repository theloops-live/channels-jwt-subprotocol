"""Consumers and ASGI apps shared by the unit tests and the live-server tests."""
from channels.generic.websocket import (
    AsyncJsonWebsocketConsumer,
    AsyncWebsocketConsumer,
    JsonWebsocketConsumer,
)
from channels.routing import URLRouter
from django.urls import path

from channels_jwt_subprotocol import (
    AsyncJWTSubprotocolMixin,
    JWTSubprotocolMiddleware,
    JWTSubprotocolMixin,
)


class AsyncFeed(AsyncJWTSubprotocolMixin, AsyncJsonWebsocketConsumer):
    async def connect(self):
        if not self.is_authenticated():
            return await self.refuse()
        await self.accept_authenticated()
        await self.send_json({"type": "connected", "user": self.scope["user"].get_username()})


class SyncFeed(JWTSubprotocolMixin, JsonWebsocketConsumer):
    def connect(self):
        if not self.is_authenticated():
            return self.refuse()
        self.accept_authenticated()
        self.send_json({"type": "connected", "user": self.scope["user"].get_username()})


class CloseBeforeAccept(AsyncWebsocketConsumer):
    """The pattern this package exists to replace, kept to measure it."""

    async def connect(self):
        await self.close(code=4401)


def router():
    return URLRouter(
        [
            path("ws/async/", AsyncFeed.as_asgi()),
            path("ws/sync/", SyncFeed.as_asgi()),
            path("ws/close-before-accept/", CloseBeforeAccept.as_asgi()),
        ]
    )


def application(**middleware_kwargs):
    return JWTSubprotocolMiddleware(router(), **middleware_kwargs)
