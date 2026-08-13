from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from app.aliyun_image import SceneImageResult
from app.main import create_app


class ReadyCoverConfig:
    model = "private-provider-model"
    available = True

    @staticmethod
    def public_status() -> dict[str, object]:
        return {
            "configured": True,
            "enabled": True,
            "available": True,
            "model": "private-provider-model",
        }


class ReadyCoverAdapter:
    config = ReadyCoverConfig()

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_story_cover(self, **kwargs: Any) -> SceneImageResult:
        self.calls.append(kwargs)
        return SceneImageResult(
            status="ready",
            image_url="https://example-oss.aliyuncs.com/card.png?Expires=1",
            alt_text=f"《{kwargs['story_title']}》的白描题图",
            message=None,
            retryable=True,
        )


def _offered_session(client: TestClient, *, cloud: bool) -> tuple[str, dict[str, Any]]:
    session = client.post(
        "/api/sessions",
        json={
            "consent": {
                "adultConfirmed": True,
                "nonClinicalAcknowledged": True,
                "cloudProcessingAccepted": cloud,
                "adultContentOptIn": False,
            }
        },
    ).json()
    session_id = session["id"]
    brief = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        json={"inputMode": "preset", "presetId": "change"},
    ).json()
    confirmed = client.patch(
        f"/api/sessions/{session_id}/experience-briefs",
        json={
            "briefId": brief["id"],
            "parentVersion": brief["version"],
            "neutralSummary": brief["neutralSummary"],
            "confirmed": True,
        },
    )
    assert confirmed.status_code == 200
    offer = client.post(
        f"/api/sessions/{session_id}/story-offers",
        json={"excludedStoryVersionIds": []},
    ).json()
    return session_id, offer["cards"][0]


def test_offered_story_cover_is_ready_cached_and_provider_private(seed_records: list[dict]) -> None:
    adapter = ReadyCoverAdapter()
    with TestClient(create_app(corpus_records=seed_records, image_adapter=adapter)) as client:
        session_id, card = _offered_session(client, cloud=True)
        path = f"/api/sessions/{session_id}/stories/{card['storyVersionId']}/cover"

        first = client.post(path)
        second = client.post(path)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "ready"
    assert first.json()["imageUrl"].startswith("https://")
    assert len(adapter.calls) == 1
    assert "private-provider-model" not in json.dumps(first.json())
    assert "apiKey" not in json.dumps(first.json())


def test_cover_requires_cloud_consent_and_never_calls_provider(seed_records: list[dict]) -> None:
    adapter = ReadyCoverAdapter()
    with TestClient(create_app(corpus_records=seed_records, image_adapter=adapter)) as client:
        session_id, card = _offered_session(client, cloud=False)
        response = client.post(
            f"/api/sessions/{session_id}/stories/{card['storyVersionId']}/cover"
        )

    assert response.status_code == 200
    assert response.json()["status"] == "fallback"
    assert response.json()["imageUrl"] is None
    assert response.json()["retryable"] is False
    assert adapter.calls == []


def test_cover_endpoint_cannot_enumerate_unoffered_stories(seed_records: list[dict]) -> None:
    adapter = ReadyCoverAdapter()
    with TestClient(create_app(corpus_records=seed_records, image_adapter=adapter)) as client:
        session_id, card = _offered_session(client, cloud=True)
        offered_ids = {
            candidate["storyVersionId"]
            for offer in client.app.state.service._sessions[session_id].story_offers
            for candidate in offer["cards"]
        }
        unoffered = next(
            record["story_version_id"]
            for record in seed_records
            if record["story_version_id"] not in offered_ids
        )
        response = client.post(f"/api/sessions/{session_id}/stories/{unoffered}/cover")

    assert response.status_code == 422
    assert response.json()["code"] == "story_not_offered"
    assert adapter.calls == []
