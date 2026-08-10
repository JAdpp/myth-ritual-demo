from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.main import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def disable_live_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit and integration tests must never inherit live-provider flags from .env."""

    monkeypatch.setenv("ENABLE_LIVE_MODEL_GENERATION", "false")
    monkeypatch.setenv("ENABLE_IMAGE_GENERATION", "false")
    for name in (
        "DASHSCOPE_API_KEY",
        "ALIYUN_IMAGE_API_HOST",
        "ALIYUN_IMAGE_FALLBACK_API_HOST",
        "ALIYUN_IMAGE_PROXY_URL",
        "ALIYUN_IMAGE_IPV6_FALLBACK_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def _record(
    story_version_id: str,
    family_id: str,
    title: str,
    work: str,
    *,
    adult_only: bool = False,
) -> dict:
    excerpt = f"{title}的公版原典摘句。"
    return {
        "story_version_id": story_version_id,
        "family_id": family_id,
        "corpus_tier": "c3-dev",
        "editorial_status": "editorial_draft",
        "eligible_for_demo": True,
        "product_primary": True,
        "production_eligible": False,
        "development_runtime_eligible": True,
        "title": title,
        "era": "先秦至汉",
        "genre": "myth",
        "source_canon": {
            "immutable": True,
            "work": work,
            "section": "固定章节",
            "source_url": f"https://example.test/{story_version_id}",
            "source_permalink": f"https://example.test/{story_version_id}?oldid=1",
            "page_revision_id": 1,
            "page_revision_timestamp": "2026-08-07T00:00:00Z",
            "original_excerpt": excerpt,
            "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "summary": f"{title}的可验证结构化摘要。",
            "ending": f"{title}原典结局。",
            "evidence_anchor": "chapter:1",
        },
        "story_card": {
            "title": title,
            "summary": f"{title}的现代语言梗概。",
            "characters": [title],
            "conflict": "主角面对难以改变的环境。",
            "motifs": ["坚持", "变化"],
            "imagery": ["山", "海"],
            "emotional_arc": "困境—选择—开放收束",
            "resonance": "可供思考持续行动与边界。",
            "non_fit": "若不希望触及损失议题，可换卡。",
            "content_warnings": ["损失"] if adult_only else [],
        },
        "adaptation_boundary": {
            "must_preserve": ["原典结局不可改写为原典事实"],
            "may_transform": ["时代背景", "对白"],
            "prohibited": ["命定人格匹配"],
        },
        "rights_and_access": {
            "status": "development_short_excerpt_open_review",
            "access_mode": "short_excerpt_adult_gated" if adult_only else "short_excerpt",
            "copyright_basis": "public_domain_base_text",
            "allowed_uses": ["local_development_runtime", "display", "adaptation"],
        },
        "review_record": {"status": "c3_dev_pending_dual_review", "reviewer": "fixture"},
        "provenance": [
            {
                "id": f"ref-{story_version_id}",
                "title": work,
                "locator": "chapter:1",
                "url": f"https://example.test/{story_version_id}",
            }
        ],
        "adult_only": adult_only,
        "default_offer_eligible": not adult_only,
        "requires_explicit_adult_opt_in": adult_only,
    }


@pytest.fixture
def seed_records() -> list[dict]:
    return [
        _record("jingwei-v1", "jingwei", "精卫填海", "山海经"),
        _record("kuafu-v1", "kuafu", "夸父逐日", "山海经"),
        _record("nvwa-v1", "nvwa", "女娲补天", "淮南子"),
        _record("ganjiang-v1", "ganjiang", "干将莫邪", "搜神记", adult_only=True),
    ]


@pytest.fixture
def app(seed_records: list[dict]):
    return create_app(corpus_records=seed_records)


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def frontend_nodes() -> list[dict]:
    definitions = [
        ("world_crack", "原来的世界与裂缝", "什么发生了变化？"),
        ("cross_threshold", "跨过门槛", "愿意尝试什么？"),
        ("allies_resources", "盟友与资源", "什么能提供帮助？"),
        ("new_understanding", "新的理解", "如何重新理解？"),
        ("bring_back", "带着什么回来", "保留什么可能性？"),
    ]
    return [
        {
            "id": node_id,
            "title": title,
            "prompt": prompt,
            "value": f"{title}的用户草稿",
            "skipped": False,
            "suggestions": [],
            "expressionOrigin": "user",
        }
        for node_id, title, prompt in definitions
    ]


def ready_session(client: TestClient) -> tuple[str, dict, dict]:
    session = client.post(
        "/api/sessions",
        json={
            "consent": {
                "adultConfirmed": True,
                "nonClinicalAcknowledged": True,
                "cloudProcessingAccepted": False,
            }
        },
    ).json()
    session_id = session["id"]
    brief_response = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        json={"inputMode": "preset", "presetId": "change"},
    )
    assert brief_response.status_code == 201
    brief = brief_response.json()
    confirmed_response = client.patch(
        f"/api/sessions/{session_id}/experience-briefs",
        json={
            "briefId": brief["id"],
            "parentVersion": brief["version"],
            "neutralSummary": brief["neutralSummary"],
            "confirmed": True,
        },
    )
    assert confirmed_response.status_code == 200
    offer_response = client.post(
        f"/api/sessions/{session_id}/story-offers",
        json={"excludedStoryVersionIds": []},
    )
    assert offer_response.status_code == 200
    return session_id, confirmed_response.json(), offer_response.json()
