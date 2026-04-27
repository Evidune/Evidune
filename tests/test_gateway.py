"""Tests for gateway modules."""

import asyncio
import json
import sys
import threading
import types

import pytest

from gateway.base import InboundMessage, OutboundMessage
from gateway.router import create_gateway
from gateway.web import WebGateway


class TestCreateGateway:
    def test_create_cli(self):
        gw = create_gateway("cli")
        assert gw is not None

    def test_create_feishu_bot_requires_sdk(self, monkeypatch):
        original_find_spec = __import__("importlib").util.find_spec
        monkeypatch.delitem(sys.modules, "lark_oapi", raising=False)
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: None if name == "lark_oapi" else original_find_spec(name),
        )
        with pytest.raises(RuntimeError, match="lark-oapi"):
            create_gateway("feishu_bot", app_id="app", app_secret="secret")

    def test_create_feishu_bot_with_sdk(self, fake_lark):
        gw = create_gateway("feishu_bot", app_id="app", app_secret="secret")
        assert gw.app_id == "app"

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

    @pytest.mark.asyncio
    async def test_handle_chat_passes_skill_creation_metadata(self):
        gw = WebGateway()

        async def handler(message: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                text="created",
                conversation_id=message.conversation_id,
                metadata={
                    "skill_creation": {
                        "status": "created",
                        "skill_name": "collect-intel",
                    }
                },
            )

        gw._handler = handler
        result = await gw._handle_chat("create skill", "conv3")
        assert result["skill_creation"]["status"] == "created"
        assert result["skill_creation"]["skill_name"] == "collect-intel"

    @pytest.mark.asyncio
    async def test_handle_chat_passes_execution_evaluations_metadata(self):
        gw = WebGateway()

        async def handler(message: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                text="evaluated",
                conversation_id=message.conversation_id,
                metadata={
                    "execution_evaluations": [
                        {
                            "skill_name": "collect-intel",
                            "execution_id": 7,
                            "aggregate_score": 0.71,
                        }
                    ]
                },
            )

        gw._handler = handler
        result = await gw._handle_chat("use skill", "conv4")
        assert result["execution_evaluations"][0]["skill_name"] == "collect-intel"
        assert result["execution_evaluations"][0]["aggregate_score"] == 0.71

    def test_skills_payload_uses_dynamic_provider(self):
        gw = WebGateway()
        gw.set_skills([{"name": "old", "description": "Old"}])
        gw.set_skill_provider(lambda: [{"name": "new", "description": "New", "status": "active"}])

        assert gw._skills_payload()[0]["name"] == "new"


class FakeResponse:
    def __init__(self, code=0, msg="ok", log_id="log-1"):
        self.code = code
        self.msg = msg
        self._log_id = log_id

    def success(self):
        return self.code == 0

    def get_log_id(self):
        return self._log_id


class FakeReplyApi:
    def __init__(self, state):
        self.state = state

    def reply(self, request):
        self.state["replies"].append(request)
        if request.request_body.msg_type == "interactive" and self.state["fail_interactive"]:
            return FakeResponse(code=999, msg="card failed", log_id="log-fail")
        return FakeResponse()


class FakeApiClient:
    def __init__(self, state):
        self.im = types.SimpleNamespace(
            v1=types.SimpleNamespace(message=FakeReplyApi(state)),
        )


class FakeClientBuilder:
    def __init__(self, state):
        self.state = state

    def app_id(self, value):
        self.state["api_app_id"] = value
        return self

    def app_secret(self, value):
        self.state["api_app_secret"] = value
        return self

    def domain(self, value):
        self.state["api_domain"] = value
        return self

    def log_level(self, value):
        self.state["api_log_level"] = value
        return self

    def build(self):
        client = FakeApiClient(self.state)
        self.state["api_client"] = client
        return client


class FakeClient:
    def __init__(self, state):
        self.state = state

    def builder(self):
        return FakeClientBuilder(self.state)


class FakeBuilder:
    def __init__(self, state):
        self.state = state

    def register_p2_im_message_receive_v1(self, handler):
        self.state["event_handler"] = handler
        return self

    def build(self):
        self.state["event_dispatcher_built"] = True
        return object()


class FakeDispatcher:
    def __init__(self, state):
        self.state = state

    def builder(self, encrypt_key, verification_token, level=None):
        self.state["dispatcher_args"] = (encrypt_key, verification_token, level)
        return FakeBuilder(self.state)


class FakeWsClient:
    def __init__(self, app_id, app_secret, **kwargs):
        self.app_id = app_id
        self.app_secret = app_secret
        self.kwargs = kwargs
        self.closed = threading.Event()
        kwargs["state"]["ws_client"] = self

    def start(self):
        self.kwargs["state"]["started"].set()
        self.closed.wait(2)

    def close(self):
        self.closed.set()


class FakeBody:
    @staticmethod
    def builder():
        return FakeBodyBuilder()


class FakeBodyBuilder:
    def __init__(self):
        self.body = types.SimpleNamespace(content="", msg_type="")

    def content(self, value):
        self.body.content = value
        return self

    def msg_type(self, value):
        self.body.msg_type = value
        return self

    def build(self):
        return self.body


class FakeRequest:
    @staticmethod
    def builder():
        return FakeRequestBuilder()


class FakeRequestBuilder:
    def __init__(self):
        self.request = types.SimpleNamespace(message_id="", request_body=None)

    def message_id(self, value):
        self.request.message_id = value
        return self

    def request_body(self, value):
        self.request.request_body = value
        return self

    def build(self):
        return self.request


@pytest.fixture
def fake_lark(monkeypatch):
    state = {
        "replies": [],
        "fail_interactive": False,
        "started": threading.Event(),
    }
    level = types.SimpleNamespace(INFO=types.SimpleNamespace(value=20))
    module = types.ModuleType("lark_oapi")
    module.LogLevel = level
    module.EventDispatcherHandler = FakeDispatcher(state)
    module.Client = FakeClient(state)
    module.im = types.SimpleNamespace(
        v1=types.SimpleNamespace(
            ReplyMessageRequestBody=FakeBody,
            ReplyMessageRequest=FakeRequest,
        )
    )
    module.ws = types.SimpleNamespace(
        Client=lambda app_id, app_secret, **kwargs: FakeWsClient(
            app_id, app_secret, state=state, **kwargs
        )
    )
    ws_client_module = types.ModuleType("lark_oapi.ws.client")
    ws_client_module.loop = types.SimpleNamespace(
        call_soon_threadsafe=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "lark_oapi", module)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws", types.ModuleType("lark_oapi.ws"))
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.client", ws_client_module)
    return types.SimpleNamespace(module=module, state=state)


def _event(
    *,
    text="hello",
    message_type="text",
    event_id="evt-1",
    message_id="msg-1",
    chat_id="chat-1",
    open_id="ou-1",
    mentions=None,
):
    message = types.SimpleNamespace(
        message_id=message_id,
        chat_id=chat_id,
        chat_type="group",
        message_type=message_type,
        content=json.dumps({"text": text}),
        mentions=mentions or [],
    )
    sender = types.SimpleNamespace(
        sender_id=types.SimpleNamespace(open_id=open_id, user_id="user-1"),
    )
    return types.SimpleNamespace(
        header=types.SimpleNamespace(event_id=event_id),
        event=types.SimpleNamespace(message=message, sender=sender),
    )


def _wire_sdk(gw, fake_lark):
    gw._lark = fake_lark.module
    gw._api_client = FakeApiClient(fake_lark.state)


class TestFeishuBotGateway:
    @pytest.mark.asyncio
    async def test_text_message_becomes_inbound_and_replies_with_card(self, fake_lark):
        gw = create_gateway("feishu_bot", app_id="app", app_secret="secret")
        _wire_sdk(gw, fake_lark)
        seen = {}

        async def handler(message: InboundMessage) -> OutboundMessage:
            seen["message"] = message
            return OutboundMessage(text="reply", conversation_id=message.conversation_id)

        gw._handler = handler
        mention = types.SimpleNamespace(key="@_bot", name="Evidune")
        await gw._process_event(_event(text="@_bot  do work", mentions=[mention]))

        assert seen["message"].text == "do work"
        assert seen["message"].conversation_id == "chat-1"
        assert seen["message"].sender_id == "ou-1"
        assert seen["message"].metadata["message_id"] == "msg-1"
        reply = fake_lark.state["replies"][0]
        assert reply.request_body.msg_type == "interactive"
        assert "reply" in reply.request_body.content

    @pytest.mark.asyncio
    async def test_non_text_message_gets_unsupported_reply(self, fake_lark):
        gw = create_gateway("feishu_bot", app_id="app", app_secret="secret")
        _wire_sdk(gw, fake_lark)
        await gw._process_event(_event(message_type="image"))
        assert "只支持文本" in fake_lark.state["replies"][0].request_body.content

    @pytest.mark.asyncio
    async def test_duplicate_event_is_ignored(self, fake_lark):
        gw = create_gateway("feishu_bot", app_id="app", app_secret="secret")
        _wire_sdk(gw, fake_lark)
        calls = 0

        async def handler(message: InboundMessage) -> OutboundMessage:
            nonlocal calls
            calls += 1
            return OutboundMessage(text="ok", conversation_id=message.conversation_id)

        gw._handler = handler
        event = _event()
        await gw._process_event(event)
        await gw._process_event(event)
        assert calls == 1
        assert len(fake_lark.state["replies"]) == 1

    @pytest.mark.asyncio
    async def test_allowlist_blocks_untrusted_sender(self, fake_lark):
        gw = create_gateway(
            "feishu_bot",
            app_id="app",
            app_secret="secret",
            allowed_open_ids=["ou-allowed"],
        )
        _wire_sdk(gw, fake_lark)
        called = False

        async def handler(message: InboundMessage) -> OutboundMessage:
            nonlocal called
            called = True
            return OutboundMessage(text="ok", conversation_id=message.conversation_id)

        gw._handler = handler
        await gw._process_event(_event(open_id="ou-blocked"))
        assert called is False
        assert fake_lark.state["replies"] == []

    @pytest.mark.asyncio
    async def test_card_reply_falls_back_to_text_and_logs_sdk_error(self, fake_lark, caplog):
        gw = create_gateway("feishu_bot", app_id="app", app_secret="secret")
        _wire_sdk(gw, fake_lark)
        fake_lark.state["fail_interactive"] = True

        await gw._reply("msg-1", "hello")

        assert [r.request_body.msg_type for r in fake_lark.state["replies"]] == [
            "interactive",
            "text",
        ]
        assert "code=999" in caplog.text
        assert "log-fail" in caplog.text

    @pytest.mark.asyncio
    async def test_long_reply_is_chunked(self, fake_lark):
        gw = create_gateway(
            "feishu_bot",
            app_id="app",
            app_secret="secret",
            max_reply_chars=500,
        )
        _wire_sdk(gw, fake_lark)

        await gw._reply("msg-1", "x" * 501)

        assert len(fake_lark.state["replies"]) == 2

    @pytest.mark.asyncio
    async def test_start_registers_handler_and_processes_queued_event(self, fake_lark):
        gw = create_gateway("feishu_bot", app_id="app", app_secret="secret")
        seen = []

        async def handler(message: InboundMessage) -> OutboundMessage:
            seen.append(message.text)
            return OutboundMessage(text="ok", conversation_id=message.conversation_id)

        task = asyncio.create_task(gw.start(handler))
        assert await asyncio.to_thread(fake_lark.state["started"].wait, 1)
        assert task.done() is False
        fake_lark.state["event_handler"](_event(text="queued"))

        for _ in range(20):
            if seen:
                break
            await asyncio.sleep(0.01)

        assert seen == ["queued"]
        await gw.stop()
        await task


class TestRouter:
    @pytest.mark.asyncio
    async def test_multiple_gateways_start_as_tasks(self):
        from gateway.router import Router

        class Agent:
            async def handle(self, message):
                return OutboundMessage(text="ok", conversation_id=message.conversation_id)

        class FakeGateway:
            def __init__(self):
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.cancelled = False

            async def start(self, handler):
                self.started.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

            async def stop(self):
                self.release.set()

        gw1 = FakeGateway()
        gw2 = FakeGateway()
        router = Router(Agent(), [gw1, gw2])
        task = asyncio.create_task(router.start())
        await gw1.started.wait()
        await gw2.started.wait()

        gw1.release.set()
        await task

        assert gw2.cancelled is True
