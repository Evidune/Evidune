"""Tests for the WebGateway feedback handling logic."""

from pathlib import Path
from threading import Thread

import pytest

from gateway.web import WebGateway
from memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def gateway(store: MemoryStore) -> WebGateway:
    gw = WebGateway()
    gw.set_memory_store(store)
    return gw


class TestHandleFeedback:
    def test_thumbs_up_persists(self, gateway: WebGateway, store: MemoryStore):
        eid = store.record_execution(skill_name="s", user_input="i", assistant_output="o")
        result = gateway._handle_feedback(
            {"execution_id": eid, "signal": "thumbs_up", "value": True}
        )
        assert result["ok"] is True
        assert result["signals"] == {"thumbs_up": True}

        execs = store.get_skill_executions("s")
        assert execs[0]["signals"] == {"thumbs_up": True}

    def test_multiple_signals_merge(self, gateway: WebGateway, store: MemoryStore):
        eid = store.record_execution(skill_name="s", user_input="i", assistant_output="o")
        gateway._handle_feedback({"execution_id": eid, "signal": "thumbs_up", "value": True})
        result = gateway._handle_feedback({"execution_id": eid, "signal": "copied", "value": True})
        assert result["signals"] == {"thumbs_up": True, "copied": True}

    def test_signal_overwrites_same_type(self, gateway: WebGateway, store: MemoryStore):
        eid = store.record_execution(skill_name="s", user_input="i", assistant_output="o")
        gateway._handle_feedback({"execution_id": eid, "signal": "thumbs_up", "value": True})
        result = gateway._handle_feedback(
            {"execution_id": eid, "signal": "thumbs_up", "value": False}
        )
        assert result["signals"] == {"thumbs_up": False}

    def test_missing_execution_id(self, gateway: WebGateway):
        result = gateway._handle_feedback({"signal": "thumbs_up"})
        assert "error" in result

    def test_missing_signal_field(self, gateway: WebGateway, store: MemoryStore):
        eid = store.record_execution(skill_name="s", user_input="i", assistant_output="o")
        result = gateway._handle_feedback({"execution_id": eid})
        assert "error" in result

    def test_unknown_execution_id(self, gateway: WebGateway):
        result = gateway._handle_feedback(
            {"execution_id": 99999, "signal": "thumbs_up", "value": True}
        )
        assert "error" in result
        assert "not found" in result["error"]

    def test_no_memory_store_configured(self):
        gw = WebGateway()  # no set_memory_store called
        result = gw._handle_feedback({"execution_id": 1, "signal": "thumbs_up", "value": True})
        assert "error" in result


class TestConversationEndpoints:
    def test_list_conversations_empty(self, gateway: WebGateway):
        assert gateway._list_conversations() == []

    def test_list_conversations_filters_channel(self, gateway: WebGateway, store: MemoryStore):
        # Seed one web + one cli conversation
        store.ensure_conversation("c-web", channel="web")
        store.add_message("c-web", "user", "hi")
        store.ensure_conversation("c-cli", channel="cli")
        store.add_message("c-cli", "user", "hi")

        result = gateway._list_conversations()
        ids = {c["id"] for c in result}
        assert ids == {"c-web"}  # scoped to channel=web

    def test_conversation_history_found(self, gateway: WebGateway, store: MemoryStore):
        store.ensure_conversation("c1", channel="web")
        store.set_conversation_mode("c1", "plan")
        store.update_conversation_plan(
            "c1",
            items=[{"step": "Inspect the request", "status": "completed"}],
        )
        store.add_message("c1", "user", "hello")
        store.add_message("c1", "assistant", "world")
        result = gateway._conversation_history("c1")
        assert result["conversation"]["id"] == "c1"
        assert result["conversation"]["mode"] == "plan"
        assert result["conversation"]["plan"]["items"][0]["step"] == "Inspect the request"
        assert [m["role"] for m in result["messages"]] == ["user", "assistant"]

    def test_conversation_history_missing(self, gateway: WebGateway):
        result = gateway._conversation_history("nope")
        assert "error" in result

    def test_archive_sets_status(self, gateway: WebGateway, store: MemoryStore):
        store.add_message("c1", "user", "hi")
        result = gateway._set_status("c1", "archived")
        assert result["ok"] is True
        assert store.get_conversation("c1")["status"] == "archived"

    def test_delete_removes_row(self, gateway: WebGateway, store: MemoryStore):
        store.add_message("c1", "user", "hi")
        result = gateway._delete_conversation("c1")
        assert result["ok"] is True
        assert store.get_conversation("c1") is None

    def test_list_conversations_from_http_thread(self, gateway: WebGateway, store: MemoryStore):
        store.ensure_conversation("c-web", channel="web")
        store.add_message("c-web", "user", "hi")

        result: dict[str, object] = {}

        def worker() -> None:
            result["value"] = gateway._list_conversations()

        thread = Thread(target=worker)
        thread.start()
        thread.join()

        assert [c["id"] for c in result["value"]] == ["c-web"]
