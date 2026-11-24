from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import ready_session


LABELS = ("现实线索：", "原典线索：", "映照差异：")
REALITY = "我答应替朋友把一封信送出去"
CANON = "《搜神记》中，使者持书往告"
DIFFERENCE = "原典里送信已经发生，现实里我还在犹豫要不要出发"


def _labeled_nodes() -> list[dict]:
    value = f"现实线索：{REALITY}；原典线索：{CANON}；映照差异：{DIFFERENCE}。"
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
            "value": value,
            "skipped": False,
            "suggestions": [],
            "expressionOrigin": "user",
        }
        for node_id, title, prompt in definitions
    ]


def test_deterministic_theatre_narration_is_speakable_without_analysis_labels(client: TestClient) -> None:
    session_id, _, offer = ready_session(client)
    selected_id = offer["cards"][0]["storyVersionId"]
    client.post(
        f"/api/sessions/{session_id}/story-selection",
        json={"action": "select", "offerId": offer["id"], "storyVersionId": selected_id},
    )
    nodes = _labeled_nodes()
    branch_response = client.post(
        f"/api/sessions/{session_id}/branches",
        json={"selectedStoryVersionId": selected_id, "nodes": nodes},
    )
    assert branch_response.status_code == 201
    branch = branch_response.json()
    saved = client.patch(
        f"/api/sessions/{session_id}/branches",
        headers={"If-Match": branch["etag"]},
        json={
            "action": "save",
            "branchVersionId": branch["id"],
            "parentVersion": branch["version"],
            "nodes": nodes,
            "hopeAnchor": {"type": "action", "detail": "先把要不要出发这件事说清楚。"},
        },
    )
    assert saved.status_code == 200, saved.text
    branch_v2 = saved.json()
    approved = client.post(
        f"/api/sessions/{session_id}/branches/{branch_v2['id']}/approve",
        headers={"If-Match": branch_v2["etag"]},
        json={"branchVersionId": branch_v2["id"], "parentVersion": branch_v2["version"]},
    )
    assert approved.status_code == 200, approved.text
    theatre = client.post(
        f"/api/sessions/{session_id}/theatre-scripts",
        json={"branchVersionId": branch_v2["id"]},
    )
    assert theatre.status_code == 201, theatre.text
    script = theatre.json()
    assert script["compositionSource"] == "deterministic_fallback"
    node_acts = [act for act in script["acts"] if act.get("sourceNodeIds")]
    assert len(node_acts) == 5
    for act in node_acts:
        narration = act["narration"]
        assert all(label not in narration for label in LABELS)
        assert REALITY in narration
        assert CANON in narration
        assert DIFFERENCE in narration
        assert "与此刻不同的是" in narration
        assert act["subtitle"] == narration
    # The approved canvas keeps the original analysis structure.
    for node in branch_v2["nodes"]:
        assert all(label in node["value"] for label in LABELS)
        assert REALITY in node["value"]
