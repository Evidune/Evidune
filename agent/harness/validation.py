"""Playwright-backed UI validation for harness environments."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from agent.harness.runtime import RuntimeEnvironment


@dataclass
class ValidationConfig:
    headless: bool = True
    slow_mo_ms: int = 0


class ValidationHarness:
    """Manage live browser sessions keyed by runtime environment."""

    def __init__(self, config: ValidationConfig | None = None) -> None:
        self.config = config or ValidationConfig()
        self._drivers: dict[str, Any] = {}
        self._browsers: dict[str, Any] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._pages: dict[str, dict[str, Any]] = {}

    async def _ensure_session(self, environment: RuntimeEnvironment, session_id: str):
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise RuntimeError("Playwright is not installed") from exc

        if environment.environment_id not in self._drivers:
            driver = await async_playwright().start()
            browser = await driver.chromium.launch(
                headless=self.config.headless,
                slow_mo=self.config.slow_mo_ms,
            )
            self._drivers[environment.environment_id] = driver
            self._browsers[environment.environment_id] = browser
            self._contexts[environment.environment_id] = {}
            self._pages[environment.environment_id] = {}

        contexts = self._contexts[environment.environment_id]
        pages = self._pages[environment.environment_id]
        if session_id not in contexts:
            contexts[session_id] = await self._browsers[environment.environment_id].new_context(
                viewport={"width": 1440, "height": 960}
            )
        if session_id not in pages:
            pages[session_id] = await contexts[session_id].new_page()
        return pages[session_id]

    async def open_app(
        self,
        environment: RuntimeEnvironment,
        *,
        session_id: str = "default",
        path: str = "/",
        base_url: str = "",
    ) -> dict[str, Any]:
        target_url = base_url or environment.base_url
        if not target_url:
            health = environment.up()
            target_url = health["base_url"]
        page = await self._ensure_session(environment, session_id)
        await page.goto(target_url.rstrip("/") + path, wait_until="networkidle")
        title = await page.title()
        environment.observability.record(
            "trace",
            {
                "span_name": "validation.open_app",
                "status": "ok",
                "duration_ms": 0,
                "session_id": session_id,
                "url": page.url,
            },
        )
        return {"session_id": session_id, "url": page.url, "title": title}

    async def navigate_ui(
        self,
        environment: RuntimeEnvironment,
        *,
        session_id: str = "default",
        path: str = "",
        click_test_id: str = "",
        click_text: str = "",
        fill_test_id: str = "",
        fill_value: str = "",
        submit: bool = False,
        wait_for_text: str = "",
    ) -> dict[str, Any]:
        page = await self._ensure_session(environment, session_id)
        started = time.perf_counter()
        if path:
            await page.goto(environment.base_url.rstrip("/") + path, wait_until="networkidle")
        if fill_test_id:
            await page.get_by_test_id(fill_test_id).fill(fill_value)
        if click_test_id:
            await page.get_by_test_id(click_test_id).click()
        if click_text:
            await page.get_by_text(click_text).click()
        if submit:
            await page.keyboard.press("Enter")
        if wait_for_text:
            await page.get_by_text(wait_for_text).wait_for()
        duration_ms = int((time.perf_counter() - started) * 1000)
        environment.observability.record(
            "trace",
            {
                "span_name": "validation.navigate_ui",
                "status": "ok",
                "duration_ms": duration_ms,
                "session_id": session_id,
                "url": page.url,
            },
        )
        return {"session_id": session_id, "url": page.url, "duration_ms": duration_ms}

    async def snapshot_ui(
        self,
        environment: RuntimeEnvironment,
        *,
        session_id: str = "default",
    ) -> dict[str, Any]:
        page = await self._ensure_session(environment, session_id)
        title = await page.title()
        text_content = await page.locator("body").inner_text()
        test_ids = await page.locator("[data-testid]").evaluate_all(
            "(nodes) => nodes.map((node) => node.getAttribute('data-testid')).filter(Boolean)"
        )
        return {
            "session_id": session_id,
            "url": page.url,
            "title": title,
            "text_excerpt": text_content[:1000],
            "test_ids": test_ids,
        }

    async def capture_screenshot(
        self,
        environment: RuntimeEnvironment,
        *,
        session_id: str = "default",
        name: str = "validation",
        full_page: bool = True,
    ) -> dict[str, Any]:
        page = await self._ensure_session(environment, session_id)
        target = environment.artifacts_dir / f"{name}.png"
        await page.screenshot(path=str(target), full_page=full_page)
        environment.observability.record(
            "log",
            {
                "level": "info",
                "event": "validation.capture_screenshot",
                "message": "Captured validation screenshot",
                "path": str(target),
                "session_id": session_id,
            },
        )
        return {"session_id": session_id, "path": str(target), "url": page.url}

    async def assert_ui_state(
        self,
        environment: RuntimeEnvironment,
        *,
        session_id: str = "default",
        contains_text: str = "",
        visible_test_id: str = "",
        url_contains: str = "",
    ) -> dict[str, Any]:
        page = await self._ensure_session(environment, session_id)
        failures: list[str] = []
        if contains_text:
            body = await page.locator("body").inner_text()
            if contains_text not in body:
                failures.append(f"missing text: {contains_text}")
        if visible_test_id:
            visible = await page.get_by_test_id(visible_test_id).is_visible()
            if not visible:
                failures.append(f"test id not visible: {visible_test_id}")
        if url_contains and url_contains not in page.url:
            failures.append(f"url missing substring: {url_contains}")
        ok = not failures
        environment.observability.record(
            "metric",
            {
                "name": "validation.assertion",
                "value": 1 if ok else 0,
                "session_id": session_id,
                "url": page.url,
                "contains_text": contains_text,
                "visible_test_id": visible_test_id,
                "url_contains": url_contains,
            },
        )
        return {"ok": ok, "url": page.url, "failures": failures}

    async def close_environment(self, environment_id: str) -> None:
        for page in self._pages.pop(environment_id, {}).values():
            await page.close()
        for context in self._contexts.pop(environment_id, {}).values():
            await context.close()
        browser = self._browsers.pop(environment_id, None)
        if browser is not None:
            await browser.close()
        driver = self._drivers.pop(environment_id, None)
        if driver is not None:
            await driver.stop()
