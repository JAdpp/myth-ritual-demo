from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "data"))

import validate_a1_annotations as validator  # noqa: E402


def annotation_stub(
    *,
    flags: list[str] | None = None,
    safety_status: str = "auto_screened",
) -> dict:
    return {
        "source_profile": {
            "title": "舊題",
            "source_text": "後來有人記下此事。",
        },
        "retrieval_profile": {
            "modern_retrieval_summary": "后来发生了一场变化。",
            "narrative_sufficiency": "sufficient",
            "narrative_sufficiency_reason": "原文足以构成一个完整事件。",
            "key_entities": [
                {"name": "旅人", "role": "当事人", "evidence_ids": ["ev01"]}
            ],
            "plot_beats": [
                {"type": "trigger", "text": "旅人遇到变故。", "evidence_ids": ["ev01"]}
            ],
            "motif_terms": ["选择"],
            "summary_evidence_ids": ["ev01"],
        },
        "life_context": [],
        "narrative_arc": {
            "trigger": "一次意外打破原有处境。",
            "conflict_types": [],
            "agency_modes": ["unknown"],
            "ending_mode": "unknown",
        },
        "auto_safety_screen": {
            "status": safety_status,
            "flags": flags if flags is not None else ["none_identified"],
            "interpretation_risks": ["none_identified"],
            "uncertainties": [],
            "model_preannotation": True,
        },
        "evidence": [
            {
                "id": "ev01",
                "locator": "舊定位",
                "excerpt": "古文原文片段",
                "start_char": 0,
                "end_char": 6,
                "supports": [
                    "retrieval_profile.modern_retrieval_summary",
                    "narrative_arc.trigger",
                ],
            }
        ],
    }


class GeneratedChineseGateTests(unittest.TestCase):
    def test_source_and_evidence_are_never_simplified(self) -> None:
        annotation = annotation_stub()
        self.assertEqual([], validator.generated_text_issues(annotation))

    def test_traditional_form_in_generated_field_fails(self) -> None:
        annotation = annotation_stub()
        annotation["retrieval_profile"]["modern_retrieval_summary"] = "後来发生了一场变化。"
        issues = validator.generated_text_issues(annotation)
        self.assertEqual(1, len(issues))
        self.assertEqual("GENERATED_TEXT_NOT_SIMPLIFIED", issues[0][0])
        self.assertIn("modern_retrieval_summary", issues[0][1])


class EvidenceSupportGateTests(unittest.TestCase):
    SOURCE = "古文原文片段"

    def test_baseline_supports_are_valid(self) -> None:
        self.assertEqual([], validator.evidence_semantic_issues(annotation_stub(), self.SOURCE))

    def test_dangling_support_fails(self) -> None:
        annotation = annotation_stub()
        annotation["evidence"][0]["supports"].append("auto_safety_screen.flags.death")
        issues = validator.evidence_semantic_issues(annotation, self.SOURCE)
        self.assertIn("EVIDENCE_SUPPORT_DANGLING", {code for code, _ in issues})

    def test_unknown_and_none_identified_supports_fail(self) -> None:
        for support in (
            "narrative_arc.agency_modes.unknown",
            "auto_safety_screen.flags.none_identified",
        ):
            with self.subTest(support=support):
                annotation = annotation_stub()
                annotation["evidence"][0]["supports"].append(support)
                issues = validator.evidence_semantic_issues(annotation, self.SOURCE)
                self.assertIn("EVIDENCE_SUPPORT_NONCLAIM", {code for code, _ in issues})


class SafetyGateTests(unittest.TestCase):
    def test_sexual_violence_requires_all_cross_labels(self) -> None:
        annotation = annotation_stub(flags=["sexual_violence"])
        issues = validator.safety_quality_issues(annotation, "恶人强奸女子。")
        details = "\n".join(detail for _, detail in issues)
        self.assertIn("sexual_content", details)
        self.assertIn("coercion_or_abuse", details)
        self.assertIn("SAFETY_CROSS_LABEL_MISSING", {code for code, _ in issues})

    def test_fatal_self_harm_requires_death(self) -> None:
        annotation = annotation_stub(flags=["self_harm_or_suicide"])
        issues = validator.safety_quality_issues(annotation, "他自刎而死。")
        self.assertIn(
            ("SAFETY_CROSS_LABEL_MISSING", "explicit fatal self-harm also requires death"),
            issues,
        )

    def test_class_a_animal_phrases_and_name_false_positives(self) -> None:
        annotation = annotation_stub(flags=[])
        for source in (
            "怪物攫马而食。",
            "村人杀狗熊。",
            "猎者断其线，两物皆死。",
        ):
            with self.subTest(source=source):
                issues = validator.safety_quality_issues(annotation, source)
                self.assertTrue(
                    any(
                        code == "SAFETY_EXPLICIT_PHRASE_MISSING_FLAGS"
                        and "explicit_animal_harm" in detail
                        for code, detail in issues
                    )
                )

        for source in (
            "牛成章病死。",
            "石虎率军而来。",
            "鸟使召我矣，某日当死。",
        ):
            with self.subTest(source=source):
                issues = validator.safety_quality_issues(annotation, source)
                self.assertFalse(
                    any("explicit_animal_harm" in detail for _, detail in issues)
                )

    def test_class_b_exclusions_do_not_fail(self) -> None:
        annotation = annotation_stub(flags=[])
        issues = validator.safety_quality_issues(
            annotation,
            "来客投刺，卒得免，又问押韵，何以不明押；诗中开金锁，又往东狱行宫。",
        )
        self.assertNotIn("SAFETY_SIGNAL_UNCOVERED", {code for code, _ in issues})

    def test_unknown_status_does_not_waive_uncovered_signal(self) -> None:
        annotation = annotation_stub(flags=[], safety_status="unknown")
        issues = validator.safety_quality_issues(annotation, "此人患病多年。")
        self.assertIn("SAFETY_SIGNAL_UNCOVERED", {code for code, _ in issues})

    def test_compatible_flag_covers_class_b_signal(self) -> None:
        annotation = annotation_stub(flags=["illness"])
        issues = validator.safety_quality_issues(annotation, "此人患病多年。")
        self.assertNotIn("SAFETY_SIGNAL_UNCOVERED", {code for code, _ in issues})


if __name__ == "__main__":
    unittest.main()
