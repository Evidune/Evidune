"""Browser-driven validation for the web gateway."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.web_harness import WEB_INDEX, start_web_harness, wait_for

if not WEB_INDEX.exists():
    pytest.skip(
        "web/dist/index.html missing; run `cd web && npm run build` first", allow_module_level=True
    )

playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect
sync_playwright = playwright.sync_playwright
PlaywrightError = playwright.Error

pytestmark = pytest.mark.browser


@pytest.fixture
def web_harness(tmp_path: Path):
    harness = start_web_harness(tmp_path / "browser-memory.db")
    try:
        yield harness
    finally:
        harness.close()


@pytest.fixture
def browser_page():
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except PlaywrightError as exc:  # pragma: no cover - local env specific
            pytest.skip(f"Chromium unavailable in this environment: {exc}")
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def open_app(page, base_url: str) -> None:
    page.goto(base_url, wait_until="networkidle")
    expect(page.get_by_test_id("chat-input")).to_be_visible()
    expect(page.get_by_test_id("message-list")).to_be_visible()


def last_assistant_message(page):
    return page.locator("[data-testid='assistant-message']").last


def test_execute_mode_streaming_timeline(browser_page, web_harness) -> None:
    open_app(browser_page, web_harness.base_url)

    browser_page.get_by_test_id("chat-input").fill("Run the execute browser validation.")
    browser_page.get_by_test_id("send-button").click()

    expect(browser_page.locator("[data-testid='user-message-body']").last).to_have_text(
        "Run the execute browser validation."
    )

    assistant = last_assistant_message(browser_page)
    first_event = assistant.locator("[data-testid='task-event']").first
    expect(first_event).to_contain_text("Planner drafted the bounded swarm plan.")

    assistant_body = assistant.locator("[data-testid='assistant-message-body']")
    assert assistant_body.text_content() != "Streaming result ready."

    expect(assistant_body).to_have_text("Streaming result ready.")
    expect(assistant.get_by_test_id("task-timeline")).to_be_visible()
    expect(assistant.get_by_test_id("task-timeline-squad")).to_have_text("general")
    expect(assistant.get_by_test_id("task-timeline-status")).to_have_text("completed")
    expect(assistant.get_by_test_id("task-timeline-decision")).to_have_text("accept")
    expect(assistant.get_by_test_id("task-budget")).to_contain_text("rounds 1/2")

    convo = browser_page.get_by_test_id("conversation-item").filter(has_text="Execute validation")
    expect(convo).to_contain_text("Streaming result ready.")


def test_plan_mode_renders_and_persists(browser_page, web_harness) -> None:
    open_app(browser_page, web_harness.base_url)

    browser_page.get_by_test_id("mode-plan").click()
    browser_page.get_by_test_id("chat-input").fill("Plan the browser validation flow.")
    browser_page.get_by_test_id("send-button").click()

    plan_panel = browser_page.get_by_test_id("plan-panel")
    expect(plan_panel).to_be_visible()
    expect(plan_panel).to_contain_text("Validate the web gateway through a small browser plan.")
    expect(browser_page.get_by_test_id("plan-step").nth(0)).to_contain_text(
        "Open the app in plan mode"
    )
    expect(browser_page.get_by_test_id("plan-step").nth(1)).to_contain_text(
        "Render the persisted structured plan"
    )

    browser_page.reload(wait_until="networkidle")
    plan_conversation = browser_page.get_by_test_id("conversation-item").filter(
        has_text="Plan validation"
    )
    expect(plan_conversation).to_be_visible()
    plan_conversation.click()

    expect(browser_page.get_by_test_id("plan-panel")).to_be_visible()
    expect(browser_page.get_by_test_id("plan-panel")).to_contain_text(
        "Validate the web gateway through a small browser plan."
    )
    expect(browser_page.get_by_test_id("plan-step").nth(1)).to_contain_text(
        "Render the persisted structured plan"
    )


def test_feedback_submission_persists_signal(browser_page, web_harness) -> None:
    open_app(browser_page, web_harness.base_url)

    browser_page.get_by_test_id("chat-input").fill("Send the feedback validation response.")
    browser_page.get_by_test_id("send-button").click()

    assistant = last_assistant_message(browser_page)
    expect(assistant.locator("[data-testid='assistant-message-body']")).to_have_text(
        "Feedback-ready response."
    )

    assistant.hover()
    thumbs_down = assistant.get_by_test_id("feedback-thumbs-down")
    with browser_page.expect_response(
        lambda response: response.url.endswith("/api/feedback")
        and response.request.method == "POST"
    ) as response_info:
        thumbs_down.click()

    response = response_info.value
    assert response.ok
    assert response.json()["ok"] is True
    expect(thumbs_down).to_have_class(re.compile(r".*active.*"))

    execution_id = wait_for(lambda: web_harness.handler.feedback_execution_id)
    stored = wait_for(
        lambda: (web_harness.memory.get_skill_executions_by_id(execution_id) or {}).get(
            "signals", {}
        ),
    )
    assert stored == {"thumbs_down": True}
