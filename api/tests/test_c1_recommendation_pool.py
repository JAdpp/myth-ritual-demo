from __future__ import annotations

from fastapi.testclient import TestClient

from app.corpus import CorpusRepository, stable_hash
from app.main import create_app


def _confirmed_session(client: TestClient) -> str:
    session_id = client.post(
        "/api/sessions",
        json={
            "consent": {
                "adultConfirmed": True,
                "nonClinicalAcknowledged": True,
                "cloudProcessingAccepted": False,
            }
        },
    ).json()["id"]
    user_story = "一位朋友托我传信，我已经答应，却担心反悔会显得不可靠。"
    brief = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        json={"inputMode": "text", "text": user_story},
    ).json()
    confirmed = client.patch(
        f"/api/sessions/{session_id}/experience-briefs",
        json={
            "briefId": brief["id"],
            "parentVersion": brief["version"],
            "neutralSummary": user_story,
            "confirmed": True,
        },
    )
    assert confirmed.status_code == 200
    return session_id


def test_c1_sqlite_pool_is_indexed_without_replacing_c3() -> None:
    repository = CorpusRepository()
    overview = repository.summary()

    assert len(repository) == 30
    assert overview["sourceSegmentedRecommendationStories"] == 12_353
    assert overview["recommendationPoolStories"] == 12_353
    assert overview["deepAnnotatedStories"] == 30
    assert overview["totalRecommendationCandidates"] == 12_383
    assert overview["defaultTotalRecommendationCandidates"] == 12_380
    assert overview["recommendationPoolMode"] == "all_canonical_records"

    first = repository.recommendation_candidates(
        query="我正在面对关系变化，也需要做一个选择。",
        query_terms=["关系", "变化", "选择"],
        limit=8,
        seed="stable-test-seed",
    )
    second = repository.recommendation_candidates(
        query="我正在面对关系变化，也需要做一个选择。",
        query_terms=["关系", "变化", "选择"],
        limit=8,
        seed="stable-test-seed",
    )
    first_c1 = [item for item in first if item["corpusTier"] == "c1-source-demo"]
    second_c1 = [item for item in second if item["corpusTier"] == "c1-source-demo"]

    assert len(first_c1) == 8
    assert [item["storyVersionId"] for item in first_c1] == [
        item["storyVersionId"] for item in second_c1
    ]
    selected = repository.get(first_c1[0]["storyVersionId"])
    assert selected is not None
    assert selected["deepAnnotated"] is False
    assert selected["storyCard"]["summary"]
    assert selected["sourceCanon"]["work"]
    assert selected["sourceCanon"]["original_excerpt"]
    assert selected["adaptationBoundary"]["mayTransform"]
    assert selected["sourceCanonHash"] == stable_hash(selected["sourceCanon"])


def test_generated_c1_card_can_be_selected_mapped_and_sent_to_theatre() -> None:
    with TestClient(create_app()) as client:
        health = client.get("/api/health").json()
        assert health["eligibleC3Records"] == 30
        assert health["sourceSegmentedRecommendationRecords"] == 12_353
        assert health["recommendationPoolRecords"] == 12_383

        session_id = _confirmed_session(client)
        offer_response = client.post(
            f"/api/sessions/{session_id}/story-offers",
            json={"excludedStoryVersionIds": []},
        )
        assert offer_response.status_code == 200
        offer = offer_response.json()
        generated = next(card for card in offer["cards"] if card["experienceMode"] == "generated")
        prepared = next(card for card in offer["cards"] if card["experienceMode"] == "prepared")
        assert generated["deepAnnotated"] is False
        assert prepared["deepAnnotated"] is True
        assert generated["recommendationBasis"]["mode"] == "source_catalog_demo_match"
        assert generated["recommendationBasis"]["candidateRecallMode"] == "sqlite_fts5"
        assert "用户线索：" in generated["recommendationReason"]
        assert "原典情节：" in generated["recommendationReason"]
        assert "关键差异：" in generated["recommendationReason"]
        assert generated["summary"]
        assert generated["sourceCanon"]["sourceTitle"]

        selection_response = client.post(
            f"/api/sessions/{session_id}/story-selection",
            json={
                "offerId": offer["offerId"],
                "storyVersionId": generated["storyVersionId"],
            },
        )
        assert selection_response.status_code == 200
        selection = selection_response.json()
        assert selection["selectedStory"]["experienceMode"] == "generated"
        assert selection["sourceCanon"]["summary"]
        assert selection["adaptationBoundary"]["mayTransform"]

        node_ids = [
            "world_crack",
            "cross_threshold",
            "allies_resources",
            "new_understanding",
            "bring_back",
        ]
        branch_response = client.post(
            f"/api/sessions/{session_id}/branches",
            json={
                "selectedStoryVersionId": generated["storyVersionId"],
                "nodes": [
                    {
                        "id": node_id,
                        "value": f"用户为{node_id}填写的映射内容",
                        "skipped": False,
                        "expressionOrigin": "user",
                    }
                    for node_id in node_ids
                ],
                "hopeAnchor": {"type": "action", "text": "先完成一个可执行的小步骤"},
            },
        )
        assert branch_response.status_code == 201
        branch = branch_response.json()
        assert branch["readyForApproval"] is True
        assert branch["sourceCanonHash"] == selection["sourceCanonHash"]

        approved = client.post(
            f"/api/sessions/{session_id}/branches/{branch['id']}/approve",
            headers={"If-Match": branch["etag"]},
            json={},
        )
        assert approved.status_code == 200
        theatre = client.post(
            f"/api/sessions/{session_id}/theatre-scripts",
            json={"branchVersion": 1, "branchVersionId": branch["id"]},
        )
        assert theatre.status_code == 201
        assert theatre.json()["storyVersionId"] == generated["storyVersionId"]
        assert 4 <= len(theatre.json()["acts"]) <= 7
