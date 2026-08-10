"""Fail-closed checks for the broad C1 discovery catalog.

The discovery catalog may describe 180+ research leads, but exactly 30 entries
currently resolve to one C3 product story each. A second source witness remains
internal and must not be counted as another runtime story.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "corpus" / "c1_story_catalog.json"
C0_PATH = ROOT / "corpus" / "c0_source_manifest.json"
C1_SECONDARY_PATH = ROOT / "corpus" / "c1_secondary_source_witnesses.json"
C2_SELECTION_PATH = ROOT / "corpus" / "c2_primary_story_selection.json"
C3_PATH = ROOT / "corpus" / "c3_story_versions.json"
MINIMUM_CATALOG_ENTRIES = 180
EXPECTED_DETAILED_FAMILIES = 30


def fail(message: str) -> None:
    raise AssertionError(message)


def load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        fail(f"invalid object envelope: {path}")
    return payload


def main() -> None:
    catalog = load(CATALOG_PATH)
    c0 = load(C0_PATH)
    c1_secondary = load(C1_SECONDARY_PATH)
    c2 = load(C2_SELECTION_PATH)
    c3 = load(C3_PATH)
    families = catalog.get("story_entries")
    primary_records = c3.get("story_versions")
    secondary_records = c1_secondary.get("source_witnesses")
    selections = c2.get("story_selections")
    c0_families = c0.get("story_families")
    if not all(
        isinstance(items, list)
        for items in (families, primary_records, secondary_records, selections, c0_families)
    ):
        fail("invalid catalog or corpus arrays")
    assert isinstance(families, list)
    assert isinstance(primary_records, list)
    assert isinstance(secondary_records, list)
    assert isinstance(selections, list)
    assert isinstance(c0_families, list)

    if catalog.get("corpus_tier") != "c1-discovery":
        fail("invalid C1 discovery envelope")
    if len(families) < MINIMUM_CATALOG_ENTRIES:
        fail(f"catalog must contain at least {MINIMUM_CATALOG_ENTRIES} entries")
    if len(primary_records) != EXPECTED_DETAILED_FAMILIES:
        fail(f"C3 must contain {EXPECTED_DETAILED_FAMILIES} product stories")
    if len(secondary_records) != EXPECTED_DETAILED_FAMILIES:
        fail(f"C1-secondary must contain {EXPECTED_DETAILED_FAMILIES} witnesses")
    if len(selections) != EXPECTED_DETAILED_FAMILIES:
        fail(f"C2 must contain {EXPECTED_DETAILED_FAMILIES} selections")

    primary_family_counts = Counter(str(item.get("story_family_id")) for item in primary_records)
    secondary_family_counts = Counter(str(item.get("story_family_id")) for item in secondary_records)
    if len(primary_family_counts) != EXPECTED_DETAILED_FAMILIES or set(primary_family_counts.values()) != {1}:
        fail("C3 must contain exactly one product story per detailed family")
    if len(secondary_family_counts) != EXPECTED_DETAILED_FAMILIES or set(secondary_family_counts.values()) != {1}:
        fail("C1-secondary must contain exactly one witness per detailed family")
    if set(primary_family_counts) != set(secondary_family_counts):
        fail("primary and secondary detailed-family sets differ")
    if any(
        item.get("product_primary") is not True
        or item.get("record_role") != "product_primary_story"
        for item in primary_records
    ):
        fail("C3 contains a non-primary product record")
    if any(
        item.get("product_primary") is not False
        or item.get("record_role") != "secondary_source_witness"
        or item.get("development_runtime_eligible") is not False
        for item in secondary_records
    ):
        fail("secondary witness leaked into runtime semantics")

    selection_family_ids = {str(item.get("story_family_id")) for item in selections}
    if selection_family_ids != set(primary_family_counts):
        fail("C2 selection families do not match C3")
    c0_title_by_family = {
        str(item.get("story_family_id")): item.get("title") for item in c0_families
    }
    if set(c0_title_by_family) != set(primary_family_counts):
        fail("C0 detailed family set does not match C3")

    discovery_groups = catalog.get("discovery_groups")
    if not isinstance(discovery_groups, list) or not discovery_groups:
        fail("catalog lacks discovery group metadata")
    group_ids = {
        item.get("discovery_group_id")
        for item in discovery_groups
        if isinstance(item, dict)
    }
    if len(group_ids) != len(discovery_groups) or None in group_ids:
        fail("invalid or duplicate discovery_group_id")
    for group in discovery_groups:
        if not isinstance(group, dict):
            fail("invalid discovery group")
        if (
            group.get("lead_scope")
            != "组级检索入口，不表示每部作品都直接包含组内每一个候选条目。"
            and group.get("discovery_group_id") != "new_detailed"
        ):
            fail(f"discovery source lead scope is overstated: {group.get('discovery_group_id')}")

    ids = [item.get("story_family_id") for item in families]
    if any(not isinstance(family_id, str) or not family_id for family_id in ids):
        fail("missing story_family_id")
    if len(ids) != len(set(ids)):
        fail("duplicate story_family_id in catalog")
    if any(not re.fullmatch(r"[a-z0-9_]+", family_id) for family_id in ids):
        fail("story_family_id must use lowercase snake_case")

    runtime_types_by_family: dict[str, set[str]] = {}
    for record in primary_records:
        family_id = str(record["story_family_id"])
        runtime_types_by_family.setdefault(family_id, set()).add(str(record["story_type"]))

    catalog_detailed_ids: set[str] = set()
    for item in families:
        family_id = str(item["story_family_id"])
        status = item.get("catalog_status")
        if status not in {"metadata_only", "c3_dev_detailed"}:
            fail(f"unknown catalog status: {family_id}")
        if item.get("production_eligible") is not False:
            fail(f"catalog item claims production eligibility: {family_id}")
        if not item.get("canonical_title") or not item.get("story_type"):
            fail(f"catalog item lacks title or type: {family_id}")
        if item.get("discovery_group_id") not in group_ids:
            fail(f"catalog item references unknown discovery group: {family_id}")
        if "representative_source_hints" in item or "source_hint_status" in item:
            fail(f"per-entry source hints are not allowed before fixation: {family_id}")
        for related_id in item.get("related_story_ids") or []:
            if related_id not in ids:
                fail(f"unknown related story id: {family_id} -> {related_id}")

        if status == "metadata_only":
            if item.get("runtime_eligible") is not False:
                fail(f"metadata-only item is runtime eligible: {family_id}")
            if item.get("source_evidence_status") != "not_fixed":
                fail(f"metadata-only source evidence is overstated: {family_id}")
            if item.get("runtime_story_types"):
                fail(f"metadata-only item claims runtime story types: {family_id}")
        else:
            catalog_detailed_ids.add(family_id)
            if item.get("runtime_eligible") is not True:
                fail(f"detailed item is not marked runtime eligible: {family_id}")
            if item.get("source_evidence_status") != "fixed_in_c0_primary_selected":
                fail(f"detailed item lacks fixed-source/primary-selected state: {family_id}")
            if set(item.get("runtime_story_types") or []) != runtime_types_by_family[family_id]:
                fail(f"runtime story type mismatch: {family_id}")
            if item.get("canonical_title") != c0_title_by_family[family_id]:
                fail(f"catalog/C0 familiar title mismatch: {family_id}")

    if catalog_detailed_ids != set(primary_family_counts):
        missing = sorted(set(primary_family_counts) - catalog_detailed_ids)
        extra = sorted(catalog_detailed_ids - set(primary_family_counts))
        fail(f"catalog/C3 detailed family mismatch; missing={missing}, extra={extra}")

    status_counts = Counter(str(item["catalog_status"]) for item in families)
    declared = catalog.get("catalog_counts", {})
    expected_counts = {
        "story_entries": len(families),
        "c3_dev_detailed": status_counts["c3_dev_detailed"],
        "metadata_only": status_counts["metadata_only"],
        "production_eligible": 0,
    }
    if declared != expected_counts:
        fail(f"catalog_counts mismatch: {declared} != {expected_counts}")

    serialized = json.dumps(catalog, ensure_ascii=False)
    for forbidden in ('"excerpt"', '"original_excerpt"', '"excerpt_sha256"'):
        if forbidden in serialized:
            fail(f"C1 discovery catalog must not ingest source text: {forbidden}")
    for stale_claim in ("双版本 C3-dev", "两条固定来源见证与开发标注", "每族 2 个版本"):
        if stale_claim in serialized:
            fail(f"stale multi-version runtime claim leaked into catalog: {stale_claim}")

    print(
        json.dumps(
            {
                "status": "PASS",
                **expected_counts,
                "product_primary_stories": len(primary_records),
                "secondary_source_witnesses": len(secondary_records),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
