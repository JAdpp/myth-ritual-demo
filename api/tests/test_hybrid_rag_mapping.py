from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import DeepSeekAdapter, DeepSeekConfig
from app.main import create_app


NODE_IDS = [
    "world_crack",
    "cross_threshold",
    "allies_resources",
    "new_understanding",
    "bring_back",
]


def _session_ready_for_offer(client: TestClient, *, cloud: bool) -> str:
    session_id = client.post(
        "/api/sessions",
        json={
            "consent": {
                "adultConfirmed": True,
                "nonClinicalAcknowledged": True,
                "cloudProcessingAccepted": cloud,
            }
        },
    ).json()["id"]
    brief = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        json={"inputMode": "preset", "presetId": "relationship-boundary"},
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
    return session_id


def test_deepseek_structured_rag_and_mapping_payloads_are_bounded() -> None:
    responses = [
        {"terms": ["关系边界", "關係邊界"], "themes": ["关系与边界"]},
        {
            "rankings": [
                {
                    "storyVersionId": f"story-{index}",
                    "score": 0.95 - index / 20,
                    "reason": "候选中的边界变化与已确认摘要可以形成具体比较。",
                    "storySignal": "边界变化",
                }
                for index in range(12)
            ]
        },
        {
            "nodes": [
                {"nodeId": node_id, "value": f"{node_id}的简体中文映照初稿。"}
                for node_id in NODE_IDS
            ],
            "hopeAnchor": {"type": "action", "detail": "先完成一个可修改的小步骤。"},
        },
        {
            "nodeUpdates": [
                {
                    "nodeId": "bring_back",
                    "value": "把最后一步改成更具体、仍可调整的行动。",
                    "rationale": "回应用户希望结尾更具体的要求。",
                }
            ]
        },
    ]

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}
                ]
            }

    class FakeClient:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        def post(self, url: str, **kwargs):
            self.requests.append({"url": url, **kwargs})
            return FakeResponse(responses[len(self.requests) - 1])

    client = FakeClient()
    adapter = DeepSeekAdapter(
        DeepSeekConfig(
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="fixture-token",
            timeout_seconds=3,
            live_enabled=True,
        ),
        client=client,
    )
    plan = adapter.retrieval_plan(user_summary="一段关系正在变化，我希望重新辨认边界。")
    candidates = [
        {
            "storyVersionId": f"story-{index}",
            "title": f"故事{index}",
            "sourceTitle": "古籍",
            "locator": f"卷{index}",
            "excerpt": "原始繁体证据" * 300,
        }
        for index in range(12)
    ]
    rankings = adapter.rerank_candidates(
        user_summary="一段关系正在变化，我希望重新辨认边界。",
        candidates=candidates,
    )
    draft = adapter.generate_mapping_draft(
        user_summary="一段关系正在变化，我希望重新辨认边界。",
        source_canon={
            "sourceTitle": "古籍",
            "excerpt": "原文" * 1000,
            "originalEnding": "原典结局",
            "motifs": ["边界", "选择"],
        },
    )
    updates = adapter.revise_mapping(
        user_message="把最后一步写得更具体一点。",
        user_summary="一段关系正在变化。",
        source_canon={"sourceTitle": "古籍", "excerpt": "原文" * 1000},
        nodes=[{"id": node_id, "value": "现有节点"} for node_id in NODE_IDS],
    )

    assert plan["terms"] == ["关系边界", "關係邊界"]
    assert rankings[0]["storyVersionId"] == "story-0"
    assert len(draft["nodes"]) == 5
    assert draft["hopeAnchor"]["detail"]
    assert updates[0]["nodeId"] == "bring_back"
    rerank_payload = json.loads(client.requests[1]["json"]["messages"][1]["content"])
    assert len(rerank_payload["candidates"]) == 12
    assert all(len(item["excerpt"]) <= 500 for item in rerank_payload["candidates"])
    mapping_payload = json.loads(client.requests[2]["json"]["messages"][1]["content"])
    assert len(mapping_payload["sourceCanon"]["excerpt"]) <= 1200
    assert mapping_payload["sourceCanon"]["excerpt"].startswith("原文")
    assert mapping_payload["sourceCanon"]["originalEnding"] == "原典结局"
    assert all("只用简体中文" in request["json"]["messages"][0]["content"] for request in client.requests[1:])
    assert "fixture-token" not in json.dumps(
        [request["json"] for request in client.requests], ensure_ascii=False
    )


def test_consented_hybrid_rag_auto_mapping_and_freeform_revision() -> None:
    class FakeConfig:
        available = True
        model = "test-rag-model"

    class FakeAdapter:
        config = FakeConfig()
        rerank_candidate_sizes: list[int] = []

        def retrieval_plan(self, *, user_summary: str) -> dict:
            assert user_summary
            return {
                "terms": ["关系边界", "關係邊界", "亲族往来", "親族往來"],
                "themes": ["关系与边界"],
            }

        def rerank_candidates(self, *, user_summary: str, candidates: list[dict]) -> list[dict]:
            assert user_summary
            assert len(candidates) <= 12
            assert all(len(item["excerpt"]) <= 500 for item in candidates)
            assert all(item["excerpt"] for item in candidates)
            assert all(item["sourceTitle"] for item in candidates)
            self.rerank_candidate_sizes.append(len(candidates))
            return [
                {
                    "storyVersionId": item["storyVersionId"],
                    "score": 1 - index / 20,
                    "reason": f"有出处的候选《{item['title']}》提供了可比较的关系边界线索。",
                    "storySignal": "关系边界",
                }
                for index, item in enumerate(candidates)
            ]

        def generate_mapping_draft(self, *, user_summary: str, source_canon: dict) -> dict:
            assert user_summary and source_canon
            assert source_canon["title"]
            assert source_canon["summary"]
            assert source_canon["motifs"]
            assert source_canon["originalEnding"]
            return {
                "nodes": [
                    {"nodeId": node_id, "value": f"{node_id}的完整自动映照初稿。"}
                    for node_id in NODE_IDS
                ],
                "hopeAnchor": {"type": "action", "detail": "先确认边界，再尝试一次具体表达。"},
            }

        def revise_mapping(self, **kwargs) -> list[dict]:
            assert kwargs["user_message"] == "把带回现实这一节点改得更具体。"
            assert kwargs["source_canon"]["summary"]
            assert kwargs["source_canon"]["motifs"]
            return [
                {
                    "nodeId": "bring_back",
                    "value": "先写下一句要表达的边界，再选择合适的时间说出来。",
                    "rationale": "把抽象愿望改成可修改的小步骤。",
                }
            ]

    with TestClient(create_app()) as client:
        adapter = FakeAdapter()
        client.app.state.service.model_adapter = adapter
        session_id = _session_ready_for_offer(client, cloud=True)
        offer_response = client.post(
            f"/api/sessions/{session_id}/story-offers",
            json={"excludedStoryVersionIds": []},
        )
        assert offer_response.status_code == 200
        offer = offer_response.json()
        assert offer["retrievalPlan"]["source"] == "model_adapter"
        assert offer["rerankSource"] == "model_adapter"
        assert offer["ragCandidateLimit"] == 8
        assert adapter.rerank_candidate_sizes == [8]
        assert all(card["recommendationBasis"]["modelGenerated"] for card in offer["cards"])
        assert all(card["recommendationBasis"]["evidence"] for card in offer["cards"])
        assert all(
            card["recommendationBasis"]["retrievalMode"] == "hybrid_rag"
            for card in offer["cards"]
        )

        chosen = offer["cards"][0]
        selected = client.post(
            f"/api/sessions/{session_id}/story-selection",
            json={"offerId": offer["offerId"], "storyVersionId": chosen["storyVersionId"]},
        )
        assert selected.status_code == 200
        assert selected.json()["selectedStory"]["recommendationBasis"]["modelGenerated"] is True

        branch_response = client.post(
            f"/api/sessions/{session_id}/branches",
            json={"selectedStoryVersionId": chosen["storyVersionId"], "nodes": []},
        )
        assert branch_response.status_code == 201
        branch = branch_response.json()
        assert branch["generationMode"] == "model_adapter_mapping_draft"
        assert branch["modelGenerated"] is True
        assert branch["readyForApproval"] is True
        assert len(branch["nodes"]) == 5
        assert branch["hopeAnchor"]["detail"]

        assistant = client.patch(
            f"/api/sessions/{session_id}/branches",
            headers={"If-Match": branch["etag"]},
            json={
                "action": "suggest",
                "branchVersionId": branch["id"],
                "parentVersion": branch["version"],
                "assistantMessage": "把带回现实这一节点改得更具体。",
            },
        )
        assert assistant.status_code == 200
        assert assistant.json()["suggestionSource"] == "model_adapter"
        assert assistant.json()["nodeUpdates"][0]["nodeId"] == "bring_back"
        assert assistant.json()["modelGenerated"] is True


def test_no_cloud_auto_mapping_old_empty_nodes_and_dynamic_theatre_fallback() -> None:
    with TestClient(create_app()) as client:
        session_id = _session_ready_for_offer(client, cloud=False)
        offer = client.post(
            f"/api/sessions/{session_id}/story-offers",
            json={"excludedStoryVersionIds": []},
        ).json()
        chosen = offer["cards"][0]
        client.post(
            f"/api/sessions/{session_id}/story-selection",
            json={"offerId": offer["offerId"], "storyVersionId": chosen["storyVersionId"]},
        )
        old_client_empty_nodes = [
            {"id": node_id, "value": "", "skipped": False} for node_id in NODE_IDS
        ]
        branch_response = client.post(
            f"/api/sessions/{session_id}/branches",
            json={
                "selectedStoryVersionId": chosen["storyVersionId"],
                "nodes": old_client_empty_nodes,
            },
        )
        assert branch_response.status_code == 201
        branch = branch_response.json()
        assert branch["generationMode"] == "deterministic_mapping_draft"
        assert branch["modelGenerated"] is False
        assert branch["readyForApproval"] is True
        assert all(node["value"] for node in branch["nodes"])
        assert branch["hopeAnchor"]["detail"]

        approved = client.post(
            f"/api/sessions/{session_id}/branches/{branch['id']}/approve",
            headers={"If-Match": branch["etag"]},
            json={},
        )
        assert approved.status_code == 200
        theatre_response = client.post(
            f"/api/sessions/{session_id}/theatre-scripts",
            json={"branchVersion": 1, "branchVersionId": branch["id"]},
        )
        assert theatre_response.status_code == 201
        theatre = theatre_response.json()
        assert len(theatre["acts"]) == 7
        assert theatre["generationMode"] == "dynamic_mapping_node_compiler"
        assert all("sourceNodeIds" in act for act in theatre["acts"])

        image = client.post(
            f"/api/sessions/{session_id}/theatre-scripts/{theatre['id']}/acts/{theatre['acts'][0]['id']}/scene-image"
        )
        assert image.status_code == 200
        assert image.json()["status"] == "fallback"
        assert image.json()["imageUrl"] is None
        missing_act = client.post(
            f"/api/sessions/{session_id}/theatre-scripts/{theatre['id']}/acts/missing/scene-image"
        )
        assert missing_act.status_code == 404
