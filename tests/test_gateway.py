"""Tests for gateway modules."""

import pytest

from gateway.base import InboundMessage
from gateway.router import create_gateway


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
