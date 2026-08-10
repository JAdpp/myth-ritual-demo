"""Build the C1 single-story Demo pool from pinned Wikisource EPUB exports.

Every canonical C1 record is eligible for the local technical Demo's lightweight
recommendation path.  The 30-record C3 corpus remains the enriched path with
editor-authored explanation, mapping, and theatre fields; C1 records receive
deterministic fallbacks at runtime and are never marked production eligible.

The source EPUB files are official Wikisource exports stored under
``data/sources/wikisource_epub``. Their hashes are pinned below so a changed
export cannot silently rewrite the catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import sqlite3
import unicodedata
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


DATA_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = DATA_ROOT / "sources" / "wikisource_epub"
DEFAULT_OUTPUT_DIR = DATA_ROOT / "corpus" / "c1_single_story"
COLLECTION_MANIFEST_PATH = DATA_ROOT / "corpus" / "c0_collection_manifest.json"

CATALOG_VERSION = "c1-single-story-wikisource-2026-08-09-demo-pool-v1"
SNAPSHOT_DATE = "2026-08-09"
BUILD_TIMESTAMP = "2026-08-09T02:25:00+08:00"
ADAPTER_VERSION = "wikisource_epub_segmenter_v1"
NORMALIZATION_VERSION = "nfkc_punctuation_source_tail_v2"
MIN_STORY_CHARS = 20
SHARD_SIZE = 1_000

XHTML_NS = "{http://www.w3.org/1999/xhtml}"
HEADING_TAGS = {f"{XHTML_NS}h{level}" for level in range(1, 7)}
EXCLUDED_CLASSES = ("license", "ws-header", "noprint", "navbox")
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
TERMS_URL = "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/en"
PROVIDER_NAME = "中文维基文库贡献者"
PROVIDER_ID = "zh_wikisource"


@dataclass(frozen=True)
class WorkSpec:
    source_work_id: str
    title: str
    author: str
    period: str
    filename: str
    root_page_title: str
    export_url: str
    retrieved_at: str
    epub_sha256: str
    parser: str

    @property
    def root_url(self) -> str:
        encoded = urllib.parse.quote(self.root_page_title, safe="/")
        return f"https://zh.wikisource.org/wiki/{encoded}"


WORK_SPECS = (
    WorkSpec(
        source_work_id="taiping_guangji",
        title="太平廣記",
        author="李昉等奉敕纂",
        period="北宋",
        filename="taiping_guangji.epub",
        root_page_title="太平廣記",
        export_url=(
            "https://ws-export.wmcloud.org/?format=epub&lang=zh&"
            "page=%E5%A4%AA%E5%B9%B3%E5%BB%A3%E8%A8%98"
        ),
        retrieved_at="2026-08-09T02:09:22+08:00",
        epub_sha256="73e7ec4426e9826eabfd08a42f09a98028cd7e42161a22cf71a025203f389ef8",
        parser="taiping_headings",
    ),
    WorkSpec(
        source_work_id="yi_jian_zhi",
        title="夷堅志",
        author="洪邁",
        period="南宋",
        filename="yi_jian_zhi.epub",
        root_page_title="夷堅志",
        export_url=(
            "https://ws-export.wmcloud.org/?format=epub&lang=zh&"
            "page=%E5%A4%B7%E5%A0%85%E5%BF%97"
        ),
        retrieved_at="2026-08-09T02:11:35+08:00",
        epub_sha256="8301ef61ba59f87d2c38202f011d4fcebb92edc80a83a541a34377751826ad89",
        parser="yijian_headings",
    ),
    WorkSpec(
        source_work_id="yue_wei_cao_tang_bi_ji",
        title="閱微草堂筆記",
        author="紀昀",
        period="清",
        filename="yue_wei_cao_tang_bi_ji.epub",
        root_page_title="閱微草堂筆記",
        export_url=(
            "https://ws-export.wmcloud.org/?format=epub&lang=zh&"
            "page=%E9%96%B1%E5%BE%AE%E8%8D%89%E5%A0%82%E7%AD%86%E8%A8%98"
        ),
        retrieved_at="2026-08-09T02:13:27+08:00",
        epub_sha256="c1cbf749a36273f53e4a624cf2dbd581c0c7326180e94e2ddf8e9029e81b94de",
        parser="yuewei_paragraphs",
    ),
    WorkSpec(
        source_work_id="zi_bu_yu",
        title="子不語",
        author="袁枚",
        period="清",
        filename="zi_bu_yu.epub",
        root_page_title="子不語",
        export_url=(
            "https://ws-export.wmcloud.org/?format=epub&lang=zh&"
            "page=%E5%AD%90%E4%B8%8D%E8%AA%9E"
        ),
        retrieved_at="2026-08-09T02:19:55+08:00",
        epub_sha256="d083a537119834121fe34a269aeb80d3c0ac286fd9de8b550c82865d6351f7ce",
        parser="zibuyu_headings",
    ),
    WorkSpec(
        source_work_id="xu_zi_bu_yu",
        title="續子不語",
        author="袁枚",
        period="清",
        filename="xu_zi_bu_yu.epub",
        root_page_title="續子不語",
        export_url=(
            "https://ws-export.wmcloud.org/?format=epub&lang=zh&"
            "page=%E7%BA%8C%E5%AD%90%E4%B8%8D%E8%AA%9E"
        ),
        retrieved_at="2026-08-09T02:20:10+08:00",
        epub_sha256="4ee417e34886c1c1973cb07ca0d4d3ce3a5fd146ac1fb1b01a73d82d292c1e7a",
        parser="xuzibuyu_headings",
    ),
    WorkSpec(
        source_work_id="liao_zhai_zhi_yi",
        title="聊齋志異",
        author="蒲松齡",
        period="清",
        filename="liao_zhai_zhi_yi.epub",
        root_page_title="聊齋志異",
        export_url=(
            "https://ws-export.wmcloud.org/?format=epub&lang=zh&"
            "page=%E8%81%8A%E9%BD%8B%E5%BF%97%E7%95%B0"
        ),
        retrieved_at="2026-08-09T02:20:25+08:00",
        epub_sha256="3b44caff89c7a41318dcd78ebc2d2d2d89f86f58e040e0344d674fdc99c60db5",
        parser="liaozhai_headings",
    ),
)


@dataclass
class Candidate:
    spec: WorkSpec
    epub_item_path: str
    source_page_title: str | None
    volume: str
    ordinal: int
    title: str
    title_origin: str
    original_heading: str | None
    parent_heading: str | None
    segmentation_method: str
    segmentation_confidence: float
    text: str


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(payload: str) -> str:
    return sha256_bytes(payload.encode("utf-8"))


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    # Wikisource transcriptions sometimes repeat one anecdote in two volumes
    # with only punctuation and the trailing cited source changed.  Those are
    # the same text unit for catalog counting, so comparison normalization
    # removes a terminal source note and Unicode punctuation/separators while
    # the untouched transcription remains in ``text`` and its own hash.
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


def element_text(element: ET.Element) -> str:
    return compact_text("".join(element.itertext()))


def element_classes(element: ET.Element) -> str:
    return str(element.attrib.get("class", "")).lower()


def is_excluded_element(element: ET.Element) -> bool:
    classes = element_classes(element)
    return any(token in classes for token in EXCLUDED_CLASSES)


def build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def has_excluded_ancestor(
    element: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> bool:
    current: ET.Element | None = element
    while current is not None:
        if is_excluded_element(current):
            return True
        current = parent_map.get(current)
    return False


def section_body(section: ET.Element, heading: ET.Element) -> str:
    parts: list[str] = []
    after_heading = False
    for element in list(section):
        if element is heading:
            after_heading = True
            continue
        if not after_heading or element.tag == f"{XHTML_NS}section":
            continue
        if is_excluded_element(element) or element.tag == f"{XHTML_NS}table":
            continue
        text = element_text(element)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def nearest_parent_heading(
    heading: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> str | None:
    heading_level = int(heading.tag[-1]) if heading.tag[-1].isdigit() else 7
    current = parent_map.get(heading)
    while current is not None:
        if current.tag == f"{XHTML_NS}section":
            for child in list(current):
                if child is heading or child.tag not in HEADING_TAGS:
                    continue
                child_level = int(child.tag[-1]) if child.tag[-1].isdigit() else 7
                if child_level < heading_level:
                    value = element_text(child)
                    if value:
                        return value
        current = parent_map.get(current)
    return None


def epub_item_order(name: str) -> int:
    match = re.search(r"/c(\d+)_", name)
    return int(match.group(1)) if match else 1_000_000


def page_title_map(archive: zipfile.ZipFile) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in archive.namelist():
        if not name.endswith(".xhtml"):
            continue
        root = ET.fromstring(archive.read(name))
        for anchor in root.iter(f"{XHTML_NS}a"):
            href = str(anchor.attrib.get("href", "")).split("#", 1)[0]
            title = compact_text(str(anchor.attrib.get("title", "")))
            if href.endswith(".xhtml") and title:
                mapping[posixpath.basename(href)] = title
    return mapping


def page_url(spec: WorkSpec, source_page_title: str | None) -> str:
    if not source_page_title:
        return spec.root_url
    encoded = urllib.parse.quote(source_page_title, safe="/")
    return f"https://zh.wikisource.org/wiki/{encoded}"


def document_title(root: ET.Element) -> str:
    title = root.find(f".//{XHTML_NS}title")
    return element_text(title) if title is not None else ""


def iter_heading_candidates(
    spec: WorkSpec,
    archive: zipfile.ZipFile,
    item_names: Iterable[str],
    title_mapping: dict[str, str],
    choose_levels: Any,
    excluded_titles: set[str] | None = None,
) -> Iterable[Candidate]:
    excluded_titles = excluded_titles or set()
    for item_name in sorted(item_names, key=epub_item_order):
        root = ET.fromstring(archive.read(item_name))
        parent_map = build_parent_map(root)
        allowed_levels: set[int] = set(choose_levels(root))
        source_page_title = title_mapping.get(posixpath.basename(item_name))
        volume = source_page_title or document_title(root) or posixpath.basename(item_name)
        ordinal = 0
        for heading in root.iter():
            if heading.tag not in HEADING_TAGS:
                continue
            level = int(heading.tag[-1])
            if level not in allowed_levels:
                continue
            source_heading = element_text(heading)
            if not source_heading or source_heading in excluded_titles:
                continue
            ordinal += 1
            parent = parent_map.get(heading)
            text = section_body(parent, heading) if parent is not None else ""
            parent_heading = nearest_parent_heading(heading, parent_map)
            display_title = source_heading
            if source_heading in {"又", "又一", "又二"} and parent_heading:
                display_title = f"{parent_heading}·{source_heading}"
            yield Candidate(
                spec=spec,
                epub_item_path=item_name,
                source_page_title=source_page_title,
                volume=volume,
                ordinal=ordinal,
                title=display_title,
                title_origin="source_heading",
                original_heading=source_heading,
                parent_heading=parent_heading,
                segmentation_method=(
                    "source_subheading" if level >= 4 else "source_heading"
                ),
                segmentation_confidence=1.0,
                text=text,
            )


def parse_taiping(spec: WorkSpec, path: Path) -> Iterable[Candidate]:
    pattern = re.compile(r"OPS/c\d+_tai_ping_guang_ji_juan_di\d{3}\.xhtml")
    with zipfile.ZipFile(path) as archive:
        mapping = page_title_map(archive)
        names = [name for name in archive.namelist() if pattern.fullmatch(name)]

        def levels(root: ET.Element) -> set[int]:
            has_h3 = any(True for _ in root.iter(f"{XHTML_NS}h3"))
            return {3, 4} if has_h3 else {2, 4}

        yield from iter_heading_candidates(spec, archive, names, mapping, levels)


def parse_yijian(spec: WorkSpec, path: Path) -> Iterable[Candidate]:
    with zipfile.ZipFile(path) as archive:
        mapping = page_title_map(archive)
        names = [
            name
            for name in archive.namelist()
            if re.search(r"\d{2}\.xhtml$", name)
        ]

        def levels(root: ET.Element) -> set[int]:
            has_h3 = any(True for _ in root.iter(f"{XHTML_NS}h3"))
            return {3} if has_h3 else {2}

        yield from iter_heading_candidates(spec, archive, names, mapping, levels)


def parse_yuewei(spec: WorkSpec, path: Path) -> Iterable[Candidate]:
    with zipfile.ZipFile(path) as archive:
        mapping = page_title_map(archive)
        names = [
            name
            for name in archive.namelist()
            if re.search(r"_juan\d+\.xhtml$", name)
        ]
        for item_name in sorted(names, key=epub_item_order):
            volume_match = re.search(r"_juan(\d+)\.xhtml$", item_name)
            if not volume_match:
                continue
            volume_number = int(volume_match.group(1))
            root = ET.fromstring(archive.read(item_name))
            parent_map = build_parent_map(root)
            source_page_title = mapping.get(posixpath.basename(item_name))
            volume = source_page_title or document_title(root) or f"卷{volume_number}"
            ordinal = 0
            for source_paragraph_ordinal, paragraph in enumerate(
                root.iter(f"{XHTML_NS}p"), start=1
            ):
                if has_excluded_ancestor(paragraph, parent_map):
                    continue
                # These five collection-section openings are Ji Yun's prefaces,
                # not individual anecdotes.  Volume 1 was already excluded in
                # v1; volumes 7, 11, 15 and 19 are the other four source prefaces.
                if (
                    volume_number in {1, 7, 11, 15, 19}
                    and source_paragraph_ordinal == 1
                ):
                    continue
                text = element_text(paragraph)
                if not text:
                    continue
                ordinal += 1
                display_title = f"《{spec.title}》{volume}·第{ordinal}則"
                yield Candidate(
                    spec=spec,
                    epub_item_path=item_name,
                    source_page_title=source_page_title,
                    volume=volume,
                    ordinal=ordinal,
                    title=display_title,
                    title_origin="editorial_locator",
                    original_heading=None,
                    parent_heading=None,
                    segmentation_method="source_paragraph_entry",
                    segmentation_confidence=0.9,
                    text=text,
                )


def parse_simple_heading_work(spec: WorkSpec, path: Path) -> Iterable[Candidate]:
    if spec.parser == "zibuyu_headings":
        filename_pattern = re.compile(r"_juan\d+\.xhtml$")
        heading_level = 3
        excluded_titles: set[str] = set()
    elif spec.parser == "xuzibuyu_headings":
        filename_pattern = re.compile(r"_\d{2}\.xhtml$")
        heading_level = 3
        excluded_titles = set()
    elif spec.parser == "liaozhai_headings":
        filename_pattern = re.compile(r"_di\d{2}juan\.xhtml$")
        heading_level = 2
        excluded_titles = {"注釋", "注释"}
    else:
        raise ValueError(f"unknown simple heading parser: {spec.parser}")

    with zipfile.ZipFile(path) as archive:
        mapping = page_title_map(archive)
        names = [
            name
            for name in archive.namelist()
            if filename_pattern.search(name)
        ]
        yield from iter_heading_candidates(
            spec,
            archive,
            names,
            mapping,
            lambda _root: {heading_level},
            excluded_titles=excluded_titles,
        )


def candidate_stream(spec: WorkSpec, path: Path) -> Iterable[Candidate]:
    if spec.parser == "taiping_headings":
        yield from parse_taiping(spec, path)
    elif spec.parser == "yijian_headings":
        yield from parse_yijian(spec, path)
    elif spec.parser == "yuewei_paragraphs":
        yield from parse_yuewei(spec, path)
    else:
        yield from parse_simple_heading_work(spec, path)


def verify_source(spec: WorkSpec, source_dir: Path) -> tuple[Path, int]:
    path = source_dir / spec.filename
    if not path.is_file():
        raise FileNotFoundError(f"missing pinned Wikisource export: {path}")
    payload = path.read_bytes()
    actual_hash = sha256_bytes(payload)
    if actual_hash != spec.epub_sha256:
        raise ValueError(
            f"source hash changed for {spec.filename}: {actual_hash} != {spec.epub_sha256}"
        )
    if not zipfile.is_zipfile(path):
        raise ValueError(f"invalid EPUB/ZIP file: {path}")
    return path, len(payload)


def record_from_candidate(candidate: Candidate, epub_hash: str) -> dict[str, Any]:
    normalized_hash = sha256_text(comparison_text(candidate.text))
    provenance_material = "|".join(
        (
            PROVIDER_ID,
            candidate.spec.source_work_id,
            epub_hash,
            candidate.epub_item_path,
            candidate.volume,
            str(candidate.ordinal),
            candidate.original_heading or "",
        )
    )
    source_key = sha256_text(provenance_material)
    entry_id = f"c1ws_{source_key[:24]}"
    source_page_url = page_url(candidate.spec, candidate.source_page_title)
    locator = f"{candidate.volume} · {candidate.title}"
    return {
        "entry_id": entry_id,
        "unit_type": "single_story",
        "corpus_tier": "c1_source_story",
        "source_key": source_key,
        "source_work_id": candidate.spec.source_work_id,
        "source_work_title": candidate.spec.title,
        "source_work_author": candidate.spec.author,
        "source_work_period": candidate.spec.period,
        "volume": candidate.volume,
        "source_locator": locator,
        "entry_ordinal": candidate.ordinal,
        "title": candidate.title,
        "title_origin": candidate.title_origin,
        "original_heading": candidate.original_heading,
        "parent_heading": candidate.parent_heading,
        "language": "lzh",
        "script": "Hant",
        "provider": PROVIDER_ID,
        "provider_name": PROVIDER_NAME,
        "source_root_url": candidate.spec.root_url,
        "source_page_title": candidate.source_page_title,
        "source_url": source_page_url,
        "source_permalink": None,
        "revision_id": None,
        "revision_timestamp": None,
        "source_revision_status": "hashed_epub_snapshot_per_page_revision_pending",
        "retrieved_at": candidate.spec.retrieved_at,
        "source_snapshot_sha256": epub_hash,
        "epub_item_path": candidate.epub_item_path,
        "ingest_adapter_version": ADAPTER_VERSION,
        "segmentation_method": candidate.segmentation_method,
        "segmentation_confidence": candidate.segmentation_confidence,
        "segmentation_review_status": "automated_structure_validated_human_review_pending",
        "text_mode": "full_text",
        "text": candidate.text,
        "extracted_text_sha256": sha256_text(candidate.text),
        "normalized_text_sha256": normalized_hash,
        "normalization_version": NORMALIZATION_VERSION,
        "char_count": len(candidate.text),
        "underlying_text_status": "public_domain",
        "transcription_license": "CC-BY-SA-4.0",
        "dataset_license": "CC-BY-SA-4.0",
        "license_url": LICENSE_URL,
        "provider_terms_url": TERMS_URL,
        "attribution": f"{PROVIDER_NAME}；{source_page_url}",
        "automated_access_allowed": True,
        "redistribution_mode": "full_text_with_attribution_sharealike",
        "full_text_search_allowed": True,
        "embedding_allowed": False,
        "allowed_uses": [
            "research",
            "local_full_text_search",
            "local_demo_recommendation",
            "local_demo_generated_mapping",
            "local_demo_generated_theatre",
            "redistribution_with_attribution_sharealike",
        ],
        "rights_review_status": "provider_terms_verified_not_legal_advice",
        "catalog_status": "source_segmented_unreviewed",
        "runtime_eligible": True,
        "production_eligible": False,
        "review_status": "automated_structure_and_hash_only",
        "sensitive_content_review_status": "not_reviewed",
        "dedupe_status": "canonical",
        "canonical_entry_id": entry_id,
        "duplicate_cluster_id": f"exact_{normalized_hash[:24]}",
        "near_duplicate_review_status": "pending",
        "known_gaps": [
            "per_page_revision_id_pending",
            "near_duplicate_review_pending",
            "story_type_and_sensitive_content_not_human_reviewed",
        ],
    }


def build_records(
    source_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    extraction_stats: dict[str, Any] = {}
    raw_segments = 0
    rejected_segments = 0

    for spec in WORK_SPECS:
        source_path, source_bytes = verify_source(spec, source_dir)
        work_raw = 0
        work_rejected = 0
        work_accepted = 0
        for candidate in candidate_stream(spec, source_path):
            work_raw += 1
            raw_segments += 1
            if len(candidate.text) < MIN_STORY_CHARS:
                work_rejected += 1
                rejected_segments += 1
                continue
            records.append(record_from_candidate(candidate, spec.epub_sha256))
            work_accepted += 1
        extraction_stats[spec.source_work_id] = {
            "raw_segments": work_raw,
            "rejected_segments": work_rejected,
            "accepted_segments": work_accepted,
        }
        source_rows.append(
            {
                "source_work_id": spec.source_work_id,
                "title": spec.title,
                "author": spec.author,
                "period": spec.period,
                "provider": PROVIDER_ID,
                "root_url": spec.root_url,
                "export_url": spec.export_url,
                "retrieved_at": spec.retrieved_at,
                "epub_file": f"sources/wikisource_epub/{spec.filename}",
                "epub_bytes": source_bytes,
                "epub_sha256": spec.epub_sha256,
                "parser": spec.parser,
                "transcription_license": "CC-BY-SA-4.0",
                "license_url": LICENSE_URL,
                "provider_terms_url": TERMS_URL,
            }
        )

    seen_text_hashes: dict[str, str] = {}
    duplicate_segments = 0
    for record in records:
        normalized_hash = str(record["normalized_text_sha256"])
        canonical_id = seen_text_hashes.get(normalized_hash)
        if canonical_id is None:
            seen_text_hashes[normalized_hash] = str(record["entry_id"])
            continue
        duplicate_segments += 1
        record["dedupe_status"] = "exact_duplicate"
        record["canonical_entry_id"] = canonical_id
        record["runtime_eligible"] = False

    stats = {
        "raw_segments": raw_segments,
        "rejected_segments": rejected_segments,
        "accepted_segments": len(records),
        "exact_duplicate_segments": duplicate_segments,
        "unique_single_stories": len(seen_text_hashes),
        "by_work": extraction_stats,
    }
    return records, stats, source_rows


def json_bytes(payload: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
    return text.encode("utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(payload, pretty=True))


def prepare_output(output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    expected_parent = (DATA_ROOT / "corpus").resolve()
    if output_dir.parent != expected_parent:
        raise ValueError(f"refusing to clean unexpected output directory: {output_dir}")
    shards_dir = output_dir / "shards"
    if shards_dir.exists():
        shutil.rmtree(shards_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("manifest.json", "summary.json", "catalog.sqlite3"):
        target = output_dir / filename
        if target.exists():
            target.unlink()


def write_shards(
    records: list[dict[str, Any]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["source_work_id"])].append(record)

    shard_manifest: list[dict[str, Any]] = []
    for spec in WORK_SPECS:
        work_records = grouped.get(spec.source_work_id, [])
        for shard_index, start in enumerate(range(0, len(work_records), SHARD_SIZE)):
            shard_records = work_records[start : start + SHARD_SIZE]
            relative_path = (
                Path("shards")
                / PROVIDER_ID
                / spec.source_work_id
                / f"part-{shard_index:05d}.ndjson"
            )
            target = output_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = b"".join(json_bytes(record) for record in shard_records)
            target.write_bytes(payload)
            shard_manifest.append(
                {
                    "path": relative_path.as_posix(),
                    "provider": PROVIDER_ID,
                    "source_work_id": spec.source_work_id,
                    "rows": len(shard_records),
                    "bytes": len(payload),
                    "sha256": sha256_bytes(payload),
                }
            )
    return shard_manifest


def build_sqlite(records: list[dict[str, Any]], target: Path) -> dict[str, Any]:
    connection = sqlite3.connect(target)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=MEMORY;
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
            CREATE INDEX idx_entries_work_ordinal
                ON entries(source_work_id, entry_ordinal);
            CREATE INDEX idx_entries_normalized_hash
                ON entries(normalized_text_sha256);
            CREATE INDEX idx_entries_catalog_status
                ON entries(dedupe_status, catalog_status);
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
        entry_rows = []
        fts_rows = []
        for record in records:
            entry_rows.append(
                (
                    record["entry_id"],
                    record["source_key"],
                    record["source_work_id"],
                    record["source_work_title"],
                    record["source_work_period"],
                    record["volume"],
                    record["source_locator"],
                    record["entry_ordinal"],
                    record["title"],
                    record["title_origin"],
                    record["source_url"],
                    record["char_count"],
                    record["text_mode"],
                    record["text"],
                    record["extracted_text_sha256"],
                    record["normalized_text_sha256"],
                    record["dedupe_status"],
                    record["canonical_entry_id"],
                    record["catalog_status"],
                    int(bool(record["runtime_eligible"])),
                    0,
                    int(bool(record["full_text_search_allowed"])),
                )
            )
            if (
                record["dedupe_status"] == "canonical"
                and record["full_text_search_allowed"]
            ):
                fts_rows.append(
                    (
                        record["entry_id"],
                        record["title"],
                        record["text"],
                        record["source_work_title"],
                        record["source_locator"],
                    )
                )
        connection.executemany(
            """
            INSERT INTO entries VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            entry_rows,
        )
        connection.executemany(
            "INSERT INTO entries_fts VALUES (?,?,?,?,?)",
            fts_rows,
        )
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()

    payload = target.read_bytes()
    return {
        "path": target.name,
        "bytes": len(payload),
        "sha256": sha256_bytes(payload),
        "entries": len(records),
        "fts_entries": len(
            [record for record in records if record["dedupe_status"] == "canonical"]
        ),
        "tokenizer": "trigram",
    }


def build_catalog(
    source_dir: Path = DEFAULT_SOURCE_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    prepare_output(output_dir)
    records, stats, source_rows = build_records(source_dir)
    shard_rows = write_shards(records, output_dir)
    sqlite_row = build_sqlite(records, output_dir / "catalog.sqlite3")

    canonical_records = [
        record for record in records if record["dedupe_status"] == "canonical"
    ]
    work_counts = Counter(
        str(record["source_work_id"]) for record in canonical_records
    )
    catalog_build_hash = sha256_text(
        "\n".join(
            f"{record['entry_id']}:{record['normalized_text_sha256']}:{int(record['runtime_eligible'])}"
            for record in canonical_records
        )
    )
    summary = {
        "catalog_version": CATALOG_VERSION,
        "snapshot_date": SNAPSHOT_DATE,
        "built_at": BUILD_TIMESTAMP,
        "corpus_tier": "c1-source-single-story",
        "unit_definition": (
            "A source-defined heading/subheading anecdote, or a source paragraph "
            "explicitly treated as one entry by the work-specific adapter."
        ),
        "raw_segments": stats["raw_segments"],
        "rejected_segments": stats["rejected_segments"],
        "accepted_segments": stats["accepted_segments"],
        "exact_duplicate_segments": stats["exact_duplicate_segments"],
        "unique_single_stories": stats["unique_single_stories"],
        "full_text_stories": stats["unique_single_stories"],
        "excerpt_stories": 0,
        "metadata_only_stories": 0,
        "source_works": len([count for count in work_counts.values() if count]),
        "status_counts": {
            "source_segmented_unreviewed": stats["unique_single_stories"]
        },
        "dedupe_scope": (
            "NFKC plus punctuation/separator removal and terminal source-note "
            "normalization; near-duplicate review pending"
        ),
        "near_duplicate_review_status": "pending",
        "work_counts": [
            {
                "source_work_id": spec.source_work_id,
                "title": spec.title,
                "count": work_counts.get(spec.source_work_id, 0),
            }
            for spec in WORK_SPECS
        ],
        "catalog_build_hash": catalog_build_hash,
        "runtime_eligible": stats["unique_single_stories"],
        "recommendation_pool_stories": stats["unique_single_stories"],
        "curated_experience_stories": 30,
        "recommendation_pool_mode": "all_canonical_records",
        "production_eligible": 0,
        "license": "CC-BY-SA-4.0",
        "license_url": LICENSE_URL,
        "claim_boundary": (
            "All canonical records are eligible for lightweight recommendation in the "
            "local technical Demo. They are source-segmented classical narratives, not "
            "12k expert-annotated myths; only 30 stories have editor-authored enrichment."
        ),
    }
    write_json(output_dir / "summary.json", summary)

    manifest = {
        "catalog_version": CATALOG_VERSION,
        "snapshot_date": SNAPSHOT_DATE,
        "built_at": BUILD_TIMESTAMP,
        "record_schema": "contracts/c1-single-story.schema.json",
        "summary_path": "summary.json",
        "summary_sha256": sha256_bytes((output_dir / "summary.json").read_bytes()),
        "records": len(records),
        "canonical_records": len(canonical_records),
        "shards": shard_rows,
        "sqlite": sqlite_row,
        "source_epubs": source_rows,
    }
    write_json(output_dir / "manifest.json", manifest)

    collection_manifest = {
        "manifest_version": "c0-collection-manifest-2026-08-09",
        "snapshot_date": SNAPSHOT_DATE,
        "built_at": BUILD_TIMESTAMP,
        "provider": PROVIDER_ID,
        "provider_name": PROVIDER_NAME,
        "provider_terms_url": TERMS_URL,
        "license": "CC-BY-SA-4.0",
        "license_url": LICENSE_URL,
        "automated_access_method": "official_wikisource_epub_export",
        "sources": [
            {
                **source,
                **stats["by_work"][str(source["source_work_id"])],
            }
            for source in source_rows
        ],
        "derived_catalog": {
            "path": "corpus/c1_single_story",
            "catalog_version": CATALOG_VERSION,
            "unique_single_stories": stats["unique_single_stories"],
            "recommendation_pool_stories": stats["unique_single_stories"],
            "recommendation_pool_mode": "all_canonical_records",
            "catalog_build_hash": catalog_build_hash,
        },
        "access_exclusions": [
            {
                "provider": "Chinese Text Project",
                "policy": "manual verification or limited quotation only",
                "reason": "official FAQ prohibits automated bulk downloading and republication",
                "url": "https://ctext.org/faq",
            },
            {
                "provider": "National Library of China",
                "policy": "links and manually reviewed metadata only without separate permission",
                "reason": "online availability does not establish bulk-download or redistribution rights",
                "url": "https://www.nlc.cn/web/dsb_footer/bqsm/index.shtml",
            },
        ],
    }
    write_json(COLLECTION_MANIFEST_PATH, collection_manifest)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    summary = build_catalog(args.source_dir, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
