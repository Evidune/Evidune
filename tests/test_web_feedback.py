"""Tests for the WebGateway feedback handling logic."""

from pathlib import Path

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
