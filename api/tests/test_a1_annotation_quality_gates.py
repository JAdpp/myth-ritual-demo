from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from opencc import OpenCC

import data.annotate_c1_retrieval as annotator

from data.annotate_c1_retrieval import (
    AnnotationError,
    SourceEntry,
    compact_evidence_map,
    normalise_annotation,
    parse_batch,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "contracts" / "a1-retrieval-annotation.schema.json").read_text(
        encoding="utf-8"
    )
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
T2S = OpenCC("t2s")


def source_entry(text: str) -> SourceEntry:
    return SourceEntry(
        entry_id="c1ws_aaaaaaaaaaaaaaaaaaaaaaaa",
        source_work_id="fixture-work",
        source_work_title="測試古籍",
        source_work_period="清",
        volume="卷一",
        source_locator="卷一·第一则",
        entry_ordinal=1,
        title="測試條目",
        source_url="https://example.test/source",
        char_count=len(text),
        text=text,
        extracted_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def raw_annotation(entry: SourceEntry) -> dict[str, object]:
    return {
        "entryId": entry.entry_id,
        "modernRetrievalSummary": "一名人物遭遇侵害與拘禁，之後設法離開，原文僅提供簡短經過。",
        "narrativeSufficiency": "unknown",
        "narrativeSufficiencyReason": "原文較短，僅作保守概括。",
        "keyEntities": [
            {"name": "張生", "role": "受害者", "evidenceExcerpt": entry.text[:12]}
        ],
        "plotBeats": [],
        "motifTerms": ["拘禁與逃生"],
        "lifeContext": [],
        "narrativeArc": {
            "trigger": "unknown",
            "conflictTypes": [],
            "agencyModes": ["unknown"],
            "endingMode": "unknown",
        },
        "autoSafetyScreen": {
            "status": "auto_screened",
            "flags": ["none_identified"],
            "interpretationRisks": ["none_identified"],
            "uncertainties": ["尚未經過人工審核。"],
        },
        "evidence": [
            {
                "excerpt": entry.text[:30],
                "supports": [
                    "retrieval_profile.modern_retrieval_summary",
                    "life_context.long_term_responsibility",
                    "narrative_arc.agency_modes.unknown",
                    "auto_safety_screen.flags.none_identified",
                ],
            }
        ],
        "overallConfidence": "high",
    }


def normalise(entry: SourceEntry, *, allow_local_repair: bool = False) -> dict[str, object]:
    return normalise_annotation(
        raw_annotation(entry),
        entry,
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
        allow_local_repair=allow_local_repair,
    )["annotation_record"]


def test_generated_prose_is_simplified_and_source_evidence_is_untouched() -> None:
    entry = source_entry("張生遭強姦後被囚禁於獄，終於逃出。此事由鄰人記下，以警後來者。")

    record = normalise(entry)
    retrieval = record["retrieval_profile"]
    safety = record["auto_safety_screen"]

    assert retrieval["modern_retrieval_summary"] == T2S.convert(
        retrieval["modern_retrieval_summary"]
    )
    assert retrieval["narrative_sufficiency_reason"] == T2S.convert(
        retrieval["narrative_sufficiency_reason"]
    )
    assert retrieval["key_entities"][0]["name"] == "张生"
    assert record["source_profile"]["source_text"] == entry.text
    assert any("強姦" in item["excerpt"] for item in record["evidence"])
    assert safety["uncertainties"][0] == "尚未经过人工审核。"


def test_explicit_sexual_violence_and_captivity_are_added_with_exact_evidence() -> None:
    entry = source_entry("張生遭強姦後被囚禁於獄，終於逃出。此事由鄰人記下，以警後來者。")

    record = normalise(entry)
    flags = set(record["auto_safety_screen"]["flags"])
    evidence = record["evidence"]
    supports = {support for item in evidence for support in item["supports"]}

    assert {"sexual_violence", "sexual_content", "coercion_or_abuse", "captivity"} <= flags
    assert "none_identified" not in flags
    for label in {"sexual_violence", "sexual_content", "coercion_or_abuse", "captivity"}:
        assert f"auto_safety_screen.flags.{label}" in supports
    for item in evidence:
        assert item["excerpt"] == entry.text[item["start_char"] : item["end_char"]]


def test_pruned_or_unknown_labels_cannot_leave_dangling_supports() -> None:
    entry = source_entry("甲受命遠行，途中折返，將所見告知鄉人。其事首尾簡略，未載其他。")

    record = normalise(entry)
    supports = {support for item in record["evidence"] for support in item["supports"]}

    assert "life_context.long_term_responsibility" not in supports
    assert "narrative_arc.agency_modes.unknown" not in supports
    assert "auto_safety_screen.flags.none_identified" not in supports
    assert supports == {"retrieval_profile.modern_retrieval_summary"}


def test_explicit_animal_death_cannot_remain_none_identified() -> None:
    entry = source_entry("獵人杖殺狗熊，又殺猛虎煮肉，同行者分食。兩獸皆死，眾人方歸。")

    record = normalise(entry)
    flags = set(record["auto_safety_screen"]["flags"])

    assert "animal_harm" in flags
    assert "none_identified" not in flags


def test_insufficient_record_may_return_an_empty_trigger() -> None:
    entry = source_entry("此条仅存题名与一句残文，人物行动和结果都未详载，暂无法重建完整叙事。")
    raw = raw_annotation(entry)
    raw["modernRetrievalSummary"] = (
        "该条逐项解释旧时器物名称和词义，未形成含人物行动与结果的完整叙事。"
    )
    raw["narrativeSufficiency"] = "insufficient"
    raw["narrativeArc"]["trigger"] = ""

    record = normalise_annotation(
        raw,
        entry,
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
    )["annotation_record"]

    assert record["narrative_arc"]["trigger"] == "未知"
    supports = {support for item in record["evidence"] for support in item["supports"]}
    assert "narrative_arc.trigger" not in supports


def test_broad_death_language_requires_a_compatible_warning() -> None:
    entry = source_entry("员外归家数日后卒，家人料理后事，乡人记下此事。其余细节不详。")

    try:
        normalise(entry)
    except AnnotationError as exc:
        assert "uncovered safety signal death_language" in str(exc)
    else:  # pragma: no cover - documents the fail-closed expectation
        raise AssertionError("uncovered death language must not be automatically validated")


def test_final_retry_adds_a_conservative_broad_warning_with_exact_evidence() -> None:
    entry = source_entry("员外归家数日后卒，家人料理后事，乡人记下此事。其余细节不详。")

    record = normalise(entry, allow_local_repair=True)
    safety = record["auto_safety_screen"]

    assert "death" in safety["flags"]
    assert safety["status"] == "unknown"
    assert any("本地规则保守补标" in item for item in safety["uncertainties"])
    assert record["annotation_meta"]["overall_confidence"] == "low"
    assert any(
        "auto_safety_screen.flags.death" in item["supports"]
        and item["excerpt"] == entry.text[item["start_char"] : item["end_char"]]
        for item in record["evidence"]
    )


def test_final_retry_sentence_bounds_an_overlong_summary() -> None:
    entry = source_entry("甲受命远行，沿途记下所见，回乡后将经历告知众人。其事首尾俱在。")
    raw = raw_annotation(entry)
    raw["modernRetrievalSummary"] = (
        "甲受命远行并记录沿途所见，回乡后向众人讲述经历。" * 12
    )

    record = normalise_annotation(
        raw,
        entry,
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
        allow_local_repair=True,
    )["annotation_record"]

    summary = record["retrieval_profile"]["modern_retrieval_summary"]
    assert 20 <= len(summary) <= 250
    assert summary.endswith("。")
    assert record["annotation_meta"]["overall_confidence"] == "low"


def test_records_envelope_is_accepted_when_child_contract_and_ids_match() -> None:
    entry = source_entry("甲受命远行，沿途记下所见，回乡后将经历告知众人。其事首尾俱在。")

    parsed = parse_batch(
        {"records": [raw_annotation(entry)]},
        [entry],
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
    )

    assert set(parsed) == {entry.entry_id}


def test_complete_narrative_may_have_no_life_context_label() -> None:
    entry = source_entry("甲从城东出发，沿河行至邻县，交付书信后原路归家，前后经过俱详。")
    raw = raw_annotation(entry)
    raw["narrativeSufficiency"] = "sufficient"
    raw["narrativeSufficiencyReason"] = None
    raw["narrativeArc"]["trigger"] = "甲受命从城东出发递送书信。"
    raw["evidence"][0]["supports"].append("narrative_arc.trigger")

    record = normalise_annotation(
        raw,
        entry,
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
    )["annotation_record"]

    assert record["retrieval_profile"]["narrative_sufficiency"] == "sufficient"
    assert record["life_context"] == []


def test_overlong_exact_evidence_is_bounded_without_rewriting_source() -> None:
    sentence = "甲奉命入山寻访异人，归来后把所见所闻逐一记下。"
    entry = source_entry(sentence * 45)
    raw = raw_annotation(entry)
    raw["evidence"][0]["excerpt"] = entry.text

    record = normalise_annotation(
        raw,
        entry,
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
    )["annotation_record"]

    evidence = record["evidence"][0]
    assert 2 <= len(evidence["excerpt"]) <= 80
    assert evidence["excerpt"] in entry.text
    assert evidence["excerpt"] == entry.text[
        evidence["start_char"] : evidence["end_char"]
    ]
    assert evidence["excerpt"].endswith("。")


def test_evidence_compaction_preserves_every_required_support() -> None:
    source = "".join(f"第{index:02d}则原文。" for index in range(30))
    evidence_map = {
        f"第{index:02d}则原文。": {
            "retrieval_profile.modern_retrieval_summary",
            f"auto_safety_screen.flags.flag_{index % 5}",
        }
        for index in range(30)
    }
    required = {
        "retrieval_profile.modern_retrieval_summary",
        *(f"auto_safety_screen.flags.flag_{index}" for index in range(5)),
    }
    compacted = compact_evidence_map(
        evidence_map,
        required_supports=set(required),
        preferred_excerpts=list(evidence_map)[:20],
        source_text=source,
        max_items=24,
    )

    assert len(compacted) == 24
    actual = {support for supports in compacted.values() for support in supports}
    assert set(required) <= actual
    assert all(excerpt in source for excerpt in compacted)


def test_partial_batch_retries_only_the_invalid_record(tmp_path, monkeypatch) -> None:
    first = source_entry("甲奉命出行，事毕归来。")
    second = replace(
        source_entry("乙奉命出行，事毕归来。"),
        entry_id="c1ws_bbbbbbbbbbbbbbbbbbbbbbbb",
    )
    db_path = tmp_path / "annotations.sqlite3"
    conn = annotator.init_derived_db(db_path)
    annotator.enqueue(conn, [first, second])

    class StubClient:
        model = "deepseek-v4-flash"

        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def complete(self, entries, retries, *, correction=None):
            self.calls.append([entry.entry_id for entry in entries])
            singleton_retry = len(entries) == 1
            return (
                {
                    "annotations": [
                        {
                            "entryId": entry.entry_id,
                            "locallyValid": entry.entry_id == first.entry_id
                            or singleton_retry,
                        }
                        for entry in entries
                    ]
                },
                {"response_id": "stub", "usage": {}, "model": self.model},
            )

    def fake_normalise(raw, entry, **kwargs):
        if not raw["locallyValid"]:
            raise AnnotationError("fixture semantic failure")
        return {"annotation_record": {"unit": {"unit_id": entry.entry_id}}}

    monkeypatch.setattr(annotator, "normalise_annotation", fake_normalise)
    client = StubClient()
    annotator.process_batch(
        conn,
        client,
        [first, second],
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        retries=0,
    )
    rows = conn.execute(
        "SELECT entry_id,status FROM annotation_jobs ORDER BY entry_id"
    ).fetchall()
    conn.close()

    assert [call for call in client.calls] == [
        [first.entry_id, second.entry_id],
        [second.entry_id],
    ]
    assert {row["entry_id"]: row["status"] for row in rows} == {
        first.entry_id: "valid",
        second.entry_id: "valid",
    }


def test_provider_call_budget_is_a_hard_process_ceiling() -> None:
    budget = annotator.ProviderCallBudget(3)

    assert [budget.reserve(), budget.reserve(), budget.reserve()] == [1, 2, 3]
    assert budget.used == 3
    assert budget.exhausted is False
    try:
        budget.reserve()
    except annotator.ProviderCallBudgetExceeded as exc:
        assert "budget exhausted" in str(exc)
    else:  # pragma: no cover - paid-call protection must fail closed
        raise AssertionError("provider call ceiling was not enforced")
    assert budget.used == 3
    assert budget.exhausted is True


def test_failed_batch_attempt_counts_against_each_rows_semantic_budget(
    tmp_path, monkeypatch
) -> None:
    first = source_entry("甲奉命出行，事毕归来。")
    second = replace(
        source_entry("乙奉命出行，事毕归来。"),
        entry_id="c1ws_bbbbbbbbbbbbbbbbbbbbbbbb",
    )
    conn = annotator.init_derived_db(tmp_path / "annotations.sqlite3")
    annotator.enqueue(conn, [first, second])

    class StubClient:
        model = "deepseek-v4-flash"

        def __init__(self) -> None:
            self.call_count = 0

        def complete(self, entries, retries, *, correction=None):
            self.call_count += 1
            return (
                {
                    "annotations": [
                        {"entryId": entry.entry_id} for entry in entries
                    ]
                },
                {"response_id": "stub", "usage": {}, "model": self.model},
            )

    def always_invalid(*args, **kwargs):
        raise AnnotationError("fixture remains invalid")

    monkeypatch.setattr(annotator, "normalise_annotation", always_invalid)
    client = StubClient()
    annotator.process_batch(
        conn,
        client,
        [first, second],
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        retries=0,
        semantic_retries=2,
    )
    statuses = dict(
        conn.execute("SELECT entry_id,status FROM annotation_jobs").fetchall()
    )
    conn.close()

    # One shared batch attempt, then two remaining attempts per invalid row.
    assert client.call_count == 5
    assert statuses == {
        first.entry_id: "quarantined",
        second.entry_id: "quarantined",
    }


def test_full_wikisource_edition_note_may_be_bridged_without_fuzzy_rewrite() -> None:
    source = "少年齎一書，索〈（「索」原作「案」，據明抄本改）〉於黔巫之南。"
    candidate = "少年齎一書，索於黔巫之南。"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source
    assert resolved in source


def test_adverbial_zu_wei_does_not_force_a_death_warning() -> None:
    entry = source_entry(
        "後章蓋初為秀才，乃削髮卒為德士也。其人改換身份後，鄉里記其始末。"
    )
    raw = raw_annotation(entry)
    raw["autoSafetyScreen"]["flags"] = ["death"]
    raw["evidence"].append(
        {
            "excerpt": "卒",
            "supports": ["auto_safety_screen.flags.death"],
        }
    )

    record = normalise_annotation(
        raw,
        entry,
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
        allow_local_repair=True,
    )["annotation_record"]

    assert "death" not in record["auto_safety_screen"]["flags"]
    assert all(len(item["excerpt"]) >= 2 for item in record["evidence"])


def test_literal_death_zu_remains_a_broad_safety_signal() -> None:
    entry = source_entry("员外归家数日后卒，家人料理后事，乡人记下此事。其余细节不详。")

    record = normalise(entry, allow_local_repair=True)

    assert "death" in record["auto_safety_screen"]["flags"]


def test_one_explicit_ellipsis_may_bridge_a_unique_short_source_span() -> None:
    source = (
        "婦裝訖，出長帶，垂諸梁而結焉。訝之。婦從容跂雙彎，"
        "引頸受縊。才一著帶，目即含，眉即豎，舌出吻兩寸許，顏色慘變如鬼。"
    )
    candidate = (
        "婦裝訖，出長帶，垂諸梁而結焉。……"
        "引頸受縊。才一著帶，目即含，眉即豎，舌出吻兩寸許，顏色慘變如鬼。"
    )

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source
    assert len(resolved) <= 80


def test_ellipsis_bridge_rejects_ambiguous_or_overlong_anchors() -> None:
    ambiguous = "甲出门。中段一。乙归来。甲出门。中段二。乙归来。"
    overlong = "甲出门。" + "途中记事。" * 20 + "乙归来。"

    assert annotator.resolve_source_excerpt(ambiguous, "甲出门。……乙归来。") is None
    assert annotator.resolve_source_excerpt(overlong, "甲出门。……乙归来。") is None


def test_live_ledger_process_lock_is_exclusive_and_recoverable(tmp_path) -> None:
    lock_path = tmp_path / ".annotator.lock"
    first = annotator.acquire_process_lock(lock_path)
    try:
        try:
            annotator.acquire_process_lock(lock_path)
        except SystemExit as exc:
            assert "already holds" in str(exc)
        else:  # pragma: no cover - concurrent paid writers must fail closed
            raise AssertionError("a second ledger writer acquired the same lock")
    finally:
        annotator.release_process_lock(first)

    recovered = annotator.acquire_process_lock(lock_path)
    annotator.release_process_lock(recovered)


def test_unique_simplified_unquoted_evidence_maps_back_to_original_source() -> None:
    source = (
        "程志奪其所持，狐聞之，委于程曰：「此汝家賠錢貨，慎勿復失。」"
    )
    candidate = "程志夺其所持，狐闻之，委于程曰：此汝家赔钱货，慎勿复失。"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source
    assert resolved in source
    assert "奪" in resolved and "「" in resolved


def test_simplified_evidence_mapping_rejects_duplicate_or_overlong_spans() -> None:
    duplicate = "狐聞之，曰：「勿失。」狐聞之，曰：「勿失。」"
    overlong = "狐聞之，曰：「" + "此事甚詳。" * 20 + "勿失。」"

    assert annotator.resolve_source_excerpt(duplicate, "狐闻之，曰：勿失。") is None
    assert annotator.resolve_source_excerpt(
        overlong, "狐闻之，曰：" + "此事甚详。" * 20 + "勿失。"
    ) is None


def test_multi_ellipsis_evidence_resolves_as_separate_exact_source_quotes() -> None:
    source = (
        "得疾。於夢中了了見五六人，群立於庭。"
        "內一人取所佩篋櫝，出紙小幅，展而示之。"
        "勸周曰：「服此即安。」周默然受之。"
    )
    candidate = (
        "得疾。於夢中了了見五六人……"
        "內一人取所佩篋櫝，出紙小幅……"
        "勸周曰：「服此即安。」"
    )

    resolved = annotator.resolve_segmented_source_excerpts(source, candidate)

    assert resolved == [
        "得疾。於夢中了了見五六人",
        "內一人取所佩篋櫝，出紙小幅",
        "勸周曰：「服此即安。」",
    ]
    assert all(item in source and len(item) <= 80 for item in resolved)


def test_multi_ellipsis_evidence_fails_closed_if_any_segment_is_ambiguous() -> None:
    source = "甲梦见鬼物。乙随后离开。甲梦见鬼物。丙终于醒来。"
    candidate = "甲梦见鬼物。……丙终于醒来。"

    assert annotator.resolve_segmented_source_excerpts(source, candidate) is None


def test_source_footnotes_and_format_chars_map_back_to_untouched_evidence() -> None:
    source = "道士貨[93]梨，著\u200b絮衣[94]，丐[95]者咄之。"
    candidate = "道士货梨，著絮衣，丐者咄之。"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source
    assert "[93]" in resolved and "\u200b" in resolved


def test_source_footnote_mapping_still_requires_a_unique_normalized_quote() -> None:
    source = "貨[1]梨。旁人经过。貨[2]梨。"

    assert annotator.resolve_source_excerpt(source, "货梨。") is None


def test_final_retry_gives_insufficient_metadata_a_source_summary_anchor() -> None:
    entry = source_entry(
        "此条逐项解释旧时器物名称，并列出若干词义，不包含人物行动、冲突或结局。"
    )
    raw = raw_annotation(entry)
    raw["narrativeSufficiency"] = "insufficient"
    raw["narrativeSufficiencyReason"] = "原文是词语释义列表，不具备可辨识的叙事结构。"
    raw["narrativeArc"]["trigger"] = ""
    raw["evidence"] = []
    raw["keyEntities"] = []

    record = normalise_annotation(
        raw,
        entry,
        schema_validator=VALIDATOR,
        catalog_version="fixture-catalog-v1",
        model="deepseek-v4-flash",
        allow_local_repair=True,
    )["annotation_record"]

    assert record["retrieval_profile"]["narrative_sufficiency"] == "insufficient"
    assert any(
        "retrieval_profile.modern_retrieval_summary" in evidence["supports"]
        and evidence["excerpt"] in entry.text
        for evidence in record["evidence"]
    )
