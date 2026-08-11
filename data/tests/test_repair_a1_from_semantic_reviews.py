from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from data import repair_a1_from_semantic_reviews as repair  # noqa: E402
from data import review_a1_semantics as reviewer  # noqa: E402


CANONICAL_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "a1-retrieval-annotation.schema.json"
ENVELOPE_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "a1-effective-repair-envelope.schema.json"
)
REVIEW_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "a1-semantic-review.schema.json"
CANONICAL_SCHEMA = json.loads(CANONICAL_SCHEMA_PATH.read_text(encoding="utf-8"))
ENVELOPE_SCHEMA = json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))
REVIEW_SCHEMA = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
EFFECTIVE_SCHEMA = repair.build_effective_schema(CANONICAL_SCHEMA)
CANONICAL_VALIDATOR = Draft202012Validator(
    CANONICAL_SCHEMA, format_checker=FormatChecker()
)
EFFECTIVE_VALIDATOR = Draft202012Validator(
    EFFECTIVE_SCHEMA, format_checker=FormatChecker()
)
ENVELOPE_VALIDATOR = Draft202012Validator(
    ENVELOPE_SCHEMA, format_checker=FormatChecker()
)
REVIEW_VALIDATOR = Draft202012Validator(REVIEW_SCHEMA, format_checker=FormatChecker())


def source_entry(
    *,
    entry_id: str = "c1ws_aaaaaaaaaaaaaaaaaaaaaaaa",
    text: str = "甲梦见故人来访，醒后才知此事未曾发生，邻人随后记录此事。",
) -> repair.SourceEntry:
    return repair.SourceEntry(
        entry_id=entry_id,
        source_work_id="fixture-work",
        source_work_title="测试古籍",
        source_work_period="清",
        volume="卷一",
        source_locator="卷一·第一则",
        entry_ordinal=1,
        title="测试条目",
        source_url="https://example.test/source",
        char_count=len(text),
        text=text,
        extracted_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def raw_annotation(source: repair.SourceEntry) -> dict:
    return {
        "entryId": source.entry_id,
        "modernRetrievalSummary": "甲在梦中见到故人来访，醒来后才知道这件事实际上并未发生。",
        "narrativeSufficiency": "sufficient",
        "narrativeSufficiencyReason": None,
        "keyEntities": [
            {"name": "甲", "role": "梦者", "evidenceExcerpt": "甲梦见故人来访"}
        ],
        "plotBeats": [
            {
                "type": "trigger",
                "text": "甲梦见故人",
                "evidenceExcerpt": "甲梦见故人来访",
            }
        ],
        "motifTerms": ["梦境"],
        "lifeContext": [],
        "narrativeArc": {
            "trigger": "甲梦见故人来访",
            "conflictTypes": [],
            "agencyModes": ["unknown"],
            "endingMode": "unknown",
        },
        "autoSafetyScreen": {
            "status": "auto_screened",
            "flags": ["none_identified"],
            "interpretationRisks": ["none_identified"],
            "uncertainties": [],
        },
        "evidence": [
            {
                "excerpt": source.text,
                "supports": [
                    "retrieval_profile.modern_retrieval_summary",
                    "narrative_arc.trigger",
                ],
            }
        ],
        "overallConfidence": "medium",
    }


def repaired_raw_annotation(source: repair.SourceEntry, iteration: int = 1) -> dict:
    raw = raw_annotation(source)
    raw["modernRetrievalSummary"] = (
        f"原文记述甲梦见故人来访，醒来后确认梦中事件并未真实发生；这是第{iteration}次自动修订。"
    )
    raw["motifTerms"] = ["梦境", "梦醒辨真"]
    return raw


def repair_candidate(
    *, verdict: str = "revise", source: repair.SourceEntry | None = None
) -> repair.RepairCandidate:
    source = source or source_entry()
    base = repair.normalise_annotation(
        raw_annotation(source),
        source,
        schema_validator=CANONICAL_VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
        allow_local_repair=True,
    )
    review = {
        "entryId": source.entry_id,
        "verdict": verdict,
        "checklist": {
            "modernRetrievalSummary": False,
            "narrativeSufficiency": True,
            "keyEntities": True,
            "plotBeats": True,
            "motifTerms": True,
            "lifeContext": True,
            "narrativeArc.trigger": True,
            "narrativeArc.conflictTypes": True,
            "narrativeArc.agencyModes": True,
            "narrativeArc.endingMode": True,
            "autoSafetyScreen.status": True,
            "autoSafetyScreen.flags": True,
            "autoSafetyScreen.interpretationRisks": True,
            "autoSafetyScreen.uncertainties": True,
            "evidence.supports": True,
        },
        "issues": [
            {
                "code": "summary_modality_negation",
                "field": "modernRetrievalSummary",
                "sourceExcerpt": "未曾发生",
                "correctionHint": "保留梦境和否定边界。",
            }
        ],
    }
    return repair.RepairCandidate(
        source=source,
        base_annotation=base,
        base_candidate_sha256=repair.sha256_text(repair.canonical_json(base)),
        review=review,
        review_sha256=repair.sha256_text(repair.canonical_json(review)),
        review_verdict=verdict,
        review_prompt_version="fixture-review-v3",
        review_prompt_sha256="c" * 64,
        review_schema_sha256="d" * 64,
        catalog_version="fixture-catalog-v1",
        canonical_root_sha256=repair.sha256_text(repair.canonical_json(base)),
        parent_effective_sha256=None,
        repair_iteration=1,
        immediate_base_origin="canonical_a1",
        prior_lineage=(),
    )


def parse(candidate: repair.RepairCandidate, *, raw: dict | None = None) -> dict:
    response = {
        "annotations": [
            raw or repaired_raw_annotation(candidate.source, candidate.repair_iteration)
        ]
    }
    return repair.parse_and_normalize_repairs(
        response,
        [candidate],
        provider_meta={
            "response_id": "resp-fixture",
            "model": "deepseek-v4-flash",
            "requested_model": "deepseek-v4-flash",
            "usage": {"total_tokens": 10},
        },
        canonical_schema_validator=CANONICAL_VALIDATOR,
        effective_schema_validator=EFFECTIVE_VALIDATOR,
        envelope_schema_validator=ENVELOPE_VALIDATOR,
        canonical_schema_sha256=repair.sha256_file(CANONICAL_SCHEMA_PATH),
        effective_schema_sha256=repair.sha256_text(
            repair.canonical_json(EFFECTIVE_SCHEMA)
        ),
        envelope_schema_sha256=repair.sha256_file(ENVELOPE_SCHEMA_PATH),
    )[candidate.entry_id]


def provider_meta(iteration: int) -> dict:
    return {
        "response_id": f"resp-fixture-{iteration}",
        "model": "deepseek-v4-flash",
        "requested_model": "deepseek-v4-flash",
        "usage": {
            "prompt_tokens": 100 + iteration,
            "completion_tokens": 20 + iteration,
            "total_tokens": 120 + 2 * iteration,
        },
    }


def parse_with_meta(candidate: repair.RepairCandidate) -> tuple[dict, dict, dict]:
    response = {
        "annotations": [
            repaired_raw_annotation(candidate.source, candidate.repair_iteration)
        ]
    }
    meta = provider_meta(candidate.repair_iteration)
    result = repair.parse_and_normalize_repairs(
        response,
        [candidate],
        provider_meta=meta,
        canonical_schema_validator=CANONICAL_VALIDATOR,
        effective_schema_validator=EFFECTIVE_VALIDATOR,
        envelope_schema_validator=ENVELOPE_VALIDATOR,
        canonical_schema_sha256=repair.sha256_file(CANONICAL_SCHEMA_PATH),
        effective_schema_sha256=repair.sha256_text(
            repair.canonical_json(EFFECTIVE_SCHEMA)
        ),
        envelope_schema_sha256=repair.sha256_file(ENVELOPE_SCHEMA_PATH),
    )[candidate.entry_id]
    return result, response, meta


def write_input_ledgers(
    root: Path,
    *,
    canonical: repair.RepairCandidate,
    reviewed_candidate_sha256: str,
) -> tuple[Path, Path, Path]:
    import sqlite3

    source_db = root / "source.sqlite3"
    annotation_db = root / "annotations.sqlite3"
    review_db = root / "reviews.sqlite3"
    source = canonical.source
    with closing(sqlite3.connect(source_db)) as conn:
        conn.execute(
            """
            CREATE TABLE entries(
                entry_id TEXT,source_work_id TEXT,source_work_title TEXT,
                source_work_period TEXT,volume TEXT,source_locator TEXT,
                entry_ordinal INTEGER,title TEXT,source_url TEXT,char_count INTEGER,
                text TEXT,extracted_text_sha256 TEXT,dedupe_status TEXT,
                runtime_eligible INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source.entry_id,
                source.source_work_id,
                source.source_work_title,
                source.source_work_period,
                source.volume,
                source.source_locator,
                source.entry_ordinal,
                source.title,
                source.source_url,
                source.char_count,
                source.text,
                source.extracted_text_sha256,
                "canonical",
                1,
            ),
        )
        conn.commit()
    with closing(sqlite3.connect(annotation_db)) as conn:
        conn.execute(
            """
            CREATE TABLE annotation_jobs(
                entry_id TEXT,source_text_sha256 TEXT,status TEXT,annotation_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO annotation_jobs VALUES(?,?,?,?)",
            (
                source.entry_id,
                source.extracted_text_sha256,
                "valid",
                repair.canonical_json(canonical.base_annotation),
            ),
        )
        conn.commit()
    with closing(sqlite3.connect(review_db)) as conn:
        conn.execute(
            """
            CREATE TABLE semantic_review_jobs(
                entry_id TEXT,source_text_sha256 TEXT,candidate_annotation_sha256 TEXT,
                source_work_id TEXT,status TEXT,verdict TEXT,review_json TEXT,
                prompt_version TEXT,prompt_sha256 TEXT,review_schema_sha256 TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO semantic_review_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                source.entry_id,
                source.extracted_text_sha256,
                reviewed_candidate_sha256,
                source.source_work_id,
                canonical.review_verdict,
                canonical.review_verdict,
                repair.canonical_json(canonical.review),
                canonical.review_prompt_version,
                canonical.review_prompt_sha256,
                canonical.review_schema_sha256,
            ),
        )
        conn.commit()
    return source_db, annotation_db, review_db


def load_fixture_candidates(
    source_db: Path,
    annotation_db: Path,
    review_db: Path,
    effective_db: Path,
    *,
    max_iterations: int = 3,
) -> list[repair.RepairCandidate]:
    return repair.load_repair_candidates(
        source_db,
        annotation_db,
        review_db,
        effective_db,
        verdicts={"revise", "uncertain"},
        canonical_schema_validator=CANONICAL_VALIDATOR,
        effective_schema_validator=EFFECTIVE_VALIDATOR,
        envelope_schema_validator=ENVELOPE_VALIDATOR,
        max_repair_iterations=max_iterations,
    )[0]


class EffectiveEnvelopeTests(unittest.TestCase):
    def test_v4_dual_stage_merge_is_directly_consumable_by_repair(self) -> None:
        stage_codes = set(
            json.loads(
                (PROJECT_ROOT / "contracts" / "a1-semantic-review-stage.schema.json")
                .read_text(encoding="utf-8")
            )["$defs"]["issueCode"]["enum"]
        )
        review_codes = set(
            REVIEW_SCHEMA["$defs"]["issue"]["properties"]["code"]["enum"]
        )
        self.assertEqual(stage_codes, set(reviewer.UNIFIED_CODE_BY_STAGE_CODE))
        self.assertEqual(
            review_codes, set(reviewer.UNIFIED_CODE_BY_STAGE_CODE.values())
        )
        candidate = repair_candidate()
        review_candidate = reviewer.ReviewCandidate(
            entry_id=candidate.entry_id,
            source_work_id=candidate.source.source_work_id,
            source_work_title=candidate.source.source_work_title,
            title=candidate.source.title,
            source_locator=candidate.source.source_locator,
            entry_ordinal=candidate.source.entry_ordinal,
            char_count=candidate.source.char_count,
            source_text=candidate.source.text,
            source_text_sha256=candidate.source.extracted_text_sha256,
            candidate_annotation_sha256=candidate.base_candidate_sha256,
            candidate_projection={},
            locked_safety_flags=(),
        )
        checks = {
            field: {"checked": True, "issueCodes": []}
            for field in reviewer.CHECKLIST_FIELDS
        }
        omission_issue = {
            "code": "summary_material_omission",
            "field": "modernRetrievalSummary",
            "targetValue": "邻人记录此事",
            "sourceExcerpt": "邻人随后记录此事",
            "correctionHint": "补入邻人记录此事。",
        }
        omission_checks = json.loads(json.dumps(checks, ensure_ascii=False))
        omission_checks["modernRetrievalSummary"]["issueCodes"] = [
            "summary_material_omission"
        ]
        merged = reviewer.merge_stage_reviews(
            {
                candidate.entry_id: {
                    "entryId": candidate.entry_id,
                    "fieldChecks": omission_checks,
                    "issues": [omission_issue],
                }
            },
            {
                candidate.entry_id: {
                    "entryId": candidate.entry_id,
                    "fieldChecks": checks,
                    "issues": [],
                }
            },
            [review_candidate],
            schema_validator=REVIEW_VALIDATOR,
        )
        merged_review = merged[candidate.entry_id]
        repair._validate_review(
            merged_review,
            entry_id=candidate.entry_id,
            verdict="revise",
            source_text=candidate.source.text,
        )
        v4_candidate = replace(
            candidate,
            review=merged_review,
            review_sha256=repair.sha256_text(repair.canonical_json(merged_review)),
            review_prompt_version=reviewer.PROMPT_VERSION,
            review_prompt_sha256=repair.sha256_text(reviewer.SYSTEM_PROMPT),
            review_schema_sha256=repair.sha256_file(REVIEW_SCHEMA_PATH),
        )
        result = parse(v4_candidate)
        self.assertEqual(
            v4_candidate.review_sha256,
            result["effective_envelope"]["review_sha256"],
        )

    def test_truthful_repair_meta_and_hash_closure(self) -> None:
        candidate = repair_candidate()
        result = parse(candidate)
        normalized = result["normalized_annotation"]
        effective = result["effective_annotation"]
        envelope = result["effective_envelope"]

        self.assertEqual([], list(CANONICAL_VALIDATOR.iter_errors(normalized)))
        self.assertNotEqual([], list(CANONICAL_VALIDATOR.iter_errors(effective)))
        self.assertEqual([], list(EFFECTIVE_VALIDATOR.iter_errors(effective)))
        self.assertEqual([], list(ENVELOPE_VALIDATOR.iter_errors(envelope)))
        meta = effective["annotation_record"]["annotation_meta"]
        self.assertEqual(repair.REPAIR_PROMPT_VERSION, meta["generator"]["prompt_version"])
        self.assertFalse(meta["validation"]["schema_valid"])
        self.assertFalse(meta["human_review"]["reviewed"])
        self.assertEqual("not_reviewed", meta["research_review_status"])
        self.assertFalse(meta["research_ready"])
        self.assertEqual("fixture-review-v3", meta["repair_provenance"]["review_prompt_version"])
        self.assertEqual(
            envelope["effective_annotation_sha256"],
            repair.sha256_text(repair.canonical_json(effective)),
        )

    def test_uncertain_repair_stays_low_confidence_and_not_human_reviewed(self) -> None:
        candidate = repair_candidate(verdict="uncertain")
        result = parse(candidate)
        record = result["effective_annotation"]["annotation_record"]
        self.assertEqual("low", record["annotation_meta"]["overall_confidence"])
        self.assertTrue(
            any("尚未经人工审核" in item for item in record["auto_safety_screen"]["uncertainties"])
        )
        self.assertFalse(result["effective_envelope"]["human_reviewed"])
        self.assertFalse(result["effective_envelope"]["research_ready"])

    def test_batch_ids_must_match_exactly(self) -> None:
        candidate = repair_candidate()
        bad = raw_annotation(candidate.source)
        bad["entryId"] = "c1ws_bbbbbbbbbbbbbbbbbbbbbbbb"
        with self.assertRaisesRegex(repair.RepairValidationError, "IDs do not exactly match"):
            parse(candidate, raw=bad)


class PackingAndGuardTests(unittest.TestCase):
    def test_provider_call_budget_is_a_shared_hard_cap(self) -> None:
        budget = repair.ProviderCallBudget(1)
        budget.reserve()
        with self.assertRaisesRegex(repair.FatalProviderError, "budget exhausted"):
            budget.reserve()

    def test_max_four_and_4000_source_chars_with_complete_long_single(self) -> None:
        small = [
            repair_candidate(
                source=source_entry(
                    entry_id=f"c1ws_{index:024x}",
                    text="甲梦见故人来访，醒后才知此事未曾发生，邻人随后记录此事。",
                )
            )
            for index in range(5)
        ]
        long_source = source_entry(
            entry_id="c1ws_ffffffffffffffffffffffff",
            text="甲梦见故人来访，醒后才知此事未曾发生。" * 210,
        )
        long = repair_candidate(source=long_source)
        batches = repair.pack_repair_batches(
            [*small, long], batch_size=4, char_limit=4000
        )
        self.assertTrue(all(len(batch) <= 4 for batch in batches))
        self.assertEqual([long], batches[-1])
        self.assertEqual(long_source.text, batches[-1][0].provider_input()["sourceText"])

    def test_four_workers_share_one_total_rpm(self) -> None:
        self.assertEqual(5, repair.per_worker_rpm(20, 4))
        self.assertEqual(20, repair.per_worker_rpm(20, 4) * 4)
        self.assertEqual(0, repair.per_worker_rpm(20, 0))

    def test_full_live_scope_requires_confirmation(self) -> None:
        with self.assertRaisesRegex(SystemExit, "confirm-full-run"):
            repair.enforce_live_scope_confirmation(
                live=True, selected_count=10, eligible_count=10, confirmed=False
            )
        repair.enforce_live_scope_confirmation(
            live=True, selected_count=9, eligible_count=10, confirmed=False
        )

    def test_input_output_paths_must_be_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "shared.sqlite3"
            with self.assertRaisesRegex(SystemExit, "source-db=effective-db"):
                repair.enforce_disjoint_paths(
                    source_db=shared,
                    annotation_db=root / "a.sqlite3",
                    review_db=root / "r.sqlite3",
                    effective_db=shared,
                )


class LedgerAndOfflineTests(unittest.TestCase):
    def test_review_prompt_change_marks_effective_row_stale(self) -> None:
        candidate = repair_candidate()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "effective.sqlite3"
            conn = repair.init_effective_db(db)
            try:
                kwargs = {
                    "model": "deepseek-v4-flash",
                    "canonical_schema_sha256": "1" * 64,
                    "effective_schema_sha256": "2" * 64,
                    "envelope_schema_sha256": "3" * 64,
                }
                self.assertEqual(0, repair.enqueue_candidates(conn, [candidate], **kwargs))
                changed = replace(
                    candidate,
                    review_prompt_version="fixture-review-v4",
                    review_prompt_sha256="e" * 64,
                )
                self.assertEqual(1, repair.enqueue_candidates(conn, [changed], **kwargs))
                row = conn.execute(
                    "SELECT status,review_prompt_version FROM effective_repair_jobs WHERE entry_id=?",
                    (candidate.entry_id,),
                ).fetchone()
                self.assertEqual("stale", row["status"])
                self.assertEqual("fixture-review-v4", row["review_prompt_version"])
            finally:
                conn.close()

    def test_dry_run_never_constructs_provider_client(self) -> None:
        candidate = repair_candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = [
                "--source-db",
                str(root / "source.sqlite3"),
                "--annotation-db",
                str(root / "annotation.sqlite3"),
                "--review-db",
                str(root / "review.sqlite3"),
                "--effective-db",
                str(root / "effective.sqlite3"),
                "--manifest",
                str(root / "manifest.json"),
                "--dry-run",
            ]
            with patch.object(
                repair, "load_repair_candidates", return_value=([candidate], 12353)
            ), patch.object(
                repair.DeepSeekRepairClient,
                "__init__",
                side_effect=AssertionError("provider client must not be constructed"),
            ):
                self.assertEqual(0, repair.main(argv))
            self.assertFalse((root / "effective.sqlite3").exists())


class IterativeRepairClosureTests(unittest.TestCase):
    LEDGER_KWARGS = {
        "model": "deepseek-v4-flash",
        "canonical_schema_sha256": repair.sha256_file(CANONICAL_SCHEMA_PATH),
        "effective_schema_sha256": repair.sha256_text(
            repair.canonical_json(EFFECTIVE_SCHEMA)
        ),
        "envelope_schema_sha256": repair.sha256_file(ENVELOPE_SCHEMA_PATH),
    }

    def test_canonical_review_resolves_to_first_repair(self) -> None:
        canonical = repair_candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_db, annotation_db, review_db = write_input_ledgers(
                root,
                canonical=canonical,
                reviewed_candidate_sha256=canonical.base_candidate_sha256,
            )
            candidates = load_fixture_candidates(
                source_db,
                annotation_db,
                review_db,
                root / "effective.sqlite3",
            )
        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual(1, candidate.repair_iteration)
        self.assertEqual("canonical_a1", candidate.immediate_base_origin)
        self.assertIsNone(candidate.parent_effective_sha256)
        self.assertEqual(candidate.canonical_root_sha256, candidate.base_candidate_sha256)
        self.assertEqual((), candidate.prior_lineage)

    def test_effective_review_becomes_second_repair_and_archives_first_provider(self) -> None:
        import sqlite3

        canonical = repair_candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            effective_db = root / "effective.sqlite3"
            source_db, annotation_db, review_db = write_input_ledgers(
                root,
                canonical=canonical,
                reviewed_candidate_sha256=canonical.base_candidate_sha256,
            )
            first = load_fixture_candidates(
                source_db, annotation_db, review_db, effective_db
            )[0]
            first_result, first_response, first_meta = parse_with_meta(first)
            conn = repair.init_effective_db(effective_db)
            try:
                repair.enqueue_candidates(conn, [first], **self.LEDGER_KWARGS)
                repair.mark_leased(conn, [first])
                repair.mark_valid(
                    conn, {first.entry_id: first_result}, [first], first_response, first_meta
                )
            finally:
                conn.close()
            first_effective_hash = first_result["effective_envelope"][
                "effective_annotation_sha256"
            ]
            with closing(sqlite3.connect(review_db)) as conn:
                conn.execute(
                    "UPDATE semantic_review_jobs SET candidate_annotation_sha256=?",
                    (first_effective_hash,),
                )
                conn.commit()
            second = load_fixture_candidates(
                source_db, annotation_db, review_db, effective_db
            )[0]
            self.assertEqual(2, second.repair_iteration)
            self.assertEqual("effective_repair", second.immediate_base_origin)
            self.assertEqual(first_effective_hash, second.parent_effective_sha256)
            self.assertEqual(first_effective_hash, second.base_candidate_sha256)
            self.assertEqual(1, len(second.prior_lineage))

            second_result, second_response, second_meta = parse_with_meta(second)
            conn = repair.init_effective_db(effective_db)
            try:
                self.assertEqual(
                    1,
                    repair.enqueue_candidates(conn, [second], **self.LEDGER_KWARGS),
                )
                repair.mark_leased(conn, [second])
                repair.mark_valid(
                    conn,
                    {second.entry_id: second_result},
                    [second],
                    second_response,
                    second_meta,
                )
                current = conn.execute(
                    """
                    SELECT repair_iteration,parent_effective_sha256,lineage_json,
                           effective_annotation_sha256,provider_response_id
                    FROM effective_repair_jobs WHERE entry_id=?
                    """,
                    (second.entry_id,),
                ).fetchone()
                history = conn.execute(
                    """
                    SELECT repair_iteration,effective_annotation_sha256,
                           provider_response_id,raw_response_json
                    FROM effective_repair_history WHERE entry_id=?
                    """,
                    (second.entry_id,),
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(2, current["repair_iteration"])
            self.assertEqual(first_effective_hash, current["parent_effective_sha256"])
            self.assertEqual(2, len(json.loads(current["lineage_json"])))
            self.assertEqual("resp-fixture-2", current["provider_response_id"])
            self.assertEqual(1, len(history))
            self.assertEqual("resp-fixture-1", history[0]["provider_response_id"])
            self.assertEqual(first_effective_hash, history[0]["effective_annotation_sha256"])
            self.assertIsNotNone(history[0]["raw_response_json"])

    def test_review_hash_must_match_canonical_or_current_effective(self) -> None:
        canonical = repair_candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_db, annotation_db, review_db = write_input_ledgers(
                root,
                canonical=canonical,
                reviewed_candidate_sha256="f" * 64,
            )
            with self.assertRaisesRegex(RuntimeError, "immediate base"):
                load_fixture_candidates(
                    source_db,
                    annotation_db,
                    review_db,
                    root / "effective.sqlite3",
                )

    def test_failed_second_iteration_resumes_from_the_same_immediate_base(self) -> None:
        import sqlite3

        canonical = repair_candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            effective_db = root / "effective.sqlite3"
            source_db, annotation_db, review_db = write_input_ledgers(
                root,
                canonical=canonical,
                reviewed_candidate_sha256=canonical.base_candidate_sha256,
            )
            first = load_fixture_candidates(
                source_db, annotation_db, review_db, effective_db
            )[0]
            first_result, first_response, first_meta = parse_with_meta(first)
            conn = repair.init_effective_db(effective_db)
            try:
                repair.enqueue_candidates(conn, [first], **self.LEDGER_KWARGS)
                repair.mark_leased(conn, [first])
                repair.mark_valid(
                    conn, {first.entry_id: first_result}, [first], first_response, first_meta
                )
            finally:
                conn.close()
            parent_hash = first_result["effective_envelope"][
                "effective_annotation_sha256"
            ]
            with closing(sqlite3.connect(review_db)) as conn:
                conn.execute(
                    "UPDATE semantic_review_jobs SET candidate_annotation_sha256=?",
                    (parent_hash,),
                )
                conn.commit()
            second = load_fixture_candidates(
                source_db, annotation_db, review_db, effective_db
            )[0]
            conn = repair.init_effective_db(effective_db)
            try:
                repair.enqueue_candidates(conn, [second], **self.LEDGER_KWARGS)
                repair.mark_leased(conn, [second])
                repair.mark_failed(
                    conn,
                    [second],
                    RuntimeError("fixture retry"),
                    retryable=True,
                    raw_response=None,
                    model="deepseek-v4-flash",
                )
            finally:
                conn.close()
            resumed = load_fixture_candidates(
                source_db, annotation_db, review_db, effective_db
            )[0]
            self.assertEqual(2, resumed.repair_iteration)
            self.assertEqual(parent_hash, resumed.base_candidate_sha256)
            self.assertEqual(parent_hash, resumed.parent_effective_sha256)
            conn = repair.init_effective_db(effective_db)
            try:
                self.assertEqual(
                    0, repair.enqueue_candidates(conn, [resumed], **self.LEDGER_KWARGS)
                )
                pending = repair.pending_candidates(
                    conn,
                    [resumed],
                    resume=True,
                    retry_quarantined=False,
                )
            finally:
                conn.close()
            self.assertEqual([resumed], pending)

    def test_iteration_limit_defaults_to_three_and_fails_closed(self) -> None:
        import sqlite3

        self.assertEqual(
            3, repair.build_parser().parse_args([]).max_repair_iterations
        )
        canonical = repair_candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            effective_db = root / "effective.sqlite3"
            source_db, annotation_db, review_db = write_input_ledgers(
                root,
                canonical=canonical,
                reviewed_candidate_sha256=canonical.base_candidate_sha256,
            )
            current_hash = canonical.base_candidate_sha256
            for expected_iteration in range(1, 4):
                with closing(sqlite3.connect(review_db)) as conn:
                    conn.execute(
                        "UPDATE semantic_review_jobs SET candidate_annotation_sha256=?",
                        (current_hash,),
                    )
                    conn.commit()
                candidate = load_fixture_candidates(
                    source_db, annotation_db, review_db, effective_db
                )[0]
                self.assertEqual(expected_iteration, candidate.repair_iteration)
                result, response, meta = parse_with_meta(candidate)
                conn = repair.init_effective_db(effective_db)
                try:
                    repair.enqueue_candidates(
                        conn, [candidate], **self.LEDGER_KWARGS
                    )
                    repair.mark_leased(conn, [candidate])
                    repair.mark_valid(
                        conn,
                        {candidate.entry_id: result},
                        [candidate],
                        response,
                        meta,
                    )
                finally:
                    conn.close()
                current_hash = result["effective_envelope"][
                    "effective_annotation_sha256"
                ]
            with closing(sqlite3.connect(review_db)) as conn:
                conn.execute(
                    "UPDATE semantic_review_jobs SET candidate_annotation_sha256=?",
                    (current_hash,),
                )
                conn.commit()
            with self.assertRaisesRegex(RuntimeError, "iteration limit exceeded"):
                load_fixture_candidates(
                    source_db,
                    annotation_db,
                    review_db,
                    effective_db,
                    max_iterations=3,
                )

    def test_v3_checklist_is_required_and_must_match_issue_fields(self) -> None:
        candidate = repair_candidate()
        repair._validate_review(
            candidate.review,
            entry_id=candidate.entry_id,
            verdict=candidate.review_verdict,
            source_text=candidate.source.text,
        )
        missing = json.loads(repair.canonical_json(candidate.review))
        del missing["checklist"]["evidence.supports"]
        with self.assertRaisesRegex(RuntimeError, "checklist contract"):
            repair._validate_review(
                missing,
                entry_id=candidate.entry_id,
                verdict=candidate.review_verdict,
                source_text=candidate.source.text,
            )
        mismatch = json.loads(repair.canonical_json(candidate.review))
        mismatch["checklist"]["modernRetrievalSummary"] = True
        with self.assertRaisesRegex(RuntimeError, "checklist/issues mismatch"):
            repair._validate_review(
                mismatch,
                entry_id=candidate.entry_id,
                verdict=candidate.review_verdict,
                source_text=candidate.source.text,
            )

    def test_no_semantic_change_is_rejected_as_a_loop(self) -> None:
        candidate = repair_candidate()
        with self.assertRaisesRegex(repair.RepairValidationError, "no semantic change"):
            parse(candidate, raw=raw_annotation(candidate.source))


class FakeHttpClientTests(unittest.TestCase):
    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, body: dict) -> None:
            self.body = body

        def json(self) -> dict:
            return self.body

    class Client:
        def __init__(self, body: dict) -> None:
            self.body = body
            self.calls: list[dict] = []

        def post(self, url: str, **kwargs: object) -> "FakeHttpClientTests.Response":
            self.calls.append({"url": url, **kwargs})
            return FakeHttpClientTests.Response(self.body)

        def close(self) -> None:
            pass

    def test_fake_provider_payload_is_nonthinking_and_source_complete(self) -> None:
        candidate = repair_candidate()
        content = json.dumps(
            {"annotations": [raw_annotation(candidate.source)]}, ensure_ascii=False
        )
        http = self.Client(
            {
                "id": "resp-fake",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 12},
            }
        )
        client = repair.DeepSeekRepairClient(
            rpm=60000,
            timeout=1,
            max_tokens=1000,
            http_client=http,
            api_key="fixture-key",
            model="deepseek-v4-flash",
            live_enabled=True,
        )
        try:
            parsed, meta = client.complete([candidate], retries=0)
        finally:
            client.close()
        self.assertIn("annotations", parsed)
        self.assertEqual("resp-fake", meta["response_id"])
        payload = http.calls[0]["json"]
        self.assertEqual({"type": "disabled"}, payload["thinking"])
        sent = json.loads(payload["messages"][1]["content"])
        self.assertEqual(candidate.source.text, sent["records"][0]["sourceText"])
        self.assertEqual(
            candidate.review_prompt_version,
            sent["records"][0]["reviewProvenance"]["promptVersion"],
        )


if __name__ == "__main__":
    unittest.main()
