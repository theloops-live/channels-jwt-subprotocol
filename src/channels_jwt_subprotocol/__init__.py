"""Authenticate Django Channels WebSockets with a JWT sent as a subprotocol."""
from .consumer import CLOSE_UNAUTHENTICATED, AsyncJWTSubprotocolMixin, JWTSubprotocolMixin
from .middleware import (
    DEFAULT_MARKER,
    SCOPE_KEY,
    JWTSubprotocolMiddleware,
    token_from_subprotocols,
)

__all__ = [
    "CLOSE_UNAUTHENTICATED",
    "DEFAULT_MARKER",
    "SCOPE_KEY",
    "AsyncJWTSubprotocolMixin",
    "JWTSubprotocolMiddleware",
    "JWTSubprotocolMixin",
    "token_from_subprotocols",
]

__version__ = "1.0.0"
