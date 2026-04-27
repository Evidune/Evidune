"""Feishu (Lark) bot gateway using the official long-connection SDK."""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from threading import Event, Thread
from typing import Any

from gateway.base import Gateway, InboundMessage, MessageHandler
from gateway.feishu_support import (
    FeishuMessage,
    extract_message,
    load_lark,
    require_lark_oapi,
    send_reply,
    strip_mentions,
)


class FeishuBotGateway(Gateway):
    """Receive Feishu messages over WebSocket and reply through IM APIs."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        domain: str = "https://open.feishu.cn",
        log_level: str = "INFO",
        reply_mode: str = "card",
        max_concurrency: int = 4,
        queue_size: int = 100,
        allowed_open_ids: list[str] | None = None,
        allowed_chat_ids: list[str] | None = None,
        event_ttl_s: int = 600,
        max_reply_chars: int = 3500,
        **_: Any,
    ) -> None:
        require_lark_oapi()
        if not app_id or not app_secret:
            raise ValueError("FeishuBotGateway requires app_id and app_secret")
        if reply_mode not in {"card", "text"}:
            raise ValueError("reply_mode must be either 'card' or 'text'")
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain.rstrip("/")
        self.log_level = log_level.upper()
        self.reply_mode = reply_mode
        self.max_concurrency = max(1, int(max_concurrency))
        self.queue_size = max(1, int(queue_size))
        self.allowed_open_ids = set(allowed_open_ids or [])
        self.allowed_chat_ids = set(allowed_chat_ids or [])
        self.event_ttl_s = max(1, int(event_ttl_s))
        self.max_reply_chars = max(500, int(max_reply_chars))

        self._handler: MessageHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._stop_event: asyncio.Event | None = None
        self._workers: list[asyncio.Task] = []
        self._thread: Thread | None = None
        self._thread_ready = Event()
        self._thread_error: BaseException | None = None
        self._stopping = False
        self._seen: dict[str, float] = {}
        self._lark: Any = None
        self._api_client: Any = None
        self._ws_client: Any = None
        self._ws_loop: Any = None
        self._logger = logging.getLogger(__name__)

    async def start(self, handler: MessageHandler) -> None:
        self._handler = handler
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self.queue_size)
        self._stop_event = asyncio.Event()
        self._stopping = False
        self._workers = [
            asyncio.create_task(self._worker_loop(), name=f"feishu-worker-{idx}")
            for idx in range(self.max_concurrency)
        ]
        self._thread = Thread(target=self._run_ws_client, name="feishu-ws", daemon=True)
        self._thread.start()
        ready = await asyncio.to_thread(self._thread_ready.wait, 5)
        if not ready:
            await self.stop()
            raise RuntimeError("Feishu long connection did not start within 5 seconds")
        if self._thread_error is not None:
            await self.stop()
            raise RuntimeError(
                f"Feishu long connection failed: {self._thread_error}"
            ) from self._thread_error
        self._logger.info(
            "feishu_bot_started domain=%s reply_mode=%s", self.domain, self.reply_mode
        )
        await self._stop_event.wait()
        if self._thread_error is not None and not self._stopping:
            raise RuntimeError(
                f"Feishu long connection stopped: {self._thread_error}"
            ) from self._thread_error

    async def stop(self) -> None:
        self._stopping = True
        if self._stop_event is not None:
            self._stop_event.set()
        self._stop_ws_client()
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        if self._thread and self._thread.is_alive():
            await asyncio.to_thread(self._thread.join, 1)

    def _run_ws_client(self) -> None:
        try:
            self._lark = load_lark()
            level = self._sdk_log_level()
            builder = self._lark.EventDispatcherHandler.builder("", "", level)
            event_handler = builder.register_p2_im_message_receive_v1(
                self._on_message_event
            ).build()
            client_builder = (
                self._lark.Client.builder()
                .app_id(self.app_id)
                .app_secret(self.app_secret)
                .domain(self.domain)
                .log_level(level)
            )
            self._api_client = client_builder.build()
            self._ws_client = self._lark.ws.Client(
                self.app_id,
                self.app_secret,
                log_level=level,
                event_handler=event_handler,
                domain=self.domain,
                auto_reconnect=True,
            )
            self._ws_loop = getattr(importlib.import_module("lark_oapi.ws.client"), "loop", None)
            self._thread_ready.set()
            self._ws_client.start()
            if not self._stopping:
                self._thread_error = RuntimeError("SDK websocket client exited unexpectedly")
        except BaseException as exc:
            if not self._stopping:
                self._thread_error = exc
                self._logger.exception("feishu_ws_client_failed")
        finally:
            self._thread_ready.set()
            if self._loop and self._stop_event and not self._stopping:
                self._loop.call_soon_threadsafe(self._stop_event.set)

    def _sdk_log_level(self) -> Any:
        levels = getattr(self._lark, "LogLevel", None)
        if levels is None:
            return None
        return getattr(levels, self.log_level, getattr(levels, "INFO", None))

    def _stop_ws_client(self) -> None:
        client = self._ws_client
        if client is None:
            return
        for method_name in ("close", "stop"):
            method = getattr(client, method_name, None)
            if callable(method):
                try:
                    method()
                    return
                except Exception:
                    self._logger.exception("feishu_ws_client_%s_failed", method_name)
        disconnect = getattr(client, "_disconnect", None)
        if callable(disconnect) and self._ws_loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(disconnect(), self._ws_loop)
                self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)
            except Exception:
                self._logger.exception("feishu_ws_client_private_stop_failed")

    def _on_message_event(self, event: Any) -> None:
        if self._loop is None:
            self._logger.warning("feishu_event_dropped reason=no_loop")
            return
        self._loop.call_soon_threadsafe(self._enqueue_event, event)

    def _enqueue_event(self, event: Any) -> None:
        if self._queue is None or self._stopping:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._logger.warning(
                "feishu_event_dropped reason=queue_full queue_size=%s", self.queue_size
            )

    async def _worker_loop(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            try:
                await self._process_event(event)
            except Exception:
                self._logger.exception("feishu_event_processing_failed")
            finally:
                self._queue.task_done()

    async def _process_event(self, event: Any) -> None:
        message = extract_message(event)
        if message is None:
            return
        dedupe_key = message.event_id or message.message_id
        if dedupe_key and self._is_duplicate(dedupe_key):
            self._logger.info("feishu_event_ignored reason=duplicate key=%s", dedupe_key)
            return
        if not self._is_allowed(message):
            self._logger.warning(
                "feishu_event_ignored reason=not_allowed chat_id=%s open_id=%s",
                message.chat_id,
                message.sender_open_id,
            )
            return
        if message.message_type != "text":
            await self._reply(message.message_id, "Evidune 当前只支持文本消息。")
            return
        text = strip_mentions(message.text, message.mentions)
        if not text:
            await self._reply(message.message_id, "请发送文本内容。")
            return
        if self._handler is None:
            return
        inbound = InboundMessage(
            text=text,
            sender_id=message.sender_open_id or "unknown",
            channel="feishu",
            conversation_id=message.chat_id or message.sender_open_id or "feishu",
            metadata={
                "message_id": message.message_id,
                "chat_id": message.chat_id,
                "chat_type": message.chat_type,
                "open_id": message.sender_open_id,
                "event_id": message.event_id,
            },
        )
        try:
            response = await self._handler(inbound)
            await self._reply(message.message_id, response.text)
        except Exception as exc:
            self._logger.exception("feishu_agent_handler_failed")
            await self._reply(message.message_id, f"[Error] {exc}")

    def _is_allowed(self, message: FeishuMessage) -> bool:
        if self.allowed_open_ids and message.sender_open_id not in self.allowed_open_ids:
            return False
        return not (self.allowed_chat_ids and message.chat_id not in self.allowed_chat_ids)

    def _is_duplicate(self, key: str) -> bool:
        now = time.monotonic()
        expired = [seen for seen, deadline in self._seen.items() if deadline <= now]
        for seen in expired:
            self._seen.pop(seen, None)
        if key in self._seen:
            return True
        self._seen[key] = now + self.event_ttl_s
        return False

    async def _reply(self, message_id: str, text: str) -> None:
        await send_reply(
            lark=self._lark,
            api_client=self._api_client,
            logger=self._logger,
            message_id=message_id,
            text=text,
            reply_mode=self.reply_mode,
            max_reply_chars=self.max_reply_chars,
        )
