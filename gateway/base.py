"""Gateway base — bidirectional message handling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """A message received from a channel."""
    text: str
    sender_id: str
    channel: str  # "cli", "feishu", "telegram", "discord"
    conversation_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """A response to send back through the channel."""
    text: str
    conversation_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


# Handler type: receives an InboundMessage, returns an OutboundMessage
MessageHandler = Callable[[InboundMessage], Awaitable[OutboundMessage]]


class Gateway(ABC):
    """Base class for bidirectional channel gateways."""

    @abstractmethod
    async def start(self, handler: MessageHandler) -> None:
        """Start listening for messages. Calls handler for each inbound message."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the gateway gracefully."""
        ...
