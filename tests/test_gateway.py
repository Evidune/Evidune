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
    async def test_handle_chat_passes_identity_into_metadata(self):
        gw = WebGateway()
        seen: dict[str, object] = {}

        async def handler(message: InboundMessage) -> OutboundMessage:
            seen["metadata"] = message.metadata
            return OutboundMessage(
                text="ok",
                conversation_id=message.conversation_id,
                metadata={"identity": message.metadata.get("identity")},
            )

        gw._handler = handler
        result = await gw._handle_chat("hello", "conv1", identity="formal-helper")
        assert seen["metadata"] == {"identity": "formal-helper"}
        assert result["identity"] == "formal-helper"

    @pytest.mark.asyncio
    async def test_handle_chat_passes_mode_and_plan_through_metadata(self):
        gw = WebGateway()
        seen: dict[str, object] = {}

        async def handler(message: InboundMessage) -> OutboundMessage:
            seen["metadata"] = message.metadata
            return OutboundMessage(
                text="plan ready",
                conversation_id=message.conversation_id,
                metadata={
                    "mode": message.metadata.get("mode"),
                    "plan": {
                        "explanation": "Ship this in two steps.",
                        "items": [{"step": "Write the plan", "status": "completed"}],
                    },
                },
            )

        gw._handler = handler
        result = await gw._handle_chat("make a plan", "conv2", mode="plan")
        assert seen["metadata"] == {"mode": "plan"}
        assert result["mode"] == "plan"
        assert result["plan"]["items"][0]["step"] == "Write the plan"
