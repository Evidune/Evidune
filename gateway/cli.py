"""CLI gateway — interactive REPL for development and local use."""

from __future__ import annotations

import asyncio
import sys

from gateway.base import Gateway, InboundMessage, MessageHandler, OutboundMessage


class CLIGateway(Gateway):
    """Interactive command-line REPL gateway."""

    def __init__(self, user_id: str = "cli-user") -> None:
        self.user_id = user_id
        self._running = False

    async def start(self, handler: MessageHandler) -> None:
        self._running = True
        print("Aiflay Agent — type your message (Ctrl+C to quit)")
        print("-" * 50)

        conversation_id = f"cli-{self.user_id}"

        while self._running:
            try:
                # Read input (run in executor to not block event loop)
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("\n> ")
                )
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break

            line = line.strip()
            if not line:
                continue

            if line.lower() in ("/quit", "/exit"):
                print("Bye!")
                break

            message = InboundMessage(
                text=line,
                sender_id=self.user_id,
                channel="cli",
                conversation_id=conversation_id,
            )

            try:
                response = await handler(message)
                print(f"\n{response.text}")
            except Exception as e:
                print(f"\n[Error] {e}", file=sys.stderr)

    async def stop(self) -> None:
        self._running = False
