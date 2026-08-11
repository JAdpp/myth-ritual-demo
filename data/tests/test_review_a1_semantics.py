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
STAGE_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "a1-semantic-review-stage.schema.json"
)
ANNOTATION_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "a1-retrieval-annotation.schema.json"
)


def schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def stage_schema_validator() -> Draft202012Validator:
    schema = json.loads(STAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
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


def stage_field_checks(*issues: dict) -> dict[str, dict]:
    result = {
        field: {"checked": True, "issueCodes": []}
        for field in reviewer.CHECKLIST_FIELDS
    }
    for item in issues:
        codes = result[item["field"]]["issueCodes"]
        if item["code"] not in codes:
            codes.append(item["code"])
    return result


def stage_issue(
    *,
    code: str,
    field: str,
    target: str,
    excerpt: str,
    hint: str,
) -> dict:
    return {
        "code": code,
        "field": field,
        "targetValue": target,
        "sourceExcerpt": excerpt,
        "correctionHint": hint,
    }


def stage_response(
    candidate: reviewer.ReviewCandidate,
    mode: str,
    *issues: dict,
) -> dict:
    return {
        "mode": mode,
        "reviews": [
            {
                "entryId": candidate.entry_id,
                "fieldChecks": stage_field_checks(*issues),
                "issues": list(issues),
            }
        ],
    }


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


class DirectionalStageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = candidate_stub()
        self.validator = stage_schema_validator()

    def parse(self, mode: str, *issues: dict) -> dict[str, dict]:
        return reviewer.parse_stage_reviews(
            stage_response(self.candidate, mode, *issues),
            [self.candidate],
            mode=mode,
            schema_validator=self.validator,
        )

    def test_empty_directional_passes_cover_all_fields(self) -> None:
        for mode in reviewer.REVIEW_MODES:
            with self.subTest(mode=mode):
                parsed = self.parse(mode)
                self.assertEqual([], parsed[self.candidate.entry_id]["issues"])

    def test_real_fieldless_array_shape_is_not_guessed(self) -> None:
        response = {
            "mode": "contradiction",
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "fieldChecks": [
                        {"checked": True, "issueCodes": []}
                        for _ in reviewer.CHECKLIST_FIELDS
                    ],
                    "issues": [],
                }
            ],
        }
        canonical = reviewer.canonicalize_stage_response_shape(response)
        self.assertIsInstance(canonical["reviews"][0]["fieldChecks"], list)
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "schema failed"):
            reviewer.parse_stage_reviews(
                response,
                [self.candidate],
                mode="contradiction",
                schema_validator=self.validator,
            )

    def test_real_aggregate_field_shape_is_not_expanded(self) -> None:
        response = {
            "mode": "contradiction",
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "fieldChecks": {
                        field: {"checked": True, "issueCodes": []}
                        for field in (
                            "modernRetrievalSummary",
                            "narrativeSufficiency",
                            "keyEntities",
                            "plotBeats",
                            "motifTerms",
                            "lifeContext",
                            "narrativeArc",
                            "autoSafetyScreen",
                            "evidence",
                        )
                    },
                    "issues": [],
                }
            ],
        }
        canonical = reviewer.canonicalize_stage_response_shape(response)
        self.assertIn("narrativeArc", canonical["reviews"][0]["fieldChecks"])
        self.assertNotIn(
            "narrativeArc.trigger", canonical["reviews"][0]["fieldChecks"]
        )
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "required property"):
            reviewer.parse_stage_reviews(
                response,
                [self.candidate],
                mode="contradiction",
                schema_validator=self.validator,
            )

    def test_labelled_array_and_empty_aliases_are_shape_only_normalized(self) -> None:
        response = {
            "mode": "contradiction",
            "reviews": [
                {
                    "entryId": self.candidate.entry_id,
                    "fieldChecks": [
                        {"field": field, "checked": True, "issues": []}
                        for field in reviewer.CHECKLIST_FIELDS
                    ],
                }
            ],
        }
        parsed = reviewer.parse_stage_reviews(
            response,
            [self.candidate],
            mode="contradiction",
            schema_validator=self.validator,
        )
        review = parsed[self.candidate.entry_id]
        self.assertEqual(set(reviewer.CHECKLIST_FIELDS), set(review["fieldChecks"]))
        self.assertEqual([], review["issues"])
        self.assertTrue(
            all(
                check == {"checked": True, "issueCodes": []}
                for check in review["fieldChecks"].values()
            )
        )

    def test_missing_issue_details_are_never_inferred_from_nonempty_codes(self) -> None:
        checks = stage_field_checks()
        checks["lifeContext"] = {
            "checked": True,
            "issues": ["life_context_omission"],
        }
        response = {
            "mode": "omission",
            "reviews": [
                {"entryId": self.candidate.entry_id, "fieldChecks": checks}
            ],
        }
        canonical = reviewer.canonicalize_stage_response_shape(response)
        self.assertNotIn("issues", canonical["reviews"][0])
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "required property"):
            reviewer.parse_stage_reviews(
                response,
                [self.candidate],
                mode="omission",
                schema_validator=self.validator,
            )

    def test_issuecodes_alias_is_accepted_only_with_exact_issue_closure(self) -> None:
        item = stage_issue(
            code="life_context_omission",
            field="lifeContext",
            target="separation_loss",
            excerpt="故人来访",
            hint="添加 separation_loss。",
        )
        response = stage_response(self.candidate, "omission", item)
        check = response["reviews"][0]["fieldChecks"]["lifeContext"]
        check["issues"] = check.pop("issueCodes")
        parsed = reviewer.parse_stage_reviews(
            response,
            [self.candidate],
            mode="omission",
            schema_validator=self.validator,
        )
        self.assertEqual(
            ["life_context_omission"],
            parsed[self.candidate.entry_id]["fieldChecks"]["lifeContext"][
                "issueCodes"
            ],
        )

        mismatched = json.loads(json.dumps(response, ensure_ascii=False))
        mismatched["reviews"][0]["fieldChecks"]["lifeContext"]["issues"] = []
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "issueCodes mismatch"):
            reviewer.parse_stage_reviews(
                mismatched,
                [self.candidate],
                mode="omission",
                schema_validator=self.validator,
            )

    def test_real_fifteen_issue_overreport_shape_remains_rejected(self) -> None:
        item = stage_issue(
            code="life_context_omission",
            field="lifeContext",
            target="separation_loss",
            excerpt="故人来访",
            hint="添加 separation_loss。",
        )
        response = stage_response(self.candidate, "omission", *([item] * 15))
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "too long"):
            reviewer.parse_stage_reviews(
                response,
                [self.candidate],
                mode="omission",
                schema_validator=self.validator,
            )

    def test_omission_requires_absent_target_and_addition_action(self) -> None:
        valid = stage_issue(
            code="life_context_omission",
            field="lifeContext",
            target="separation_loss",
            excerpt="故人来访",
            hint="添加 separation_loss。",
        )
        self.parse("omission", valid)

        already_present = dict(valid, targetValue="knowledge_uncertainty")
        already_present["field"] = "narrativeArc.conflictTypes"
        already_present["code"] = "conflict_omission"
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "already exists"):
            self.parse("omission", already_present)

        no_action = dict(valid, correctionHint="该字段语义并不完整。")
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "addition action"):
            self.parse("omission", no_action)

    def test_contradiction_requires_current_target_and_replacement_action(self) -> None:
        valid = stage_issue(
            code="ending_contradiction",
            field="narrativeArc.endingMode",
            target="unresolved",
            excerpt="醒后才知此事未曾发生",
            hint="将 unresolved 替换为 restoration。",
        )
        self.parse("contradiction", valid)

        absent = dict(valid, targetValue="restoration")
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "absent"):
            self.parse("contradiction", absent)

        wrong_direction = dict(valid, correctionHint="补入 restoration。")
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "omission"):
            self.parse("contradiction", wrong_direction)

    def test_directional_code_field_and_exact_quote_are_fail_closed(self) -> None:
        wrong_code = stage_issue(
            code="summary_actor_contradiction",
            field="modernRetrievalSummary",
            target="某甲",
            excerpt="某甲梦见故人来访",
            hint="删除摘要中的某甲。",
        )
        response = stage_response(self.candidate, "omission", wrong_code)
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "directional code"):
            reviewer.parse_stage_reviews(
                response,
                [self.candidate],
                mode="omission",
                schema_validator=self.validator,
            )

        nonexact = stage_issue(
            code="life_context_omission",
            field="lifeContext",
            target="separation_loss",
            excerpt="故人前来拜访",
            hint="添加 separation_loss。",
        )
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "byte-exact"):
            self.parse("omission", nonexact)

    def test_field_checks_must_exactly_name_actual_issue_codes(self) -> None:
        item = stage_issue(
            code="life_context_omission",
            field="lifeContext",
            target="separation_loss",
            excerpt="故人来访",
            hint="添加 separation_loss。",
        )
        response = stage_response(self.candidate, "omission", item)
        response["reviews"][0]["fieldChecks"]["lifeContext"]["issueCodes"] = []
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "issueCodes mismatch"):
            reviewer.parse_stage_reviews(
                response,
                [self.candidate],
                mode="omission",
                schema_validator=self.validator,
            )

    def test_evidence_support_contradiction_is_tied_to_one_evidence_item(self) -> None:
        linked = stage_issue(
            code="evidence_support_contradiction",
            field="evidence.supports",
            target="retrieval_profile.modern_retrieval_summary",
            excerpt="某甲梦见故人来访",
            hint="删除该摘录的现有 supports 值。",
        )
        self.parse("contradiction", linked)

        unlinked = dict(linked, sourceExcerpt="醒后才知此事未曾发生")
        with self.assertRaisesRegex(reviewer.ReviewValidationError, "linked"):
            self.parse("contradiction", unlinked)

    def test_merge_is_deterministic_and_preserves_both_directions(self) -> None:
        omission_issue = stage_issue(
            code="life_context_omission",
            field="lifeContext",
            target="separation_loss",
            excerpt="故人来访",
            hint="添加 separation_loss。",
        )
        contradiction_issue = stage_issue(
            code="ending_contradiction",
            field="narrativeArc.endingMode",
            target="unresolved",
            excerpt="醒后才知此事未曾发生",
            hint="将 unresolved 替换为 restoration。",
        )
        omission = self.parse("omission", omission_issue)
        contradiction = self.parse("contradiction", contradiction_issue)
        merged = reviewer.merge_stage_reviews(
            omission,
            contradiction,
            [self.candidate],
            schema_validator=schema_validator(),
        )[self.candidate.entry_id]
        self.assertEqual("revise", merged["verdict"])
        self.assertFalse(merged["checklist"]["lifeContext"])
        self.assertFalse(merged["checklist"]["narrativeArc.endingMode"])
        self.assertEqual(
            ["label_life_context", "label_ending"],
            [item["code"] for item in merged["issues"]],
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


class PromptV4RegressionAnchorTests(unittest.TestCase):
    def test_prompt_requires_two_independent_directional_field_audits(self) -> None:
        self.assertEqual(
            "mengdie-a1-semantic-review-v4.3-nonthinking-codebook-audit",
            reviewer.PROMPT_VERSION,
        )
        self.assertEqual(reviewer.PROMPT_VERSION, reviewer.REVIEW_VERSION)
        self.assertEqual(("omission", "contradiction"), reviewer.REVIEW_MODES)
        for mode in reviewer.REVIEW_MODES:
            prompt = reviewer.SYSTEM_PROMPTS[mode]
            self.assertIn("逐字段强制检查", prompt)
            self.assertIn("全部 15 个 fieldChecks", prompt)
            self.assertIn("JSON", prompt)
            self.assertIn("固定关闭 thinking", prompt)
            self.assertIn("fieldChecks 必须是 JSON 对象", prompt)
            self.assertIn('"issues":[]', prompt)
            self.assertIn("不得把同一事实机械复制成 15 个字段的问题", prompt)
            self.assertIn("每条最多 10 个", prompt)
            for field in reviewer.CHECKLIST_FIELDS:
                self.assertIn(f'"{field}"', prompt)
            self.assertIn(f"mode 必须原样返回 {mode}", prompt)
        self.assertIn("只查明确遗漏", reviewer.OMISSION_SYSTEM_PROMPT)
        self.assertIn("只查明确矛盾", reviewer.CONTRADICTION_SYSTEM_PROMPT)

    def test_both_stage_prompts_cover_the_complete_annotation_codebook(self) -> None:
        schema = json.loads(ANNOTATION_SCHEMA_PATH.read_text(encoding="utf-8"))
        definitions = schema["$defs"]
        arc = definitions["narrativeArc"]["properties"]
        safety = definitions["autoSafetyScreen"]["properties"]
        vocabulary = {
            *definitions["lifeContext"]["items"]["enum"],
            *arc["conflict_types"]["items"]["enum"],
            *arc["agency_modes"]["items"]["enum"],
            *arc["ending_mode"]["enum"],
            *safety["status"]["enum"],
            *definitions["contentWarning"]["enum"],
            *definitions["interpretationRisk"]["enum"],
        }
        for value in sorted(vocabulary):
            self.assertIn(
                f"{value}=",
                reviewer.SHARED_SEMANTIC_CODEBOOK,
                f"shared codebook lacks a definition for {value}",
            )
        for mode in reviewer.REVIEW_MODES:
            prompt = reviewer.SYSTEM_PROMPTS[mode]
            self.assertIn(reviewer.SHARED_SEMANTIC_CODEBOOK, prompt)
            self.assertIn("autoSafetyScreen.uncertainties", prompt)
            self.assertIn("合法羁押仍可为 captivity", prompt)
            self.assertIn("被动受害不自动是 endure", prompt)
            self.assertIn("不能因标签来自附记", prompt)
        self.assertIn("candidate 字段为空也必须逐项检查", reviewer.OMISSION_SYSTEM_PROMPT)
        self.assertIn("逐枚举反向查漏", reviewer.OMISSION_SYSTEM_PROMPT)
        self.assertIn("每个现值都必须单独检查", reviewer.CONTRADICTION_SYSTEM_PROMPT)

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
    def test_each_directional_request_prompt_explicitly_requests_json(self) -> None:
        candidate = candidate_stub()
        for mode in reviewer.REVIEW_MODES:
            with self.subTest(mode=mode):
                fake = FakeHttpClient(
                    {
                        "id": f"fake-{mode}",
                        "model": "deepseek-v4-flash",
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        stage_response(candidate, mode),
                                        ensure_ascii=False,
                                    )
                                }
                            }
                        ],
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
                client.complete([candidate], retries=0, mode=mode)
                request = fake.calls[0]["json"]
                request_prompt = "\n".join(
                    message["content"] for message in request["messages"]
                )
                self.assertIn("json", request_prompt.lower())
                self.assertIn("仅返回 JSON 对象", request["messages"][0]["content"])
                client.close()

    def test_nonthinking_json_payload_uses_zero_temperature_without_real_network(self) -> None:
        candidate = candidate_stub()
        content = json.dumps(stage_response(candidate, "omission"), ensure_ascii=False)
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
        response, meta = client.complete([candidate], retries=0, mode="omission")
        self.assertEqual("fake-response", meta["response_id"])
        self.assertEqual("omission", response["mode"])
        sent = fake.calls[0]["json"]
        self.assertEqual({"type": "disabled"}, sent["thinking"])
        self.assertEqual(0, sent["temperature"])
        self.assertNotIn("reasoning_effort", sent)
        self.assertEqual({"type": "json_object"}, sent["response_format"])
        self.assertEqual("disabled", meta["thinking_mode"])
        self.assertIsNone(meta["reasoning_effort"])
        request_prompt = "\n".join(message["content"] for message in sent["messages"])
        self.assertIn("json", request_prompt.lower())
        self.assertIn("json", sent["messages"][0]["content"].lower())
        user_payload = json.loads(sent["messages"][1]["content"])
        self.assertEqual("omission", user_payload["reviewMode"])
        self.assertEqual(reviewer.OMISSION_SYSTEM_PROMPT, sent["messages"][0]["content"])
        self.assertEqual(candidate.source_text, user_payload["records"][0]["sourceText"])
        client.close()
        self.assertTrue(fake.closed)

    def test_v43_client_rejects_enabled_thinking(self) -> None:
        with self.assertRaisesRegex(
            reviewer.FatalProviderError, "thinking_mode=disabled"
        ):
            reviewer.DeepSeekReviewClient(
                rpm=1,
                timeout=1,
                max_tokens=100,
                http_client=FakeHttpClient({}),
                api_key="test-key",
                base_url="https://example.invalid",
                model="deepseek-v4-flash",
                live_enabled=True,
                thinking_mode="enabled",
            )

    def test_persisted_provider_metadata_requires_explicit_thinking_provenance(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "thinking_mode=disabled"):
            reviewer.provider_thinking_provenance(
                {"model": "deepseek-v4-flash", "reasoning_effort": None}
            )
        with self.assertRaisesRegex(RuntimeError, "reasoning_effort provenance"):
            reviewer.provider_thinking_provenance(
                {"model": "deepseek-v4-flash", "thinking_mode": "disabled"}
            )

    def test_provider_reported_model_must_match_requested_model(self) -> None:
        candidate = candidate_stub()
        content = json.dumps(
            stage_response(candidate, "contradiction"), ensure_ascii=False
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
            client.complete([candidate], retries=0, mode="contradiction")
        client.close()

    def test_shared_provider_call_budget_is_a_hard_cap(self) -> None:
        candidate = candidate_stub()
        content = json.dumps(stage_response(candidate, "omission"), ensure_ascii=False)
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
        client.complete([candidate], retries=0, mode="omission")
        with self.assertRaisesRegex(
            reviewer.ProviderCallBudgetExceeded, "budget exhausted"
        ):
            client.complete([candidate], retries=0, mode="omission")
        self.assertEqual(1, budget.used)
        self.assertTrue(budget.exhausted)
        self.assertEqual(1, len(fake.calls))
        client.close()

    def test_budget_exhaustion_leaves_directional_and_merged_rows_resumable(self) -> None:
        candidate = candidate_stub()
        fake = FakeHttpClient(
            {
                "id": "only-omission",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                stage_response(candidate, "omission"),
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
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
        with tempfile.TemporaryDirectory() as tmp:
            conn = reviewer.init_sidecar_db(Path(tmp) / "reviews.sqlite3")
            try:
                stage_hash = reviewer.sha256_file(STAGE_SCHEMA_PATH)
                reviewer.enqueue_candidates(
                    conn,
                    [candidate],
                    model=client.model,
                    schema_sha256=reviewer.sha256_file(SCHEMA_PATH),
                    stage_schema_sha256=stage_hash,
                )
                with self.assertRaises(reviewer.ProviderCallBudgetExceeded):
                    reviewer.process_batch(
                        conn,
                        client,
                        [candidate],
                        schema_validator=schema_validator(),
                        stage_schema_validator=stage_schema_validator(),
                        stage_schema_sha256=stage_hash,
                        retries=0,
                    )
                states = {
                    row["mode"]: row["status"]
                    for row in conn.execute(
                        "SELECT mode,status FROM semantic_review_stages"
                    )
                }
                self.assertEqual("valid", states["omission"])
                self.assertEqual("retryable_failed", states["contradiction"])
                # The worker-level handler converts the still-leased merged row
                # to retryable_failed; no stage is falsely admitted as complete.
                self.assertEqual(
                    "leased",
                    conn.execute("SELECT status FROM semantic_review_jobs").fetchone()[0],
                )
            finally:
                conn.close()
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


def create_effective_database(
    directory: Path, annotation_db: Path, source_text: str
) -> Path:
    effective_db = directory / "effective.sqlite3"
    conn = sqlite3.connect(annotation_db)
    canonical = json.loads(
        conn.execute("SELECT annotation_json FROM annotation_jobs").fetchone()[0]
    )
    conn.close()
    entry_id = canonical["annotation_record"]["unit"]["unit_id"]
    source_hash = reviewer.sha256_text(source_text)
    canonical_hash = reviewer.sha256_text(reviewer.canonical_json(canonical))
    review = {
        "entryId": entry_id,
        "verdict": "revise",
        "checklist": checklist("modernRetrievalSummary"),
        "issues": [issue()],
    }
    review_hash = reviewer.sha256_text(reviewer.canonical_json(review))
    effective = json.loads(json.dumps(canonical, ensure_ascii=False))
    effective["annotation_record"]["retrieval_profile"][
        "modern_retrieval_summary"
    ] = "某甲梦见故人来访，醒后确认梦中事件没有发生。"
    effective["annotation_record"]["annotation_meta"] = {
        "research_review_status": "not_reviewed",
        "research_ready": False,
        "human_review": {"reviewed": False},
        "repair_provenance": {
            "canonical_root_sha256": canonical_hash,
            "base_candidate_sha256": canonical_hash,
            "immediate_base_sha256": canonical_hash,
            "parent_effective_sha256": None,
            "repair_iteration": 1,
            "immediate_base_origin": "canonical_a1",
            "review_sha256": review_hash,
            "automatic_semantic_repair": True,
            "human_reviewed": False,
        },
    }
    effective_hash = reviewer.sha256_text(reviewer.canonical_json(effective))
    lineage = [
        {
            "repair_iteration": 1,
            "canonical_root_sha256": canonical_hash,
            "immediate_base_sha256": canonical_hash,
            "parent_effective_sha256": None,
            "review_sha256": review_hash,
            "effective_annotation_sha256": effective_hash,
        }
    ]
    envelope = {
        "entry_id": entry_id,
        "source_text_sha256": source_hash,
        "canonical_root_sha256": canonical_hash,
        "base_candidate_sha256": canonical_hash,
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
    conn = sqlite3.connect(effective_db)
    conn.execute(
        """
        CREATE TABLE effective_repair_jobs(
            entry_id TEXT PRIMARY KEY,source_text_sha256 TEXT,
            canonical_root_sha256 TEXT,base_candidate_sha256 TEXT,
            parent_effective_sha256 TEXT,repair_iteration INTEGER,
            immediate_base_origin TEXT,lineage_json TEXT,review_sha256 TEXT,
            base_annotation_json TEXT,review_json TEXT,status TEXT,
            effective_annotation_json TEXT,effective_annotation_sha256 TEXT,
            effective_envelope_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO effective_repair_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            entry_id,
            source_hash,
            canonical_hash,
            canonical_hash,
            None,
            1,
            "canonical_a1",
            reviewer.canonical_json(lineage),
            review_hash,
            reviewer.canonical_json(canonical),
            reviewer.canonical_json(review),
            "valid",
            reviewer.canonical_json(effective),
            effective_hash,
            reviewer.canonical_json(envelope),
        ),
    )
    conn.commit()
    conn.close()
    return effective_db


class ReadOnlyInputAndResumeTests(unittest.TestCase):
    def test_process_batch_writes_only_the_sidecar(self) -> None:
        class StubReviewClient:
            model = "deepseek-v4-flash"
            thinking_mode = reviewer.THINKING_MODE
            reasoning_effort = None

            def __init__(self) -> None:
                self.call_count = 0

            def complete(self, candidates, retries, *, mode, correction=None):
                self.call_count += 1
                response = {
                    "mode": mode,
                    "reviews": [
                        {
                            "entryId": candidate.entry_id,
                            "fieldChecks": stage_field_checks(),
                            "issues": [],
                        }
                        for candidate in candidates
                    ]
                }
                return response, {
                    "response_id": f"stub-response-{mode}",
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 5,
                        "total_tokens": 25,
                    },
                    "model": self.model,
                    "requested_model": self.model,
                    "thinking_mode": self.thinking_mode,
                    "reasoning_effort": self.reasoning_effort,
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
                    stage_schema_sha256=reviewer.sha256_file(STAGE_SCHEMA_PATH),
                )
                client = StubReviewClient()
                reviewer.process_batch(
                    conn,
                    client,  # type: ignore[arg-type]
                    [candidate],
                    schema_validator=schema_validator(),
                    stage_schema_validator=stage_schema_validator(),
                    stage_schema_sha256=reviewer.sha256_file(STAGE_SCHEMA_PATH),
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
                self.assertEqual(50, row["total_tokens"])
                self.assertEqual(1, row["attempts"])
                merged = json.loads(row["raw_response_json"])
                self.assertEqual("merged", merged["mode"])
                self.assertEqual(
                    {"omission", "contradiction"},
                    set(merged["stageReviews"][candidate.entry_id]),
                )
                stage_rows = conn.execute(
                    "SELECT mode,status,attempts FROM semantic_review_stages ORDER BY mode"
                ).fetchall()
                self.assertEqual(2, len(stage_rows))
                self.assertTrue(all(item["status"] == "valid" for item in stage_rows))
                self.assertTrue(all(item["attempts"] == 1 for item in stage_rows))
                self.assertEqual(2, client.call_count)
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

    def test_valid_effective_repair_overlays_canonical_candidate_with_hash_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source_db, annotation_db, source_text = create_input_databases(directory)
            effective_db = create_effective_database(
                directory, annotation_db, source_text
            )
            before = tuple(
                reviewer.sha256_file(path)
                for path in (source_db, annotation_db, effective_db)
            )
            candidates, source_count = reviewer.load_review_candidates(
                source_db, annotation_db, effective_db
            )
            after = tuple(
                reviewer.sha256_file(path)
                for path in (source_db, annotation_db, effective_db)
            )
            self.assertEqual(before, after)
            self.assertEqual(1, source_count)
            self.assertEqual("effective_repair", candidates[0].candidate_origin)
            self.assertEqual(1, candidates[0].repair_iteration)
            self.assertIn(
                "确认梦中事件没有发生",
                candidates[0].candidate_projection["modernRetrievalSummary"],
            )

    def test_effective_repair_overlay_rejects_broken_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source_db, annotation_db, source_text = create_input_databases(directory)
            effective_db = create_effective_database(
                directory, annotation_db, source_text
            )
            conn = sqlite3.connect(effective_db)
            conn.execute("UPDATE effective_repair_jobs SET lineage_json='[]'")
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(RuntimeError, "lineage"):
                reviewer.load_review_candidates(
                    source_db, annotation_db, effective_db
                )

    def test_stage_quarantine_recovery_normalizes_only_provable_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviews.sqlite3"
            candidate = candidate_stub()
            schema_hash = reviewer.sha256_file(SCHEMA_PATH)
            stage_hash = reviewer.sha256_file(STAGE_SCHEMA_PATH)
            conn = reviewer.init_sidecar_db(path)
            try:
                reviewer.enqueue_candidates(
                    conn,
                    [candidate],
                    model="deepseek-v4-flash",
                    schema_sha256=schema_hash,
                    stage_schema_sha256=stage_hash,
                )
                safe_raw = {
                    "mode": "contradiction",
                    "reviews": [
                        {
                            "entryId": candidate.entry_id,
                            "fieldChecks": [
                                {"field": field, "checked": True, "issues": []}
                                for field in reviewer.CHECKLIST_FIELDS
                            ],
                        }
                    ],
                }
                unsafe_raw = {
                    "mode": "omission",
                    "reviews": [
                        {
                            "entryId": candidate.entry_id,
                            "fieldChecks": [
                                {"checked": True, "issueCodes": []}
                                for _ in reviewer.CHECKLIST_FIELDS
                            ],
                            "issues": [],
                        }
                    ],
                }
                with conn:
                    for mode, raw in (
                        ("contradiction", safe_raw),
                        ("omission", unsafe_raw),
                    ):
                        conn.execute(
                            """
                            UPDATE semantic_review_stages
                            SET status='quarantined',raw_response_json=?,
                                error_kind='ReviewValidationError',
                                error_message='real raw shape regression',attempts=2
                            WHERE entry_id=? AND mode=?
                            """,
                            (
                                reviewer.canonical_json(raw),
                                candidate.entry_id,
                                mode,
                            ),
                        )
                recovered = reviewer.revalidate_stored_stage_quarantines(
                    conn,
                    [candidate],
                    stage_schema_validator=stage_schema_validator(),
                    expected_model="deepseek-v4-flash",
                    expected_stage_schema_sha256=stage_hash,
                )
                self.assertEqual(1, recovered)
                safe_row = conn.execute(
                    """
                    SELECT status,stage_review_json,raw_response_json
                    FROM semantic_review_stages
                    WHERE entry_id=? AND mode='contradiction'
                    """,
                    (candidate.entry_id,),
                ).fetchone()
                self.assertEqual("valid", safe_row["status"])
                normalized = json.loads(safe_row["stage_review_json"])
                self.assertEqual([], normalized["issues"])
                self.assertEqual(
                    set(reviewer.CHECKLIST_FIELDS), set(normalized["fieldChecks"])
                )
                self.assertEqual(
                    safe_raw, json.loads(safe_row["raw_response_json"])
                )
                unsafe_status = conn.execute(
                    """
                    SELECT status FROM semantic_review_stages
                    WHERE entry_id=? AND mode='omission'
                    """,
                    (candidate.entry_id,),
                ).fetchone()[0]
                self.assertEqual("quarantined", unsafe_status)
            finally:
                conn.close()

    def test_resume_reuses_valid_omission_checkpoint_and_calls_only_contradiction(self) -> None:
        class StubReviewClient:
            model = "deepseek-v4-flash"
            thinking_mode = reviewer.THINKING_MODE
            reasoning_effort = None

            def __init__(self) -> None:
                self.call_count = 0
                self.modes: list[str] = []

            def complete(self, candidates, retries, *, mode, correction=None):
                self.call_count += 1
                self.modes.append(mode)
                response = {
                    "mode": mode,
                    "reviews": [
                        {
                            "entryId": item.entry_id,
                            "fieldChecks": stage_field_checks(),
                            "issues": [],
                        }
                        for item in candidates
                    ],
                }
                return response, {
                    "response_id": f"resume-{mode}",
                    "usage": {},
                    "model": self.model,
                    "requested_model": self.model,
                    "thinking_mode": self.thinking_mode,
                    "reasoning_effort": self.reasoning_effort,
                }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviews.sqlite3"
            candidate = candidate_stub()
            stage_hash = reviewer.sha256_file(STAGE_SCHEMA_PATH)
            conn = reviewer.init_sidecar_db(path)
            try:
                reviewer.enqueue_candidates(
                    conn,
                    [candidate],
                    model="deepseek-v4-flash",
                    schema_sha256=reviewer.sha256_file(SCHEMA_PATH),
                    stage_schema_sha256=stage_hash,
                )
                omission_response = stage_response(candidate, "omission")
                parsed = reviewer.parse_stage_reviews(
                    omission_response,
                    [candidate],
                    mode="omission",
                    schema_validator=stage_schema_validator(),
                )
                reviewer.mark_stage_reviews(
                    conn,
                    parsed,
                    [candidate],
                    mode="omission",
                    raw_response=omission_response,
                    provider_meta={
                        "response_id": "checkpoint-omission",
                        "usage": {},
                        "model": "deepseek-v4-flash",
                        "requested_model": "deepseek-v4-flash",
                        "thinking_mode": reviewer.THINKING_MODE,
                        "reasoning_effort": reviewer.REASONING_EFFORT,
                    },
                )
                client = StubReviewClient()
                reviewer.process_batch(
                    conn,
                    client,  # type: ignore[arg-type]
                    [candidate],
                    schema_validator=schema_validator(),
                    stage_schema_validator=stage_schema_validator(),
                    stage_schema_sha256=stage_hash,
                    retries=0,
                )
                self.assertEqual(["contradiction"], client.modes)
                self.assertEqual(
                    "pass",
                    conn.execute(
                        "SELECT status FROM semantic_review_jobs WHERE entry_id=?",
                        (candidate.entry_id,),
                    ).fetchone()[0],
                )
            finally:
                conn.close()

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
                    {
                        "response_id": "fake",
                        "usage": {},
                        "model": "deepseek-v4-flash",
                        "requested_model": "deepseek-v4-flash",
                        "thinking_mode": reviewer.THINKING_MODE,
                        "reasoning_effort": reviewer.REASONING_EFFORT,
                    },
                )
                # A merged verdict alone is not complete in v4: both independent
                # directional checkpoints must also be valid.
                self.assertEqual(
                    [candidate],
                    reviewer.pending_candidates(conn, [candidate], resume=True),
                )
                for mode in reviewer.REVIEW_MODES:
                    response = stage_response(candidate, mode)
                    parsed = reviewer.parse_stage_reviews(
                        response,
                        [candidate],
                        mode=mode,
                        schema_validator=stage_schema_validator(),
                    )
                    reviewer.mark_stage_reviews(
                        conn,
                        parsed,
                        [candidate],
                        mode=mode,
                        raw_response=response,
                        provider_meta={
                            "response_id": f"fake-{mode}",
                            "usage": {},
                            "model": "deepseek-v4-flash",
                            "requested_model": "deepseek-v4-flash",
                            "thinking_mode": reviewer.THINKING_MODE,
                            "reasoning_effort": reviewer.REASONING_EFFORT,
                        },
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

    def test_old_enabled_rows_become_stale_under_v43_nonthinking_contract(self) -> None:
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
                    stage_schema_sha256="b" * 64,
                )
                fresh_job = conn.execute(
                    """
                    SELECT status,thinking_mode,reasoning_effort
                    FROM semantic_review_jobs WHERE entry_id=?
                    """,
                    (candidate.entry_id,),
                ).fetchone()
                self.assertEqual(("queued", "disabled", None), tuple(fresh_job))
                fresh_stages = conn.execute(
                    """
                    SELECT mode,status,thinking_mode,reasoning_effort
                    FROM semantic_review_stages WHERE entry_id=? ORDER BY mode
                    """,
                    (candidate.entry_id,),
                ).fetchall()
                self.assertEqual(2, len(fresh_stages))
                self.assertTrue(
                    all(
                        row["status"] == "queued"
                        and row["thinking_mode"] == "disabled"
                        and row["reasoning_effort"] is None
                        for row in fresh_stages
                    )
                )
                with conn:
                    conn.execute(
                        """
                        UPDATE semantic_review_jobs
                        SET status='pass',verdict='pass',review_json='{}',issues_json='[]',
                            raw_response_json='{}',provider_response_id='old-enabled',
                            total_tokens=2,thinking_mode='enabled',reasoning_effort=NULL
                        WHERE entry_id=?
                        """,
                        (candidate.entry_id,),
                    )
                    conn.execute(
                        """
                        UPDATE semantic_review_stages
                        SET status='valid',stage_review_json='{}',raw_response_json='{}',
                            provider_response_id='old-enabled',total_tokens=2,
                            thinking_mode='enabled',reasoning_effort=NULL
                        WHERE entry_id=?
                        """,
                        (candidate.entry_id,),
                    )
                invalidated = reviewer.enqueue_candidates(
                    conn,
                    [candidate],
                    model="deepseek-v4-flash",
                    schema_sha256="a" * 64,
                    stage_schema_sha256="b" * 64,
                )
                self.assertEqual(1, invalidated)
                job = conn.execute(
                    """
                    SELECT status,thinking_mode,reasoning_effort
                    FROM semantic_review_jobs WHERE entry_id=?
                    """,
                    (candidate.entry_id,),
                ).fetchone()
                self.assertEqual(("stale", "disabled", None), tuple(job))
                stages = conn.execute(
                    """
                    SELECT mode,status,thinking_mode,reasoning_effort
                    FROM semantic_review_stages WHERE entry_id=? ORDER BY mode
                    """,
                    (candidate.entry_id,),
                ).fetchall()
                self.assertEqual(2, len(stages))
                self.assertTrue(
                    all(
                        row["status"] == "stale"
                        and row["thinking_mode"] == "disabled"
                        and row["reasoning_effort"] is None
                        for row in stages
                    )
                )
            finally:
                conn.close()

    def test_merged_artifact_rejects_directional_thinking_provenance_drift(self) -> None:
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
                    stage_schema_sha256="b" * 64,
                )
                for mode in reviewer.REVIEW_MODES:
                    response = stage_response(candidate, mode)
                    parsed = reviewer.parse_stage_reviews(
                        response,
                        [candidate],
                        mode=mode,
                        schema_validator=stage_schema_validator(),
                    )
                    reviewer.mark_stage_reviews(
                        conn,
                        parsed,
                        [candidate],
                        mode=mode,
                        raw_response=response,
                        provider_meta={
                            "response_id": f"fake-{mode}",
                            "usage": {},
                            "model": "deepseek-v4-flash",
                            "requested_model": "deepseek-v4-flash",
                            "thinking_mode": reviewer.THINKING_MODE,
                            "reasoning_effort": reviewer.REASONING_EFFORT,
                        },
                    )
                with conn:
                    conn.execute(
                        """
                        UPDATE semantic_review_stages SET thinking_mode='enabled'
                        WHERE entry_id=? AND mode='contradiction'
                        """,
                        (candidate.entry_id,),
                    )
                with self.assertRaisesRegex(RuntimeError, "one thinking mode"):
                    reviewer._merged_provider_artifacts(
                        conn,
                        [candidate],
                        model="deepseek-v4-flash",
                    )
            finally:
                conn.close()

    def test_legacy_rows_without_thinking_provenance_become_stale_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-v41.sqlite3"
            candidate = candidate_stub()
            now = reviewer.utc_now()
            input_json = reviewer.canonical_json(candidate.provider_input())
            with sqlite3.connect(path) as legacy:
                legacy.executescript(
                    """
                    CREATE TABLE semantic_review_jobs(
                        entry_id TEXT PRIMARY KEY,source_text_sha256 TEXT,
                        candidate_annotation_sha256 TEXT,source_work_id TEXT,
                        source_work_title TEXT,title TEXT,char_count INTEGER,input_json TEXT,
                        status TEXT,verdict TEXT,review_json TEXT,issues_json TEXT,
                        raw_response_json TEXT,attempts INTEGER,error_kind TEXT,
                        error_message TEXT,provider_response_id TEXT,prompt_tokens INTEGER,
                        completion_tokens INTEGER,total_tokens INTEGER,model TEXT,
                        provider_reported_model TEXT,prompt_version TEXT,prompt_sha256 TEXT,
                        review_schema_sha256 TEXT,created_at TEXT,updated_at TEXT
                    );
                    CREATE TABLE semantic_review_stages(
                        entry_id TEXT,mode TEXT,source_text_sha256 TEXT,
                        candidate_annotation_sha256 TEXT,input_json TEXT,status TEXT,
                        stage_review_json TEXT,raw_response_json TEXT,attempts INTEGER,
                        error_kind TEXT,error_message TEXT,provider_response_id TEXT,
                        prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,
                        model TEXT,provider_reported_model TEXT,prompt_version TEXT,
                        prompt_sha256 TEXT,stage_schema_sha256 TEXT,created_at TEXT,
                        updated_at TEXT,PRIMARY KEY(entry_id,mode)
                    );
                    """
                )
                legacy.execute(
                    """
                    INSERT INTO semantic_review_jobs(
                        entry_id,source_text_sha256,candidate_annotation_sha256,
                        source_work_id,source_work_title,title,char_count,input_json,status,
                        verdict,review_json,issues_json,raw_response_json,attempts,
                        provider_response_id,prompt_tokens,completion_tokens,total_tokens,
                        model,provider_reported_model,prompt_version,prompt_sha256,
                        review_schema_sha256,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate.entry_id,
                        candidate.source_text_sha256,
                        candidate.candidate_annotation_sha256,
                        candidate.source_work_id,
                        candidate.source_work_title,
                        candidate.title,
                        candidate.char_count,
                        input_json,
                        "pass",
                        "pass",
                        "{}",
                        "[]",
                        "{}",
                        1,
                        "legacy-response",
                        1,
                        1,
                        2,
                        "deepseek-v4-flash",
                        "deepseek-v4-flash",
                        reviewer.PROMPT_VERSION,
                        reviewer.sha256_text(reviewer.SYSTEM_PROMPT),
                        "a" * 64,
                        now,
                        now,
                    ),
                )
                for mode in reviewer.REVIEW_MODES:
                    legacy.execute(
                        """
                        INSERT INTO semantic_review_stages(
                            entry_id,mode,source_text_sha256,candidate_annotation_sha256,
                            input_json,status,stage_review_json,raw_response_json,attempts,
                            provider_response_id,prompt_tokens,completion_tokens,total_tokens,
                            model,provider_reported_model,prompt_version,prompt_sha256,
                            stage_schema_sha256,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            candidate.entry_id,
                            mode,
                            candidate.source_text_sha256,
                            candidate.candidate_annotation_sha256,
                            input_json,
                            "valid",
                            "{}",
                            "{}",
                            1,
                            f"legacy-{mode}",
                            1,
                            1,
                            2,
                            "deepseek-v4-flash",
                            "deepseek-v4-flash",
                            reviewer.STAGE_PROMPT_VERSIONS[mode],
                            reviewer.sha256_text(reviewer.SYSTEM_PROMPTS[mode]),
                            "b" * 64,
                            now,
                            now,
                        ),
                    )
            legacy.close()

            conn = reviewer.init_sidecar_db(path)
            try:
                migrated_job = conn.execute(
                    "SELECT thinking_mode,reasoning_effort FROM semantic_review_jobs"
                ).fetchone()
                self.assertEqual((None, None), tuple(migrated_job))
                migrated_stages = conn.execute(
                    "SELECT thinking_mode,reasoning_effort FROM semantic_review_stages"
                ).fetchall()
                self.assertEqual(
                    [(None, None), (None, None)],
                    [tuple(row) for row in migrated_stages],
                )

                invalidated = reviewer.enqueue_candidates(
                    conn,
                    [candidate],
                    model="deepseek-v4-flash",
                    schema_sha256="a" * 64,
                    stage_schema_sha256="b" * 64,
                )
                self.assertEqual(1, invalidated)
                job = conn.execute(
                    """
                    SELECT status,thinking_mode,reasoning_effort,raw_response_json,
                           provider_response_id,total_tokens
                    FROM semantic_review_jobs
                    """
                ).fetchone()
                self.assertEqual(("stale", "disabled", None, None, None, None), tuple(job))
                stages = conn.execute(
                    """
                    SELECT status,thinking_mode,reasoning_effort,raw_response_json,
                           provider_response_id,total_tokens
                    FROM semantic_review_stages ORDER BY mode
                    """
                ).fetchall()
                self.assertEqual(
                    [("stale", "disabled", None, None, None, None)] * 2,
                    [tuple(row) for row in stages],
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
                    conn,
                    [candidate],
                    model="deepseek-v4-flash",
                    schema_sha256="a" * 64,
                    stage_schema_sha256=reviewer.sha256_file(STAGE_SCHEMA_PATH),
                )
                reviewer.write_manifest(
                    conn,
                    manifest,
                    source_db=directory / "source.sqlite3",
                    annotation_db=directory / "annotations.sqlite3",
                    sidecar_db=sidecar,
                    schema_path=SCHEMA_PATH,
                    stage_schema_path=STAGE_SCHEMA_PATH,
                    effective_db=None,
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
            self.assertEqual(2, payload["directional_input_saved_rows"])
            self.assertEqual(2, payload["directional_stage_rows_expected"])
            self.assertEqual(
                reviewer.STAGE_PROMPT_VERSIONS, payload["stage_prompt_versions"]
            )
            self.assertEqual(1, payload["oversized_complete_single_records"])
            self.assertEqual(7584, payload["maximum_source_characters"])
            self.assertEqual(0, payload["truncated_source_records"])
            self.assertEqual(0, payload["canonical_overwrite_count"])
            self.assertEqual(4, payload["current_run_provider_call_limit"])
            self.assertEqual("disabled", payload["thinking_mode"])
            self.assertIsNone(payload["reasoning_effort"])
            self.assertEqual(0, payload["temperature"])


if __name__ == "__main__":
    unittest.main()
