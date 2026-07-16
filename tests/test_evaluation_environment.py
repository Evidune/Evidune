from core.evaluation_environment import safe_reset
from skills.benchmark import PreparedTask


def test_safe_reset_turns_cleanup_exceptions_into_invalid_environment_evidence(tmp_path):
    class BrokenAdapter:
        def reset(self, prepared):
            raise RuntimeError("cleanup failed")

    prepared = PreparedTask("corpus", object(), "development", str(tmp_path))

    result = safe_reset(BrokenAdapter(), prepared)

    assert result.ok is False
    assert "RuntimeError: cleanup failed" in result.reason
