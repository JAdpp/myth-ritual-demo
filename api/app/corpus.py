"""C3 deep records plus lightweight C1 SQLite recommendation access."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_CORPUS_PATH = Path(__file__).resolve().parents[2] / "data" / "corpus" / "c3_story_versions.json"
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "corpus" / "c1_story_catalog.json"
DEFAULT_SINGLE_STORY_SUMMARY_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "corpus"
    / "c1_single_story"
    / "summary.json"
)
DEFAULT_SINGLE_STORY_DB_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "corpus"
    / "c1_single_story"
    / "catalog.sqlite3"
)
DEFAULT_SOURCE_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "corpus" / "c0_source_manifest.json"
)

_BLOCKED_STATUSES = {
    "blocked",
    "denied",
    "draft",
    "metadata_only",
    "pending",
    "rejected",
    "restricted",
    "unreviewed",
}

_C3_DEV_REVIEW_STATUSES = {"c3_dev_pending_dual_review"}
_C3_DEV_RIGHTS_STATUSES = {"development_short_excerpt_open_review"}
_C3_DEV_ACCESS_MODES = {"short_excerpt", "short_excerpt_adult_gated"}
_C3_PRODUCTION_REVIEW_STATUSES = {"approved", "c3_approved", "dual_review_approved"}
_C3_PRODUCTION_RIGHTS_STATUSES = {"approved", "cleared", "production_approved"}

_C1_SELECT_COLUMNS = """
    entry_id,
    source_work_id,
    source_work_title,
    source_work_period,
    volume,
    source_locator,
    entry_ordinal,
    title,
    source_url,
    char_count,
    text,
    extracted_text_sha256
"""

_C1_REQUIRED_COLUMNS = {
    "entry_id",
    "source_work_id",
    "source_work_title",
    "source_work_period",
    "volume",
    "source_locator",
    "entry_ordinal",
    "title",
    "source_url",
    "char_count",
    "text",
    "extracted_text_sha256",
    "dedupe_status",
    "runtime_eligible",
}

# These aliases are deliberately small and transparent. They are used only to
# fetch a bounded shortlist from classical Chinese source text; the service
# still explains recommendations using the words found in the user's summary.
_C1_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "关系": ("父母兄弟", "夫妻婚姻", "朋友交遊", "親族往來"),
    "边界": ("不可侵犯", "禮法所限", "禁止往來", "內外有別"),
    "信任": ("深信不疑", "以信相託", "託付其事", "不相疑忌"),
    "变化": ("忽然變化", "遭逢變故", "遷移他處", "改易其形"),
    "改变": ("改變舊制", "改易其事", "變化不測", "更始更新"),
    "离开": ("辭別而去", "離家遠行", "出走他鄉", "遠行不返"),
    "失去": ("亡失所有", "喪失親人", "生死別離", "失而復得"),
    "选择": ("兩者取捨", "決意從之", "乃自選擇", "從其所願"),
    "决定": ("決意而行", "遂決其事", "乃定其計", "自作主張"),
    "行动": ("即日往行", "乃往其所", "遂往求之", "力行其志"),
    "坚持": ("終不肯從", "守之不失", "久而不止", "堅持其志"),
    "身份": ("姓名身世", "自稱某人", "本是何人", "隱其姓名"),
    "责任": ("職守所在", "當為此事", "受命而行", "不負所託"),
    "工作": ("仕官任職", "為官一方", "掌管職事", "受職赴任"),
    "目标": ("志向所求", "欲得其物", "所願未遂", "立志求之"),
    "梦想": ("夢中所見", "夜夢神人", "夢寐所求", "所願得成"),
}


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return default


def _status(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, Mapping):
        nested = _first(value, "status", "review_status", "reviewStatus")
        return str(nested).strip().lower() if nested is not None else None
    return None


def _is_eligible(record: Mapping[str, Any]) -> bool:
    explicit = _first(record, "eligible_for_demo", "eligibleForDemo", "c3_eligible", "c3Eligible")
    if explicit is not True:
        return False
    if _first(record, "product_primary", "productPrimary") is not True:
        return False

    tier = str(_first(record, "corpus_tier", "corpusTier", default="")).strip().lower()
    editorial_status = str(
        _first(record, "editorial_status", "editorialStatus", default="")
    ).strip().lower()
    development_runtime_eligible = _first(
        record,
        "development_runtime_eligible",
        "developmentRuntimeEligible",
    )
    production_eligible = _first(record, "production_eligible", "productionEligible")
    review_status = _status(_first(record, "review_record", "reviewRecord", "review_status", "reviewStatus"))
    rights = _first(record, "rights_and_access", "rightsAndAccess", default={})
    rights_status = _status(rights)
    access_mode = None
    if isinstance(rights, Mapping):
        access_mode = _first(rights, "access_mode", "accessMode", "content_mode", "contentMode")
    access_mode = str(access_mode).strip().lower() if access_mode is not None else None

    if development_runtime_eligible is not True:
        return False
    if review_status is None or rights_status is None or access_mode is None:
        return False
    if review_status in _BLOCKED_STATUSES or rights_status in _BLOCKED_STATUSES:
        return False
    if access_mode == "metadata_only":
        return False

    source_canon = _first(record, "source_canon", "sourceCanon")
    story_card = _first(record, "story_card", "storyCard")
    adaptation_boundary = _first(record, "adaptation_boundary", "adaptationBoundary")
    review_record = _first(record, "review_record", "reviewRecord")
    if not all(
        isinstance(value, Mapping) and bool(value)
        for value in (source_canon, story_card, adaptation_boundary, rights, review_record)
    ):
        return False

    assert isinstance(source_canon, Mapping)
    assert isinstance(story_card, Mapping)
    assert isinstance(adaptation_boundary, Mapping)
    if _first(source_canon, "immutable") is not True:
        return False
    required_source_values = (
        _first(source_canon, "work", "source_title", "sourceTitle"),
        _first(source_canon, "source_url", "sourceUrl"),
        _first(source_canon, "source_permalink", "sourcePermalink"),
        _first(source_canon, "page_revision_id", "pageRevisionId"),
        _first(source_canon, "page_revision_timestamp", "pageRevisionTimestamp"),
        _first(source_canon, "original_excerpt", "originalExcerpt"),
        _first(source_canon, "excerpt_sha256", "excerptSha256"),
    )
    if any(value in (None, "") for value in required_source_values):
        return False
    excerpt = str(_first(source_canon, "original_excerpt", "originalExcerpt", default=""))
    excerpt_hash = str(_first(source_canon, "excerpt_sha256", "excerptSha256", default=""))
    if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != excerpt_hash:
        return False

    if not str(_first(story_card, "summary", default="")).strip():
        return False
    if not _first(adaptation_boundary, "must_preserve", "mustPreserve"):
        return False
    if not _first(adaptation_boundary, "prohibited", "prohibited_changes", "prohibitedChanges"):
        return False
    if not isinstance(rights, Mapping):
        return False
    allowed_uses = _first(rights, "allowed_uses", "allowedUses", default=[])
    if not isinstance(allowed_uses, list) or not {
        "local_development_runtime",
        "local_development_runtime_with_adult_opt_in",
    }.intersection(allowed_uses):
        return False

    adult_only = _first(record, "adult_only", "adultOnly", default=False)
    default_offer = _first(record, "default_offer_eligible", "defaultOfferEligible", default=True)
    explicit_adult_opt_in = _first(
        record,
        "requires_explicit_adult_opt_in",
        "requiresExplicitAdultOptIn",
        default=False,
    )
    if adult_only is True and (default_offer is not False or explicit_adult_opt_in is not True):
        return False

    if tier == "c3-dev":
        return (
            editorial_status == "editorial_draft"
            and production_eligible is False
            and review_status in _C3_DEV_REVIEW_STATUSES
            and rights_status in _C3_DEV_RIGHTS_STATUSES
            and access_mode in _C3_DEV_ACCESS_MODES
        )
    if tier == "c3":
        return (
            editorial_status in {"approved", "reviewed"}
            and production_eligible is True
            and review_status in _C3_PRODUCTION_REVIEW_STATUSES
            and rights_status in _C3_PRODUCTION_RIGHTS_STATUSES
        )
    return False


def normalize_record(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    version_id = _first(raw, "story_version_id", "storyVersionId", "version_id", "versionId", "id")
    if version_id is None:
        return None

    family_id = _first(raw, "family_id", "familyId", "story_family_id", "storyFamilyId", default="unknown")
    source_canon = copy.deepcopy(_first(raw, "source_canon", "sourceCanon", default={}))
    story_card = copy.deepcopy(_first(raw, "story_card", "storyCard", default={}))
    adaptation_boundary = copy.deepcopy(
        _first(raw, "adaptation_boundary", "adaptationBoundary", default={})
    )

    title = _first(
        raw,
        "title",
        "original_title",
        "originalTitle",
        default=_first(story_card, "title", default=str(version_id)),
    )
    if not isinstance(source_canon, Mapping):
        source_canon = {"summary": str(source_canon)}
    if not isinstance(story_card, Mapping):
        story_card = {"summary": str(story_card)}
    if not isinstance(adaptation_boundary, Mapping):
        adaptation_boundary = {}

    adult_only = bool(_first(raw, "adult_only", "adultOnly", default=False))
    requires_explicit_adult_opt_in = bool(
        _first(
            raw,
            "requires_explicit_adult_opt_in",
            "requiresExplicitAdultOptIn",
            default=False,
        )
    )
    sensitive = adult_only or requires_explicit_adult_opt_in
    normalized = {
        "storyVersionId": str(version_id),
        "familyId": str(family_id),
        "title": str(title),
        "era": _first(raw, "era", "dynasty"),
        "genre": raw.get("genre"),
        "storyType": str(_first(raw, "story_type", "storyType", default="uncategorized")),
        "sourceCanon": dict(source_canon),
        "storyCard": dict(story_card),
        "adaptationBoundary": dict(adaptation_boundary),
        "rightsAndAccess": copy.deepcopy(
            _first(raw, "rights_and_access", "rightsAndAccess", default={})
        ),
        "reviewRecord": copy.deepcopy(_first(raw, "review_record", "reviewRecord", default={})),
        "provenance": copy.deepcopy(raw.get("provenance", [])),
        "adultOnly": adult_only,
        "defaultOfferEligible": bool(
            _first(raw, "default_offer_eligible", "defaultOfferEligible", default=True)
        ) and not sensitive,
        "requiresExplicitAdultOptIn": sensitive,
        "retrievalMode": "curated_deep_record",
    }
    normalized["sourceCanonHash"] = stable_hash(normalized["sourceCanon"])
    normalized["eligibleForDemo"] = _is_eligible(raw)
    normalized["corpusTier"] = str(
        _first(raw, "corpus_tier", "corpusTier", default="c3")
    )
    return normalized


def _compact_source_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    sentence_end = max(clipped.rfind(mark) for mark in "。！？；")
    if sentence_end >= max(40, limit // 2):
        clipped = clipped[: sentence_end + 1]
    return clipped.rstrip() + "…"


def _c1_theme_terms(text: str) -> list[str]:
    terms: list[str] = []
    for modern, classical in _C1_QUERY_ALIASES.items():
        if modern in text or any(term in text for term in classical):
            terms.append(modern)
    return terms[:4] or ["古典叙事"]


def _c1_query_terms(query: str, explicit_terms: Sequence[str] | None = None) -> list[str]:
    terms: list[str] = []
    for raw_term in explicit_terms or ():
        term = re.sub(r"\s+", "", str(raw_term)).strip()
        if term:
            terms.extend(_C1_QUERY_ALIASES.get(term, (term,))[:2])
    for modern, aliases in _C1_QUERY_ALIASES.items():
        if modern in query:
            terms.extend(aliases[:2])
    # Long literal fragments can occasionally match a title or a source
    # passage directly. Keep them bounded so one request cannot create a very
    # large SQL expression.
    for fragment in re.findall(r"[\u3400-\u9fff]{3,8}", query):
        terms.append(fragment)
    unique: list[str] = []
    for term in terms:
        if term not in unique:
            unique.append(term)
        if len(unique) >= 8:
            break
    return unique


class CorpusRepository:
    def __init__(
        self,
        path: Path | str | None = None,
        records: Iterable[Mapping[str, Any]] | None = None,
        catalog_path: Path | str | None = None,
        single_story_summary_path: Path | str | None = None,
        single_story_db_path: Path | str | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_CORPUS_PATH
        if catalog_path is not None:
            self.catalog_path: Path | None = Path(catalog_path)
        elif records is not None:
            # Injected test/runtime records must not silently inherit the repository catalog.
            self.catalog_path = None
        elif path is None:
            self.catalog_path = DEFAULT_CATALOG_PATH
        else:
            self.catalog_path = self.path.with_name("c1_story_catalog.json")
        if single_story_summary_path is not None:
            self.single_story_summary_path: Path | None = Path(single_story_summary_path)
        elif records is not None:
            # Unit-test/runtime records must not inherit the repository's 10k+
            # discovery summary. Their overview describes only the injected set.
            self.single_story_summary_path = None
        elif path is None:
            self.single_story_summary_path = DEFAULT_SINGLE_STORY_SUMMARY_PATH
        else:
            self.single_story_summary_path = self.path.parent / "c1_single_story" / "summary.json"
        if single_story_db_path is not None:
            self.single_story_db_path: Path | None = Path(single_story_db_path)
        elif records is not None:
            # Injected C3 fixtures stay isolated unless a C1 SQLite fixture is
            # explicitly supplied by the caller.
            self.single_story_db_path = None
        elif path is None:
            self.single_story_db_path = DEFAULT_SINGLE_STORY_DB_PATH
        else:
            self.single_story_db_path = self.path.parent / "c1_single_story" / "catalog.sqlite3"
        if records is not None:
            self.source_manifest_path: Path | None = None
        elif path is None:
            self.source_manifest_path = DEFAULT_SOURCE_MANIFEST_PATH
        else:
            self.source_manifest_path = self.path.with_name("c0_source_manifest.json")
        self.corpus_version = "corpus-v0.1-demo"
        self.snapshot_date: str | None = None
        raw_records: list[Mapping[str, Any]]

        if records is not None:
            raw_records = list(records)
        else:
            raw_records = self._load_file()

        normalized = [normalize_record(item) for item in raw_records]
        eligible_records = [
            item
            for item in normalized
            if item is not None and item["eligibleForDemo"]
        ]
        family_counts: dict[str, int] = {}
        for item in eligible_records:
            family_id = str(item["familyId"])
            family_counts[family_id] = family_counts.get(family_id, 0) + 1
        # A product story has exactly one adopted main text.  If a bad corpus
        # ships two runtime records for one family, reject both records rather
        # than choosing an arbitrary "first" version for the user.
        self.rejected_duplicate_family_ids = {
            family_id for family_id, count in family_counts.items() if count > 1
        }
        self._records = {
            item["storyVersionId"]: item
            for item in eligible_records
            if str(item["familyId"]) not in self.rejected_duplicate_family_ids
        }
        self._catalog = self._load_catalog_summary()
        self._single_story_catalog = self._load_single_story_summary()
        self._single_story_candidate_ids = self._load_single_story_candidate_ids()
        self._single_story_candidate_id_set = frozenset(self._single_story_candidate_ids)
        self._single_story_candidate_count = len(self._single_story_candidate_ids)
        self._source_witness_count = self._load_source_witness_count()

    def _load_catalog_summary(self) -> dict[str, Any] | None:
        """Load catalog-level counts without treating metadata-only entries as runtime stories."""

        if self.catalog_path is None or not self.catalog_path.exists():
            return None
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        families = payload.get("story_entries")
        if not isinstance(families, list) or not families:
            return None
        family_ids: list[str] = []
        statuses: dict[str, int] = {}
        tradition_groups: dict[str, int] = {}
        for family in families:
            if not isinstance(family, Mapping):
                return None
            family_id = str(family.get("story_family_id") or "").strip()
            status = str(family.get("catalog_status") or "").strip()
            if not family_id or not status:
                return None
            family_ids.append(family_id)
            statuses[status] = statuses.get(status, 0) + 1
            tradition_group = str(family.get("tradition_group") or "未分类").strip()
            tradition_groups[tradition_group] = tradition_groups.get(tradition_group, 0) + 1
        if len(family_ids) != len(set(family_ids)):
            return None
        return {
            "entries": len(family_ids),
            "statusCounts": statuses,
            "traditionGroups": tradition_groups,
            "snapshotDate": payload.get("snapshot_date"),
        }

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _load_single_story_summary(self) -> dict[str, Any] | None:
        """Load only the generated aggregate for the 10k+ C1 single-story layer.

        The NDJSON shards never enter the API process. Runtime candidates are
        read separately and in bounded batches from SQLite, while health and
        landing responses continue to expose aggregate counts only.
        """

        if self.single_story_summary_path is None or not self.single_story_summary_path.exists():
            return None
        try:
            payload = json.loads(self.single_story_summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None

        catalog_version = str(payload.get("catalog_version") or "").strip()
        snapshot_date = str(payload.get("snapshot_date") or "").strip()
        dedupe_scope = str(payload.get("dedupe_scope") or "").strip()
        raw_segments = self._nonnegative_int(payload.get("raw_segments"))
        rejected_segments = self._nonnegative_int(payload.get("rejected_segments"))
        unique_single_stories = self._nonnegative_int(payload.get("unique_single_stories"))
        full_text_stories = self._nonnegative_int(payload.get("full_text_stories"))
        source_works = self._nonnegative_int(payload.get("source_works"))
        if (
            not catalog_version
            or not snapshot_date
            or not dedupe_scope
            or raw_segments is None
            or rejected_segments is None
            or unique_single_stories is None
            or unique_single_stories == 0
            or full_text_stories is None
            or source_works is None
            or rejected_segments > raw_segments
            or unique_single_stories > raw_segments - rejected_segments
            or full_text_stories > unique_single_stories
        ):
            return None

        raw_status_counts = payload.get("status_counts")
        if not isinstance(raw_status_counts, Mapping):
            return None
        status_counts: dict[str, int] = {}
        for raw_name, raw_count in raw_status_counts.items():
            name = str(raw_name).strip()
            count = self._nonnegative_int(raw_count)
            if not name or count is None:
                return None
            status_counts[name] = count

        raw_work_counts = payload.get("work_counts")
        if not isinstance(raw_work_counts, list):
            return None
        work_counts: list[dict[str, Any]] = []
        seen_work_ids: set[str] = set()
        for raw_work in raw_work_counts:
            if not isinstance(raw_work, Mapping):
                return None
            source_work_id = str(raw_work.get("source_work_id") or "").strip()
            title = str(raw_work.get("title") or "").strip()
            count = self._nonnegative_int(raw_work.get("count"))
            if (
                not source_work_id
                or source_work_id in seen_work_ids
                or not title
                or count is None
            ):
                return None
            seen_work_ids.add(source_work_id)
            work_counts.append(
                {"sourceWorkId": source_work_id, "title": title, "count": count}
            )
        if (
            source_works != len(work_counts)
            or sum(item["count"] for item in work_counts) != unique_single_stories
        ):
            return None

        return {
            "catalogVersion": catalog_version,
            "snapshotDate": snapshot_date,
            "rawSegments": raw_segments,
            "rejectedSegments": rejected_segments,
            "uniqueSingleStories": unique_single_stories,
            "fullTextStories": full_text_stories,
            "sourceWorks": source_works,
            "statusCounts": status_counts,
            "dedupeScope": dedupe_scope,
            "workCounts": work_counts,
        }

    def _open_single_story_db(self) -> sqlite3.Connection:
        path = self.single_story_db_path
        if path is None or not path.is_file():
            raise FileNotFoundError("C1 single-story SQLite catalog is unavailable")
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _load_single_story_candidate_ids(self) -> tuple[str, ...]:
        """Load the small stable-ID index, never the 12k story bodies."""

        try:
            with closing(self._open_single_story_db()) as connection:
                columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(entries)")
                }
                if not _C1_REQUIRED_COLUMNS.issubset(columns):
                    return ()
                rows = connection.execute(
                    """
                    SELECT entry_id FROM entries
                    WHERE dedupe_status = 'canonical' AND runtime_eligible = 1
                    ORDER BY entry_id ASC
                    """
                ).fetchall()
        except (OSError, sqlite3.Error):
            return ()
        ids = tuple(str(row[0]) for row in rows)
        return ids if len(ids) == len(set(ids)) else ()

    @staticmethod
    def _normalize_c1_row(row: Mapping[str, Any]) -> dict[str, Any]:
        """Adapt one SQLite source segment to the existing story-card contract.

        C1 remains stored once in SQLite. Only a bounded excerpt and summary for
        a shortlisted or selected row are materialized in the API process.
        """

        entry_id = str(row["entry_id"])
        title = str(row["title"])
        work_title = str(row["source_work_title"])
        locator = str(row["source_locator"])
        source_url = str(row["source_url"])
        full_text = str(row["text"])
        summary = _compact_source_text(full_text, limit=260)
        # The full text stays in SQLite and remains reachable through the source
        # link.  Keep the mapping anchor compact enough to sit beside the user's
        # editable branch without turning the canvas into a transcript viewer.
        excerpt = _compact_source_text(full_text, limit=520)
        motifs = _c1_theme_terms(full_text)
        source_canon = {
            "immutable": True,
            "work": work_title,
            "sourceTitle": work_title,
            "sourceWorkId": str(row["source_work_id"]),
            "source_url": source_url,
            "source_permalink": source_url,
            "original_excerpt": excerpt,
            "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "sourceTextSha256": str(row["extracted_text_sha256"]),
            "summary": summary,
            "ending": _compact_source_text(full_text[-500:], limit=220),
            "evidence_anchor": locator,
            "volume": str(row["volume"]),
            "entryOrdinal": int(row["entry_ordinal"]),
            "textTruncatedForApi": len(excerpt) < len(re.sub(r"\s+", " ", full_text).strip()),
        }
        adaptation_boundary = {
            "mustPreserve": ["来源书名、篇名与原文片段", "区分原典内容与用户改写"],
            "mayTransform": ["现代语境", "人物关系", "对白", "叙事视角", "结局支线"],
            "prohibitedChanges": ["把当代改写冒充原典事实", "把故事推荐表述为心理诊断"],
        }
        normalized = {
            "storyVersionId": entry_id,
            # Each exact-text-unique source segment is an independent demo
            # candidate; no artificial version-family choice is introduced.
            "familyId": entry_id,
            "title": title,
            "era": str(row["source_work_period"]),
            "genre": "古典叙事",
            "storyType": "source_segmented_story",
            "sourceCanon": source_canon,
            "storyCard": {
                "title": title,
                "summary": summary,
                "characters": [],
                "conflict": "原典人物面对事件变化并作出回应。",
                "motifs": motifs,
                "imagery": [work_title],
                "emotionalArc": "原典片段—关键变化—文本结局",
                "resonance": "可与用户分享的处境作开放比较，再由用户决定是否采用。",
                "nonFit": "这是按来源结构切分的演示候选；若题名或情节不合适，可直接换一组。",
                "contentWarnings": [],
            },
            "adaptationBoundary": adaptation_boundary,
            "rightsAndAccess": {
                "status": "demo_source_catalog",
                "accessMode": "bounded_excerpt",
                "copyrightBasis": "wikisource_cc_by_sa_4_0",
            },
            "reviewRecord": {"status": "demo_unreviewed_fallback"},
            "provenance": [
                {
                    "id": f"source-{entry_id}",
                    "title": title,
                    "work": work_title,
                    "era": str(row["source_work_period"]),
                    "locator": locator,
                    "url": source_url,
                    "rights": "CC BY-SA 4.0 transcription snapshot",
                }
            ],
            "adultOnly": False,
            "defaultOfferEligible": True,
            "requiresExplicitAdultOptIn": False,
            "eligibleForDemo": True,
            "corpusTier": "c1-source-demo",
            "deepAnnotated": False,
            "retrievalMode": "sqlite_by_id",
        }
        normalized["sourceCanonHash"] = stable_hash(source_canon)
        return normalized

    def _get_c1_row(self, story_version_id: str) -> dict[str, Any] | None:
        if story_version_id not in self._single_story_candidate_id_set:
            return None
        try:
            with closing(self._open_single_story_db()) as connection:
                row = connection.execute(
                    f"""
                    SELECT {_C1_SELECT_COLUMNS}
                    FROM entries
                    WHERE entry_id = ?
                      AND dedupe_status = 'canonical'
                      AND runtime_eligible = 1
                    """,
                    (story_version_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            return None
        return self._normalize_c1_row(row) if row is not None else None

    def _count_c1_candidate_ids(self, story_version_ids: set[str]) -> int:
        return len(story_version_ids.intersection(self._single_story_candidate_id_set))

    def _query_c1_candidates(
        self,
        *,
        query: str,
        query_terms: Sequence[str] | None,
        limit: int,
        excluded_ids: set[str],
        seed: str,
    ) -> list[dict[str, Any]]:
        if self._single_story_candidate_count == 0 or limit <= 0:
            return []

        selected_rows: list[sqlite3.Row] = []
        selected_ids: set[str] = set()
        fts_matched_ids: set[str] = set()
        terms = [term for term in _c1_query_terms(query, query_terms) if len(term) >= 3]
        try:
            with closing(self._open_single_story_db()) as connection:
                if terms:
                    fts_query = " OR ".join(f'"{term}"' for term in terms)
                    ranked_ids = [
                        str(row["entry_id"])
                        for row in connection.execute(
                            """
                            SELECT entry_id
                            FROM entries_fts
                            WHERE entries_fts MATCH ?
                            ORDER BY bm25(entries_fts, 0.0, 8.0, 1.0, 2.0, 2.0), rowid
                            LIMIT ?
                            """,
                            (fts_query, max(limit * 4, 24)),
                        ).fetchall()
                    ]
                    if ranked_ids:
                        placeholders = ",".join("?" for _ in ranked_ids)
                        rows = connection.execute(
                            f"""
                            SELECT {_C1_SELECT_COLUMNS}
                            FROM entries
                            WHERE dedupe_status = 'canonical'
                              AND runtime_eligible = 1
                              AND entry_id IN ({placeholders})
                            """,
                            ranked_ids,
                        ).fetchall()
                        rows_by_id = {str(row["entry_id"]): row for row in rows}
                    else:
                        rows_by_id = {}
                    for entry_id in ranked_ids:
                        row = rows_by_id.get(entry_id)
                        if row is None or entry_id in excluded_ids or entry_id in selected_ids:
                            continue
                        selected_rows.append(row)
                        selected_ids.add(entry_id)
                        fts_matched_ids.add(entry_id)
                        if len(selected_rows) >= limit:
                            break

                remaining = limit - len(selected_rows)
                if remaining > 0:
                    offset = int(
                        stable_hash({"seed": seed, "query": query})[:16], 16
                    ) % self._single_story_candidate_count
                    sample_ids: list[str] = []
                    for scanned in range(self._single_story_candidate_count):
                        entry_id = self._single_story_candidate_ids[
                            (offset + scanned) % self._single_story_candidate_count
                        ]
                        if entry_id in excluded_ids or entry_id in selected_ids:
                            continue
                        sample_ids.append(entry_id)
                        if len(sample_ids) >= remaining:
                            break
                    if sample_ids:
                        placeholders = ",".join("?" for _ in sample_ids)
                        sampled_rows = connection.execute(
                            f"""
                            SELECT {_C1_SELECT_COLUMNS}
                            FROM entries
                            WHERE dedupe_status = 'canonical'
                              AND runtime_eligible = 1
                              AND entry_id IN ({placeholders})
                            """,
                            sample_ids,
                        ).fetchall()
                        rows_by_id = {
                            str(row["entry_id"]): row for row in sampled_rows
                        }
                        for entry_id in sample_ids:
                            row = rows_by_id.get(entry_id)
                            if row is None:
                                continue
                            selected_rows.append(row)
                            selected_ids.add(entry_id)
        except (OSError, sqlite3.Error):
            return []

        normalized: list[dict[str, Any]] = []
        for row in selected_rows[:limit]:
            record = self._normalize_c1_row(row)
            record["retrievalMode"] = (
                "sqlite_fts5" if record["storyVersionId"] in fts_matched_ids else "stable_sample"
            )
            normalized.append(record)
        return normalized

    def _load_source_witness_count(self) -> int | None:
        """Count auditable C0 source witnesses without exposing them as product choices."""

        if self.source_manifest_path is None or not self.source_manifest_path.exists():
            return None
        try:
            payload = json.loads(self.source_manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        source_items = payload.get("source_items")
        if not isinstance(source_items, list) or not source_items:
            return None
        witness_ids: list[str] = []
        for item in source_items:
            if not isinstance(item, Mapping):
                return None
            witness_id = str(item.get("story_version_id") or "").strip()
            if not witness_id:
                return None
            witness_ids.append(witness_id)
        if len(witness_ids) != len(set(witness_ids)):
            return None
        return len(witness_ids)

    def _load_file(self) -> list[Mapping[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []

        if isinstance(payload, list):
            # A bare list has no auditable corpus-tier/runtime envelope.
            return []
        if not isinstance(payload, Mapping):
            return []

        corpus_tier = str(_first(payload, "corpus_tier", "corpusTier", default="")).strip().lower()
        development_runtime_eligible = _first(
            payload,
            "development_runtime_eligible",
            "developmentRuntimeEligible",
        )
        production_eligible = _first(payload, "production_eligible", "productionEligible")
        if development_runtime_eligible is not True:
            return []
        if corpus_tier == "c3-dev" and production_eligible is not False:
            return []
        if corpus_tier == "c3" and production_eligible is not True:
            return []
        if corpus_tier not in {"c3-dev", "c3"}:
            return []

        self.corpus_version = str(
            _first(payload, "corpus_version", "corpusVersion", "version", default=self.corpus_version)
        )
        snapshot = _first(payload, "snapshot_date", "snapshotDate")
        self.snapshot_date = str(snapshot) if snapshot is not None else None
        if not self.corpus_version.strip() or self.snapshot_date is None:
            return []
        items = _first(
            payload,
            "story_versions",
            "storyVersions",
            "versions",
            "records",
            "items",
            default=[],
        )
        return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []

    def __len__(self) -> int:
        return len(self._records)

    def get(self, story_version_id: str) -> dict[str, Any] | None:
        record = self._records.get(story_version_id)
        if record is not None:
            return copy.deepcopy(record)
        return self._get_c1_row(story_version_id)

    def offers(
        self,
        *,
        limit: int = 3,
        excluded_ids: set[str] | None = None,
        adult_content_opt_in: bool = False,
    ) -> list[dict[str, Any]]:
        excluded_ids = excluded_ids or set()
        ordered = sorted(
            self._records.values(),
            key=lambda item: (item["familyId"], item["storyVersionId"]),
        )
        candidates = [
            item
            for item in ordered
            if item["storyVersionId"] not in excluded_ids
            and (
                item["defaultOfferEligible"]
                or (
                    adult_content_opt_in
                    and item["adultOnly"]
                    and item["requiresExplicitAdultOptIn"]
                )
            )
        ]
        return [copy.deepcopy(item) for item in candidates[: max(2, min(limit, 3))]]

    def candidates(
        self,
        *,
        excluded_ids: set[str] | None = None,
        adult_content_opt_in: bool = False,
    ) -> list[dict[str, Any]]:
        """Return every currently offerable record for service-level ranking.

        Ranking deliberately lives in the application service: the corpus layer
        only enforces review, rights, adult-gating, and fixed-source integrity.
        """

        excluded_ids = excluded_ids or set()
        records = [
            item
            for item in self._records.values()
            if item["storyVersionId"] not in excluded_ids
            and (
                item["defaultOfferEligible"]
                or (
                    adult_content_opt_in
                    and item["adultOnly"]
                    and item["requiresExplicitAdultOptIn"]
                )
            )
        ]
        return [
            copy.deepcopy(item)
            for item in sorted(
                records,
                key=lambda item: (item["familyId"], item["storyVersionId"]),
            )
        ]

    def recommendation_candidates(
        self,
        *,
        query: str = "",
        query_terms: Sequence[str] | None = None,
        limit: int = 18,
        excluded_ids: set[str] | None = None,
        adult_content_opt_in: bool = False,
        seed: str = "",
    ) -> list[dict[str, Any]]:
        """Return a bounded mixed shortlist from C3 memory and C1 SQLite.

        The 12k C1 rows are never copied into the C3 JSON or loaded wholesale.
        Every runtime-eligible canonical C1 ID is retrievable and can be reached
        by the stable sampler, while the existing 30 deep records keep their
        richer card and mapping data.
        """

        excluded = excluded_ids or set()
        bounded_limit = max(2, min(int(limit), 60))
        c3_records = self.candidates(
            excluded_ids=excluded,
            adult_content_opt_in=adult_content_opt_in,
        )
        # Keep the complete (small) deep set available for relevance ranking;
        # bound SQLite materialization independently.
        c1_records = self._query_c1_candidates(
            query=query,
            query_terms=query_terms,
            limit=bounded_limit,
            excluded_ids=excluded,
            seed=seed,
        )
        return [*c3_records, *c1_records]

    def recommendation_candidate_count(self, *, adult_content_opt_in: bool = False) -> int:
        return len(self.offerable_ids(adult_content_opt_in=adult_content_opt_in)) + (
            self._single_story_candidate_count
        )

    def is_recommendation_candidate(
        self,
        story_version_id: str,
        *,
        adult_content_opt_in: bool = False,
    ) -> bool:
        record = self._records.get(story_version_id)
        if record is not None:
            return bool(
                record["defaultOfferEligible"]
                or (
                    adult_content_opt_in
                    and record["adultOnly"]
                    and record["requiresExplicitAdultOptIn"]
                )
            )
        return self._get_c1_row(story_version_id) is not None

    def remaining_recommendation_count(
        self,
        excluded_ids: set[str],
        *,
        adult_content_opt_in: bool = False,
    ) -> int:
        total = self.recommendation_candidate_count(
            adult_content_opt_in=adult_content_opt_in
        )
        c3_ids = self.offerable_ids(adult_content_opt_in=adult_content_opt_in)
        excluded_eligible = len(excluded_ids.intersection(c3_ids)) + self._count_c1_candidate_ids(
            excluded_ids.difference(c3_ids)
        )
        return max(0, total - excluded_eligible)

    def summary(self) -> dict[str, Any]:
        """Describe the loaded runtime corpus without exposing source excerpts."""

        records = list(self._records.values())
        family_ids = sorted({str(item["familyId"]) for item in records})
        genres: dict[str, int] = {}
        story_types: dict[str, int] = {}
        eras: set[str] = set()
        for item in records:
            genre = str(item.get("genre") or "未分类")
            genres[genre] = genres.get(genre, 0) + 1
            story_type = str(item.get("storyType") or "uncategorized")
            story_types[story_type] = story_types.get(story_type, 0) + 1
            era = str(item.get("era") or "").strip()
            if era:
                eras.add(era)
        single_story_catalog = self._single_story_catalog
        catalog_entries = (
            single_story_catalog["uniqueSingleStories"]
            if single_story_catalog
            else self._catalog["entries"]
            if self._catalog
            else len(family_ids)
        )
        catalog_statuses = (
            single_story_catalog["statusCounts"]
            if single_story_catalog
            else self._catalog["statusCounts"]
            if self._catalog
            else {"c3_dev_detailed": len(family_ids)}
        )
        source_witnesses = (
            self._source_witness_count
            if self._source_witness_count is not None
            else len(records)
        )
        return {
            # Product-facing count: one adopted main text per familiar story.
            "productStories": len(records),
            # Demo recommendation pool: C1 rows stay in SQLite and the 30 deep
            # C3 records remain the richer fallback/experience layer.
            "recommendationPoolStories": self._single_story_candidate_count,
            "sourceSegmentedRecommendationStories": self._single_story_candidate_count,
            "deepAnnotatedStories": len(records),
            "curatedExperienceStories": len(records),
            "totalRecommendationCandidates": self.recommendation_candidate_count(
                adult_content_opt_in=True
            ),
            "defaultTotalRecommendationCandidates": self.recommendation_candidate_count(
                adult_content_opt_in=False
            ),
            "recommendationPoolMode": (
                "all_canonical_records" if self._single_story_candidate_count else "c3_only"
            ),
            # Audit-facing count: source witnesses remain evidence, never user choices.
            "sourceWitnesses": source_witnesses,
            # Legacy fields stay available while API consumers migrate terminology.
            "storyFamilies": len(family_ids),
            "storyVersions": len(records),
            # Compatibility alias. This is the C1 exact-deduped single-story
            # count when the generated aggregate exists, never a 10k-row array.
            "catalogEntries": catalog_entries,
            "catalogUniqueStories": catalog_entries,
            "catalogRawSegments": (
                single_story_catalog["rawSegments"] if single_story_catalog else catalog_entries
            ),
            "catalogRejectedSegments": (
                single_story_catalog["rejectedSegments"] if single_story_catalog else 0
            ),
            "catalogFullTextStories": (
                single_story_catalog["fullTextStories"] if single_story_catalog else 0
            ),
            "catalogSourceWorks": (
                single_story_catalog["sourceWorks"] if single_story_catalog else 0
            ),
            "catalogDedupeScope": (
                single_story_catalog["dedupeScope"] if single_story_catalog else None
            ),
            "catalogWorks": (
                copy.deepcopy(single_story_catalog["workCounts"])
                if single_story_catalog
                else []
            ),
            "catalogMetadataOnlyEntries": catalog_statuses.get("metadata_only", 0),
            "catalogStatusCounts": catalog_statuses,
            "catalogGroups": [
                {"name": name, "count": count}
                for name, count in sorted(
                    (self._catalog["traditionGroups"] if self._catalog else {}).items(),
                    key=lambda item: item[0],
                )
            ],
            "catalogSnapshotDate": (
                single_story_catalog["snapshotDate"]
                if single_story_catalog
                else self._catalog["snapshotDate"]
                if self._catalog
                else None
            ),
            "familyIds": family_ids,
            "genres": [
                {"name": name, "count": count}
                for name, count in sorted(genres.items(), key=lambda item: (-item[1], item[0]))
            ],
            "storyTypes": [
                {"name": name, "count": count}
                for name, count in sorted(story_types.items(), key=lambda item: (-item[1], item[0]))
            ],
            "eraLabels": sorted(eras),
            "snapshotDate": self.snapshot_date,
            "reviewStatus": "development_dual_review_pending",
            "productionEligibleVersions": 0,
        }

    def offerable_ids(self, *, adult_content_opt_in: bool = False) -> set[str]:
        return {
            item["storyVersionId"]
            for item in self._records.values()
            if item["defaultOfferEligible"]
            or (
                adult_content_opt_in
                and item["adultOnly"]
                and item["requiresExplicitAdultOptIn"]
            )
        }
