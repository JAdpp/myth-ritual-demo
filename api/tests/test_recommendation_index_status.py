from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path

from app.corpus import CorpusRepository

from conftest import _record


def _seed_records() -> list[dict]:
    return [
        _record("jingwei-v1", "jingwei", "精卫填海", "山海经"),
        _record("kuafu-v1", "kuafu", "夸父逐日", "山海经"),
    ]


def _write_sqlite_fixture(path: Path) -> None:
    text = "友人託書，使者持書往告，其人許之而後有悔。"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE entries (
                entry_id TEXT PRIMARY KEY,
                source_key TEXT NOT NULL UNIQUE,
                source_work_id TEXT NOT NULL,
                source_work_title TEXT NOT NULL,
                source_work_period TEXT NOT NULL,
                volume TEXT NOT NULL,
                source_locator TEXT NOT NULL,
                entry_ordinal INTEGER NOT NULL,
                title TEXT NOT NULL,
                title_origin TEXT NOT NULL,
                source_url TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                text_mode TEXT NOT NULL,
                text TEXT NOT NULL,
                extracted_text_sha256 TEXT NOT NULL,
                normalized_text_sha256 TEXT NOT NULL,
                dedupe_status TEXT NOT NULL,
                canonical_entry_id TEXT NOT NULL,
                catalog_status TEXT NOT NULL,
                runtime_eligible INTEGER NOT NULL CHECK(runtime_eligible IN (0, 1)),
                production_eligible INTEGER NOT NULL CHECK(production_eligible = 0),
                full_text_search_allowed INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE entries_fts USING fts5(
                entry_id UNINDEXED,
                title,
                text,
                source_work_title,
                source_locator,
                tokenize='trigram'
            );
            """
        )
        connection.execute(
            """
            INSERT INTO entries VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                "c1-letter-1",
                "source-letter-1",
                "soushenji",
                "搜神记",
                "魏晋",
                "卷一",
                "卷1",
                1,
                "持书往告",
                "source",
                "https://example.test/c1-letter-1",
                len(text),
                "full_text",
                text,
                digest,
                digest,
                "canonical",
                "c1-letter-1",
                "c1_source_demo",
                1,
                0,
                1,
            ),
        )
        connection.execute(
            "INSERT INTO entries_fts VALUES (?,?,?,?,?)",
            ("c1-letter-1", "持书往告", text, "搜神记", "卷1"),
        )
        connection.commit()
    finally:
        connection.close()


def test_injected_records_without_index_are_marked_not_configured() -> None:
    repository = CorpusRepository(records=_seed_records())
    overview = repository.summary()
    assert overview["recommendationPoolMode"] == "c3_only"
    assert overview["recommendationIndexStatus"] == "not_configured"
    assert overview["recommendationIndexAvailable"] is False
    assert overview["recommendationPoolStories"] == 0
    assert overview["deepAnnotatedStories"] == 2


def test_missing_index_path_is_unavailable_and_falls_back_to_c3() -> None:
    missing = Path(tempfile.mkdtemp()) / "catalog.sqlite3"
    repository = CorpusRepository(records=_seed_records(), single_story_db_path=missing)
    overview = repository.summary()
    assert overview["recommendationPoolMode"] == "c3_only"
    assert overview["recommendationIndexStatus"] == "unavailable"
    assert overview["recommendationIndexAvailable"] is False
    assert overview["recommendationPoolStories"] == 0
    assert overview["deepAnnotatedStories"] == 2
    candidates = repository.recommendation_candidates(
        query="我答应替朋友传信。",
        query_terms=["答应", "传信"],
        limit=4,
        seed="missing-index",
    )
    assert candidates
    assert all(item["corpusTier"] != "c1-source-demo" for item in candidates)


def test_temporary_index_fixture_marks_pool_available() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _write_sqlite_fixture(db_path)
        repository = CorpusRepository(records=_seed_records(), single_story_db_path=db_path)
        overview = repository.summary()
        assert overview["recommendationIndexStatus"] == "available"
        assert overview["recommendationIndexAvailable"] is True
        assert overview["recommendationPoolMode"] == "all_canonical_records"
        assert overview["recommendationPoolStories"] == 1
        candidates = repository.recommendation_candidates(
            query="友人托我传信，我已经答应。",
            query_terms=["传信", "答应"],
            limit=4,
            seed="present-index",
        )
        c1 = [item for item in candidates if item["storyVersionId"] == "c1-letter-1"]
        assert c1
        assert c1[0]["retrievalMode"] == "sqlite_fts5"
