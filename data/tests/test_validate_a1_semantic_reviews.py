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
                "correctionHint": "保留梦境和否定边界。",
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
                    model TEXT NOT NULL,
                    provider_reported_model TEXT,
                    prompt_version TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    review_schema_sha256 TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO semantic_review_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    validator.EXPECTED_REVIEW_MODEL,
                    validator.EXPECTED_REVIEW_MODEL,
                    validator.EXPECTED_REVIEW_PROMPT_VERSION,
                    validator.EXPECTED_REVIEW_PROMPT_SHA256,
                    validator.sha256_file(SCHEMA_PATH),
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
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_jobs
                SET status=?,verdict=?,review_json=?,issues_json=?
                WHERE entry_id=?
                """,
                (
                    review["verdict"],
                    review["verdict"],
                    validator.canonical_json(review),
                    validator.canonical_json(review["issues"]),
                    ENTRY_ID,
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
        self.assertEqual(
            reviewer.PROMPT_VERSION, validator.EXPECTED_REVIEW_PROMPT_VERSION
        )
        self.assertEqual(
            reviewer.sha256_text(reviewer.SYSTEM_PROMPT),
            validator.EXPECTED_REVIEW_PROMPT_SHA256,
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
        self.assertTrue(self.validate(allow_uncertain=True).passed)

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
        self.assertTrue(self.validate(allow_uncertain=True).passed)

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
