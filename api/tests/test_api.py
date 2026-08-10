from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import DeepSeekAdapter, DeepSeekConfig, ModelUnavailable
from app.corpus import stable_hash
from app.main import create_app

from conftest import ready_session


def _select_first(client: TestClient, session_id: str, offer: dict) -> dict:
    card = offer["cards"][0]
    response = client.post(
        f"/api/sessions/{session_id}/story-selection",
        json={
            "action": "select",
            "offerId": offer["id"],
            "storyVersionId": card["storyVersionId"],
        },
    )
    assert response.status_code == 200
    return response.json()


def test_frontend_contract_end_to_end_and_source_canon_immutable(
    client: TestClient,
    frontend_nodes: list[dict],
) -> None:
    session_id, brief, offer = ready_session(client)
    assert brief["confirmed"] is True
    assert len(offer["cards"]) == 3
    assert offer["id"] == offer["offerId"]
    assert all(card["storyVersionId"] != "ganjiang-v1" for card in offer["cards"])
    assert len({card["storyFamilyId"] for card in offer["cards"]}) == 3
    assert all(card["recommendationReason"] for card in offer["cards"])
    assert all(card["recommendationBasis"]["modelGenerated"] is False for card in offer["cards"])
    assert all(card["explanation"]["plotBeats"] for card in offer["cards"])
    assert all(card["illustrationKey"] == card["storyFamilyId"] for card in offer["cards"])

    selected = _select_first(client, session_id, offer)
    selected_id = selected["selectedStory"]["storyVersionId"]
    before = client.app.state.service.corpus.get(selected_id)
    before_hash = stable_hash(before["sourceCanon"])

    branch_response = client.post(
        f"/api/sessions/{session_id}/branches",
        json={"selectedStoryVersionId": selected_id, "nodes": frontend_nodes},
    )
    assert branch_response.status_code == 201
    branch_v1 = branch_response.json()
    assert branch_v1["id"] == "branch-v1"
    assert branch_v1["version"] == 1
    assert len(branch_v1["nodes"]) == 5
    assert branch_response.headers["etag"] == branch_v1["etag"]

    suggestion_response = client.patch(
        f"/api/sessions/{session_id}/branches",
        headers={"If-Match": branch_v1["etag"]},
        json={
            "action": "suggest",
            "branchVersionId": branch_v1["id"],
            "parentVersion": branch_v1["version"],
            "nodeId": "cross_threshold",
            "nodes": frontend_nodes,
        },
    )
    assert suggestion_response.status_code == 200
    suggestion = suggestion_response.json()
    assert suggestion["nodeId"] == "cross_threshold"
    assert len(suggestion["suggestions"]) == 2
    assert suggestion["suggestionSource"] == "deterministic_fallback"

    updated_nodes = [dict(node) for node in frontend_nodes]
    updated_nodes[1]["value"] = "主角先停下来，再尝试一个很小的步骤。"
    save_response = client.patch(
        f"/api/sessions/{session_id}/branches",
        headers={"If-Match": branch_v1["etag"]},
        json={
            "action": "save",
            "branchVersionId": branch_v1["id"],
            "parentVersion": branch_v1["version"],
            "nodes": updated_nodes,
            "hopeAnchor": {"type": "open", "detail": "事情未解，但可以保留下一次尝试。"},
            "preview": "用户批准前的现代支线预览。",
        },
    )
    assert save_response.status_code == 200
    branch_v2 = save_response.json()
    assert branch_v2["id"] == "branch-v2"
    assert branch_v2["parentVersionId"] == branch_v1["id"]

    unapproved = client.post(
        f"/api/sessions/{session_id}/theatre-scripts",
        json={"branchVersionId": branch_v2["id"]},
    )
    assert unapproved.status_code == 409
    assert unapproved.json()["code"] == "branch_not_approved"

    approve_response = client.post(
        f"/api/sessions/{session_id}/branches/{branch_v2['id']}/approve",
        headers={"If-Match": branch_v2["etag"]},
        json={"branchVersionId": branch_v2["id"], "parentVersion": branch_v2["version"]},
    )
    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["status"] == "approved"
    assert approved["approvedVersion"] == 2

    theatre_response = client.post(
        f"/api/sessions/{session_id}/theatre-scripts",
        json={"branchVersionId": branch_v2["id"]},
    )
    assert theatre_response.status_code == 201
    theatre = theatre_response.json()
    assert theatre["id"] == "theatre-v1"
    assert theatre["branchVersionId"] == branch_v2["id"]
    assert 4 <= len(theatre["acts"]) <= 7
    assert theatre["totalDurationSeconds"] == sum(
        act["durationSeconds"] for act in theatre["acts"]
    )
    assert theatre["generationMode"] == "dynamic_mapping_node_compiler"
    assert all(act["id"] and act["stageDirection"] and act["mood"] for act in theatre["acts"])
    compiled_frame_copy = " ".join(
        [
            theatre["acts"][0]["title"],
            theatre["acts"][0]["narration"],
            theatre["acts"][-1]["title"],
            theatre["acts"][-1]["narration"],
            *theatre["finalLineSuggestions"],
        ]
    )
    assert theatre["acts"][0]["title"] == "序幕 · 幕布拉开"
    assert not any(
        phrase in compiled_frame_copy
        for phrase in ("一面镜子", "不只是", "真正的出路", "与其强求")
    )

    ritual_response = client.post(
        f"/api/sessions/{session_id}/ritual-actions",
        json={
            "theatreScriptId": theatre["id"],
            "storyTitle": "留下一点可能",
            "finalLine": "我会把下一步留给明天。",
            "ritualGesture": "seal",
            "saveArtifact": True,
        },
    )
    assert ritual_response.status_code == 201
    artifact = ritual_response.json()
    assert artifact["id"]
    assert artifact["ritualGesture"] == "seal"
    assert artifact["saved"] is True

    provenance_response = client.get(f"/api/sessions/{session_id}/provenance")
    assert provenance_response.status_code == 200
    provenance = provenance_response.json()
    assert provenance["sourceCanon"]["storyVersionId"] == selected_id
    assert any(entry["origin"] == "source_canon" for entry in provenance["entries"])
    assert any(entry["origin"] == "user_created" for entry in provenance["entries"])
    branch_entries = [
        entry for entry in provenance["entries"] if entry["origin"] != "source_canon"
    ]
    assert len(branch_entries) == 5
    assert all(entry["id"].startswith(f"{branch_v2['id']}:") for entry in branch_entries)

    after = client.app.state.service.corpus.get(selected_id)
    assert stable_hash(after["sourceCanon"]) == before_hash
    assert provenance["sourceCanonHash"] == before_hash


def test_health_reports_live_corpus_overview(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    overview = payload["corpusOverview"]
    assert overview["productStories"] == 4
    assert overview["sourceWitnesses"] == 4
    assert overview["storyFamilies"] == 4
    assert overview["storyVersions"] == 4
    assert len(overview["familyIds"]) == 4
    assert sum(item["count"] for item in overview["genres"]) == 4
    assert overview["productionEligibleVersions"] == 0
    assert payload["imageGeneration"] == {
        "configured": False,
        "enabled": False,
        "available": False,
        "model": "z-image-turbo",
        "region": "cn-beijing",
        "endpointType": "dashscope",
        "sameRegionFallbackConfigured": False,
        "proxyConfigured": False,
        "ipv6FallbackEnabled": False,
    }
    assert "apiKey" not in json.dumps(payload["imageGeneration"])
    assert "llm-" not in json.dumps(payload["imageGeneration"])


def test_story_combination_is_422(client: TestClient) -> None:
    session_id, _, offer = ready_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/story-selection",
        json={
            "action": "select",
            "offerId": offer["id"],
            "storyVersionIds": [
                offer["cards"][0]["storyVersionId"],
                offer["cards"][1]["storyVersionId"],
            ],
            "combine": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "story_combination_forbidden"


def test_crisis_input_stops_personalization_and_raw_text_is_not_retained(client: TestClient) -> None:
    session_id = client.post(
        "/api/sessions",
        json={"consent": {"adultConfirmed": True, "nonClinicalAcknowledged": True}},
    ).json()["id"]
    raw_text = "我现在想自杀，马上就去。"
    brief_response = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        json={"inputMode": "text", "originalText": raw_text},
    )
    assert brief_response.status_code == 201
    assert brief_response.json()["safetyRoute"]["blocked"] is True

    offers_response = client.post(
        f"/api/sessions/{session_id}/story-offers",
        json={"excludedStoryVersionIds": []},
    )
    assert offers_response.status_code == 403
    assert offers_response.json()["code"] == "safety_blocked"

    provenance = client.get(f"/api/sessions/{session_id}/provenance").json()
    assert raw_text not in json.dumps(provenance, ensure_ascii=False)
    assert provenance["safetyRoute"]["route"] == "crisis_stop"
    assert provenance["sourceCanon"] is None


def test_delete_makes_session_inaccessible(client: TestClient) -> None:
    session_id = client.post(
        "/api/sessions",
        json={"consent": {"adultConfirmed": True, "nonClinicalAcknowledged": True}},
    ).json()["id"]
    delete_response = client.delete(f"/api/sessions/{session_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
    assert delete_response.json()["deletionProof"]

    provenance_response = client.get(f"/api/sessions/{session_id}/provenance")
    assert provenance_response.status_code == 404
    assert provenance_response.json()["code"] == "session_not_found"


def test_idempotency_key_replays_and_reuse_with_different_body_conflicts(
    client: TestClient,
    frontend_nodes: list[dict],
) -> None:
    session_id, _, offer = ready_session(client)
    selected = _select_first(client, session_id, offer)["selectedStory"]
    body = {"selectedStoryVersionId": selected["storyVersionId"], "nodes": frontend_nodes}
    headers = {"Idempotency-Key": "branch-create-1"}

    first = client.post(f"/api/sessions/{session_id}/branches", json=body, headers=headers)
    second = client.post(f"/api/sessions/{session_id}/branches", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["idempotency-replayed"] == "true"

    changed = {**body, "preview": "different request"}
    conflict = client.post(f"/api/sessions/{session_id}/branches", json=changed, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_reused"


def test_parent_version_and_etag_conflicts_are_409(
    client: TestClient,
    frontend_nodes: list[dict],
) -> None:
    session_id, _, offer = ready_session(client)
    selected = _select_first(client, session_id, offer)["selectedStory"]
    branch = client.post(
        f"/api/sessions/{session_id}/branches",
        json={"selectedStoryVersionId": selected["storyVersionId"], "nodes": frontend_nodes},
    ).json()

    stale_parent = client.patch(
        f"/api/sessions/{session_id}/branches",
        headers={"If-Match": branch["etag"]},
        json={
            "action": "save",
            "branchVersionId": branch["id"],
            "parentVersion": 0,
            "nodes": frontend_nodes,
        },
    )
    assert stale_parent.status_code == 409
    assert stale_parent.json()["code"] == "parent_version_conflict"

    stale_etag = client.patch(
        f"/api/sessions/{session_id}/branches",
        headers={"If-Match": '"stale"'},
        json={
            "action": "save",
            "branchVersionId": branch["id"],
            "parentVersion": branch["version"],
            "nodes": frontend_nodes,
        },
    )
    assert stale_etag.status_code == 409
    assert stale_etag.json()["code"] == "etag_conflict"


def test_adult_confirmed_does_not_enable_sensitive_story(client: TestClient) -> None:
    session_id, _, offer = ready_session(client)
    assert "ganjiang-v1" not in {card["storyVersionId"] for card in offer["cards"]}

    guessed = client.post(
        f"/api/sessions/{session_id}/story-selection",
        json={
            "action": "select",
            "offerId": offer["id"],
            "storyVersionId": "ganjiang-v1",
        },
    )
    assert guessed.status_code == 422
    assert guessed.json()["code"] == "story_not_offered"


def test_explicit_adult_content_opt_in_can_offer_sensitive_story(seed_records: list[dict]) -> None:
    app = create_app(corpus_records=seed_records)
    with TestClient(app) as client:
        session = client.post(
            "/api/sessions",
            json={
                "consent": {
                    "adultConfirmed": True,
                    "adultContentOptIn": True,
                    "nonClinicalAcknowledged": True,
                }
            },
        ).json()
        session_id = session["id"]
        brief = client.post(
            f"/api/sessions/{session_id}/experience-briefs",
            json={"inputMode": "preset", "presetId": "change"},
        ).json()
        client.patch(
            f"/api/sessions/{session_id}/experience-briefs",
            json={
                "briefId": brief["id"],
                "parentVersion": brief["version"],
                "neutralSummary": brief["neutralSummary"],
                "confirmed": True,
            },
        )
        offer = client.post(
            f"/api/sessions/{session_id}/story-offers",
            json={"excludedStoryVersionIds": []},
        ).json()
        assert "ganjiang-v1" in {card["storyVersionId"] for card in offer["cards"]}


def test_empty_corpus_is_tolerated_until_offers_are_requested() -> None:
    app = create_app(corpus_records=[])
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["eligibleC3Records"] == 0
        session_id = client.post(
            "/api/sessions",
            json={"consent": {"adultConfirmed": True, "nonClinicalAcknowledged": True}},
        ).json()["id"]
        brief = client.post(
            f"/api/sessions/{session_id}/experience-briefs",
            json={"inputMode": "preset", "presetId": "choice"},
        ).json()
        client.patch(
            f"/api/sessions/{session_id}/experience-briefs",
            json={
                "briefId": brief["id"],
                "parentVersion": brief["version"],
                "neutralSummary": brief["neutralSummary"],
                "confirmed": True,
            },
        )
        response = client.post(
            f"/api/sessions/{session_id}/story-offers",
            json={"excludedStoryVersionIds": []},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "c3_corpus_unavailable"


def test_deepseek_adapter_uses_env_only_and_never_exposes_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-that-must-not-appear")
    config = DeepSeekConfig.from_env()
    adapter = DeepSeekAdapter(config)
    assert config.model == "deepseek-v4-flash"
    assert config.configured is True
    assert "test-secret-that-must-not-appear" not in repr(adapter)
    try:
        adapter.suggest(node_number=1)
    except ModelUnavailable:
        pass
    else:
        raise AssertionError("Development adapter must remain offline")


def test_deepseek_adapter_live_call_is_opt_in_and_structured(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-provider-token")
    monkeypatch.setenv("ENABLE_LIVE_MODEL_GENERATION", "true")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"suggestions":["我愿意先走一小步。","我会带着新的理解回来。"]}'
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self) -> None:
            self.request: dict | None = None

        def post(self, url: str, **kwargs):
            self.request = {"url": url, **kwargs}
            return FakeResponse()

    fake_client = FakeClient()
    adapter = DeepSeekAdapter(DeepSeekConfig.from_env(), client=fake_client)
    suggestions = adapter.suggest(node_number=2)

    assert suggestions == ["我愿意先走一小步。", "我会带着新的理解回来。"]
    assert fake_client.request is not None
    assert fake_client.request["url"] == "https://api.deepseek.com/chat/completions"
    assert fake_client.request["json"]["model"] == "deepseek-v4-flash"
    serialized_payload = str(fake_client.request["json"])
    assert "test-provider-token" not in serialized_payload


def test_deepseek_conversation_is_short_structured_and_uses_bounded_history(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-provider-token")
    monkeypatch.setenv("ENABLE_LIVE_MODEL_GENERATION", "true")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {"message": {"content": '{"reply":"你提到节奏发生了变化；什么最值得先保留？"}'}}
                ]
            }

    class FakeClient:
        def __init__(self) -> None:
            self.request: dict | None = None

        def post(self, url: str, **kwargs):
            self.request = {"url": url, **kwargs}
            return FakeResponse()

    fake_client = FakeClient()
    adapter = DeepSeekAdapter(DeepSeekConfig.from_env(), client=fake_client)
    history = [
        {"role": "user", "text": f"历史-{index}"}
        for index in range(8)
    ]
    reply = adapter.chat(user_message="我在适应新的合作节奏。", history=history)

    assert reply == "你提到节奏发生了变化。\n\n什么最值得先保留？"
    assert fake_client.request is not None
    messages = fake_client.request["json"]["messages"]
    assert len(messages) == 8  # system + last six history turns + current message
    assert "历史-0" not in str(messages)
    assert "历史-7" in str(messages)
    assert "test-provider-token" not in str(fake_client.request["json"])


def test_deepseek_conversation_turn_returns_event_grounded_options(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-provider-token")
    monkeypatch.setenv("ENABLE_LIVE_MODEL_GENERATION", "true")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"acknowledgement":"你提到搬进新住处后的第一晚，我先把这个细节留下来。",'
                                '"followUpQuestion":"当时最先让你留意到什么？",'
                                '"followUpOptions":["从搬进新住处那天说起",'
                                '"说说第一晚听见陌生声音时的反应"]}'
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self) -> None:
            self.request: dict | None = None

        def post(self, url: str, **kwargs):
            self.request = {"url": url, **kwargs}
            return FakeResponse()

    fake_client = FakeClient()
    adapter = DeepSeekAdapter(DeepSeekConfig.from_env(), client=fake_client)
    turn = adapter.conversation_turn(
        user_message="我搬进新住处后的第一晚，听见了陌生的楼道声音。",
        history=[],
    )

    assert turn == {
        "reply": "你提到搬进新住处后的第一晚，我先把这个细节留下来。\n\n当时最先让你留意到什么？",
        "acknowledgement": "你提到搬进新住处后的第一晚，我先把这个细节留下来。",
        "followUpQuestion": "当时最先让你留意到什么？",
        "followUpOptions": [
            "从搬进新住处那天说起",
            "说说第一晚听见陌生声音时的反应",
        ],
    }
    assert fake_client.request is not None
    system_prompt = fake_client.request["json"]["messages"][0]["content"]
    assert "不得返回固定主题菜单" in system_prompt
    assert "两拍式回应" in system_prompt
    assert "不得夸奖用户、诊断用户或臆测" in system_prompt
    assert "不要推荐、暗示或硬编码任何中国古典神话传说" in system_prompt
    assert "test-provider-token" not in str(fake_client.request["json"])
