"""Harness runtime, validation, delivery, and maintenance tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent.harness.delivery import DeliveryManager
from agent.harness.maintenance import MaintenanceSweepRunner
from agent.harness.runtime import RuntimeEnvironment
from agent.harness.validation import ValidationHarness
from agent.tools.base import Tool
from memory.store import MemoryStore


def harness_tools(
    *,
    memory: MemoryStore,
    task_id: str,
    environment: RuntimeEnvironment,
    validator: ValidationHarness | None = None,
    delivery_manager: DeliveryManager | None = None,
    maintenance_runner: MaintenanceSweepRunner | None = None,
    allow_mutation: bool = True,
) -> list[Tool]:
    """Structured harness tools bound to one runtime environment."""

    def _append_manifest(kind: str, payload: dict[str, Any]) -> None:
        task = memory.get_harness_task(task_id) or {}
        manifest = dict(task.get("artifact_manifest") or {})
        items = list(manifest.get(kind, []))
        items.append(payload)
        manifest[kind] = items[-20:]
        memory.update_harness_task(task_id, artifact_manifest=manifest)

    def _record_artifact(
        *,
        phase: str,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        accepted: bool = True,
    ) -> int:
        artifact_id = memory.record_harness_artifact(
            task_id,
            phase=phase,
            role="validator" if phase == "validation" else "operator",
            kind=kind,
            summary=summary,
            content=json.dumps(payload, ensure_ascii=False),
            accepted=accepted,
            meta=payload,
        )
        _append_manifest(kind, {"artifact_id": artifact_id, **payload})
        return artifact_id

    def _update_validation_summary(payload: dict[str, Any]) -> None:
        task = memory.get_harness_task(task_id) or {}
        current = dict(task.get("validation_summary") or {})
        current.update(payload)
        memory.update_harness_task(task_id, validation_summary=current)

    def _update_delivery_summary(payload: dict[str, Any]) -> None:
        task = memory.get_harness_task(task_id) or {}
        current = dict(task.get("delivery_summary") or {})
        current.update(payload)
        memory.update_harness_task(task_id, delivery_summary=current)

    async def environment_status() -> dict[str, Any]:
        status = await asyncio.to_thread(environment.status)
        memory.update_harness_task(
            task_id,
            environment_id=environment.environment_id,
            environment_status=status["service"]["status"],
        )
        return status

    async def up_app() -> dict[str, Any]:
        status = await asyncio.to_thread(environment.up)
        memory.update_harness_task(
            task_id,
            environment_id=environment.environment_id,
            environment_status=status["status"],
        )
        _record_artifact(
            phase="environment", kind="environment", summary="Environment started", payload=status
        )
        return status

    async def health_app() -> dict[str, Any]:
        status = await asyncio.to_thread(environment.health)
        memory.update_harness_task(
            task_id,
            environment_id=environment.environment_id,
            environment_status=status["status"],
        )
        return status

    async def restart_app() -> dict[str, Any]:
        status = await asyncio.to_thread(environment.restart)
        memory.update_harness_task(
            task_id,
            environment_id=environment.environment_id,
            environment_status=status["status"],
        )
        _record_artifact(
            phase="environment", kind="environment", summary="Environment restarted", payload=status
        )
        return status

    async def down_app() -> dict[str, Any]:
        status = await asyncio.to_thread(environment.down)
        memory.update_harness_task(
            task_id,
            environment_id=environment.environment_id,
            environment_status=status["status"],
        )
        return status

    async def query_logs(
        level: str = "", event: str = "", message: str = "", limit: int = 20
    ) -> list[dict]:
        return await asyncio.to_thread(
            environment.observability.query,
            "log",
            filters={"level": level, "event": event, "message": message},
            limit=limit,
        )

    async def query_metrics(name: str = "", session_id: str = "", limit: int = 20) -> list[dict]:
        return await asyncio.to_thread(
            environment.observability.query,
            "metric",
            filters={"name": name, "session_id": session_id},
            limit=limit,
        )

    async def query_traces(span_name: str = "", status: str = "", limit: int = 20) -> list[dict]:
        return await asyncio.to_thread(
            environment.observability.query,
            "trace",
            filters={"span_name": span_name, "status": status},
            limit=limit,
        )

    async def open_app(
        path: str = "/", session_id: str = "default", base_url: str = ""
    ) -> dict[str, Any]:
        if validator is None:
            raise RuntimeError("Validation harness not configured")
        result = await validator.open_app(
            environment, session_id=session_id, path=path, base_url=base_url
        )
        artifact_id = _record_artifact(
            phase="validation",
            kind="ui-open",
            summary=f"Opened app at {result['url']}",
            payload=result,
        )
        _update_validation_summary({"last_open": result, "last_artifact_id": artifact_id})
        return result

    async def navigate_ui(
        session_id: str = "default",
        path: str = "",
        click_test_id: str = "",
        click_text: str = "",
        fill_test_id: str = "",
        fill_value: str = "",
        submit: bool = False,
        wait_for_text: str = "",
    ) -> dict[str, Any]:
        if validator is None:
            raise RuntimeError("Validation harness not configured")
        result = await validator.navigate_ui(
            environment,
            session_id=session_id,
            path=path,
            click_test_id=click_test_id,
            click_text=click_text,
            fill_test_id=fill_test_id,
            fill_value=fill_value,
            submit=submit,
            wait_for_text=wait_for_text,
        )
        _record_artifact(
            phase="validation",
            kind="ui-nav",
            summary=f"Navigated to {result['url']}",
            payload=result,
        )
        _update_validation_summary({"last_navigation": result})
        return result

    async def snapshot_ui(session_id: str = "default") -> dict[str, Any]:
        if validator is None:
            raise RuntimeError("Validation harness not configured")
        result = await validator.snapshot_ui(environment, session_id=session_id)
        _record_artifact(
            phase="validation",
            kind="ui-snapshot",
            summary=f"Snapshot for {result['url']}",
            payload=result,
        )
        _update_validation_summary({"last_snapshot": result})
        return result

    async def capture_screenshot(
        session_id: str = "default", name: str = "validation"
    ) -> dict[str, Any]:
        if validator is None:
            raise RuntimeError("Validation harness not configured")
        result = await validator.capture_screenshot(environment, session_id=session_id, name=name)
        _record_artifact(
            phase="validation",
            kind="screenshot",
            summary=f"Screenshot saved to {result['path']}",
            payload=result,
        )
        _update_validation_summary({"last_screenshot": result})
        return result

    async def assert_ui_state(
        session_id: str = "default",
        contains_text: str = "",
        visible_test_id: str = "",
        url_contains: str = "",
    ) -> dict[str, Any]:
        if validator is None:
            raise RuntimeError("Validation harness not configured")
        result = await validator.assert_ui_state(
            environment,
            session_id=session_id,
            contains_text=contains_text,
            visible_test_id=visible_test_id,
            url_contains=url_contains,
        )
        _record_artifact(
            phase="validation",
            kind="assertion",
            summary="Validation passed" if result["ok"] else "Validation failed",
            payload=result,
            accepted=result["ok"],
        )
        _update_validation_summary(
            {"last_assertion": result, "status": "passed" if result["ok"] else "failed"}
        )
        return result

    async def delivery_submit(
        files: list[str] | None = None,
        branch: str = "",
        message: str = "",
        pr_title: str = "",
        pr_body: str = "",
    ) -> dict[str, Any]:
        if delivery_manager is None:
            raise RuntimeError("Delivery manager not configured")
        result = await asyncio.to_thread(
            delivery_manager.submit,
            environment,
            files=files or [],
            branch=branch,
            message=message,
            pr_title=pr_title,
            pr_body=pr_body,
        )
        _record_artifact(
            phase="delivery", kind="delivery", summary="Delivery pipeline executed", payload=result
        )
        _update_delivery_summary(result)
        return result

    async def list_review_comments() -> list[dict[str, Any]]:
        if delivery_manager is None:
            return []
        return await asyncio.to_thread(delivery_manager.list_review_comments, environment)

    async def add_review_comment(
        body: str, author: str = "agent", path: str = "", line: int = 0
    ) -> dict[str, Any]:
        if delivery_manager is None:
            raise RuntimeError("Delivery manager not configured")
        result = await asyncio.to_thread(
            delivery_manager.add_review_comment,
            environment,
            body=body,
            author=author,
            path=path,
            line=line,
        )
        _update_delivery_summary(
            {
                "review_comments": await asyncio.to_thread(
                    delivery_manager.list_review_comments, environment
                )
            }
        )
        return result

    async def respond_review_comment(comment_id: int, response: str) -> dict[str, Any]:
        if delivery_manager is None:
            raise RuntimeError("Delivery manager not configured")
        result = await asyncio.to_thread(
            delivery_manager.respond_review_comment,
            environment,
            comment_id=comment_id,
            response=response,
        )
        _update_delivery_summary(
            {
                "review_comments": await asyncio.to_thread(
                    delivery_manager.list_review_comments, environment
                )
            }
        )
        return result

    async def maintenance_sweep() -> dict[str, Any]:
        if maintenance_runner is None:
            raise RuntimeError("Maintenance runner not configured")
        result = await asyncio.to_thread(maintenance_runner.sweep)
        _record_artifact(
            phase="maintenance",
            kind="maintenance",
            summary="Maintenance sweep completed",
            payload=result,
        )
        return result

    tools = [
        Tool(
            name="environment_status",
            description="Get the structured status for the current runtime environment.",
            parameters={"type": "object", "properties": {}},
            handler=environment_status,
        ),
        Tool(
            name="query_logs",
            description="Query structured runtime logs emitted by the environment and validation harness.",
            parameters={
                "type": "object",
                "properties": {
                    "level": {"type": "string"},
                    "event": {"type": "string"},
                    "message": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
            handler=query_logs,
        ),
        Tool(
            name="query_metrics",
            description="Query structured validation and environment metrics.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "session_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
            handler=query_metrics,
        ),
        Tool(
            name="query_traces",
            description="Query structured runtime traces such as validation steps and service lifecycle spans.",
            parameters={
                "type": "object",
                "properties": {
                    "span_name": {"type": "string"},
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
            handler=query_traces,
        ),
        Tool(
            name="list_review_comments",
            description="List structured review comments captured for the current delivery session.",
            parameters={"type": "object", "properties": {}},
            handler=list_review_comments,
        ),
        Tool(
            name="maintenance_sweep",
            description="Run the structured maintenance sweep and return issues plus suggested follow-up tasks.",
            parameters={"type": "object", "properties": {}},
            handler=maintenance_sweep,
        ),
    ]
    if allow_mutation:
        tools.extend(
            [
                Tool(
                    name="up_app",
                    description="Start the isolated web runtime for this task.",
                    parameters={"type": "object", "properties": {}},
                    handler=up_app,
                ),
                Tool(
                    name="health_app",
                    description="Check runtime health for the current task environment.",
                    parameters={"type": "object", "properties": {}},
                    handler=health_app,
                ),
                Tool(
                    name="restart_app",
                    description="Restart the isolated web runtime for this task.",
                    parameters={"type": "object", "properties": {}},
                    handler=restart_app,
                ),
                Tool(
                    name="down_app",
                    description="Stop the isolated web runtime for this task.",
                    parameters={"type": "object", "properties": {}},
                    handler=down_app,
                ),
                Tool(
                    name="open_app",
                    description="Open the target web app in Playwright and return structured page metadata.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "default": "/"},
                            "session_id": {"type": "string", "default": "default"},
                            "base_url": {"type": "string", "default": ""},
                        },
                    },
                    handler=open_app,
                ),
                Tool(
                    name="navigate_ui",
                    description="Drive the active Playwright page using path, test id, text, and fill actions.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string", "default": "default"},
                            "path": {"type": "string", "default": ""},
                            "click_test_id": {"type": "string", "default": ""},
                            "click_text": {"type": "string", "default": ""},
                            "fill_test_id": {"type": "string", "default": ""},
                            "fill_value": {"type": "string", "default": ""},
                            "submit": {"type": "boolean", "default": False},
                            "wait_for_text": {"type": "string", "default": ""},
                        },
                    },
                    handler=navigate_ui,
                ),
                Tool(
                    name="snapshot_ui",
                    description="Capture structured UI state including URL, visible test ids, and text excerpt.",
                    parameters={
                        "type": "object",
                        "properties": {"session_id": {"type": "string", "default": "default"}},
                    },
                    handler=snapshot_ui,
                ),
                Tool(
                    name="capture_screenshot",
                    description="Save a screenshot to the current environment artifacts directory.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string", "default": "default"},
                            "name": {"type": "string", "default": "validation"},
                        },
                    },
                    handler=capture_screenshot,
                ),
                Tool(
                    name="assert_ui_state",
                    description="Assert UI conditions using page text, visible test ids, and URL fragments.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string", "default": "default"},
                            "contains_text": {"type": "string", "default": ""},
                            "visible_test_id": {"type": "string", "default": ""},
                            "url_contains": {"type": "string", "default": ""},
                        },
                    },
                    handler=assert_ui_state,
                ),
                Tool(
                    name="delivery_submit",
                    description="Create or update the delivery branch, commit changes, and optionally open a PR.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "files": {"type": "array", "items": {"type": "string"}},
                            "branch": {"type": "string", "default": ""},
                            "message": {"type": "string", "default": ""},
                            "pr_title": {"type": "string", "default": ""},
                            "pr_body": {"type": "string", "default": ""},
                        },
                    },
                    handler=delivery_submit,
                ),
                Tool(
                    name="add_review_comment",
                    description="Persist a structured review comment for the current delivery session.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "body": {"type": "string"},
                            "author": {"type": "string", "default": "agent"},
                            "path": {"type": "string", "default": ""},
                            "line": {"type": "integer", "default": 0},
                        },
                        "required": ["body"],
                    },
                    handler=add_review_comment,
                ),
                Tool(
                    name="respond_review_comment",
                    description="Mark a structured review comment as addressed with a response.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "comment_id": {"type": "integer"},
                            "response": {"type": "string"},
                        },
                        "required": ["comment_id", "response"],
                    },
                    handler=respond_review_comment,
                ),
            ]
        )
    return tools
