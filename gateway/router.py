"""Router — starts all gateways and routes messages to AgentCore."""

from __future__ import annotations

import asyncio
from typing import Any

from agent.core import AgentCore
from gateway.base import Gateway, InboundMessage, OutboundMessage
from gateway.cli import CLIGateway
from gateway.feishu_bot import FeishuBotGateway
from gateway.web import WebGateway


def create_gateway(gateway_type: str, **config: Any) -> Gateway:
    """Factory function to create a gateway by type."""
    if gateway_type == "cli":
        return CLIGateway(**{k: v for k, v in config.items() if k in ("user_id",)})
    elif gateway_type == "feishu_bot":
        return FeishuBotGateway(**config)
    elif gateway_type == "web":
        return WebGateway(**{k: v for k, v in config.items() if k in ("port", "host")})
    else:
        raise ValueError(f"Unknown gateway type: {gateway_type}")


class Router:
    """Starts all gateways and routes messages to the agent."""

    def __init__(self, agent: AgentCore, gateways: list[Gateway]) -> None:
        self.agent = agent
        self.gateways = gateways

    async def _handle(self, message: InboundMessage) -> OutboundMessage:
        return await self.agent.handle(message)

    async def start(self) -> None:
        """Start all gateways concurrently."""
        if not self.gateways:
            raise ValueError("No gateways configured")

        tasks = [asyncio.create_task(gw.start(self._handle)) for gw in self.gateways]

        # If only CLI, run it directly (blocking)
        if len(tasks) == 1:
            await tasks[0]
        else:
            # Run all gateways, stop when any completes (CLI exits)
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                t.result()

    async def stop(self) -> None:
        for gw in self.gateways:
            await gw.stop()
