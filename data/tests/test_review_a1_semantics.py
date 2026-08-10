from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "data"))

import review_a1_semantics as reviewer  # noqa: E402
from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402


SCHEMA_PATH = PROJECT_ROOT / "contracts" / "a1-semantic-review.schema.json"


def schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def candidate_stub(
    *,
    entry_id: str = "c1ws_0123456789abcdef01234567",
    source_text: str = "某甲梦见故人来访，醒后才知此事未曾发生。",
    char_count: int | None = None,
    flags: list[str] | None = None,
    locked_flags: tuple[str, ...] = (),
    candidate_hash: str = "b" * 64,
) -> reviewer.ReviewCandidate:
    projection = {
        "modernRetrievalSummary": "某甲在梦中见到故人，醒后知道事情并未发生。",
        "narrativeSufficiency": "sufficient",
        "narrativeSufficiencyReason": "原文构成一个完整事件。",
        "keyEntities": [
            {"name": "某甲", "role": "梦者", "evidence_ids": ["ev01"]}
        ],
        "plotBeats": [
            {"type": "trigger", "text": "梦见故人", "evidence_ids": ["ev01"]}
        ],
        "motifTerms": ["梦兆"],
        "summaryEvidenceIds": ["ev01"],
        "lifeContext": [],
        "narrativeArc": {
            "trigger": "某甲梦见故人",
            "conflictTypes": ["knowledge_uncertainty"],
            "agencyModes": ["unknown"],
            "endingMode": "unresolved",
        },
        "autoSafetyScreen": {
            "status": "auto_screened",
            "flags": flags if flags is not None else ["none_identified"],
            "interpretationRisks": ["none_identified"],
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
    return reviewer.ReviewCandidate(
        entry_id=entry_id,
        source_work_id="work-1",
        source_work_title="测试古籍",
        title="测试条目",
        source_locator="卷一",
        entry_ordinal=1,
        char_count=len(source_text) if char_count is None else char_count,
        source_text=source_text,
        source_text_sha256=reviewer.sha256_text(source_text),
        candidate_annotation_sha256=candidate_hash,
        candidate_projection=projection,
        locked_safety_flags=locked_flags,
    )


def issue(
    code: str = "summary_modality_negation",
    field: str = "modernRetrievalSummary",
    excerpt: str = "未曾发生",
) -> dict:
    return {
        "code": code,
        "field": field,
        "sourceExcerpt": excerpt,
        "correctionHint": "保留原文的否定和梦境边界。",
    }


def checklist(*false_fields: str) -> dict[str, bool]:
    false_set = set(false_fields)
    return {field: field not in false_set for field in reviewer.CHECKLIST_FIELDS}


class ReviewContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = candidate_stub()
        self.validator = schema_validator()

    def parse(self, response: dict) -> dict[str, dict]:
        return reviewer.parse_reviews(
            response, [self.candidate], schema_validator=self.validator
        )

    def test_pass_requires_empty_issues(self) -> None:
        parsed = self.parse(
            {
                "reviews": [
                    {
                        "entryId": self.candidate.entry_id,
                        "verdict": "pass",
                        "checklist": checklist(),
                        "issues": [],
                    }
                ]
            }
        )
        self.assertEqual("pass", parsed[self.candidate.entry_id]["verdict"])

        with self.assertRaises(reviewer.ReviewValidationError):
            self.parse(
                {
                    "reviews": [
                        {
                            "entryId": self.candidate.entry_id,
                            "verdict": "pass",
                            "checklist": checklist(),
                            "issues": [issue()],
                        }
                    ]
                }
            )

    def test_entry_id_file_preserves_exact_calibration_order(self) -> None:
        first = candidate_stub(entry_id="c1ws_111111111111111111111111")
        second = candidate_stub(entry_id="c1ws_222222222222222222222222")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cohort.ids.txt"
            path.write_text(
                f"# stable cohort\n{second.entry_id}\n{first.entry_id}\n",
                encoding="utf-8",
            )
            selected = reviewer.select_candidates_from_id_file(
                [first, second], path
            )

        self.assertEqual(
            [second.entry_id, first.entry_id],
            [candidate.entry_id for candidate in selected],
        )

    def test_issue_quote_maps_simplified_punctuation_drift_to_exact_source(self) -> None:
        source = "見一獰鬼，面翠色，齒巉巉如鋸。鋪人皮於榻上，執彩筆而繪之。"
        candidate = candidate_stub(source_text=source)
        response = {
            "reviews": [
                {
                    "entryId": candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist("narrativeArc.conflictTypes"),
                    "issues": [
                        {
                            "code": "label_conflict",
                            "field": "narrativeArc.conflictTypes",
                            "sourceExcerpt": (
                                "見一獰鬼，面翠色，齒巉巉如鋸；"
                                "鋪人皮于榻上，執彩筆而繪之"
                            ),
                            "correctionHint": "补入原文明确呈现的超自然冲突。",
                        }
                    ],
                }
            ]
        }

        parsed = reviewer.parse_reviews(
            response, [candidate], schema_validator=self.validator
        )
        excerpt = parsed[candidate.entry_id]["issues"][0]["sourceExcerpt"]

        assert excerpt in source
        assert excerpt in {"見一獰鬼", "鋪人皮於榻上"} or len(excerpt) >= 4

    def test_checklist_is_complete_and_matches_issue_fields(self) -> None:
        missing_checklist = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "pass",
                    "issues": [],
                }
            ]
        }
        with self.assertRaises(reviewer.ReviewValidationError):
            self.parse(missing_checklist)

        pass_with_false_field = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "pass",
                    "checklist": checklist("modernRetrievalSummary"),
                    "issues": [],
                }
            ]
        }
        with self.assertRaises(reviewer.ReviewValidationError):
            self.parse(pass_with_false_field)

        issue_marked_true = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist(),
                    "issues": [issue()],
                }
            ]
        }
        with self.assertRaisesRegex(
            reviewer.ReviewValidationError, "checklist and issue fields"
        ):
            self.parse(issue_marked_true)

        false_without_issue = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist(
                        "modernRetrievalSummary", "plotBeats"
                    ),
                    "issues": [issue()],
                }
            ]
        }
        with self.assertRaisesRegex(
            reviewer.ReviewValidationError, "false fields without issues"
        ):
            self.parse(false_without_issue)

    def test_revise_rejects_soft_or_correct_but_issue_hints(self) -> None:
        response = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist("modernRetrievalSummary"),
                    "issues": [
                        {
                            **issue(),
                            "correctionHint": "当前标注合理，可保留，无需修改。",
                        }
                    ],
                }
            ]
        }
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "soft"):
            self.parse(response)

        response["reviews"][0]["verdict"] = "uncertain"
        response["reviews"][0]["issues"][0]["correctionHint"] = (
            "语境残缺，需确认否定范围。"
        )
        self.parse(response)

    def test_safety_status_and_uncertainty_issue_fields_are_explicit(self) -> None:
        response = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist(
                        "autoSafetyScreen.status",
                        "autoSafetyScreen.uncertainties",
                    ),
                    "issues": [
                        issue(
                            code="safety_status_mismatch",
                            field="autoSafetyScreen.status",
                            excerpt="醒后才知",
                        ),
                        issue(
                            code="safety_uncertainty_mismatch",
                            field="autoSafetyScreen.uncertainties",
                            excerpt="未曾发生",
                        ),
                    ],
                }
            ]
        }
        parsed = self.parse(response)
        self.assertEqual("revise", parsed[self.candidate.entry_id]["verdict"])


    def test_revise_and_uncertain_require_issues(self) -> None:
        for verdict in ("revise", "uncertain"):
            with self.subTest(verdict=verdict):
                with self.assertRaises(reviewer.ReviewValidationError):
                    self.parse(
                        {
                            "reviews": [
                                {
                                    "entryId": self.candidate.entry_id,
                                    "verdict": verdict,
                                    "checklist": checklist(),
                                    "issues": [],
                                }
                            ]
                        }
                    )

    def test_issue_excerpt_must_be_exact_and_field_drift_is_canonicalized(self) -> None:
        valid = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist("modernRetrievalSummary"),
                    "issues": [issue()],
                }
            ]
        }
        self.parse(valid)

        invalid_excerpt = json.loads(json.dumps(valid, ensure_ascii=False))
        invalid_excerpt["reviews"][0]["issues"][0]["sourceExcerpt"] = "并未发生"
        with self.assertRaisesRegex(
            reviewer.ReviewValidationError, "not exact source text"
        ):
            self.parse(invalid_excerpt)

        invalid_field = json.loads(json.dumps(valid, ensure_ascii=False))
        invalid_field["reviews"][0]["issues"][0]["field"] = "plotBeats"
        invalid_field["reviews"][0]["checklist"] = checklist("plotBeats")
        parsed = self.parse(invalid_field)
        canonical_issue = parsed[self.candidate.entry_id]["issues"][0]
        self.assertEqual("plot_beat_mismatch", canonical_issue["code"])
        self.assertEqual("plotBeats", canonical_issue["field"])

    def test_ids_must_exactly_match_batch(self) -> None:
        second = candidate_stub(entry_id="c1ws_89abcdef0123456789abcdef")
        response = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "pass",
                    "checklist": checklist(),
                    "issues": [],
                }
            ]
        }
        with self.assertRaisesRegex(
            reviewer.ReviewValidationError, "IDs do not exactly match"
        ):
            reviewer.parse_reviews(
                response,
                [self.candidate, second],
                schema_validator=self.validator,
            )

    def test_schema_rejects_unknown_issue_code_and_extra_properties(self) -> None:
        response = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist("modernRetrievalSummary"),
                    "issues": [{**issue(), "code": "invented_code"}],
                    "explanation": "not allowed",
                }
            ]
        }
        with self.assertRaises(reviewer.ReviewValidationError):
            self.parse(response)

    def test_extended_semantic_fields_are_supported(self) -> None:
        extended = [
            ("narrative_sufficiency_mismatch", "narrativeSufficiency"),
            ("key_entity_mismatch", "keyEntities"),
            ("plot_beat_mismatch", "plotBeats"),
            ("motif_mismatch", "motifTerms"),
            ("trigger_mismatch", "narrativeArc.trigger"),
            (
                "interpretation_risk_mismatch",
                "autoSafetyScreen.interpretationRisks",
            ),
            ("evidence_support_mismatch", "evidence.supports"),
        ]
        response = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist(*(field for _, field in extended)),
                    "issues": [
                        issue(code=code, field=field, excerpt="某甲梦见故人来访")
                        for code, field in extended
                    ],
                }
            ]
        }
        parsed = self.parse(response)
        self.assertEqual(len(extended), len(parsed[self.candidate.entry_id]["issues"]))

    def test_safety_overreach_requires_an_unlocked_candidate_flag(self) -> None:
        response = {
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "verdict": "revise",
                    "checklist": checklist("autoSafetyScreen.flags"),
                    "issues": [
                        issue(
                            code="safety_overreach",
                            field="autoSafetyScreen.flags",
                            excerpt="某甲梦见故人来访",
                        )
                    ],
                }
            ]
        }
        locked_only = candidate_stub(flags=["death"], locked_flags=("death",))
        with self.assertRaisesRegex(
            reviewer.ReviewValidationError, "non-locked candidate flag"
        ):
            reviewer.parse_reviews(
                response,
                [locked_only],
                schema_validator=self.validator,
            )

        partly_unlocked = candidate_stub(
            flags=["death", "supernatural_horror"], locked_flags=("death",)
        )
        reviewer.parse_reviews(
            response,
            [partly_unlocked],
            schema_validator=self.validator,
        )


class SafetyAndScopeGuardsTests(unittest.TestCase):
    def test_missing_locked_safety_flag_fails_before_provider_use(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "sexual_violence"):
            reviewer.ensure_locked_safety_flags_present(
                entry_id="c1ws_0123456789abcdef01234567",
                candidate_flags=["none_identified"],
                locked_flags=[
                    "sexual_violence",
                    "sexual_content",
                    "coercion_or_abuse",
                ],
            )

    def test_full_scope_confirmation_uses_corpus_count_not_current_valid_count(self) -> None:
        reviewer.enforce_live_scope_confirmation(
            live=True, selected_count=120, corpus_count=12353, confirmed=False
        )
        with self.assertRaisesRegex(SystemExit, "confirm-full-run"):
            reviewer.enforce_live_scope_confirmation(
                live=True, selected_count=12353, corpus_count=12353, confirmed=False
            )
        reviewer.enforce_live_scope_confirmation(
            live=True, selected_count=12353, corpus_count=12353, confirmed=True
        )

    def test_input_and_output_paths_must_be_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "shared.sqlite3"
            with self.assertRaisesRegex(SystemExit, "source-db=sidecar-db"):
                reviewer.enforce_disjoint_paths(
                    source_db=shared,
                    annotation_db=root / "annotations.sqlite3",
                    sidecar_db=shared,
                    schema=root / "schema.json",
                    manifest=root / "manifest.json",
                )


class PromptV3RegressionAnchorTests(unittest.TestCase):
    def test_prompt_requires_field_by_field_bidirectional_review(self) -> None:
        self.assertEqual(
            "mengdie-a1-semantic-review-v3-nonthinking-field-checklist",
            reviewer.PROMPT_VERSION,
        )
        self.assertEqual(reviewer.PROMPT_VERSION, reviewer.REVIEW_VERSION)
        for anchor in (
            "逐项读取 candidate 的实际值",
            "通读完整 sourceText",
            "双向核验",
            "空数组和 verdict=pass 尤其要反向查漏",
            "只有字段完全不需要修改才写 true",
            "每个 false 字段必须至少有一个同字段 issue",
        ):
            self.assertIn(anchor, reviewer.SYSTEM_PROMPT)

    def test_prompt_pins_v2_false_positive_and_false_negative_boundaries(self) -> None:
        for anchor in (
            "不得把 candidate 已经写出的事实再次报为遗漏",
            "合法羁押仍成立",
            "双方同意或篇幅短仍成立",
            "惩戒、自卫",
            "不要求想死",
            "阻挡、叫阵、攻击和自卫",
            "乞儿成为将军",
            "任职、致富/继承财富、无后、改过、筑成工程、长期隐居",
            "附记、次要故事和次要人物",
            "只把 evidence.supports 设为 false",
            "不得因此把正确的全局标签字段设为 false",
        ):
            self.assertIn(anchor, reviewer.SYSTEM_PROMPT)


class LockedSafetyFlagTests(unittest.TestCase):
    def test_only_class_a_and_cross_labels_are_locked(self) -> None:
        self.assertEqual((), reviewer.deterministic_locked_safety_flags("此人患病多年。"))
        self.assertEqual(
            ("coercion_or_abuse", "sexual_content", "sexual_violence"),
            reviewer.deterministic_locked_safety_flags("恶人强奸女子。"),
        )
        self.assertEqual(
            ("death", "self_harm_or_suicide"),
            reviewer.deterministic_locked_safety_flags("其人自刎而死。"),
        )


class PackingAndInputTests(unittest.TestCase):
    def test_four_workers_share_one_total_rpm_budget(self) -> None:
        share = reviewer.per_worker_rpm(20, 4)
        self.assertEqual(5, share)
        self.assertEqual(20, share * 4)
        self.assertEqual(0, reviewer.per_worker_rpm(20, 0))

    def test_max_six_and_4000_chars_with_complete_oversize_single(self) -> None:
        small = [
            candidate_stub(
                entry_id=f"c1ws_{index:024x}",
                source_text="甲" * 600,
                char_count=600,
            )
            for index in range(1, 8)
        ]
        long_text = "乙" * 7584
        oversize = candidate_stub(
            entry_id="c1ws_ffffffffffffffffffffffff",
            source_text=long_text,
            char_count=len(long_text),
        )
        batches = reviewer.pack_review_batches(
            [*small, oversize], batch_size=6, char_limit=4000
        )
        for batch in batches:
            self.assertLessEqual(len(batch), 6)
            if len(batch) > 1:
                self.assertLessEqual(sum(item.char_count for item in batch), 4000)
        self.assertEqual([oversize], batches[-1])
        self.assertEqual(long_text, batches[-1][0].provider_input()["sourceText"])

    def test_provider_input_contains_full_projection_and_hashes(self) -> None:
        candidate = candidate_stub()
        payload = candidate.provider_input()
        self.assertEqual(candidate.source_text, payload["sourceText"])
        self.assertEqual(candidate.source_text_sha256, payload["sourceTextSha256"])
        self.assertIn("keyEntities", payload["candidate"])
        self.assertIn("evidence", payload["candidate"])


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> dict:
        return self._body


class FakeHttpClient:
    def __init__(self, body: dict) -> None:
        self.body = body
        self.calls: list[dict] = []
        self.closed = False

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return FakeResponse(self.body)

    def close(self) -> None:
        self.closed = True


class ProviderPayloadTests(unittest.TestCase):
    def test_nonthinking_json_payload_without_real_network(self) -> None:
        candidate = candidate_stub()
        content = json.dumps(
            {
                "reviews": [
                    {
                        "entryId": candidate.entry_id,
                        "verdict": "pass",
                        "checklist": checklist(),
                        "issues": [],
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake = FakeHttpClient(
            {
                "id": "fake-response",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        )
        client = reviewer.DeepSeekReviewClient(
            rpm=1_000_000,
            timeout=1,
            max_tokens=4000,
            http_client=fake,
            api_key="test-key",
            base_url="https://example.invalid",
            model="deepseek-v4-flash",
            live_enabled=True,
        )
        response, meta = client.complete([candidate], retries=0)
        self.assertEqual("fake-response", meta["response_id"])
        self.assertEqual("pass", response["reviews"][0]["verdict"])
        sent = fake.calls[0]["json"]
        self.assertEqual({"type": "disabled"}, sent["thinking"])
        self.assertEqual(0, sent["temperature"])
        self.assertEqual({"type": "json_object"}, sent["response_format"])
        user_payload = json.loads(sent["messages"][1]["content"])
        self.assertEqual(candidate.source_text, user_payload["records"][0]["sourceText"])
        client.close()
        self.assertTrue(fake.closed)

    def test_provider_reported_model_must_match_requested_model(self) -> None:
        candidate = candidate_stub()
        content = json.dumps(
            {
                "reviews": [
                    {
                        "entryId": candidate.entry_id,
                        "verdict": "pass",
                        "checklist": checklist(),
                        "issues": [],
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake = FakeHttpClient(
            {
                "id": "fake-response",
                "model": "different-model",
                "choices": [{"message": {"content": content}}],
                "usage": {},
            }
        )
        client = reviewer.DeepSeekReviewClient(
            rpm=1_000_000,
            timeout=1,
            max_tokens=4000,
            http_client=fake,
            api_key="test-key",
            base_url="https://example.invalid",
            model="deepseek-v4-flash",
            live_enabled=True,
        )
        with self.assertRaisesRegex(reviewer.FatalProviderError, "reported model"):
            client.complete([candidate], retries=0)
        client.close()

    def test_shared_provider_call_budget_is_a_hard_cap(self) -> None:
        candidate = candidate_stub()
        content = json.dumps(
            {
                "reviews": [
                    {
                        "entryId": candidate.entry_id,
                        "verdict": "pass",
                        "checklist": checklist(),
                        "issues": [],
                    }
                ]
            },
            ensure_ascii=False,
        )
        fake = FakeHttpClient(
            {
                "id": "fake-response",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": content}}],
                "usage": {},
            }
        )
        budget = reviewer.ProviderCallBudget(1)
        client = reviewer.DeepSeekReviewClient(
            rpm=1_000_000,
            timeout=1,
            max_tokens=4000,
            http_client=fake,
            api_key="test-key",
            base_url="https://example.invalid",
            model="deepseek-v4-flash",
            live_enabled=True,
            call_budget=budget,
        )
        client.complete([candidate], retries=0)
        with self.assertRaisesRegex(
            reviewer.ProviderCallBudgetExceeded, "budget exhausted"
        ):
            client.complete([candidate], retries=0)
        self.assertEqual(1, budget.used)
        self.assertTrue(budget.exhausted)
        self.assertEqual(1, len(fake.calls))
        client.close()


def create_input_databases(directory: Path) -> tuple[Path, Path, str]:
    source_db = directory / "source.sqlite3"
    annotation_db = directory / "annotations.sqlite3"
    source_text = "某甲梦见故人来访，醒后才知此事未曾发生。"
    source_hash = reviewer.sha256_text(source_text)
    entry_id = "c1ws_0123456789abcdef01234567"

    conn = sqlite3.connect(source_db)
    conn.execute(
        """
        CREATE TABLE entries(
            entry_id TEXT,source_work_id TEXT,source_work_title TEXT,title TEXT,
            source_locator TEXT,entry_ordinal INTEGER,char_count INTEGER,text TEXT,
            extracted_text_sha256 TEXT,dedupe_status TEXT,runtime_eligible INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            entry_id,
            "work-1",
            "测试古籍",
            "测试条目",
            "卷一",
            1,
            len(source_text),
            source_text,
            source_hash,
            "canonical",
            1,
        ),
    )
    conn.commit()
    conn.close()

    annotation = {
        "annotation_record": {
            "unit": {
                "unit_id": entry_id,
                "source_text_sha256": source_hash,
            },
            "source_profile": {
                "source_text": source_text,
                "source_text_sha256": source_hash,
            },
            "retrieval_profile": {
                "modern_retrieval_summary": "某甲梦见故人，醒后知道事情未发生。",
                "narrative_sufficiency": "sufficient",
                "narrative_sufficiency_reason": "原文构成完整事件。",
                "key_entities": [
                    {"name": "某甲", "role": "梦者", "evidence_ids": ["ev01"]}
                ],
                "plot_beats": [
                    {
                        "type": "trigger",
                        "text": "梦见故人",
                        "evidence_ids": ["ev01"],
                    }
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
    conn = sqlite3.connect(annotation_db)
    conn.execute(
        """
        CREATE TABLE annotation_jobs(
            entry_id TEXT,source_text_sha256 TEXT,status TEXT,annotation_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO annotation_jobs VALUES(?,?,?,?)",
        (entry_id, source_hash, "valid", json.dumps(annotation, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return source_db, annotation_db, source_text


class ReadOnlyInputAndResumeTests(unittest.TestCase):
    def test_process_batch_writes_only_the_sidecar(self) -> None:
        class StubReviewClient:
            model = "deepseek-v4-flash"

            def __init__(self) -> None:
                self.call_count = 0

            def complete(self, candidates, retries, *, correction=None):
                self.call_count += 1
                response = {
                    "reviews": [
                        {
                            "entryId": candidate.entry_id,
                            "verdict": "pass",
                            "checklist": checklist(),
                            "issues": [],
                        }
                        for candidate in candidates
                    ]
                }
                return response, {
                    "response_id": "stub-response",
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 5,
                        "total_tokens": 25,
                    },
                    "model": self.model,
                    "requested_model": self.model,
                }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviews.sqlite3"
            candidate = candidate_stub()
            conn = reviewer.init_sidecar_db(path)
            try:
                reviewer.enqueue_candidates(
                    conn,
                    [candidate],
                    model="deepseek-v4-flash",
                    schema_sha256="a" * 64,
                )
                client = StubReviewClient()
                reviewer.process_batch(
                    conn,
                    client,  # type: ignore[arg-type]
                    [candidate],
                    schema_validator=schema_validator(),
                    retries=0,
                )
                row = conn.execute(
                    """
                    SELECT status,verdict,raw_response_json,total_tokens,attempts
                    FROM semantic_review_jobs WHERE entry_id=?
                    """,
                    (candidate.entry_id,),
                ).fetchone()
                self.assertEqual("pass", row["status"])
                self.assertEqual("pass", row["verdict"])
                self.assertEqual(25, row["total_tokens"])
                self.assertEqual(1, row["attempts"])
                self.assertEqual(
                    candidate.entry_id,
                    json.loads(row["raw_response_json"])["reviews"][0]["entryId"],
                )
            finally:
                conn.close()

    def test_loads_valid_a1_and_does_not_modify_input_databases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source_db, annotation_db, source_text = create_input_databases(directory)
            before = (
                reviewer.sha256_file(source_db),
                reviewer.sha256_file(annotation_db),
            )
            candidates, source_count = reviewer.load_review_candidates(
                source_db, annotation_db
            )
            after = (
                reviewer.sha256_file(source_db),
                reviewer.sha256_file(annotation_db),
            )
            self.assertEqual(before, after)
            self.assertEqual(1, source_count)
            self.assertEqual(source_text, candidates[0].source_text)

    def test_sidecar_resume_and_candidate_hash_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviews.sqlite3"
            candidate = candidate_stub()
            conn = reviewer.init_sidecar_db(path)
            try:
                invalidated = reviewer.enqueue_candidates(
                    conn, [candidate], model="deepseek-v4-flash", schema_sha256="a" * 64
                )
                self.assertEqual(0, invalidated)
                row = conn.execute(
                    "SELECT status,input_json FROM semantic_review_jobs"
                ).fetchone()
                self.assertEqual("queued", row["status"])
                self.assertEqual(
                    candidate.source_text, json.loads(row["input_json"])["sourceText"]
                )
                reviewer.mark_reviews(
                    conn,
                    {
                        candidate.entry_id: {
                            "entryId": candidate.entry_id,
                            "verdict": "pass",
                            "checklist": checklist(),
                            "issues": [],
                        }
                    },
                    [candidate],
                    {
                        "reviews": [
                            {
                                "entryId": candidate.entry_id,
                                "verdict": "pass",
                                "checklist": checklist(),
                                "issues": [],
                            }
                        ]
                    },
                    {"response_id": "fake", "usage": {}, "model": "deepseek-v4-flash"},
                )
                self.assertEqual(
                    [], reviewer.pending_candidates(conn, [candidate], resume=True)
                )
                with self.assertRaisesRegex(RuntimeError, "pass --resume"):
                    reviewer.pending_candidates(conn, [candidate], resume=False)

                changed = replace(candidate, candidate_annotation_sha256="c" * 64)
                invalidated = reviewer.enqueue_candidates(
                    conn, [changed], model="deepseek-v4-flash", schema_sha256="a" * 64
                )
                self.assertEqual(1, invalidated)
                pending = reviewer.pending_candidates(conn, [changed], resume=True)
                self.assertEqual([changed], pending)
                self.assertEqual(
                    "stale",
                    conn.execute(
                        "SELECT status FROM semantic_review_jobs"
                    ).fetchone()[0],
                )
            finally:
                conn.close()

    def test_dry_run_creates_no_sidecar_and_calls_no_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source_db, annotation_db, _ = create_input_databases(directory)
            sidecar = directory / "never-created.sqlite3"
            manifest = directory / "never-created.json"
            result = reviewer.main(
                [
                    "--source-db",
                    str(source_db),
                    "--annotation-db",
                    str(annotation_db),
                    "--sidecar-db",
                    str(sidecar),
                    "--manifest",
                    str(manifest),
                    "--schema",
                    str(SCHEMA_PATH),
                    "--dry-run",
                    "--limit",
                    "1",
                    "--sample-mode",
                    "stratified",
                    "--workers",
                    "4",
                ]
            )
            self.assertEqual(0, result)
            self.assertFalse(sidecar.exists())
            self.assertFalse(manifest.exists())


class ManifestTests(unittest.TestCase):
    def test_manifest_reports_complete_input_and_no_canonical_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sidecar = directory / "reviews.sqlite3"
            manifest = directory / "manifest.json"
            candidate = candidate_stub(source_text="甲" * 7584, char_count=7584)
            conn = reviewer.init_sidecar_db(sidecar)
            try:
                reviewer.enqueue_candidates(
                    conn, [candidate], model="deepseek-v4-flash", schema_sha256="a" * 64
                )
                reviewer.write_manifest(
                    conn,
                    manifest,
                    source_db=directory / "source.sqlite3",
                    annotation_db=directory / "annotations.sqlite3",
                    sidecar_db=sidecar,
                    schema_path=SCHEMA_PATH,
                    selected=[candidate],
                    batch_size=6,
                    char_limit=4000,
                    model="deepseek-v4-flash",
                    provider_calls=0,
                    workers=0,
                    max_provider_calls=4,
                )
            finally:
                conn.close()
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual("complete_never_truncated", payload["source_input_policy"])
            self.assertEqual(1, payload["full_input_saved_records"])
            self.assertEqual(1, payload["oversized_complete_single_records"])
            self.assertEqual(7584, payload["maximum_source_characters"])
            self.assertEqual(0, payload["truncated_source_records"])
            self.assertEqual(0, payload["canonical_overwrite_count"])
            self.assertEqual(4, payload["current_run_provider_call_limit"])


if __name__ == "__main__":
    unittest.main()
