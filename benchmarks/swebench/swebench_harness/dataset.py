"""SWE-bench Verified dataset loader.

We use HuggingFace `datasets` rather than pinning a JSON snapshot —
SWE-bench Verified is hosted at
``princeton-nlp/SWE-bench_Verified`` and the schema is stable.

A single instance has these fields that the harness cares about:

    instance_id            "django__django-11099"
    repo                   "django/django"
    base_commit            "0668164b4ac93a5be79f5b87fac1c661cb24bca9"
    problem_statement      <markdown of the GitHub issue>
    hints_text             <author commentary; we DO NOT include this in prompts>
    FAIL_TO_PASS           <JSON list of test names that should turn green>
    PASS_TO_PASS           <regression set; for scoring, NOT for the prompt>
    version                "3.1"
    environment_setup_commit  <may differ from base_commit; used by SWE-bench scorer>

The prompt builder reads `problem_statement`, `repo`, `base_commit`, and
`FAIL_TO_PASS`. `hints_text` is excluded by SWE-bench rules and not
fetched here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from datasets import load_dataset

VERIFIED_DATASET = "princeton-nlp/SWE-bench_Verified"
VERIFIED_SPLIT = "test"


@dataclass(slots=True)
class SWEBenchInstance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    fail_to_pass: list[str]

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.repo.split("/", 1)[1]

    @property
    def slug(self) -> str:
        """Filesystem-safe identifier — same as instance_id."""
        return self.instance_id


def _parse_fail_to_pass(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return [str(parsed)]
    return []


def load_verified_instances(
    *,
    instance_ids: list[str] | None = None,
    limit: int | None = None,
) -> Iterator[SWEBenchInstance]:
    """Yield SWE-bench Verified instances.

    `instance_ids`: if provided, only yield matching ids (preserves their
    order; missing ids are silently skipped, log if you care).
    `limit`: optional cap (applied AFTER instance_ids filter).
    """
    ds = load_dataset(VERIFIED_DATASET, split=VERIFIED_SPLIT)
    wanted = set(instance_ids) if instance_ids else None
    count = 0
    if wanted:
        # Index rows by instance_id so we yield in request order.
        rows_by_id: dict[str, dict[str, Any]] = {}
        for row in ds:
            iid = row["instance_id"]
            if iid in wanted:
                rows_by_id[iid] = row
        ordered = [rows_by_id[i] for i in instance_ids or [] if i in rows_by_id]
        for row in ordered:
            yield _row_to_instance(row)
            count += 1
            if limit is not None and count >= limit:
                return
    else:
        for row in ds:
            yield _row_to_instance(row)
            count += 1
            if limit is not None and count >= limit:
                return


def _row_to_instance(row: dict[str, Any]) -> SWEBenchInstance:
    return SWEBenchInstance(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        problem_statement=row["problem_statement"],
        fail_to_pass=_parse_fail_to_pass(row.get("FAIL_TO_PASS")),
    )
