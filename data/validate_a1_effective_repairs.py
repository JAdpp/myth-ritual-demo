#!/usr/bin/env python3
"""Read-only final validator for the C1 -> A1 -> review -> repair chain.

This validator never calls a provider and never writes to any SQLite file.  It
opens all four ledgers with ``mode=ro`` and ``PRAGMA query_only=ON`` and then
reconstructs every stored hash/provenance edge for effective repair rows,
including iterative parents and archived lineage.  The repair job's stored
review is the causal review that produced the repair; the current review row
may instead be a later pass/revise re-review of the latest effective result.

An empty repair sidecar is structurally valid by default.  Use
``--require-count`` and/or ``--require-all-reviewed-revisions`` when this is a
coverage gate rather than a structural/integrity gate.  Add
``--require-effective-rereview-pass`` for final semantic-loop closure.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

try:  # package imports in tests
    from data import repair_a1_from_semantic_reviews as repair_worker
    from data import review_a1_semantics as review_worker
    from data.validate_a1_annotations import (
        evidence_semantic_issues,
        generated_text_issues,
        safety_quality_issues,
        source_semantic_issues,
        status_semantic_issues,
    )
except ImportError:  # direct ``python data/validate_a1_effective_repairs.py``
    import repair_a1_from_semantic_reviews as repair_worker  # type: ignore[no-redef]
    import review_a1_semantics as review_worker  # type: ignore[no-redef]
    from validate_a1_annotations import (  # type: ignore[no-redef]
        evidence_semantic_issues,
        generated_text_issues,
        safety_quality_issues,
        source_semantic_issues,
        status_semantic_issues,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT / "data" / "corpus" / "c1_single_story" / "catalog.sqlite3"
DEFAULT_ANNOTATION_DB = (
    ROOT / "data" / "corpus" / "a1_retrieval_annotations" / "annotations.sqlite3"
)
DEFAULT_REVIEW_DB = ROOT / "data" / "corpus" / "a1_semantic_reviews" / "reviews.sqlite3"
DEFAULT_EFFECTIVE_DB = (
    ROOT / "data" / "corpus" / "a1_effective_repairs" / "effective.sqlite3"
)
DEFAULT_CANONICAL_SCHEMA = ROOT / "contracts" / "a1-retrieval-annotation.schema.json"
DEFAULT_REVIEW_SCHEMA = ROOT / "contracts" / "a1-semantic-review.schema.json"
DEFAULT_ENVELOPE_SCHEMA = ROOT / "contracts" / "a1-effective-repair-envelope.schema.json"

EXPECTED_REVIEW_MODEL = "deepseek-v4-flash"
EXPECTED_REPAIR_MODEL = "deepseek-v4-flash"
SQLITE_BUSY_TIMEOUT_MS = 30_000
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
FINAL_REPAIR_STATUS = "valid"

SOURCE_COLUMNS = {
    "entry_id",
    "source_work_id",
    "source_work_title",
    "source_work_period",
    "volume",
    "source_locator",
    "entry_ordinal",
    "title",
    "source_url",
    "char_count",
    "text",
    "extracted_text_sha256",
    "dedupe_status",
    "canonical_entry_id",
    "runtime_eligible",
}

ANNOTATION_COLUMNS = {
    "entry_id",
    "source_text_sha256",
    "source_work_id",
    "source_work_title",
    "title",
    "char_count",
    "status",
    "annotation_json",
}

REVIEW_COLUMNS = {
    "entry_id",
    "source_text_sha256",
    "candidate_annotation_sha256",
    "source_work_id",
    "source_work_title",
    "title",
    "char_count",
    "status",
    "verdict",
    "review_json",
    "issues_json",
    "model",
    "provider_reported_model",
    "prompt_version",
    "prompt_sha256",
    "review_schema_sha256",
}

EFFECTIVE_COLUMNS = {
    "entry_id",
    "source_text_sha256",
    "canonical_root_sha256",
    "base_candidate_sha256",
    "parent_effective_sha256",
    "repair_iteration",
    "immediate_base_origin",
    "lineage_json",
    "review_sha256",
    "review_verdict",
    "review_prompt_version",
    "review_prompt_sha256",
    "review_schema_sha256",
    "source_work_id",
    "source_work_title",
    "title",
    "char_count",
    "input_json",
    "status",
    "base_annotation_json",
    "review_json",
    "raw_model_annotation_json",
    "normalized_annotation_json",
    "normalized_annotation_sha256",
    "effective_annotation_json",
    "effective_annotation_sha256",
    "effective_envelope_json",
    "raw_response_json",
    "error_kind",
    "error_message",
    "provider_response_id",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "model",
    "provider_reported_model",
    "prompt_version",
    "prompt_sha256",
    "canonical_schema_sha256",
    "effective_schema_sha256",
    "envelope_schema_sha256",
}

EFFECTIVE_HISTORY_COLUMNS = {
    "entry_id",
    "repair_iteration",
    "canonical_root_sha256",
    "base_candidate_sha256",
    "parent_effective_sha256",
    "review_sha256",
    "review_prompt_version",
    "review_prompt_sha256",
    "normalized_annotation_sha256",
    "effective_annotation_sha256",
    "effective_envelope_json",
    "lineage_json",
    "raw_response_json",
    "provider_response_id",
    "model",
    "provider_reported_model",
    "prompt_version",
    "prompt_sha256",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}


class ConfigurationError(RuntimeError):
    """A path, schema, option, or SQLite layout cannot be validated."""


@dataclass
class ValidationReport:
    repair_count: int = 0
    checked_repairs: int = 0
    required_revision_count: int = 0
    effective_rereview_pass_count: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    verdict_counts: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    total_error_count: int = 0
    max_errors: int = 100

    @property
    def passed(self) -> bool:
        return self.total_error_count == 0

    def fail(self, message: str) -> None:
        self.total_error_count += 1
        if len(self.errors) < self.max_errors:
            self.errors.append(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_json_object(raw: Any, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root is not an object")
    return value


def parse_json_array(raw: Any, *, label: str) -> list[Any]:
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, list):
        raise ValueError(f"{label} root is not an array")
    return value


def readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ConfigurationError(f"SQLite file does not exist: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def require_integrity(conn: sqlite3.Connection, label: str) -> None:
    rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
    if rows != ["ok"]:
        raise ConfigurationError(f"{label} integrity_check failed")


def require_columns(
    conn: sqlite3.Connection, table: str, required: set[str], label: str
) -> None:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if table not in tables:
        raise ConfigurationError(f"{label} is missing table {table}")
    columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
    missing = sorted(required - columns)
    if missing:
        raise ConfigurationError(f"{label}.{table} is missing columns: {','.join(missing)}")


def load_schema(path: Path, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise ConfigurationError(f"{label} does not exist: {path}")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise ConfigurationError(f"invalid {label}: {exc}") from exc
    return schema, sha256_file(path)


def schema_paths(errors: Sequence[Any]) -> str:
    paths: list[str] = []
    for error in errors[:3]:
        rendered = ".".join(str(part) for part in error.absolute_path) or "<root>"
        paths.append(rendered)
    return ",".join(paths)


def status_counts(conn: sqlite3.Connection, table: str) -> Counter[str]:
    return Counter(
        {
            str(row["status"]): int(row["count"])
            for row in conn.execute(
                f'SELECT status,COUNT(*) AS count FROM "{table}" GROUP BY status'
            )
        }
    )


def annotation_gate_errors(
    payload: Mapping[str, Any],
    *,
    source: sqlite3.Row,
    entry_id: str,
    source_hash: str,
) -> list[str]:
    """Run the maintained A1 deterministic semantic gates on one payload."""
    record = payload.get("annotation_record")
    if not isinstance(record, Mapping):
        return ["annotation_record is missing or not an object"]
    errors: list[str] = []
    _, source_text, source_issues = source_semantic_issues(
        record, source, entry_id, source_hash
    )
    errors.extend(code for code, _ in source_issues)
    errors.extend(code for code, _ in generated_text_issues(record))
    errors.extend(code for code, _ in evidence_semantic_issues(record, source_text))
    errors.extend(code for code, _ in safety_quality_issues(record, source_text))
    errors.extend(code for code, _ in status_semantic_issues(record))
    return errors


def expected_repair_input(
    *,
    source: sqlite3.Row,
    source_hash: str,
    base: Mapping[str, Any],
    base_hash: str,
    review: Mapping[str, Any],
    review_hash: str,
    review_prompt_version: str,
    review_prompt_sha256: str,
    review_schema_sha256: str,
    review_verdict: str,
    canonical_root_sha256: str,
    parent_effective_sha256: str | None,
    repair_iteration: int,
    immediate_base_origin: str,
) -> dict[str, Any]:
    return {
        "entryId": str(source["entry_id"]),
        "work": str(source["source_work_title"]),
        "volume": str(source["volume"]),
        "locator": str(source["source_locator"]),
        "title": str(source["title"]),
        "sourceTextSha256": source_hash,
        "baseCandidateSha256": base_hash,
        "canonicalRootSha256": canonical_root_sha256,
        "parentEffectiveSha256": parent_effective_sha256,
        "repairIteration": repair_iteration,
        "immediateBaseOrigin": immediate_base_origin,
        "reviewSha256": review_hash,
        "reviewProvenance": {
            "promptVersion": review_prompt_version,
            "promptSha256": review_prompt_sha256,
            "schemaSha256": review_schema_sha256,
        },
        "sourceText": str(source["text"]),
        "candidate": base,
        "reviewVerdict": review_verdict,
        "reviewIssues": review.get("issues"),
    }


def source_profile_errors(
    annotation: Mapping[str, Any],
    *,
    source: sqlite3.Row,
    base_profile: Mapping[str, Any],
) -> list[str]:
    try:
        record = annotation["annotation_record"]
        unit = record["unit"]
        profile = record["source_profile"]
    except (KeyError, TypeError):
        return ["source/unit objects are missing"]
    expected_profile = {
        "title": source["title"],
        "work_id": source["source_work_id"],
        "work": source["source_work_title"],
        "period": source["source_work_period"],
        "juan_or_section": source["volume"],
        "locator": source["source_locator"],
        "source_url_or_snapshot": source["source_url"],
        "source_version": base_profile.get("source_version"),
        "source_text": source["text"],
        "source_text_sha256": source["extracted_text_sha256"],
        "rights_status": base_profile.get("rights_status"),
    }
    errors: list[str] = []
    if unit.get("corpus_level") != "C1" or unit.get("unit_type") != "source_segment":
        errors.append("unit type is not current C1/source_segment")
    if unit.get("unit_id") != source["entry_id"]:
        errors.append("unit_id does not match C1")
    if unit.get("source_text_sha256") != source["extracted_text_sha256"]:
        errors.append("unit source hash does not match C1")
    if profile != expected_profile:
        errors.append("source_profile does not match current C1/base provenance")
    return errors


def automatic_boundary_errors(annotation: Mapping[str, Any]) -> list[str]:
    try:
        meta = annotation["annotation_record"]["annotation_meta"]
        human = meta["human_review"]
    except (KeyError, TypeError):
        return ["annotation_meta/human_review is missing"]
    errors: list[str] = []
    if meta.get("product_annotation_status") != "automatically_validated":
        errors.append("product status is not automatically_validated")
    if meta.get("research_review_status") != "not_reviewed":
        errors.append("research_review_status claims review")
    if meta.get("research_ready") is not False:
        errors.append("research_ready is not false")
    if meta.get("model_preannotation") is not True:
        errors.append("model_preannotation is not true")
    if human.get("reviewed") is not False:
        errors.append("human_review.reviewed is not false")
    return errors


def expected_effective_from_normalized(
    normalized: Mapping[str, Any],
    *,
    repair_provenance: Mapping[str, Any],
    verdict: str,
) -> dict[str, Any]:
    expected = copy.deepcopy(normalized)
    record = expected["annotation_record"]
    meta = record["annotation_meta"]
    meta["generator"]["prompt_version"] = repair_worker.REPAIR_PROMPT_VERSION
    meta["validation"]["schema_valid"] = False
    meta["repair_provenance"] = dict(repair_provenance)
    if verdict == "uncertain":
        meta["overall_confidence"] = "low"
        uncertainties = record["auto_safety_screen"]["uncertainties"]
        note = "语义审校结论仍不确定，本条仅完成自动修订，尚未经人工审核。"
        if note not in uncertainties:
            if len(uncertainties) < 6:
                uncertainties.append(note)
            else:
                uncertainties[-1] = note
    return expected


def validate_databases(
    *,
    source_db: Path,
    annotation_db: Path,
    review_db: Path,
    effective_db: Path,
    canonical_schema: Path = DEFAULT_CANONICAL_SCHEMA,
    review_schema: Path = DEFAULT_REVIEW_SCHEMA,
    envelope_schema: Path = DEFAULT_ENVELOPE_SCHEMA,
    require_count: int | None = None,
    require_all_reviewed_revisions: bool = False,
    require_effective_rereview_pass: bool = False,
    expected_review_model: str = EXPECTED_REVIEW_MODEL,
    expected_review_prompt_version: str | None = None,
    expected_review_prompt_sha256: str | None = None,
    expected_repair_model: str = EXPECTED_REPAIR_MODEL,
    max_errors: int = 100,
) -> ValidationReport:
    if require_count is not None and require_count < 0:
        raise ConfigurationError("--require-count must be zero or positive")
    if max_errors <= 0:
        raise ConfigurationError("--max-errors must be positive")
    paths = {
        "source": source_db.resolve(),
        "annotation": annotation_db.resolve(),
        "review": review_db.resolve(),
        "effective": effective_db.resolve(),
    }
    if len(set(paths.values())) != 4:
        raise ConfigurationError("the four SQLite paths must be distinct")

    canonical_schema_json, canonical_schema_sha = load_schema(
        canonical_schema, "canonical A1 schema"
    )
    review_schema_json, review_schema_sha = load_schema(
        review_schema, "semantic-review schema"
    )
    envelope_schema_json, envelope_schema_sha = load_schema(
        envelope_schema, "effective envelope schema"
    )
    effective_schema_json = repair_worker.build_effective_schema(canonical_schema_json)
    try:
        Draft202012Validator.check_schema(effective_schema_json)
    except SchemaError as exc:
        raise ConfigurationError(f"invalid derived effective schema: {exc}") from exc
    effective_schema_sha = sha256_text(canonical_json(effective_schema_json))

    canonical_validator = Draft202012Validator(
        canonical_schema_json, format_checker=FormatChecker()
    )
    review_validator = Draft202012Validator(
        review_schema_json, format_checker=FormatChecker()
    )
    envelope_validator = Draft202012Validator(
        envelope_schema_json, format_checker=FormatChecker()
    )
    effective_validator = Draft202012Validator(
        effective_schema_json, format_checker=FormatChecker()
    )

    current_review_prompt_version = (
        expected_review_prompt_version or review_worker.PROMPT_VERSION
    )
    current_review_prompt_sha = expected_review_prompt_sha256 or sha256_text(
        review_worker.SYSTEM_PROMPT
    )
    if not HEX_SHA256.fullmatch(current_review_prompt_sha):
        raise ConfigurationError("expected review prompt hash is not SHA-256")
    repair_prompt_sha = sha256_text(repair_worker.SYSTEM_PROMPT)

    report = ValidationReport(max_errors=max_errors)
    with closing(readonly_connection(source_db)) as source_conn, closing(
        readonly_connection(annotation_db)
    ) as annotation_conn, closing(readonly_connection(review_db)) as review_conn, closing(
        readonly_connection(effective_db)
    ) as effective_conn:
        for conn, label in (
            (source_conn, "C1 source"),
            (annotation_conn, "canonical A1"),
            (review_conn, "semantic review"),
            (effective_conn, "effective repair"),
        ):
            require_integrity(conn, label)
        require_columns(source_conn, "entries", SOURCE_COLUMNS, "C1 source")
        require_columns(annotation_conn, "annotation_jobs", ANNOTATION_COLUMNS, "A1")
        require_columns(review_conn, "semantic_review_jobs", REVIEW_COLUMNS, "review")
        require_columns(
            effective_conn,
            "effective_repair_jobs",
            EFFECTIVE_COLUMNS,
            "effective repair",
        )
        require_columns(
            effective_conn,
            "effective_repair_history",
            EFFECTIVE_HISTORY_COLUMNS,
            "effective repair history",
        )

        report.repair_count = int(
            effective_conn.execute("SELECT COUNT(*) FROM effective_repair_jobs").fetchone()[0]
        )
        report.status_counts = status_counts(effective_conn, "effective_repair_jobs")
        invalid_states = {
            state: count
            for state, count in report.status_counts.items()
            if state != FINAL_REPAIR_STATUS
        }
        if invalid_states:
            report.fail(
                "effective sidecar has non-final states: "
                + canonical_json(dict(sorted(invalid_states.items())))
            )
        if require_count is not None and report.repair_count != require_count:
            report.fail(
                f"effective repair count {report.repair_count} != required {require_count}"
            )

        effective_ids = {
            str(row[0])
            for row in effective_conn.execute("SELECT entry_id FROM effective_repair_jobs")
        }
        if require_all_reviewed_revisions:
            required_ids = {
                str(row[0])
                for row in review_conn.execute(
                    """
                    SELECT entry_id FROM semantic_review_jobs
                    WHERE verdict IN ('revise','uncertain')
                      AND status=verdict
                    """
                )
            }
            report.required_revision_count = len(required_ids)
            missing = sorted(required_ids - effective_ids)
            if missing:
                report.fail(
                    f"{len(missing)} reviewed revise/uncertain rows lack effective repairs"
                )

        rows = effective_conn.execute(
            "SELECT * FROM effective_repair_jobs ORDER BY entry_id"
        )
        for job in rows:
            entry_id = str(job["entry_id"])
            report.checked_repairs += 1
            report.verdict_counts[str(job["review_verdict"])] += 1

            def fail(message: str) -> None:
                report.fail(f"{entry_id}: {message}")

            if job["status"] != FINAL_REPAIR_STATUS:
                fail(f"repair status is {job['status']!r}, not valid")

            source = source_conn.execute(
                "SELECT * FROM entries WHERE entry_id=?", (entry_id,)
            ).fetchone()
            if source is None:
                fail("missing C1 source row")
                continue
            source_text = str(source["text"])
            source_hash = sha256_text(source_text)
            if source["dedupe_status"] != "canonical" or int(source["runtime_eligible"]) != 1:
                fail("C1 row is not canonical/runtime eligible")
            if source["canonical_entry_id"] != entry_id:
                fail("C1 canonical_entry_id does not equal entry_id")
            if int(source["char_count"]) != len(source_text):
                fail("C1 char_count is stale")
            if source["extracted_text_sha256"] != source_hash:
                fail("C1 source hash is stale")

            a1_row = annotation_conn.execute(
                "SELECT * FROM annotation_jobs WHERE entry_id=?", (entry_id,)
            ).fetchone()
            review_row = review_conn.execute(
                "SELECT * FROM semantic_review_jobs WHERE entry_id=?", (entry_id,)
            ).fetchone()
            if a1_row is None:
                fail("missing canonical A1 row")
                continue
            if review_row is None:
                fail("missing semantic-review row")
                continue

            for label, row in (("A1", a1_row), ("review", review_row), ("repair", job)):
                for column in ("source_work_id", "source_work_title", "title", "char_count"):
                    if row[column] != source[column]:
                        fail(f"{label} {column} does not match C1")
            if a1_row["status"] != "valid":
                fail("canonical A1 status is not valid")
            if a1_row["source_text_sha256"] != source_hash:
                fail("canonical A1 source hash is stale")
            if review_row["source_text_sha256"] != source_hash:
                fail("semantic-review source hash is stale")
            if job["source_text_sha256"] != source_hash:
                fail("effective repair source hash is stale")

            try:
                canonical_base = parse_json_object(
                    a1_row["annotation_json"], label="A1 annotation_json"
                )
                immediate_base = parse_json_object(
                    job["base_annotation_json"], label="base_annotation_json"
                )
                current_review = parse_json_object(
                    review_row["review_json"], label="review_json"
                )
                repair_review = parse_json_object(
                    job["review_json"], label="repair review_json"
                )
                repair_input = parse_json_object(job["input_json"], label="input_json")
                raw_model = parse_json_object(
                    job["raw_model_annotation_json"], label="raw_model_annotation_json"
                )
                raw_response = parse_json_object(
                    job["raw_response_json"], label="raw_response_json"
                )
                normalized = parse_json_object(
                    job["normalized_annotation_json"], label="normalized_annotation_json"
                )
                effective = parse_json_object(
                    job["effective_annotation_json"], label="effective_annotation_json"
                )
                envelope = parse_json_object(
                    job["effective_envelope_json"], label="effective_envelope_json"
                )
                lineage = parse_json_array(job["lineage_json"], label="lineage_json")
            except ValueError as exc:
                fail(str(exc))
                continue

            for label, raw, value in (
                ("A1 annotation_json", a1_row["annotation_json"], canonical_base),
                ("base_annotation_json", job["base_annotation_json"], immediate_base),
                ("review row JSON", review_row["review_json"], current_review),
                ("repair review_json", job["review_json"], repair_review),
                ("input_json", job["input_json"], repair_input),
                ("raw_model_annotation_json", job["raw_model_annotation_json"], raw_model),
                ("raw_response_json", job["raw_response_json"], raw_response),
                ("normalized_annotation_json", job["normalized_annotation_json"], normalized),
                ("effective_annotation_json", job["effective_annotation_json"], effective),
                ("effective_envelope_json", job["effective_envelope_json"], envelope),
                ("lineage_json", job["lineage_json"], lineage),
            ):
                if raw != canonical_json(value):
                    fail(f"{label} is not canonical JSON")

            canonical_hash = sha256_text(canonical_json(canonical_base))
            immediate_hash = sha256_text(canonical_json(immediate_base))
            current_review_hash = sha256_text(canonical_json(current_review))
            repair_review_hash = sha256_text(canonical_json(repair_review))
            normalized_hash = sha256_text(canonical_json(normalized))
            effective_hash = sha256_text(canonical_json(effective))
            try:
                repair_iteration = int(job["repair_iteration"])
            except (TypeError, ValueError):
                fail("repair iteration is not an integer")
                continue
            parent_effective = (
                str(job["parent_effective_sha256"])
                if job["parent_effective_sha256"]
                else None
            )
            immediate_origin = str(job["immediate_base_origin"])

            if job["canonical_root_sha256"] != canonical_hash:
                fail("canonical root hash closure failed")
            if job["base_candidate_sha256"] != immediate_hash:
                fail("base candidate hash closure failed")
            if job["review_sha256"] != repair_review_hash:
                fail("repair review JSON hash closure failed")
            if job["normalized_annotation_sha256"] != normalized_hash:
                fail("normalized annotation hash closure failed")
            if job["effective_annotation_sha256"] != effective_hash:
                fail("effective annotation hash closure failed")

            if repair_iteration < 1:
                fail("repair iteration is less than one")
            if repair_iteration == 1:
                if immediate_origin != "canonical_a1" or parent_effective is not None:
                    fail("first repair does not originate from canonical A1")
                if immediate_base != canonical_base:
                    fail("first repair immediate base is not current canonical A1")
            else:
                if immediate_origin != "effective_repair":
                    fail("iterative repair does not originate from an effective repair")
                if parent_effective != immediate_hash:
                    fail("iterative repair parent hash does not equal immediate base hash")

            canonical_errors = sorted(
                canonical_validator.iter_errors(canonical_base),
                key=lambda error: list(error.absolute_path),
            )
            if canonical_errors:
                fail("canonical A1 schema failure at " + schema_paths(canonical_errors))
            base_validator = (
                canonical_validator if repair_iteration == 1 else effective_validator
            )
            immediate_schema_errors = sorted(
                base_validator.iter_errors(immediate_base),
                key=lambda error: list(error.absolute_path),
            )
            if immediate_schema_errors:
                fail("immediate base schema failure at " + schema_paths(immediate_schema_errors))
            normalized_schema_errors = sorted(
                canonical_validator.iter_errors(normalized),
                key=lambda error: list(error.absolute_path),
            )
            if normalized_schema_errors:
                fail("normalized A1 schema failure at " + schema_paths(normalized_schema_errors))
            effective_schema_errors = sorted(
                effective_validator.iter_errors(effective),
                key=lambda error: list(error.absolute_path),
            )
            if effective_schema_errors:
                fail("effective A1 schema failure at " + schema_paths(effective_schema_errors))
            if not list(canonical_validator.iter_errors(effective)):
                fail("effective repair incorrectly passes fixed first-pass A1 schema")
            for label, review_value in (
                ("repair review", repair_review),
                ("current review", current_review),
            ):
                review_schema_errors = sorted(
                    review_validator.iter_errors({"reviews": [review_value]}),
                    key=lambda error: list(error.absolute_path),
                )
                if review_schema_errors:
                    fail(f"{label} schema failure at " + schema_paths(review_schema_errors))
            envelope_schema_errors = sorted(
                envelope_validator.iter_errors(envelope),
                key=lambda error: list(error.absolute_path),
            )
            if envelope_schema_errors:
                fail("effective envelope schema failure at " + schema_paths(envelope_schema_errors))

            try:
                canonical_profile = canonical_base["annotation_record"]["source_profile"]
            except (KeyError, TypeError):
                canonical_profile = {}
            annotations_to_check = [("canonical base", canonical_base)]
            if repair_iteration > 1:
                annotations_to_check.append(("immediate effective base", immediate_base))
            annotations_to_check.extend(
                [("normalized", normalized), ("effective", effective)]
            )
            for label, annotation in annotations_to_check:
                for issue in source_profile_errors(
                    annotation, source=source, base_profile=canonical_profile
                ):
                    fail(f"{label}: {issue}")
                gate_errors = annotation_gate_errors(
                    annotation,
                    source=source,
                    entry_id=entry_id,
                    source_hash=source_hash,
                )
                if gate_errors:
                    fail(
                        f"{label} deterministic A1 gates failed: "
                        + ",".join(sorted(set(gate_errors)))
                    )
                for issue in automatic_boundary_errors(annotation):
                    fail(f"{label}: {issue}")

            repair_verdict = str(job["review_verdict"])
            if repair_verdict not in {"revise", "uncertain"}:
                fail("effective repair does not target revise/uncertain review")
            if (
                repair_review.get("entryId") != entry_id
                or repair_review.get("verdict") != repair_verdict
            ):
                fail("stored repair review identity/verdict does not match repair job")
            for issue in repair_review.get("issues", []):
                excerpt = issue.get("sourceExcerpt") if isinstance(issue, Mapping) else None
                if not isinstance(excerpt, str) or excerpt not in source_text:
                    fail("repair review issue excerpt is not exact current source text")

            current_verdict = str(review_row["verdict"] or "")
            if current_verdict not in {"pass", "revise", "uncertain"}:
                fail("current semantic review is not terminal")
            if review_row["status"] != current_verdict:
                fail("current semantic-review status does not equal verdict")
            if (
                current_review.get("entryId") != entry_id
                or current_review.get("verdict") != current_verdict
            ):
                fail("current review JSON identity/verdict does not match review row")
            try:
                current_issues = json.loads(review_row["issues_json"])
            except (TypeError, json.JSONDecodeError):
                fail("current review issues_json is invalid")
            else:
                if current_issues != current_review.get("issues"):
                    fail("current review issues_json does not match current review JSON")
                if review_row["issues_json"] != canonical_json(current_issues):
                    fail("current review issues_json is not canonical JSON")
            for issue in current_review.get("issues", []):
                excerpt = issue.get("sourceExcerpt") if isinstance(issue, Mapping) else None
                if not isinstance(excerpt, str) or excerpt not in source_text:
                    fail("current review issue excerpt is not exact current source text")

            current_candidate_hash = str(review_row["candidate_annotation_sha256"])
            current_is_causal_review = (
                current_candidate_hash == immediate_hash
                and current_review_hash == repair_review_hash
                and current_verdict == repair_verdict
            )
            current_is_effective_rereview = current_candidate_hash == effective_hash
            if not current_is_causal_review and not current_is_effective_rereview:
                fail("current semantic review targets neither repair base nor latest effective")
            if current_is_effective_rereview and current_verdict == "pass":
                report.effective_rereview_pass_count += 1
            if require_all_reviewed_revisions and current_verdict in {"revise", "uncertain"}:
                if not current_is_causal_review:
                    fail("current revise/uncertain review has not been repaired")
            if require_effective_rereview_pass and not (
                current_is_effective_rereview and current_verdict == "pass"
            ):
                fail("latest effective annotation lacks a current pass re-review")

            if review_row["model"] != expected_review_model:
                fail("review model is not current")
            if not str(review_row["provider_reported_model"] or "").strip():
                fail("review provider_reported_model is missing")
            if review_row["prompt_version"] != current_review_prompt_version:
                fail("review prompt_version is not current")
            if review_row["prompt_sha256"] != current_review_prompt_sha:
                fail("review prompt hash is not current")
            if review_row["review_schema_sha256"] != review_schema_sha:
                fail("review schema hash is not current")
            if job["review_prompt_version"] != current_review_prompt_version:
                fail("repair review prompt_version is not current")
            if job["review_prompt_sha256"] != current_review_prompt_sha:
                fail("repair review prompt hash is not current")
            if job["review_schema_sha256"] != review_schema_sha:
                fail("repair review schema hash is not current")

            if job["model"] != expected_repair_model:
                fail("repair requested model is not current")
            if not str(job["provider_reported_model"] or "").strip():
                fail("repair provider_reported_model is missing")
            if job["prompt_version"] != repair_worker.REPAIR_PROMPT_VERSION:
                fail("repair prompt_version is not current")
            if job["prompt_sha256"] != repair_prompt_sha:
                fail("repair prompt hash is not current")
            if job["canonical_schema_sha256"] != canonical_schema_sha:
                fail("canonical schema hash is not current")
            if job["effective_schema_sha256"] != effective_schema_sha:
                fail("derived effective schema hash is not current")
            if job["envelope_schema_sha256"] != envelope_schema_sha:
                fail("effective envelope schema hash is not current")

            try:
                generated_at = effective["annotation_record"]["annotation_meta"][
                    "generated_at"
                ]
            except (KeyError, TypeError):
                fail("effective annotation generated_at is missing")
                generated_at = None

            expected_input = expected_repair_input(
                source=source,
                source_hash=source_hash,
                base=immediate_base,
                base_hash=immediate_hash,
                review=repair_review,
                review_hash=repair_review_hash,
                review_prompt_version=str(job["review_prompt_version"]),
                review_prompt_sha256=str(job["review_prompt_sha256"]),
                review_schema_sha256=str(job["review_schema_sha256"]),
                review_verdict=repair_verdict,
                canonical_root_sha256=canonical_hash,
                parent_effective_sha256=parent_effective,
                repair_iteration=repair_iteration,
                immediate_base_origin=immediate_origin,
            )
            if repair_input != expected_input:
                fail("repair input_json does not match its immediate base/review inputs")

            if envelope.get("entry_id") != entry_id:
                fail("envelope entry_id does not match job")
            if envelope.get("source_text_sha256") != source_hash:
                fail("envelope source hash is stale")
            if envelope.get("canonical_root_sha256") != canonical_hash:
                fail("envelope canonical root hash closure failed")
            if envelope.get("base_candidate_sha256") != immediate_hash:
                fail("envelope base hash closure failed")
            if envelope.get("parent_effective_sha256") != parent_effective:
                fail("envelope parent effective hash is stale")
            if envelope.get("repair_iteration") != repair_iteration:
                fail("envelope repair iteration is stale")
            if envelope.get("immediate_base_origin") != immediate_origin:
                fail("envelope immediate base origin is stale")
            if envelope.get("lineage") != lineage:
                fail("envelope lineage does not match ledger lineage")
            if envelope.get("review_sha256") != repair_review_hash:
                fail("envelope review hash closure failed")
            if envelope.get("review_verdict") != repair_verdict:
                fail("envelope review verdict is stale")
            if envelope.get("normalized_annotation_sha256") != normalized_hash:
                fail("envelope normalized hash closure failed")
            if envelope.get("effective_annotation_sha256") != effective_hash:
                fail("envelope effective hash closure failed")
            if envelope.get("base_annotation") != immediate_base:
                fail("envelope base annotation is not current")
            if envelope.get("review") != repair_review:
                fail("envelope repair review does not match ledger")
            if envelope.get("normalized_a1_annotation") != normalized:
                fail("envelope normalized annotation does not match ledger")
            if envelope.get("effective_annotation") != effective:
                fail("envelope effective annotation does not match ledger")
            if envelope.get("human_reviewed") is not False:
                fail("envelope claims human review")
            if envelope.get("research_ready") is not False:
                fail("envelope claims research readiness")

            provenance = envelope.get("repair_provenance")
            if not isinstance(provenance, Mapping):
                fail("envelope repair_provenance is missing")
                provenance = {}
            expected_provenance = {
                "type": "llm_semantic_repair",
                "provider": "deepseek",
                "requested_model": job["model"],
                "provider_reported_model": job["provider_reported_model"],
                "prompt_version": repair_worker.REPAIR_PROMPT_VERSION,
                "prompt_sha256": repair_prompt_sha,
                "canonical_schema_sha256": canonical_schema_sha,
                "effective_schema_sha256": effective_schema_sha,
                "envelope_schema_sha256": envelope_schema_sha,
                "base_candidate_sha256": immediate_hash,
                "canonical_root_sha256": canonical_hash,
                "parent_effective_sha256": parent_effective,
                "repair_iteration": repair_iteration,
                "immediate_base_origin": immediate_origin,
                "review_sha256": repair_review_hash,
                "review_verdict": repair_verdict,
                "review_prompt_version": job["review_prompt_version"],
                "review_prompt_sha256": job["review_prompt_sha256"],
                "review_schema_sha256": job["review_schema_sha256"],
                "provider_response_id": job["provider_response_id"],
                "generated_at": generated_at,
                "normalizer": "data.annotate_c1_retrieval.normalise_annotation",
                "normalizer_local_repair_enabled": True,
                "automatic_semantic_repair": True,
                "human_reviewed": False,
            }
            if provenance != expected_provenance:
                fail("envelope repair provenance is not current/closed")

            try:
                meta_repair = effective["annotation_record"]["annotation_meta"][
                    "repair_provenance"
                ]
            except (KeyError, TypeError):
                fail("effective annotation repair_provenance is missing")
                meta_repair = {}
            expected_meta_repair = {
                "type": "llm_semantic_repair",
                "base_prompt_version": repair_worker.BASE_PROMPT_VERSION,
                "repair_prompt_version": repair_worker.REPAIR_PROMPT_VERSION,
                "repair_prompt_sha256": repair_prompt_sha,
                "base_candidate_sha256": immediate_hash,
                "review_sha256": repair_review_hash,
                "review_verdict": repair_verdict,
                "review_prompt_version": job["review_prompt_version"],
                "review_prompt_sha256": job["review_prompt_sha256"],
                "review_schema_sha256": job["review_schema_sha256"],
                "canonical_root_sha256": canonical_hash,
                "immediate_base_sha256": immediate_hash,
                "parent_effective_sha256": parent_effective,
                "repair_iteration": repair_iteration,
                "immediate_base_origin": immediate_origin,
                "prior_effective_sha256": [
                    item.get("effective_annotation_sha256")
                    for item in lineage[:-1]
                    if isinstance(item, Mapping)
                ],
                "automatic_semantic_repair": True,
                "human_reviewed": False,
                "canonical_schema_valid": False,
            }
            if meta_repair != expected_meta_repair:
                fail("effective annotation repair provenance is not current/closed")
            expected_effective = expected_effective_from_normalized(
                normalized,
                repair_provenance=expected_meta_repair,
                verdict=repair_verdict,
            )
            if effective != expected_effective:
                fail("effective annotation is not the deterministic normalized-to-effective transform")

            if len(lineage) != repair_iteration:
                fail("lineage depth does not equal repair iteration")
            previous_effective: str | None = None
            seen_effective: set[str] = set()
            for expected_iteration, item in enumerate(lineage, start=1):
                if not isinstance(item, Mapping):
                    fail("lineage contains a non-object item")
                    continue
                if item.get("repair_iteration") != expected_iteration:
                    fail("lineage iteration sequence is not contiguous")
                if item.get("canonical_root_sha256") != canonical_hash:
                    fail("lineage canonical root is stale")
                expected_parent = previous_effective
                expected_base_hash = canonical_hash if expected_iteration == 1 else previous_effective
                if item.get("parent_effective_sha256") != expected_parent:
                    fail("lineage parent hash chain is broken")
                if item.get("immediate_base_sha256") != expected_base_hash:
                    fail("lineage immediate base hash chain is broken")
                item_effective = item.get("effective_annotation_sha256")
                if not isinstance(item_effective, str) or item_effective in seen_effective:
                    fail("lineage effective hashes are missing or cyclic")
                else:
                    seen_effective.add(item_effective)
                    previous_effective = item_effective
            if previous_effective != effective_hash:
                fail("lineage does not terminate at latest effective annotation")

            if lineage and isinstance(lineage[-1], Mapping):
                expected_current_lineage = {
                    "repair_iteration": repair_iteration,
                    "canonical_root_sha256": canonical_hash,
                    "immediate_base_sha256": immediate_hash,
                    "parent_effective_sha256": parent_effective,
                    "review_sha256": repair_review_hash,
                    "review_prompt_version": job["review_prompt_version"],
                    "review_prompt_sha256": job["review_prompt_sha256"],
                    "review_schema_sha256": job["review_schema_sha256"],
                    "normalized_annotation_sha256": normalized_hash,
                    "effective_annotation_sha256": effective_hash,
                    "raw_model_annotation_sha256": sha256_text(canonical_json(raw_model)),
                    "raw_response_sha256": sha256_text(canonical_json(raw_response)),
                    "repair_prompt_version": repair_worker.REPAIR_PROMPT_VERSION,
                    "repair_prompt_sha256": repair_prompt_sha,
                    "requested_model": job["model"],
                    "provider_reported_model": job["provider_reported_model"],
                    "provider_response_id": job["provider_response_id"],
                    "prompt_tokens": job["prompt_tokens"],
                    "completion_tokens": job["completion_tokens"],
                    "total_tokens": job["total_tokens"],
                    "generated_at": generated_at,
                }
                if dict(lineage[-1]) != expected_current_lineage:
                    fail("latest lineage entry does not close to current repair artifacts")

            history_rows = effective_conn.execute(
                "SELECT * FROM effective_repair_history WHERE entry_id=?",
                (entry_id,),
            ).fetchall()
            history_by_hash = {
                str(row["effective_annotation_sha256"]): row for row in history_rows
            }
            for prior in lineage[:-1]:
                if not isinstance(prior, Mapping):
                    continue
                prior_hash = str(prior.get("effective_annotation_sha256") or "")
                history = history_by_hash.get(prior_hash)
                if history is None:
                    fail("lineage predecessor is absent from repair history")
                    continue
                try:
                    history_envelope = parse_json_object(
                        history["effective_envelope_json"], label="history envelope"
                    )
                    history_lineage = parse_json_array(
                        history["lineage_json"], label="history lineage"
                    )
                except ValueError as exc:
                    fail(str(exc))
                    continue
                try:
                    history_iteration = int(prior.get("repair_iteration"))
                except (TypeError, ValueError):
                    fail("history lineage repair iteration is invalid")
                    continue
                if history["repair_iteration"] != history_iteration:
                    fail("history repair iteration does not match lineage")
                if history_lineage != lineage[:history_iteration]:
                    fail("history lineage is not the current lineage prefix")
                if history_envelope.get("lineage") != history_lineage:
                    fail("history envelope lineage does not match history ledger")
                if history_envelope.get("effective_annotation_sha256") != prior_hash:
                    fail("history envelope effective hash is stale")
                history_schema_errors = sorted(
                    envelope_validator.iter_errors(history_envelope),
                    key=lambda error: list(error.absolute_path),
                )
                if history_schema_errors:
                    fail(
                        "history envelope schema failure at "
                        + schema_paths(history_schema_errors)
                    )

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only final validator for A1 effective semantic repairs"
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--annotation-db", type=Path, default=DEFAULT_ANNOTATION_DB)
    parser.add_argument("--review-db", type=Path, default=DEFAULT_REVIEW_DB)
    parser.add_argument("--effective-db", type=Path, default=DEFAULT_EFFECTIVE_DB)
    parser.add_argument("--canonical-schema", type=Path, default=DEFAULT_CANONICAL_SCHEMA)
    parser.add_argument("--review-schema", type=Path, default=DEFAULT_REVIEW_SCHEMA)
    parser.add_argument("--envelope-schema", type=Path, default=DEFAULT_ENVELOPE_SCHEMA)
    parser.add_argument("--require-count", type=int)
    parser.add_argument("--require-all-reviewed-revisions", action="store_true")
    parser.add_argument(
        "--require-effective-rereview-pass",
        action="store_true",
        help=(
            "Require every latest effective annotation to be the candidate of a "
            "current pass review. This is the semantic-loop closure gate."
        ),
    )
    parser.add_argument("--expected-review-model", default=EXPECTED_REVIEW_MODEL)
    parser.add_argument("--expected-review-prompt-version")
    parser.add_argument("--expected-review-prompt-sha256")
    parser.add_argument("--expected-repair-model", default=EXPECTED_REPAIR_MODEL)
    parser.add_argument("--max-errors", type=int, default=100)
    return parser


def print_report(report: ValidationReport) -> None:
    print("A1 effective-repair independent validation")
    print(
        "  counts: "
        f"repairs={report.repair_count}, checked={report.checked_repairs}, "
        f"requiredReviewedRevisions={report.required_revision_count}, "
        f"effectiveRereviewPasses={report.effective_rereview_pass_count}"
    )
    print(f"  repair statuses: {dict(sorted(report.status_counts.items()))}")
    print(f"  review verdicts: {dict(sorted(report.verdict_counts.items()))}")
    if report.passed:
        print("PASS: every stored repair has a current, closed, automatic-only provenance chain")
        return
    print(f"FAIL: {report.total_error_count} validation error(s)")
    for message in report.errors:
        print(f"  - {message}")
    omitted = report.total_error_count - len(report.errors)
    if omitted > 0:
        print(f"  - ... {omitted} additional error(s) omitted")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = validate_databases(
            source_db=args.source_db,
            annotation_db=args.annotation_db,
            review_db=args.review_db,
            effective_db=args.effective_db,
            canonical_schema=args.canonical_schema,
            review_schema=args.review_schema,
            envelope_schema=args.envelope_schema,
            require_count=args.require_count,
            require_all_reviewed_revisions=args.require_all_reviewed_revisions,
            require_effective_rereview_pass=args.require_effective_rereview_pass,
            expected_review_model=args.expected_review_model,
            expected_review_prompt_version=args.expected_review_prompt_version,
            expected_review_prompt_sha256=args.expected_review_prompt_sha256,
            expected_repair_model=args.expected_repair_model,
            max_errors=args.max_errors,
        )
    except (ConfigurationError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
