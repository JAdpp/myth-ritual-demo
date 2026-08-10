"""Fail-closed offline validation for the C0/C1-secondary/C2/C3 corpus.

Run from the project root with ``python data/validate_corpus.py``. The check
does not contact external services. It enforces one C3 product story per family
while retaining one non-runtime secondary witness per family for provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DATA_ROOT = Path(__file__).resolve().parent
C0_PATH = DATA_ROOT / "corpus" / "c0_source_manifest.json"
C1_SECONDARY_PATH = DATA_ROOT / "corpus" / "c1_secondary_source_witnesses.json"
C2_SELECTION_PATH = DATA_ROOT / "corpus" / "c2_primary_story_selection.json"
C3_PATH = DATA_ROOT / "corpus" / "c3_story_versions.json"
EXPECTED_FAMILIES = 30
EXPECTED_SOURCE_WITNESSES = 60
EXPECTED_PRIMARY_STORIES = 30
EXPECTED_SECONDARY_WITNESSES = 30
PRIMARY_SELECTION_STATUS = "demo_editorial_selection_pending_expert_review"
EXPECTED_ADULT_IDS = {
    "ganjiang_moye_wuyuechunqiu",
    "ganjiang_moye_soushenji_11",
    "mengjiangnu_lienvzhuan_04",
    "butterfly_qingshi_leilue_10",
}
EXPECTED_PRIMARY_ADULT_IDS = {
    "ganjiang_moye_wuyuechunqiu",
    "mengjiangnu_lienvzhuan_04",
    "butterfly_qingshi_leilue_10",
}
EXPECTED_SECONDARY_ADULT_IDS = {"ganjiang_moye_soushenji_11"}
USER_COPY_COMPARISON_TERMS = ("另一版本", "同族另一", "异文", "并排", "对照版本")


def fail(message: str) -> None:
    raise AssertionError(message)


def load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        fail(f"expected object envelope: {path}")
    return payload


def oldid_from_permalink(url: str) -> int:
    values = parse_qs(urlparse(url).query).get("oldid")
    if not values or not values[0].isdigit():
        fail(f"permalink has no numeric oldid: {url}")
    return int(values[0])


def assert_unique(items: list[dict[str, object]], key: str, expected: int, layer: str) -> None:
    values = [item.get(key) for item in items]
    if len(values) != expected or len(set(values)) != expected or None in values:
        fail(f"{layer} must contain {expected} unique {key} values")


def assert_record_evidence(
    record: dict[str, object],
    c0_item: dict[str, object],
    *,
    runtime_layer: bool,
    now: datetime,
) -> None:
    version_id = str(record["story_version_id"])
    source = record.get("source_canon")
    rights = record.get("rights_and_access")
    review = record.get("review_record")
    story_card = record.get("story_card")
    adaptation = record.get("adaptation_boundary")
    if not all(isinstance(value, dict) for value in (source, rights, review, story_card, adaptation)):
        fail(f"annotated record lacks required nested object: {version_id}")
    assert isinstance(source, dict)
    assert isinstance(rights, dict)
    assert isinstance(review, dict)
    assert isinstance(story_card, dict)
    assert isinstance(adaptation, dict)

    excerpt = source.get("original_excerpt")
    if not isinstance(excerpt, str) or not excerpt:
        fail(f"missing source excerpt: {version_id}")
    digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    if digest != source.get("excerpt_sha256") or digest != c0_item.get("excerpt_sha256"):
        fail(f"SHA-256 mismatch: {version_id}")
    if excerpt != c0_item.get("excerpt"):
        fail(f"annotated/C0 excerpt mismatch: {version_id}")
    if len(excerpt) >= 100:
        fail(f"excerpt must stay under 100 Unicode characters: {version_id}")

    for c0_key, record_key in (
        ("story_family_id", "story_family_id"),
        ("story_type", "story_type"),
        ("source_url", "source_url"),
        ("source_permalink", "source_permalink"),
        ("page_revision_id", "page_revision_id"),
        ("page_revision_timestamp", "page_revision_timestamp"),
    ):
        record_value = record.get(record_key) if record_key in record else source.get(record_key)
        if c0_item.get(c0_key) != record_value:
            fail(f"annotated/C0 {c0_key} mismatch: {version_id}")

    permalink = source.get("source_permalink")
    revision_id = source.get("page_revision_id")
    timestamp = source.get("page_revision_timestamp")
    if not isinstance(permalink, str) or oldid_from_permalink(permalink) != revision_id:
        fail(f"oldid/revision mismatch: {version_id}")
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp
    ):
        fail(f"invalid revision timestamp: {version_id}")
    retrieved_at_raw = source.get("retrieved_at")
    if not isinstance(retrieved_at_raw, str):
        fail(f"missing source retrieved_at: {version_id}")
    retrieved_at = datetime.fromisoformat(retrieved_at_raw.replace("Z", "+00:00"))
    if retrieved_at.astimezone(timezone.utc) > now + timedelta(minutes=5):
        fail(f"source retrieved_at is in the future: {version_id}")

    if source.get("immutable") is not True:
        fail(f"source canon is mutable: {version_id}")
    if record.get("editorial_status") != "editorial_draft":
        fail(f"wrong editorial status: {version_id}")
    if record.get("production_eligible") is not False or c0_item.get("production_eligible") is not False:
        fail(f"production record leaked into development data: {version_id}")
    if rights.get("status") != "development_short_excerpt_open_review":
        fail(f"unexpected rights status: {version_id}")
    prohibited_until_review = rights.get("prohibited_until_review")
    if not isinstance(prohibited_until_review, list) or "production_release" not in prohibited_until_review:
        fail(f"rights do not block production release: {version_id}")
    if review.get("reviewers") != []:
        fail(f"unverified reviewer names present: {version_id}")
    if not review.get("known_gaps"):
        fail(f"missing review gap: {version_id}")

    adult_only = record.get("adult_only") is True
    expected_c0_rights = (
        "development_short_excerpt_only_adult"
        if adult_only
        else "development_short_excerpt_only"
    )
    expected_access_mode = "short_excerpt_adult_gated" if adult_only else "short_excerpt"
    if c0_item.get("rights_status") != expected_c0_rights:
        fail(f"C0 adult rights mismatch: {version_id}")
    if rights.get("access_mode") != expected_access_mode:
        fail(f"record access mode mismatch: {version_id}")
    if record.get("requires_explicit_adult_opt_in") is not adult_only:
        fail(f"adult opt-in flag mismatch: {version_id}")

    allowed_uses_raw = rights.get("allowed_uses")
    if not isinstance(allowed_uses_raw, list):
        fail(f"invalid allowed_uses: {version_id}")
    allowed_uses = set(allowed_uses_raw)
    if runtime_layer:
        if record.get("corpus_tier") != "c3-dev":
            fail(f"wrong C3 tier: {version_id}")
        if record.get("product_primary") is not True:
            fail(f"C3 contains a non-primary record: {version_id}")
        if record.get("record_role") != "product_primary_story":
            fail(f"wrong C3 record role: {version_id}")
        if record.get("primary_selection_status") != PRIMARY_SELECTION_STATUS:
            fail(f"wrong primary selection status: {version_id}")
        if record.get("eligible_for_demo") is not True or record.get("development_runtime_eligible") is not True:
            fail(f"C3 primary is not runtime eligible: {version_id}")
        if record.get("default_offer_eligible") is not (not adult_only):
            fail(f"C3 default-offer gate mismatch: {version_id}")
        if review.get("status") != "c3_dev_pending_dual_review":
            fail(f"wrong C3 review status: {version_id}")
        expected_runtime_use = (
            "local_development_runtime_with_adult_opt_in"
            if adult_only
            else "local_development_runtime"
        )
        forbidden_runtime_use = (
            "local_development_runtime"
            if adult_only
            else "local_development_runtime_with_adult_opt_in"
        )
        if expected_runtime_use not in allowed_uses or forbidden_runtime_use in allowed_uses:
            fail(f"C3 runtime rights mismatch: {version_id}")
        if record.get("title") != story_card.get("title"):
            fail(f"product title is not the familiar canonical title: {version_id}")
        if story_card.get("subtitle") != source.get("work"):
            fail(f"product subtitle does not identify its single source: {version_id}")
        user_copy = json.dumps(
            {"story_card": story_card, "adaptation_boundary": adaptation},
            ensure_ascii=False,
        )
        leaked = [term for term in USER_COPY_COMPARISON_TERMS if term in user_copy]
        if leaked:
            fail(f"user-facing version comparison leaked into C3: {version_id}: {leaked}")
    else:
        if record.get("corpus_tier") != "c1-secondary-witness":
            fail(f"wrong secondary tier: {version_id}")
        if record.get("product_primary") is not False:
            fail(f"secondary marked as product primary: {version_id}")
        if record.get("record_role") != "secondary_source_witness":
            fail(f"wrong secondary record role: {version_id}")
        if record.get("primary_selection_status") != "not_selected_secondary_source_witness":
            fail(f"wrong secondary selection status: {version_id}")
        for flag in ("eligible_for_demo", "development_runtime_eligible", "default_offer_eligible"):
            if record.get(flag) is not False:
                fail(f"secondary runtime gate is open ({flag}): {version_id}")
        if review.get("status") != "c1_secondary_pending_dual_review":
            fail(f"wrong secondary review status: {version_id}")
        if "internal_source_audit" not in allowed_uses:
            fail(f"secondary lacks internal audit use: {version_id}")
        if {"local_development_runtime", "local_development_runtime_with_adult_opt_in"} & allowed_uses:
            fail(f"secondary leaked runtime rights: {version_id}")


def main() -> None:
    c0 = load(C0_PATH)
    c1_secondary = load(C1_SECONDARY_PATH)
    c2 = load(C2_SELECTION_PATH)
    c3 = load(C3_PATH)
    c0_families = c0.get("story_families")
    c0_items = c0.get("source_items")
    secondary_items = c1_secondary.get("source_witnesses")
    selections = c2.get("story_selections")
    primary_items = c3.get("story_versions")
    if not all(
        isinstance(items, list)
        for items in (c0_families, c0_items, secondary_items, selections, primary_items)
    ):
        fail("invalid corpus arrays")
    assert isinstance(c0_families, list)
    assert isinstance(c0_items, list)
    assert isinstance(secondary_items, list)
    assert isinstance(selections, list)
    assert isinstance(primary_items, list)

    now = datetime.now(timezone.utc)
    top_retrieved_at_raw = c0.get("retrieved_at")
    if not isinstance(top_retrieved_at_raw, str):
        fail("C0 lacks retrieved_at")
    top_retrieved_at = datetime.fromisoformat(top_retrieved_at_raw.replace("Z", "+00:00"))
    if top_retrieved_at.astimezone(timezone.utc) > now + timedelta(minutes=5):
        fail("C0 retrieved_at is in the future")

    if len(c0_families) != EXPECTED_FAMILIES:
        fail(f"expected {EXPECTED_FAMILIES} C0 families")
    assert_unique(c0_items, "story_version_id", EXPECTED_SOURCE_WITNESSES, "C0")
    assert_unique(primary_items, "story_version_id", EXPECTED_PRIMARY_STORIES, "C3")
    assert_unique(secondary_items, "story_version_id", EXPECTED_SECONDARY_WITNESSES, "C1-secondary")
    assert_unique(selections, "story_family_id", EXPECTED_FAMILIES, "C2")

    corpus_versions = {
        c0.get("corpus_version"),
        c1_secondary.get("corpus_version"),
        c3.get("corpus_version"),
    }
    if len(corpus_versions) != 1 or None in corpus_versions:
        fail("C0/C1-secondary/C3 corpus_version mismatch")
    snapshot_dates = {
        c0.get("snapshot_date"),
        c1_secondary.get("snapshot_date"),
        c2.get("snapshot_date"),
        c3.get("snapshot_date"),
    }
    if len(snapshot_dates) != 1 or None in snapshot_dates:
        fail("C0/C1-secondary/C2/C3 snapshot_date mismatch")
    if c3.get("corpus_tier") != "c3-dev" or c3.get("development_runtime_eligible") is not True:
        fail("invalid C3 envelope")
    if c3.get("record_policy") != "one_product_primary_story_per_family":
        fail("C3 does not enforce one-primary policy")
    if c3.get("primary_selection_status") != PRIMARY_SELECTION_STATUS:
        fail("C3 top-level primary selection status mismatch")
    if c1_secondary.get("corpus_tier") != "c1-secondary-witness":
        fail("invalid C1 secondary envelope")
    if c1_secondary.get("development_runtime_eligible") is not False:
        fail("C1 secondary envelope is runtime eligible")
    if c1_secondary.get("record_policy") != "internal_source_evidence_only_not_user_selectable":
        fail("C1 secondary policy is not fail-closed")
    if c2.get("selection_status") != PRIMARY_SELECTION_STATUS or c2.get("authoritative_claim") is not False:
        fail("C2 selection boundary is overstated")
    if c2.get("source_corpus_version") != c3.get("corpus_version"):
        fail("C2 selection ledger targets a different source corpus version")

    c0_ids = {str(item["story_version_id"]) for item in c0_items}
    primary_ids = {str(item["story_version_id"]) for item in primary_items}
    secondary_ids = {str(item["story_version_id"]) for item in secondary_items}
    if primary_ids & secondary_ids:
        fail("primary and secondary witness sets overlap")
    if primary_ids | secondary_ids != c0_ids:
        fail("primary/secondary union does not exactly match C0")

    primary_family_counts = Counter(str(item["story_family_id"]) for item in primary_items)
    secondary_family_counts = Counter(str(item["story_family_id"]) for item in secondary_items)
    if len(primary_family_counts) != EXPECTED_FAMILIES or set(primary_family_counts.values()) != {1}:
        fail("C3 must contain exactly one product story per family")
    if len(secondary_family_counts) != EXPECTED_FAMILIES or set(secondary_family_counts.values()) != {1}:
        fail("C1 secondary must contain exactly one witness per family")
    if set(primary_family_counts) != set(secondary_family_counts):
        fail("primary and secondary family sets differ")

    c0_family_map = {str(item["story_family_id"]): item for item in c0_families}
    if len(c0_family_map) != EXPECTED_FAMILIES or set(c0_family_map) != set(primary_family_counts):
        fail("C0 family set does not match product families")
    selection_map = {str(item["story_family_id"]): item for item in selections}
    if set(selection_map) != set(c0_family_map):
        fail("C2 selection family set does not match C0")
    primary_by_id = {str(item["story_version_id"]): item for item in primary_items}
    secondary_by_id = {str(item["story_version_id"]): item for item in secondary_items}
    for family_id, selection in selection_map.items():
        primary_id = str(selection.get("primary_version_id"))
        secondary_id = str(selection.get("secondary_witness_id"))
        manifest = c0_family_map[family_id]
        if primary_id not in primary_by_id or secondary_id not in secondary_by_id:
            fail(f"C2 selection does not resolve to primary/secondary layers: {family_id}")
        if primary_by_id[primary_id].get("story_family_id") != family_id:
            fail(f"C2 primary family mismatch: {family_id}")
        if secondary_by_id[secondary_id].get("story_family_id") != family_id:
            fail(f"C2 secondary family mismatch: {family_id}")
        if selection.get("title") != manifest.get("title"):
            fail(f"C2/C0 title mismatch: {family_id}")
        if primary_by_id[primary_id].get("title") != manifest.get("title"):
            fail(f"C3 does not use familiar family title: {family_id}")
        if selection.get("primary_adult_only") is not (primary_by_id[primary_id].get("adult_only") is True):
            fail(f"C2 adult flag mismatch: {family_id}")
        if manifest.get("product_primary_version_id") != primary_id:
            fail(f"C0 primary pointer mismatch: {family_id}")
        if manifest.get("secondary_witness_id") != secondary_id:
            fail(f"C0 secondary pointer mismatch: {family_id}")
        if set(str(value) for value in manifest.get("version_ids", [])) != {primary_id, secondary_id}:
            fail(f"C0 family witness list mismatch: {family_id}")
        if "不向用户提供版本选择" not in str(manifest.get("version_policy")):
            fail(f"C0 family lacks user-facing single-story policy: {family_id}")

    c0_by_id = {str(item["story_version_id"]): item for item in c0_items}
    for record in primary_items:
        assert_record_evidence(record, c0_by_id[str(record["story_version_id"])], runtime_layer=True, now=now)
    for record in secondary_items:
        assert_record_evidence(record, c0_by_id[str(record["story_version_id"])], runtime_layer=False, now=now)

    primary_adult_ids = {
        str(record["story_version_id"]) for record in primary_items if record.get("adult_only") is True
    }
    secondary_adult_ids = {
        str(record["story_version_id"]) for record in secondary_items if record.get("adult_only") is True
    }
    if primary_adult_ids != EXPECTED_PRIMARY_ADULT_IDS:
        fail(f"primary adult-only set changed unexpectedly: {primary_adult_ids}")
    if secondary_adult_ids != EXPECTED_SECONDARY_ADULT_IDS:
        fail(f"secondary adult-only set changed unexpectedly: {secondary_adult_ids}")
    if primary_adult_ids | secondary_adult_ids != EXPECTED_ADULT_IDS:
        fail("total adult-only witness set changed unexpectedly")

    fable_families = {
        str(record["story_family_id"])
        for record in primary_items
        if record.get("story_type") == "fable"
    }
    if not {"yugong_moves_mountains", "fox_borrows_tiger_might"}.issubset(fable_families):
        fail("required fable families are absent from product stories")

    counts = Counter(str(record["story_type"]) for record in primary_items)
    print(
        json.dumps(
            {
                "status": "PASS",
                "corpus_version": c3["corpus_version"],
                "story_families": len(primary_family_counts),
                "product_primary_stories": len(primary_items),
                "secondary_source_witnesses": len(secondary_items),
                "fixed_source_witnesses": len(c0_items),
                "adult_only_witnesses": len(EXPECTED_ADULT_IDS),
                "adult_product_stories": len(primary_adult_ids),
                "production_eligible_records": 0,
                "story_type_counts": dict(sorted(counts.items())),
                "hashes_verified": len(primary_items) + len(secondary_items),
                "c0_annotated_records_aligned": len(primary_items) + len(secondary_items),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
