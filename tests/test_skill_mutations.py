"""Tests for known-bad Skill mutation operators."""

import pytest

from skills.mutations import mutate_skill, mutation_operators

SKILL = """---
name: deploy
description: Deploy safely when asked
---

1. Check the current state.
2. Run `deploy_tool`.
3. Never write without permission.
4. Verify the deployment.
"""


@pytest.mark.parametrize("operator", mutation_operators())
def test_each_mutation_is_explicit_and_changes_the_fixture(operator: str):
    mutation = mutate_skill(SKILL, operator)

    assert mutation.operator == operator
    assert mutation.changed is True
    assert mutation.content != SKILL
    assert mutation.description


def test_unknown_mutation_is_rejected():
    with pytest.raises(ValueError, match="Unknown Skill mutation"):
        mutate_skill(SKILL, "unknown")


def test_remove_verification_keeps_frontmatter_but_removes_all_body_checks():
    mutation = mutate_skill(
        "---\nname: verify\ndescription: Verify records\n---\n"
        "Check the input.\nRun the action.\nConfirm the output.\n",
        "remove_verification",
    )

    assert "description: Verify records" in mutation.content
    assert "Check the input" not in mutation.content
    assert "Confirm the output" not in mutation.content
