#!/usr/bin/env python3
"""Score an A1 semantic-review sidecar against the 36-entry dev audit.

This is a pure, read-only, offline calibration utility.  It never calls a
model and never changes either input.  The bundled benchmark is a single-
developer development audit: it is *not* a formal independently annotated
human gold standard and it does not make an annotation research-ready.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT / "data" / "corpus" / "c1_single_story" / "catalog.sqlite3"
DEFAULT_ANNOTATION_DB = (
    ROOT / "data" / "corpus" / "a1_retrieval_annotations" / "annotations.sqlite3"
)

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
CHECKLIST_FIELD_SET = frozenset(CHECKLIST_FIELDS)
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_COLUMNS = {
    "entry_id",
    "source_text_sha256",
    "title",
    "candidate_annotation_sha256",
    "status",
    "verdict",
    "review_json",
}
SQLITE_BUSY_TIMEOUT_MS = 30_000


class CalibrationError(RuntimeError):
    """The benchmark or review sidecar cannot be scored safely."""


@dataclass(frozen=True)
class IssueExpectation:
    field: str
    direction: str
    expectation: str
    concise_fact: str


@dataclass(frozen=True)
class CurrentReviewAudit:
    review_json_sha256: str
    strict_has_true_issue: bool
    strict_is_pure_fp_revise: bool
    concise_notes: str


@dataclass(frozen=True)
class GoldEntry:
    entry_id: str
    title: str
    source_text_sha256: str
    candidate_annotation_sha256: str
    gold_has_clear_error: bool
    must_detect_fields: frozenset[str]
    must_not_flag_fields: frozenset[str]
    concise_notes: str
    issue_expectations: tuple[IssueExpectation, ...]
    current_v3_issue_audit: CurrentReviewAudit | None


@dataclass(frozen=True)
class ReviewObservation:
    verdict: str
    flagged_fields: frozenset[str]
    issue_directions: frozenset[tuple[str, str]]
    canonical_review_sha256: str


ISSUE_DIRECTIONS = frozenset({"omission", "overreach", "contradiction", "evidence"})
EXPECTATION_KINDS = frozenset({"must_detect", "must_not_detect"})


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"{label} must be a non-empty string")
    return value


def _require_field_list(value: Any, label: str) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CalibrationError(f"{label} must be an array of checklist field names")
    if len(value) != len(set(value)):
        raise CalibrationError(f"{label} contains duplicate field names")
    unknown = set(value) - CHECKLIST_FIELD_SET
    if unknown:
        raise CalibrationError(f"{label} contains unknown fields: {sorted(unknown)}")
    return frozenset(value)


def load_gold(path: Path) -> tuple[dict[str, Any], dict[str, GoldEntry]]:
    if not path.is_file():
        raise CalibrationError(f"gold JSON does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot read gold JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CalibrationError("gold JSON root must be an object")

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise CalibrationError("gold JSON metadata must be an object")
    if metadata.get("benchmark_kind") != "single_developer_development_audit":
        raise CalibrationError(
            "gold metadata must identify a single_developer_development_audit"
        )
    if metadata.get("formal_human_gold_standard") is not False:
        raise CalibrationError("gold metadata must set formal_human_gold_standard=false")
    if metadata.get("research_ready") is not False:
        raise CalibrationError("gold metadata must set research_ready=false")
    expected_fields = payload.get("checklist_fields")
    if expected_fields != list(CHECKLIST_FIELDS):
        raise CalibrationError("gold checklist_fields do not match the reviewer checklist")

    declared_ids = payload.get("entry_ids")
    if (
        not isinstance(declared_ids, list)
        or not declared_ids
        or any(not isinstance(item, str) or not item for item in declared_ids)
        or len(declared_ids) != len(set(declared_ids))
    ):
        raise CalibrationError("gold entry_ids must be a non-empty unique string array")
    declared_ids_sha256 = metadata.get("entry_ids_sha256")
    calculated_ids_sha256 = hashlib.sha256(
        "".join(f"{entry_id}\n" for entry_id in declared_ids).encode("utf-8")
    ).hexdigest()
    if declared_ids_sha256 != calculated_ids_sha256:
        raise CalibrationError("gold entry_ids_sha256 does not match ordered entry_ids")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CalibrationError("gold entries must be a non-empty array")
    entries: dict[str, GoldEntry] = {}
    for index, raw in enumerate(raw_entries):
        label = f"entries[{index}]"
        if not isinstance(raw, dict):
            raise CalibrationError(f"{label} must be an object")
        entry_id = _require_string(raw.get("entry_id"), f"{label}.entry_id")
        title = _require_string(raw.get("title"), f"{label}.title")
        source_hash = _require_string(
            raw.get("source_text_sha256"), f"{label}.source_text_sha256"
        )
        if not HEX_SHA256.fullmatch(source_hash):
            raise CalibrationError(f"{label}.source_text_sha256 is not SHA-256")
        candidate_hash = _require_string(
            raw.get("candidate_annotation_sha256"),
            f"{label}.candidate_annotation_sha256",
        )
        if not HEX_SHA256.fullmatch(candidate_hash):
            raise CalibrationError(f"{label}.candidate_annotation_sha256 is not SHA-256")
        clear_error = raw.get("gold_has_clear_error")
        if not isinstance(clear_error, bool):
            raise CalibrationError(f"{label}.gold_has_clear_error must be boolean")
        must_detect = _require_field_list(
            raw.get("must_detect_fields"), f"{label}.must_detect_fields"
        )
        must_not = _require_field_list(
            raw.get("must_not_flag_fields"), f"{label}.must_not_flag_fields"
        )
        if must_detect & must_not:
            raise CalibrationError(
                f"{label} marks fields as both required and prohibited: "
                f"{sorted(must_detect & must_not)}"
            )
        if clear_error != bool(must_detect):
            raise CalibrationError(
                f"{label}: gold_has_clear_error must agree with non-empty "
                "must_detect_fields"
            )
        notes = _require_string(raw.get("concise_notes"), f"{label}.concise_notes")
        raw_expectations = raw.get("issue_expectations")
        if not isinstance(raw_expectations, list):
            raise CalibrationError(f"{label}.issue_expectations must be an array")
        expectations: list[IssueExpectation] = []
        expectation_keys: set[tuple[str, str, str]] = set()
        for issue_index, raw_expectation in enumerate(raw_expectations):
            issue_label = f"{label}.issue_expectations[{issue_index}]"
            if not isinstance(raw_expectation, dict):
                raise CalibrationError(f"{issue_label} must be an object")
            field = raw_expectation.get("field")
            direction = raw_expectation.get("direction")
            expectation = raw_expectation.get("expectation")
            if field not in CHECKLIST_FIELD_SET:
                raise CalibrationError(f"{issue_label}.field is not a checklist field")
            if direction not in ISSUE_DIRECTIONS:
                raise CalibrationError(f"{issue_label}.direction is invalid")
            if expectation not in EXPECTATION_KINDS:
                raise CalibrationError(f"{issue_label}.expectation is invalid")
            concise_fact = _require_string(
                raw_expectation.get("concise_fact"), f"{issue_label}.concise_fact"
            )
            key = (str(field), str(direction), str(expectation))
            if key in expectation_keys:
                raise CalibrationError(
                    f"{issue_label} duplicates a field/direction/expectation tuple; "
                    "combine facts in concise_fact"
                )
            expectation_keys.add(key)
            expectations.append(
                IssueExpectation(
                    field=str(field),
                    direction=str(direction),
                    expectation=str(expectation),
                    concise_fact=concise_fact,
                )
            )
        required_expectation_fields = {
            item.field for item in expectations if item.expectation == "must_detect"
        }
        if required_expectation_fields != set(must_detect):
            raise CalibrationError(
                f"{label}: must_detect issue expectations must cover exactly "
                "must_detect_fields"
            )

        raw_current_audit = raw.get("current_v3_issue_audit")
        current_audit: CurrentReviewAudit | None = None
        if raw_current_audit is not None:
            audit_label = f"{label}.current_v3_issue_audit"
            if not isinstance(raw_current_audit, dict):
                raise CalibrationError(f"{audit_label} must be an object")
            review_hash = _require_string(
                raw_current_audit.get("review_json_sha256"),
                f"{audit_label}.review_json_sha256",
            )
            if not HEX_SHA256.fullmatch(review_hash):
                raise CalibrationError(f"{audit_label}.review_json_sha256 is not SHA-256")
            strict_hit = raw_current_audit.get("strict_has_true_issue")
            strict_pure_fp = raw_current_audit.get("strict_is_pure_fp_revise")
            if not isinstance(strict_hit, bool) or not isinstance(strict_pure_fp, bool):
                raise CalibrationError(f"{audit_label} strict audit flags must be boolean")
            if strict_hit and strict_pure_fp:
                raise CalibrationError(f"{audit_label} cannot be both a hit and pure FP")
            current_audit = CurrentReviewAudit(
                review_json_sha256=review_hash,
                strict_has_true_issue=strict_hit,
                strict_is_pure_fp_revise=strict_pure_fp,
                concise_notes=_require_string(
                    raw_current_audit.get("concise_notes"),
                    f"{audit_label}.concise_notes",
                ),
            )
        if entry_id in entries:
            raise CalibrationError(f"duplicate gold entry_id: {entry_id}")
        entries[entry_id] = GoldEntry(
            entry_id=entry_id,
            title=title,
            source_text_sha256=source_hash,
            candidate_annotation_sha256=candidate_hash,
            gold_has_clear_error=clear_error,
            must_detect_fields=must_detect,
            must_not_flag_fields=must_not,
            concise_notes=notes,
            issue_expectations=tuple(expectations),
            current_v3_issue_audit=current_audit,
        )

    declared_count = metadata.get("entry_count")
    if declared_count != len(entries):
        raise CalibrationError(
            f"gold metadata entry_count={declared_count!r}, actual={len(entries)}"
        )
    if list(entries) != declared_ids:
        raise CalibrationError(
            "gold entries must use exactly the ordered IDs declared in entry_ids"
        )
    clear_error_count = sum(entry.gold_has_clear_error for entry in entries.values())
    if metadata.get("clear_error_count") != clear_error_count:
        raise CalibrationError("gold metadata clear_error_count does not match entries")
    true_pass_count = len(entries) - clear_error_count
    if metadata.get("true_pass_count") != true_pass_count:
        raise CalibrationError("gold metadata true_pass_count does not match entries")
    return metadata, entries


def readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise CalibrationError(f"review SQLite does not exist: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        conn.close()
        raise CalibrationError(f"review SQLite integrity_check failed: {integrity}")
    return conn


def _review_columns(conn: sqlite3.Connection) -> set[str]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_review_jobs'"
    ).fetchone()
    if not exists:
        raise CalibrationError("review SQLite lacks semantic_review_jobs")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(semantic_review_jobs)")}
    missing = EXPECTED_COLUMNS - columns
    if missing:
        raise CalibrationError(f"semantic_review_jobs lacks columns: {sorted(missing)}")
    return columns


def load_reviews(path: Path, gold_ids: set[str]) -> dict[str, sqlite3.Row]:
    conn = readonly_connection(path)
    try:
        _review_columns(conn)
        rows = conn.execute(
            """
            SELECT entry_id, title, source_text_sha256,
                   candidate_annotation_sha256,
                   status, verdict, review_json
            FROM semantic_review_jobs
            """
        ).fetchall()
    finally:
        conn.close()
    reviews = {str(row["entry_id"]): row for row in rows}
    if len(reviews) != len(rows):
        raise CalibrationError("review SQLite contains duplicate entry_id values")
    review_ids = set(reviews)
    missing = gold_ids - review_ids
    extra = review_ids - gold_ids
    if missing or extra:
        raise CalibrationError(
            "review/gold ID set mismatch: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return reviews


def validate_current_inputs(
    source_db: Path,
    annotation_db: Path,
    gold_entries: Mapping[str, GoldEntry],
) -> dict[str, Any]:
    """Fail closed if the audited source or A1 candidate has drifted.

    Matching a review row to hashes copied into a benchmark is not enough: the
    benchmark and sidecar could both be stale.  This check independently reads
    the current C1 and A1 ledgers, recomputes the source and canonical JSON
    hashes, and verifies the embedded unit/source identities.
    """

    gold_ids = set(gold_entries)
    with readonly_connection(source_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
        required = {
            "entry_id",
            "title",
            "text",
            "extracted_text_sha256",
            "dedupe_status",
            "runtime_eligible",
        }
        if not required <= columns:
            raise CalibrationError(
                f"current C1 entries table lacks columns: {sorted(required - columns)}"
            )
        placeholders = ",".join("?" for _ in gold_ids)
        source_rows = conn.execute(
            f"""
            SELECT entry_id,title,text,extracted_text_sha256,
                   dedupe_status,runtime_eligible
            FROM entries WHERE entry_id IN ({placeholders})
            """,
            tuple(sorted(gold_ids)),
        ).fetchall()
    source_by_id = {str(row["entry_id"]): row for row in source_rows}
    if set(source_by_id) != gold_ids:
        raise CalibrationError(
            "current C1 exact ID set is missing benchmark entries: "
            f"{sorted(gold_ids - set(source_by_id))}"
        )

    with readonly_connection(annotation_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(annotation_jobs)")}
        required = {
            "entry_id",
            "title",
            "source_text_sha256",
            "status",
            "annotation_json",
        }
        if not required <= columns:
            raise CalibrationError(
                "current A1 annotation_jobs lacks columns: "
                f"{sorted(required - columns)}"
            )
        placeholders = ",".join("?" for _ in gold_ids)
        annotation_rows = conn.execute(
            f"""
            SELECT entry_id,title,source_text_sha256,status,annotation_json
            FROM annotation_jobs WHERE entry_id IN ({placeholders})
            """,
            tuple(sorted(gold_ids)),
        ).fetchall()
    annotation_by_id = {str(row["entry_id"]): row for row in annotation_rows}
    if set(annotation_by_id) != gold_ids:
        raise CalibrationError(
            "current A1 exact ID set is missing benchmark entries: "
            f"{sorted(gold_ids - set(annotation_by_id))}"
        )

    for entry_id in gold_entries:
        gold = gold_entries[entry_id]
        source = source_by_id[entry_id]
        annotation_row = annotation_by_id[entry_id]
        if source["dedupe_status"] != "canonical" or int(source["runtime_eligible"]) != 1:
            raise CalibrationError(
                f"{entry_id}: current C1 row is no longer canonical/runtime eligible"
            )
        if source["title"] != gold.title or annotation_row["title"] != gold.title:
            raise CalibrationError(f"{entry_id}: current title drift")
        source_text = source["text"]
        if not isinstance(source_text, str):
            raise CalibrationError(f"{entry_id}: current C1 text is not a string")
        current_source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if current_source_hash != source["extracted_text_sha256"]:
            raise CalibrationError(f"{entry_id}: current C1 extracted text hash is invalid")
        if current_source_hash != gold.source_text_sha256:
            raise CalibrationError(f"{entry_id}: current source hash drift")
        if annotation_row["status"] != "valid":
            raise CalibrationError(
                f"{entry_id}: current A1 status is not valid ({annotation_row['status']!r})"
            )
        if annotation_row["source_text_sha256"] != current_source_hash:
            raise CalibrationError(f"{entry_id}: current A1/source hash mismatch")
        try:
            annotation = json.loads(annotation_row["annotation_json"])
            record = annotation["annotation_record"]
            embedded_unit_id = record["unit"]["unit_id"]
            embedded_unit_hash = record["unit"]["source_text_sha256"]
            embedded_source_hash = record["source_profile"]["source_text_sha256"]
            embedded_source_text = record["source_profile"]["source_text"]
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"{entry_id}: current A1 JSON is malformed") from exc
        if (
            embedded_unit_id != entry_id
            or embedded_unit_hash != current_source_hash
            or embedded_source_hash != current_source_hash
            or embedded_source_text != source_text
        ):
            raise CalibrationError(f"{entry_id}: current A1 embedded source identity drift")
        current_candidate_hash = hashlib.sha256(
            canonical_json(annotation).encode("utf-8")
        ).hexdigest()
        if current_candidate_hash != gold.candidate_annotation_sha256:
            raise CalibrationError(f"{entry_id}: current candidate hash drift")

    return {
        "source_db": str(source_db.resolve()),
        "annotation_db": str(annotation_db.resolve()),
        "validated_entry_count": len(gold_entries),
        "source_hashes_current": True,
        "candidate_hashes_current": True,
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def infer_issue_direction(issue: Mapping[str, Any]) -> str:
    """Infer the coarse audit direction without interpreting source semantics.

    This intentionally remains a four-way approximation.  Two claims about
    the same field and direction can still differ semantically; the exact v3
    record audit below is retained for that reason.
    """

    code = str(issue.get("code", ""))
    hint = str(issue.get("correctionHint", ""))
    if code == "evidence_support_mismatch":
        return "evidence"
    if "omission" in code:
        return "omission"
    if "overreach" in code:
        return "overreach"
    overreach_markers = (
        "不应",
        "不宜",
        "删除",
        "移除",
        "过标",
        "過標",
        "缺乏依据",
        "缺乏依據",
        "无原文依据",
        "無原文依據",
        "证据不足",
        "證據不足",
    )
    omission_markers = (
        "漏标",
        "漏標",
        "遗漏",
        "遺漏",
        "应添加",
        "應添加",
        "应补充",
        "應補充",
        "应包含",
        "應包含",
        "缺少",
    )
    if any(marker in hint for marker in overreach_markers):
        return "overreach"
    if any(marker in hint for marker in omission_markers):
        return "omission"
    return "contradiction"


def parse_review_observation(row: sqlite3.Row, gold: GoldEntry) -> ReviewObservation:
    if row["title"] != gold.title:
        raise CalibrationError(
            f"{gold.entry_id}: title mismatch gold={gold.title!r}, review={row['title']!r}"
        )
    if row["candidate_annotation_sha256"] != gold.candidate_annotation_sha256:
        raise CalibrationError(
            f"{gold.entry_id}: candidate hash mismatch; the review is not for the "
            "audited candidate"
        )
    if row["source_text_sha256"] != gold.source_text_sha256:
        raise CalibrationError(
            f"{gold.entry_id}: source hash mismatch; the review is not for the "
            "audited source text"
        )
    if row["status"] not in {"pass", "revise", "uncertain"}:
        raise CalibrationError(
            f"{gold.entry_id}: review status is unresolved ({row['status']!r})"
        )
    try:
        review = json.loads(row["review_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"{gold.entry_id}: invalid review_json: {exc}") from exc
    if not isinstance(review, dict) or review.get("entryId") != gold.entry_id:
        raise CalibrationError(f"{gold.entry_id}: review_json entryId mismatch")
    verdict = review.get("verdict")
    if verdict not in {"pass", "revise", "uncertain"}:
        raise CalibrationError(f"{gold.entry_id}: invalid review verdict {verdict!r}")
    if row["verdict"] != verdict or row["status"] != verdict:
        raise CalibrationError(f"{gold.entry_id}: row status/verdict disagree with review_json")

    checklist = review.get("checklist")
    if not isinstance(checklist, dict) or set(checklist) != CHECKLIST_FIELD_SET:
        raise CalibrationError(f"{gold.entry_id}: checklist is not the exact 15-field set")
    if any(not isinstance(value, bool) for value in checklist.values()):
        raise CalibrationError(f"{gold.entry_id}: checklist values must be boolean")
    flagged = {field for field, value in checklist.items() if value is False}
    issues = review.get("issues")
    if not isinstance(issues, list):
        raise CalibrationError(f"{gold.entry_id}: issues must be an array")
    issue_fields: set[str] = set()
    for index, issue in enumerate(issues):
        if not isinstance(issue, Mapping):
            raise CalibrationError(f"{gold.entry_id}: issues[{index}] must be an object")
        field = issue.get("field")
        if field not in CHECKLIST_FIELD_SET:
            raise CalibrationError(
                f"{gold.entry_id}: issues[{index}] has unknown field {field!r}"
            )
        issue_fields.add(str(field))
    if issue_fields != flagged:
        raise CalibrationError(
            f"{gold.entry_id}: issue fields and false checklist fields disagree"
        )
    if verdict == "pass" and (flagged or issues):
        raise CalibrationError(f"{gold.entry_id}: pass review contains flagged fields")
    if verdict == "revise" and not flagged:
        raise CalibrationError(f"{gold.entry_id}: revise review has no flagged field")
    directions = frozenset(
        (str(issue["field"]), infer_issue_direction(issue)) for issue in issues
    )
    review_hash = hashlib.sha256(canonical_json(review).encode("utf-8")).hexdigest()
    return ReviewObservation(
        verdict=str(verdict),
        flagged_fields=frozenset(flagged),
        issue_directions=directions,
        canonical_review_sha256=review_hash,
    )


def _metric(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": None if denominator == 0 else numerator / denominator,
    }


def score_calibration(
    metadata: Mapping[str, Any],
    gold_entries: Mapping[str, GoldEntry],
    review_rows: Mapping[str, sqlite3.Row],
) -> dict[str, Any]:
    field_required_total = 0
    field_required_detected = 0
    error_records = 0
    field_detected_error_records = 0
    revise_records = 0
    field_revise_true_records = 0
    field_pure_fp_revise: list[dict[str, Any]] = []
    false_pass: list[dict[str, Any]] = []
    field_prohibited_violations: list[dict[str, Any]] = []
    direction_required_total = 0
    direction_required_detected = 0
    direction_detected_error_records = 0
    direction_revise_true_records = 0
    direction_pure_fp_revise: list[dict[str, Any]] = []
    direction_prohibited_violations: list[dict[str, Any]] = []
    direction_ambiguous_expectations: list[dict[str, Any]] = []
    strict_matching_records = 0
    strict_detected_error_records = 0
    strict_revise_true_records = 0
    strict_pure_fp_revise: list[dict[str, Any]] = []
    per_entry: list[dict[str, Any]] = []

    for entry_id in sorted(gold_entries):
        gold = gold_entries[entry_id]
        observation = parse_review_observation(review_rows[entry_id], gold)
        verdict = observation.verdict
        flagged = observation.flagged_fields
        detected = flagged & gold.must_detect_fields
        missed = gold.must_detect_fields - flagged
        prohibited = flagged & gold.must_not_flag_fields
        required_directions = {
            (item.field, item.direction)
            for item in gold.issue_expectations
            if item.expectation == "must_detect"
        }
        prohibited_directions = {
            (item.field, item.direction)
            for item in gold.issue_expectations
            if item.expectation == "must_not_detect"
        }
        prohibited_directions |= {
            (field, direction)
            for field in gold.must_not_flag_fields
            for direction in ISSUE_DIRECTIONS
        }
        # If the audit contains a true and a false fact in the same field and
        # coarse direction (for example, one safety flag is over-tagged while
        # another must be retained), field+direction alone cannot identify the
        # fact.  Exclude that pair from aggregate direction metrics and expose
        # it explicitly rather than awarding both a hit and a violation.
        ambiguous_directions = required_directions & prohibited_directions
        scored_required_directions = required_directions - ambiguous_directions
        scored_prohibited_directions = prohibited_directions - ambiguous_directions
        detected_directions = (
            observation.issue_directions & scored_required_directions
        )
        missed_directions = (
            scored_required_directions - observation.issue_directions
        )
        prohibited_direction_hits = (
            observation.issue_directions & scored_prohibited_directions
        )
        if ambiguous_directions:
            direction_ambiguous_expectations.append(
                {
                    "entry_id": entry_id,
                    "title": gold.title,
                    "field_directions": sorted(
                        f"{field}:{direction}"
                        for field, direction in ambiguous_directions
                    ),
                }
            )

        field_required_total += len(gold.must_detect_fields)
        field_required_detected += len(detected)
        direction_required_total += len(scored_required_directions)
        direction_required_detected += len(detected_directions)
        if gold.gold_has_clear_error:
            error_records += 1
            if detected:
                field_detected_error_records += 1
            if detected_directions:
                direction_detected_error_records += 1
        if verdict == "revise":
            revise_records += 1
            if detected:
                field_revise_true_records += 1
            if detected_directions:
                direction_revise_true_records += 1
            if flagged and not detected and flagged <= gold.must_not_flag_fields:
                field_pure_fp_revise.append(
                    {"entry_id": entry_id, "title": gold.title, "flagged_fields": sorted(flagged)}
                )
            if (
                observation.issue_directions
                and not detected_directions
                and observation.issue_directions <= scored_prohibited_directions
            ):
                direction_pure_fp_revise.append(
                    {
                        "entry_id": entry_id,
                        "title": gold.title,
                        "issue_directions": sorted(
                            f"{field}:{direction}"
                            for field, direction in observation.issue_directions
                        ),
                    }
                )
        if verdict == "pass" and gold.gold_has_clear_error:
            false_pass.append(
                {
                    "entry_id": entry_id,
                    "title": gold.title,
                    "missed_required_fields": sorted(missed),
                }
            )
        if prohibited:
            field_prohibited_violations.append(
                {
                    "entry_id": entry_id,
                    "title": gold.title,
                    "fields": sorted(prohibited),
                }
            )
        if prohibited_direction_hits:
            direction_prohibited_violations.append(
                {
                    "entry_id": entry_id,
                    "title": gold.title,
                    "field_directions": sorted(
                        f"{field}:{direction}"
                        for field, direction in prohibited_direction_hits
                    ),
                }
            )

        strict_audit = gold.current_v3_issue_audit
        strict_match = bool(
            strict_audit
            and strict_audit.review_json_sha256
            == observation.canonical_review_sha256
        )
        if strict_match and strict_audit is not None:
            strict_matching_records += 1
            if gold.gold_has_clear_error and strict_audit.strict_has_true_issue:
                strict_detected_error_records += 1
            if verdict == "revise" and strict_audit.strict_has_true_issue:
                strict_revise_true_records += 1
            if verdict == "revise" and strict_audit.strict_is_pure_fp_revise:
                strict_pure_fp_revise.append({"entry_id": entry_id, "title": gold.title})
        per_entry.append(
            {
                "entry_id": entry_id,
                "title": gold.title,
                "verdict": verdict,
                "flagged_fields": sorted(flagged),
                "detected_required_fields": sorted(detected),
                "missed_required_fields": sorted(missed),
                "prohibited_flagged_fields": sorted(prohibited),
                "detected_required_directions": sorted(
                    f"{field}:{direction}" for field, direction in detected_directions
                ),
                "missed_required_directions": sorted(
                    f"{field}:{direction}" for field, direction in missed_directions
                ),
                "prohibited_flagged_directions": sorted(
                    f"{field}:{direction}"
                    for field, direction in prohibited_direction_hits
                ),
                "unscored_ambiguous_directions": sorted(
                    f"{field}:{direction}"
                    for field, direction in ambiguous_directions
                ),
                "current_v3_strict_audit_matched": strict_match,
            }
        )

    strict_available = strict_matching_records == len(gold_entries)
    strict_metrics: dict[str, Any] = {
        "available": strict_available,
        "matching_records": strict_matching_records,
        "required_records": len(gold_entries),
        "limitation": (
            "仅当全部 review_json 与逐记录 v3 审计哈希一致时可用；它只复现"
            "每条记录是否至少含一个真实 issue 或是否为纯假阳性，不提供逐 issue "
            "precision。其他 reviewer 只能使用字段级与方向级近似。"
        ),
    }
    if strict_available:
        strict_metrics.update(
            {
                "record_recall": _metric(strict_detected_error_records, error_records),
                "revise_precision": _metric(strict_revise_true_records, revise_records),
                "pure_fp_revise_count": len(strict_pure_fp_revise),
                "false_pass_count": len(false_pass),
            }
        )

    return {
        "benchmark_id": metadata.get("benchmark_id"),
        "entry_count": len(gold_entries),
        "disclaimer": (
            "单人开发审计校准基准；非正式人工金标，不能据此声称人工复核或研究就绪。"
        ),
        "metrics": {
            "field_level": {
                "record_recall": _metric(field_detected_error_records, error_records),
                "revise_precision": _metric(field_revise_true_records, revise_records),
                "required_detection": _metric(
                    field_required_detected, field_required_total
                ),
                "pure_fp_revise_count": len(field_pure_fp_revise),
                "must_not_flag_violation_count": len(field_prohibited_violations),
                "limitation": "同字段内相反方向或不同事实的判断会合并。",
            },
            "direction_aware": {
                "record_recall": _metric(direction_detected_error_records, error_records),
                "revise_precision": _metric(direction_revise_true_records, revise_records),
                "required_detection": _metric(
                    direction_required_detected, direction_required_total
                ),
                "pure_fp_revise_count": len(direction_pure_fp_revise),
                "must_not_detect_violation_count": len(
                    direction_prohibited_violations
                ),
                "unscored_ambiguous_direction_count": len(
                    direction_ambiguous_expectations
                ),
                "limitation": (
                    "方向由 issue code 与 correctionHint 的固定规则推断；"
                    "同字段同方向的不同事实仍会合并。"
                ),
            },
            "current_v3_strict_record_audit": strict_metrics,
            "false_pass_count": len(false_pass),
        },
        "field_level_pure_fp_revise": field_pure_fp_revise,
        "direction_aware_pure_fp_revise": direction_pure_fp_revise,
        "current_v3_strict_pure_fp_revise": (
            strict_pure_fp_revise if strict_available else []
        ),
        "false_pass": false_pass,
        "field_level_must_not_flag_violations": field_prohibited_violations,
        "direction_aware_must_not_detect_violations": (
            direction_prohibited_violations
        ),
        "direction_aware_unscored_ambiguities": direction_ambiguous_expectations,
        "per_entry": per_entry,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gold_json", type=Path, help="development-audit gold JSON")
    parser.add_argument("review_sqlite", type=Path, help="semantic review SQLite sidecar")
    parser.add_argument(
        "--source-db",
        type=Path,
        default=DEFAULT_SOURCE_DB,
        help="current C1 source SQLite used to reject source drift",
    )
    parser.add_argument(
        "--annotation-db",
        type=Path,
        default=DEFAULT_ANNOTATION_DB,
        help="current canonical A1 SQLite used to reject candidate drift",
    )
    parser.add_argument(
        "--compact", action="store_true", help="emit one-line JSON instead of indented JSON"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metadata, gold_entries = load_gold(args.gold_json)
        current_validation = validate_current_inputs(
            args.source_db, args.annotation_db, gold_entries
        )
        review_rows = load_reviews(args.review_sqlite, set(gold_entries))
        result = score_calibration(metadata, gold_entries, review_rows)
        result["current_input_validation"] = current_validation
    except CalibrationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"ok": True, **result},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
