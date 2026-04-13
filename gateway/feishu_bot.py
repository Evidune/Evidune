"""Feishu (Lark) bot gateway — receives messages via webhook, replies via API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

import httpx

from gateway.base import Gateway, InboundMessage, MessageHandler, OutboundMessage


class FeishuBotGateway(Gateway):
    """Feishu bot that receives events via HTTP webhook and replies via API.

    Config:
        app_id: Feishu app ID
        app_secret: Feishu app secret
        verification_token: Event subscription verification token
        port: HTTP server port (default 9000)
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        verification_token: str = "",
        encrypt_key: str = "",
        port: int = 9000,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.port = port
        self._handler: MessageHandler | None = None
        self._server: HTTPServer | None = None
        self._thread: Thread | None = None
        self._tenant_access_token: str = ""
        self._token_expires: float = 0

    async def start(self, handler: MessageHandler) -> None:
        self._handler = handler

        # Get initial token
        await self._refresh_token()

        # Start HTTP server in a thread
        gateway = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return

                # URL verification challenge
                if data.get("type") == "url_verification":
                    challenge = data.get("challenge", "")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"challenge": challenge}).encode())
                    return

                # Handle event
                self.send_response(200)
                self.end_headers()
                asyncio.get_event_loop().call_soon_threadsafe(
                    asyncio.create_task,
                    gateway._handle_event(data),
                )

            def log_message(self, format, *args):
                pass  # Suppress default logging

        self._server = HTTPServer(("0.0.0.0", self.port), RequestHandler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"Feishu bot listening on port {self.port}")

    async def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    async def _refresh_token(self) -> None:
        """Get tenant access token from Feishu."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=10,
            )
            data = resp.json()
            self._tenant_access_token = data.get("tenant_access_token", "")

    async def _handle_event(self, data: dict[str, Any]) -> None:
        """Process a Feishu event callback."""
        if not self._handler:
            return

        # Extract message from event
        event = data.get("event", {})
        header = data.get("header", {})
        event_type = header.get("event_type", "")

        if event_type != "im.message.receive_v1":
            return

        message = event.get("message", {})
        sender = event.get("sender", {}).get("sender_id", {})

        msg_type = message.get("message_type", "")
        if msg_type != "text":
            return  # Only handle text messages for now

        try:
            content = json.loads(message.get("content", "{}"))
            text = content.get("text", "")
        except json.JSONDecodeError:
            return

        if not text:
            return

        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        sender_id = sender.get("open_id", "unknown")

        inbound = InboundMessage(
            text=text,
            sender_id=sender_id,
            channel="feishu",
            conversation_id=chat_id,
            metadata={"message_id": message_id},
        )

        try:
            response = await self._handler(inbound)
            await self._reply(message_id, response.text)
        except Exception as e:
            await self._reply(message_id, f"[Error] {e}")

    async def _reply(self, message_id: str, text: str) -> None:
        """Reply to a Feishu message."""
        if not self._tenant_access_token:
            await self._refresh_token()

        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {self._tenant_access_token}"},
                json={
                    "content": json.dumps({"text": text}),
                    "msg_type": "text",
                },
                timeout=10,
            )
