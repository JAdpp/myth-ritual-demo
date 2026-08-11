#!/usr/bin/env python3
"""Generate resumable A1 retrieval annotations for the C1 story corpus.

The source catalog is opened read-only.  Model output is assembled with
source facts by this script, validated against the A1 JSON Schema, and written
to a separate SQLite database.  Live calls require both ``--live`` and the
existing server-side DeepSeek environment variables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, local
from typing import Any, Iterable, Sequence

import httpx
from dotenv import load_dotenv
from jsonschema import Draft202012Validator, FormatChecker
from opencc import OpenCC

try:  # package import in tests
    from data.validate_a1_annotations import (
        A_SAFETY_PHRASE_RULES,
        B_SAFETY_SIGNAL_RULES,
        SELF_HARM_DEATH_PATTERN,
        signal_match,
    )
except ImportError:  # direct ``python data/annotate_c1_retrieval.py`` execution
    from validate_a1_annotations import (  # type: ignore[no-redef]
        A_SAFETY_PHRASE_RULES,
        B_SAFETY_SIGNAL_RULES,
        SELF_HARM_DEATH_PATTERN,
        signal_match,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT / "data" / "corpus" / "c1_single_story" / "catalog.sqlite3"
DEFAULT_SUMMARY = ROOT / "data" / "corpus" / "c1_single_story" / "summary.json"
DEFAULT_SCHEMA = ROOT / "contracts" / "a1-retrieval-annotation.schema.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "corpus" / "a1_retrieval_annotations"
SQLITE_BUSY_TIMEOUT_MS = 30_000
MAX_WORKERS = 16
ANNOTATION_VERSION = "mengdie-annotation-v1.1"
SCHEMA_VERSION = "mengdie-a1-schema-v1.1"
PROMPT_VERSION = "mengdie-a1-v1.1-nonthinking-r6-quality-gates"
SIMPLIFIED_CONVERTER_LOCAL = local()
WIKISOURCE_MISSING_GLYPH_PLACEHOLDER = "\U000f2cf4"
EVIDENCE_SOURCE_VARIANTS = {"釡": "釜"}


def simplified_converter() -> OpenCC:
    """Return one OpenCC instance per worker thread.

    The annotation worker shares this module across several threads.  OpenCC's
    Python wrapper does not document a thread-safety guarantee for a shared
    converter, so each worker owns its converter instead of racing on a module
    singleton.  Source text and exact evidence never pass through this helper.
    """

    converter = getattr(SIMPLIFIED_CONVERTER_LOCAL, "converter", None)
    if converter is None:
        converter = OpenCC("t2s")
        SIMPLIFIED_CONVERTER_LOCAL.converter = converter
    return converter


def to_simplified_generated_text(value: str) -> str:
    # OpenCC phrase segmentation can expose a second conversion only after the
    # first pass (for example ``於潛`` -> ``於潜`` -> ``于潜``).  Iterate to a
    # fixed point so a row is already idempotent when the independent validator
    # applies its own t2s check.
    converter = simplified_converter()
    current = value
    for _ in range(4):
        converted = converter.convert(current)
        if converted == current:
            return current
        current = converted
    return current

LIFE_CONTEXTS = {
    "relationship_boundary",
    "family_duty",
    "belonging_isolation",
    "separation_loss",
    "work_study_pressure",
    "long_term_responsibility",
    "choice_uncertainty",
    "injustice_conflict",
    "identity_transition",
    "persistence_change",
}
CONFLICT_TYPES = {
    "self",
    "relational",
    "institutional_collective",
    "nature_fate",
    "knowledge_uncertainty",
}
AGENCY_MODES = {
    "endure",
    "avoid",
    "seek_help",
    "negotiate",
    "confront",
    "transform_method",
    "withdraw",
    "sacrifice",
    "collective_action",
    "unknown",
}
ENDING_MODES = {
    "restoration",
    "transformation",
    "separation",
    "sacrifice",
    "unresolved",
    "cautionary",
    "open",
    "unknown",
}
CONTENT_WARNINGS = {
    "death",
    "violence",
    "physical_injury",
    "self_harm_or_suicide",
    "sexual_content",
    "sexual_violence",
    "coercion_or_abuse",
    "child_harm",
    "animal_harm",
    "discrimination",
    "captivity",
    "supernatural_horror",
    "grief_or_bereavement",
    "illness",
    "none_identified",
}
INTERPRETATION_RISKS = {
    "glorify_self_sacrifice",
    "normalize_violence",
    "victim_blaming",
    "fatalism",
    "gender_stereotype",
    "filial_coercion",
    "revenge_as_justice",
    "authority_obedience",
    "none_identified",
}

# Extra phrases are unambiguous enough to repair locally but deliberately
# narrower than the broad lexical signals used by the independent validator.
EXTRA_EXPLICIT_SAFETY_RULES: tuple[
    tuple[re.Pattern[str], tuple[str, ...]], ...
] = (
    (
        re.compile(r"自焚|投水|赴水|服毒|吞藥|吞药"),
        ("self_harm_or_suicide",),
    ),
    (
        re.compile(r"囚禁|禁錮|禁锢|下獄|下狱|坐獄|坐狱"),
        ("captivity",),
    ),
    (
        re.compile(r"歡好|欢好|同寢|同寝|交合|私通|姦通|奸通|枕席"),
        ("sexual_content",),
    ),
)
ALLOWED_SUPPORT = re.compile(
    r"^(retrieval_profile\.modern_retrieval_summary|life_context\.[a-z_]+|"
    r"narrative_arc\.(trigger|conflict_types\.[a-z_]+|agency_modes\.[a-z_]+|"
    r"ending_mode\.[a-z_]+)|auto_safety_screen\."
    r"(flags|interpretation_risks)\.[a-z_]+)$"
)


class AnnotationError(RuntimeError):
    """A model response cannot be admitted to the derived corpus."""


class RetryableProviderError(RuntimeError):
    """A provider or transport failure can be retried later."""


class FatalProviderError(RuntimeError):
    """Authentication/model configuration is invalid; stop the run."""


class ProviderCallBudgetExceeded(RuntimeError):
    """The process-wide provider-call ceiling has been reached."""


class ProviderCallBudget:
    """Thread-safe, process-wide hard ceiling for paid provider requests."""

    def __init__(self, max_calls: int) -> None:
        if max_calls <= 0:
            raise ValueError("provider call budget must be positive")
        self.max_calls = max_calls
        self._used = 0
        self._exhausted = False
        self._lock = Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._exhausted

    def reserve(self) -> int:
        with self._lock:
            if self._used >= self.max_calls:
                self._exhausted = True
                raise ProviderCallBudgetExceeded(
                    f"provider call budget exhausted ({self.max_calls})"
                )
            self._used += 1
            return self._used


@dataclass(frozen=True)
class SourceEntry:
    entry_id: str
    source_work_id: str
    source_work_title: str
    source_work_period: str
    volume: str
    source_locator: str
    entry_ordinal: int
    title: str
    source_url: str
    char_count: int
    text: str
    extracted_text_sha256: str


@dataclass(frozen=True)
class WorkerResult:
    """Terminal accounting returned by one thread-owned worker."""

    worker_id: int
    batches_processed: int
    provider_calls: int
    fatal_reason: str | None = None
    unexpected_reason: str | None = None


class ProgressTracker:
    """Serialize progress output and aggregate provider-call accounting."""

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
        batch_status_counts: dict[str, int],
        global_status_counts: dict[str, int],
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
                        "batchStatusCounts": batch_status_counts,
                        "statusCounts": global_status_counts,
                        "outcome": outcome,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def acquire_process_lock(path: Path) -> Any:
    """Hold one OS-backed exclusive byte lock for the live ledger writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - exercised by non-Windows CI only
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise SystemExit(
            "another live A1 annotation process already holds the output ledger lock"
        ) from exc
    return handle


def release_process_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - exercised by non-Windows CI only
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def compact_error(exc: BaseException) -> str:
    """Return a non-secret diagnostic suitable for the job ledger."""
    message = re.sub(r"\s+", " ", str(exc)).strip()
    message = re.sub(r"sk-[A-Za-z0-9._-]+", "[redacted]", message)
    return message[:400]


def display_path(path: Path) -> str:
    """Prefer a project-relative path without rejecting explicit external paths."""
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def source_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_source_entries(path: Path) -> list[SourceEntry]:
    with source_connection(path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"source SQLite integrity check failed: {integrity}")
        rows = conn.execute(
            """
            SELECT entry_id, source_work_id, source_work_title,
                   source_work_period, volume, source_locator, entry_ordinal,
                   title, source_url, char_count, text,
                   extracted_text_sha256
            FROM entries
            WHERE dedupe_status = 'canonical' AND runtime_eligible = 1
            ORDER BY source_work_id, entry_ordinal, entry_id
            """
        ).fetchall()
    entries = [SourceEntry(**dict(row)) for row in rows]
    if not entries:
        raise RuntimeError("source query returned no canonical runtime records")
    for entry in entries:
        actual = sha256_text(entry.text)
        if actual != entry.extracted_text_sha256:
            raise RuntimeError(f"source hash mismatch for {entry.entry_id}")
    return entries


def length_bucket(char_count: int) -> int:
    if char_count <= 100:
        return 0
    if char_count <= 500:
        return 1
    if char_count <= 1000:
        return 2
    if char_count <= 3000:
        return 3
    return 4


def stratified_sample(entries: Sequence[SourceEntry], limit: int) -> list[SourceEntry]:
    groups: dict[tuple[str, int], list[SourceEntry]] = {}
    for entry in entries:
        groups.setdefault((entry.source_work_id, length_bucket(entry.char_count)), []).append(entry)
    for key, values in groups.items():
        seed = int(hashlib.sha256(f"{key[0]}:{key[1]}".encode()).hexdigest()[:16], 16)
        random.Random(seed).shuffle(values)
    keys = sorted(groups)
    selected: list[SourceEntry] = []
    cursor = 0
    while keys and len(selected) < limit:
        key = keys[cursor % len(keys)]
        values = groups[key]
        if values:
            selected.append(values.pop())
        if not values:
            keys.remove(key)
            cursor = 0
        else:
            cursor += 1
    return selected


def select_entries(
    entries: Sequence[SourceEntry],
    *,
    limit: int | None,
    sample_mode: str,
    entry_id_file: Path | None,
) -> list[SourceEntry]:
    by_id = {entry.entry_id: entry for entry in entries}
    if entry_id_file:
        requested = [
            line.strip()
            for line in entry_id_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        missing = [entry_id for entry_id in requested if entry_id not in by_id]
        if missing:
            raise RuntimeError(f"entry-id file contains {len(missing)} unknown IDs")
        selected = [by_id[entry_id] for entry_id in dict.fromkeys(requested)]
        return selected[:limit] if limit else selected
    if limit is None:
        return list(entries)
    if limit <= 0:
        raise RuntimeError("--limit must be positive")
    if sample_mode == "stratified":
        return stratified_sample(entries, min(limit, len(entries)))
    return list(entries[:limit])


def pack_batches(
    entries: Sequence[SourceEntry], *, batch_size: int, char_limit: int
) -> list[list[SourceEntry]]:
    batches: list[list[SourceEntry]] = []
    current: list[SourceEntry] = []
    current_chars = 0
    for entry in entries:
        if entry.char_count > 3000:
            if current:
                batches.append(current)
                current, current_chars = [], 0
            batches.append([entry])
            continue
        if current and (
            len(current) >= batch_size or current_chars + entry.char_count > char_limit
        ):
            batches.append(current)
            current, current_chars = [], 0
        current.append(entry)
        current_chars += entry.char_count
    if current:
        batches.append(current)
    return batches


def per_worker_rpm(total_rpm: float, active_workers: int) -> float:
    """Split the configured process RPM evenly across active workers."""

    return total_rpm / active_workers if active_workers > 0 else 0.0


def configure_derived_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Configure one thread-owned derived DB connection for concurrent writers."""
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def derived_connection(path: Path) -> sqlite3.Connection:
    """Open an existing derived DB using an independent WAL connection."""
    return configure_derived_connection(
        sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    )


def init_derived_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = configure_derived_connection(
        sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS annotation_jobs (
            entry_id TEXT PRIMARY KEY,
            source_text_sha256 TEXT NOT NULL,
            source_work_id TEXT NOT NULL,
            source_work_title TEXT NOT NULL,
            title TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'queued', 'leased', 'valid', 'retryable_failed',
                'quarantined', 'stale'
            )),
            attempts INTEGER NOT NULL DEFAULT 0,
            annotation_json TEXT,
            error_kind TEXT,
            error_message TEXT,
            last_raw_response_json TEXT,
            provider_response_id TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            model TEXT,
            prompt_version TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_annotation_jobs_status
            ON annotation_jobs(status);
        CREATE TABLE IF NOT EXISTS run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail_json TEXT NOT NULL
        );
        """
    )
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(annotation_jobs)").fetchall()
    }
    if "last_raw_response_json" not in columns:
        conn.execute("ALTER TABLE annotation_jobs ADD COLUMN last_raw_response_json TEXT")
    conn.commit()
    return conn


def enqueue(conn: sqlite3.Connection, entries: Sequence[SourceEntry]) -> None:
    now = utc_now()
    with conn:
        for entry in entries:
            existing = conn.execute(
                "SELECT source_text_sha256, status FROM annotation_jobs WHERE entry_id = ?",
                (entry.entry_id,),
            ).fetchone()
            if existing and existing["source_text_sha256"] != entry.extracted_text_sha256:
                conn.execute(
                    """
                    UPDATE annotation_jobs
                    SET source_text_sha256=?, status='stale', annotation_json=NULL,
                        error_kind='source_changed', error_message=NULL, updated_at=?
                    WHERE entry_id=?
                    """,
                    (entry.extracted_text_sha256, now, entry.entry_id),
                )
            elif not existing:
                conn.execute(
                    """
                    INSERT INTO annotation_jobs(
                        entry_id, source_text_sha256, source_work_id,
                        source_work_title, title, char_count, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        entry.entry_id,
                        entry.extracted_text_sha256,
                        entry.source_work_id,
                        entry.source_work_title,
                        entry.title,
                        entry.char_count,
                        now,
                        now,
                    ),
                )


def pending_entries(
    conn: sqlite3.Connection,
    selected: Sequence[SourceEntry],
    *,
    resume: bool,
    retry_quarantined: bool,
) -> list[SourceEntry]:
    states = {
        row["entry_id"]: row["status"]
        for row in conn.execute("SELECT entry_id, status FROM annotation_jobs")
    }
    already = [entry.entry_id for entry in selected if states.get(entry.entry_id) == "valid"]
    if already and not resume:
        raise RuntimeError(
            f"{len(already)} selected entries are already valid; pass --resume to skip them"
        )
    allowed = {"queued", "retryable_failed", "stale", "leased"}
    if retry_quarantined:
        allowed.add("quarantined")
    return [entry for entry in selected if states.get(entry.entry_id) in allowed]


def invalidate_incompatible_valid_rows(
    conn: sqlite3.Connection,
    *,
    requested_model: str,
) -> int:
    """Fail closed when model/prompt/schema provenance differs on resume."""
    stale_ids: list[str] = []
    for row in conn.execute(
        "SELECT entry_id, annotation_json FROM annotation_jobs WHERE status='valid'"
    ):
        try:
            record = json.loads(row["annotation_json"])["annotation_record"]
            meta = record["annotation_meta"]
            generator = meta["generator"]
            compatible = (
                record["annotation_version"] == ANNOTATION_VERSION
                and meta["schema_version"] == SCHEMA_VERSION
                and generator["prompt_version"] == PROMPT_VERSION
                and generator["model"] == requested_model
            )
        except (TypeError, KeyError, json.JSONDecodeError):
            compatible = False
        if not compatible:
            stale_ids.append(row["entry_id"])
    if stale_ids:
        now = utc_now()
        with conn:
            conn.executemany(
                """
                UPDATE annotation_jobs
                SET status='stale', error_kind='provenance_changed',
                    error_message=NULL, updated_at=? WHERE entry_id=?
                """,
                [(now, entry_id) for entry_id in stale_ids],
            )
    return len(stale_ids)


SYSTEM_PROMPT = """你是《梦蝶记》的中国古典叙事轻量标注器。只依据每条输入的当前原文，输出简体中文的可检索释义与可核验标签。不要混入大众熟知的其他版本，不诊断心理，不给治疗建议，不添加动机或道德结论。

每条都必须原样返回 entryId，且只能返回 JSON 对象 {"annotations":[...]}。modernRetrievalSummary 为20至250字，通常60至180字；短原文应忠实而短，不得凑字。释义必须逐项核对人物、动作、对象、时间、条件和结果：须、待、欲、将、未、若、云、疑、梦等未完成、条件、传闻或梦中事件，不得改写成已经发生的事实；官职、地名和迁任不得自行换成另一职衔；同一条中的不同人物、插话或故事不得合并。总结只能概括其 evidence 能直接支持的事实，不得把“击而不见”夸写成“斩杀”。

证据 excerpt 必须是该条原文中连续、逐字一致、不超过80字的子串。narrativeSufficiency 只判断原文是否具备可辨识的叙事结构，不由 lifeContext 是否非空决定：完整叙事也可能没有与生活情境枚举直接对应的标签，此时 lifeContext 可为空且 narrativeSufficiency 仍可为 sufficient。极短、目录性或非叙事文本可将 narrativeSufficiency 设为 insufficient/unknown、lifeContext 为空、conflictTypes 为空、agencyModes=["unknown"]、endingMode="unknown"，不要强迫补全。

lifeContext 最多3项，只在原文明示时使用：relationship_boundary=关系中的边界或亲疏冲突；family_duty=明确的亲属照护、义务或家庭冲突；belonging_isolation=接纳、排斥或孤立；separation_loss=分离或丧失；work_study_pressure=明确的工作、学业任务压力，不是仅仅任官、经商或作诗；long_term_responsibility=持续承担的义务，不是只出现较长时间；choice_uncertainty=明确犹豫或多种选择；injustice_conflict=不公、冤屈或权利冲突；identity_transition=身份角色的实质转变；persistence_change=持续努力或改变过程，不是一次行动。
conflictTypes 最多2项：self=人物内在冲突；relational=人物间冲突；institutional_collective=制度或群体权力冲突，不是普通母子争执；nature_fate=自然、超自然限制或命运；knowledge_uncertainty=事实真伪或认知不确定。
agencyModes 最多2项：endure=人物有意识承受持续困境，不是事情落在其身上；avoid=主动回避；seek_help=明确求助，不是传话、考证或受人帮助；negotiate=协商；confront=主动正面对抗，不是被攻击；transform_method=主动改变方法，不是升官或结局变化；withdraw=主动退出，不是被抛弃；sacrifice=主动放弃重要利益，不是被害；collective_action=多人协调行动，不是单人资助或从军；无充分证据用 unknown。
endingMode：restoration=恢复原有秩序；transformation=结局形成持续的新状态，不是任意变化；separation=以分离告终；sacrifice=以主动牺牲告终；unresolved=冲突未决；cautionary=明确警示性后果；open=开放结局；证据不足用 unknown。

flags 只可取 death,violence,physical_injury,self_harm_or_suicide,sexual_content,sexual_violence,coercion_or_abuse,child_harm,animal_harm,discrimination,captivity,supernatural_horror,grief_or_bereavement,illness,none_identified。安全初筛宁可保守多标，不得漏掉原文中的明确伤害：
- 奸污、强奸、逼奸等必须同时标 sexual_violence、sexual_content、coercion_or_abuse；欢好、同寝、交合、私通、枕席等标 sexual_content。
- 自经、自缢、投缳、自刎、自杀、自焚、投水、赴水、服毒等标 self_harm_or_suicide；若明确死亡再标 death。
- 逼迫、威胁、虐待标 coercion_or_abuse；枷锁、系狱、囚禁、幽闭、禁锢标 captivity。
- 车裂、支解、穿腮、杖脊、鞭楚、乱殴、拷掠、血流等标 violence 与 physical_injury；原文明确死、亡、卒、杀害则标 death。
- 动物被杀、杖击、剥皮、烹食或死亡标 animal_harm；儿童被伤害标 child_harm。
- 病、疾、衰弱、消瘦、精神日减等身体或精神耗损标 illness；只有妖鬼意象且没有惊惧、威胁或伤害时不必自动标 supernatural_horror。
interpretationRisks 只可取 glorify_self_sacrifice,normalize_violence,victim_blaming,fatalism,gender_stereotype,filial_coercion,revenge_as_justice,authority_obedience,none_identified。none_identified 不得和其他值并列；不确定时 status="unknown"，不得把未检出写成经人工确认安全。只在当前原文确实没有任何上述警示时使用 none_identified。

每条结构：entryId,modernRetrievalSummary,narrativeSufficiency,narrativeSufficiencyReason,keyEntities[{name,role,evidenceExcerpt}],plotBeats[{type(trigger|action_or_turn|outcome),text,evidenceExcerpt}],motifTerms,lifeContext,narrativeArc{trigger,conflictTypes,agencyModes,endingMode},autoSafetyScreen{status(auto_screened|unknown),flags,interpretationRisks,uncertainties},evidence[{excerpt,supports}],overallConfidence(low|medium|high)。
supports 只使用 retrieval_profile.modern_retrieval_summary、life_context.<标签>、narrative_arc.trigger、narrative_arc.conflict_types.<标签>、narrative_arc.agency_modes.<标签>、narrative_arc.ending_mode.<标签>、auto_safety_screen.flags.<标签>、auto_safety_screen.interpretation_risks.<标签>。现代释义、每个 lifeContext、trigger、每个非unknown conflict/agency/ending、每个非none风险标签都必须至少有一条逐字证据支持；不得为 unknown、none_identified 或最终未选择的标签填写 supports。"""


def build_user_payload(entries: Sequence[SourceEntry]) -> dict[str, Any]:
    return {
        "annotationVersion": ANNOTATION_VERSION,
        "records": [
            {
                "entryId": entry.entry_id,
                "work": entry.source_work_title,
                "volume": entry.volume,
                "locator": entry.source_locator,
                "title": entry.title,
                "sourceText": entry.text,
            }
            for entry in entries
        ],
    }


class DeepSeekBatchClient:
    def __init__(
        self,
        *,
        rpm: float,
        timeout: float,
        max_tokens: int,
        call_budget: ProviderCallBudget | None = None,
    ) -> None:
        load_dotenv(ROOT / ".env", override=False)
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.live_enabled = os.getenv("ENABLE_LIVE_MODEL_GENERATION", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.timeout = timeout
        self.max_tokens = max_tokens
        if rpm <= 0:
            raise FatalProviderError("worker RPM must be positive")
        self.min_interval = 60.0 / rpm
        self.last_request_at = 0.0
        self.call_count = 0
        self.call_budget = call_budget
        self.client = httpx.Client(timeout=timeout)

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.live_enabled

    def close(self) -> None:
        self.client.close()

    def complete(
        self,
        entries: Sequence[SourceEntry],
        retries: int,
        *,
        correction: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        user_payload = build_user_payload(entries)
        if correction:
            user_payload["previousValidationError"] = correction
            user_payload["correctionInstruction"] = (
                "上一输出未通过本地硬校验。请重新从原文生成完整结果，严格只用已列枚举，"
                "并为每个必需字段补足逐字证据；不要解释错误。"
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
        }
        for attempt in range(retries + 1):
            wait = self.min_interval - (time.monotonic() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            self.last_request_at = time.monotonic()
            if self.call_budget is not None:
                self.call_budget.reserve()
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
                time.sleep(min(30.0, 2.0 ** attempt))
                continue
            if response.status_code in {401, 403, 404}:
                raise FatalProviderError(f"provider rejected configuration ({response.status_code})")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= retries:
                    raise RetryableProviderError(f"provider status {response.status_code}")
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30.0, 2.0 ** attempt)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise FatalProviderError(f"provider rejected request ({response.status_code})")
            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                if isinstance(content, str):
                    candidate = content.strip()
                    if candidate.startswith("```"):
                        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
                        candidate = re.sub(r"\s*```$", "", candidate)
                    if not candidate.startswith("{"):
                        start, end = candidate.find("{"), candidate.rfind("}")
                        if start >= 0 and end > start:
                            candidate = candidate[start : end + 1]
                    parsed = json.loads(candidate)
                else:
                    parsed = content
                if not isinstance(parsed, dict):
                    raise ValueError("response content is not an object")
                return parsed, {
                    "response_id": str(body.get("id") or ""),
                    "usage": body.get("usage") or {},
                    "model": str(body.get("model") or self.model),
                }
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AnnotationError("provider returned malformed JSON") from exc
        raise AssertionError("unreachable")


def require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AnnotationError(f"{name} must be an array")
    return value


def enum_list(value: Any, allowed: set[str], name: str, maximum: int) -> list[str]:
    values = require_list(value, name)
    cleaned: list[str] = []
    for item in values:
        if not isinstance(item, str) or item not in allowed:
            raise AnnotationError(f"{name} contains an invalid value")
        if item not in cleaned:
            cleaned.append(item)
    if len(cleaned) > maximum:
        raise AnnotationError(f"{name} exceeds {maximum} values")
    return cleaned


def lenient_enum_list(
    value: Any, allowed: set[str], name: str, maximum: int
) -> tuple[list[str], int]:
    """Drop out-of-codebook model labels; later evidence gates still apply."""
    values = require_list(value, name)
    cleaned: list[str] = []
    dropped = 0
    for item in values:
        if not isinstance(item, str) or item not in allowed:
            dropped += 1
            continue
        if item not in cleaned:
            cleaned.append(item)
    if len(cleaned) > maximum:
        dropped += len(cleaned) - maximum
        cleaned = cleaned[:maximum]
    return cleaned, dropped


def clean_text(value: Any, name: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise AnnotationError(f"{name} must be text")
    # All model-authored prose is stored as Simplified Chinese.  Source text,
    # locators and exact evidence never pass through this function.
    cleaned = to_simplified_generated_text(re.sub(r"\s+", " ", value).strip())
    if not minimum <= len(cleaned) <= maximum:
        raise AnnotationError(f"{name} length is outside {minimum}..{maximum}")
    return cleaned


def clean_summary(value: Any, *, allow_local_repair: bool) -> tuple[str, bool]:
    """Validate the modern summary, with a final-attempt sentence-bound repair."""
    if not isinstance(value, str):
        raise AnnotationError("modernRetrievalSummary must be text")
    cleaned = to_simplified_generated_text(re.sub(r"\s+", " ", value).strip())
    if len(cleaned) < 20:
        raise AnnotationError("modernRetrievalSummary length is outside 20..250")
    if len(cleaned) <= 250:
        return cleaned, False
    if not allow_local_repair:
        raise AnnotationError("modernRetrievalSummary length is outside 20..250")
    window = cleaned[:250]
    sentence_end = max(window.rfind(mark) for mark in "。！？；")
    repaired = window[: sentence_end + 1] if sentence_end >= 19 else window
    return repaired, True


def is_explicitly_unknown_trigger(value: Any) -> bool:
    """Recognize a model's explicit statement that the source has no trigger."""

    if not isinstance(value, str):
        return False
    cleaned = to_simplified_generated_text(re.sub(r"\s+", "", value)).lower()
    if cleaned in {"unknown", "未知", "不详", "不明"}:
        return True
    return bool(
        re.match(
            r"^(?:无|没有|未见|不详|不明)(?:明确|具体|可辨|可识别)?"
            r".{0,20}(?:触发|起因|冲突)",
            cleaned,
        )
    )


def simplify_annotation_generated_texts(annotation: dict[str, Any]) -> int:
    """Normalize only model-authored display text in one stored envelope.

    This mirrors the independent validator's generated-field allowlist.  It
    deliberately excludes the source profile, titles, locators and verbatim
    evidence so a repair can never rewrite provenance.
    """

    record = annotation.get("annotation_record")
    if not isinstance(record, dict):
        raise AnnotationError("stored annotation_record must be an object")
    changed = 0

    def convert_key(mapping: Any, key: str) -> None:
        nonlocal changed
        if not isinstance(mapping, dict) or not isinstance(mapping.get(key), str):
            return
        value = mapping[key]
        converted = to_simplified_generated_text(value)
        if converted != value:
            mapping[key] = converted
            changed += 1

    retrieval = record.get("retrieval_profile")
    convert_key(retrieval, "modern_retrieval_summary")
    convert_key(retrieval, "narrative_sufficiency_reason")
    if isinstance(retrieval, dict):
        for entity in retrieval.get("key_entities") or []:
            convert_key(entity, "name")
            convert_key(entity, "role")
        for beat in retrieval.get("plot_beats") or []:
            convert_key(beat, "text")
        motifs = retrieval.get("motif_terms")
        if isinstance(motifs, list):
            for index, value in enumerate(motifs):
                if not isinstance(value, str):
                    continue
                converted = to_simplified_generated_text(value)
                if converted != value:
                    motifs[index] = converted
                    changed += 1

    convert_key(record.get("narrative_arc"), "trigger")
    safety = record.get("auto_safety_screen")
    if isinstance(safety, dict):
        uncertainties = safety.get("uncertainties")
        if isinstance(uncertainties, list):
            for index, value in enumerate(uncertainties):
                if not isinstance(value, str):
                    continue
                converted = to_simplified_generated_text(value)
                if converted != value:
                    uncertainties[index] = converted
                    changed += 1
    return changed


def repair_valid_generated_texts(
    conn: sqlite3.Connection,
    *,
    schema_validator: Draft202012Validator,
) -> tuple[int, int]:
    """Repair legacy valid rows that escaped Simplified-Chinese normalization."""

    repaired_records = 0
    repaired_fields = 0
    rows = conn.execute(
        "SELECT entry_id, annotation_json FROM annotation_jobs "
        "WHERE status='valid' AND annotation_json IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            annotation = json.loads(row["annotation_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise AnnotationError(
                f"stored valid annotation is not JSON: {row['entry_id']}"
            ) from exc
        if not isinstance(annotation, dict):
            raise AnnotationError(
                f"stored valid annotation is not an object: {row['entry_id']}"
            )
        record = annotation.get("annotation_record")
        if not isinstance(record, dict):
            raise AnnotationError(
                f"stored valid annotation_record is missing: {row['entry_id']}"
            )
        immutable_before = json.dumps(
            {
                "unit": record.get("unit"),
                "source_profile": record.get("source_profile"),
                "evidence": record.get("evidence"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        changed = simplify_annotation_generated_texts(annotation)
        if not changed:
            continue
        immutable_after = json.dumps(
            {
                "unit": record.get("unit"),
                "source_profile": record.get("source_profile"),
                "evidence": record.get("evidence"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if immutable_after != immutable_before:
            raise AnnotationError(
                f"generated-text repair changed provenance: {row['entry_id']}"
            )
        errors = sorted(schema_validator.iter_errors(annotation), key=lambda exc: list(exc.path))
        if errors:
            first = errors[0]
            raise AnnotationError(
                f"generated-text repair failed schema at {list(first.path)}: {first.message}"
            )
        with conn:
            conn.execute(
                "UPDATE annotation_jobs SET annotation_json=?, updated_at=? "
                "WHERE entry_id=? AND status='valid'",
                (
                    json.dumps(annotation, ensure_ascii=False, separators=(",", ":")),
                    utc_now(),
                    row["entry_id"],
                ),
            )
        repaired_records += 1
        repaired_fields += changed
    return repaired_records, repaired_fields


def repair_valid_unknown_trigger_supports(
    conn: sqlite3.Connection,
    *,
    schema_validator: Draft202012Validator,
) -> tuple[int, int]:
    """Remove legacy evidence links to an explicitly unknown trigger.

    ``unknown`` is a non-claim and therefore must neither require nor retain a
    support path.  Only the derived ``supports`` array is changed; exact
    excerpts, offsets and source fields remain byte-for-byte untouched.
    """

    repaired_records = 0
    removed_links = 0
    rows = conn.execute(
        "SELECT entry_id, annotation_json FROM annotation_jobs "
        "WHERE status='valid' AND annotation_json IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            annotation = json.loads(row["annotation_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise AnnotationError(
                f"stored valid annotation is not JSON: {row['entry_id']}"
            ) from exc
        record = annotation.get("annotation_record") if isinstance(annotation, dict) else None
        if not isinstance(record, dict):
            raise AnnotationError(
                f"stored valid annotation_record is missing: {row['entry_id']}"
            )
        trigger = (record.get("narrative_arc") or {}).get("trigger")
        if not isinstance(trigger, str) or trigger.strip().lower() not in {
            "unknown",
            "未知",
            "不详",
            "不明",
        }:
            continue
        changed = 0
        for evidence in record.get("evidence") or []:
            if not isinstance(evidence, dict) or not isinstance(evidence.get("supports"), list):
                continue
            supports = evidence["supports"]
            kept = [value for value in supports if value != "narrative_arc.trigger"]
            changed += len(supports) - len(kept)
            if not kept:
                raise AnnotationError(
                    "unknown-trigger support repair would leave empty evidence: "
                    f"{row['entry_id']}"
                )
            evidence["supports"] = kept
        if not changed:
            continue
        errors = sorted(schema_validator.iter_errors(annotation), key=lambda exc: list(exc.path))
        if errors:
            first = errors[0]
            raise AnnotationError(
                f"unknown-trigger support repair failed schema at {list(first.path)}: "
                f"{first.message}"
            )
        with conn:
            conn.execute(
                "UPDATE annotation_jobs SET annotation_json=?, updated_at=? "
                "WHERE entry_id=? AND status='valid'",
                (
                    json.dumps(annotation, ensure_ascii=False, separators=(",", ":")),
                    utc_now(),
                    row["entry_id"],
                ),
            )
        repaired_records += 1
        removed_links += changed
    return repaired_records, removed_links


def clean_excerpt(value: Any, name: str = "evidence excerpt") -> str:
    if not isinstance(value, str):
        raise AnnotationError(f"{name} must be text")
    cleaned = value.strip()
    if len(cleaned) < 2:
        raise AnnotationError(f"{name} length is below 2")
    if len(cleaned) > 10_000:
        raise AnnotationError(f"{name} is implausibly long")
    return cleaned


def resolve_source_excerpt(source_text: str, candidate: str) -> str | None:
    """Resolve a quote while preserving the final excerpt as an exact substring.

    Wikisource transcriptions occasionally insert parenthetical edition notes
    inside a sentence.  Models tend to omit those notes when copying.  The
    fallback below may bridge only such parenthetical spans, then returns the
    corresponding untouched source span.  It does not perform fuzzy lexical
    matching or rewrite characters.
    """
    def bounded_exact_window(exact_span: str) -> str:
        """Keep an exact quote while reducing an overlong span at a sentence edge."""
        if len(exact_span) <= 80:
            return exact_span
        prefix = exact_span[:80]
        sentence_end = max(prefix.rfind(mark) for mark in "。！？；\n\r")
        return prefix[: sentence_end + 1] if sentence_end >= 1 else prefix

    if candidate in source_text:
        return bounded_exact_window(candidate)
    # A model may explicitly mark one omitted source span with a Chinese
    # double ellipsis.  Bridge it only when both substantial anchors are
    # unique, ordered, and the untouched source span still fits the evidence
    # ceiling.  This is deterministic source recovery, not fuzzy matching.
    if candidate.count("……") == 1:
        left_anchor, right_anchor = candidate.split("……")
        if (
            len(left_anchor) >= 2
            and len(right_anchor) >= 2
            and source_text.count(left_anchor) == 1
            and source_text.count(right_anchor) == 1
        ):
            source_start = source_text.find(left_anchor)
            right_start = source_text.find(
                right_anchor, source_start + len(left_anchor)
            )
            if right_start >= 0:
                source_end = right_start + len(right_anchor)
                exact_span = source_text[source_start:source_end]
                if len(exact_span) <= 80:
                    return exact_span
    excluded: set[int] = set()
    for match in re.finditer(
        r"〈[^〈〉\r\n]*〉|［[^［］\r\n]*］|\([^()\r\n]*\)|（[^（）\r\n]*）",
        source_text,
    ):
        excluded.update(range(match.start(), match.end()))
    if excluded:
        compact_chars: list[str] = []
        source_indices: list[int] = []
        for index, char in enumerate(source_text):
            if index in excluded:
                continue
            compact_chars.append(char)
            source_indices.append(index)
        compact = "".join(compact_chars)
        start = compact.find(candidate)
        if start >= 0:
            end = start + len(candidate) - 1
            source_start = source_indices[start]
            source_end = source_indices[end] + 1
            return bounded_exact_window(source_text[source_start:source_end])

    # Some model responses copy source evidence after converting only Han
    # characters to Simplified Chinese and dropping Chinese quotation marks.
    # Reconstruct an index map from that strictly defined display transform;
    # admit it only when the normalized quote occurs exactly once and maps
    # back to an untouched source span of at most 80 characters.
    dropped_quotes = set("「」『』")

    def display_form(text: str, *, with_indices: bool) -> tuple[str, list[int]]:
        chars: list[str] = []
        indices: list[int] = []
        ignored_source_indices: set[int] = set()
        if with_indices:
            for note in re.finditer(
                r"\[\d+\]|〈[^〈〉\r\n]*〉|［[^［］\r\n]*］|\([^()\r\n]*\)|（[^（）\r\n]*）",
                text,
            ):
                ignored_source_indices.update(range(note.start(), note.end()))
        for source_index, char in enumerate(text):
            if (
                char in dropped_quotes
                or source_index in ignored_source_indices
                or (with_indices and char == WIKISOURCE_MISSING_GLYPH_PLACEHOLDER)
                or (with_indices and unicodedata.category(char) == "Cf")
            ):
                continue
            normalized_char = EVIDENCE_SOURCE_VARIANTS.get(char, char) if with_indices else char
            converted = to_simplified_generated_text(normalized_char)
            chars.extend(converted)
            if with_indices:
                indices.extend([source_index] * len(converted))
        return "".join(chars), indices

    normalized_source, normalized_indices = display_form(
        source_text, with_indices=True
    )
    normalized_candidate, _ = display_form(candidate, with_indices=False)
    if (
        len(normalized_candidate) >= 2
        and normalized_source.count(normalized_candidate) == 1
    ):
        normalized_start = normalized_source.find(normalized_candidate)
        normalized_end = normalized_start + len(normalized_candidate) - 1
        source_start = normalized_indices[normalized_start]
        source_end = normalized_indices[normalized_end] + 1
        while source_start > 0 and source_text[source_start - 1] in dropped_quotes:
            source_start -= 1
        while source_end < len(source_text) and source_text[source_end] in dropped_quotes:
            source_end += 1
        exact_span = source_text[source_start:source_end]
        if len(exact_span) <= 80:
            return exact_span

    # A square missing-glyph marker can be followed by an explicit edition
    # note such as ``□(葉本作「正」。)``.  If the model copies the quoted edition
    # reading, map that reading back to the entire untouched source construct.
    # This is limited to the explicit placeholder plus a quoted replacement;
    # ordinary textual variants are never silently substituted.
    placeholder_note = re.compile(
        r"□(?:\([^()\r\n]*作「(?P<ascii_rep>[^」\r\n]{1,4})」[^()\r\n]*\)"
        r"|（[^（）\r\n]*作「(?P<full_rep>[^」\r\n]{1,4})」[^（）\r\n]*）)"
    )
    replacements = {match.start(): match for match in placeholder_note.finditer(source_text)}
    if replacements:
        replacement_spans = [
            (match.start(), match.end()) for match in replacements.values()
        ]
        ignored_editorial_indices: set[int] = set()
        for note in re.finditer(
            r"\[\d+\]|〈[^〈〉\r\n]*〉|［[^［］\r\n]*］|\([^()\r\n]*\)|（[^（）\r\n]*）",
            source_text,
        ):
            if any(
                replacement_start <= note.start()
                and note.end() <= replacement_end
                for replacement_start, replacement_end in replacement_spans
            ):
                continue
            ignored_editorial_indices.update(range(note.start(), note.end()))
        alt_chars: list[str] = []
        alt_starts: list[int] = []
        alt_ends: list[int] = []
        source_index = 0
        while source_index < len(source_text):
            match = replacements.get(source_index)
            if match is not None:
                replacement = match.group("ascii_rep") or match.group("full_rep") or ""
                converted = to_simplified_generated_text(replacement)
                for char in converted:
                    alt_chars.append(char)
                    alt_starts.append(match.start())
                    alt_ends.append(match.end())
                source_index = match.end()
                continue
            char = source_text[source_index]
            if (
                char in dropped_quotes
                or source_index in ignored_editorial_indices
                or char == WIKISOURCE_MISSING_GLYPH_PLACEHOLDER
                or unicodedata.category(char) == "Cf"
            ):
                source_index += 1
                continue
            normalized_char = EVIDENCE_SOURCE_VARIANTS.get(char, char)
            converted = to_simplified_generated_text(normalized_char)
            for converted_char in converted:
                alt_chars.append(converted_char)
                alt_starts.append(source_index)
                alt_ends.append(source_index + 1)
            source_index += 1
        alternative_source = "".join(alt_chars)
        if (
            len(normalized_candidate) >= 2
            and alternative_source.count(normalized_candidate) == 1
        ):
            normalized_start = alternative_source.find(normalized_candidate)
            normalized_end = normalized_start + len(normalized_candidate) - 1
            source_start = alt_starts[normalized_start]
            source_end = alt_ends[normalized_end]
            exact_span = source_text[source_start:source_end]
            if len(exact_span) <= 80:
                return exact_span

    # Last, admit punctuation/whitespace-only transcription drift.  Models
    # sometimes turn a source comma into a full stop, omit a line break, or
    # drop quotation marks while otherwise copying every source character.
    # Build a source-side index map with only Unicode punctuation, whitespace,
    # Wikisource numeric footnotes and format controls removed; require the
    # normalized candidate to occur exactly once and always return the
    # untouched source span.  This is deliberately narrower than fuzzy text
    # matching: no lexical character may be inserted, removed or substituted.
    def punctuation_free_form(
        text: str, *, with_indices: bool
    ) -> tuple[str, list[int]]:
        chars: list[str] = []
        indices: list[int] = []
        ignored_source_indices: set[int] = set()
        if with_indices:
            for note in re.finditer(
                r"\[\d+\]|〈[^〈〉\r\n]*〉|［[^［］\r\n]*］|\([^()\r\n]*\)|（[^（）\r\n]*）",
                text,
            ):
                ignored_source_indices.update(range(note.start(), note.end()))
        for source_index, char in enumerate(text):
            if (
                source_index in ignored_source_indices
                or (with_indices and char == WIKISOURCE_MISSING_GLYPH_PLACEHOLDER)
                or char.isspace()
                or unicodedata.category(char) in {"Cf", "Cc"}
                or unicodedata.category(char).startswith("P")
            ):
                continue
            normalized_char = EVIDENCE_SOURCE_VARIANTS.get(char, char) if with_indices else char
            converted = to_simplified_generated_text(normalized_char)
            chars.extend(converted)
            if with_indices:
                indices.extend([source_index] * len(converted))
        return "".join(chars), indices

    punctuation_free_source, punctuation_free_indices = punctuation_free_form(
        source_text, with_indices=True
    )
    punctuation_free_candidate, _ = punctuation_free_form(
        candidate, with_indices=False
    )
    if (
        len(punctuation_free_candidate) >= 2
        and punctuation_free_source.count(punctuation_free_candidate) == 1
    ):
        normalized_start = punctuation_free_source.find(punctuation_free_candidate)
        normalized_end = normalized_start + len(punctuation_free_candidate) - 1
        source_start = punctuation_free_indices[normalized_start]
        source_end = punctuation_free_indices[normalized_end] + 1
        exact_span = source_text[source_start:source_end]
        if len(exact_span) <= 80:
            return bounded_exact_window(exact_span)

    # A model can occasionally concatenate two exact source anchors without
    # inserting an ellipsis.  This is the final fallback so stricter quote,
    # edition-note and punctuation mappings always get first refusal.  Admit
    # one omitted source span only when every candidate character is accounted
    # for by a unique ordered prefix/suffix, the omission crosses an explicit
    # source sentence boundary, and every valid split maps to the same short
    # untouched source span.
    if 4 <= len(candidate) <= 80:
        omission_spans: set[str] = set()
        for split_at in range(2, len(candidate) - 1):
            left_anchor = candidate[:split_at]
            right_anchor = candidate[split_at:]
            if max(len(left_anchor), len(right_anchor)) < 8:
                continue
            if source_text.count(left_anchor) != 1 or source_text.count(right_anchor) != 1:
                continue
            source_start = source_text.find(left_anchor)
            left_end = source_start + len(left_anchor)
            right_start = source_text.find(right_anchor, left_end)
            if right_start <= left_end:
                continue
            omitted = source_text[left_end:right_start]
            if not any(mark in omitted for mark in "。！？；\n\r"):
                continue
            exact_span = source_text[source_start : right_start + len(right_anchor)]
            if len(exact_span) <= 80:
                omission_spans.add(exact_span)
        if len(omission_spans) == 1:
            return next(iter(omission_spans))
    return None


def resolve_segmented_source_excerpts(
    source_text: str, candidate: str
) -> list[str] | None:
    """Resolve an explicit multi-ellipsis quote as separate exact snippets.

    Models use both the Chinese ellipsis and three-or-more ASCII full stops
    when marking omitted source text. Treat those markers identically while
    preserving the existing unique/exact/fail-closed evidence guarantees.
    """
    ellipsis_pattern = r"(?:……|\.{3,})"
    if re.search(ellipsis_pattern, candidate) is not None:
        parts = [part.strip() for part in re.split(ellipsis_pattern, candidate)]
    else:
        # A copied quotation can also omit a complete intervening source
        # sentence without inserting an ellipsis.  Recover only when the
        # candidate itself contains at least two complete sentence-sized
        # chunks; every chunk must remain an exact, unique source substring in
        # the original order.  This never admits paraphrase or fuzzy matching.
        parts = [
            part.strip()
            for part in re.findall(r"[^。！？；]+[。！？；]?", candidate)
            if part.strip()
        ]
    if len(parts) < 2 or any(len(part) < 2 for part in parts):
        return None
    resolved: list[str] = []
    previous_source_end = -1
    for part in parts:
        excerpt = resolve_source_excerpt(source_text, part)
        if excerpt is None or len(excerpt) > 80:
            return None
        # Ordering must be checked against the exact recovered source span,
        # not the model fragment.  A fragment may carry a closing quote that
        # appears only after omitted source text; the strict display resolver
        # deliberately drops that dangling mark before returning the untouched
        # source span.
        if source_text.count(excerpt) != 1:
            return None
        source_start = source_text.find(excerpt)
        if source_start < previous_source_end:
            return None
        previous_source_end = source_start + len(excerpt)
        if excerpt not in resolved:
            resolved.append(excerpt)
    return resolved if len(resolved) >= 2 else None


def add_evidence(
    evidence_map: dict[str, set[str]], entry: SourceEntry, excerpt: Any, supports: Iterable[str]
) -> str:
    text = clean_excerpt(excerpt)
    resolved = resolve_source_excerpt(entry.text, text)
    if resolved is None:
        raise AnnotationError("evidence excerpt is not an exact source substring")
    support_set = evidence_map.setdefault(resolved, set())
    for support in supports:
        if isinstance(support, str) and support.startswith("interpretation_risks."):
            support = "auto_safety_screen." + support
        if not isinstance(support, str) or not ALLOWED_SUPPORT.fullmatch(support):
            raise AnnotationError("evidence contains an invalid support path")
        support_set.add(support)
    return resolved


def source_context_excerpt(source_text: str, start: int, end: int) -> str:
    """Return an exact, sentence-bounded source span of at most 80 characters."""
    left_bound = max(0, start - 38)
    right_bound = min(len(source_text), end + 38)
    left = start
    while left > left_bound and source_text[left - 1] not in "。；！？\n\r":
        left -= 1
    right = end
    while right < right_bound and source_text[right] not in "。；！？\n\r":
        right += 1
    if right < len(source_text) and source_text[right] in "。；！？":
        right += 1
    if right - left > 80:
        left = max(left, start - 30)
        right = min(right, left + 80)
        if right < end:
            right = end
            left = max(0, right - 80)
    excerpt = source_text[left:right].strip()
    if len(excerpt) < 2:
        excerpt = source_text[max(0, start - 1) : min(len(source_text), end + 1)]
    return excerpt[:80]


def add_explicit_safety_backstops(
    entry: SourceEntry,
    evidence_map: dict[str, set[str]],
    flags: list[str],
) -> int:
    """Add only explicit, deterministic safety labels with exact evidence."""
    additions = 0

    def apply_match(match: re.Match[str], labels: Iterable[str]) -> None:
        nonlocal additions
        excerpt = source_context_excerpt(entry.text, match.start(), match.end())
        label_list = list(labels)
        for label in label_list:
            if label not in flags:
                flags.append(label)
                additions += 1
        if "none_identified" in flags and len(flags) > 1:
            flags.remove("none_identified")
        add_evidence(
            evidence_map,
            entry,
            excerpt,
            [f"auto_safety_screen.flags.{label}" for label in label_list],
        )

    for rule in A_SAFETY_PHRASE_RULES:
        for match in rule.pattern.finditer(entry.text):
            apply_match(match, rule.required_flags)
    for pattern, labels in EXTRA_EXPLICIT_SAFETY_RULES:
        for match in pattern.finditer(entry.text):
            apply_match(match, labels)
    fatal_self_harm = SELF_HARM_DEATH_PATTERN.search(entry.text)
    if fatal_self_harm is not None:
        apply_match(fatal_self_harm, ("self_harm_or_suicide", "death"))
    return additions


def retain_only_final_support_targets(
    evidence_map: dict[str, set[str]], valid_targets: set[str]
) -> int:
    """Remove supports for labels pruned from the final annotation object."""
    removed = 0
    empty_excerpts: list[str] = []
    for excerpt, supports in evidence_map.items():
        kept = supports & valid_targets
        removed += len(supports) - len(kept)
        supports.clear()
        supports.update(kept)
        if not supports:
            empty_excerpts.append(excerpt)
    for excerpt in empty_excerpts:
        evidence_map.pop(excerpt, None)
    return removed


def compact_evidence_map(
    evidence_map: dict[str, set[str]],
    *,
    required_supports: set[str],
    preferred_excerpts: Sequence[str],
    source_text: str,
    max_items: int = 24,
) -> dict[str, set[str]]:
    """Select a deterministic evidence subset without losing required claims.

    Long stories can yield many entity, beat, and safety quotes.  The A1
    contract caps evidence at 24, so first retain a greedy exact set cover for
    every required claim, then keep entity/beat quotes and remaining source
    quotes in source order while capacity remains.
    """
    if len(evidence_map) <= max_items:
        return evidence_map
    source_order = {
        excerpt: source_text.find(excerpt) for excerpt in evidence_map
    }
    selected: list[str] = []
    uncovered = set(required_supports)
    remaining = set(evidence_map)
    while uncovered:
        best = max(
            remaining,
            key=lambda excerpt: (
                len(evidence_map[excerpt] & uncovered),
                -len(excerpt),
                -source_order[excerpt],
            ),
            default=None,
        )
        if best is None or not (evidence_map[best] & uncovered):
            raise AnnotationError(
                "cannot compact evidence without losing required support links"
            )
        selected.append(best)
        remaining.remove(best)
        uncovered -= evidence_map[best]
        if len(selected) > max_items:
            raise AnnotationError("required support links need more than 24 evidence items")

    ordered_preferences = list(dict.fromkeys(preferred_excerpts))
    for excerpt in ordered_preferences:
        if len(selected) >= max_items:
            break
        if excerpt in remaining:
            selected.append(excerpt)
            remaining.remove(excerpt)
    for excerpt in sorted(remaining, key=lambda item: (source_order[item], len(item), item)):
        if len(selected) >= max_items:
            break
        selected.append(excerpt)
    return {excerpt: evidence_map[excerpt] for excerpt in selected}


def enforce_safety_cross_labels(
    evidence_map: dict[str, set[str]], flags: list[str]
) -> int:
    """Apply content-warning implications without inventing new evidence."""
    additions = 0
    implications = {
        "sexual_violence": ("sexual_content", "coercion_or_abuse"),
    }
    for source_label, implied_labels in implications.items():
        source_support = f"auto_safety_screen.flags.{source_label}"
        supporting_sets = [
            supports for supports in evidence_map.values() if source_support in supports
        ]
        if source_label not in flags or not supporting_sets:
            continue
        for implied_label in implied_labels:
            if implied_label not in flags:
                flags.append(implied_label)
                additions += 1
            implied_support = f"auto_safety_screen.flags.{implied_label}"
            for supports in supporting_sets:
                supports.add(implied_support)
    if "none_identified" in flags and len(flags) > 1:
        flags.remove("none_identified")
        additions += 1
    return additions


def require_broad_safety_signal_coverage(source_text: str, flags: list[str]) -> None:
    """Reject a model result that leaves broad warning language wholly uncovered."""
    selected = set(flags)
    for rule in B_SAFETY_SIGNAL_RULES:
        match = signal_match(rule, source_text)
        if match is not None and not (selected & rule.compatible_flags):
            compatible = ",".join(sorted(rule.compatible_flags))
            raise AnnotationError(
                f"uncovered safety signal {rule.name} ({match.group(0)!r}); "
                f"add an evidence-backed compatible flag: {compatible}"
            )


BROAD_SAFETY_FALLBACK_FLAGS = {
    "death_language": "death",
    "violence_or_injury_language": "violence",
    "captivity_or_coercion_language": "captivity",
    "sexual_language": "sexual_content",
    "illness_language": "illness",
}


def add_broad_safety_backstops(
    entry: SourceEntry,
    evidence_map: dict[str, set[str]],
    flags: list[str],
) -> int:
    """On the final retry, add one conservative umbrella flag per uncovered signal."""
    additions = 0
    selected = set(flags)
    for rule in B_SAFETY_SIGNAL_RULES:
        match = signal_match(rule, entry.text)
        if match is None or selected & rule.compatible_flags:
            continue
        label = BROAD_SAFETY_FALLBACK_FLAGS[rule.name]
        if label not in flags:
            flags.append(label)
            selected.add(label)
            additions += 1
        excerpt = source_context_excerpt(entry.text, match.start(), match.end())
        add_evidence(
            evidence_map,
            entry,
            excerpt,
            [f"auto_safety_screen.flags.{label}"],
        )
    if "none_identified" in flags and len(flags) > 1:
        flags.remove("none_identified")
        additions += 1
    return additions


def normalise_annotation(
    raw: dict[str, Any],
    entry: SourceEntry,
    *,
    schema_validator: Draft202012Validator,
    catalog_version: str,
    model: str,
    allow_local_repair: bool = False,
) -> dict[str, Any]:
    if raw.get("entryId") != entry.entry_id:
        raise AnnotationError("response entryId does not match the source entry")
    summary, summary_repaired = clean_summary(
        raw.get("modernRetrievalSummary"), allow_local_repair=allow_local_repair
    )
    sufficiency = raw.get("narrativeSufficiency")
    if sufficiency not in {"sufficient", "insufficient", "unknown"}:
        raise AnnotationError("invalid narrativeSufficiency")
    reason_raw = raw.get("narrativeSufficiencyReason")
    reason = None if reason_raw in {None, ""} else clean_text(reason_raw, "narrativeSufficiencyReason", 2, 160)
    if sufficiency != "sufficient" and reason is None:
        raise AnnotationError("insufficient/unknown records require a reason")

    life_context, label_drops = lenient_enum_list(
        raw.get("lifeContext"), LIFE_CONTEXTS, "lifeContext", 3
    )
    arc_raw = raw.get("narrativeArc")
    if not isinstance(arc_raw, dict):
        raise AnnotationError("narrativeArc must be an object")
    trigger_raw = arc_raw.get("trigger")
    if is_explicitly_unknown_trigger(trigger_raw):
        trigger = "未知"
    elif sufficiency != "sufficient" and trigger_raw in {None, ""}:
        trigger = "未知"
    else:
        trigger = clean_text(trigger_raw, "narrativeArc.trigger", 2, 160)
    conflicts, dropped = lenient_enum_list(
        arc_raw.get("conflictTypes"), CONFLICT_TYPES, "conflictTypes", 2
    )
    label_drops += dropped
    agencies, dropped = lenient_enum_list(
        arc_raw.get("agencyModes"), AGENCY_MODES, "agencyModes", 2
    )
    label_drops += dropped
    if not agencies:
        agencies = ["unknown"]
    ending = arc_raw.get("endingMode")
    if ending not in ENDING_MODES:
        ending = "unknown"
        label_drops += 1

    safety_raw = raw.get("autoSafetyScreen")
    if not isinstance(safety_raw, dict):
        raise AnnotationError("autoSafetyScreen must be an object")
    safety_status = safety_raw.get("status")
    if safety_status not in {"auto_screened", "unknown"}:
        safety_status = "unknown"
        label_drops += 1
    flags, dropped = lenient_enum_list(
        safety_raw.get("flags"), CONTENT_WARNINGS, "flags", 15
    )
    label_drops += dropped
    risks, dropped = lenient_enum_list(
        safety_raw.get("interpretationRisks"),
        INTERPRETATION_RISKS,
        "interpretationRisks",
        9,
    )
    label_drops += dropped
    if "none_identified" in flags and len(flags) != 1:
        flags.remove("none_identified")
        label_drops += 1
    if "none_identified" in risks and len(risks) != 1:
        risks.remove("none_identified")
        label_drops += 1
    uncertainties = [
        clean_text(value, "uncertainty", 1, 160)
        for value in require_list(safety_raw.get("uncertainties"), "uncertainties")[:6]
    ]

    evidence_map: dict[str, set[str]] = {}
    for item in require_list(raw.get("evidence"), "evidence"):
        if not isinstance(item, dict):
            raise AnnotationError("evidence item must be an object")
        support_value = item.get("supports")
        supports = [support_value] if isinstance(support_value, str) else require_list(
            support_value, "supports"
        )
        excerpt_value = item.get("excerpt")
        try:
            add_evidence(evidence_map, entry, excerpt_value, supports)
        except AnnotationError as exc:
            # A model sometimes repeats its modern paraphrase in the evidence
            # array.  Drop only non-source excerpts; required-support checks
            # below still fail closed unless exact source evidence remains.
            segmented = (
                resolve_segmented_source_excerpts(entry.text, excerpt_value)
                if isinstance(excerpt_value, str)
                and "not an exact source substring" in str(exc)
                else None
            )
            if segmented:
                for exact_excerpt in segmented:
                    add_evidence(
                        evidence_map,
                        entry,
                        exact_excerpt,
                        supports,
                    )
                continue
            droppable_final_retry = allow_local_repair and "length is below 2" in str(exc)
            if "not an exact source substring" not in str(exc) and not droppable_final_retry:
                raise

    key_entities: list[dict[str, Any]] = []
    entity_evidence: list[str] = []
    for item in require_list(raw.get("keyEntities"), "keyEntities")[:8]:
        if not isinstance(item, dict):
            raise AnnotationError("keyEntities item must be an object")
        try:
            excerpt = clean_excerpt(item.get("evidenceExcerpt"), "entity evidence")
        except AnnotationError as exc:
            if allow_local_repair and "length is below 2" in str(exc):
                continue
            raise
        try:
            excerpt = add_evidence(
                evidence_map,
                entry,
                excerpt,
                ["retrieval_profile.modern_retrieval_summary"],
            )
        except AnnotationError as exc:
            if "not an exact source substring" in str(exc):
                continue
            raise
        entity_evidence.append(excerpt)
        key_entities.append(
            {
                "name": clean_text(item.get("name"), "entity name", 1, 40),
                "role": clean_text(item.get("role"), "entity role", 1, 40),
                "_excerpt": excerpt,
            }
        )

    plot_beats: list[dict[str, Any]] = []
    beat_evidence: list[str] = []
    for item in require_list(raw.get("plotBeats"), "plotBeats")[:6]:
        if not isinstance(item, dict) or item.get("type") not in {"trigger", "action_or_turn", "outcome"}:
            raise AnnotationError("invalid plot beat")
        try:
            excerpt = clean_excerpt(item.get("evidenceExcerpt"), "plot beat evidence")
        except AnnotationError as exc:
            if allow_local_repair and "length is below 2" in str(exc):
                continue
            raise
        beat_supports = ["retrieval_profile.modern_retrieval_summary"]
        if item["type"] == "trigger":
            beat_supports.append("narrative_arc.trigger")
        if item["type"] == "outcome" and ending != "unknown":
            beat_supports.append(f"narrative_arc.ending_mode.{ending}")
        try:
            excerpt = add_evidence(evidence_map, entry, excerpt, beat_supports)
        except AnnotationError as exc:
            if "not an exact source substring" in str(exc):
                continue
            raise
        beat_evidence.append(excerpt)
        plot_beats.append(
            {
                "type": item["type"],
                "text": clean_text(item.get("text"), "plot beat text", 2, 120),
                "_excerpt": excerpt,
            }
        )

    motifs: list[str] = []
    for value in require_list(raw.get("motifTerms"), "motifTerms"):
        motif = clean_text(value, "motif term", 1, 20)
        if motif not in motifs:
            motifs.append(motif)
        if len(motifs) >= 8:
            break

    safety_backstop_additions = add_explicit_safety_backstops(
        entry, evidence_map, flags
    )
    safety_backstop_additions += enforce_safety_cross_labels(evidence_map, flags)
    actual_supports = {support for supports in evidence_map.values() for support in supports}
    pruned_claims = label_drops + safety_backstop_additions + int(summary_repaired)
    original_count = len(life_context)
    life_context = [
        value for value in life_context if f"life_context.{value}" in actual_supports
    ]
    pruned_claims += original_count - len(life_context)
    original_count = len(conflicts)
    conflicts = [
        value
        for value in conflicts
        if f"narrative_arc.conflict_types.{value}" in actual_supports
    ]
    pruned_claims += original_count - len(conflicts)
    original_count = len(agencies)
    agencies = [
        value
        for value in agencies
        if value == "unknown" or f"narrative_arc.agency_modes.{value}" in actual_supports
    ]
    pruned_claims += original_count - len(agencies)
    if not agencies:
        agencies = ["unknown"]
    if ending != "unknown" and f"narrative_arc.ending_mode.{ending}" not in actual_supports:
        ending = "unknown"
        pruned_claims += 1
    original_count = len(flags)
    flags = [
        value
        for value in flags
        if value == "none_identified"
        or f"auto_safety_screen.flags.{value}" in actual_supports
    ]
    pruned_claims += original_count - len(flags)
    original_count = len(risks)
    risks = [
        value
        for value in risks
        if value == "none_identified"
        or f"auto_safety_screen.interpretation_risks.{value}" in actual_supports
    ]
    pruned_claims += original_count - len(risks)
    if not flags or not risks:
        safety_status = "unknown"
        note = "模型风险标签缺少可定位的逐字证据，已由自动校验降为未知。"
        if note not in uncertainties:
            if len(uncertainties) < 6:
                uncertainties.append(note)
            else:
                uncertainties[-1] = note
    if allow_local_repair:
        broad_additions = add_broad_safety_backstops(entry, evidence_map, flags)
        if broad_additions:
            pruned_claims += broad_additions
            safety_status = "unknown"
            note = "模型未覆盖广义安全信号，已由本地规则保守补标，仍待人工审核。"
            if note not in uncertainties:
                if len(uncertainties) < 6:
                    uncertainties.append(note)
                else:
                    uncertainties[-1] = note
        current_supports = {
            support for supports in evidence_map.values() for support in supports
        }
        if (
            sufficiency != "sufficient"
            and "retrieval_profile.modern_retrieval_summary" not in current_supports
        ):
            first_content = re.search(r"\S", entry.text)
            if first_content is not None:
                start = first_content.start()
                excerpt = source_context_excerpt(
                    entry.text, start, min(len(entry.text), start + 1)
                )
                if len(excerpt) >= 2:
                    add_evidence(
                        evidence_map,
                        entry,
                        excerpt,
                        ["retrieval_profile.modern_retrieval_summary"],
                    )
                    pruned_claims += 1
    require_broad_safety_signal_coverage(entry.text, flags)

    required_supports = {"retrieval_profile.modern_retrieval_summary"}
    trigger_requires_support = trigger.strip().lower() not in {
        "unknown",
        "未知",
        "不详",
        "不明",
    }
    if trigger_requires_support:
        required_supports.add("narrative_arc.trigger")
    required_supports.update(f"life_context.{value}" for value in life_context)
    required_supports.update(f"narrative_arc.conflict_types.{value}" for value in conflicts)
    required_supports.update(
        f"narrative_arc.agency_modes.{value}" for value in agencies if value != "unknown"
    )
    if ending != "unknown":
        required_supports.add(f"narrative_arc.ending_mode.{ending}")
    required_supports.update(
        f"auto_safety_screen.flags.{value}" for value in flags if value != "none_identified"
    )
    required_supports.update(
        f"auto_safety_screen.interpretation_risks.{value}"
        for value in risks
        if value != "none_identified"
    )
    pruned_claims += retain_only_final_support_targets(
        evidence_map, required_supports
    )
    actual_supports = {
        support for supports in evidence_map.values() for support in supports
    }
    missing_supports = required_supports - actual_supports
    if missing_supports:
        raise AnnotationError(
            "missing evidence links: " + ",".join(sorted(missing_supports))
        )
    if not evidence_map:
        raise AnnotationError("annotation has no exact evidence")

    before_compaction = len(evidence_map)
    evidence_map = compact_evidence_map(
        evidence_map,
        required_supports=required_supports,
        preferred_excerpts=[*entity_evidence, *beat_evidence],
        source_text=entry.text,
        max_items=24,
    )
    if len(evidence_map) < before_compaction:
        pruned_claims += before_compaction - len(evidence_map)
        key_entities = [
            item for item in key_entities if item["_excerpt"] in evidence_map
        ]
        plot_beats = [
            item for item in plot_beats if item["_excerpt"] in evidence_map
        ]

    evidence: list[dict[str, Any]] = []
    id_by_excerpt: dict[str, str] = {}
    for index, (excerpt, supports) in enumerate(evidence_map.items(), start=1):
        evidence_id = f"ev{index:02d}"
        start = entry.text.find(excerpt)
        id_by_excerpt[excerpt] = evidence_id
        evidence.append(
            {
                "id": evidence_id,
                "locator": entry.source_locator,
                "excerpt": excerpt,
                "start_char": start,
                "end_char": start + len(excerpt),
                "supports": sorted(supports),
            }
        )
    for item in key_entities:
        item["evidence_ids"] = [id_by_excerpt[item.pop("_excerpt")]]
    for item in plot_beats:
        item["evidence_ids"] = [id_by_excerpt[item.pop("_excerpt")]]
    summary_ids = [
        item["id"]
        for item in evidence
        if "retrieval_profile.modern_retrieval_summary" in item["supports"]
    ][:12]
    if not summary_ids:
        raise AnnotationError("modern summary has no evidence")

    generated_at = utc_now()
    confidence = raw.get("overallConfidence")
    if confidence not in {"low", "medium", "high"}:
        raise AnnotationError("invalid overallConfidence")
    if pruned_claims:
        confidence = "low"
    annotation = {
        "annotation_record": {
            "annotation_version": ANNOTATION_VERSION,
            "annotation_level": "A1",
            "unit": {
                "corpus_level": "C1",
                "unit_type": "source_segment",
                "unit_id": entry.entry_id,
                "source_text_sha256": entry.extracted_text_sha256,
            },
            "source_profile": {
                "title": entry.title,
                "work_id": entry.source_work_id,
                "work": entry.source_work_title,
                "period": entry.source_work_period,
                "juan_or_section": entry.volume,
                "locator": entry.source_locator,
                "source_url_or_snapshot": entry.source_url,
                "source_version": catalog_version,
                "source_text": entry.text,
                "source_text_sha256": entry.extracted_text_sha256,
                "rights_status": "CC-BY-SA-4.0_source_segmented_unreviewed",
            },
            "retrieval_profile": {
                "modern_retrieval_summary": summary,
                "summary_kind": "model_generated_retrieval_paraphrase",
                "summary_language": "zh-Hans",
                "narrative_sufficiency": sufficiency,
                "narrative_sufficiency_reason": reason,
                "key_entities": key_entities,
                "plot_beats": plot_beats,
                "motif_terms": motifs,
                "summary_evidence_ids": summary_ids,
            },
            "life_context": life_context,
            "narrative_arc": {
                "trigger": trigger,
                "conflict_types": conflicts,
                "agency_modes": agencies,
                "ending_mode": ending,
            },
            "auto_safety_screen": {
                "status": safety_status,
                "flags": flags,
                "interpretation_risks": risks,
                "uncertainties": uncertainties,
                "model_preannotation": True,
            },
            "reflection_profile": None,
            "evidence": evidence,
            "annotation_meta": {
                "product_annotation_status": "automatically_validated",
                "research_review_status": "not_reviewed",
                "research_ready": False,
                "generated_at": generated_at,
                "generator": {
                    "type": "llm",
                    "provider": "deepseek",
                    "model": model,
                    "model_version": model,
                    "prompt_version": PROMPT_VERSION,
                    "temperature": 0,
                },
                "schema_version": SCHEMA_VERSION,
                "validation": {
                    "schema_valid": True,
                    "source_hash_match": True,
                    "evidence_links_valid": True,
                    "evidence_exact_substrings": True,
                    "validated_at": generated_at,
                },
                "human_review": {
                    "reviewed": False,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "decision": None,
                    "notes": [],
                },
                "overall_confidence": confidence,
                "model_preannotation": True,
            },
        }
    }
    errors = sorted(schema_validator.iter_errors(annotation), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise AnnotationError(f"schema validation failed at {list(first.path)}: {first.message}")
    return annotation


def parse_batch(
    response: dict[str, Any],
    entries: Sequence[SourceEntry],
    *,
    schema_validator: Draft202012Validator,
    catalog_version: str,
    model: str,
    allow_local_repair: bool = False,
) -> dict[str, dict[str, Any]]:
    raw_annotations = response.get("annotations")
    if not isinstance(raw_annotations, list):
        # Some JSON-mode responses mirror the user payload envelope as
        # ``records`` even though each child follows the annotation contract.
        raw_annotations = response.get("records")
    if not isinstance(raw_annotations, list):
        raise AnnotationError("response must contain an annotations array")
    expected = {entry.entry_id for entry in entries}
    received = [item.get("entryId") for item in raw_annotations if isinstance(item, dict)]
    if len(received) != len(raw_annotations) or set(received) != expected or len(received) != len(set(received)):
        raise AnnotationError("response IDs do not exactly match the requested batch")
    by_id = {item["entryId"]: item for item in raw_annotations}
    return {
        entry.entry_id: normalise_annotation(
            by_id[entry.entry_id],
            entry,
            schema_validator=schema_validator,
            catalog_version=catalog_version,
            model=model,
            allow_local_repair=allow_local_repair,
        )
        for entry in entries
    }


def mark_leased(conn: sqlite3.Connection, entries: Sequence[SourceEntry]) -> None:
    now = utc_now()
    with conn:
        conn.executemany(
            """
            UPDATE annotation_jobs
            SET status='leased', attempts=attempts+1, updated_at=?
            WHERE entry_id=?
            """,
            [(now, entry.entry_id) for entry in entries],
        )


def mark_valid(
    conn: sqlite3.Connection,
    entry_id: str,
    annotation: dict[str, Any],
    provider_meta: dict[str, Any],
) -> None:
    usage = provider_meta.get("usage") or {}
    with conn:
        conn.execute(
            """
            UPDATE annotation_jobs
            SET status='valid', annotation_json=?, error_kind=NULL,
                error_message=NULL, last_raw_response_json=NULL,
                provider_response_id=?, prompt_tokens=?,
                completion_tokens=?, total_tokens=?, model=?, prompt_version=?,
                updated_at=?
            WHERE entry_id=?
            """,
            (
                json.dumps(annotation, ensure_ascii=False, separators=(",", ":")),
                provider_meta.get("response_id") or None,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
                provider_meta.get("model"),
                PROMPT_VERSION,
                utc_now(),
                entry_id,
            ),
        )


def mark_failed(
    conn: sqlite3.Connection,
    entries: Sequence[SourceEntry],
    exc: BaseException,
    *,
    retryable: bool,
    raw_response: dict[str, Any] | None = None,
    model: str | None = None,
) -> None:
    status = "retryable_failed" if retryable else "quarantined"
    now = utc_now()
    with conn:
        conn.executemany(
            """
            UPDATE annotation_jobs
            SET status=?, error_kind=?, error_message=?,
                last_raw_response_json=?, model=COALESCE(?, model),
                prompt_version=?, updated_at=?
            WHERE entry_id=?
            """,
            [
                (
                    status,
                    type(exc).__name__,
                    compact_error(exc),
                    json.dumps(raw_response, ensure_ascii=False, separators=(",", ":"))
                    if raw_response is not None
                    else None,
                    model,
                    PROMPT_VERSION,
                    now,
                    entry.entry_id,
                )
                for entry in entries
            ],
        )


def revalidate_stored_responses(
    conn: sqlite3.Connection,
    selected: Sequence[SourceEntry],
    *,
    schema_validator: Draft202012Validator,
    catalog_version: str,
    model: str,
) -> int:
    """Reuse rejected raw output only after the current local gates accept it."""
    by_id = {entry.entry_id: entry for entry in selected}
    recovered = 0
    rows = conn.execute(
        """
        SELECT entry_id, last_raw_response_json, model, prompt_version
        FROM annotation_jobs
        WHERE status='quarantined' AND last_raw_response_json IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        entry = by_id.get(row["entry_id"])
        if (
            entry is None
            or row["model"] != model
            or row["prompt_version"] != PROMPT_VERSION
        ):
            continue
        try:
            response = json.loads(row["last_raw_response_json"])
            raw_items = response.get("annotations")
            if not isinstance(raw_items, list):
                raw_items = response.get("records")
            if not isinstance(raw_items, list):
                continue
            raw = next(
                item
                for item in raw_items
                if isinstance(item, dict) and item.get("entryId") == entry.entry_id
            )
            annotation = normalise_annotation(
                raw,
                entry,
                schema_validator=schema_validator,
                catalog_version=catalog_version,
                model=model,
                allow_local_repair=True,
            )
        except (StopIteration, TypeError, ValueError, json.JSONDecodeError, AnnotationError):
            continue
        mark_valid(
            conn,
            entry.entry_id,
            annotation,
            {"response_id": "", "usage": {}, "model": model},
        )
        recovered += 1
    return recovered


def process_batch(
    conn: sqlite3.Connection,
    client: DeepSeekBatchClient,
    entries: Sequence[SourceEntry],
    *,
    schema_validator: Draft202012Validator,
    catalog_version: str,
    retries: int,
    semantic_retries: int = 2,
    correction: str | None = None,
) -> None:
    mark_leased(conn, entries)
    response: dict[str, Any] | None = None
    try:
        response, provider_meta = client.complete(
            entries, retries, correction=correction
        )
        raw_annotations = response.get("annotations")
        if not isinstance(raw_annotations, list):
            raw_annotations = response.get("records")
        if not isinstance(raw_annotations, list):
            raise AnnotationError("response must contain an annotations array")
        expected = {entry.entry_id for entry in entries}
        received = [
            item.get("entryId") for item in raw_annotations if isinstance(item, dict)
        ]
        if (
            len(received) != len(raw_annotations)
            or set(received) != expected
            or len(received) != len(set(received))
        ):
            raise AnnotationError("response IDs do not exactly match the requested batch")
        raw_by_id = {item["entryId"]: item for item in raw_annotations}
        failed_items: list[tuple[SourceEntry, AnnotationError]] = []
        for entry in entries:
            try:
                annotation = normalise_annotation(
                    raw_by_id[entry.entry_id],
                    entry,
                    schema_validator=schema_validator,
                    catalog_version=catalog_version,
                    model=client.model,
                    allow_local_repair=semantic_retries == 0,
                )
            except AnnotationError as exc:
                failed_items.append((entry, exc))
            else:
                mark_valid(conn, entry.entry_id, annotation, provider_meta)
        for entry, item_error in failed_items:
            if semantic_retries > 0:
                process_batch(
                    conn,
                    client,
                    [entry],
                    schema_validator=schema_validator,
                    catalog_version=catalog_version,
                    retries=retries,
                    # The failed batch response already consumed one semantic
                    # attempt for this row.  Do not grant the singleton a fresh
                    # full retry allowance or one bad batch can multiply calls.
                    semantic_retries=semantic_retries - 1,
                    correction=compact_error(item_error),
                )
            else:
                mark_failed(
                    conn,
                    [entry],
                    item_error,
                    retryable=False,
                    raw_response=response,
                    model=client.model,
                )
    except FatalProviderError:
        mark_failed(
            conn,
            entries,
            FatalProviderError("fatal provider configuration"),
            retryable=True,
            model=client.model,
        )
        raise
    except RetryableProviderError as exc:
        mark_failed(conn, entries, exc, retryable=True, model=client.model)
    except AnnotationError as exc:
        if len(entries) > 1:
            for entry in entries:
                process_batch(
                    conn,
                    client,
                    [entry],
                    schema_validator=schema_validator,
                    catalog_version=catalog_version,
                    retries=retries,
                    # The invalid envelope was still one model attempt for
                    # every requested row, so carry only the remaining budget.
                    semantic_retries=max(0, semantic_retries - 1),
                    correction=compact_error(exc),
                )
        elif semantic_retries > 0:
            process_batch(
                conn,
                client,
                entries,
                schema_validator=schema_validator,
                catalog_version=catalog_version,
                retries=retries,
                semantic_retries=semantic_retries - 1,
                correction=compact_error(exc),
            )
        else:
            mark_failed(
                conn,
                entries,
                exc,
                retryable=False,
                raw_response=response,
                model=client.model,
            )


def batch_status_counts(
    conn: sqlite3.Connection, entries: Sequence[SourceEntry]
) -> dict[str, int]:
    if not entries:
        return {}
    placeholders = ",".join("?" for _ in entries)
    return {
        row["status"]: int(row["count"])
        for row in conn.execute(
            f"""
            SELECT status, COUNT(*) AS count
            FROM annotation_jobs
            WHERE entry_id IN ({placeholders})
            GROUP BY status ORDER BY status
            """,
            [entry.entry_id for entry in entries],
        )
    }


def annotation_worker(
    worker_id: int,
    work_queue: Queue[tuple[int, list[SourceEntry]]],
    stop_event: Event,
    progress: ProgressTracker,
    *,
    derived_db: Path,
    schema: dict[str, Any],
    catalog_version: str,
    expected_model: str,
    worker_rpm: float,
    startup_delay: float,
    timeout: float,
    max_tokens: int,
    retries: int,
    call_budget: ProviderCallBudget,
) -> WorkerResult:
    """Drain batches using resources created and closed inside this thread."""
    client: DeepSeekBatchClient | None = None
    conn: sqlite3.Connection | None = None
    batches_processed = 0
    fatal_reason: str | None = None
    unexpected_reason: str | None = None
    try:
        client = DeepSeekBatchClient(
            rpm=worker_rpm,
            timeout=timeout,
            max_tokens=max_tokens,
            call_budget=call_budget,
        )
        if not client.available:
            raise FatalProviderError("live DeepSeek annotation is not enabled/configured")
        if client.model != expected_model:
            raise FatalProviderError("provider model changed while workers were starting")
        conn = derived_connection(derived_db)
        schema_validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        # Keep the configured total RPM while avoiding a four-request burst at
        # process start.  Later requests remain paced by each thread client.
        if startup_delay > 0:
            stop_event.wait(startup_delay)

        while not stop_event.is_set():
            try:
                batch_index, batch = work_queue.get_nowait()
            except Empty:
                break
            if stop_event.is_set():
                # The ledger rows have not been leased, so a resumed run can
                # deterministically pick them up again.
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
                    schema_validator=schema_validator,
                    catalog_version=catalog_version,
                    retries=retries,
                )
            except FatalProviderError as exc:
                fatal_reason = compact_error(exc)
                outcome = "fatal_configuration"
                stop_after_batch = True
                stop_event.set()
            except ProviderCallBudgetExceeded as exc:
                outcome = "provider_call_budget_exhausted"
                stop_after_batch = True
                stop_event.set()
                mark_failed(
                    conn,
                    batch,
                    exc,
                    retryable=True,
                    model=client.model,
                )
            except Exception as exc:  # preserve the ledger before stopping
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
                        model=client.model,
                    )
                except sqlite3.Error:
                    # A leased row is itself recoverable: pending_entries
                    # explicitly admits it on --resume.
                    pass
            finally:
                batches_processed += 1
                calls_this_batch = client.call_count - calls_before
                try:
                    local_counts = batch_status_counts(conn, batch)
                    global_counts = status_counts(conn)
                except sqlite3.Error:
                    local_counts, global_counts = {}, {}
                progress.record_batch(
                    worker_id=worker_id,
                    batch_index=batch_index,
                    records_in_batch=len(batch),
                    provider_calls=calls_this_batch,
                    batch_status_counts=local_counts,
                    global_status_counts=global_counts,
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


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM annotation_jobs GROUP BY status ORDER BY status"
        )
    }


def selected_status_counts(
    conn: sqlite3.Connection, selected: Sequence[SourceEntry]
) -> dict[str, int]:
    selected_ids = {entry.entry_id for entry in selected}
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT entry_id, status FROM annotation_jobs"):
        if row["entry_id"] in selected_ids:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
    return dict(sorted(counts.items()))


def export_outputs(
    conn: sqlite3.Connection,
    *,
    ndjson_path: Path,
    manifest_path: Path,
    source_db: Path,
    schema_path: Path,
    selected_count: int,
    model: str,
    catalog_version: str,
    provider_calls: int,
    max_provider_calls: int,
    workers: int,
) -> None:
    ndjson_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ndjson_path.with_suffix(ndjson_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in conn.execute(
            """
            SELECT annotation_json FROM annotation_jobs
            WHERE status='valid' ORDER BY source_work_id, entry_id
            """
        ):
            handle.write(row["annotation_json"])
            handle.write("\n")
    tmp.replace(ndjson_path)
    counts = status_counts(conn)
    succeeded = counts.get("valid", 0)
    quarantined = counts.get("quarantined", 0)
    retryable_failed = counts.get("retryable_failed", 0)
    pending = counts.get("queued", 0) + counts.get("leased", 0) + retryable_failed
    record_attempts = int(
        conn.execute("SELECT COALESCE(SUM(attempts), 0) FROM annotation_jobs").fetchone()[0]
    )
    manifest = {
        "annotation_version": ANNOTATION_VERSION,
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "catalog_version": catalog_version,
        "source_database": display_path(source_db),
        "source_database_sha256": sha256_file(source_db),
        "schema_sha256": sha256_file(schema_path),
        "prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "model": model,
        "model_preannotation": True,
        "product_use_gate": "automatically_validated",
        "research_review_status": "not_reviewed",
        "research_ready": False,
        "selected_records": selected_count,
        "ledger_records": sum(counts.values()),
        "status_counts": counts,
        "status_summary": {
            "succeeded": succeeded,
            "failed": retryable_failed + quarantined,
            "quarantined": quarantined,
            "pending": pending,
        },
        "current_run_provider_calls": provider_calls,
        "current_run_max_provider_calls": max_provider_calls,
        "current_run_workers": workers,
        "ledger_record_attempts": record_attempts,
        "valid_ndjson": display_path(ndjson_path),
        "valid_ndjson_sha256": sha256_file(ndjson_path),
        "updated_at": utc_now(),
    }
    manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    manifest_tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_tmp.replace(manifest_path)


def log_event(conn: sqlite3.Connection, event_type: str, detail: dict[str, Any]) -> None:
    with conn:
        conn.execute(
            "INSERT INTO run_events(occurred_at,event_type,detail_json) VALUES(?,?,?)",
            (utc_now(), event_type, json.dumps(detail, ensure_ascii=False, separators=(",", ":"))),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create validated, resumable A1 annotations without modifying the C1 source DB."
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-mode", choices=["stable", "stratified"], default="stable")
    parser.add_argument("--entry-id-file", type=Path)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--batch-char-limit", type=int, default=4000)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Independent annotation workers; total --rpm is divided across "
            f"them (1..{MAX_WORKERS})."
        ),
    )
    parser.add_argument("--rpm", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=0,
        help=(
            "Process-wide hard request ceiling. Zero selects four times the "
            "planned pending-batch count."
        ),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-quarantined", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Perform real DeepSeek API calls.")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly print the deterministic plan without calling the model (the default).",
    )
    parser.add_argument(
        "--confirm-full-run",
        action="store_true",
        help="Required with --live when no --limit or --entry-id-file is supplied.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.batch_size <= 6:
        raise SystemExit("--batch-size must be between 1 and 6")
    if not 1 <= args.workers <= MAX_WORKERS:
        raise SystemExit(f"--workers must be between 1 and {MAX_WORKERS}")
    if (
        not 1 <= args.batch_char_limit <= 4000
        or args.rpm <= 0
        or args.retries < 0
        or args.max_provider_calls < 0
    ):
        raise SystemExit(
            "batch char limit must be 1..4000, RPM positive, retries non-negative, "
            "and max provider calls non-negative"
        )
    if args.live and args.limit is None and args.entry_id_file is None and not args.confirm_full_run:
        raise SystemExit("full live annotation requires --confirm-full-run")

    source_db = args.source_db.resolve()
    schema_path = args.schema.resolve()
    output_dir = args.output_dir.resolve()
    derived_db = output_dir / "annotations.sqlite3"
    ndjson_path = output_dir / "annotations.valid.ndjson"
    manifest_path = output_dir / "manifest.json"
    catalog_summary = json.loads(args.summary.read_text(encoding="utf-8"))
    catalog_version = str(catalog_summary["catalog_version"])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_validator = Draft202012Validator(schema, format_checker=FormatChecker())

    all_entries = load_source_entries(source_db)
    selected = select_entries(
        all_entries,
        limit=args.limit,
        sample_mode=args.sample_mode,
        entry_id_file=args.entry_id_file,
    )
    if args.live and len(selected) == len(all_entries) and not args.confirm_full_run:
        raise SystemExit("a selection covering the full corpus requires --confirm-full-run")
    batches = pack_batches(selected, batch_size=args.batch_size, char_limit=args.batch_char_limit)
    plan = {
        "sourceCanonicalRuntimeRecords": len(all_entries),
        "selectedRecords": len(selected),
        "plannedBatches": len(batches),
        "plannedCharacters": sum(entry.char_count for entry in selected),
        "sampleMode": args.sample_mode,
        "batchSize": args.batch_size,
        "batchCharacterLimit": args.batch_char_limit,
        "workers": args.workers,
        "totalRequestsPerMinute": args.rpm,
        "live": bool(args.live),
    }
    print(json.dumps({"event": "plan", **plan}, ensure_ascii=False), flush=True)
    if not args.live:
        return 0

    # Read and validate the provider configuration once before mutating the
    # ledger.  This client never performs a request; live clients are created
    # independently inside worker threads.
    probe_client = DeepSeekBatchClient(
        rpm=float(args.rpm), timeout=args.timeout, max_tokens=args.max_tokens
    )
    requested_model = probe_client.model
    provider_available = probe_client.available
    probe_client.close()
    if not provider_available:
        print(
            json.dumps(
                {
                    "event": "fatal",
                    "reason": "live DeepSeek annotation is not enabled/configured",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return 3

    run_lock = acquire_process_lock(output_dir / ".annotator.lock")
    conn = init_derived_db(derived_db)
    try:
        enqueue(conn, selected)
        invalidated = invalidate_incompatible_valid_rows(
            conn, requested_model=requested_model
        )
        locally_simplified_records, locally_simplified_fields = (
            repair_valid_generated_texts(
                conn,
                schema_validator=schema_validator,
            )
        )
        locally_pruned_trigger_records, locally_pruned_trigger_links = (
            repair_valid_unknown_trigger_supports(
                conn,
                schema_validator=schema_validator,
            )
        )
        locally_recovered = revalidate_stored_responses(
            conn,
            selected,
            schema_validator=schema_validator,
            catalog_version=catalog_version,
            model=requested_model,
        )
        todo = pending_entries(
            conn,
            selected,
            resume=args.resume,
            retry_quarantined=args.retry_quarantined,
        )
        todo_batches = pack_batches(todo, batch_size=args.batch_size, char_limit=args.batch_char_limit)
        max_provider_calls = args.max_provider_calls or max(1, len(todo_batches) * 4)
        call_budget = ProviderCallBudget(max_provider_calls)
        active_workers = min(args.workers, len(todo_batches))
        worker_rpm = per_worker_rpm(args.rpm, active_workers)
        log_event(
            conn,
            "run_started",
            {
                **plan,
                "pendingRecords": len(todo),
                "provenanceInvalidated": invalidated,
                "locallyRecovered": locally_recovered,
                "locallySimplifiedRecords": locally_simplified_records,
                "locallySimplifiedFields": locally_simplified_fields,
                "locallyPrunedUnknownTriggerRecords": locally_pruned_trigger_records,
                "locallyPrunedUnknownTriggerLinks": locally_pruned_trigger_links,
                "activeWorkers": active_workers,
                "totalRequestsPerMinute": args.rpm,
                "requestsPerMinutePerWorker": worker_rpm,
                "maxProviderCalls": max_provider_calls,
            },
        )
        print(
            json.dumps(
                {
                    "event": "run_started",
                    "pendingRecords": len(todo),
                    "pendingBatches": len(todo_batches),
                    "provenanceInvalidated": invalidated,
                    "locallyRecovered": locally_recovered,
                    "locallySimplifiedRecords": locally_simplified_records,
                    "locallySimplifiedFields": locally_simplified_fields,
                    "locallyPrunedUnknownTriggerRecords": locally_pruned_trigger_records,
                    "locallyPrunedUnknownTriggerLinks": locally_pruned_trigger_links,
                    "activeWorkers": active_workers,
                    "totalRequestsPerMinute": args.rpm,
                    "requestsPerMinutePerWorker": worker_rpm,
                    "maxProviderCalls": max_provider_calls,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        worker_results: list[WorkerResult] = []
        progress = ProgressTracker(len(todo_batches))
        if active_workers:
            work_queue: Queue[tuple[int, list[SourceEntry]]] = Queue()
            for index, batch in enumerate(todo_batches, start=1):
                work_queue.put((index, batch))
            stop_event = Event()
            with ThreadPoolExecutor(
                max_workers=active_workers,
                thread_name_prefix="a1-annotator",
            ) as executor:
                futures = {
                    executor.submit(
                        annotation_worker,
                        worker_id,
                        work_queue,
                        stop_event,
                        progress,
                        derived_db=derived_db,
                        schema=schema,
                        catalog_version=catalog_version,
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
                    except Exception as exc:  # defensive: worker normally contains failures
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
                "provider call accounting mismatch between workers and progress tracker"
            )
        if provider_calls != call_budget.used:
            unexpected_reasons.append(
                "provider call accounting mismatch between workers and process budget"
            )

        export_outputs(
            conn,
            ndjson_path=ndjson_path,
            manifest_path=manifest_path,
            source_db=source_db,
            schema_path=schema_path,
            selected_count=len(selected),
            model=requested_model,
            catalog_version=catalog_version,
            provider_calls=provider_calls,
            max_provider_calls=max_provider_calls,
            workers=active_workers,
        )
        final_counts = status_counts(conn)
        selected_counts = selected_status_counts(conn, selected)
        terminal_detail = {
            "statusCounts": final_counts,
            "selectedStatusCounts": selected_counts,
            "providerCalls": provider_calls,
            "maxProviderCalls": max_provider_calls,
            "providerCallBudgetExhausted": call_budget.exhausted,
            "workers": active_workers,
            "completedBatches": progress.completed_batches,
            "plannedBatches": len(todo_batches),
        }
        if fatal_reasons or unexpected_reasons:
            terminal_detail["fatalReasons"] = fatal_reasons
            terminal_detail["unexpectedReasons"] = unexpected_reasons
            log_event(conn, "run_stopped", terminal_detail)
            print(
                json.dumps(
                    {"event": "run_stopped", **terminal_detail},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            return 3 if fatal_reasons else 4

        log_event(conn, "run_finished", terminal_detail)
        print(
            json.dumps(
                {"event": "finished", **terminal_detail},
                ensure_ascii=False,
            ),
            flush=True,
        )
        # A successful stage is all-or-nothing.  This defensive gate also
        # catches an unexpectedly abandoned queued/leased/stale row.
        return 0 if selected_counts == {"valid": len(selected)} else 2
    finally:
        conn.close()
        release_process_lock(run_lock)


if __name__ == "__main__":
    raise SystemExit(main())
