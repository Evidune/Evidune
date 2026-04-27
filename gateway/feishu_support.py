"""Helpers for the Feishu long-connection gateway."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import re
from dataclasses import dataclass
from typing import Any

INSTALL_HINT = (
    "Feishu bot gateway requires optional dependency 'lark-oapi'. "
    'Install with `pip install -e ".[feishu]"` or `pip install "lark-oapi>=1.5.5,<2"`.'
)


@dataclass(frozen=True)
class FeishuMessage:
    message_id: str
    chat_id: str
    chat_type: str
    sender_open_id: str
    message_type: str
    text: str
    event_id: str
    mentions: list[Any]


def require_lark_oapi() -> None:
    if "lark_oapi" in importlib.sys.modules:
        return
    if importlib.util.find_spec("lark_oapi") is None:
        raise RuntimeError(INSTALL_HINT)


def load_lark() -> Any:
    try:
        return importlib.import_module("lark_oapi")
    except ModuleNotFoundError as exc:
        raise RuntimeError(INSTALL_HINT) from exc


def extract_message(event: Any) -> FeishuMessage | None:
    event_body = getattr(event, "event", None)
    if event_body is None:
        return None
    msg = getattr(event_body, "message", None)
    sender = getattr(event_body, "sender", None)
    if msg is None:
        return None
    sender_id = getattr(sender, "sender_id", None)
    open_id = getattr(sender_id, "open_id", "") or getattr(sender_id, "user_id", "")
    header = getattr(event, "header", None)
    event_id = getattr(header, "event_id", "") if header is not None else ""
    raw_text = ""
    if getattr(msg, "content", None):
        try:
            raw_text = str(json.loads(msg.content).get("text", ""))
        except json.JSONDecodeError:
            raw_text = ""
    return FeishuMessage(
        message_id=getattr(msg, "message_id", "") or "",
        chat_id=getattr(msg, "chat_id", "") or "",
        chat_type=getattr(msg, "chat_type", "") or "",
        sender_open_id=open_id or "",
        message_type=getattr(msg, "message_type", "") or "",
        text=raw_text,
        event_id=event_id or "",
        mentions=list(getattr(msg, "mentions", None) or []),
    )


def strip_mentions(text: str, mentions: list[Any]) -> str:
    cleaned = text
    for mention in mentions:
        key = getattr(mention, "key", "") or ""
        name = getattr(mention, "name", "") or ""
        if key:
            cleaned = cleaned.replace(key, "")
        if name:
            cleaned = cleaned.replace(f"@{name}", "")
    cleaned = re.sub(r"<at[^>]*>.*?</at>", "", cleaned)
    return cleaned.strip()


def chunks(text: str, size: int) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


def card_content(title: str, text: str) -> str:
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "elements": [
            {"tag": "markdown", "content": text},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "Evidune"}]},
        ],
    }
    return json.dumps(card, ensure_ascii=False)


async def send_reply(
    *,
    lark: Any,
    api_client: Any,
    logger: Any,
    message_id: str,
    text: str,
    reply_mode: str,
    max_reply_chars: int,
) -> None:
    if not message_id:
        logger.warning("feishu_reply_skipped reason=missing_message_id")
        return
    for idx, chunk in enumerate(chunks(text, max_reply_chars), 1):
        title = "Evidune" if len(text) <= max_reply_chars else f"Evidune ({idx})"
        if reply_mode == "card":
            sent = await send_sdk_reply(
                lark=lark,
                api_client=api_client,
                logger=logger,
                message_id=message_id,
                msg_type="interactive",
                content=card_content(title, chunk),
            )
            if sent:
                continue
            logger.warning("feishu_card_reply_failed_fallback_text message_id=%s", message_id)
        await send_sdk_reply(
            lark=lark,
            api_client=api_client,
            logger=logger,
            message_id=message_id,
            msg_type="text",
            content=json.dumps({"text": chunk}, ensure_ascii=False),
        )


async def send_sdk_reply(
    *,
    lark: Any,
    api_client: Any,
    logger: Any,
    message_id: str,
    msg_type: str,
    content: str,
) -> bool:
    if lark is None or api_client is None:
        logger.warning("feishu_reply_failed reason=sdk_not_ready message_id=%s", message_id)
        return False
    body = lark.im.v1.ReplyMessageRequestBody.builder().content(content).msg_type(msg_type).build()
    request = (
        lark.im.v1.ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    )
    try:
        response = await asyncio.to_thread(api_client.im.v1.message.reply, request)
    except Exception:
        logger.exception(
            "feishu_reply_request_failed message_id=%s msg_type=%s", message_id, msg_type
        )
        return False
    success = response.success() if callable(getattr(response, "success", None)) else False
    if not success:
        logger.warning(
            "feishu_reply_failed code=%s msg=%s log_id=%s",
            getattr(response, "code", None),
            getattr(response, "msg", None),
            response.get_log_id() if callable(getattr(response, "get_log_id", None)) else None,
        )
    return bool(success)
