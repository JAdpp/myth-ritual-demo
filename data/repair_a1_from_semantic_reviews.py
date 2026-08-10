#!/usr/bin/env python3
"""Create resumable, non-destructive effective A1 repairs from semantic reviews.

The canonical C1 catalog, canonical A1 annotations, and semantic-review ledger
are opened read-only.  This worker writes a fourth, independent sidecar.  It
never promotes automatic repair to human review or research-ready status.

Reviews may target either the immutable canonical A1 root or the current valid
effective repair.  Each new layer must name its immediate parent, increments a
bounded lineage counter, and archives the superseded provider artifacts.

The current canonical A1 schema fixes the first-pass prompt version.  A repair
therefore retains three distinguishable artifacts in an effective envelope:
the original A1 record, the exact record returned by the canonical normalizer,
and an effective copy whose metadata truthfully names the repair prompt.  The
last copy is validated against a derived sidecar schema and is explicitly not
claimed to conform to the canonical A1 schema.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock
from typing import Any, Iterable, Sequence

import httpx
from dotenv import load_dotenv
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

try:  # package import in tests
    from data.annotate_c1_retrieval import (
        AnnotationError,
        PROMPT_VERSION as BASE_PROMPT_VERSION,
        SYSTEM_PROMPT as BASE_ANNOTATION_PROMPT,
        SourceEntry,
        normalise_annotation,
    )
except ImportError:  # direct ``python data/repair_a1_from_semantic_reviews.py``
    from annotate_c1_retrieval import (  # type: ignore[no-redef]
        AnnotationError,
        PROMPT_VERSION as BASE_PROMPT_VERSION,
        SYSTEM_PROMPT as BASE_ANNOTATION_PROMPT,
        SourceEntry,
        normalise_annotation,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT / "data" / "corpus" / "c1_single_story" / "catalog.sqlite3"
DEFAULT_ANNOTATION_DB = (
    ROOT / "data" / "corpus" / "a1_retrieval_annotations" / "annotations.sqlite3"
)
DEFAULT_REVIEW_DB = (
    ROOT / "data" / "corpus" / "a1_semantic_reviews" / "reviews.sqlite3"
)
DEFAULT_EFFECTIVE_DB = (
    ROOT / "data" / "corpus" / "a1_effective_repairs" / "effective.sqlite3"
)
DEFAULT_CANONICAL_SCHEMA = ROOT / "contracts" / "a1-retrieval-annotation.schema.json"
DEFAULT_ENVELOPE_SCHEMA = ROOT / "contracts" / "a1-effective-repair-envelope.schema.json"
DEFAULT_MANIFEST = ROOT / "data" / "corpus" / "a1_effective_repairs" / "manifest.json"

SQLITE_BUSY_TIMEOUT_MS = 30_000
EFFECTIVE_RECORD_VERSION = "mengdie-a1-effective-repair-v2"
REPAIR_VERSION = "mengdie-a1-semantic-repair-v2"
REPAIR_PROMPT_VERSION = "mengdie-a1-semantic-repair-v2-nonthinking-iterative"
DEFAULT_MAX_REPAIR_ITERATIONS = 3

CHECKLIST_FIELDS = {
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
}

REPAIR_INSTRUCTION = """

你现在执行独立的语义修订。每条输入都含完整 sourceText、当前 immediate candidate、repairIteration、以及刚刚针对该 candidate 生成的 reviewVerdict 与 reviewIssues。candidate 可能是 canonical 首轮标注，也可能是上一轮 effective 修订；不得退回更早版本。上一轮审校不是人工审核，也不是真理；issue 中的 sourceExcerpt 是必须核对的逐字反证起点。唯一事实来源仍是当前条目的完整 sourceText，严禁借用其他版本、常识或同批其他条目的情节。

请重新生成每条完整 raw A1 annotation，而不是只返回补丁：
1. 逐项修复 reviewIssues 指向的主体、动作、对象、否定、条件、梦境/传闻、时间、结果、叙事充分性、实体、情节节点、标签、结局、安全旗标、解释风险与 evidence.supports 问题。
2. 不因 reviewer 写了 correctionHint 就机械照抄；必须回到 sourceText 验证。reviewVerdict=uncertain 时采取保守标签和 low confidence，并在 uncertainties 说明自动修订仍存在语义不确定性。
3. 所有事实、标签与安全判断仍须给出当前 sourceText 中连续逐字证据；不要把现代释义当证据。不得删除原文明示的高风险内容。sexual_violence 必须同时包含 sexual_content 与 coercion_or_abuse。
4. 当前 candidate 只用于定位待修订处，不得复制其无证据说法；生成结果必须自洽、完整、可独立通过 A1 Schema/简体/证据/安全硬门。

只输出 JSON 对象 {"annotations":[...]}。annotations 必须与请求记录一一对应，原样返回 entryId，使用原 A1 raw annotation 字段契约；不要输出 annotation_record、差异说明、思考过程或 Markdown。
""".strip()

SYSTEM_PROMPT = BASE_ANNOTATION_PROMPT.rstrip() + "\n\n" + REPAIR_INSTRUCTION


class RepairValidationError(RuntimeError):
    """A provider response cannot be admitted to the effective sidecar."""


class RetryableProviderError(RuntimeError):
    """A provider or transport failure may be resumed later."""


class FatalProviderError(RuntimeError):
    """Provider configuration is invalid; the live run must stop."""


@dataclass(frozen=True)
class RepairCandidate:
    source: SourceEntry
    base_annotation: dict[str, Any]
    base_candidate_sha256: str
    review: dict[str, Any]
    review_sha256: str
    review_verdict: str
    review_prompt_version: str
    review_prompt_sha256: str
    review_schema_sha256: str
    catalog_version: str
    canonical_root_sha256: str
    parent_effective_sha256: str | None
    repair_iteration: int
    immediate_base_origin: str
    prior_lineage: tuple[dict[str, Any], ...]

    @property
    def entry_id(self) -> str:
        return self.source.entry_id

    @property
    def char_count(self) -> int:
        return self.source.char_count

    def provider_input(self) -> dict[str, Any]:
        """Complete, retained provider input; source text is never truncated."""
        return {
            "entryId": self.entry_id,
            "work": self.source.source_work_title,
            "volume": self.source.volume,
            "locator": self.source.source_locator,
            "title": self.source.title,
            "sourceTextSha256": self.source.extracted_text_sha256,
            "baseCandidateSha256": self.base_candidate_sha256,
            "canonicalRootSha256": self.canonical_root_sha256,
            "parentEffectiveSha256": self.parent_effective_sha256,
            "repairIteration": self.repair_iteration,
            "immediateBaseOrigin": self.immediate_base_origin,
            "reviewSha256": self.review_sha256,
            "reviewProvenance": {
                "promptVersion": self.review_prompt_version,
                "promptSha256": self.review_prompt_sha256,
                "schemaSha256": self.review_schema_sha256,
            },
            "sourceText": self.source.text,
            "candidate": self.base_annotation,
            "reviewVerdict": self.review_verdict,
            "reviewIssues": self.review["issues"],
        }


@dataclass(frozen=True)
class WorkerResult:
    worker_id: int
    batches_processed: int
    provider_calls: int
    fatal_reason: str | None = None
    unexpected_reason: str | None = None


class ProviderCallBudget:
    """A process-wide hard call cap shared by every repair worker."""

    def __init__(self, max_calls: int) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        self.max_calls = max_calls
        self.used = 0
        self._lock = Lock()

    def reserve(self) -> None:
        with self._lock:
            if self.used >= self.max_calls:
                raise FatalProviderError("provider call budget exhausted")
            self.used += 1


class ProgressTracker:
    def __init__(self, total_batches: int) -> None:
        self.total_batches = total_batches
        self.completed_batches = 0
        self.provider_calls = 0
        self._lock = Lock()

    def record_batch(
        self,
        *,
        worker_id: int,
        batch_index: int,
        records_in_batch: int,
        provider_calls: int,
        local_counts: dict[str, int],
        global_counts: dict[str, int],
        outcome: str,
    ) -> None:
        with self._lock:
            self.completed_batches += 1
            self.provider_calls += provider_calls
            print(
                json.dumps(
                    {
                        "event": "progress",
                        "worker": worker_id,
                        "batch": batch_index,
                        "batches": self.total_batches,
                        "completedBatches": self.completed_batches,
                        "recordsInBatch": records_in_batch,
                        "providerCallsThisBatch": provider_calls,
                        "currentRunProviderCalls": self.provider_calls,
                        "batchStatusCounts": local_counts,
                        "statusCounts": global_counts,
                        "outcome": outcome,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def compact_error(exc: BaseException) -> str:
    message = re.sub(r"\s+", " ", str(exc)).strip()
    message = re.sub(r"sk-[A-Za-z0-9._-]+", "[redacted]", message)
    return message[:500]


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def _validate_review(
    review: dict[str, Any], *, entry_id: str, verdict: str, source_text: str
) -> None:
    if review.get("entryId") != entry_id or review.get("verdict") != verdict:
        raise RuntimeError(f"semantic review identity/verdict mismatch for {entry_id}")
    issues = review.get("issues")
    if not isinstance(issues, list) or not issues:
        raise RuntimeError(f"repairable review has no issues for {entry_id}")
    checklist = review.get("checklist")
    if not isinstance(checklist, dict) or set(checklist) != CHECKLIST_FIELDS:
        raise RuntimeError(f"semantic review checklist contract is invalid for {entry_id}")
    if any(not isinstance(value, bool) for value in checklist.values()):
        raise RuntimeError(f"semantic review checklist is not boolean for {entry_id}")
    issue_fields: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            raise RuntimeError(f"malformed semantic review issue for {entry_id}")
        excerpt = issue.get("sourceExcerpt")
        if not isinstance(excerpt, str) or not 2 <= len(excerpt) <= 80:
            raise RuntimeError(f"review issue excerpt length is invalid for {entry_id}")
        if excerpt not in source_text:
            raise RuntimeError(f"review issue excerpt is not exact source text for {entry_id}")
        field = issue.get("field")
        if field not in CHECKLIST_FIELDS:
            raise RuntimeError(f"review issue field is invalid for {entry_id}")
        issue_fields.add(str(field))
    false_fields = {field for field, value in checklist.items() if value is False}
    if false_fields != issue_fields:
        raise RuntimeError(f"semantic review checklist/issues mismatch for {entry_id}")


def load_repair_candidates(
    source_db: Path,
    annotation_db: Path,
    review_db: Path,
    effective_db: Path,
    *,
    verdicts: set[str],
    canonical_schema_validator: Draft202012Validator,
    effective_schema_validator: Draft202012Validator,
    envelope_schema_validator: Draft202012Validator,
    max_repair_iterations: int,
) -> tuple[list[RepairCandidate], int]:
    """Resolve each review against its immediate canonical/effective base."""
    with closing(readonly_connection(source_db)) as conn:
        source_rows = conn.execute(
            """
            SELECT entry_id,source_work_id,source_work_title,source_work_period,
                   volume,source_locator,entry_ordinal,title,source_url,char_count,
                   text,extracted_text_sha256
            FROM entries
            WHERE dedupe_status='canonical' AND runtime_eligible=1
            ORDER BY source_work_id,entry_ordinal,entry_id
            """
        ).fetchall()
    if not source_rows:
        raise RuntimeError("source query returned no canonical runtime records")
    source_by_id = {row["entry_id"]: row for row in source_rows}

    with closing(readonly_connection(annotation_db)) as conn:
        annotation_rows = conn.execute(
            """
            SELECT entry_id,source_text_sha256,annotation_json
            FROM annotation_jobs WHERE status='valid'
            """
        ).fetchall()
    annotation_by_id = {row["entry_id"]: row for row in annotation_rows}

    effective_by_id: dict[str, sqlite3.Row] = {}
    if effective_db.exists():
        with closing(readonly_connection(effective_db)) as conn:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(effective_repair_jobs)"
                ).fetchall()
            }
            required = {
                "entry_id",
                "status",
                "source_text_sha256",
                "canonical_root_sha256",
                "repair_iteration",
                "lineage_json",
                "effective_annotation_json",
                "effective_annotation_sha256",
                "effective_envelope_json",
                "base_candidate_sha256",
                "base_annotation_json",
                "parent_effective_sha256",
                "immediate_base_origin",
            }
            if not required <= columns:
                raise RuntimeError(
                    "existing effective repair DB predates the iterative lineage contract"
                )
            effective_rows = conn.execute(
                """
                SELECT entry_id,status,source_text_sha256,canonical_root_sha256,
                       base_candidate_sha256,base_annotation_json,
                       parent_effective_sha256,repair_iteration,
                       immediate_base_origin,lineage_json,effective_annotation_json,
                       effective_annotation_sha256,effective_envelope_json
                FROM effective_repair_jobs
                """
            ).fetchall()
        effective_by_id = {str(row["entry_id"]): row for row in effective_rows}

    placeholders = ",".join("?" for _ in verdicts)
    with closing(readonly_connection(review_db)) as conn:
        review_rows = conn.execute(
            f"""
            SELECT entry_id,source_text_sha256,candidate_annotation_sha256,
                   status,verdict,review_json,prompt_version,prompt_sha256,
                   review_schema_sha256
            FROM semantic_review_jobs
            WHERE status IN ({placeholders}) AND verdict IN ({placeholders})
            ORDER BY source_work_id,entry_id
            """,
            [*sorted(verdicts), *sorted(verdicts)],
        ).fetchall()

    candidates: list[RepairCandidate] = []
    for review_row in review_rows:
        entry_id = str(review_row["entry_id"])
        source_row = source_by_id.get(entry_id)
        if source_row is None:
            raise RuntimeError(f"repair review targets a non-canonical C1 row: {entry_id}")
        annotation_row = annotation_by_id.get(entry_id)
        if annotation_row is None:
            raise RuntimeError(f"repair review lacks a current valid A1 row: {entry_id}")
        source_text = str(source_row["text"])
        source_hash = sha256_text(source_text)
        if int(source_row["char_count"]) != len(source_text):
            raise RuntimeError(f"C1 char_count mismatch for {entry_id}")
        if source_hash != source_row["extracted_text_sha256"]:
            raise RuntimeError(f"C1 source hash mismatch for {entry_id}")
        if (
            source_hash != annotation_row["source_text_sha256"]
            or source_hash != review_row["source_text_sha256"]
        ):
            raise RuntimeError(f"C1/A1/review source provenance mismatch for {entry_id}")
        try:
            canonical_annotation = json.loads(str(annotation_row["annotation_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"valid A1 JSON is malformed for {entry_id}") from exc
        schema_errors = sorted(
            canonical_schema_validator.iter_errors(canonical_annotation),
            key=lambda error: list(error.path),
        )
        if schema_errors:
            raise RuntimeError(f"valid A1 row fails canonical schema for {entry_id}")
        canonical_record = canonical_annotation["annotation_record"]
        if (
            canonical_record["unit"]["unit_id"] != entry_id
            or canonical_record["unit"]["source_text_sha256"] != source_hash
            or canonical_record["source_profile"]["source_text"] != source_text
            or canonical_record["source_profile"]["source_text_sha256"] != source_hash
        ):
            raise RuntimeError(f"valid A1 source contract mismatch for {entry_id}")
        canonical_hash = sha256_text(canonical_json(canonical_annotation))
        reviewed_hash = str(review_row["candidate_annotation_sha256"])
        current_effective = effective_by_id.get(entry_id)
        if reviewed_hash == canonical_hash:
            base_annotation = canonical_annotation
            base_hash = canonical_hash
            parent_effective_sha256 = None
            repair_iteration = 1
            immediate_base_origin = "canonical_a1"
            prior_lineage: tuple[dict[str, Any], ...] = ()
        elif (
            current_effective is not None
            and reviewed_hash == current_effective["base_candidate_sha256"]
        ):
            if (
                source_hash != current_effective["source_text_sha256"]
                or canonical_hash != current_effective["canonical_root_sha256"]
            ):
                raise RuntimeError(f"in-progress effective provenance mismatch for {entry_id}")
            try:
                base_annotation = json.loads(
                    str(current_effective["base_annotation_json"])
                )
                lineage_value = json.loads(str(current_effective["lineage_json"]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"in-progress repair JSON is malformed for {entry_id}") from exc
            base_hash = sha256_text(canonical_json(base_annotation))
            if base_hash != reviewed_hash:
                raise RuntimeError(f"in-progress base hash closure failed for {entry_id}")
            repair_iteration = int(current_effective["repair_iteration"])
            immediate_base_origin = str(current_effective["immediate_base_origin"])
            parent_value = current_effective["parent_effective_sha256"]
            parent_effective_sha256 = str(parent_value) if parent_value else None
            if not isinstance(lineage_value, list):
                raise RuntimeError(f"in-progress lineage is malformed for {entry_id}")
            prior_lineage = tuple(copy.deepcopy(lineage_value))
            base_validator = (
                canonical_schema_validator
                if immediate_base_origin == "canonical_a1"
                else effective_schema_validator
            )
            if list(base_validator.iter_errors(base_annotation)):
                raise RuntimeError(f"in-progress immediate base fails schema for {entry_id}")
            if len(prior_lineage) != repair_iteration - 1:
                raise RuntimeError(f"in-progress lineage depth mismatch for {entry_id}")
        elif (
            current_effective is not None
            and current_effective["status"] == "valid"
            and reviewed_hash == current_effective["effective_annotation_sha256"]
        ):
            if source_hash != current_effective["source_text_sha256"]:
                raise RuntimeError(f"effective/source hash mismatch for {entry_id}")
            if canonical_hash != current_effective["canonical_root_sha256"]:
                raise RuntimeError(f"effective canonical root mismatch for {entry_id}")
            try:
                base_annotation = json.loads(
                    str(current_effective["effective_annotation_json"])
                )
                lineage_value = json.loads(str(current_effective["lineage_json"]))
                existing_envelope = json.loads(
                    str(current_effective["effective_envelope_json"])
                )
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"effective repair JSON is malformed for {entry_id}") from exc
            effective_errors = sorted(
                effective_schema_validator.iter_errors(base_annotation),
                key=lambda error: list(error.path),
            )
            if effective_errors:
                raise RuntimeError(f"current effective row fails sidecar schema for {entry_id}")
            envelope_errors = sorted(
                envelope_schema_validator.iter_errors(existing_envelope),
                key=lambda error: list(error.path),
            )
            if envelope_errors:
                raise RuntimeError(f"current effective envelope fails schema for {entry_id}")
            base_hash = sha256_text(canonical_json(base_annotation))
            if base_hash != reviewed_hash:
                raise RuntimeError(f"effective annotation hash closure failed for {entry_id}")
            if not isinstance(lineage_value, list) or not lineage_value:
                raise RuntimeError(f"effective lineage is missing for {entry_id}")
            existing_iteration = int(current_effective["repair_iteration"])
            effective_meta = base_annotation["annotation_record"]["annotation_meta"][
                "repair_provenance"
            ]
            if (
                len(lineage_value) != existing_iteration
                or lineage_value[-1].get("effective_annotation_sha256") != base_hash
                or existing_envelope.get("lineage") != lineage_value
                or existing_envelope.get("repair_iteration") != existing_iteration
                or existing_envelope.get("canonical_root_sha256") != canonical_hash
                or effective_meta.get("repair_iteration") != existing_iteration
                or effective_meta.get("canonical_root_sha256") != canonical_hash
            ):
                raise RuntimeError(f"effective lineage closure failed for {entry_id}")
            parent_effective_sha256 = base_hash
            repair_iteration = existing_iteration + 1
            immediate_base_origin = "effective_repair"
            prior_lineage = tuple(copy.deepcopy(lineage_value))
        else:
            raise RuntimeError(
                f"semantic review candidate hash does not match the immediate base for {entry_id}"
            )
        if repair_iteration > max_repair_iterations:
            raise RuntimeError(
                f"repair iteration limit exceeded for {entry_id}: "
                f"{repair_iteration}>{max_repair_iterations}"
            )
        verdict = str(review_row["verdict"])
        if review_row["status"] != verdict:
            raise RuntimeError(f"semantic review status/verdict mismatch for {entry_id}")
        try:
            review = json.loads(str(review_row["review_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"semantic review JSON is malformed for {entry_id}") from exc
        _validate_review(review, entry_id=entry_id, verdict=verdict, source_text=source_text)
        review_prompt_version = str(review_row["prompt_version"] or "")
        review_prompt_sha = str(review_row["prompt_sha256"] or "")
        review_schema_sha = str(review_row["review_schema_sha256"] or "")
        if not review_prompt_version or not re.fullmatch(r"[0-9a-f]{64}", review_prompt_sha):
            raise RuntimeError(f"semantic review prompt provenance is invalid for {entry_id}")
        if not re.fullmatch(r"[0-9a-f]{64}", review_schema_sha):
            raise RuntimeError(f"semantic review schema provenance is invalid for {entry_id}")
        source = SourceEntry(
            entry_id=entry_id,
            source_work_id=str(source_row["source_work_id"]),
            source_work_title=str(source_row["source_work_title"]),
            source_work_period=str(source_row["source_work_period"]),
            volume=str(source_row["volume"]),
            source_locator=str(source_row["source_locator"]),
            entry_ordinal=int(source_row["entry_ordinal"]),
            title=str(source_row["title"]),
            source_url=str(source_row["source_url"]),
            char_count=int(source_row["char_count"]),
            text=source_text,
            extracted_text_sha256=source_hash,
        )
        candidates.append(
            RepairCandidate(
                source=source,
                base_annotation=base_annotation,
                base_candidate_sha256=base_hash,
                review=review,
                review_sha256=sha256_text(canonical_json(review)),
                review_verdict=verdict,
                review_prompt_version=review_prompt_version,
                review_prompt_sha256=review_prompt_sha,
                review_schema_sha256=review_schema_sha,
                catalog_version=str(canonical_record["source_profile"]["source_version"]),
                canonical_root_sha256=canonical_hash,
                parent_effective_sha256=parent_effective_sha256,
                repair_iteration=repair_iteration,
                immediate_base_origin=immediate_base_origin,
                prior_lineage=prior_lineage,
            )
        )
    return candidates, len(source_rows)


def length_bucket(char_count: int) -> int:
    if char_count <= 100:
        return 0
    if char_count <= 500:
        return 1
    if char_count <= 1000:
        return 2
    if char_count <= 3000:
        return 3
    if char_count <= 4000:
        return 4
    return 5


def stratified_sample(
    candidates: Sequence[RepairCandidate], limit: int
) -> list[RepairCandidate]:
    groups: dict[tuple[str, str, int], list[RepairCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(
            (
                candidate.review_verdict,
                candidate.source.source_work_id,
                length_bucket(candidate.char_count),
            ),
            [],
        ).append(candidate)
    for key, values in groups.items():
        seed = int(sha256_text(":".join(map(str, key)) + ":repair")[:16], 16)
        random.Random(seed).shuffle(values)
    keys = sorted(groups)
    selected: list[RepairCandidate] = []
    cursor = 0
    while keys and len(selected) < limit:
        key = keys[cursor % len(keys)]
        selected.append(groups[key].pop())
        if not groups[key]:
            keys.remove(key)
            cursor = 0
        else:
            cursor += 1
    return selected


def select_candidates(
    candidates: Sequence[RepairCandidate], *, limit: int | None, sample_mode: str
) -> list[RepairCandidate]:
    if limit is None:
        return list(candidates)
    if limit <= 0:
        raise RuntimeError("--limit must be positive")
    capped = min(limit, len(candidates))
    if sample_mode == "stratified":
        return stratified_sample(candidates, capped)
    return list(candidates[:capped])


def pack_repair_batches(
    candidates: Sequence[RepairCandidate], *, batch_size: int, char_limit: int
) -> list[list[RepairCandidate]]:
    """Pack complete sources; records above the limit remain full singletons."""
    batches: list[list[RepairCandidate]] = []
    current: list[RepairCandidate] = []
    current_chars = 0
    for candidate in candidates:
        if candidate.char_count > char_limit:
            if current:
                batches.append(current)
                current, current_chars = [], 0
            batches.append([candidate])
            continue
        if current and (
            len(current) >= batch_size
            or current_chars + candidate.char_count > char_limit
        ):
            batches.append(current)
            current, current_chars = [], 0
        current.append(candidate)
        current_chars += candidate.char_count
    if current:
        batches.append(current)
    return batches


def per_worker_rpm(total_rpm: float, active_workers: int) -> float:
    if active_workers <= 0:
        return 0.0
    return total_rpm / active_workers


def enforce_live_scope_confirmation(
    *, live: bool, selected_count: int, eligible_count: int, confirmed: bool
) -> None:
    if live and eligible_count > 0 and selected_count == eligible_count and not confirmed:
        raise SystemExit("full live semantic repair requires --confirm-full-run")


def enforce_disjoint_paths(**paths: Path) -> None:
    by_path: dict[Path, list[str]] = {}
    for name, path in paths.items():
        by_path.setdefault(path.resolve(), []).append(name.replace("_", "-"))
    collisions = [names for names in by_path.values() if len(names) > 1]
    if collisions:
        rendered = "; ".join("=".join(names) for names in collisions)
        raise SystemExit(f"semantic-repair input/output paths must be distinct: {rendered}")


def configure_effective_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def effective_connection(path: Path) -> sqlite3.Connection:
    return configure_effective_connection(
        sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    )


def init_effective_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = effective_connection(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS effective_repair_jobs (
            entry_id TEXT PRIMARY KEY,
            source_text_sha256 TEXT NOT NULL,
            canonical_root_sha256 TEXT NOT NULL,
            base_candidate_sha256 TEXT NOT NULL,
            parent_effective_sha256 TEXT,
            repair_iteration INTEGER NOT NULL CHECK(repair_iteration >= 1),
            immediate_base_origin TEXT NOT NULL CHECK(
                immediate_base_origin IN ('canonical_a1','effective_repair')
            ),
            lineage_json TEXT NOT NULL,
            review_sha256 TEXT NOT NULL,
            review_verdict TEXT NOT NULL CHECK(review_verdict IN ('revise','uncertain')),
            review_prompt_version TEXT NOT NULL,
            review_prompt_sha256 TEXT NOT NULL,
            review_schema_sha256 TEXT NOT NULL,
            source_work_id TEXT NOT NULL,
            source_work_title TEXT NOT NULL,
            title TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            input_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'queued','leased','valid','retryable_failed','quarantined','stale'
            )),
            attempts INTEGER NOT NULL DEFAULT 0,
            base_annotation_json TEXT NOT NULL,
            review_json TEXT NOT NULL,
            raw_model_annotation_json TEXT,
            normalized_annotation_json TEXT,
            normalized_annotation_sha256 TEXT,
            effective_annotation_json TEXT,
            effective_annotation_sha256 TEXT,
            effective_envelope_json TEXT,
            raw_response_json TEXT,
            error_kind TEXT,
            error_message TEXT,
            provider_response_id TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            model TEXT NOT NULL,
            provider_reported_model TEXT,
            prompt_version TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            canonical_schema_sha256 TEXT NOT NULL,
            effective_schema_sha256 TEXT NOT NULL,
            envelope_schema_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_effective_repair_jobs_status
            ON effective_repair_jobs(status);
        CREATE TABLE IF NOT EXISTS effective_repair_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS effective_repair_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            repair_iteration INTEGER NOT NULL,
            canonical_root_sha256 TEXT NOT NULL,
            base_candidate_sha256 TEXT NOT NULL,
            parent_effective_sha256 TEXT,
            review_sha256 TEXT NOT NULL,
            review_prompt_version TEXT NOT NULL,
            review_prompt_sha256 TEXT NOT NULL,
            normalized_annotation_sha256 TEXT NOT NULL,
            effective_annotation_sha256 TEXT NOT NULL,
            effective_envelope_json TEXT NOT NULL,
            lineage_json TEXT NOT NULL,
            raw_response_json TEXT,
            provider_response_id TEXT,
            model TEXT NOT NULL,
            provider_reported_model TEXT,
            prompt_version TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            superseded_reason TEXT NOT NULL,
            archived_at TEXT NOT NULL,
            UNIQUE(entry_id,effective_annotation_sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_effective_repair_history_entry
            ON effective_repair_history(entry_id,repair_iteration);
        """
    )
    conn.commit()
    return conn


def archive_current_valid_row(
    conn: sqlite3.Connection, *, entry_id: str, superseded_reason: str
) -> None:
    """Preserve every superseded provider result before the current row is reused."""
    row = conn.execute(
        "SELECT * FROM effective_repair_jobs WHERE entry_id=? AND status='valid'",
        (entry_id,),
    ).fetchone()
    if row is None:
        return
    required = (
        "normalized_annotation_sha256",
        "effective_annotation_sha256",
        "effective_envelope_json",
        "lineage_json",
    )
    if any(not row[name] for name in required):
        raise RuntimeError(f"valid effective row lacks audit artifacts for {entry_id}")
    conn.execute(
        """
        INSERT OR IGNORE INTO effective_repair_history(
            entry_id,repair_iteration,canonical_root_sha256,
            base_candidate_sha256,parent_effective_sha256,review_sha256,
            review_prompt_version,review_prompt_sha256,
            normalized_annotation_sha256,effective_annotation_sha256,
            effective_envelope_json,lineage_json,raw_response_json,
            provider_response_id,model,provider_reported_model,prompt_version,
            prompt_sha256,prompt_tokens,completion_tokens,total_tokens,
            superseded_reason,archived_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["entry_id"],
            row["repair_iteration"],
            row["canonical_root_sha256"],
            row["base_candidate_sha256"],
            row["parent_effective_sha256"],
            row["review_sha256"],
            row["review_prompt_version"],
            row["review_prompt_sha256"],
            row["normalized_annotation_sha256"],
            row["effective_annotation_sha256"],
            row["effective_envelope_json"],
            row["lineage_json"],
            row["raw_response_json"],
            row["provider_response_id"],
            row["model"],
            row["provider_reported_model"],
            row["prompt_version"],
            row["prompt_sha256"],
            row["prompt_tokens"],
            row["completion_tokens"],
            row["total_tokens"],
            superseded_reason,
            utc_now(),
        ),
    )


def enqueue_candidates(
    conn: sqlite3.Connection,
    candidates: Sequence[RepairCandidate],
    *,
    model: str,
    canonical_schema_sha256: str,
    effective_schema_sha256: str,
    envelope_schema_sha256: str,
) -> int:
    now = utc_now()
    prompt_sha = sha256_text(SYSTEM_PROMPT)
    invalidated = 0
    with conn:
        for candidate in candidates:
            input_json = canonical_json(candidate.provider_input())
            base_json = canonical_json(candidate.base_annotation)
            review_json = canonical_json(candidate.review)
            existing = conn.execute(
                """
                SELECT source_text_sha256,canonical_root_sha256,
                       base_candidate_sha256,parent_effective_sha256,
                       repair_iteration,immediate_base_origin,review_sha256,
                       review_verdict,review_prompt_version,review_prompt_sha256,
                       review_schema_sha256,model,prompt_version,prompt_sha256,
                       canonical_schema_sha256,effective_schema_sha256,
                       envelope_schema_sha256
                FROM effective_repair_jobs WHERE entry_id=?
                """,
                (candidate.entry_id,),
            ).fetchone()
            provenance = (
                candidate.source.extracted_text_sha256,
                candidate.canonical_root_sha256,
                candidate.base_candidate_sha256,
                candidate.parent_effective_sha256,
                candidate.repair_iteration,
                candidate.immediate_base_origin,
                candidate.review_sha256,
                candidate.review_verdict,
                candidate.review_prompt_version,
                candidate.review_prompt_sha256,
                candidate.review_schema_sha256,
                model,
                REPAIR_PROMPT_VERSION,
                prompt_sha,
                canonical_schema_sha256,
                effective_schema_sha256,
                envelope_schema_sha256,
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO effective_repair_jobs(
                        entry_id,source_text_sha256,base_candidate_sha256,
                        canonical_root_sha256,parent_effective_sha256,
                        repair_iteration,immediate_base_origin,lineage_json,
                        review_sha256,review_verdict,review_prompt_version,
                        review_prompt_sha256,review_schema_sha256,source_work_id,
                        source_work_title,title,char_count,input_json,status,
                        base_annotation_json,review_json,model,prompt_version,
                        prompt_sha256,canonical_schema_sha256,
                        effective_schema_sha256,envelope_schema_sha256,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate.entry_id,
                        candidate.source.extracted_text_sha256,
                        candidate.base_candidate_sha256,
                        candidate.canonical_root_sha256,
                        candidate.parent_effective_sha256,
                        candidate.repair_iteration,
                        candidate.immediate_base_origin,
                        canonical_json(list(candidate.prior_lineage)),
                        candidate.review_sha256,
                        candidate.review_verdict,
                        candidate.review_prompt_version,
                        candidate.review_prompt_sha256,
                        candidate.review_schema_sha256,
                        candidate.source.source_work_id,
                        candidate.source.source_work_title,
                        candidate.source.title,
                        candidate.char_count,
                        input_json,
                        base_json,
                        review_json,
                        model,
                        REPAIR_PROMPT_VERSION,
                        prompt_sha,
                        canonical_schema_sha256,
                        effective_schema_sha256,
                        envelope_schema_sha256,
                        now,
                        now,
                    ),
                )
                continue
            compatible = tuple(existing[key] for key in existing.keys()) == provenance
            if compatible:
                continue
            invalidated += 1
            archive_current_valid_row(
                conn,
                entry_id=candidate.entry_id,
                superseded_reason=(
                    "next_repair_iteration"
                    if candidate.parent_effective_sha256 is not None
                    and int(existing["repair_iteration"]) + 1
                    == candidate.repair_iteration
                    else "repair_provenance_changed"
                ),
            )
            conn.execute(
                """
                UPDATE effective_repair_jobs
                SET source_text_sha256=?,canonical_root_sha256=?,
                    base_candidate_sha256=?,parent_effective_sha256=?,
                    repair_iteration=?,immediate_base_origin=?,lineage_json=?,review_sha256=?,
                    review_verdict=?,review_prompt_version=?,review_prompt_sha256=?,
                    review_schema_sha256=?,source_work_id=?,source_work_title=?,title=?,
                    char_count=?,input_json=?,status='stale',base_annotation_json=?,
                    review_json=?,raw_model_annotation_json=NULL,
                    normalized_annotation_json=NULL,normalized_annotation_sha256=NULL,
                    effective_annotation_json=NULL,effective_annotation_sha256=NULL,
                    effective_envelope_json=NULL,raw_response_json=NULL,
                    error_kind='provenance_changed',error_message=NULL,
                    provider_response_id=NULL,prompt_tokens=NULL,
                    completion_tokens=NULL,total_tokens=NULL,model=?,
                    provider_reported_model=NULL,prompt_version=?,prompt_sha256=?,
                    canonical_schema_sha256=?,effective_schema_sha256=?,
                    envelope_schema_sha256=?,updated_at=? WHERE entry_id=?
                """,
                (
                    candidate.source.extracted_text_sha256,
                    candidate.canonical_root_sha256,
                    candidate.base_candidate_sha256,
                    candidate.parent_effective_sha256,
                    candidate.repair_iteration,
                    candidate.immediate_base_origin,
                    canonical_json(list(candidate.prior_lineage)),
                    candidate.review_sha256,
                    candidate.review_verdict,
                    candidate.review_prompt_version,
                    candidate.review_prompt_sha256,
                    candidate.review_schema_sha256,
                    candidate.source.source_work_id,
                    candidate.source.source_work_title,
                    candidate.source.title,
                    candidate.char_count,
                    input_json,
                    base_json,
                    review_json,
                    model,
                    REPAIR_PROMPT_VERSION,
                    prompt_sha,
                    canonical_schema_sha256,
                    effective_schema_sha256,
                    envelope_schema_sha256,
                    now,
                    candidate.entry_id,
                ),
            )
    return invalidated


def pending_candidates(
    conn: sqlite3.Connection,
    selected: Sequence[RepairCandidate],
    *,
    resume: bool,
    retry_quarantined: bool,
) -> list[RepairCandidate]:
    states = {
        row["entry_id"]: row["status"]
        for row in conn.execute("SELECT entry_id,status FROM effective_repair_jobs")
    }
    if not resume:
        nonfresh = [
            item.entry_id
            for item in selected
            if states.get(item.entry_id) not in {"queued", "stale"}
        ]
        if nonfresh:
            raise RuntimeError(
                f"{len(nonfresh)} selected repairs already started; pass --resume"
            )
        allowed = {"queued", "stale"}
    else:
        allowed = {"queued", "stale", "leased", "retryable_failed"}
        if retry_quarantined:
            allowed.add("quarantined")
    return [item for item in selected if states.get(item.entry_id) in allowed]


def build_effective_schema(canonical_schema: dict[str, Any]) -> dict[str, Any]:
    """Derive a sidecar schema without weakening the canonical source schema."""
    schema = copy.deepcopy(canonical_schema)
    schema["$id"] = "https://mengdieji.local/contracts/a1-effective-repair-derived-v2.json"
    meta = schema["$defs"]["annotationMeta"]
    meta["required"] = [*meta["required"], "repair_provenance"]
    meta["properties"]["generator"]["properties"]["prompt_version"] = {
        "const": REPAIR_PROMPT_VERSION
    }
    meta["properties"]["validation"]["properties"]["schema_valid"] = {
        "const": False
    }
    sha_ref = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    meta["properties"]["repair_provenance"] = {
        "type": "object",
        "required": [
            "type",
            "base_prompt_version",
            "repair_prompt_version",
            "repair_prompt_sha256",
            "base_candidate_sha256",
            "review_sha256",
            "review_verdict",
            "review_prompt_version",
            "review_prompt_sha256",
            "review_schema_sha256",
            "canonical_root_sha256",
            "immediate_base_sha256",
            "parent_effective_sha256",
            "repair_iteration",
            "immediate_base_origin",
            "prior_effective_sha256",
            "automatic_semantic_repair",
            "human_reviewed",
            "canonical_schema_valid",
        ],
        "properties": {
            "type": {"const": "llm_semantic_repair"},
            "base_prompt_version": {"const": BASE_PROMPT_VERSION},
            "repair_prompt_version": {"const": REPAIR_PROMPT_VERSION},
            "repair_prompt_sha256": sha_ref,
            "base_candidate_sha256": sha_ref,
            "review_sha256": sha_ref,
            "review_verdict": {"enum": ["revise", "uncertain"]},
            "review_prompt_version": {"type": "string", "minLength": 1},
            "review_prompt_sha256": sha_ref,
            "review_schema_sha256": sha_ref,
            "canonical_root_sha256": sha_ref,
            "immediate_base_sha256": sha_ref,
            "parent_effective_sha256": {
                "anyOf": [sha_ref, {"type": "null"}]
            },
            "repair_iteration": {"type": "integer", "minimum": 1, "maximum": 20},
            "immediate_base_origin": {
                "enum": ["canonical_a1", "effective_repair"]
            },
            "prior_effective_sha256": {
                "type": "array",
                "maxItems": 19,
                "uniqueItems": True,
                "items": sha_ref,
            },
            "automatic_semantic_repair": {"const": True},
            "human_reviewed": {"const": False},
            "canonical_schema_valid": {"const": False},
        },
        "additionalProperties": False,
    }
    return schema


def build_effective_envelope(
    *,
    candidate: RepairCandidate,
    normalized_annotation: dict[str, Any],
    raw_model_annotation: dict[str, Any],
    raw_response_sha256: str,
    provider_meta: dict[str, Any],
    canonical_schema_validator: Draft202012Validator,
    effective_schema_validator: Draft202012Validator,
    envelope_schema_validator: Draft202012Validator,
    canonical_schema_sha256: str,
    effective_schema_sha256: str,
    envelope_schema_sha256: str,
) -> dict[str, Any]:
    """Build and validate the explicit base/normalized/effective provenance chain."""
    canonical_errors = sorted(
        canonical_schema_validator.iter_errors(normalized_annotation),
        key=lambda error: list(error.path),
    )
    if canonical_errors:
        first = canonical_errors[0]
        raise RepairValidationError(
            f"normalizer output failed canonical schema at {list(first.path)}: {first.message}"
        )
    normalized_hash = sha256_text(canonical_json(normalized_annotation))
    prior_lineage = [copy.deepcopy(item) for item in candidate.prior_lineage]
    if len(prior_lineage) != candidate.repair_iteration - 1:
        raise RepairValidationError("prior lineage length does not match repair iteration")
    prior_effective_hashes = [
        str(item.get("effective_annotation_sha256")) for item in prior_lineage
    ]
    if len(prior_effective_hashes) != len(set(prior_effective_hashes)):
        raise RepairValidationError("prior effective lineage contains a hash cycle")
    for expected_iteration, item in enumerate(prior_lineage, start=1):
        if (
            item.get("repair_iteration") != expected_iteration
            or item.get("canonical_root_sha256") != candidate.canonical_root_sha256
        ):
            raise RepairValidationError("prior lineage iteration/root closure failed")
    if candidate.repair_iteration == 1:
        if (
            candidate.parent_effective_sha256 is not None
            or candidate.base_candidate_sha256 != candidate.canonical_root_sha256
            or candidate.immediate_base_origin != "canonical_a1"
        ):
            raise RepairValidationError("canonical first-repair lineage is invalid")
    elif (
        not prior_lineage
        or candidate.parent_effective_sha256 != prior_effective_hashes[-1]
        or candidate.base_candidate_sha256 != candidate.parent_effective_sha256
        or candidate.immediate_base_origin != "effective_repair"
    ):
        raise RepairValidationError("effective parent/immediate-base lineage is invalid")

    def semantic_payload_hash(annotation: dict[str, Any]) -> str:
        record = copy.deepcopy(annotation["annotation_record"])
        record.pop("annotation_meta", None)
        return sha256_text(canonical_json(record))

    if semantic_payload_hash(normalized_annotation) == semantic_payload_hash(
        candidate.base_annotation
    ):
        raise RepairValidationError("repair produced no semantic change from immediate base")
    effective_annotation = copy.deepcopy(normalized_annotation)
    meta = effective_annotation["annotation_record"]["annotation_meta"]
    meta["generator"]["prompt_version"] = REPAIR_PROMPT_VERSION
    meta["validation"]["schema_valid"] = False
    meta["repair_provenance"] = {
        "type": "llm_semantic_repair",
        "base_prompt_version": BASE_PROMPT_VERSION,
        "repair_prompt_version": REPAIR_PROMPT_VERSION,
        "repair_prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "base_candidate_sha256": candidate.base_candidate_sha256,
        "review_sha256": candidate.review_sha256,
        "review_verdict": candidate.review_verdict,
        "review_prompt_version": candidate.review_prompt_version,
        "review_prompt_sha256": candidate.review_prompt_sha256,
        "review_schema_sha256": candidate.review_schema_sha256,
        "canonical_root_sha256": candidate.canonical_root_sha256,
        "immediate_base_sha256": candidate.base_candidate_sha256,
        "parent_effective_sha256": candidate.parent_effective_sha256,
        "repair_iteration": candidate.repair_iteration,
        "immediate_base_origin": candidate.immediate_base_origin,
        "prior_effective_sha256": prior_effective_hashes,
        "automatic_semantic_repair": True,
        "human_reviewed": False,
        "canonical_schema_valid": False,
    }
    if candidate.review_verdict == "uncertain":
        meta["overall_confidence"] = "low"
        uncertainties = effective_annotation["annotation_record"]["auto_safety_screen"][
            "uncertainties"
        ]
        note = "语义审校结论仍不确定，本条仅完成自动修订，尚未经人工审核。"
        if note not in uncertainties:
            if len(uncertainties) < 6:
                uncertainties.append(note)
            else:
                uncertainties[-1] = note
    if (
        meta["research_review_status"] != "not_reviewed"
        or meta["research_ready"] is not False
        or meta["human_review"]["reviewed"] is not False
    ):
        raise RepairValidationError("automatic repair attempted to claim human/research review")
    effective_errors = sorted(
        effective_schema_validator.iter_errors(effective_annotation),
        key=lambda error: list(error.path),
    )
    if effective_errors:
        first = effective_errors[0]
        raise RepairValidationError(
            f"effective schema failed at {list(first.path)}: {first.message}"
        )
    # The fixed canonical prompt/version contract must not accidentally admit
    # the effective repair as if it were a first-pass A1 annotation.
    if not list(canonical_schema_validator.iter_errors(effective_annotation)):
        raise RepairValidationError(
            "effective repair unexpectedly passes the fixed canonical A1 schema"
        )
    effective_hash = sha256_text(canonical_json(effective_annotation))
    if effective_hash in {candidate.base_candidate_sha256, *prior_effective_hashes}:
        raise RepairValidationError("effective annotation hash repeats its lineage")
    generated_at = str(meta["generated_at"])
    requested_model = str(
        provider_meta.get("requested_model") or provider_meta.get("model") or "unknown"
    )
    reported_model = str(provider_meta.get("model") or requested_model)
    usage = provider_meta.get("usage") or {}
    raw_model_hash = sha256_text(canonical_json(raw_model_annotation))
    lineage_entry = {
        "repair_iteration": candidate.repair_iteration,
        "canonical_root_sha256": candidate.canonical_root_sha256,
        "immediate_base_sha256": candidate.base_candidate_sha256,
        "parent_effective_sha256": candidate.parent_effective_sha256,
        "review_sha256": candidate.review_sha256,
        "review_prompt_version": candidate.review_prompt_version,
        "review_prompt_sha256": candidate.review_prompt_sha256,
        "review_schema_sha256": candidate.review_schema_sha256,
        "normalized_annotation_sha256": normalized_hash,
        "effective_annotation_sha256": effective_hash,
        "raw_model_annotation_sha256": raw_model_hash,
        "raw_response_sha256": raw_response_sha256,
        "repair_prompt_version": REPAIR_PROMPT_VERSION,
        "repair_prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "requested_model": requested_model,
        "provider_reported_model": reported_model,
        "provider_response_id": provider_meta.get("response_id") or None,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "generated_at": generated_at,
    }
    lineage = [*prior_lineage, lineage_entry]
    envelope = {
        "effective_record_version": EFFECTIVE_RECORD_VERSION,
        "entry_id": candidate.entry_id,
        "source_text_sha256": candidate.source.extracted_text_sha256,
        "canonical_root_sha256": candidate.canonical_root_sha256,
        "base_candidate_sha256": candidate.base_candidate_sha256,
        "parent_effective_sha256": candidate.parent_effective_sha256,
        "repair_iteration": candidate.repair_iteration,
        "immediate_base_origin": candidate.immediate_base_origin,
        "lineage": lineage,
        "review_sha256": candidate.review_sha256,
        "review_verdict": candidate.review_verdict,
        "normalized_annotation_sha256": normalized_hash,
        "effective_annotation_sha256": effective_hash,
        "base_annotation": candidate.base_annotation,
        "review": candidate.review,
        "normalized_a1_annotation": normalized_annotation,
        "effective_annotation": effective_annotation,
        "repair_provenance": {
            "type": "llm_semantic_repair",
            "provider": "deepseek",
            "requested_model": requested_model,
            "provider_reported_model": reported_model,
            "prompt_version": REPAIR_PROMPT_VERSION,
            "prompt_sha256": sha256_text(SYSTEM_PROMPT),
            "canonical_schema_sha256": canonical_schema_sha256,
            "effective_schema_sha256": effective_schema_sha256,
            "envelope_schema_sha256": envelope_schema_sha256,
            "base_candidate_sha256": candidate.base_candidate_sha256,
            "canonical_root_sha256": candidate.canonical_root_sha256,
            "parent_effective_sha256": candidate.parent_effective_sha256,
            "repair_iteration": candidate.repair_iteration,
            "immediate_base_origin": candidate.immediate_base_origin,
            "review_sha256": candidate.review_sha256,
            "review_verdict": candidate.review_verdict,
            "review_prompt_version": candidate.review_prompt_version,
            "review_prompt_sha256": candidate.review_prompt_sha256,
            "review_schema_sha256": candidate.review_schema_sha256,
            "provider_response_id": provider_meta.get("response_id") or None,
            "generated_at": generated_at,
            "normalizer": "data.annotate_c1_retrieval.normalise_annotation",
            "normalizer_local_repair_enabled": True,
            "automatic_semantic_repair": True,
            "human_reviewed": False,
        },
        "canonical_schema_status": {
            "normalized_record_valid": True,
            "effective_record_valid": False,
            "effective_record_schema": "sidecar-derived-effective-schema",
            "reason": (
                "The canonical A1 schema fixes the first-pass prompt version; "
                "the truthful repair prompt is validated only by the sidecar schema."
            ),
        },
        "human_reviewed": False,
        "research_ready": False,
    }
    envelope_errors = sorted(
        envelope_schema_validator.iter_errors(envelope),
        key=lambda error: list(error.path),
    )
    if envelope_errors:
        first = envelope_errors[0]
        raise RepairValidationError(
            f"repair envelope schema failed at {list(first.path)}: {first.message}"
        )
    # Hash/provenance closure is checked after schema validation as a local
    # semantic invariant rather than delegated to JSON Schema.
    if envelope["base_candidate_sha256"] != sha256_text(
        canonical_json(envelope["base_annotation"])
    ):
        raise RepairValidationError("base annotation hash closure failed")
    if envelope["review_sha256"] != sha256_text(canonical_json(envelope["review"])):
        raise RepairValidationError("semantic review hash closure failed")
    if envelope["normalized_annotation_sha256"] != normalized_hash:
        raise RepairValidationError("normalized annotation hash closure failed")
    if envelope["effective_annotation_sha256"] != effective_hash:
        raise RepairValidationError("effective annotation hash closure failed")
    if envelope["lineage"][-1]["effective_annotation_sha256"] != effective_hash:
        raise RepairValidationError("current lineage does not end at the effective annotation")
    return {
        "raw_model_annotation": raw_model_annotation,
        "normalized_annotation": normalized_annotation,
        "effective_annotation": effective_annotation,
        "effective_envelope": envelope,
        "lineage": lineage,
    }


def parse_and_normalize_repairs(
    response: dict[str, Any],
    candidates: Sequence[RepairCandidate],
    *,
    provider_meta: dict[str, Any],
    canonical_schema_validator: Draft202012Validator,
    effective_schema_validator: Draft202012Validator,
    envelope_schema_validator: Draft202012Validator,
    canonical_schema_sha256: str,
    effective_schema_sha256: str,
    envelope_schema_sha256: str,
) -> dict[str, dict[str, Any]]:
    raw_annotations = response.get("annotations")
    if not isinstance(raw_annotations, list):
        raw_annotations = response.get("records")
    if not isinstance(raw_annotations, list):
        raise RepairValidationError("response must contain an annotations array")
    expected = {candidate.entry_id for candidate in candidates}
    received = [
        item.get("entryId") for item in raw_annotations if isinstance(item, dict)
    ]
    if (
        len(received) != len(raw_annotations)
        or set(received) != expected
        or len(received) != len(set(received))
    ):
        raise RepairValidationError("response IDs do not exactly match the repair batch")
    raw_by_id = {item["entryId"]: item for item in raw_annotations}
    raw_response_sha256 = sha256_text(canonical_json(response))
    output: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        raw = raw_by_id[candidate.entry_id]
        try:
            normalized = normalise_annotation(
                raw,
                candidate.source,
                schema_validator=canonical_schema_validator,
                catalog_version=candidate.catalog_version,
                model=str(
                    provider_meta.get("requested_model")
                    or provider_meta.get("model")
                    or "unknown"
                ),
                allow_local_repair=True,
            )
        except AnnotationError as exc:
            raise RepairValidationError(str(exc)) from exc
        output[candidate.entry_id] = build_effective_envelope(
            candidate=candidate,
            normalized_annotation=normalized,
            raw_model_annotation=raw,
            raw_response_sha256=raw_response_sha256,
            provider_meta=provider_meta,
            canonical_schema_validator=canonical_schema_validator,
            effective_schema_validator=effective_schema_validator,
            envelope_schema_validator=envelope_schema_validator,
            canonical_schema_sha256=canonical_schema_sha256,
            effective_schema_sha256=effective_schema_sha256,
            envelope_schema_sha256=envelope_schema_sha256,
        )
    return output


class DeepSeekRepairClient:
    def __init__(
        self,
        *,
        rpm: float,
        timeout: float,
        max_tokens: int,
        http_client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        live_enabled: bool | None = None,
        call_budget: ProviderCallBudget | None = None,
    ) -> None:
        load_dotenv(ROOT / ".env", override=False)
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY")
        self.base_url = (
            base_url
            if base_url is not None
            else os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")
        self.model = model if model is not None else os.getenv(
            "DEEPSEEK_MODEL", "deepseek-v4-flash"
        )
        if live_enabled is None:
            live_enabled = os.getenv("ENABLE_LIVE_MODEL_GENERATION", "false").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        self.live_enabled = live_enabled
        if rpm <= 0:
            raise FatalProviderError("worker RPM must be positive")
        self.min_interval = 60.0 / rpm
        self.last_request_at = 0.0
        self.max_tokens = max_tokens
        self.call_count = 0
        self.call_budget = call_budget
        self.client = http_client if http_client is not None else httpx.Client(timeout=timeout)

    @property
    def available(self) -> bool:
        return bool(self.api_key) and bool(self.live_enabled)

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def complete(
        self,
        candidates: Sequence[RepairCandidate],
        retries: int,
        *,
        correction: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.available:
            raise FatalProviderError("live DeepSeek semantic repair is not enabled/configured")
        user_payload: dict[str, Any] = {
            "repairVersion": REPAIR_VERSION,
            "records": [candidate.provider_input() for candidate in candidates],
        }
        if correction:
            user_payload["previousValidationError"] = correction
            user_payload["correctionInstruction"] = (
                "上一输出未通过本地契约。请逐条回到完整 sourceText，重新生成请求中"
                "每条记录的完整 raw A1 annotation；仅返回完整 JSON，不要解释。"
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            ],
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
        }
        for attempt in range(retries + 1):
            if self.call_budget is not None:
                self.call_budget.reserve()
            wait = self.min_interval - (time.monotonic() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            self.last_request_at = time.monotonic()
            self.call_count += 1
            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.HTTPError as exc:
                if attempt >= retries:
                    raise RetryableProviderError(type(exc).__name__) from exc
                time.sleep(min(30.0, 2.0**attempt))
                continue
            if response.status_code in {401, 403, 404}:
                raise FatalProviderError(
                    f"provider rejected configuration ({response.status_code})"
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= retries:
                    raise RetryableProviderError(
                        f"provider status {response.status_code}"
                    )
                retry_after = response.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else min(30.0, 2.0**attempt)
                )
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise FatalProviderError(
                    f"provider rejected request ({response.status_code})"
                )
            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                if isinstance(content, str):
                    candidate_text = content.strip()
                    if candidate_text.startswith("```"):
                        candidate_text = re.sub(
                            r"^```(?:json)?\s*", "", candidate_text, flags=re.I
                        )
                        candidate_text = re.sub(r"\s*```$", "", candidate_text)
                    if not candidate_text.startswith("{"):
                        start, end = candidate_text.find("{"), candidate_text.rfind("}")
                        if start >= 0 and end > start:
                            candidate_text = candidate_text[start : end + 1]
                    parsed = json.loads(candidate_text)
                else:
                    parsed = content
                if not isinstance(parsed, dict):
                    raise ValueError("response content is not an object")
                reported_model = str(body.get("model") or self.model)
                if reported_model != self.model:
                    raise FatalProviderError(
                        "provider reported model does not match the requested model"
                    )
                return parsed, {
                    "response_id": str(body.get("id") or ""),
                    "usage": body.get("usage") or {},
                    "model": reported_model,
                    "requested_model": self.model,
                }
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RepairValidationError("provider returned malformed JSON") from exc
        raise AssertionError("unreachable")


def mark_leased(conn: sqlite3.Connection, candidates: Sequence[RepairCandidate]) -> None:
    now = utc_now()
    with conn:
        conn.executemany(
            """
            UPDATE effective_repair_jobs
            SET status='leased',attempts=attempts+1,updated_at=? WHERE entry_id=?
            """,
            [(now, candidate.entry_id) for candidate in candidates],
        )


def mark_valid(
    conn: sqlite3.Connection,
    results: dict[str, dict[str, Any]],
    candidates: Sequence[RepairCandidate],
    raw_response: dict[str, Any],
    provider_meta: dict[str, Any],
) -> None:
    usage = provider_meta.get("usage") or {}
    raw_response_json = canonical_json(raw_response)
    now = utc_now()
    with conn:
        for candidate in candidates:
            result = results[candidate.entry_id]
            normalized = result["normalized_annotation"]
            effective = result["effective_annotation"]
            envelope = result["effective_envelope"]
            current = conn.execute(
                """
                SELECT status,source_text_sha256,canonical_root_sha256,
                       base_candidate_sha256,parent_effective_sha256,
                       repair_iteration,review_sha256
                FROM effective_repair_jobs WHERE entry_id=?
                """,
                (candidate.entry_id,),
            ).fetchone()
            expected = (
                "leased",
                candidate.source.extracted_text_sha256,
                candidate.canonical_root_sha256,
                candidate.base_candidate_sha256,
                candidate.parent_effective_sha256,
                candidate.repair_iteration,
                candidate.review_sha256,
            )
            if current is None or tuple(current[key] for key in current.keys()) != expected:
                raise RuntimeError(
                    f"effective repair ledger provenance changed before commit for "
                    f"{candidate.entry_id}"
                )
            cursor = conn.execute(
                """
                UPDATE effective_repair_jobs
                SET status='valid',raw_model_annotation_json=?,
                    normalized_annotation_json=?,normalized_annotation_sha256=?,
                    effective_annotation_json=?,effective_annotation_sha256=?,
                    effective_envelope_json=?,lineage_json=?,raw_response_json=?,error_kind=NULL,
                    error_message=NULL,provider_response_id=?,prompt_tokens=?,
                    completion_tokens=?,total_tokens=?,model=?,
                    provider_reported_model=?,prompt_version=?,updated_at=?
                WHERE entry_id=?
                """,
                (
                    canonical_json(result["raw_model_annotation"]),
                    canonical_json(normalized),
                    sha256_text(canonical_json(normalized)),
                    canonical_json(effective),
                    sha256_text(canonical_json(effective)),
                    canonical_json(envelope),
                    canonical_json(result["lineage"]),
                    raw_response_json,
                    provider_meta.get("response_id") or None,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    provider_meta.get("requested_model")
                    or provider_meta.get("model")
                    or "unknown",
                    provider_meta.get("model") or None,
                    REPAIR_PROMPT_VERSION,
                    now,
                    candidate.entry_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"effective repair commit failed for {candidate.entry_id}")


def mark_failed(
    conn: sqlite3.Connection,
    candidates: Sequence[RepairCandidate],
    exc: BaseException,
    *,
    retryable: bool,
    raw_response: dict[str, Any] | None,
    model: str,
) -> None:
    status = "retryable_failed" if retryable else "quarantined"
    now = utc_now()
    raw_json = canonical_json(raw_response) if raw_response is not None else None
    with conn:
        conn.executemany(
            """
            UPDATE effective_repair_jobs
            SET status=?,raw_model_annotation_json=NULL,
                normalized_annotation_json=NULL,normalized_annotation_sha256=NULL,
                effective_annotation_json=NULL,effective_annotation_sha256=NULL,
                effective_envelope_json=NULL,raw_response_json=?,error_kind=?,
                error_message=?,provider_response_id=NULL,prompt_tokens=NULL,
                completion_tokens=NULL,total_tokens=NULL,model=?,
                provider_reported_model=NULL,prompt_version=?,updated_at=? WHERE entry_id=?
            """,
            [
                (
                    status,
                    raw_json,
                    type(exc).__name__,
                    compact_error(exc),
                    model,
                    REPAIR_PROMPT_VERSION,
                    now,
                    candidate.entry_id,
                )
                for candidate in candidates
            ],
        )


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["status"]: int(row["count"])
        for row in conn.execute(
            """
            SELECT status,COUNT(*) AS count FROM effective_repair_jobs
            GROUP BY status ORDER BY status
            """
        )
    }


def selected_status_counts(
    conn: sqlite3.Connection, selected: Sequence[RepairCandidate]
) -> dict[str, int]:
    if not selected:
        return {}
    placeholders = ",".join("?" for _ in selected)
    return {
        row["status"]: int(row["count"])
        for row in conn.execute(
            f"""
            SELECT status,COUNT(*) AS count FROM effective_repair_jobs
            WHERE entry_id IN ({placeholders}) GROUP BY status ORDER BY status
            """,
            [candidate.entry_id for candidate in selected],
        )
    }


def batch_status_counts(
    conn: sqlite3.Connection, candidates: Sequence[RepairCandidate]
) -> dict[str, int]:
    return selected_status_counts(conn, candidates)


def log_event(conn: sqlite3.Connection, event_type: str, detail: dict[str, Any]) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO effective_repair_events(occurred_at,event_type,detail_json)
            VALUES(?,?,?)
            """,
            (utc_now(), event_type, canonical_json(detail)),
        )


def process_batch(
    conn: sqlite3.Connection,
    client: DeepSeekRepairClient,
    candidates: Sequence[RepairCandidate],
    *,
    canonical_schema_validator: Draft202012Validator,
    effective_schema_validator: Draft202012Validator,
    envelope_schema_validator: Draft202012Validator,
    canonical_schema_sha256: str,
    effective_schema_sha256: str,
    envelope_schema_sha256: str,
    retries: int,
    semantic_retries: int = 1,
    correction: str | None = None,
) -> None:
    mark_leased(conn, candidates)
    response: dict[str, Any] | None = None
    try:
        response, provider_meta = client.complete(
            candidates, retries, correction=correction
        )
        results = parse_and_normalize_repairs(
            response,
            candidates,
            provider_meta=provider_meta,
            canonical_schema_validator=canonical_schema_validator,
            effective_schema_validator=effective_schema_validator,
            envelope_schema_validator=envelope_schema_validator,
            canonical_schema_sha256=canonical_schema_sha256,
            effective_schema_sha256=effective_schema_sha256,
            envelope_schema_sha256=envelope_schema_sha256,
        )
        mark_valid(conn, results, candidates, response, provider_meta)
    except FatalProviderError:
        mark_failed(
            conn,
            candidates,
            FatalProviderError("fatal provider configuration"),
            retryable=True,
            raw_response=response,
            model=client.model,
        )
        raise
    except RetryableProviderError as exc:
        mark_failed(
            conn,
            candidates,
            exc,
            retryable=True,
            raw_response=response,
            model=client.model,
        )
    except RepairValidationError as exc:
        if len(candidates) > 1:
            for candidate in candidates:
                process_batch(
                    conn,
                    client,
                    [candidate],
                    canonical_schema_validator=canonical_schema_validator,
                    effective_schema_validator=effective_schema_validator,
                    envelope_schema_validator=envelope_schema_validator,
                    canonical_schema_sha256=canonical_schema_sha256,
                    effective_schema_sha256=effective_schema_sha256,
                    envelope_schema_sha256=envelope_schema_sha256,
                    retries=retries,
                    semantic_retries=semantic_retries,
                    correction=compact_error(exc),
                )
        elif semantic_retries > 0:
            process_batch(
                conn,
                client,
                candidates,
                canonical_schema_validator=canonical_schema_validator,
                effective_schema_validator=effective_schema_validator,
                envelope_schema_validator=envelope_schema_validator,
                canonical_schema_sha256=canonical_schema_sha256,
                effective_schema_sha256=effective_schema_sha256,
                envelope_schema_sha256=envelope_schema_sha256,
                retries=retries,
                semantic_retries=semantic_retries - 1,
                correction=compact_error(exc),
            )
        else:
            mark_failed(
                conn,
                candidates,
                exc,
                retryable=False,
                raw_response=response,
                model=client.model,
            )


def repair_worker(
    worker_id: int,
    work_queue: Queue[tuple[int, list[RepairCandidate]]],
    stop_event: Event,
    progress: ProgressTracker,
    *,
    effective_db: Path,
    canonical_schema: dict[str, Any],
    effective_schema: dict[str, Any],
    envelope_schema: dict[str, Any],
    canonical_schema_sha256: str,
    effective_schema_sha256: str,
    envelope_schema_sha256: str,
    expected_model: str,
    worker_rpm: float,
    startup_delay: float,
    timeout: float,
    max_tokens: int,
    retries: int,
    call_budget: ProviderCallBudget,
) -> WorkerResult:
    client: DeepSeekRepairClient | None = None
    conn: sqlite3.Connection | None = None
    batches_processed = 0
    fatal_reason: str | None = None
    unexpected_reason: str | None = None
    try:
        client = DeepSeekRepairClient(
            rpm=worker_rpm,
            timeout=timeout,
            max_tokens=max_tokens,
            call_budget=call_budget,
        )
        if not client.available:
            raise FatalProviderError(
                "live DeepSeek semantic repair is not enabled/configured"
            )
        if client.model != expected_model:
            raise FatalProviderError("provider model changed while workers were starting")
        conn = effective_connection(effective_db)
        canonical_validator = Draft202012Validator(
            canonical_schema, format_checker=FormatChecker()
        )
        effective_validator = Draft202012Validator(
            effective_schema, format_checker=FormatChecker()
        )
        envelope_validator = Draft202012Validator(
            envelope_schema, format_checker=FormatChecker()
        )
        if startup_delay > 0:
            stop_event.wait(startup_delay)
        while not stop_event.is_set():
            try:
                batch_index, batch = work_queue.get_nowait()
            except Empty:
                break
            if stop_event.is_set():
                work_queue.task_done()
                break
            calls_before = client.call_count
            outcome = "completed"
            stop_after_batch = False
            try:
                process_batch(
                    conn,
                    client,
                    batch,
                    canonical_schema_validator=canonical_validator,
                    effective_schema_validator=effective_validator,
                    envelope_schema_validator=envelope_validator,
                    canonical_schema_sha256=canonical_schema_sha256,
                    effective_schema_sha256=effective_schema_sha256,
                    envelope_schema_sha256=envelope_schema_sha256,
                    retries=retries,
                )
            except FatalProviderError as exc:
                fatal_reason = compact_error(exc)
                outcome = "fatal_configuration"
                stop_after_batch = True
                stop_event.set()
            except Exception as exc:
                unexpected_reason = compact_error(exc)
                outcome = "unexpected_error"
                stop_after_batch = True
                stop_event.set()
                try:
                    mark_failed(
                        conn,
                        batch,
                        exc,
                        retryable=True,
                        raw_response=None,
                        model=client.model,
                    )
                except sqlite3.Error:
                    pass
            finally:
                batches_processed += 1
                try:
                    local_counts = batch_status_counts(conn, batch)
                    global_counts = status_counts(conn)
                except sqlite3.Error:
                    local_counts, global_counts = {}, {}
                progress.record_batch(
                    worker_id=worker_id,
                    batch_index=batch_index,
                    records_in_batch=len(batch),
                    provider_calls=client.call_count - calls_before,
                    local_counts=local_counts,
                    global_counts=global_counts,
                    outcome=outcome,
                )
                work_queue.task_done()
            if stop_after_batch:
                break
    except FatalProviderError as exc:
        fatal_reason = compact_error(exc)
        stop_event.set()
    except Exception as exc:
        unexpected_reason = compact_error(exc)
        stop_event.set()
    finally:
        if conn is not None:
            conn.close()
        if client is not None:
            client.close()
    return WorkerResult(
        worker_id=worker_id,
        batches_processed=batches_processed,
        provider_calls=client.call_count if client is not None else 0,
        fatal_reason=fatal_reason,
        unexpected_reason=unexpected_reason,
    )


def write_manifest(
    conn: sqlite3.Connection,
    manifest_path: Path,
    *,
    source_db: Path,
    annotation_db: Path,
    review_db: Path,
    effective_db: Path,
    canonical_schema_path: Path,
    envelope_schema_path: Path,
    canonical_schema_sha256: str,
    effective_schema_sha256: str,
    envelope_schema_sha256: str,
    source_count: int,
    eligible_count: int,
    selected: Sequence[RepairCandidate],
    model: str,
    provider_calls: int,
    max_provider_calls: int,
    workers: int,
    batch_size: int,
    char_limit: int,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    selected_counts = selected_status_counts(conn, selected)
    manifest = {
        "effective_record_version": EFFECTIVE_RECORD_VERSION,
        "repair_version": REPAIR_VERSION,
        "prompt_version": REPAIR_PROMPT_VERSION,
        "prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "model": model,
        "source_canonical_runtime_records": source_count,
        "eligible_review_records": eligible_count,
        "selected_records": len(selected),
        "selected_status_counts": selected_counts,
        "global_status_counts": status_counts(conn),
        "review_verdict_counts": {
            verdict: sum(1 for item in selected if item.review_verdict == verdict)
            for verdict in ("revise", "uncertain")
        },
        "review_prompt_versions": sorted(
            {item.review_prompt_version for item in selected}
        ),
        "review_prompt_sha256": sorted(
            {item.review_prompt_sha256 for item in selected}
        ),
        "review_schema_sha256": sorted(
            {item.review_schema_sha256 for item in selected}
        ),
        "selected_repair_iteration_counts": {
            str(iteration): sum(
                1 for item in selected if item.repair_iteration == iteration
            )
            for iteration in sorted({item.repair_iteration for item in selected})
        },
        "archived_effective_layers": int(
            conn.execute("SELECT COUNT(*) FROM effective_repair_history").fetchone()[0]
        ),
        "lineage_contract": {
            "canonical_root_column": "effective_repair_jobs.canonical_root_sha256",
            "immediate_base_column": "effective_repair_jobs.base_candidate_sha256",
            "parent_effective_column": "effective_repair_jobs.parent_effective_sha256",
            "iteration_column": "effective_repair_jobs.repair_iteration",
            "current_lineage_column": "effective_repair_jobs.lineage_json",
            "superseded_layers_table": "effective_repair_history",
            "current_effective_column": "effective_repair_jobs.effective_annotation_json",
            "maximum_iterations_default": DEFAULT_MAX_REPAIR_ITERATIONS,
        },
        "source_characters": sum(item.char_count for item in selected),
        "maximum_source_characters": max(
            (item.char_count for item in selected), default=0
        ),
        "oversized_complete_single_records": sum(
            item.char_count > char_limit for item in selected
        ),
        "truncated_source_records": 0,
        "batch_size": batch_size,
        "batch_character_limit": char_limit,
        "workers": workers,
        "provider_calls": provider_calls,
        "current_run_provider_call_limit": max_provider_calls,
        "canonical_schema": {
            "path": display_path(canonical_schema_path),
            "sha256": canonical_schema_sha256,
            "normalized_record_validated": True,
            "effective_record_claimed_valid": False,
        },
        "effective_schema_sha256": effective_schema_sha256,
        "envelope_schema": {
            "path": display_path(envelope_schema_path),
            "sha256": envelope_schema_sha256,
        },
        "inputs": {
            "source_db": display_path(source_db),
            "source_db_sha256": sha256_file(source_db),
            "annotation_db": display_path(annotation_db),
            "annotation_db_sha256": sha256_file(annotation_db),
            "review_db": display_path(review_db),
            "review_db_sha256": sha256_file(review_db),
        },
        "output": {
            "effective_db": display_path(effective_db),
            "effective_db_sha256": sha256_file(effective_db),
        },
        "automatic_semantic_repair": True,
        "human_reviewed": False,
        "research_ready": False,
        "updated_at": utc_now(),
    }
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(manifest_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Repair revise/uncertain A1 semantic reviews into an independent, "
            "resumable effective sidecar without changing C1/A1/review inputs."
        )
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--annotation-db", type=Path, default=DEFAULT_ANNOTATION_DB)
    parser.add_argument("--review-db", type=Path, default=DEFAULT_REVIEW_DB)
    parser.add_argument("--effective-db", type=Path, default=DEFAULT_EFFECTIVE_DB)
    parser.add_argument(
        "--canonical-schema", type=Path, default=DEFAULT_CANONICAL_SCHEMA
    )
    parser.add_argument("--envelope-schema", type=Path, default=DEFAULT_ENVELOPE_SCHEMA)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--verdict",
        dest="verdicts",
        action="append",
        choices=["revise", "uncertain"],
        help="Review verdict to repair; repeat to select both (default: both).",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--sample-mode", choices=["stable", "stratified"], default="stable"
    )
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--batch-char-limit", type=int, default=4000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rpm", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument(
        "--max-repair-iterations",
        type=int,
        default=DEFAULT_MAX_REPAIR_ITERATIONS,
        help="Maximum lineage depth per entry (default: 3).",
    )
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=0,
        help="Hard process-wide call cap; 0 derives four times the planned base batches.",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-quarantined", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--confirm-full-run",
        action="store_true",
        help="Required when a live selection covers every eligible repair review.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.batch_size <= 4:
        raise SystemExit("--batch-size must be between 1 and 4")
    if not 1 <= args.workers <= 4:
        raise SystemExit("--workers must be between 1 and 4")
    if args.batch_char_limit != 4000:
        raise SystemExit("--batch-char-limit is fixed at 4000 for this contract")
    if (
        args.rpm <= 0
        or args.retries < 0
        or args.max_tokens <= 0
        or args.max_provider_calls < 0
        or not 1 <= args.max_repair_iterations <= 20
    ):
        raise SystemExit("RPM/max-tokens must be positive and retries non-negative")
    if args.retry_quarantined and not args.resume:
        raise SystemExit("--retry-quarantined requires --resume")

    source_db = args.source_db.resolve()
    annotation_db = args.annotation_db.resolve()
    review_db = args.review_db.resolve()
    effective_db = args.effective_db.resolve()
    canonical_schema_path = args.canonical_schema.resolve()
    envelope_schema_path = args.envelope_schema.resolve()
    manifest_path = args.manifest.resolve()
    enforce_disjoint_paths(
        source_db=source_db,
        annotation_db=annotation_db,
        review_db=review_db,
        effective_db=effective_db,
        canonical_schema=canonical_schema_path,
        envelope_schema=envelope_schema_path,
        manifest=manifest_path,
    )
    try:
        canonical_schema = json.loads(
            canonical_schema_path.read_text(encoding="utf-8")
        )
        envelope_schema = json.loads(envelope_schema_path.read_text(encoding="utf-8"))
        effective_schema = build_effective_schema(canonical_schema)
        for schema in (canonical_schema, envelope_schema, effective_schema):
            Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, KeyError, SchemaError) as exc:
        raise RuntimeError(f"semantic-repair schema is invalid: {exc}") from exc
    canonical_validator = Draft202012Validator(
        canonical_schema, format_checker=FormatChecker()
    )
    effective_validator = Draft202012Validator(
        effective_schema, format_checker=FormatChecker()
    )
    envelope_validator = Draft202012Validator(
        envelope_schema, format_checker=FormatChecker()
    )
    verdicts = set(args.verdicts or ["revise", "uncertain"])
    all_candidates, source_count = load_repair_candidates(
        source_db,
        annotation_db,
        review_db,
        effective_db,
        verdicts=verdicts,
        canonical_schema_validator=canonical_validator,
        effective_schema_validator=effective_validator,
        envelope_schema_validator=envelope_validator,
        max_repair_iterations=args.max_repair_iterations,
    )
    selected = select_candidates(
        all_candidates, limit=args.limit, sample_mode=args.sample_mode
    )
    enforce_live_scope_confirmation(
        live=bool(args.live),
        selected_count=len(selected),
        eligible_count=len(all_candidates),
        confirmed=bool(args.confirm_full_run),
    )
    batches = pack_repair_batches(
        selected, batch_size=args.batch_size, char_limit=args.batch_char_limit
    )
    max_provider_calls = args.max_provider_calls or max(1, len(batches) * 4)
    plan = {
        "sourceCanonicalRuntimeRecords": source_count,
        "eligibleRepairReviews": len(all_candidates),
        "selectedRecords": len(selected),
        "selectedVerdicts": sorted(verdicts),
        "repairIterationCounts": {
            str(iteration): sum(
                1 for item in selected if item.repair_iteration == iteration
            )
            for iteration in sorted({item.repair_iteration for item in selected})
        },
        "maxRepairIterations": args.max_repair_iterations,
        "plannedBatches": len(batches),
        "plannedSourceCharacters": sum(item.char_count for item in selected),
        "oversizedCompleteSingleRecords": sum(
            item.char_count > args.batch_char_limit for item in selected
        ),
        "maximumSourceCharacters": max(
            (item.char_count for item in selected), default=0
        ),
        "truncatedSourceRecords": 0,
        "sampleMode": args.sample_mode,
        "batchSize": args.batch_size,
        "batchCharacterLimit": args.batch_char_limit,
        "workers": args.workers,
        "totalRequestsPerMinute": args.rpm,
        "maxProviderCalls": max_provider_calls,
        "canonicalInputsReadOnly": True,
        "outputOverwritesCanonicalA1": False,
        "humanReviewed": False,
        "researchReady": False,
        "live": bool(args.live),
    }
    print(json.dumps({"event": "plan", **plan}, ensure_ascii=False), flush=True)
    if not args.live or not selected:
        return 0

    # Verify configuration before creating or mutating the effective ledger.
    probe = DeepSeekRepairClient(
        rpm=float(args.rpm), timeout=args.timeout, max_tokens=args.max_tokens
    )
    requested_model = probe.model
    provider_available = probe.available
    probe.close()
    if not provider_available:
        print(
            json.dumps(
                {
                    "event": "fatal",
                    "reason": "live DeepSeek semantic repair is not enabled/configured",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return 3

    canonical_schema_sha = sha256_file(canonical_schema_path)
    effective_schema_sha = sha256_text(canonical_json(effective_schema))
    envelope_schema_sha = sha256_file(envelope_schema_path)
    conn = init_effective_db(effective_db)
    try:
        invalidated = enqueue_candidates(
            conn,
            selected,
            model=requested_model,
            canonical_schema_sha256=canonical_schema_sha,
            effective_schema_sha256=effective_schema_sha,
            envelope_schema_sha256=envelope_schema_sha,
        )
        todo = pending_candidates(
            conn,
            selected,
            resume=bool(args.resume),
            retry_quarantined=bool(args.retry_quarantined),
        )
        todo_batches = pack_repair_batches(
            todo, batch_size=args.batch_size, char_limit=args.batch_char_limit
        )
        active_workers = min(args.workers, len(todo_batches))
        worker_rpm = per_worker_rpm(args.rpm, active_workers)
        run_detail = {
            **plan,
            "pendingRecords": len(todo),
            "pendingBatches": len(todo_batches),
            "provenanceInvalidated": invalidated,
            "activeWorkers": active_workers,
            "requestsPerMinutePerWorker": worker_rpm,
        }
        log_event(conn, "run_started", run_detail)
        print(
            json.dumps({"event": "run_started", **run_detail}, ensure_ascii=False),
            flush=True,
        )

        progress = ProgressTracker(len(todo_batches))
        call_budget = ProviderCallBudget(max_provider_calls)
        worker_results: list[WorkerResult] = []
        if active_workers:
            work_queue: Queue[tuple[int, list[RepairCandidate]]] = Queue()
            for index, batch in enumerate(todo_batches, start=1):
                work_queue.put((index, batch))
            stop_event = Event()
            with ThreadPoolExecutor(
                max_workers=active_workers,
                thread_name_prefix="a1-semantic-repairer",
            ) as executor:
                futures = {
                    executor.submit(
                        repair_worker,
                        worker_id,
                        work_queue,
                        stop_event,
                        progress,
                        effective_db=effective_db,
                        canonical_schema=canonical_schema,
                        effective_schema=effective_schema,
                        envelope_schema=envelope_schema,
                        canonical_schema_sha256=canonical_schema_sha,
                        effective_schema_sha256=effective_schema_sha,
                        envelope_schema_sha256=envelope_schema_sha,
                        expected_model=requested_model,
                        worker_rpm=worker_rpm,
                        startup_delay=(worker_id - 1) * (60.0 / args.rpm),
                        timeout=args.timeout,
                        max_tokens=args.max_tokens,
                        retries=args.retries,
                        call_budget=call_budget,
                    ): worker_id
                    for worker_id in range(1, active_workers + 1)
                }
                for future in as_completed(futures):
                    worker_id = futures[future]
                    try:
                        worker_results.append(future.result())
                    except Exception as exc:
                        stop_event.set()
                        worker_results.append(
                            WorkerResult(
                                worker_id=worker_id,
                                batches_processed=0,
                                provider_calls=0,
                                unexpected_reason=compact_error(exc),
                            )
                        )

        provider_calls = sum(result.provider_calls for result in worker_results)
        fatal_reasons = [
            result.fatal_reason for result in worker_results if result.fatal_reason
        ]
        unexpected_reasons = [
            result.unexpected_reason
            for result in worker_results
            if result.unexpected_reason
        ]
        if provider_calls != progress.provider_calls:
            unexpected_reasons.append(
                "provider call accounting mismatch between workers and progress"
            )
        write_manifest(
            conn,
            manifest_path,
            source_db=source_db,
            annotation_db=annotation_db,
            review_db=review_db,
            effective_db=effective_db,
            canonical_schema_path=canonical_schema_path,
            envelope_schema_path=envelope_schema_path,
            canonical_schema_sha256=canonical_schema_sha,
            effective_schema_sha256=effective_schema_sha,
            envelope_schema_sha256=envelope_schema_sha,
            source_count=source_count,
            eligible_count=len(all_candidates),
            selected=selected,
            model=requested_model,
            provider_calls=provider_calls,
            max_provider_calls=max_provider_calls,
            workers=active_workers,
            batch_size=args.batch_size,
            char_limit=args.batch_char_limit,
        )
        selected_counts = selected_status_counts(conn, selected)
        terminal_detail = {
            "statusCounts": status_counts(conn),
            "selectedStatusCounts": selected_counts,
            "providerCalls": provider_calls,
            "workers": active_workers,
            "completedBatches": progress.completed_batches,
            "plannedBatches": len(todo_batches),
            "truncatedSourceRecords": 0,
            "humanReviewed": False,
            "researchReady": False,
        }
        if fatal_reasons or unexpected_reasons:
            terminal_detail["fatalReasons"] = fatal_reasons
            terminal_detail["unexpectedReasons"] = unexpected_reasons
            log_event(conn, "run_stopped", terminal_detail)
            write_manifest(
                conn,
                manifest_path,
                source_db=source_db,
                annotation_db=annotation_db,
                review_db=review_db,
                effective_db=effective_db,
                canonical_schema_path=canonical_schema_path,
                envelope_schema_path=envelope_schema_path,
                canonical_schema_sha256=canonical_schema_sha,
                effective_schema_sha256=effective_schema_sha,
                envelope_schema_sha256=envelope_schema_sha,
                source_count=source_count,
                eligible_count=len(all_candidates),
                selected=selected,
                model=requested_model,
                provider_calls=provider_calls,
                max_provider_calls=max_provider_calls,
                workers=active_workers,
                batch_size=args.batch_size,
                char_limit=args.batch_char_limit,
            )
            print(
                json.dumps(
                    {"event": "run_stopped", **terminal_detail}, ensure_ascii=False
                ),
                file=sys.stderr,
                flush=True,
            )
            return 3 if fatal_reasons else 4
        complete = selected_counts == {"valid": len(selected)}
        event = "run_completed" if complete else "run_incomplete"
        log_event(conn, event, terminal_detail)
        write_manifest(
            conn,
            manifest_path,
            source_db=source_db,
            annotation_db=annotation_db,
            review_db=review_db,
            effective_db=effective_db,
            canonical_schema_path=canonical_schema_path,
            envelope_schema_path=envelope_schema_path,
            canonical_schema_sha256=canonical_schema_sha,
            effective_schema_sha256=effective_schema_sha,
            envelope_schema_sha256=envelope_schema_sha,
            source_count=source_count,
            eligible_count=len(all_candidates),
            selected=selected,
            model=requested_model,
            provider_calls=provider_calls,
            max_provider_calls=max_provider_calls,
            workers=active_workers,
            batch_size=args.batch_size,
            char_limit=args.batch_char_limit,
        )
        print(
            json.dumps({"event": event, **terminal_detail}, ensure_ascii=False),
            flush=True,
        )
        return 0 if complete else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
