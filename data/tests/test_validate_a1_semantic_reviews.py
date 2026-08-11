from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "data"))

import validate_a1_semantic_reviews as validator  # noqa: E402
import review_a1_semantics as reviewer  # noqa: E402


SCHEMA_PATH = PROJECT_ROOT / "contracts" / "a1-semantic-review.schema.json"
STAGE_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "a1-semantic-review-stage.schema.json"
)
ENTRY_ID = "c1ws_0123456789abcdef01234567"
SOURCE_TEXT = "某甲梦见故人来访，醒后才知此事未曾发生。"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def annotation_stub() -> dict:
    source_hash = validator.sha256_text(SOURCE_TEXT)
    return {
        "annotation_record": {
            "unit": {
                "unit_id": ENTRY_ID,
                "source_text_sha256": source_hash,
            },
            "source_profile": {
                "source_text": SOURCE_TEXT,
                "source_text_sha256": source_hash,
            },
            "retrieval_profile": {
                "modern_retrieval_summary": "某甲梦见故人，醒后确认事情并未发生。",
                "narrative_sufficiency": "sufficient",
                "narrative_sufficiency_reason": "原文包含触发、转折和结果。",
                "key_entities": [
                    {"name": "某甲", "role": "梦者", "evidence_ids": ["ev01"]}
                ],
                "plot_beats": [
                    {"type": "trigger", "text": "梦见故人", "evidence_ids": ["ev01"]}
                ],
                "motif_terms": ["梦兆"],
                "summary_evidence_ids": ["ev01"],
            },
            "life_context": [],
            "narrative_arc": {
                "trigger": "某甲梦见故人",
                "conflict_types": ["knowledge_uncertainty"],
                "agency_modes": ["unknown"],
                "ending_mode": "unresolved",
            },
            "auto_safety_screen": {
                "status": "auto_screened",
                "flags": ["none_identified"],
                "interpretation_risks": ["none_identified"],
                "uncertainties": [],
            },
            "evidence": [
                {
                    "id": "ev01",
                    "excerpt": "某甲梦见故人来访",
                    "supports": ["retrieval_profile.modern_retrieval_summary"],
                }
            ],
        }
    }


def checklist(*false_fields: str) -> dict[str, bool]:
    false_set = set(false_fields)
    return {field: field not in false_set for field in validator.CHECKLIST_FIELDS}


def pass_review() -> dict:
    return {
        "entryId": ENTRY_ID,
        "verdict": "pass",
        "checklist": checklist(),
        "issues": [],
    }


def pass_stage() -> dict:
    return {
        "entryId": ENTRY_ID,
        "fieldChecks": {
            field: {"checked": True, "issueCodes": []}
            for field in validator.CHECKLIST_FIELDS
        },
        "issues": [],
    }


def effective_row_stub() -> tuple[dict, dict, str]:
    canonical = annotation_stub()
    root_hash = validator.sha256_text(validator.canonical_json(canonical))
    review = issue_review("revise")
    review_hash = validator.sha256_text(validator.canonical_json(review))
    effective = json.loads(json.dumps(canonical, ensure_ascii=False))
    effective["annotation_record"]["retrieval_profile"][
        "modern_retrieval_summary"
    ] = "某甲梦见故人来访，醒后确认梦中事件没有发生。"
    effective["annotation_record"]["annotation_meta"] = {
        "research_review_status": "not_reviewed",
        "research_ready": False,
        "human_review": {"reviewed": False},
        "repair_provenance": {
            "canonical_root_sha256": root_hash,
            "base_candidate_sha256": root_hash,
            "immediate_base_sha256": root_hash,
            "parent_effective_sha256": None,
            "repair_iteration": 1,
            "immediate_base_origin": "canonical_a1",
            "review_sha256": review_hash,
            "automatic_semantic_repair": True,
            "human_reviewed": False,
        },
    }
    effective_hash = validator.sha256_text(validator.canonical_json(effective))
    lineage = [
        {
            "repair_iteration": 1,
            "canonical_root_sha256": root_hash,
            "immediate_base_sha256": root_hash,
            "parent_effective_sha256": None,
            "review_sha256": review_hash,
            "effective_annotation_sha256": effective_hash,
        }
    ]
    envelope = {
        "entry_id": ENTRY_ID,
        "source_text_sha256": validator.sha256_text(SOURCE_TEXT),
        "canonical_root_sha256": root_hash,
        "base_candidate_sha256": root_hash,
        "parent_effective_sha256": None,
        "repair_iteration": 1,
        "immediate_base_origin": "canonical_a1",
        "lineage": lineage,
        "review_sha256": review_hash,
        "effective_annotation_sha256": effective_hash,
        "base_annotation": canonical,
        "review": review,
        "effective_annotation": effective,
        "human_reviewed": False,
        "research_ready": False,
    }
    row = {
        "source_text_sha256": validator.sha256_text(SOURCE_TEXT),
        "canonical_root_sha256": root_hash,
        "base_candidate_sha256": root_hash,
        "parent_effective_sha256": None,
        "repair_iteration": 1,
        "immediate_base_origin": "canonical_a1",
        "lineage_json": validator.canonical_json(lineage),
        "review_sha256": review_hash,
        "base_annotation_json": validator.canonical_json(canonical),
        "review_json": validator.canonical_json(review),
        "effective_annotation_json": validator.canonical_json(effective),
        "effective_annotation_sha256": effective_hash,
        "effective_envelope_json": validator.canonical_json(envelope),
    }
    return row, effective, root_hash


def issue_review(verdict: str) -> dict:
    return {
        "entryId": ENTRY_ID,
        "verdict": verdict,
        "checklist": checklist("modernRetrievalSummary"),
        "issues": [
            {
                "code": "summary_modality_negation",
                "field": "modernRetrievalSummary",
                "sourceExcerpt": "未曾发生",
                "correctionHint": "将现有摘要替换为保留梦境和否定边界的表述。",
            }
        ],
    }


class SemanticReviewValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.source_db = root / "source.sqlite3"
        self.annotation_db = root / "annotations.sqlite3"
        self.review_db = root / "reviews.sqlite3"
        self._create_source_db()
        self._create_annotation_db()
        self._create_review_db()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _create_source_db(self) -> None:
        source_hash = validator.sha256_text(SOURCE_TEXT)
        with sqlite3.connect(self.source_db) as conn:
            conn.execute(
                """
                CREATE TABLE entries(
                    entry_id TEXT PRIMARY KEY,
                    source_work_id TEXT NOT NULL,
                    source_work_title TEXT NOT NULL,
                    source_locator TEXT NOT NULL,
                    entry_ordinal INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    extracted_text_sha256 TEXT NOT NULL,
                    dedupe_status TEXT NOT NULL,
                    canonical_entry_id TEXT NOT NULL,
                    runtime_eligible INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ENTRY_ID,
                    "work-1",
                    "测试古籍",
                    "卷一 · 测试条目",
                    1,
                    "测试条目",
                    len(SOURCE_TEXT),
                    SOURCE_TEXT,
                    source_hash,
                    "canonical",
                    ENTRY_ID,
                    1,
                ),
            )

    def _create_annotation_db(self) -> None:
        annotation = annotation_stub()
        with sqlite3.connect(self.annotation_db) as conn:
            conn.execute(
                """
                CREATE TABLE annotation_jobs(
                    entry_id TEXT PRIMARY KEY,
                    source_text_sha256 TEXT NOT NULL,
                    source_work_id TEXT NOT NULL,
                    source_work_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    annotation_json TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO annotation_jobs VALUES(?,?,?,?,?,?,?,?)",
                (
                    ENTRY_ID,
                    validator.sha256_text(SOURCE_TEXT),
                    "work-1",
                    "测试古籍",
                    "测试条目",
                    len(SOURCE_TEXT),
                    "valid",
                    validator.canonical_json(annotation),
                ),
            )

    def _create_review_db(self) -> None:
        annotation = annotation_stub()
        candidate_hash = validator.sha256_text(validator.canonical_json(annotation))
        source_hash = validator.sha256_text(SOURCE_TEXT)
        source = {
            "entry_id": ENTRY_ID,
            "source_work_title": "测试古籍",
            "title": "测试条目",
            "source_locator": "卷一 · 测试条目",
            "text": SOURCE_TEXT,
        }
        input_json = validator.expected_review_input(
            source,
            annotation,
            source_hash=source_hash,
            candidate_hash=candidate_hash,
        )
        review = pass_review()
        stages = {mode: pass_stage() for mode in ("omission", "contradiction")}
        merged_raw = {
            "mode": "merged",
            "reviewVersion": validator.EXPECTED_REVIEW_PROMPT_VERSION,
            "stagePromptVersions": dict(validator.EXPECTED_STAGE_PROMPT_VERSIONS),
            "stagePromptSha256": dict(validator.EXPECTED_STAGE_PROMPT_SHA256),
            "thinkingMode": validator.EXPECTED_THINKING_MODE,
            "reasoningEffort": validator.EXPECTED_REASONING_EFFORT,
            "stageReviews": {ENTRY_ID: stages},
        }
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                CREATE TABLE semantic_review_jobs(
                    entry_id TEXT PRIMARY KEY,
                    source_text_sha256 TEXT NOT NULL,
                    candidate_annotation_sha256 TEXT NOT NULL,
                    source_work_id TEXT NOT NULL,
                    source_work_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    input_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verdict TEXT,
                    review_json TEXT,
                    issues_json TEXT,
                    raw_response_json TEXT,
                    model TEXT NOT NULL,
                    thinking_mode TEXT NOT NULL,
                    reasoning_effort TEXT,
                    provider_reported_model TEXT,
                    prompt_version TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    review_schema_sha256 TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO semantic_review_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ENTRY_ID,
                    source_hash,
                    candidate_hash,
                    "work-1",
                    "测试古籍",
                    "测试条目",
                    len(SOURCE_TEXT),
                    validator.canonical_json(input_json),
                    "pass",
                    "pass",
                    validator.canonical_json(review),
                    validator.canonical_json([]),
                    validator.canonical_json(merged_raw),
                    validator.EXPECTED_REVIEW_MODEL,
                    validator.EXPECTED_THINKING_MODE,
                    validator.EXPECTED_REASONING_EFFORT,
                    validator.EXPECTED_REVIEW_MODEL,
                    validator.EXPECTED_REVIEW_PROMPT_VERSION,
                    validator.EXPECTED_REVIEW_PROMPT_SHA256,
                    validator.sha256_file(SCHEMA_PATH),
                ),
            )
            conn.execute(
                """
                CREATE TABLE semantic_review_stages(
                    entry_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    source_text_sha256 TEXT NOT NULL,
                    candidate_annotation_sha256 TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage_review_json TEXT,
                    raw_response_json TEXT,
                    model TEXT NOT NULL,
                    thinking_mode TEXT NOT NULL,
                    reasoning_effort TEXT,
                    provider_reported_model TEXT,
                    prompt_version TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    stage_schema_sha256 TEXT NOT NULL,
                    PRIMARY KEY(entry_id,mode)
                )
                """
            )
            for mode, stage in stages.items():
                raw_stage = {"mode": mode, "reviews": [stage]}
                conn.execute(
                    "INSERT INTO semantic_review_stages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ENTRY_ID,
                        mode,
                        source_hash,
                        candidate_hash,
                        validator.canonical_json(input_json),
                        "valid",
                        validator.canonical_json(stage),
                        validator.canonical_json(raw_stage),
                        validator.EXPECTED_REVIEW_MODEL,
                        validator.EXPECTED_THINKING_MODE,
                        validator.EXPECTED_REASONING_EFFORT,
                        validator.EXPECTED_REVIEW_MODEL,
                        validator.EXPECTED_STAGE_PROMPT_VERSIONS[mode],
                        validator.EXPECTED_STAGE_PROMPT_SHA256[mode],
                        validator.sha256_file(STAGE_SCHEMA_PATH),
                    ),
                )

    def validate(self, **overrides: object) -> validator.ValidationReport:
        kwargs: dict[str, object] = {
            "source_db": self.source_db,
            "annotation_db": self.annotation_db,
            "review_db": self.review_db,
            "review_schema": SCHEMA_PATH,
            "expected_count": 1,
        }
        kwargs.update(overrides)
        return validator.validate_databases(**kwargs)  # type: ignore[arg-type]

    def set_review(self, review: dict) -> None:
        stages = {mode: pass_stage() for mode in ("omission", "contradiction")}
        if review.get("issues"):
            first = review["issues"][0]
            if (
                first.get("code") == "summary_modality_negation"
                and first.get("field") == "modernRetrievalSummary"
            ):
                directional_issue = {
                    "code": "summary_modality_negation_contradiction",
                    "field": "modernRetrievalSummary",
                    "targetValue": annotation_stub()["annotation_record"][
                        "retrieval_profile"
                    ]["modern_retrieval_summary"],
                    "sourceExcerpt": first["sourceExcerpt"],
                    "correctionHint": first["correctionHint"],
                }
                stages["contradiction"] = {
                    "entryId": ENTRY_ID,
                    "fieldChecks": {
                        field: {
                            "checked": True,
                            "issueCodes": (
                                [directional_issue["code"]]
                                if field == "modernRetrievalSummary"
                                else []
                            ),
                        }
                        for field in validator.CHECKLIST_FIELDS
                    },
                    "issues": [directional_issue],
                }
        merged_raw = {
            "mode": "merged",
            "reviewVersion": validator.EXPECTED_REVIEW_PROMPT_VERSION,
            "stagePromptVersions": dict(validator.EXPECTED_STAGE_PROMPT_VERSIONS),
            "stagePromptSha256": dict(validator.EXPECTED_STAGE_PROMPT_SHA256),
            "thinkingMode": validator.EXPECTED_THINKING_MODE,
            "reasoningEffort": validator.EXPECTED_REASONING_EFFORT,
            "stageReviews": {ENTRY_ID: stages},
        }
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_jobs
                SET status=?,verdict=?,review_json=?,issues_json=?,raw_response_json=?
                WHERE entry_id=?
                """,
                (
                    review["verdict"],
                    review["verdict"],
                    validator.canonical_json(review),
                    validator.canonical_json(review["issues"]),
                    validator.canonical_json(merged_raw),
                    ENTRY_ID,
                ),
            )
            for mode, stage in stages.items():
                conn.execute(
                    """
                    UPDATE semantic_review_stages
                    SET stage_review_json=?,raw_response_json=?
                    WHERE entry_id=? AND mode=?
                    """,
                    (
                        validator.canonical_json(stage),
                        validator.canonical_json({"mode": mode, "reviews": [stage]}),
                        ENTRY_ID,
                        mode,
                    ),
                )

    def test_complete_current_pass_chain_passes_without_writing_files(self) -> None:
        before = {
            path: (file_sha256(path), path.stat().st_mtime_ns)
            for path in (self.source_db, self.annotation_db, self.review_db)
        }
        report = self.validate()
        self.assertTrue(report.passed, report.errors)
        self.assertEqual(1, report.checked_records)
        self.assertEqual({"pass": 1}, dict(report.verdict_counts))
        after = {
            path: (file_sha256(path), path.stat().st_mtime_ns)
            for path in (self.source_db, self.annotation_db, self.review_db)
        }
        self.assertEqual(before, after)

    def test_pinned_review_provenance_matches_the_current_runner(self) -> None:
        self.assertEqual(reviewer.THINKING_MODE, validator.EXPECTED_THINKING_MODE)
        self.assertIs(reviewer.REASONING_EFFORT, validator.EXPECTED_REASONING_EFFORT)
        self.assertEqual(
            reviewer.PROMPT_VERSION, validator.EXPECTED_REVIEW_PROMPT_VERSION
        )
        self.assertEqual(
            reviewer.sha256_text(reviewer.SYSTEM_PROMPT),
            validator.EXPECTED_REVIEW_PROMPT_SHA256,
        )
        self.assertEqual(
            reviewer.STAGE_PROMPT_VERSIONS,
            validator.EXPECTED_STAGE_PROMPT_VERSIONS,
        )
        self.assertEqual(
            {
                mode: reviewer.sha256_text(reviewer.SYSTEM_PROMPTS[mode])
                for mode in reviewer.REVIEW_MODES
            },
            validator.EXPECTED_STAGE_PROMPT_SHA256,
        )
        self.assertEqual(
            reviewer.STAGE_ISSUE_FIELD_BY_CODE,
            validator.STAGE_ISSUE_FIELD_BY_CODE,
        )
        self.assertEqual(
            reviewer.UNIFIED_CODE_BY_STAGE_CODE,
            validator.UNIFIED_CODE_BY_STAGE_CODE,
        )
        self.assertEqual(reviewer.ISSUE_FIELD_BY_CODE, validator.ISSUE_FIELD_BY_CODE)
        self.assertEqual(reviewer.CHECKLIST_FIELDS, validator.CHECKLIST_FIELDS)
        self.assertEqual(
            reviewer.SOFT_REVISE_HINT_MARKERS,
            validator.SOFT_REVISE_HINT_MARKERS,
        )
        self.assertEqual(
            reviewer.annotation_projection(annotation_stub()),
            validator.annotation_projection(annotation_stub()),
        )

    def test_raw_stage_shape_closure_is_independent_and_fail_closed(self) -> None:
        safe_raw = {
            "mode": "contradiction",
            "reviews": [
                {
                    "entryId": ENTRY_ID,
                    "fieldChecks": [
                        {"field": field, "checked": True, "issues": []}
                        for field in validator.CHECKLIST_FIELDS
                    ],
                }
            ],
        }
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_stages SET raw_response_json=?
                WHERE entry_id=? AND mode='contradiction'
                """,
                (validator.canonical_json(safe_raw), ENTRY_ID),
            )
        report = self.validate()
        self.assertTrue(report.passed, report.errors)
        self.assertEqual(
            reviewer.canonicalize_stage_response_shape(safe_raw),
            validator.canonicalize_stage_response_shape(safe_raw),
        )

        unsafe_raw = {
            "mode": "contradiction",
            "reviews": [
                {
                    "entryId": ENTRY_ID,
                    "fieldChecks": [
                        {"checked": True, "issueCodes": []}
                        for _ in validator.CHECKLIST_FIELDS
                    ],
                    "issues": [],
                }
            ],
        }
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_stages SET raw_response_json=?
                WHERE entry_id=? AND mode='contradiction'
                """,
                (validator.canonical_json(unsafe_raw), ENTRY_ID),
            )
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertTrue(
            any("contradiction raw" in error for error in report.errors),
            report.errors,
        )

    def test_directional_stage_status_hash_and_raw_response_are_hard_gates(self) -> None:
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_stages
                SET status='retryable_failed',prompt_sha256=?
                WHERE mode='omission'
                """,
                ("c" * 64,),
            )
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertTrue(any("omission stage" in error for error in report.errors))

        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_stages
                SET status='valid',prompt_sha256=?,raw_response_json='{}'
                WHERE mode='omission'
                """,
                (validator.EXPECTED_STAGE_PROMPT_SHA256["omission"],),
            )
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertTrue(any("omission raw" in error for error in report.errors))

    def test_effective_candidate_hash_lineage_and_review_closure_are_independent(self) -> None:
        row, expected, root_hash = effective_row_stub()
        candidate, candidate_hash, iteration = validator.validated_effective_candidate(
            row,  # type: ignore[arg-type]
            entry_id=ENTRY_ID,
            source_text=SOURCE_TEXT,
            source_hash=validator.sha256_text(SOURCE_TEXT),
            canonical_root_sha256=root_hash,
        )
        self.assertEqual(expected, candidate)
        self.assertEqual(
            validator.sha256_text(validator.canonical_json(expected)), candidate_hash
        )
        self.assertEqual(1, iteration)

        broken = dict(row, review_sha256="d" * 64)
        with self.assertRaisesRegex(ValueError, "review hash"):
            validator.validated_effective_candidate(
                broken,  # type: ignore[arg-type]
                entry_id=ENTRY_ID,
                source_text=SOURCE_TEXT,
                source_hash=validator.sha256_text(SOURCE_TEXT),
                canonical_root_sha256=root_hash,
            )

    def test_current_candidate_and_source_hashes_are_required(self) -> None:
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                "UPDATE semantic_review_jobs SET candidate_annotation_sha256=?",
                ("f" * 64,),
            )
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertTrue(any("candidate hash is not current" in e for e in report.errors))

        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                "UPDATE semantic_review_jobs SET source_text_sha256=?",
                ("e" * 64,),
            )
        report = self.validate()
        self.assertTrue(any("review source hash is not current" in e for e in report.errors))

    def test_input_json_must_exactly_reconstruct_current_source_and_candidate(self) -> None:
        with sqlite3.connect(self.review_db) as conn:
            raw = conn.execute("SELECT input_json FROM semantic_review_jobs").fetchone()[0]
            value = json.loads(raw)
            value["sourceText"] += "篡改"
            conn.execute(
                "UPDATE semantic_review_jobs SET input_json=?",
                (validator.canonical_json(value),),
            )
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertTrue(any("input_json does not match" in e for e in report.errors))

    def test_revise_and_uncertain_require_explicit_allow_flags(self) -> None:
        self.set_review(issue_review("revise"))
        self.assertFalse(self.validate().passed)
        self.assertTrue(self.validate(allow_revise=True).passed)

        self.set_review(issue_review("uncertain"))
        self.assertFalse(self.validate().passed)
        report = self.validate(allow_uncertain=True)
        self.assertFalse(report.passed)
        self.assertTrue(any("deterministic stage union" in error for error in report.errors))

    def test_nonfinal_status_never_passes(self) -> None:
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_jobs
                SET status='queued',verdict=NULL,review_json=NULL,issues_json=NULL
                """
            )
        report = self.validate(allow_revise=True, allow_uncertain=True)
        self.assertFalse(report.passed)
        self.assertTrue(any("non-final/disallowed states" in e for e in report.errors))

    def test_schema_id_excerpt_and_code_field_contracts_are_checked(self) -> None:
        review = issue_review("revise")
        review["entryId"] = "c1ws_89abcdef0123456789abcdef"
        review["issues"][0]["field"] = "plotBeats"
        review["issues"][0]["sourceExcerpt"] = "不存在的原文"
        self.set_review(review)
        report = self.validate(allow_revise=True)
        self.assertFalse(report.passed)
        joined = "\n".join(report.errors)
        self.assertIn("entryId does not match", joined)
        self.assertIn("code-field mapping is invalid", joined)
        self.assertIn("sourceExcerpt is not exact", joined)

    def test_checklist_and_soft_revise_language_are_independent_hard_gates(self) -> None:
        review = issue_review("revise")
        review["checklist"] = checklist()
        self.set_review(review)
        report = self.validate(allow_revise=True)
        self.assertFalse(report.passed)
        self.assertTrue(
            any("checklist fields marked true" in error for error in report.errors)
        )

        review = issue_review("revise")
        review["issues"][0]["correctionHint"] = "当前标注合理，可保留，无需修改。"
        self.set_review(review)
        report = self.validate(allow_revise=True)
        self.assertFalse(report.passed)
        self.assertTrue(any("soft language" in error for error in report.errors))

        review = issue_review("uncertain")
        review["issues"][0]["correctionHint"] = "语境残缺，需确认否定范围。"
        self.set_review(review)
        report = self.validate(allow_uncertain=True)
        self.assertFalse(report.passed)
        self.assertTrue(any("deterministic stage union" in error for error in report.errors))

    def test_prompt_schema_and_model_provenance_are_required(self) -> None:
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_jobs
                SET model='wrong-model',prompt_version='old-prompt',
                    prompt_sha256=?,review_schema_sha256=?
                """,
                ("a" * 64, "b" * 64),
            )
        report = self.validate()
        joined = "\n".join(report.errors)
        self.assertIn("model does not match", joined)
        self.assertIn("prompt_version is not current", joined)
        self.assertIn("prompt_sha256 is not current", joined)
        self.assertIn("schema hash is not current", joined)

    def test_thinking_provenance_is_closed_at_job_stage_and_merged_layers(self) -> None:
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_jobs
                SET thinking_mode='enabled',reasoning_effort='high'
                """
            )
            conn.execute(
                """
                UPDATE semantic_review_stages
                SET thinking_mode='enabled',reasoning_effort='high'
                WHERE mode='omission'
                """
            )
            merged_raw = json.loads(
                conn.execute(
                    "SELECT raw_response_json FROM semantic_review_jobs"
                ).fetchone()[0]
            )
            merged_raw["thinkingMode"] = "enabled"
            merged_raw["reasoningEffort"] = "high"
            conn.execute(
                "UPDATE semantic_review_jobs SET raw_response_json=?",
                (validator.canonical_json(merged_raw),),
            )

        report = self.validate()
        self.assertFalse(report.passed)
        joined = "\n".join(report.errors)
        self.assertIn("review thinking_mode is not current", joined)
        self.assertIn("review reasoning_effort is not current", joined)
        self.assertIn("omission stage thinking_mode is not current", joined)
        self.assertIn("omission stage reasoning_effort is not current", joined)
        self.assertIn("merged raw response provenance is invalid", joined)

    def test_expected_count_and_full_coverage_are_hard_gates(self) -> None:
        report = self.validate(expected_count=2)
        self.assertFalse(report.passed)
        self.assertTrue(any("C1 count" in e for e in report.errors))

        with sqlite3.connect(self.review_db) as conn:
            conn.execute("DELETE FROM semantic_review_jobs")
        report = self.validate()
        self.assertFalse(report.passed)
        self.assertTrue(any("review row count" in e for e in report.errors))
        self.assertTrue(any("missing semantic-review row" in e for e in report.errors))


if __name__ == "__main__":
    unittest.main()
