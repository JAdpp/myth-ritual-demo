from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Lock, current_thread

from jsonschema import Draft202012Validator, FormatChecker
from opencc import OpenCC
import pytest

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


@pytest.mark.parametrize("worker_count", [8, 16])
def test_simplified_conversion_is_thread_local_and_complete(worker_count) -> None:
    barrier = Barrier(worker_count)

    def convert_in_worker(_: int) -> tuple[int, tuple[str, ...]]:
        converter_id = id(annotator.simplified_converter())
        barrier.wait(timeout=5)
        converted = tuple(
            annotator.to_simplified_generated_text(text)
            for text in (
                "齋戒誦經",
                "後來發現",
                "萬卷書",
                "董吉是於潛人，世代奉佛",
            )
            * 20
        )
        return converter_id, converted

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(convert_in_worker, range(worker_count)))

    assert len({converter_id for converter_id, _ in results}) == worker_count
    expected = (
        "斋戒诵经",
        "后来发现",
        "万卷书",
        "董吉是于潜人，世代奉佛",
    ) * 20
    assert all(converted == expected for _, converted in results)


def test_valid_row_simplification_repair_preserves_provenance(tmp_path) -> None:
    entry = source_entry("張生遭強姦後被囚禁於獄，終於逃出。此事由鄰人記下，以警後來者。")
    annotation = {
        "annotation_record": normalise(entry),
    }
    annotation["annotation_record"]["retrieval_profile"][
        "modern_retrieval_summary"
    ] = "人物遭遇侵害與拘禁，之後設法離開，原文僅提供簡短經過。"
    provenance_before = json.dumps(
        {
            "unit": annotation["annotation_record"]["unit"],
            "source_profile": annotation["annotation_record"]["source_profile"],
            "evidence": annotation["annotation_record"]["evidence"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    conn = annotator.init_derived_db(tmp_path / "annotations.sqlite3")
    annotator.enqueue(conn, [entry])
    annotator.mark_valid(
        conn,
        entry.entry_id,
        annotation,
        {"response_id": "fixture", "usage": {}, "model": "deepseek-v4-flash"},
    )

    repaired_records, repaired_fields = annotator.repair_valid_generated_texts(
        conn,
        schema_validator=VALIDATOR,
    )
    stored = json.loads(
        conn.execute(
            "SELECT annotation_json FROM annotation_jobs WHERE entry_id=?",
            (entry.entry_id,),
        ).fetchone()[0]
    )
    conn.close()

    assert (repaired_records, repaired_fields) == (1, 1)
    assert stored["annotation_record"]["retrieval_profile"][
        "modern_retrieval_summary"
    ] == "人物遭遇侵害与拘禁，之后设法离开，原文仅提供简短经过。"
    provenance_after = json.dumps(
        {
            "unit": stored["annotation_record"]["unit"],
            "source_profile": stored["annotation_record"]["source_profile"],
            "evidence": stored["annotation_record"]["evidence"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    assert provenance_after == provenance_before


def test_valid_unknown_trigger_support_repair_removes_only_nonclaim_link(tmp_path) -> None:
    entry = source_entry("甲受命远行，途中折返，将所见告知乡人。其事首尾简略，未载其他。")
    annotation = {"annotation_record": normalise(entry)}
    record = annotation["annotation_record"]
    record["narrative_arc"]["trigger"] = "unknown"
    record["evidence"][0]["supports"].append("narrative_arc.trigger")
    evidence_before = [
        (item["excerpt"], item["start_char"], item["end_char"])
        for item in record["evidence"]
    ]
    conn = annotator.init_derived_db(tmp_path / "annotations.sqlite3")
    annotator.enqueue(conn, [entry])
    annotator.mark_valid(
        conn,
        entry.entry_id,
        annotation,
        {"response_id": "fixture", "usage": {}, "model": "deepseek-v4-flash"},
    )

    repaired_records, removed_links = annotator.repair_valid_unknown_trigger_supports(
        conn,
        schema_validator=VALIDATOR,
    )
    stored = json.loads(
        conn.execute(
            "SELECT annotation_json FROM annotation_jobs WHERE entry_id=?",
            (entry.entry_id,),
        ).fetchone()[0]
    )["annotation_record"]
    conn.close()

    assert (repaired_records, removed_links) == (1, 1)
    assert all(
        "narrative_arc.trigger" not in item["supports"] for item in stored["evidence"]
    )
    assert [
        (item["excerpt"], item["start_char"], item["end_char"])
        for item in stored["evidence"]
    ] == evidence_before


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


def test_explicit_no_trigger_statement_normalizes_to_unknown_without_evidence() -> None:
    entry = source_entry("班孟展示飞行、入地与喷墨成字等异能，末后进入山中。")
    raw = raw_annotation(entry)
    raw["narrativeSufficiency"] = "sufficient"
    raw["narrativeSufficiencyReason"] = "原文有明确人物和连续动作。"
    raw["narrativeArc"] = {
        "trigger": "无明确冲突触发，仅为异能展示。",
        "conflictTypes": [],
        "agencyModes": ["unknown"],
        "endingMode": "unknown",
    }

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


@pytest.mark.parametrize("worker_count", [8, 16])
def test_parallel_worker_offline_run_closes_wal_and_call_ledgers(
    tmp_path, monkeypatch, capsys, worker_count
) -> None:
    """Exercise the production worker orchestration without any HTTP calls."""

    record_count = 512
    batch_size = 6
    batch_count = (record_count + batch_size - 1) // batch_size
    source_db = tmp_path / "source.sqlite3"
    source_conn = sqlite3.connect(source_db)
    source_conn.execute(
        """
        CREATE TABLE entries(
            entry_id TEXT PRIMARY KEY,
            source_work_id TEXT,
            source_work_title TEXT,
            source_work_period TEXT,
            volume TEXT,
            source_locator TEXT,
            entry_ordinal INTEGER,
            title TEXT,
            source_url TEXT,
            char_count INTEGER,
            text TEXT,
            extracted_text_sha256 TEXT,
            dedupe_status TEXT,
            runtime_eligible INTEGER
        )
        """
    )
    source_rows = []
    for index in range(record_count):
        text = f"甲奉命远行，事毕归来。第{index:03d}则。"
        source_rows.append(
            (
                f"c1ws_{index:024x}",
                "fixture-work",
                "测试古籍",
                "清",
                "卷一",
                f"卷一·第{index + 1}则",
                index + 1,
                f"测试条目{index + 1}",
                "https://example.test/source",
                len(text),
                text,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "canonical",
                1,
            )
        )
    source_conn.executemany(
        "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", source_rows
    )
    source_conn.commit()
    source_conn.close()

    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps({"catalog_version": "fixture-catalog-v1"}), encoding="utf-8"
    )
    output_dir = tmp_path / "annotations"
    conversion_lock = Lock()
    converter_ids_by_thread: dict[str, int] = {}

    class FakeParallelWorkerClient:
        model = "deepseek-v4-flash"
        available = True
        first_call_barrier = Barrier(worker_count)
        accounting_lock = Lock()
        worker_instances: list["FakeParallelWorkerClient"] = []
        worker_rpms: list[float] = []
        calls: list[tuple[int, str, tuple[str, ...]]] = []

        def __init__(self, *, rpm, timeout, max_tokens, call_budget=None) -> None:
            del timeout, max_tokens
            self.call_budget = call_budget
            self.call_count = 0
            self.is_first_call = True
            if call_budget is not None:
                with type(self).accounting_lock:
                    type(self).worker_instances.append(self)
                    type(self).worker_rpms.append(float(rpm))

        def complete(self, entries, retries, *, correction=None):
            del retries, correction
            if self.is_first_call:
                self.is_first_call = False
                type(self).first_call_barrier.wait(timeout=10)
            assert self.call_budget is not None
            self.call_budget.reserve()
            self.call_count += 1
            with type(self).accounting_lock:
                call_id = len(type(self).calls) + 1
                type(self).calls.append(
                    (
                        call_id,
                        current_thread().name,
                        tuple(entry.entry_id for entry in entries),
                    )
                )
            return (
                {
                    "annotations": [
                        {"entryId": entry.entry_id} for entry in entries
                    ]
                },
                {
                    "response_id": f"offline-fake-{call_id}",
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                    "model": self.model,
                },
            )

        def close(self) -> None:
            return None

    def fake_normalise(raw, entry, **kwargs):
        del raw, kwargs
        thread_name = current_thread().name
        converted = annotator.to_simplified_generated_text("齋戒誦經")
        assert converted == "斋戒诵经"
        with conversion_lock:
            converter_ids_by_thread[thread_name] = id(annotator.simplified_converter())
        return {"annotation_record": {"unit": {"unit_id": entry.entry_id}}}

    monkeypatch.setattr(annotator, "DeepSeekBatchClient", FakeParallelWorkerClient)
    monkeypatch.setattr(annotator, "normalise_annotation", fake_normalise)

    total_rpm = worker_count * 60_000
    result = annotator.main(
        [
            "--source-db",
            str(source_db),
            "--summary",
            str(summary_path),
            "--schema",
            str(ROOT / "contracts" / "a1-retrieval-annotation.schema.json"),
            "--output-dir",
            str(output_dir),
            "--sample-mode",
            "stable",
            "--batch-size",
            str(batch_size),
            "--batch-char-limit",
            "4000",
            "--workers",
            str(worker_count),
            "--rpm",
            str(total_rpm),
            "--retries",
            "0",
            "--max-provider-calls",
            str(batch_count),
            "--live",
            "--confirm-full-run",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0, captured.err
    assert "locked" not in captured.err.lower()
    assert len(FakeParallelWorkerClient.worker_instances) == worker_count
    assert all(client.call_count > 0 for client in FakeParallelWorkerClient.worker_instances)
    assert FakeParallelWorkerClient.worker_rpms == [total_rpm / worker_count] * worker_count
    assert sum(FakeParallelWorkerClient.worker_rpms) == total_rpm
    assert annotator.per_worker_rpm(40, worker_count) == 40 / worker_count
    assert annotator.per_worker_rpm(40, 0) == 0
    assert len(converter_ids_by_thread) == worker_count
    assert len(set(converter_ids_by_thread.values())) == worker_count
    assert len(FakeParallelWorkerClient.calls) == batch_count
    assert len({call_id for call_id, _, _ in FakeParallelWorkerClient.calls}) == batch_count

    ledger_path = output_dir / "annotations.sqlite3"
    ledger = sqlite3.connect(ledger_path)
    status_counts = dict(
        ledger.execute(
            "SELECT status,COUNT(*) FROM annotation_jobs GROUP BY status"
        ).fetchall()
    )
    row = ledger.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT entry_id),
               COUNT(DISTINCT provider_response_id), SUM(attempts)
        FROM annotation_jobs
        """
    ).fetchone()
    integrity = ledger.execute("PRAGMA integrity_check").fetchone()[0]
    journal_mode = ledger.execute("PRAGMA journal_mode").fetchone()[0]
    last_event = ledger.execute(
        "SELECT event_type,detail_json FROM run_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    ledger.close()

    assert status_counts == {"valid": record_count}
    assert row == (record_count, record_count, batch_count, record_count)
    assert integrity == "ok"
    assert journal_mode == "wal"
    assert last_event[0] == "run_finished"
    terminal_detail = json.loads(last_event[1])
    assert terminal_detail["providerCalls"] == batch_count
    assert terminal_detail["completedBatches"] == batch_count
    assert terminal_detail["plannedBatches"] == batch_count

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status_counts"] == {"valid": record_count}
    assert manifest["current_run_provider_calls"] == batch_count
    assert manifest["current_run_max_provider_calls"] == batch_count
    assert manifest["current_run_workers"] == worker_count
    assert manifest["ledger_record_attempts"] == record_count

    exported = [
        json.loads(line)
        for line in (output_dir / "annotations.valid.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(exported) == record_count
    assert len({item["annotation_record"]["unit"]["unit_id"] for item in exported}) == record_count


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


def test_angle_bracket_edition_notes_without_outer_parentheses_may_be_bridged() -> None:
    fixtures = (
        (
            "火至〈「火至」二字原本無，據明抄本補。〉皆焚。長舒家正住下，分意燒燬",
            "火至皆焚。長舒家正住下，分意燒燬",
        ),
        (
            "沒官爲輕，改〈輕改字原作改輕，據宋孔平仲續世説一改〉從死",
            "沒官爲輕，改從死",
        ),
    )

    for source, candidate in fixtures:
        resolved = annotator.resolve_source_excerpt(source, candidate)
        assert resolved == source
        assert resolved in source


def test_fullwidth_square_bracket_edition_note_may_be_bridged() -> None:
    source = "北虜南［陸本無「南」字］犯南京。合圍方急，有穹龜見城中。"
    candidate = "北虜南犯南京。合圍方急，有穹龜見城中。"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source
    assert "［陸本無「南」字］" in resolved


@pytest.mark.parametrize(
    ("entry_id", "source", "raw_trigger"),
    (
        (
            "c1ws_7dd8ac23e3e3205ef2ad43f2",
            "有頭陀道人之(明鈔本作「入」。)學，至養□(葉本作「正」。)齋前，再三瞻視不去",
            "有頭陀道人之學，至養正齋前，再三瞻視不去",
        ),
        (
            "c1ws_003657bb5d3edec1f097ae26",
            "變怪驟興，正晝鬼見形於中庭，窺户嘯梁，移床徙釡，(葉本作「几」。)"
            "歌笑馳走，百端千態，舉室怖駭，寢食不安。",
            "變怪驟興，正晝鬼見形於中庭，窺户嘯梁，移床徙釜，"
            "歌笑馳走，百端千態，舉室怖駭，寢食不安。",
        ),
    ),
    ids=("建康頭陀", "王直夫"),
)
def test_real_quarantine_trigger_maps_back_to_short_exact_source(
    entry_id: str,
    source: str,
    raw_trigger: str,
) -> None:
    """Replay the two quarantined trigger quotes without reading the live ledger."""
    resolved = annotator.resolve_source_excerpt(source, raw_trigger)

    assert resolved is not None, entry_id
    assert resolved == source
    assert resolved in source
    assert len(resolved) <= 80


def test_unlisted_rare_variant_is_not_silently_normalized() -> None:
    source = "甲神乙記下此事。"
    candidate = "甲神乙記下此事。"

    assert "神" not in annotator.EVIDENCE_SOURCE_VARIANTS
    assert annotator.resolve_source_excerpt(source, candidate) is None


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


def test_known_wikisource_missing_glyph_placeholder_maps_to_untouched_source() -> None:
    marker = annotator.WIKISOURCE_MISSING_GLYPH_PLACEHOLDER
    source = marker * 3 + "董掌奏記府主褊急。" + marker * 2 + "詣梁園勸梁太祖入中原。"
    candidate = "董掌奏記府主褊急。詣梁園勸梁太祖入中原。"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source[3:]
    assert marker in resolved
    assert resolved in source


def test_other_private_use_characters_are_not_silently_dropped() -> None:
    source = "甲" + "\ue434" + "乙記下此事。"

    assert annotator.resolve_source_excerpt(source, "甲乙記下此事。") is None


def test_one_unmarked_sentence_omission_may_bridge_unique_exact_anchors() -> None:
    source = (
        "忽有一人排闥叫呼，相貌粗黑，言辭鄙陋，腰插騾鞭，"
        "如隨商客騾馱者。罵曰"
    )
    candidate = "忽有一人排闥叫呼，相貌粗黑，言辭鄙陋，腰插騾鞭，罵曰"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source
    assert resolved in source


def test_unmarked_omission_rejects_no_boundary_or_ambiguous_anchors() -> None:
    no_boundary = "甲帶長劍徒步越山乙在門外等候"
    ambiguous = "甲出門。中途歇息。乙歸來。甲出門。另走小徑。乙歸來。"

    assert annotator.resolve_source_excerpt(
        no_boundary,
        "甲帶長劍乙在門外等候",
    ) is None
    assert annotator.resolve_source_excerpt(
        ambiguous,
        "甲出門。乙歸來。",
    ) is None


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


def test_segment_order_uses_recovered_span_when_quote_closes_after_omission() -> None:
    source = (
        "馬生云：「三人俱貴達。大李少府位極人臣。"
        "從今後十年，家有大難，兄弟並流，唯公與一弟獲全。"
        "又十年之後，方卻得官。」"
    )
    candidate = (
        "馬生云：「三人俱貴達。……"
        "從今後十年，家有大難，兄弟並流，唯公與一弟獲全。」"
    )

    resolved = annotator.resolve_segmented_source_excerpts(source, candidate)

    assert resolved == [
        "馬生云：「三人俱貴達。",
        "從今後十年，家有大難，兄弟並流，唯公與一弟獲全。",
    ]
    assert all(item in source and len(item) <= 80 for item in resolved)


def test_multi_ellipsis_evidence_fails_closed_if_any_segment_is_ambiguous() -> None:
    source = "甲梦见鬼物。乙随后离开。甲梦见鬼物。丙终于醒来。"
    candidate = "甲梦见鬼物。……丙终于醒来。"

    assert annotator.resolve_segmented_source_excerpts(source, candidate) is None


def test_ascii_multi_ellipsis_resolves_as_separate_exact_source_quotes() -> None:
    source = (
        "忽有一女，自西乘馬而來，青衣老少數人隨後。"
        "女有殊色，所乘駿馬極佳。崔生未及細視，則已過矣。"
    )
    candidate = "忽有一女，自西乘馬而來...女有殊色...崔生未及細視，則已過矣。"

    resolved = annotator.resolve_segmented_source_excerpts(source, candidate)

    assert resolved == [
        "忽有一女，自西乘馬而來",
        "女有殊色",
        "崔生未及細視，則已過矣。",
    ]
    assert all(item in source and len(item) <= 80 for item in resolved)


def test_unmarked_omitted_source_sentence_resolves_only_exact_ordered_chunks() -> None:
    source = (
        "盧肇、丁稜之及第也，先是放榜訖，則須謁宰相。"
        "其導啟詞語，一出榜元者，俯仰疾徐，尤宜精審。"
        "時肇首冠，有故不至。次乃稜也。"
    )
    candidate = (
        "盧肇、丁稜之及第也，先是放榜訖，則須謁宰相。"
        "時肇首冠，有故不至。次乃稜也。"
    )

    assert annotator.resolve_segmented_source_excerpts(source, candidate) == [
        "盧肇、丁稜之及第也，先是放榜訖，則須謁宰相。",
        "時肇首冠，有故不至。",
        "次乃稜也。",
    ]
    assert (
        annotator.resolve_segmented_source_excerpts(
            source,
            "時肇首冠，有故不至。盧肇、丁稜之及第也，先是放榜訖，則須謁宰相。",
        )
        is None
    )


def test_edition_note_plus_punctuation_drift_maps_to_untouched_source() -> None:
    source = (
        "乃見承雲著通天冠，長八尺，自言〈（「言」原作「有」，據明抄本改）〉。"
        "為方伯，某第三子有雋才，方當與君周旋。"
    )
    candidate = "乃見承雲著通天冠，長八尺，自言為方伯，某第三子有雋才，方當與君周旋"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved == source[:-1]
    assert "據明抄本改" in resolved


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


def test_punctuation_and_whitespace_only_quote_drift_maps_to_exact_source() -> None:
    source = "\u5ffd\u4e00\u591c\u5922\u5100\u5f9e\u751a\u90fd\uff0c\u5fa1\u98a8\u800c\u884c\u3002\n\u81f3\u4e00\u8655\uff0c\u984d\u66f0\u300c\u542b\u5143\u6bbf\u300d\uff0c\u65c1\u8a2d\u516c\u5ea7\u3002"
    candidate = "\u5ffd\u4e00\u591c\u5922\u5100\u5f9e\u751a\u90fd\uff0c\u5fa1\u98a8\u800c\u884c\u3002\u81f3\u4e00\u8655\uff0c\u984d\u66f0\u542b\u5143\u6bbf\u3002"

    resolved = annotator.resolve_source_excerpt(source, candidate)

    assert resolved is not None
    assert resolved in source
    assert len(resolved) <= 80
    assert "\n" in resolved and "\u300c" in resolved


def test_punctuation_only_quote_recovery_rejects_lexical_or_ambiguous_drift() -> None:
    source = "\u7532\u5165\u9580\uff0c\u4e59\u5f8c\u4f86\u3002\u7532\u5165\u9580\uff0c\u4e59\u5f8c\u4f86\u3002"

    assert annotator.resolve_source_excerpt(source, "\u7532\u5165\u9580\u3002\u4e19\u5f8c\u4f86\u3002") is None
    assert annotator.resolve_source_excerpt(source, "\u7532\u5165\u9580\u3002\u4e59\u5f8c\u4f86\u3002") is None


def test_legacy_interpretation_risk_support_prefix_is_canonicalized() -> None:
    entry = source_entry("\u6b64\u5fc5\u5929\u547d\u4e5f\u3002\u4eba\u7686\u4fe1\u4e4b\u3002")
    evidence_map: dict[str, set[str]] = {}

    excerpt = annotator.add_evidence(
        evidence_map,
        entry,
        "\u6b64\u5fc5\u5929\u547d\u4e5f\u3002",
        ["interpretation_risks.fatalism"],
    )

    assert evidence_map[excerpt] == {
        "auto_safety_screen.interpretation_risks.fatalism"
    }
