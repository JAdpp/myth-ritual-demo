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
from contextlib import nullcontext
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
DEFAULT_STAGE_SCHEMA = ROOT / "contracts" / "a1-semantic-review-stage.schema.json"

EXPECTED_REVIEW_MODEL = "deepseek-v4-flash"
EXPECTED_THINKING_MODE = "disabled"
EXPECTED_REASONING_EFFORT: str | None = None
EXPECTED_REVIEW_PROMPT_VERSION = (
    "mengdie-a1-semantic-review-v4.3-nonthinking-codebook-audit"
)
EXPECTED_REVIEW_PROMPT_SHA256 = (
    "9e2ecee699478b4be5ae3108c87e5f32dec9b8884864c6449f07b5cf1df6bc53"
)
EXPECTED_STAGE_PROMPT_VERSIONS = {
    "omission": "mengdie-a1-semantic-review-v4.3a-nonthinking-omission-codebook",
    "contradiction": "mengdie-a1-semantic-review-v4.3b-nonthinking-contradiction-codebook",
}
EXPECTED_STAGE_PROMPT_SHA256 = {
    "omission": "7d925499860a61cddfc8c2733917d19b83aaf190109324535cdab033b0e76134",
    "contradiction": "24d39cac3cc38163a8cc4b8c271714ff7f281fcbe3ef87da0365d10435da0927",
}
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

STAGE_ISSUE_FIELD_BY_CODE = {
    "summary_material_omission": "modernRetrievalSummary",
    "narrative_sufficiency_omission": "narrativeSufficiency",
    "key_entity_omission": "keyEntities",
    "plot_beat_omission": "plotBeats",
    "motif_omission": "motifTerms",
    "life_context_omission": "lifeContext",
    "trigger_omission": "narrativeArc.trigger",
    "conflict_omission": "narrativeArc.conflictTypes",
    "agency_omission": "narrativeArc.agencyModes",
    "ending_omission": "narrativeArc.endingMode",
    "safety_status_omission": "autoSafetyScreen.status",
    "safety_flag_omission": "autoSafetyScreen.flags",
    "safety_uncertainty_omission": "autoSafetyScreen.uncertainties",
    "interpretation_risk_omission": "autoSafetyScreen.interpretationRisks",
    "summary_actor_contradiction": "modernRetrievalSummary",
    "summary_action_object_contradiction": "modernRetrievalSummary",
    "summary_modality_negation_contradiction": "modernRetrievalSummary",
    "summary_time_contradiction": "modernRetrievalSummary",
    "summary_outcome_contradiction": "modernRetrievalSummary",
    "summary_location_contradiction": "modernRetrievalSummary",
    "summary_quantity_contradiction": "modernRetrievalSummary",
    "summary_relation_contradiction": "modernRetrievalSummary",
    "summary_causality_contradiction": "modernRetrievalSummary",
    "summary_other_fact_contradiction": "modernRetrievalSummary",
    "other_version_intrusion": "modernRetrievalSummary",
    "narrative_sufficiency_contradiction": "narrativeSufficiency",
    "key_entity_overreach": "keyEntities",
    "plot_beat_overreach": "plotBeats",
    "motif_overreach": "motifTerms",
    "life_context_overreach": "lifeContext",
    "trigger_contradiction": "narrativeArc.trigger",
    "conflict_overreach": "narrativeArc.conflictTypes",
    "agency_overreach": "narrativeArc.agencyModes",
    "ending_contradiction": "narrativeArc.endingMode",
    "safety_status_contradiction": "autoSafetyScreen.status",
    "safety_flag_overreach": "autoSafetyScreen.flags",
    "safety_uncertainty_contradiction": "autoSafetyScreen.uncertainties",
    "interpretation_risk_overreach": "autoSafetyScreen.interpretationRisks",
    "evidence_support_contradiction": "evidence.supports",
}
OMISSION_STAGE_CODES = frozenset(
    code for code in STAGE_ISSUE_FIELD_BY_CODE if code.endswith("_omission")
)
CONTRADICTION_STAGE_CODES = frozenset(STAGE_ISSUE_FIELD_BY_CODE) - OMISSION_STAGE_CODES
UNIFIED_CODE_BY_STAGE_CODE = {
    "summary_material_omission": "summary_other_fact",
    "narrative_sufficiency_omission": "narrative_sufficiency_mismatch",
    "key_entity_omission": "key_entity_mismatch",
    "plot_beat_omission": "plot_beat_mismatch",
    "motif_omission": "motif_mismatch",
    "life_context_omission": "label_life_context",
    "trigger_omission": "trigger_mismatch",
    "conflict_omission": "label_conflict",
    "agency_omission": "label_agency",
    "ending_omission": "label_ending",
    "safety_status_omission": "safety_status_mismatch",
    "safety_flag_omission": "safety_omission",
    "safety_uncertainty_omission": "safety_uncertainty_mismatch",
    "interpretation_risk_omission": "interpretation_risk_mismatch",
    "summary_actor_contradiction": "summary_actor",
    "summary_action_object_contradiction": "summary_action_object",
    "summary_modality_negation_contradiction": "summary_modality_negation",
    "summary_time_contradiction": "summary_time",
    "summary_outcome_contradiction": "summary_outcome",
    "summary_location_contradiction": "summary_location",
    "summary_quantity_contradiction": "summary_quantity",
    "summary_relation_contradiction": "summary_relation",
    "summary_causality_contradiction": "summary_causality",
    "summary_other_fact_contradiction": "summary_other_fact",
    "other_version_intrusion": "other_version",
    "narrative_sufficiency_contradiction": "narrative_sufficiency_mismatch",
    "key_entity_overreach": "key_entity_mismatch",
    "plot_beat_overreach": "plot_beat_mismatch",
    "motif_overreach": "motif_mismatch",
    "life_context_overreach": "label_life_context",
    "trigger_contradiction": "trigger_mismatch",
    "conflict_overreach": "label_conflict",
    "agency_overreach": "label_agency",
    "ending_contradiction": "label_ending",
    "safety_status_contradiction": "safety_status_mismatch",
    "safety_flag_overreach": "safety_overreach",
    "safety_uncertainty_contradiction": "safety_uncertainty_mismatch",
    "interpretation_risk_overreach": "interpretation_risk_mismatch",
    "evidence_support_contradiction": "evidence_support_mismatch",
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
    "raw_response_json",
    "model",
    "thinking_mode",
    "reasoning_effort",
    "provider_reported_model",
    "prompt_version",
    "prompt_sha256",
    "review_schema_sha256",
}

STAGE_COLUMNS = {
    "entry_id",
    "mode",
    "source_text_sha256",
    "candidate_annotation_sha256",
    "input_json",
    "status",
    "stage_review_json",
    "raw_response_json",
    "model",
    "thinking_mode",
    "reasoning_effort",
    "provider_reported_model",
    "prompt_version",
    "prompt_sha256",
    "stage_schema_sha256",
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
    "base_annotation_json",
    "review_json",
    "status",
    "effective_annotation_json",
    "effective_annotation_sha256",
    "effective_envelope_json",
}


class ConfigurationError(RuntimeError):
    """The command line, schema, or SQLite layout cannot be validated."""


@dataclass
class ValidationReport:
    expected_count: int
    source_count: int = 0
    annotation_count: int = 0
    review_count: int = 0
    stage_count: int = 0
    checked_records: int = 0
    verdict_counts: Counter[str] = field(default_factory=Counter)
    status_counts: Counter[str] = field(default_factory=Counter)
    stage_status_counts: dict[str, Counter[str]] = field(default_factory=dict)
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


def _stage_checks_explicitly_report_no_issues(field_checks: Any) -> bool:
    """Independently prove that all 15 exact fields declared zero codes."""
    if not isinstance(field_checks, dict):
        return False
    if set(field_checks) != set(CHECKLIST_FIELDS):
        return False
    for check in field_checks.values():
        if not isinstance(check, dict):
            return False
        if set(check) != {"checked", "issueCodes"}:
            return False
        if check.get("checked") is not True or check.get("issueCodes") != []:
            return False
    return True


def canonicalize_stage_response_shape(response: dict[str, Any]) -> dict[str, Any]:
    """Mirror the runner's semantics-preserving directional shape closure."""
    canonical = json.loads(json.dumps(response, ensure_ascii=False))
    reviews = canonical.get("reviews")
    if not isinstance(reviews, list):
        return canonical
    for review in reviews:
        if not isinstance(review, dict):
            continue
        field_checks = review.get("fieldChecks")
        if isinstance(field_checks, list):
            mapped: dict[str, Any] = {}
            convertible = True
            for item in field_checks:
                if not isinstance(item, dict) or not isinstance(item.get("field"), str):
                    convertible = False
                    break
                field = item["field"]
                if field in mapped:
                    convertible = False
                    break
                check = dict(item)
                del check["field"]
                mapped[field] = check
            if convertible:
                field_checks = mapped
                review["fieldChecks"] = field_checks
        if isinstance(field_checks, dict):
            for field, raw_check in list(field_checks.items()):
                if not isinstance(raw_check, dict):
                    continue
                check = dict(raw_check)
                if "issueCodes" not in check and "issues" in check:
                    check["issueCodes"] = check.pop("issues")
                field_checks[field] = check
        if "issues" not in review and _stage_checks_explicitly_report_no_issues(
            field_checks
        ):
            review["issues"] = []
    return canonical


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
    candidate_origin: str = "canonical_a1",
    repair_iteration: int = 0,
) -> dict[str, Any]:
    source_text = str(source["text"])
    return {
        "entryId": source["entry_id"],
        "work": source["source_work_title"],
        "title": source["title"],
        "locator": source["source_locator"],
        "sourceTextSha256": source_hash,
        "candidateAnnotationSha256": candidate_hash,
        "candidateOrigin": candidate_origin,
        "repairIteration": repair_iteration,
        "sourceText": source_text,
        "lockedSafetyFlags": deterministic_locked_safety_flags(source_text),
        "candidate": annotation_projection(annotation),
    }


def validated_effective_candidate(
    row: sqlite3.Row,
    *,
    entry_id: str,
    source_text: str,
    source_hash: str,
    canonical_root_sha256: str,
) -> tuple[dict[str, Any], str, int]:
    """Independently reconstruct one valid effective candidate or fail closed."""

    def canonical_object(column: str) -> dict[str, Any]:
        value = parse_json_object(row[column], label=column)
        if row[column] != canonical_json(value):
            raise ValueError(f"{column} is not canonical JSON")
        return value

    if row["source_text_sha256"] != source_hash:
        raise ValueError("effective source hash is not current")
    if row["canonical_root_sha256"] != canonical_root_sha256:
        raise ValueError("effective canonical root hash is not current")
    try:
        iteration = int(row["repair_iteration"])
    except (TypeError, ValueError) as exc:
        raise ValueError("effective repair iteration is invalid") from exc
    if iteration < 1:
        raise ValueError("effective repair iteration is invalid")
    effective = canonical_object("effective_annotation_json")
    effective_hash = sha256_text(canonical_json(effective))
    if row["effective_annotation_sha256"] != effective_hash:
        raise ValueError("effective candidate hash does not close")
    base = canonical_object("base_annotation_json")
    review = canonical_object("review_json")
    if sha256_text(canonical_json(base)) != row["base_candidate_sha256"]:
        raise ValueError("effective immediate-base hash does not close")
    if sha256_text(canonical_json(review)) != row["review_sha256"]:
        raise ValueError("effective source-review hash does not close")
    try:
        lineage = json.loads(row["lineage_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("effective lineage is invalid JSON") from exc
    if (
        not isinstance(lineage, list)
        or len(lineage) != iteration
        or row["lineage_json"] != canonical_json(lineage)
    ):
        raise ValueError("effective lineage length/encoding is invalid")
    prior: str | None = None
    seen: set[str] = set()
    for expected_iteration, item in enumerate(lineage, start=1):
        if not isinstance(item, dict):
            raise ValueError("effective lineage contains a non-object item")
        item_hash = str(item.get("effective_annotation_sha256") or "")
        if (
            item.get("repair_iteration") != expected_iteration
            or item.get("canonical_root_sha256") != canonical_root_sha256
            or not HEX_SHA256.fullmatch(item_hash)
            or item_hash in seen
        ):
            raise ValueError("effective lineage iteration/root/hash closure failed")
        if expected_iteration == 1:
            if (
                item.get("immediate_base_sha256") != canonical_root_sha256
                or item.get("parent_effective_sha256") is not None
            ):
                raise ValueError("effective first-parent closure failed")
        elif (
            item.get("immediate_base_sha256") != prior
            or item.get("parent_effective_sha256") != prior
        ):
            raise ValueError("effective parent closure failed")
        prior = item_hash
        seen.add(item_hash)
    if prior != effective_hash:
        raise ValueError("effective lineage does not end at current candidate")
    expected_parent = None if iteration == 1 else lineage[-2]["effective_annotation_sha256"]
    expected_base = canonical_root_sha256 if iteration == 1 else expected_parent
    expected_origin = "canonical_a1" if iteration == 1 else "effective_repair"
    if (
        row["parent_effective_sha256"] != expected_parent
        or row["base_candidate_sha256"] != expected_base
        or row["immediate_base_origin"] != expected_origin
        or lineage[-1].get("review_sha256") != row["review_sha256"]
        or lineage[-1].get("immediate_base_sha256") != row["base_candidate_sha256"]
        or lineage[-1].get("parent_effective_sha256")
        != row["parent_effective_sha256"]
    ):
        raise ValueError("effective current-parent metadata does not close")
    envelope = canonical_object("effective_envelope_json")
    expected_envelope_values = {
        "entry_id": entry_id,
        "source_text_sha256": source_hash,
        "canonical_root_sha256": canonical_root_sha256,
        "base_candidate_sha256": row["base_candidate_sha256"],
        "parent_effective_sha256": row["parent_effective_sha256"],
        "repair_iteration": iteration,
        "immediate_base_origin": row["immediate_base_origin"],
        "review_sha256": row["review_sha256"],
        "effective_annotation_sha256": effective_hash,
        "human_reviewed": False,
        "research_ready": False,
    }
    if any(envelope.get(key) != value for key, value in expected_envelope_values.items()):
        raise ValueError("effective envelope provenance does not close")
    if (
        envelope.get("lineage") != lineage
        or envelope.get("base_annotation") != base
        or envelope.get("review") != review
        or envelope.get("effective_annotation") != effective
    ):
        raise ValueError("effective envelope content does not close")
    try:
        record = effective["annotation_record"]
        meta = record["annotation_meta"]
        provenance = meta["repair_provenance"]
        checks = (
            record["unit"]["unit_id"] == entry_id,
            record["unit"]["source_text_sha256"] == source_hash,
            record["source_profile"]["source_text_sha256"] == source_hash,
            record["source_profile"]["source_text"] == source_text,
            meta["research_review_status"] == "not_reviewed",
            meta["research_ready"] is False,
            meta["human_review"]["reviewed"] is False,
            provenance["canonical_root_sha256"] == canonical_root_sha256,
            provenance["base_candidate_sha256"] == row["base_candidate_sha256"],
            provenance["immediate_base_sha256"] == row["base_candidate_sha256"],
            provenance["parent_effective_sha256"] == row["parent_effective_sha256"],
            provenance["repair_iteration"] == iteration,
            provenance["immediate_base_origin"] == row["immediate_base_origin"],
            provenance["review_sha256"] == row["review_sha256"],
            provenance["automatic_semantic_repair"] is True,
            provenance["human_reviewed"] is False,
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("effective annotation provenance is incomplete") from exc
    if not all(checks):
        raise ValueError("effective annotation provenance does not close")
    return effective, effective_hash, iteration


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


def candidate_values_for_field(
    candidate: Mapping[str, Any], field_name: str
) -> tuple[str, ...]:
    if field_name == "modernRetrievalSummary":
        return (str(candidate["modernRetrievalSummary"]),)
    if field_name == "narrativeSufficiency":
        return (str(candidate["narrativeSufficiency"]),)
    if field_name == "keyEntities":
        return tuple(
            str(value)
            for item in candidate["keyEntities"]
            for key, value in item.items()
            if key in {"name", "role"} and isinstance(value, str)
        )
    if field_name == "plotBeats":
        return tuple(
            str(value)
            for item in candidate["plotBeats"]
            for key, value in item.items()
            if key in {"type", "text"} and isinstance(value, str)
        )
    if field_name == "motifTerms":
        return tuple(str(value) for value in candidate["motifTerms"])
    if field_name == "lifeContext":
        return tuple(str(value) for value in candidate["lifeContext"])
    if field_name == "narrativeArc.trigger":
        return (str(candidate["narrativeArc"]["trigger"]),)
    if field_name == "narrativeArc.conflictTypes":
        return tuple(str(value) for value in candidate["narrativeArc"]["conflictTypes"])
    if field_name == "narrativeArc.agencyModes":
        return tuple(str(value) for value in candidate["narrativeArc"]["agencyModes"])
    if field_name == "narrativeArc.endingMode":
        return (str(candidate["narrativeArc"]["endingMode"]),)
    if field_name == "autoSafetyScreen.status":
        return (str(candidate["autoSafetyScreen"]["status"]),)
    if field_name == "autoSafetyScreen.flags":
        return tuple(str(value) for value in candidate["autoSafetyScreen"]["flags"])
    if field_name == "autoSafetyScreen.interpretationRisks":
        return tuple(
            str(value) for value in candidate["autoSafetyScreen"]["interpretationRisks"]
        )
    if field_name == "autoSafetyScreen.uncertainties":
        return tuple(
            str(value) for value in candidate["autoSafetyScreen"]["uncertainties"]
        )
    if field_name == "evidence.supports":
        return tuple(
            str(value)
            for item in candidate["evidence"]
            for value in item["supports"]
        )
    raise KeyError(field_name)


def candidate_contains_target(
    candidate: Mapping[str, Any], field_name: str, target: str
) -> bool:
    values = candidate_values_for_field(candidate, field_name)
    if field_name in {
        "modernRetrievalSummary",
        "narrativeArc.trigger",
        "autoSafetyScreen.uncertainties",
    }:
        return any(target == value or target in value for value in values)
    return target in values


def validate_stage_semantics(
    stage: Mapping[str, Any],
    *,
    mode: str,
    entry_id: str,
    source_text: str,
    candidate_projection: Mapping[str, Any],
    locked_safety_flags: Iterable[str],
) -> list[str]:
    errors: list[str] = []
    if stage.get("entryId") != entry_id:
        errors.append(f"{mode} stage entryId does not match current entry")
    checks = stage.get("fieldChecks")
    if not isinstance(checks, dict) or set(checks) != set(CHECKLIST_FIELDS):
        return errors + [f"{mode} stage fieldChecks field set is not exact"]
    issues = stage.get("issues")
    if not isinstance(issues, list):
        return errors + [f"{mode} stage issues is not an array"]
    allowed_codes = OMISSION_STAGE_CODES if mode == "omission" else CONTRADICTION_STAGE_CODES
    deletion_markers = (
        "删除", "刪除", "移除", "去除", "去掉", "误标", "誤標", "过度", "過度",
        "不应", "不應",
    )
    addition_markers = (
        "遗漏", "遺漏", "补入", "補入", "补充", "補充", "添加", "加入", "增加",
        "增补", "增補",
    )
    omission_actions = (*addition_markers[2:], "改为", "改為", "改成")
    contradiction_actions = (
        *deletion_markers,
        "替换", "替換", "改为", "改為", "改成", "应为", "應為",
    )
    issue_codes_by_field: dict[str, set[str]] = {
        field_name: set() for field_name in CHECKLIST_FIELDS
    }
    seen: set[tuple[str, str, str, str]] = set()
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            errors.append(f"{mode} stage issue[{index}] is not an object")
            continue
        code = str(issue.get("code"))
        field_name = str(issue.get("field"))
        target_value = issue.get("targetValue")
        excerpt = issue.get("sourceExcerpt")
        hint = issue.get("correctionHint")
        if code not in allowed_codes:
            errors.append(f"{mode} stage issue[{index}] uses a wrong-direction code")
        if STAGE_ISSUE_FIELD_BY_CODE.get(code) != field_name:
            errors.append(f"{mode} stage issue[{index}] code-field mapping is invalid")
        if not isinstance(target_value, str) or target_value != target_value.strip() or not target_value:
            errors.append(f"{mode} stage issue[{index}] targetValue is invalid")
            target = ""
        else:
            target = target_value
            if target in {"整体", "整體", "若干", "相关标签", "相關標籤", "相关内容", "相關內容", "字段"}:
                errors.append(f"{mode} stage issue[{index}] targetValue is generic")
        try:
            present = candidate_contains_target(candidate_projection, field_name, target)
        except (KeyError, TypeError):
            present = False
        if mode == "omission" and present:
            errors.append(f"{mode} stage issue[{index}] target already exists")
        if mode == "contradiction" and not present:
            errors.append(f"{mode} stage issue[{index}] target is absent")
        if not isinstance(hint, str):
            errors.append(f"{mode} stage issue[{index}] correctionHint is invalid")
            hint_text = ""
        else:
            hint_text = hint
            if any(marker in hint_text for marker in SOFT_REVISE_HINT_MARKERS):
                errors.append(f"{mode} stage issue[{index}] correctionHint is soft")
            if mode == "omission" and any(marker in hint_text for marker in deletion_markers):
                errors.append(f"{mode} stage issue[{index}] attempts deletion")
            if mode == "contradiction" and any(marker in hint_text for marker in addition_markers):
                errors.append(f"{mode} stage issue[{index}] attempts addition")
            if mode == "omission" and not any(marker in hint_text for marker in omission_actions):
                errors.append(f"{mode} stage issue[{index}] lacks an addition action")
            if mode == "contradiction" and not any(
                marker in hint_text for marker in contradiction_actions
            ):
                errors.append(f"{mode} stage issue[{index}] lacks a removal action")
        if not isinstance(excerpt, str) or excerpt not in source_text:
            errors.append(f"{mode} stage issue[{index}] excerpt is not byte-exact")
            excerpt_text = str(excerpt)
        else:
            excerpt_text = excerpt
        if code == "safety_flag_overreach" and target in set(locked_safety_flags):
            errors.append(f"{mode} stage issue[{index}] questions a locked safety flag")
        if code == "evidence_support_contradiction":
            try:
                linked = any(
                    target in item["supports"]
                    and (
                        excerpt_text == item["excerpt"]
                        or excerpt_text in item["excerpt"]
                        or item["excerpt"] in excerpt_text
                    )
                    for item in candidate_projection["evidence"]
                )
            except (KeyError, TypeError):
                linked = False
            if not linked:
                errors.append(f"{mode} stage issue[{index}] is not evidence-linked")
        signature = (code, field_name, target, excerpt_text)
        if signature in seen:
            errors.append(f"{mode} stage issue[{index}] duplicates an earlier issue")
        seen.add(signature)
        if field_name in issue_codes_by_field:
            issue_codes_by_field[field_name].add(code)
    for field_name in CHECKLIST_FIELDS:
        check = checks.get(field_name)
        if (
            not isinstance(check, dict)
            or check.get("checked") is not True
            or set(check) != {"checked", "issueCodes"}
            or not isinstance(check.get("issueCodes"), list)
            or set(check["issueCodes"]) != issue_codes_by_field[field_name]
        ):
            errors.append(f"{mode} stage fieldChecks mismatch for {field_name}")
    return errors


def merge_directional_stages(
    omission: Mapping[str, Any], contradiction: Mapping[str, Any], *, entry_id: str
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    false_fields: set[str] = set()
    for stage in (omission, contradiction):
        for issue in stage["issues"]:
            code = UNIFIED_CODE_BY_STAGE_CODE[issue["code"]]
            field_name = issue["field"]
            excerpt = issue["sourceExcerpt"]
            hint = issue["correctionHint"]
            signature = (code, field_name, excerpt, hint)
            if signature in seen:
                continue
            seen.add(signature)
            false_fields.add(field_name)
            issues.append(
                {
                    "code": code,
                    "field": field_name,
                    "sourceExcerpt": excerpt,
                    "correctionHint": hint,
                }
            )
    issues.sort(
        key=lambda item: (
            CHECKLIST_FIELDS.index(item["field"]),
            item["code"],
            item["sourceExcerpt"],
            item["correctionHint"],
        )
    )
    return {
        "entryId": entry_id,
        "verdict": "revise" if issues else "pass",
        "checklist": {
            field_name: field_name not in false_fields for field_name in CHECKLIST_FIELDS
        },
        "issues": issues,
    }


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
    stage_schema: Path = DEFAULT_STAGE_SCHEMA,
    effective_db: Path | None = None,
    expected_count: int,
    allow_revise: bool = False,
    allow_uncertain: bool = False,
    expected_model: str = EXPECTED_REVIEW_MODEL,
    expected_thinking_mode: str = EXPECTED_THINKING_MODE,
    expected_reasoning_effort: str | None = EXPECTED_REASONING_EFFORT,
    expected_prompt_version: str = EXPECTED_REVIEW_PROMPT_VERSION,
    expected_prompt_sha256: str = EXPECTED_REVIEW_PROMPT_SHA256,
    expected_stage_prompt_versions: Mapping[str, str] = EXPECTED_STAGE_PROMPT_VERSIONS,
    expected_stage_prompt_sha256: Mapping[str, str] = EXPECTED_STAGE_PROMPT_SHA256,
    max_errors: int = 100,
) -> ValidationReport:
    if expected_count <= 0:
        raise ConfigurationError("--expected-count must be a positive integer")
    if max_errors <= 0:
        raise ConfigurationError("--max-errors must be a positive integer")
    if expected_thinking_mode != EXPECTED_THINKING_MODE:
        raise ConfigurationError("v4.3 validator requires thinking_mode=disabled")
    if expected_reasoning_effort is not None:
        raise ConfigurationError(
            "v4.3 does not use a separate reasoning_effort"
        )
    if not HEX_SHA256.fullmatch(expected_prompt_sha256):
        raise ConfigurationError("expected prompt SHA-256 must be 64 lowercase hex chars")
    if not review_schema.is_file():
        raise ConfigurationError(f"review schema does not exist: {review_schema}")
    if not stage_schema.is_file():
        raise ConfigurationError(f"stage schema does not exist: {stage_schema}")
    if set(expected_stage_prompt_versions) != {"omission", "contradiction"}:
        raise ConfigurationError("expected stage prompt versions must cover both modes")
    if set(expected_stage_prompt_sha256) != {"omission", "contradiction"} or any(
        not HEX_SHA256.fullmatch(value)
        for value in expected_stage_prompt_sha256.values()
    ):
        raise ConfigurationError("expected stage prompt hashes must cover both modes")
    try:
        schema = json.loads(review_schema.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        directional_schema = json.loads(stage_schema.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(directional_schema)
    except (json.JSONDecodeError, OSError, SchemaError) as exc:
        raise ConfigurationError(f"invalid review schema: {exc}") from exc
    schema_validator = Draft202012Validator(
        schema, format_checker=FormatChecker()
    )
    stage_schema_validator = Draft202012Validator(
        directional_schema, format_checker=FormatChecker()
    )
    schema_sha256 = sha256_file(review_schema)
    stage_schema_sha256 = sha256_file(stage_schema)

    report = ValidationReport(expected_count=expected_count, max_errors=max_errors)
    effective_context = (
        readonly_connection(effective_db) if effective_db is not None else nullcontext(None)
    )
    with readonly_connection(source_db) as source_conn, readonly_connection(
        annotation_db
    ) as annotation_conn, readonly_connection(review_db) as review_conn, effective_context as effective_conn:
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
        if effective_conn is not None:
            require_integrity(effective_conn, "effective repair")
            require_columns(
                effective_conn,
                "effective_repair_jobs",
                EFFECTIVE_COLUMNS,
                "effective repair",
            )
        require_columns(
            review_conn,
            "semantic_review_stages",
            STAGE_COLUMNS,
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
        report.stage_count = int(
            review_conn.execute("SELECT COUNT(*) FROM semantic_review_stages").fetchone()[0]
        )
        report.status_counts = status_counts(review_conn, "semantic_review_jobs")
        report.stage_status_counts = {
            mode: Counter(
                {
                    str(row["status"]): int(row["count"])
                    for row in review_conn.execute(
                        """
                        SELECT status,COUNT(*) count FROM semantic_review_stages
                        WHERE mode=? GROUP BY status
                        """,
                        (mode,),
                    )
                }
            )
            for mode in ("omission", "contradiction")
        }

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
        if report.stage_count != report.source_count * 2:
            report.fail(
                f"directional stage row count {report.stage_count} != "
                f"2 * C1 selected count {report.source_count}"
            )
        if effective_conn is not None:
            canonical_ids = {
                str(row[0])
                for row in source_conn.execute(
                    """
                    SELECT entry_id FROM entries
                    WHERE dedupe_status='canonical' AND runtime_eligible=1
                    """
                )
            }
            effective_ids = {
                str(row[0])
                for row in effective_conn.execute(
                    "SELECT entry_id FROM effective_repair_jobs"
                )
            }
            orphan_effective = sorted(effective_ids - canonical_ids)
            if orphan_effective:
                report.fail(
                    f"effective repair has {len(orphan_effective)} non-canonical entry IDs"
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
        for mode, counts in report.stage_status_counts.items():
            invalid_stage_states = {
                name: count for name, count in counts.items() if name != "valid"
            }
            if invalid_stage_states or counts.get("valid", 0) != report.source_count:
                report.fail(
                    f"{mode} stage has incomplete/disallowed states: "
                    f"{dict(sorted(counts.items()))}"
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
                canonical_annotation = parse_json_object(
                    annotation_row["annotation_json"], label="annotation_json"
                )
                record = canonical_annotation["annotation_record"]
                if record["unit"]["unit_id"] != entry_id:
                    raise ValueError("unit_id does not match entry_id")
                if record["unit"]["source_text_sha256"] != source_hash:
                    raise ValueError("unit source hash is not current")
                profile = record["source_profile"]
                if profile["source_text_sha256"] != source_hash:
                    raise ValueError("source_profile hash is not current")
                if profile["source_text"] != source_text:
                    raise ValueError("source_profile text does not match C1")
            except (KeyError, TypeError, ValueError) as exc:
                report.fail(f"{entry_id}: invalid/current A1 JSON contract: {exc}")
                continue
            canonical_hash = sha256_text(canonical_json(canonical_annotation))
            annotation = canonical_annotation
            candidate_hash = canonical_hash
            candidate_origin = "canonical_a1"
            repair_iteration = 0
            if effective_conn is not None:
                effective_row = effective_conn.execute(
                    "SELECT * FROM effective_repair_jobs WHERE entry_id=?",
                    (entry_id,),
                ).fetchone()
                if effective_row is not None and effective_row["status"] == "valid":
                    try:
                        annotation, candidate_hash, repair_iteration = (
                            validated_effective_candidate(
                                effective_row,
                                entry_id=entry_id,
                                source_text=source_text,
                                source_hash=source_hash,
                                canonical_root_sha256=canonical_hash,
                            )
                        )
                        candidate_origin = "effective_repair"
                    except (KeyError, TypeError, ValueError) as exc:
                        report.fail(f"{entry_id}: invalid effective candidate: {exc}")
                        continue
            try:
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
                report.fail(f"{entry_id}: invalid current review candidate: {exc}")
                continue

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
            if review_row["thinking_mode"] != expected_thinking_mode:
                report.fail(f"{entry_id}: review thinking_mode is not current")
            if review_row["reasoning_effort"] != expected_reasoning_effort:
                report.fail(f"{entry_id}: review reasoning_effort is not current")
            if review_row["provider_reported_model"] != expected_model:
                report.fail(f"{entry_id}: provider_reported_model is missing or mismatched")
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
                candidate_origin=candidate_origin,
                repair_iteration=repair_iteration,
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

            directional_reviews: dict[str, dict[str, Any]] = {}
            for mode in ("omission", "contradiction"):
                stage_row = review_conn.execute(
                    """
                    SELECT * FROM semantic_review_stages
                    WHERE entry_id=? AND mode=?
                    """,
                    (entry_id, mode),
                ).fetchone()
                if stage_row is None:
                    report.fail(f"{entry_id}: missing {mode} directional stage")
                    continue
                if stage_row["status"] != "valid":
                    report.fail(
                        f"{entry_id}: {mode} stage status is {stage_row['status']!r}"
                    )
                if stage_row["source_text_sha256"] != source_hash:
                    report.fail(f"{entry_id}: {mode} stage source hash is not current")
                if stage_row["candidate_annotation_sha256"] != candidate_hash:
                    report.fail(f"{entry_id}: {mode} stage candidate hash is not current")
                if stage_row["input_json"] != canonical_json(expected_input):
                    report.fail(f"{entry_id}: {mode} stage input_json is not current/canonical")
                if stage_row["model"] != expected_model:
                    report.fail(f"{entry_id}: {mode} stage model is not current")
                if stage_row["thinking_mode"] != expected_thinking_mode:
                    report.fail(f"{entry_id}: {mode} stage thinking_mode is not current")
                if stage_row["reasoning_effort"] != expected_reasoning_effort:
                    report.fail(f"{entry_id}: {mode} stage reasoning_effort is not current")
                if stage_row["provider_reported_model"] != expected_model:
                    report.fail(
                        f"{entry_id}: {mode} stage provider model is missing or mismatched"
                    )
                if stage_row["prompt_version"] != expected_stage_prompt_versions[mode]:
                    report.fail(f"{entry_id}: {mode} stage prompt version is not current")
                if stage_row["prompt_sha256"] != expected_stage_prompt_sha256[mode]:
                    report.fail(f"{entry_id}: {mode} stage prompt hash is not current")
                if stage_row["stage_schema_sha256"] != stage_schema_sha256:
                    report.fail(f"{entry_id}: {mode} stage schema hash is not current")
                try:
                    stage_review = parse_json_object(
                        stage_row["stage_review_json"],
                        label=f"{mode} stage_review_json",
                    )
                except ValueError as exc:
                    report.fail(f"{entry_id}: {exc}")
                    continue
                if stage_row["stage_review_json"] != canonical_json(stage_review):
                    report.fail(f"{entry_id}: {mode} stage_review_json is not canonical")
                stage_response = {"mode": mode, "reviews": [stage_review]}
                for schema_error in sorted(
                    stage_schema_validator.iter_errors(stage_response),
                    key=lambda error: list(error.absolute_path),
                ):
                    report.fail(
                        f"{entry_id}: {mode} {schema_error_message(schema_error)}"
                    )
                for semantic_error in validate_stage_semantics(
                    stage_review,
                    mode=mode,
                    entry_id=entry_id,
                    source_text=source_text,
                    candidate_projection=projection,
                    locked_safety_flags=locked_flags,
                ):
                    report.fail(f"{entry_id}: {semantic_error}")
                try:
                    raw_stage = parse_json_object(
                        stage_row["raw_response_json"],
                        label=f"{mode} raw_response_json",
                    )
                except ValueError as exc:
                    report.fail(f"{entry_id}: {exc}")
                else:
                    if stage_row["raw_response_json"] != canonical_json(raw_stage):
                        report.fail(f"{entry_id}: {mode} raw response is not canonical")
                    canonical_raw_stage = canonicalize_stage_response_shape(raw_stage)
                    raw_errors = sorted(
                        stage_schema_validator.iter_errors(canonical_raw_stage),
                        key=lambda error: list(error.absolute_path),
                    )
                    for schema_error in raw_errors:
                        report.fail(
                            f"{entry_id}: {mode} raw {schema_error_message(schema_error)}"
                        )
                    raw_reviews = canonical_raw_stage.get("reviews")
                    if isinstance(raw_reviews, list):
                        matching = [
                            item
                            for item in raw_reviews
                            if isinstance(item, dict) and item.get("entryId") == entry_id
                        ]
                        if matching != [stage_review]:
                            report.fail(
                                f"{entry_id}: {mode} stage row does not close to raw response"
                            )
                directional_reviews[mode] = stage_review

            if set(directional_reviews) == {"omission", "contradiction"}:
                expected_merged = merge_directional_stages(
                    directional_reviews["omission"],
                    directional_reviews["contradiction"],
                    entry_id=entry_id,
                )
                if review != expected_merged:
                    report.fail(
                        f"{entry_id}: merged review_json is not the deterministic stage union"
                    )
                try:
                    merged_raw = parse_json_object(
                        review_row["raw_response_json"], label="merged raw_response_json"
                    )
                except ValueError as exc:
                    report.fail(f"{entry_id}: {exc}")
                else:
                    if review_row["raw_response_json"] != canonical_json(merged_raw):
                        report.fail(f"{entry_id}: merged raw response is not canonical")
                    if (
                        merged_raw.get("mode") != "merged"
                        or merged_raw.get("reviewVersion") != expected_prompt_version
                        or merged_raw.get("stagePromptVersions")
                        != dict(expected_stage_prompt_versions)
                        or merged_raw.get("stagePromptSha256")
                        != dict(expected_stage_prompt_sha256)
                        or merged_raw.get("thinkingMode") != expected_thinking_mode
                        or merged_raw.get("reasoningEffort")
                        != expected_reasoning_effort
                    ):
                        report.fail(f"{entry_id}: merged raw response provenance is invalid")
                    merged_stages = merged_raw.get("stageReviews")
                    if (
                        not isinstance(merged_stages, dict)
                        or merged_stages.get(entry_id) != directional_reviews
                    ):
                        report.fail(
                            f"{entry_id}: merged raw response does not contain both exact stages"
                        )

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only final validator for A1 semantic-review coverage"
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--annotation-db", type=Path, default=DEFAULT_ANNOTATION_DB)
    parser.add_argument(
        "--effective-db",
        type=Path,
        help="Optional effective-repair ledger overlaid exactly as in reviewer v4.",
    )
    parser.add_argument("--review-db", type=Path, default=DEFAULT_REVIEW_DB)
    parser.add_argument("--review-schema", type=Path, default=DEFAULT_REVIEW_SCHEMA)
    parser.add_argument("--stage-schema", type=Path, default=DEFAULT_STAGE_SCHEMA)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--allow-revise", action="store_true")
    parser.add_argument("--allow-uncertain", action="store_true")
    parser.add_argument("--expected-model", default=EXPECTED_REVIEW_MODEL)
    parser.add_argument(
        "--expected-thinking-mode",
        choices=[EXPECTED_THINKING_MODE],
        default=EXPECTED_THINKING_MODE,
    )
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
        f"reviews={report.review_count}, stages={report.stage_count}, "
        f"checked={report.checked_records}, "
        f"expected={report.expected_count}"
    )
    print(f"  review statuses: {dict(sorted(report.status_counts.items()))}")
    print(
        "  directional stage statuses: "
        f"{ {mode: dict(sorted(counts.items())) for mode, counts in report.stage_status_counts.items()} }"
    )
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
            effective_db=args.effective_db,
            review_db=args.review_db,
            review_schema=args.review_schema,
            stage_schema=args.stage_schema,
            expected_count=args.expected_count,
            allow_revise=args.allow_revise,
            allow_uncertain=args.allow_uncertain,
            expected_model=args.expected_model,
            expected_thinking_mode=args.expected_thinking_mode,
            expected_reasoning_effort=EXPECTED_REASONING_EFFORT,
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
