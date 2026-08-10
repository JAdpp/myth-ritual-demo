#!/usr/bin/env python3
"""Independently validate the complete C1 -> A1 -> semantic-review chain.

This command is deliberately read-only.  It opens the C1 source database, the
A1 annotation ledger, and the semantic-review sidecar with SQLite ``mode=ro``
and ``query_only`` enabled.  It never calls a model and never mutates a file.

The final gate is intentionally strict: unless explicitly allowed on the
command line, every canonical/runtime C1 record must have a current, valid A1
candidate and a matching ``pass`` semantic review.  Queued, leased, failed,
quarantined, and stale sidecar rows always fail the gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from validate_a1_annotations import A_SAFETY_PHRASE_RULES, SELF_HARM_DEATH_PATTERN


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT / "data" / "corpus" / "c1_single_story" / "catalog.sqlite3"
DEFAULT_ANNOTATION_DB = (
    ROOT / "data" / "corpus" / "a1_retrieval_annotations" / "annotations.sqlite3"
)
DEFAULT_REVIEW_DB = (
    ROOT / "data" / "corpus" / "a1_semantic_reviews" / "reviews.sqlite3"
)
DEFAULT_REVIEW_SCHEMA = ROOT / "contracts" / "a1-semantic-review.schema.json"

EXPECTED_REVIEW_MODEL = "deepseek-v4-flash"
EXPECTED_REVIEW_PROMPT_VERSION = (
    "mengdie-a1-semantic-review-v3-nonthinking-field-checklist"
)
EXPECTED_REVIEW_PROMPT_SHA256 = (
    "5dd04b68542e7be341a65764f2b127a839cdafa2d4faa499f02ddd27e7677cee"
)
SQLITE_BUSY_TIMEOUT_MS = 30_000
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

ISSUE_FIELD_BY_CODE = {
    "summary_actor": "modernRetrievalSummary",
    "summary_action_object": "modernRetrievalSummary",
    "summary_modality_negation": "modernRetrievalSummary",
    "summary_time": "modernRetrievalSummary",
    "summary_outcome": "modernRetrievalSummary",
    "summary_location": "modernRetrievalSummary",
    "summary_quantity": "modernRetrievalSummary",
    "summary_relation": "modernRetrievalSummary",
    "summary_causality": "modernRetrievalSummary",
    "summary_other_fact": "modernRetrievalSummary",
    "other_version": "modernRetrievalSummary",
    "narrative_sufficiency_mismatch": "narrativeSufficiency",
    "key_entity_mismatch": "keyEntities",
    "plot_beat_mismatch": "plotBeats",
    "motif_mismatch": "motifTerms",
    "label_life_context": "lifeContext",
    "trigger_mismatch": "narrativeArc.trigger",
    "label_conflict": "narrativeArc.conflictTypes",
    "label_agency": "narrativeArc.agencyModes",
    "label_ending": "narrativeArc.endingMode",
    "safety_status_mismatch": "autoSafetyScreen.status",
    "safety_omission": "autoSafetyScreen.flags",
    "safety_overreach": "autoSafetyScreen.flags",
    "safety_uncertainty_mismatch": "autoSafetyScreen.uncertainties",
    "interpretation_risk_mismatch": "autoSafetyScreen.interpretationRisks",
    "evidence_support_mismatch": "evidence.supports",
}

CHECKLIST_FIELDS = (
    "modernRetrievalSummary",
    "narrativeSufficiency",
    "keyEntities",
    "plotBeats",
    "motifTerms",
    "lifeContext",
    "narrativeArc.trigger",
    "narrativeArc.conflictTypes",
    "narrativeArc.agencyModes",
    "narrativeArc.endingMode",
    "autoSafetyScreen.status",
    "autoSafetyScreen.flags",
    "autoSafetyScreen.interpretationRisks",
    "autoSafetyScreen.uncertainties",
    "evidence.supports",
)

SOFT_REVISE_HINT_MARKERS = (
    "基本相符",
    "基本一致",
    "正确",
    "正確",
    "合理",
    "可保留",
    "保留即可",
    "无需修改",
    "無需修改",
    "不需修改",
    "需确认",
    "需確認",
    "可能",
    "建议",
    "建議",
)

SOURCE_COLUMNS = {
    "entry_id",
    "source_work_id",
    "source_work_title",
    "source_locator",
    "entry_ordinal",
    "title",
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
    "input_json",
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


class ConfigurationError(RuntimeError):
    """The command line, schema, or SQLite layout cannot be validated."""


@dataclass
class ValidationReport:
    expected_count: int
    source_count: int = 0
    annotation_count: int = 0
    review_count: int = 0
    checked_records: int = 0
    verdict_counts: Counter[str] = field(default_factory=Counter)
    status_counts: Counter[str] = field(default_factory=Counter)
    issue_count: int = 0
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


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
    if rows != ["ok"]:
        detail = "; ".join(rows[:3])
        raise ConfigurationError(f"{label} SQLite integrity_check failed: {detail}")


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    escaped = table.replace('"', '""')
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{escaped}")')}


def require_columns(
    conn: sqlite3.Connection, table: str, required: set[str], label: str
) -> None:
    columns = table_columns(conn, table)
    if not columns:
        raise ConfigurationError(f"{label} table is missing: {table}")
    missing = sorted(required - columns)
    if missing:
        raise ConfigurationError(
            f"{label} table {table} is missing columns: {', '.join(missing)}"
        )


def parse_json_object(raw: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{label} is empty")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value


def deterministic_locked_safety_flags(source_text: str) -> list[str]:
    """Recompute A-class safety locks without importing the review runner."""
    flags: set[str] = set()
    for rule in A_SAFETY_PHRASE_RULES:
        if rule.pattern.search(source_text):
            flags.update(rule.required_flags)
    if SELF_HARM_DEATH_PATTERN.search(source_text):
        flags.update({"self_harm_or_suicide", "death"})
    if "sexual_violence" in flags:
        flags.update({"sexual_content", "coercion_or_abuse"})
    return sorted(flags)


def annotation_projection(annotation: Mapping[str, Any]) -> dict[str, Any]:
    """Build the exact candidate projection retained by the review sidecar."""
    record = annotation["annotation_record"]
    retrieval = record["retrieval_profile"]
    arc = record["narrative_arc"]
    safety = record["auto_safety_screen"]
    return {
        "modernRetrievalSummary": retrieval["modern_retrieval_summary"],
        "narrativeSufficiency": retrieval["narrative_sufficiency"],
        "narrativeSufficiencyReason": retrieval["narrative_sufficiency_reason"],
        "keyEntities": retrieval["key_entities"],
        "plotBeats": retrieval["plot_beats"],
        "motifTerms": retrieval["motif_terms"],
        "summaryEvidenceIds": retrieval["summary_evidence_ids"],
        "lifeContext": record["life_context"],
        "narrativeArc": {
            "trigger": arc["trigger"],
            "conflictTypes": arc["conflict_types"],
            "agencyModes": arc["agency_modes"],
            "endingMode": arc["ending_mode"],
        },
        "autoSafetyScreen": {
            "status": safety["status"],
            "flags": safety["flags"],
            "interpretationRisks": safety["interpretation_risks"],
            "uncertainties": safety["uncertainties"],
        },
        "evidence": [
            {
                "id": item["id"],
                "excerpt": item["excerpt"],
                "supports": item["supports"],
            }
            for item in record["evidence"]
        ],
    }


def expected_review_input(
    source: sqlite3.Row,
    annotation: Mapping[str, Any],
    *,
    source_hash: str,
    candidate_hash: str,
) -> dict[str, Any]:
    source_text = str(source["text"])
    return {
        "entryId": source["entry_id"],
        "work": source["source_work_title"],
        "title": source["title"],
        "locator": source["source_locator"],
        "sourceTextSha256": source_hash,
        "candidateAnnotationSha256": candidate_hash,
        "sourceText": source_text,
        "lockedSafetyFlags": deterministic_locked_safety_flags(source_text),
        "candidate": annotation_projection(annotation),
    }


def schema_error_message(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"schema violation at {path}: {error.message}"


def validate_review_semantics(
    review: Mapping[str, Any],
    *,
    entry_id: str,
    source_text: str,
    candidate_projection: Mapping[str, Any],
    locked_safety_flags: Iterable[str],
) -> list[str]:
    errors: list[str] = []
    if review.get("entryId") != entry_id:
        errors.append("review entryId does not match the sidecar/source entry_id")

    verdict = review.get("verdict")
    checklist = review.get("checklist")
    if not isinstance(checklist, dict):
        errors.append("review checklist is not an object")
        false_fields: set[str] = set()
    else:
        actual_fields = set(checklist)
        expected_fields = set(CHECKLIST_FIELDS)
        if actual_fields != expected_fields:
            errors.append("review checklist field set is not exact")
        non_boolean = [
            field
            for field in CHECKLIST_FIELDS
            if field in checklist and not isinstance(checklist[field], bool)
        ]
        if non_boolean:
            errors.append("review checklist contains non-boolean values")
        false_fields = {
            field for field in CHECKLIST_FIELDS if checklist.get(field) is False
        }
    issues = review.get("issues")
    if not isinstance(issues, list):
        return errors + ["review issues is not an array"]
    if verdict == "pass" and issues:
        errors.append("pass verdict has non-empty issues")
    if verdict == "pass" and false_fields:
        errors.append("pass verdict has false checklist fields")
    if verdict in {"revise", "uncertain"} and not issues:
        errors.append(f"{verdict} verdict has no issues")

    seen: set[tuple[str, str, str]] = set()
    issue_fields: set[str] = set()
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            errors.append(f"issue[{index}] is not an object")
            continue
        code = issue.get("code")
        field_name = issue.get("field")
        excerpt = issue.get("sourceExcerpt")
        if ISSUE_FIELD_BY_CODE.get(str(code)) != field_name:
            errors.append(f"issue[{index}] code-field mapping is invalid")
        if isinstance(field_name, str):
            issue_fields.add(field_name)
        hint = issue.get("correctionHint")
        if verdict == "revise" and isinstance(hint, str) and any(
            marker in hint for marker in SOFT_REVISE_HINT_MARKERS
        ):
            errors.append(
                f"issue[{index}] revise correctionHint contains soft language"
            )
        if not isinstance(excerpt, str) or excerpt not in source_text:
            errors.append(f"issue[{index}] sourceExcerpt is not exact source text")
        signature = (str(code), str(field_name), str(excerpt))
        if signature in seen:
            errors.append(f"issue[{index}] duplicates an earlier issue")
        seen.add(signature)

        if code == "safety_overreach":
            try:
                candidate_flags = set(
                    candidate_projection["autoSafetyScreen"]["flags"]
                ) - {"none_identified"}
            except (KeyError, TypeError):
                candidate_flags = set()
            if not candidate_flags - set(locked_safety_flags):
                errors.append(
                    f"issue[{index}] safety_overreach has no unlocked candidate flag"
                )
    if false_fields != issue_fields:
        if false_fields - issue_fields:
            errors.append("review checklist has false fields without matching issues")
        if issue_fields - false_fields:
            errors.append("review issues target checklist fields marked true")
    return errors


def status_counts(conn: sqlite3.Connection, table: str) -> Counter[str]:
    escaped = table.replace('"', '""')
    return Counter(
        {
            str(row["status"]): int(row["count"])
            for row in conn.execute(
                f'SELECT status, COUNT(*) AS count FROM "{escaped}" GROUP BY status'
            )
        }
    )


def validate_databases(
    *,
    source_db: Path,
    annotation_db: Path,
    review_db: Path,
    review_schema: Path,
    expected_count: int,
    allow_revise: bool = False,
    allow_uncertain: bool = False,
    expected_model: str = EXPECTED_REVIEW_MODEL,
    expected_prompt_version: str = EXPECTED_REVIEW_PROMPT_VERSION,
    expected_prompt_sha256: str = EXPECTED_REVIEW_PROMPT_SHA256,
    max_errors: int = 100,
) -> ValidationReport:
    if expected_count <= 0:
        raise ConfigurationError("--expected-count must be a positive integer")
    if max_errors <= 0:
        raise ConfigurationError("--max-errors must be a positive integer")
    if not HEX_SHA256.fullmatch(expected_prompt_sha256):
        raise ConfigurationError("expected prompt SHA-256 must be 64 lowercase hex chars")
    if not review_schema.is_file():
        raise ConfigurationError(f"review schema does not exist: {review_schema}")
    try:
        schema = json.loads(review_schema.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (json.JSONDecodeError, OSError, SchemaError) as exc:
        raise ConfigurationError(f"invalid review schema: {exc}") from exc
    schema_validator = Draft202012Validator(
        schema, format_checker=FormatChecker()
    )
    schema_sha256 = sha256_file(review_schema)

    report = ValidationReport(expected_count=expected_count, max_errors=max_errors)
    with readonly_connection(source_db) as source_conn, readonly_connection(
        annotation_db
    ) as annotation_conn, readonly_connection(review_db) as review_conn:
        require_integrity(source_conn, "C1 source")
        require_integrity(annotation_conn, "A1 annotation")
        require_integrity(review_conn, "semantic-review sidecar")
        require_columns(source_conn, "entries", SOURCE_COLUMNS, "C1 source")
        require_columns(
            annotation_conn, "annotation_jobs", ANNOTATION_COLUMNS, "A1 annotation"
        )
        require_columns(
            review_conn,
            "semantic_review_jobs",
            REVIEW_COLUMNS,
            "semantic-review sidecar",
        )

        report.source_count = int(
            source_conn.execute(
                """
                SELECT COUNT(*) FROM entries
                WHERE dedupe_status='canonical' AND runtime_eligible=1
                """
            ).fetchone()[0]
        )
        report.annotation_count = int(
            annotation_conn.execute("SELECT COUNT(*) FROM annotation_jobs").fetchone()[0]
        )
        report.review_count = int(
            review_conn.execute("SELECT COUNT(*) FROM semantic_review_jobs").fetchone()[0]
        )
        report.status_counts = status_counts(review_conn, "semantic_review_jobs")

        if report.source_count != expected_count:
            report.fail(
                f"canonical/runtime C1 count {report.source_count} != expected {expected_count}"
            )
        if report.annotation_count != report.source_count:
            report.fail(
                f"A1 row count {report.annotation_count} != C1 selected count {report.source_count}"
            )
        if report.review_count != report.source_count:
            report.fail(
                f"review row count {report.review_count} != C1 selected count {report.source_count}"
            )

        annotation_states = status_counts(annotation_conn, "annotation_jobs")
        invalid_a1_states = {
            name: count for name, count in annotation_states.items() if name != "valid"
        }
        if invalid_a1_states:
            report.fail(f"A1 has non-valid states: {dict(sorted(invalid_a1_states.items()))}")

        allowed_final_statuses = {"pass"}
        if allow_revise:
            allowed_final_statuses.add("revise")
        if allow_uncertain:
            allowed_final_statuses.add("uncertain")
        invalid_review_states = {
            name: count
            for name, count in report.status_counts.items()
            if name not in allowed_final_statuses
        }
        if invalid_review_states:
            report.fail(
                "review sidecar has non-final/disallowed states: "
                f"{dict(sorted(invalid_review_states.items()))}"
            )

        source_cursor = source_conn.execute(
            """
            SELECT entry_id,source_work_id,source_work_title,source_locator,
                   entry_ordinal,title,char_count,text,extracted_text_sha256,
                   dedupe_status,canonical_entry_id,runtime_eligible
            FROM entries
            WHERE dedupe_status='canonical' AND runtime_eligible=1
            ORDER BY entry_id
            """
        )
        for source in source_cursor:
            entry_id = str(source["entry_id"])
            report.checked_records += 1
            source_text = str(source["text"])
            source_hash = sha256_text(source_text)
            if source["canonical_entry_id"] != entry_id:
                report.fail(f"{entry_id}: canonical_entry_id does not equal entry_id")
            if int(source["char_count"]) != len(source_text):
                report.fail(f"{entry_id}: C1 char_count does not match source text")
            if source["extracted_text_sha256"] != source_hash:
                report.fail(f"{entry_id}: C1 extracted_text_sha256 is not current")

            annotation_row = annotation_conn.execute(
                """
                SELECT entry_id,source_text_sha256,source_work_id,source_work_title,
                       title,char_count,status,annotation_json
                FROM annotation_jobs WHERE entry_id=?
                """,
                (entry_id,),
            ).fetchone()
            if annotation_row is None:
                report.fail(f"{entry_id}: missing A1 row")
                continue
            if annotation_row["status"] != "valid":
                report.fail(f"{entry_id}: A1 status is {annotation_row['status']!r}, not valid")
            for column in ("source_work_id", "source_work_title", "title", "char_count"):
                if annotation_row[column] != source[column]:
                    report.fail(f"{entry_id}: A1 {column} does not match C1")
            if annotation_row["source_text_sha256"] != source_hash:
                report.fail(f"{entry_id}: A1 source hash is not current")
            try:
                annotation = parse_json_object(
                    annotation_row["annotation_json"], label="annotation_json"
                )
                record = annotation["annotation_record"]
                if record["unit"]["unit_id"] != entry_id:
                    raise ValueError("unit_id does not match entry_id")
                if record["unit"]["source_text_sha256"] != source_hash:
                    raise ValueError("unit source hash is not current")
                profile = record["source_profile"]
                if profile["source_text_sha256"] != source_hash:
                    raise ValueError("source_profile hash is not current")
                if profile["source_text"] != source_text:
                    raise ValueError("source_profile text does not match C1")
                projection = annotation_projection(annotation)
                locked_flags = set(deterministic_locked_safety_flags(source_text))
                candidate_flags = set(projection["autoSafetyScreen"]["flags"])
                missing_locked_flags = sorted(locked_flags - candidate_flags)
                if missing_locked_flags:
                    raise ValueError(
                        "candidate omits deterministic safety flags: "
                        + ",".join(missing_locked_flags)
                    )
            except (KeyError, TypeError, ValueError) as exc:
                report.fail(f"{entry_id}: invalid/current A1 JSON contract: {exc}")
                continue
            candidate_hash = sha256_text(canonical_json(annotation))

            review_row = review_conn.execute(
                "SELECT * FROM semantic_review_jobs WHERE entry_id=?", (entry_id,)
            ).fetchone()
            if review_row is None:
                report.fail(f"{entry_id}: missing semantic-review row")
                continue
            report.verdict_counts[str(review_row["verdict"])] += 1
            if review_row["source_text_sha256"] != source_hash:
                report.fail(f"{entry_id}: review source hash is not current")
            if review_row["candidate_annotation_sha256"] != candidate_hash:
                report.fail(f"{entry_id}: review candidate hash is not current")
            for column in ("source_work_id", "source_work_title", "title", "char_count"):
                if review_row[column] != source[column]:
                    report.fail(f"{entry_id}: review {column} does not match C1")
            if review_row["model"] != expected_model:
                report.fail(f"{entry_id}: review model does not match expected model")
            if not isinstance(review_row["provider_reported_model"], str) or not str(
                review_row["provider_reported_model"]
            ).strip():
                report.fail(f"{entry_id}: provider_reported_model is missing")
            if review_row["prompt_version"] != expected_prompt_version:
                report.fail(f"{entry_id}: review prompt_version is not current")
            if review_row["prompt_sha256"] != expected_prompt_sha256:
                report.fail(f"{entry_id}: review prompt_sha256 is not current")
            if review_row["review_schema_sha256"] != schema_sha256:
                report.fail(f"{entry_id}: review schema hash is not current")

            expected_input = expected_review_input(
                source,
                annotation,
                source_hash=source_hash,
                candidate_hash=candidate_hash,
            )
            try:
                review_input = parse_json_object(
                    review_row["input_json"], label="input_json"
                )
                if review_input != expected_input:
                    report.fail(f"{entry_id}: input_json does not match current C1/A1 data")
                if review_row["input_json"] != canonical_json(review_input):
                    report.fail(f"{entry_id}: input_json is not canonical JSON")
            except ValueError as exc:
                report.fail(f"{entry_id}: {exc}")

            try:
                review = parse_json_object(
                    review_row["review_json"], label="review_json"
                )
            except ValueError as exc:
                report.fail(f"{entry_id}: {exc}")
                continue
            schema_errors = sorted(
                schema_validator.iter_errors({"reviews": [review]}),
                key=lambda error: list(error.absolute_path),
            )
            for schema_error in schema_errors:
                report.fail(f"{entry_id}: {schema_error_message(schema_error)}")

            verdict = review.get("verdict")
            if review_row["status"] != verdict:
                report.fail(f"{entry_id}: sidecar status does not equal review verdict")
            if review_row["verdict"] != verdict:
                report.fail(f"{entry_id}: sidecar verdict does not equal review_json verdict")
            if verdict not in allowed_final_statuses:
                report.fail(f"{entry_id}: verdict {verdict!r} is not allowed by this gate")
            issues = review.get("issues")
            if isinstance(issues, list):
                report.issue_count += len(issues)
                try:
                    stored_issues = json.loads(review_row["issues_json"])
                except (TypeError, json.JSONDecodeError):
                    report.fail(f"{entry_id}: issues_json is invalid JSON")
                else:
                    if stored_issues != issues:
                        report.fail(f"{entry_id}: issues_json does not match review_json")
                    if review_row["issues_json"] != canonical_json(stored_issues):
                        report.fail(f"{entry_id}: issues_json is not canonical JSON")
            if review_row["review_json"] != canonical_json(review):
                report.fail(f"{entry_id}: review_json is not canonical JSON")

            for semantic_error in validate_review_semantics(
                review,
                entry_id=entry_id,
                source_text=source_text,
                candidate_projection=projection,
                locked_safety_flags=deterministic_locked_safety_flags(source_text),
            ):
                report.fail(f"{entry_id}: {semantic_error}")

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only final validator for A1 semantic-review coverage"
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--annotation-db", type=Path, default=DEFAULT_ANNOTATION_DB)
    parser.add_argument("--review-db", type=Path, default=DEFAULT_REVIEW_DB)
    parser.add_argument("--review-schema", type=Path, default=DEFAULT_REVIEW_SCHEMA)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--allow-revise", action="store_true")
    parser.add_argument("--allow-uncertain", action="store_true")
    parser.add_argument("--expected-model", default=EXPECTED_REVIEW_MODEL)
    parser.add_argument(
        "--expected-prompt-version", default=EXPECTED_REVIEW_PROMPT_VERSION
    )
    parser.add_argument(
        "--expected-prompt-sha256", default=EXPECTED_REVIEW_PROMPT_SHA256
    )
    parser.add_argument("--max-errors", type=int, default=100)
    return parser


def print_report(report: ValidationReport) -> None:
    print("A1 semantic-review independent validation")
    print(
        "  counts: "
        f"source={report.source_count}, A1={report.annotation_count}, "
        f"reviews={report.review_count}, checked={report.checked_records}, "
        f"expected={report.expected_count}"
    )
    print(f"  review statuses: {dict(sorted(report.status_counts.items()))}")
    print(f"  review verdicts: {dict(sorted(report.verdict_counts.items()))}")
    print(f"  reported issues: {report.issue_count}")
    if report.passed:
        print("PASS: every selected record has a current, allowed semantic review")
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
            review_schema=args.review_schema,
            expected_count=args.expected_count,
            allow_revise=args.allow_revise,
            allow_uncertain=args.allow_uncertain,
            expected_model=args.expected_model,
            expected_prompt_version=args.expected_prompt_version,
            expected_prompt_sha256=args.expected_prompt_sha256,
            max_errors=args.max_errors,
        )
    except (ConfigurationError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
