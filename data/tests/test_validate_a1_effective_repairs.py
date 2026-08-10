from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from data import repair_a1_from_semantic_reviews as repair  # noqa: E402
from data import review_a1_semantics as reviewer  # noqa: E402
from data import validate_a1_effective_repairs as validator  # noqa: E402


CANONICAL_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "a1-retrieval-annotation.schema.json"
REVIEW_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "a1-semantic-review.schema.json"
ENVELOPE_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "a1-effective-repair-envelope.schema.json"
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def raw_annotation(source: repair.SourceEntry) -> dict:
    return {
        "entryId": source.entry_id,
        "modernRetrievalSummary": "甲在梦中见到故人来访，醒来后才知道此事未曾发生。",
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


class FourLedgerFixture:
    def __init__(self, *, with_repair: bool = True) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_db = self.root / "source.sqlite3"
        self.annotation_db = self.root / "a1.sqlite3"
        self.review_db = self.root / "review.sqlite3"
        self.effective_db = self.root / "effective.sqlite3"
        self.envelope_schema_path = self.root / "effective-envelope-v3.schema.json"
        self.source = repair.SourceEntry(
            entry_id="c1ws_aaaaaaaaaaaaaaaaaaaaaaaa",
            source_work_id="fixture-work",
            source_work_title="测试古籍",
            source_work_period="清",
            volume="卷一",
            source_locator="卷一·第一则",
            entry_ordinal=1,
            title="测试条目",
            source_url="https://example.test/source",
            char_count=0,
            text="甲梦见故人来访，醒后才知此事未曾发生，邻人随后记录此事。",
            extracted_text_sha256="",
        )
        self.source = repair.SourceEntry(
            **{
                **self.source.__dict__,
                "char_count": len(self.source.text),
                "extracted_text_sha256": sha256_text(self.source.text),
            }
        )

        self.canonical_schema = json.loads(
            CANONICAL_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.review_schema = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.envelope_schema = self._v3_compatible_envelope_schema()
        self.envelope_schema_path.write_text(
            json.dumps(self.envelope_schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.effective_schema = repair.build_effective_schema(self.canonical_schema)
        self.canonical_validator = Draft202012Validator(
            self.canonical_schema, format_checker=FormatChecker()
        )
        self.review_validator = Draft202012Validator(
            self.review_schema, format_checker=FormatChecker()
        )
        self.envelope_validator = Draft202012Validator(
            self.envelope_schema, format_checker=FormatChecker()
        )
        self.effective_validator = Draft202012Validator(
            self.effective_schema, format_checker=FormatChecker()
        )

        self.base = repair.normalise_annotation(
            raw_annotation(self.source),
            self.source,
            schema_validator=self.canonical_validator,
            catalog_version="fixture-catalog-v1",
            model="deepseek-v4-flash",
            allow_local_repair=True,
        )
        self.review = self._current_review()
        self.candidate = repair.RepairCandidate(
            source=self.source,
            base_annotation=self.base,
            base_candidate_sha256=sha256_text(canonical_json(self.base)),
            review=self.review,
            review_sha256=sha256_text(canonical_json(self.review)),
            review_verdict="revise",
            review_prompt_version=reviewer.PROMPT_VERSION,
            review_prompt_sha256=sha256_text(reviewer.SYSTEM_PROMPT),
            review_schema_sha256=validator.sha256_file(REVIEW_SCHEMA_PATH),
            catalog_version="fixture-catalog-v1",
        )
        self._create_source_db()
        self._create_annotation_db()
        self._create_review_db()
        self._create_effective_db(with_repair=with_repair)

    def close(self) -> None:
        self._tmp.cleanup()

    def _v3_compatible_envelope_schema(self) -> dict:
        """Adapt the envelope fixture while the production contract is frozen separately."""
        schema = json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))
        review_def = self.review_schema["$defs"]["review"]
        checklist = review_def.get("properties", {}).get("checklist")
        if checklist is not None:
            schema["$defs"]["checklist"] = copy.deepcopy(
                self.review_schema["$defs"]["checklist"]
            )
            schema["properties"]["review"]["properties"]["checklist"] = {
                "$ref": "#/$defs/checklist"
            }
            required = schema["properties"]["review"]["required"]
            if "checklist" not in required:
                required.append("checklist")
        return schema

    def _current_review(self) -> dict:
        checklist_schema = self.review_schema["$defs"]["review"]["properties"].get(
            "checklist"
        )
        review = {
            "entryId": self.source.entry_id,
            "verdict": "revise",
            "issues": [
                {
                    "code": "summary_modality_negation",
                    "field": "modernRetrievalSummary",
                    "sourceExcerpt": "未曾发生",
                    "correctionHint": "保留梦境和否定边界。",
                }
            ],
        }
        if checklist_schema is not None:
            properties = self.review_schema["$defs"]["checklist"]["properties"]
            review["checklist"] = {name: True for name in properties}
            review["checklist"]["modernRetrievalSummary"] = False
        errors = list(self.review_validator.iter_errors({"reviews": [review]}))
        if errors:
            raise AssertionError(f"fixture review does not match current schema: {errors[0]}")
        return review

    def _create_source_db(self) -> None:
        conn = sqlite3.connect(self.source_db)
        try:
            conn.execute(
                """
                CREATE TABLE entries(
                    entry_id TEXT PRIMARY KEY, source_work_id TEXT,
                    source_work_title TEXT, source_work_period TEXT, volume TEXT,
                    source_locator TEXT, entry_ordinal INTEGER, title TEXT,
                    source_url TEXT, char_count INTEGER, text TEXT,
                    extracted_text_sha256 TEXT, dedupe_status TEXT,
                    canonical_entry_id TEXT, runtime_eligible INTEGER
                )
                """
            )
            conn.execute(
                "INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.source.entry_id,
                    self.source.source_work_id,
                    self.source.source_work_title,
                    self.source.source_work_period,
                    self.source.volume,
                    self.source.source_locator,
                    self.source.entry_ordinal,
                    self.source.title,
                    self.source.source_url,
                    self.source.char_count,
                    self.source.text,
                    self.source.extracted_text_sha256,
                    "canonical",
                    self.source.entry_id,
                    1,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _create_annotation_db(self) -> None:
        conn = sqlite3.connect(self.annotation_db)
        try:
            conn.execute(
                """
                CREATE TABLE annotation_jobs(
                    entry_id TEXT PRIMARY KEY, source_text_sha256 TEXT,
                    source_work_id TEXT, source_work_title TEXT, title TEXT,
                    char_count INTEGER, status TEXT, annotation_json TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO annotation_jobs VALUES(?,?,?,?,?,?,?,?)",
                (
                    self.source.entry_id,
                    self.source.extracted_text_sha256,
                    self.source.source_work_id,
                    self.source.source_work_title,
                    self.source.title,
                    self.source.char_count,
                    "valid",
                    canonical_json(self.base),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _create_review_db(self) -> None:
        conn = sqlite3.connect(self.review_db)
        try:
            conn.execute(
                """
                CREATE TABLE semantic_review_jobs(
                    entry_id TEXT PRIMARY KEY, source_text_sha256 TEXT,
                    candidate_annotation_sha256 TEXT, source_work_id TEXT,
                    source_work_title TEXT, title TEXT, char_count INTEGER,
                    status TEXT, verdict TEXT, review_json TEXT, issues_json TEXT,
                    model TEXT, provider_reported_model TEXT, prompt_version TEXT,
                    prompt_sha256 TEXT, review_schema_sha256 TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO semantic_review_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.source.entry_id,
                    self.source.extracted_text_sha256,
                    self.candidate.base_candidate_sha256,
                    self.source.source_work_id,
                    self.source.source_work_title,
                    self.source.title,
                    self.source.char_count,
                    "revise",
                    "revise",
                    canonical_json(self.review),
                    canonical_json(self.review["issues"]),
                    "deepseek-v4-flash",
                    "deepseek-v4-flash",
                    reviewer.PROMPT_VERSION,
                    sha256_text(reviewer.SYSTEM_PROMPT),
                    validator.sha256_file(REVIEW_SCHEMA_PATH),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _create_effective_db(self, *, with_repair: bool) -> None:
        conn = repair.init_effective_db(self.effective_db)
        try:
            if not with_repair:
                return
            canonical_sha = validator.sha256_file(CANONICAL_SCHEMA_PATH)
            effective_sha = sha256_text(canonical_json(self.effective_schema))
            envelope_sha = validator.sha256_file(self.envelope_schema_path)
            repair.enqueue_candidates(
                conn,
                [self.candidate],
                model="deepseek-v4-flash",
                canonical_schema_sha256=canonical_sha,
                effective_schema_sha256=effective_sha,
                envelope_schema_sha256=envelope_sha,
            )
            result = repair.parse_and_normalize_repairs(
                {"annotations": [raw_annotation(self.source)]},
                [self.candidate],
                provider_meta={
                    "response_id": "resp-fixture",
                    "model": "deepseek-v4-flash",
                    "requested_model": "deepseek-v4-flash",
                    "usage": {"total_tokens": 10},
                },
                canonical_schema_validator=self.canonical_validator,
                effective_schema_validator=self.effective_validator,
                envelope_schema_validator=self.envelope_validator,
                canonical_schema_sha256=canonical_sha,
                effective_schema_sha256=effective_sha,
                envelope_schema_sha256=envelope_sha,
            )
            repair.mark_valid(
                conn,
                result,
                [self.candidate],
                {"annotations": [raw_annotation(self.source)]},
                {
                    "response_id": "resp-fixture",
                    "model": "deepseek-v4-flash",
                    "requested_model": "deepseek-v4-flash",
                    "usage": {"total_tokens": 10},
                },
            )
        finally:
            conn.close()

    def validate(self, **overrides: object) -> validator.ValidationReport:
        kwargs: dict[str, object] = {
            "source_db": self.source_db,
            "annotation_db": self.annotation_db,
            "review_db": self.review_db,
            "effective_db": self.effective_db,
            "canonical_schema": CANONICAL_SCHEMA_PATH,
            "review_schema": REVIEW_SCHEMA_PATH,
            "envelope_schema": self.envelope_schema_path,
        }
        kwargs.update(overrides)
        return validator.validate_databases(**kwargs)  # type: ignore[arg-type]


class EffectiveRepairValidatorTests(unittest.TestCase):
    def test_valid_effective_repair_passes_all_hash_and_semantic_gates(self) -> None:
        fixture = FourLedgerFixture()
        try:
            report = fixture.validate(
                require_count=1, require_all_reviewed_revisions=True
            )
            self.assertTrue(report.passed, report.errors)
            self.assertEqual(1, report.checked_repairs)
            self.assertEqual(1, report.required_revision_count)
        finally:
            fixture.close()

    def test_source_drift_fails(self) -> None:
        fixture = FourLedgerFixture()
        try:
            conn = sqlite3.connect(fixture.source_db)
            conn.execute(
                "UPDATE entries SET text=text || '异文' WHERE entry_id=?",
                (fixture.source.entry_id,),
            )
            conn.commit()
            conn.close()
            report = fixture.validate()
            self.assertFalse(report.passed)
            self.assertTrue(any("C1 source hash is stale" in item for item in report.errors))
        finally:
            fixture.close()

    def test_base_candidate_drift_fails(self) -> None:
        fixture = FourLedgerFixture()
        try:
            changed = copy.deepcopy(fixture.base)
            changed["annotation_record"]["annotation_meta"]["overall_confidence"] = "low"
            conn = sqlite3.connect(fixture.annotation_db)
            conn.execute(
                "UPDATE annotation_jobs SET annotation_json=? WHERE entry_id=?",
                (canonical_json(changed), fixture.source.entry_id),
            )
            conn.commit()
            conn.close()
            report = fixture.validate()
            self.assertFalse(report.passed)
            self.assertTrue(
                any("base candidate hash closure failed" in item for item in report.errors)
            )
        finally:
            fixture.close()

    def test_review_v3_prompt_drift_fails(self) -> None:
        fixture = FourLedgerFixture()
        try:
            conn = sqlite3.connect(fixture.review_db)
            conn.execute(
                "UPDATE semantic_review_jobs SET prompt_version='obsolete-v2' WHERE entry_id=?",
                (fixture.source.entry_id,),
            )
            conn.commit()
            conn.close()
            report = fixture.validate()
            self.assertFalse(report.passed)
            self.assertTrue(
                any("review prompt_version is not current" in item for item in report.errors)
            )
        finally:
            fixture.close()

    def test_envelope_tampering_fails(self) -> None:
        fixture = FourLedgerFixture()
        try:
            conn = sqlite3.connect(fixture.effective_db)
            raw = conn.execute(
                "SELECT effective_envelope_json FROM effective_repair_jobs WHERE entry_id=?",
                (fixture.source.entry_id,),
            ).fetchone()[0]
            envelope = json.loads(raw)
            envelope["research_ready"] = True
            conn.execute(
                "UPDATE effective_repair_jobs SET effective_envelope_json=? WHERE entry_id=?",
                (canonical_json(envelope), fixture.source.entry_id),
            )
            conn.commit()
            conn.close()
            report = fixture.validate()
            self.assertFalse(report.passed)
            self.assertTrue(any("research readiness" in item for item in report.errors))
        finally:
            fixture.close()

    def test_pending_repair_fails_default_gate(self) -> None:
        fixture = FourLedgerFixture()
        try:
            conn = sqlite3.connect(fixture.effective_db)
            conn.execute(
                "UPDATE effective_repair_jobs SET status='queued' WHERE entry_id=?",
                (fixture.source.entry_id,),
            )
            conn.commit()
            conn.close()
            report = fixture.validate()
            self.assertFalse(report.passed)
            self.assertTrue(any("non-final states" in item for item in report.errors))
        finally:
            fixture.close()

    def test_empty_repair_sidecar_passes_structural_gate(self) -> None:
        fixture = FourLedgerFixture(with_repair=False)
        try:
            report = fixture.validate(require_count=0)
            self.assertTrue(report.passed, report.errors)
        finally:
            fixture.close()

    def test_strict_revision_coverage_rejects_empty_sidecar(self) -> None:
        fixture = FourLedgerFixture(with_repair=False)
        try:
            report = fixture.validate(require_all_reviewed_revisions=True)
            self.assertFalse(report.passed)
            self.assertTrue(any("lack effective repairs" in item for item in report.errors))
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()
