"""Pinned third-party Skill source catalogs, separate from runnable task corpora."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adapters.benchmark import CorpusSource, source_checkout_path
from adapters.corpus import load_evaluation_corpus
from skills.governance import canonical_digest
from skills.loader import parse_skill


@dataclass(frozen=True)
class CatalogSkill:
    name: str
    source: str
    path: str
    review_status: str = "source_only"
    intended_fixture: str = ""
    fixture_manifest: str = ""
    fixture_task_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillCatalog:
    catalog_id: str
    schema_version: int
    sources: list[CorpusSource]
    skills: list[CatalogSkill]
    manifest_path: str
    manifest_digest: str


@dataclass(frozen=True)
class CatalogValidation:
    ok: bool
    catalog_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_skill_catalog(path: Path) -> SkillCatalog:
    manifest = path.resolve()
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Skill catalog root must be a mapping")
    sources = [
        CorpusSource(
            name=str(item.get("name") or ""),
            url=str(item.get("url") or ""),
            commit=str(item.get("commit") or ""),
            license=str(item.get("license") or ""),
            paths=[str(value) for value in item.get("paths", [])],
            digest=str(item.get("digest") or ""),
            mode=str(item.get("mode") or "git"),
        )
        for item in raw.get("sources", [])
        if isinstance(item, dict)
    ]
    skills = [
        CatalogSkill(
            name=str(item.get("name") or ""),
            source=str(item.get("source") or ""),
            path=str(item.get("path") or ""),
            review_status=str(item.get("review_status") or "source_only"),
            intended_fixture=str(item.get("intended_fixture") or ""),
            fixture_manifest=str(item.get("fixture_manifest") or ""),
            fixture_task_ids=[str(value) for value in item.get("fixture_task_ids", [])],
        )
        for item in raw.get("skills", [])
        if isinstance(item, dict)
    ]
    return SkillCatalog(
        catalog_id=str(raw.get("catalog_id") or ""),
        schema_version=int(raw.get("schema_version") or 1),
        sources=sources,
        skills=skills,
        manifest_path=str(manifest),
        manifest_digest=canonical_digest(raw),
    )


def catalog_source_root(catalog: SkillCatalog, base_dir: Path) -> Path:
    safe_id = "".join(
        character if character.isalnum() or character in "_.-" else "-"
        for character in catalog.catalog_id
    ).strip("-")
    return base_dir.resolve() / ".evidune" / "skill-catalogs" / safe_id / "sources"


def validate_skill_catalog(catalog: SkillCatalog, source_root: Path) -> CatalogValidation:
    errors: list[str] = []
    warnings: list[str] = []
    inspected: list[dict[str, Any]] = []
    if not catalog.catalog_id:
        errors.append("catalog_id is required")
    if catalog.schema_version != 1:
        errors.append(f"unsupported schema_version: {catalog.schema_version}")
    sources = {source.name: source for source in catalog.sources}
    if not sources:
        errors.append("at least one source is required")
    for source in sources.values():
        if source.mode != "git":
            errors.append(f"catalog source {source.name} must use git mode")
        is_commit = len(source.commit) == 40 and all(
            character in "0123456789abcdefABCDEF" for character in source.commit
        )
        if not is_commit:
            errors.append(f"catalog source {source.name} requires a full commit SHA")
        if not source.url or not source.license:
            errors.append(f"catalog source {source.name} requires URL and license")
    for item in catalog.skills:
        source = sources.get(item.source)
        if source is None:
            errors.append(f"Skill {item.name} references unknown source {item.source}")
            continue
        if item.path not in source.paths:
            errors.append(f"Skill {item.name} path is not declared by source {item.source}")
            continue
        checkout = source_checkout_path(source, source_root).resolve()
        path = (checkout / item.path).resolve()
        if not path.is_relative_to(checkout) or not path.is_file():
            errors.append(f"Skill {item.name} is not synced: {item.path}")
            continue
        try:
            parsed = parse_skill(path)
        except (ValueError, OSError) as exc:
            errors.append(f"Skill {item.name} cannot be parsed: {exc}")
            continue
        if parsed.name != item.name:
            errors.append(f"Skill name mismatch: catalog={item.name}, file={parsed.name}")
        fixture_details: dict[str, Any] = {}
        if item.review_status == "source_only":
            warnings.append(f"Skill {item.name} has no approved executable fixture pairing")
        elif item.review_status not in {"quarantined", "approved"}:
            errors.append(f"Skill {item.name} has invalid review_status {item.review_status}")
        elif item.review_status == "approved":
            fixture_details = _validate_approved_fixture(catalog, item, source, errors)
        inspected.append(
            {
                "name": item.name,
                "source": item.source,
                "path": item.path,
                "review_status": item.review_status,
                "version": parsed.version,
                **fixture_details,
            }
        )
    return CatalogValidation(
        ok=not errors,
        catalog_id=catalog.catalog_id,
        errors=errors,
        warnings=warnings,
        skills=inspected,
    )


def _validate_approved_fixture(
    catalog: SkillCatalog,
    item: CatalogSkill,
    source: CorpusSource,
    errors: list[str],
) -> dict[str, Any]:
    """Require an approved Skill to have a source-matched, declared fixture pairing."""
    if not item.fixture_manifest or not item.fixture_task_ids:
        errors.append(f"Approved Skill {item.name} requires fixture_manifest and fixture_task_ids")
        return {}
    catalog_dir = Path(catalog.manifest_path).parent.resolve()
    fixture_path = (catalog_dir / item.fixture_manifest).resolve()
    if not fixture_path.is_relative_to(catalog_dir) or not fixture_path.is_file():
        errors.append(f"Approved Skill {item.name} fixture manifest is missing or unsafe")
        return {}
    try:
        corpus = load_evaluation_corpus(fixture_path)
    except (OSError, ValueError) as exc:
        errors.append(f"Approved Skill {item.name} fixture cannot be loaded: {exc}")
        return {}
    matching_sources = [
        fixture_source
        for fixture_source in corpus.sources
        if fixture_source.url == source.url
        and fixture_source.commit == source.commit
        and item.path in fixture_source.paths
    ]
    if not matching_sources:
        errors.append(
            f"Approved Skill {item.name} fixture does not pin the catalog source and path"
        )
    pairings = [pairing for pairing in corpus.pairings if pairing.skill == item.name]
    paired_tasks = {task for pairing in pairings for task in pairing.tasks}
    missing_tasks = sorted(set(item.fixture_task_ids) - paired_tasks)
    if not pairings or missing_tasks:
        errors.append(
            f"Approved Skill {item.name} fixture pairing is missing tasks: "
            + ", ".join(missing_tasks or item.fixture_task_ids)
        )
    unknown_tasks = sorted(set(item.fixture_task_ids) - set(corpus.tasks))
    if unknown_tasks:
        errors.append(
            f"Approved Skill {item.name} fixture references unknown tasks: "
            + ", ".join(unknown_tasks)
        )
    return {
        "fixture_manifest": item.fixture_manifest,
        "fixture_task_ids": item.fixture_task_ids,
    }
