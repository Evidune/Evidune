"""Tests for core/updater.py."""

from pathlib import Path

from core.updater import append_only, full_replace, replace_section, update_reference


class TestAppendOnly:
    def test_appends_new_lines(self):
        current = "# Existing\n- rule 1\n"
        new = "- rule 2\n- rule 3"
        result = append_only(current, new)
        assert "- rule 2" in result
        assert "- rule 3" in result
        assert result.startswith("# Existing")

    def test_skips_duplicate_lines(self):
        current = "# Doc\n- rule 1\n- rule 2\n"
        new = "- rule 1\n- rule 3"
        result = append_only(current, new)
        assert result.count("- rule 1") == 1
        assert "- rule 3" in result

    def test_no_change_when_all_exist(self):
        current = "- a\n- b\n"
        new = "- a\n- b"
        result = append_only(current, new)
        assert result == current

    def test_empty_current(self):
        result = append_only("", "- new line")
        assert "- new line" in result


class TestReplaceSection:
    def test_replaces_existing_section(self):
        current = """# Doc

## Section A
old content A

## Section B
old content B

## Section C
content C
"""
        new_section = "## Section B\nnew content B"
        result = replace_section(current, "## Section B", new_section)
        assert "new content B" in result
        assert "old content B" not in result
        assert "old content A" in result
        assert "content C" in result

    def test_appends_when_section_missing(self):
        current = "# Doc\n\n## Section A\ncontent A\n"
        new_section = "## Section B\nnew content B"
        result = replace_section(current, "## Section B", new_section)
        assert "new content B" in result
        assert "content A" in result

    def test_replaces_last_section(self):
        current = "# Doc\n\n## Section A\ncontent A\n\n## Section B\nold B\n"
        new_section = "## Section B\nnew B"
        result = replace_section(current, "## Section B", new_section)
        assert "new B" in result
        assert "old B" not in result

    def test_handles_nested_headings(self):
        current = """## Top
content

### Sub
sub content

## Next
next content
"""
        new_section = "## Top\nreplaced"
        result = replace_section(current, "## Top", new_section)
        assert "replaced" in result
        assert "next content" in result
        assert "sub content" not in result


class TestFullReplace:
    def test_replaces_all(self):
        result = full_replace("old content", "new content")
        assert result == "new content"


class TestUpdateReference:
    def test_creates_file_when_missing(self, tmp_path: Path):
        path = tmp_path / "new_doc.md"
        result = update_reference(path, "full_replace", "# New Doc\ncontent")
        assert result.has_changes
        assert path.read_text() == "# New Doc\ncontent"

    def test_no_changes_returns_false(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text("existing content")
        result = update_reference(path, "full_replace", "existing content")
        assert not result.has_changes

    def test_append_only_on_file(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text("- rule 1\n")
        result = update_reference(path, "append_only", "- rule 2")
        assert result.has_changes
        assert "- rule 2" in path.read_text()
        assert "- rule 1" in path.read_text()

    def test_replace_section_on_file(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text("## A\nold\n\n## B\nkeep\n")
        result = update_reference(path, "replace_section", "## A\nnew", section="## A")
        assert result.has_changes
        content = path.read_text()
        assert "new" in content
        assert "old" not in content
        assert "keep" in content

    def test_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "sub" / "dir" / "doc.md"
        result = update_reference(path, "full_replace", "content")
        assert result.has_changes
        assert path.exists()
