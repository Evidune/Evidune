"""Tests for gateway modules."""

import pytest

from gateway.base import InboundMessage, OutboundMessage
from gateway.router import create_gateway
from gateway.web import WebGateway


class TestCreateGateway:
    def test_create_cli(self):
        gw = create_gateway("cli")
        assert gw is not None

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown gateway"):
            create_gateway("nonexistent")


class TestInboundMessage:
    def test_fields(self):
        msg = InboundMessage(
            text="hello",
            sender_id="user1",
            channel="cli",
            conversation_id="conv1",
        )
        assert msg.text == "hello"
        assert msg.channel == "cli"


class TestWebGatewayChat:
    @pytest.mark.asyncio
    async def test_handle_chat_passes_persona_into_metadata(self):
        gw = WebGateway()
        seen: dict[str, object] = {}

        async def handler(message: InboundMessage) -> OutboundMessage:
            seen["metadata"] = message.metadata
            return OutboundMessage(
                text="ok",
                conversation_id=message.conversation_id,
                metadata={"persona": message.metadata.get("persona")},
            )

        gw._handler = handler
        result = await gw._handle_chat("hello", "conv1", persona="formal-helper")
        assert seen["metadata"] == {"persona": "formal-helper"}
        assert result["persona"] == "formal-helper"
