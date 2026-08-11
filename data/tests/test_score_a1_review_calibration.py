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

import score_a1_review_calibration as scorer  # noqa: E402


def checklist(*false_fields: str) -> dict[str, bool]:
    false_set = set(false_fields)
    return {field: field not in false_set for field in scorer.CHECKLIST_FIELDS}


class CalibrationScorerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.gold_path = root / "gold.json"
        self.review_db = root / "reviews.sqlite3"
        self.source_db = root / "source.sqlite3"
        self.annotation_db = root / "annotations.sqlite3"
        self.ids = ["e1", "e2", "e3"]
        self.source_texts = {entry_id: f"原文-{entry_id}" for entry_id in self.ids}
        self.source_hashes = {
            entry_id: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for entry_id, text in self.source_texts.items()
        }
        self.annotations = {
            entry_id: {
                "annotation_record": {
                    "unit": {
                        "unit_id": entry_id,
                        "source_text_sha256": self.source_hashes[entry_id],
                    },
                    "source_profile": {
                        "source_text_sha256": self.source_hashes[entry_id],
                        "source_text": self.source_texts[entry_id],
                    },
                }
            }
            for entry_id in self.ids
        }
        self.candidate_hashes = {
            entry_id: hashlib.sha256(
                scorer.canonical_json(annotation).encode("utf-8")
            ).hexdigest()
            for entry_id, annotation in self.annotations.items()
        }
        self._write_gold()
        self._write_review_db()
        self._write_current_dbs()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_gold(self) -> None:
        ids_sha = hashlib.sha256(
            "".join(f"{entry_id}\n" for entry_id in self.ids).encode("utf-8")
        ).hexdigest()
        payload = {
            "metadata": {
                "benchmark_id": "unit-test",
                "benchmark_kind": "single_developer_development_audit",
                "formal_human_gold_standard": False,
                "research_ready": False,
                "entry_count": 3,
                "clear_error_count": 2,
                "true_pass_count": 1,
                "entry_ids_sha256": ids_sha,
            },
            "checklist_fields": list(scorer.CHECKLIST_FIELDS),
            "entry_ids": self.ids,
            "entries": [
                {
                    "entry_id": "e1",
                    "title": "甲",
                    "source_text_sha256": self.source_hashes["e1"],
                    "candidate_annotation_sha256": self.candidate_hashes["e1"],
                    "gold_has_clear_error": True,
                    "must_detect_fields": ["modernRetrievalSummary"],
                    "must_not_flag_fields": [],
                    "concise_notes": "摘要主体错误。",
                    "issue_expectations": [
                        {
                            "field": "modernRetrievalSummary",
                            "direction": "contradiction",
                            "expectation": "must_detect",
                            "concise_fact": "摘要主体错误。",
                        }
                    ],
                },
                {
                    "entry_id": "e2",
                    "title": "乙",
                    "source_text_sha256": self.source_hashes["e2"],
                    "candidate_annotation_sha256": self.candidate_hashes["e2"],
                    "gold_has_clear_error": True,
                    "must_detect_fields": ["plotBeats"],
                    "must_not_flag_fields": [],
                    "concise_notes": "漏掉结局节点。",
                    "issue_expectations": [
                        {
                            "field": "plotBeats",
                            "direction": "omission",
                            "expectation": "must_detect",
                            "concise_fact": "漏掉结局节点。",
                        }
                    ],
                },
                {
                    "entry_id": "e3",
                    "title": "丙",
                    "source_text_sha256": self.source_hashes["e3"],
                    "candidate_annotation_sha256": self.candidate_hashes["e3"],
                    "gold_has_clear_error": False,
                    "must_detect_fields": [],
                    "must_not_flag_fields": list(scorer.CHECKLIST_FIELDS),
                    "concise_notes": "逐字段审计未发现明确错误。",
                    "issue_expectations": [],
                },
            ],
        }
        self.gold_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _write_review_db(self) -> None:
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                CREATE TABLE semantic_review_jobs(
                    entry_id TEXT PRIMARY KEY,
                    source_text_sha256 TEXT NOT NULL,
                    title TEXT NOT NULL,
                    candidate_annotation_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verdict TEXT,
                    review_json TEXT
                )
                """
            )
            rows = [
                ("e1", "甲", "revise", "modernRetrievalSummary", "test_issue"),
                ("e2", "乙", "pass", None, None),
                ("e3", "丙", "revise", "autoSafetyScreen.flags", "safety_overreach"),
            ]
            for entry_id, title, verdict, field, code in rows:
                issues = []
                if field:
                    issues = [
                        {
                            "code": code,
                            "field": field,
                            "sourceExcerpt": "测试原文",
                            "correctionHint": "测试修订",
                        }
                    ]
                review = {
                    "entryId": entry_id,
                    "verdict": verdict,
                    "checklist": checklist(*(field,) if field else ()),
                    "issues": issues,
                }
                conn.execute(
                    "INSERT INTO semantic_review_jobs VALUES(?,?,?,?,?,?,?)",
                    (
                        entry_id,
                        self.source_hashes[entry_id],
                        title,
                        self.candidate_hashes[entry_id],
                        verdict,
                        verdict,
                        json.dumps(review, ensure_ascii=False),
                    ),
                )

    def _write_current_dbs(self) -> None:
        with sqlite3.connect(self.source_db) as conn:
            conn.execute(
                """
                CREATE TABLE entries(
                    entry_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    extracted_text_sha256 TEXT NOT NULL,
                    dedupe_status TEXT NOT NULL,
                    runtime_eligible INTEGER NOT NULL
                )
                """
            )
            for entry_id, title in zip(self.ids, ("甲", "乙", "丙")):
                conn.execute(
                    "INSERT INTO entries VALUES(?,?,?,?,?,?)",
                    (
                        entry_id,
                        title,
                        self.source_texts[entry_id],
                        self.source_hashes[entry_id],
                        "canonical",
                        1,
                    ),
                )
        with sqlite3.connect(self.annotation_db) as conn:
            conn.execute(
                """
                CREATE TABLE annotation_jobs(
                    entry_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_text_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    annotation_json TEXT NOT NULL
                )
                """
            )
            for entry_id, title in zip(self.ids, ("甲", "乙", "丙")):
                conn.execute(
                    "INSERT INTO annotation_jobs VALUES(?,?,?,?,?)",
                    (
                        entry_id,
                        title,
                        self.source_hashes[entry_id],
                        "valid",
                        json.dumps(self.annotations[entry_id], ensure_ascii=False),
                    ),
                )

    def test_scores_required_detection_and_false_positive_counts(self) -> None:
        metadata, gold = scorer.load_gold(self.gold_path)
        rows = scorer.load_reviews(self.review_db, set(gold))
        result = scorer.score_calibration(metadata, gold, rows)
        field_metrics = result["metrics"]["field_level"]
        self.assertEqual(
            field_metrics["record_recall"],
            {"numerator": 1, "denominator": 2, "rate": 0.5},
        )
        self.assertEqual(
            field_metrics["revise_precision"],
            {"numerator": 1, "denominator": 2, "rate": 0.5},
        )
        self.assertEqual(field_metrics["pure_fp_revise_count"], 1)
        self.assertEqual(result["field_level_pure_fp_revise"][0]["entry_id"], "e3")
        self.assertEqual(result["false_pass"][0]["entry_id"], "e2")

    def test_rejects_review_candidate_and_source_hash_drift(self) -> None:
        metadata, gold = scorer.load_gold(self.gold_path)
        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                "UPDATE semantic_review_jobs SET candidate_annotation_sha256=? WHERE entry_id='e1'",
                ("f" * 64,),
            )
        rows = scorer.load_reviews(self.review_db, set(gold))
        with self.assertRaisesRegex(scorer.CalibrationError, "candidate hash mismatch"):
            scorer.score_calibration(metadata, gold, rows)

        with sqlite3.connect(self.review_db) as conn:
            conn.execute(
                """
                UPDATE semantic_review_jobs
                SET candidate_annotation_sha256=?, source_text_sha256=?
                WHERE entry_id='e1'
                """,
                (self.candidate_hashes["e1"], "f" * 64),
            )
        rows = scorer.load_reviews(self.review_db, set(gold))
        with self.assertRaisesRegex(scorer.CalibrationError, "source hash mismatch"):
            scorer.score_calibration(metadata, gold, rows)

    def test_rejects_id_set_and_order_drift(self) -> None:
        _, gold = scorer.load_gold(self.gold_path)
        with sqlite3.connect(self.review_db) as conn:
            conn.execute("DELETE FROM semantic_review_jobs WHERE entry_id='e3'")
        with self.assertRaisesRegex(scorer.CalibrationError, "ID set mismatch"):
            scorer.load_reviews(self.review_db, set(gold))

        payload = json.loads(self.gold_path.read_text(encoding="utf-8"))
        payload["entry_ids"].reverse()
        payload["metadata"]["entry_ids_sha256"] = hashlib.sha256(
            "".join(f"{entry_id}\n" for entry_id in payload["entry_ids"]).encode("utf-8")
        ).hexdigest()
        self.gold_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(scorer.CalibrationError, "ordered IDs"):
            scorer.load_gold(self.gold_path)

    def test_current_inputs_reject_source_and_candidate_drift(self) -> None:
        _, gold = scorer.load_gold(self.gold_path)
        validation = scorer.validate_current_inputs(
            self.source_db, self.annotation_db, gold
        )
        self.assertTrue(validation["source_hashes_current"])
        self.assertTrue(validation["candidate_hashes_current"])

        with sqlite3.connect(self.source_db) as conn:
            conn.execute("UPDATE entries SET text='已漂移' WHERE entry_id='e1'")
        with self.assertRaisesRegex(scorer.CalibrationError, "extracted text hash"):
            scorer.validate_current_inputs(self.source_db, self.annotation_db, gold)

        with sqlite3.connect(self.source_db) as conn:
            conn.execute(
                "UPDATE entries SET text=? WHERE entry_id='e1'",
                (self.source_texts["e1"],),
            )
        with sqlite3.connect(self.annotation_db) as conn:
            changed = dict(self.annotations["e1"])
            changed["extra"] = True
            conn.execute(
                "UPDATE annotation_jobs SET annotation_json=? WHERE entry_id='e1'",
                (json.dumps(changed, ensure_ascii=False),),
            )
        with self.assertRaisesRegex(scorer.CalibrationError, "candidate hash drift"):
            scorer.validate_current_inputs(self.source_db, self.annotation_db, gold)

    def test_same_direction_mixed_facts_are_excluded_not_double_scored(self) -> None:
        payload = json.loads(self.gold_path.read_text(encoding="utf-8"))
        payload["entries"][0]["issue_expectations"].extend(
            [
                {
                    "field": "modernRetrievalSummary",
                    "direction": "overreach",
                    "expectation": "must_detect",
                    "concise_fact": "一个事实过度外推。",
                },
                {
                    "field": "modernRetrievalSummary",
                    "direction": "overreach",
                    "expectation": "must_not_detect",
                    "concise_fact": "另一个事实不应删除。",
                },
            ]
        )
        self.gold_path.write_text(json.dumps(payload), encoding="utf-8")
        metadata, gold = scorer.load_gold(self.gold_path)
        rows = scorer.load_reviews(self.review_db, set(gold))
        result = scorer.score_calibration(metadata, gold, rows)
        ambiguities = result["direction_aware_unscored_ambiguities"]
        self.assertEqual(ambiguities[0]["entry_id"], "e1")
        self.assertIn(
            "modernRetrievalSummary:overreach",
            ambiguities[0]["field_directions"],
        )

    def test_gold_must_use_exact_checklist_names(self) -> None:
        payload = json.loads(self.gold_path.read_text(encoding="utf-8"))
        payload["entries"][0]["must_detect_fields"] = ["summary"]
        self.gold_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(scorer.CalibrationError, "unknown fields"):
            scorer.load_gold(self.gold_path)


class BundledGold36Tests(unittest.TestCase):
    def test_gold36_is_exact_current_and_reproduces_strict_v3_audit(self) -> None:
        gold_path = (
            PROJECT_ROOT
            / "data"
            / "audits"
            / "a1_semantic_review_gold36_v1.json"
        )
        ids_path = (
            PROJECT_ROOT
            / "data"
            / "audits"
            / "a1_review_calibration_v2_36.ids.txt"
        )
        review_db = (
            PROJECT_ROOT
            / "data"
            / "corpus"
            / "a1_semantic_reviews_v3_pilot36"
            / "reviews.sqlite3"
        )
        metadata, gold = scorer.load_gold(gold_path)
        expected_ids = [
            line.strip()
            for line in ids_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(list(gold), expected_ids)
        self.assertEqual(metadata["clear_error_count"], 32)
        self.assertEqual(metadata["true_pass_count"], 4)

        current = scorer.validate_current_inputs(
            scorer.DEFAULT_SOURCE_DB,
            scorer.DEFAULT_ANNOTATION_DB,
            gold,
        )
        self.assertEqual(current["validated_entry_count"], 36)
        rows = scorer.load_reviews(review_db, set(gold))
        result = scorer.score_calibration(metadata, gold, rows)
        strict = result["metrics"]["current_v3_strict_record_audit"]
        self.assertTrue(strict["available"])
        self.assertEqual(
            strict["record_recall"],
            {"numerator": 16, "denominator": 32, "rate": 0.5},
        )
        self.assertEqual(
            strict["revise_precision"],
            {"numerator": 16, "denominator": 21, "rate": 16 / 21},
        )
        self.assertEqual(strict["pure_fp_revise_count"], 5)
        self.assertEqual(strict["false_pass_count"], 11)
        self.assertEqual(
            {item["title"] for item in result["current_v3_strict_pure_fp_revise"]},
            {"洪貞", "獵戶說虎", "愛奴", "牛成章", "高駢"},
        )


if __name__ == "__main__":
    unittest.main()
