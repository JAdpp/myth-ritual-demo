"""Validation for the 10k+ C1 single-story local Demo recommendation pool."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


DATA_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = DATA_ROOT / "corpus" / "c1_single_story"
SUMMARY_PATH = CORPUS_ROOT / "summary.json"
MANIFEST_PATH = CORPUS_ROOT / "manifest.json"
COLLECTION_MANIFEST_PATH = DATA_ROOT / "corpus" / "c0_collection_manifest.json"
MINIMUM_UNIQUE_STORIES = 10_000
MINIMUM_SOURCE_WORKS = 5
MINIMUM_TEXT_CHARS = 20
EXPECTED_LICENSE = "CC-BY-SA-4.0"
EXPECTED_NORMALIZATION_VERSION = "nfkc_punctuation_source_tail_v2"
EXPECTED_SNAPSHOT_COUNTS = {
    "raw_segments": 12_374,
    "rejected_segments": 20,
    "accepted_segments": 12_354,
    "exact_duplicate_segments": 1,
    "unique_single_stories": 12_353,
}
EXPECTED_WORK_COUNTS = {
    "taiping_guangji": 6_995,
    "yi_jian_zhi": 2_646,
    "yue_wei_cao_tang_bi_ji": 1_198,
    "zi_bu_yu": 745,
    "xu_zi_bu_yu": 277,
    "liao_zhai_zhi_yi": 492,
}
EXPECTED_BUILD_HASH = (
    "e7c0186068f00aaa310353b86807fa81e8c0d1cc93e13eb40818b52984748c22"
)


def fail(message: str) -> None:
    raise SystemExit(f"C1 STORY CORPUS VALIDATION FAILED: {message}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path}: {exc}")
    if not isinstance(payload, dict):
        fail(f"expected object in {path}")
    return payload


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(payload: str) -> str:
    return sha256_bytes(payload.encode("utf-8"))


def comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(
        r"(?:[〈<][^〉>]{0,160}(?:出|见|見)[^〉>]*[〉>])+$",
        "",
        normalized,
    )
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character)[0] not in {"P", "Z"}
    )


def required_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(f"record {record.get('entry_id')} lacks {key}")
    return value


def validate_record(record: dict[str, Any]) -> None:
    entry_id = required_string(record, "entry_id")
    if not re.fullmatch(r"c1ws_[0-9a-f]{24}", entry_id):
        fail(f"malformed entry_id: {entry_id}")
    if record.get("unit_type") != "single_story":
        fail(f"non-story unit leaked into corpus: {entry_id}")
    if record.get("corpus_tier") != "c1_source_story":
        fail(f"wrong corpus tier: {entry_id}")
    if record.get("provider") != "zh_wikisource":
        fail(f"unexpected provider: {entry_id}")
    if record.get("catalog_status") != "source_segmented_unreviewed":
        fail(f"unexpected catalog status: {entry_id}")
    expected_runtime_eligible = record.get("dedupe_status") == "canonical"
    if record.get("runtime_eligible") is not expected_runtime_eligible:
        fail(f"C1 Demo runtime eligibility mismatch: {entry_id}")
    if record.get("production_eligible") is not False:
        fail(f"C1 record is production eligible: {entry_id}")
    if record.get("embedding_allowed") is not False:
        fail(f"unreviewed C1 record permits embeddings: {entry_id}")
    if record.get("full_text_search_allowed") is not True:
        fail(f"full-text record lacks search permission: {entry_id}")
    if record.get("text_mode") != "full_text":
        fail(f"unexpected text mode: {entry_id}")
    if record.get("normalization_version") != EXPECTED_NORMALIZATION_VERSION:
        fail(f"unexpected normalization version: {entry_id}")
    if record.get("transcription_license") != EXPECTED_LICENSE:
        fail(f"missing CC BY-SA transcription license: {entry_id}")
    if record.get("dataset_license") != EXPECTED_LICENSE:
        fail(f"missing CC BY-SA dataset license: {entry_id}")
    if not str(record.get("source_url", "")).startswith(
        "https://zh.wikisource.org/wiki/"
    ):
        fail(f"non-Wikisource source URL: {entry_id}")
    required_string(record, "source_work_id")
    required_string(record, "source_work_title")
    required_string(record, "volume")
    required_string(record, "source_locator")
    required_string(record, "title")
    required_string(record, "source_key")
    required_string(record, "source_snapshot_sha256")

    text = required_string(record, "text")
    if len(text) < MINIMUM_TEXT_CHARS:
        fail(f"short/empty story passed filter: {entry_id}")
    if record.get("char_count") != len(text):
        fail(f"char_count mismatch: {entry_id}")
    if record.get("extracted_text_sha256") != sha256_text(text):
        fail(f"extracted text hash mismatch: {entry_id}")
    expected_normalized_hash = sha256_text(comparison_text(text))
    if record.get("normalized_text_sha256") != expected_normalized_hash:
        fail(f"normalized text hash mismatch: {entry_id}")
    if record.get("dedupe_status") not in {"canonical", "exact_duplicate"}:
        fail(f"unknown dedupe status: {entry_id}")
    required_string(record, "canonical_entry_id")


def resolve_manifest_path(relative_path: str) -> Path:
    target = (CORPUS_ROOT / relative_path).resolve()
    if CORPUS_ROOT.resolve() not in target.parents:
        fail(f"manifest path escapes corpus root: {relative_path}")
    return target


def validate() -> dict[str, Any]:
    summary = load_json(SUMMARY_PATH)
    manifest = load_json(MANIFEST_PATH)
    collection_manifest = load_json(COLLECTION_MANIFEST_PATH)

    if summary.get("corpus_tier") != "c1-source-single-story":
        fail("wrong summary corpus_tier")
    unique_declared = int(summary.get("unique_single_stories", 0))
    if unique_declared < MINIMUM_UNIQUE_STORIES:
        fail(
            f"unique story count {unique_declared} is below {MINIMUM_UNIQUE_STORIES}"
        )
    if int(summary.get("source_works", 0)) < MINIMUM_SOURCE_WORKS:
        fail("source diversity gate not met")
    if summary.get("license") != EXPECTED_LICENSE:
        fail("summary license mismatch")
    if summary.get("runtime_eligible") != unique_declared:
        fail("summary runtime-eligible count differs from canonical story count")
    if summary.get("recommendation_pool_stories") != unique_declared:
        fail("summary recommendation pool differs from canonical story count")
    if summary.get("recommendation_pool_mode") != "all_canonical_records":
        fail("summary does not enable the all-canonical Demo recommendation pool")
    if int(summary.get("curated_experience_stories", 0)) < 1:
        fail("summary lacks the enriched C3 story count")
    if summary.get("production_eligible") != 0:
        fail("summary claims production-eligible C1 records")
    if summary.get("near_duplicate_review_status") != "pending":
        fail("near-duplicate review boundary must remain explicit")
    if int(summary.get("full_text_stories", 0)) != unique_declared:
        fail("full-text and unique counts diverge")
    observed_snapshot_counts = {
        key: int(summary.get(key, -1)) for key in EXPECTED_SNAPSHOT_COUNTS
    }
    if observed_snapshot_counts != EXPECTED_SNAPSHOT_COUNTS:
        fail(
            "pinned snapshot counts changed: "
            f"{observed_snapshot_counts} != {EXPECTED_SNAPSHOT_COUNTS}"
        )

    summary_payload = SUMMARY_PATH.read_bytes()
    if manifest.get("summary_sha256") != sha256_bytes(summary_payload):
        fail("summary hash mismatch")

    shard_rows = manifest.get("shards")
    if not isinstance(shard_rows, list) or not shard_rows:
        fail("manifest has no shards")

    records_seen = 0
    canonical_seen = 0
    exact_duplicates_seen = 0
    entry_ids: set[str] = set()
    source_keys: set[str] = set()
    canonical_hashes: dict[str, str] = {}
    canonical_ids: set[str] = set()
    work_counts: Counter[str] = Counter()
    ordered_build_rows: list[str] = []

    for shard in shard_rows:
        if not isinstance(shard, dict):
            fail("malformed shard manifest row")
        relative_path = required_string(shard, "path")
        path = resolve_manifest_path(relative_path)
        if not path.is_file():
            fail(f"missing shard: {relative_path}")
        payload = path.read_bytes()
        if len(payload) != shard.get("bytes"):
            fail(f"shard byte count mismatch: {relative_path}")
        if sha256_bytes(payload) != shard.get("sha256"):
            fail(f"shard hash mismatch: {relative_path}")
        lines = payload.splitlines()
        if len(lines) != shard.get("rows"):
            fail(f"shard row count mismatch: {relative_path}")
        if len(lines) > 1_000:
            fail(f"shard exceeds 1,000-row limit: {relative_path}")
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                fail(f"invalid NDJSON in {relative_path}: {exc}")
            if not isinstance(record, dict):
                fail(f"non-object record in {relative_path}")
            validate_record(record)
            entry_id = str(record["entry_id"])
            source_key = str(record["source_key"])
            normalized_hash = str(record["normalized_text_sha256"])
            if entry_id in entry_ids:
                fail(f"duplicate entry_id: {entry_id}")
            if source_key in source_keys:
                fail(f"duplicate source_key: {source_key}")
            entry_ids.add(entry_id)
            source_keys.add(source_key)
            records_seen += 1

            if record["dedupe_status"] == "canonical":
                if normalized_hash in canonical_hashes:
                    fail(
                        "two canonical rows share normalized text: "
                        f"{entry_id}, {canonical_hashes[normalized_hash]}"
                    )
                if record["canonical_entry_id"] != entry_id:
                    fail(f"canonical row points elsewhere: {entry_id}")
                canonical_hashes[normalized_hash] = entry_id
                canonical_ids.add(entry_id)
                canonical_seen += 1
                work_counts[str(record["source_work_id"])] += 1
                ordered_build_rows.append(
                    f"{entry_id}:{normalized_hash}:{int(record['runtime_eligible'])}"
                )
            else:
                exact_duplicates_seen += 1

    if records_seen != manifest.get("records"):
        fail("manifest record count mismatch")
    if canonical_seen != manifest.get("canonical_records"):
        fail("manifest canonical count mismatch")
    if canonical_seen != unique_declared:
        fail("summary unique count mismatch")
    if exact_duplicates_seen != summary.get("exact_duplicate_segments"):
        fail("exact duplicate count mismatch")
    if int(summary.get("accepted_segments", 0)) != records_seen:
        fail("accepted segment count mismatch")
    if int(summary.get("raw_segments", 0)) != (
        records_seen + int(summary.get("rejected_segments", 0))
    ):
        fail("raw/accepted/rejected arithmetic mismatch")

    for shard in shard_rows:
        # Cross-check every duplicate pointer after all canonical IDs are known.
        path = resolve_manifest_path(str(shard["path"]))
        for line in path.read_bytes().splitlines():
            record = json.loads(line)
            if (
                record["dedupe_status"] == "exact_duplicate"
                and record["canonical_entry_id"] not in canonical_ids
            ):
                fail(f"orphan duplicate pointer: {record['entry_id']}")

    declared_work_rows = summary.get("work_counts")
    if not isinstance(declared_work_rows, list):
        fail("summary work_counts is not a list")
    declared_work_counts = {
        str(row.get("source_work_id")): int(row.get("count", -1))
        for row in declared_work_rows
        if isinstance(row, dict)
    }
    if declared_work_counts != dict(work_counts):
        fail(
            f"work counts mismatch: {declared_work_counts} != {dict(work_counts)}"
        )
    if len(declared_work_counts) != summary.get("source_works"):
        fail("source_works count mismatch")
    if sum(declared_work_counts.values()) != unique_declared:
        fail("work_counts do not sum to unique_single_stories")
    if declared_work_counts != EXPECTED_WORK_COUNTS:
        fail(
            "pinned work counts changed: "
            f"{declared_work_counts} != {EXPECTED_WORK_COUNTS}"
        )

    expected_build_hash = sha256_text("\n".join(ordered_build_rows))
    if summary.get("catalog_build_hash") != expected_build_hash:
        fail("catalog build hash mismatch")
    if expected_build_hash != EXPECTED_BUILD_HASH:
        fail(f"pinned catalog build hash changed: {expected_build_hash}")

    sqlite_info = manifest.get("sqlite")
    if not isinstance(sqlite_info, dict):
        fail("manifest lacks SQLite metadata")
    sqlite_path = resolve_manifest_path(str(sqlite_info.get("path", "")))
    sqlite_payload = sqlite_path.read_bytes()
    if len(sqlite_payload) != sqlite_info.get("bytes"):
        fail("SQLite byte count mismatch")
    if sha256_bytes(sqlite_payload) != sqlite_info.get("sha256"):
        fail("SQLite hash mismatch")
    with sqlite3.connect(sqlite_path) as connection:
        db_entries = int(connection.execute("SELECT count(*) FROM entries").fetchone()[0])
        fts_entries = int(
            connection.execute("SELECT count(*) FROM entries_fts").fetchone()[0]
        )
        invalid_runtime_rows = int(
            connection.execute(
                "SELECT count(*) FROM entries WHERE production_eligible != 0 "
                "OR (dedupe_status = 'canonical' AND runtime_eligible != 1) "
                "OR (dedupe_status != 'canonical' AND runtime_eligible != 0)"
            ).fetchone()[0]
        )
        recommendation_pool_rows = int(
            connection.execute(
                "SELECT count(*) FROM entries "
                "WHERE dedupe_status = 'canonical' AND runtime_eligible = 1"
            ).fetchone()[0]
        )
        search_probe = connection.execute(
            "SELECT entry_id FROM entries_fts WHERE entries_fts MATCH ? LIMIT 1",
            ("考城隍",),
        ).fetchone()
    if db_entries != records_seen:
        fail("SQLite entry count mismatch")
    if fts_entries != canonical_seen:
        fail("SQLite FTS count mismatch")
    if invalid_runtime_rows:
        fail("SQLite contains invalid Demo runtime or production eligibility")
    if recommendation_pool_rows != unique_declared:
        fail("SQLite recommendation pool differs from canonical story count")
    if not search_probe:
        fail("SQLite trigram FTS smoke test failed")

    source_rows = collection_manifest.get("sources")
    if not isinstance(source_rows, list) or len(source_rows) != len(work_counts):
        fail("collection manifest source count mismatch")
    source_hashes = {
        str(row.get("source_work_id")): str(row.get("epub_sha256"))
        for row in source_rows
        if isinstance(row, dict)
    }
    for shard in shard_rows:
        if str(shard["source_work_id"]) not in source_hashes:
            fail(f"shard uses undeclared source: {shard['source_work_id']}")
    for source in source_rows:
        if not isinstance(source, dict):
            fail("malformed collection source row")
        relative_source = str(source.get("epub_file", ""))
        source_path = (DATA_ROOT / relative_source).resolve()
        if DATA_ROOT.resolve() not in source_path.parents or not source_path.is_file():
            fail(f"missing/unsafe source EPUB path: {relative_source}")
        if sha256_bytes(source_path.read_bytes()) != source.get("epub_sha256"):
            fail(f"source EPUB hash mismatch: {relative_source}")
        if source.get("transcription_license") != EXPECTED_LICENSE:
            fail(f"source license mismatch: {relative_source}")

    return {
        "status": "PASS",
        "catalog_version": summary.get("catalog_version"),
        "raw_segments": summary.get("raw_segments"),
        "rejected_segments": summary.get("rejected_segments"),
        "unique_single_stories": canonical_seen,
        "source_works": len(work_counts),
        "work_counts": declared_work_counts,
        "shards": len(shard_rows),
        "full_text_records": canonical_seen,
        "exact_duplicate_segments": exact_duplicates_seen,
        "near_duplicate_review_status": summary.get(
            "near_duplicate_review_status"
        ),
        "runtime_eligible": canonical_seen,
        "recommendation_pool_stories": recommendation_pool_rows,
        "production_eligible": 0,
        "sqlite_entries": db_entries,
        "fts_entries": fts_entries,
        "license": summary.get("license"),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
