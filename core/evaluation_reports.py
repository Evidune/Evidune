"""Human-readable and CI report rendering for evaluation experiments."""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import quoteattr

from adapters.benchmark import write_json
from core.evaluation_models import ExperimentRunSummary, utc_now


class EvaluationReportMixin:
    @staticmethod
    def _write_manifest(**context: Any) -> None:
        artifact_dir = context.pop("artifact_dir")
        corpus = context.pop("corpus")
        adapter = context.pop("adapter")
        tasks = context.pop("tasks")
        variants = context.pop("variants")
        write_json(
            artifact_dir / "manifest.json",
            {
                **context,
                "corpus_id": corpus.corpus_id,
                "corpus_manifest": corpus.manifest_path,
                "corpus_digest": corpus.manifest_digest,
                "adapter": {"id": adapter.adapter_id, "revision": adapter.revision},
                "task_ids": [task.id for task in tasks],
                "variants": [
                    {
                        "name": item.name,
                        "version": item.version,
                        "digest": item.digest,
                        "mutation_operator": item.mutation_operator,
                    }
                    for item in variants
                ],
                "budget": corpus.budget,
                "created_at": utc_now(),
            },
        )

    def _summary_markdown(self, summary: ExperimentRunSummary) -> str:
        lines = [
            f"# Evaluation experiment {summary.experiment_id}",
            "",
            f"- Corpus: `{summary.corpus_id}`",
            f"- Split: `{summary.split}`",
            f"- Status: `{summary.status}`",
            f"- Valid trials: {summary.valid_trials}/{summary.planned_trials}",
            f"- Invalid trials: {summary.invalid_trials}",
            f"- Governance: `{summary.governance.get('verdict', 'unknown')}`",
            "",
            "## Variants",
            "",
        ]
        for name, counts in summary.variant_counts.items():
            lines.append(
                f"- `{name}`: valid={counts['valid']} invalid={counts['invalid']} "
                f"pass={counts['pass']} fail={counts['fail']}"
            )
        return "\n".join(lines) + "\n"

    def _junit(self, experiment_id: str) -> str:
        trials = self.memory.list_experiment_trials(experiment_id)
        passing_statuses = {"passed", "mutation_killed"}
        failures = sum(1 for trial in trials if trial["status"] not in passing_statuses)
        cases = []
        for trial in trials:
            name = f"{trial['task_ref']}[{trial['variant']}#{trial['trial_number']}]"
            if trial["status"] in passing_statuses:
                cases.append(f'  <testcase classname="evidune.eval" name={quoteattr(name)} />')
            else:
                message = str(trial["classification"] or trial["status"])
                cases.append(
                    f'  <testcase classname="evidune.eval" name={quoteattr(name)}>'
                    f"<failure message={quoteattr(message)} /></testcase>"
                )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<testsuite name={quoteattr(experiment_id)} tests="{len(trials)}" '
            f'failures="{failures}">\n' + "\n".join(cases) + "\n</testsuite>\n"
        )
