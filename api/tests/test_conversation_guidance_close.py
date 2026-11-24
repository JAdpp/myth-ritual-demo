from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _session_with_cloud(client: TestClient) -> tuple[str, dict]:
    session = client.post(
        "/api/sessions",
        json={
            "consent": {
                "adultConfirmed": True,
                "nonClinicalAcknowledged": True,
                "cloudProcessingAccepted": True,
            }
        },
    )
    assert session.status_code == 201
    session_id = session.json()["id"]
    brief_response = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        json={"inputMode": "text", "text": "最近工作安排突然改了，我还在想要不要把答应的事继续做完。"},
    )
    assert brief_response.status_code == 201
    brief = brief_response.json()
    assert brief["confirmed"] is False
    return session_id, brief


def _history_from_transcript(transcript: list[dict]) -> list[dict]:
    return [{"role": item["role"], "text": item["text"]} for item in transcript]


def test_closing_and_later_turns_skip_discarded_conversation_call(seed_records: list[dict]) -> None:
    class CountingAdapter:
        def __init__(self) -> None:
            self.conversation_turn_calls = 0
            self.summarize_calls = 0

        def conversation_turn(self, *, user_message: str, history: list[dict]) -> dict[str, str | list[str]]:
            self.conversation_turn_calls += 1
            return {
                "acknowledgement": "我听见你提到这次突然的改动。",
                "followUpQuestion": "那件事里你最在意的是什么？",
                "followUpOptions": ["时间安排", "身边的人"],
            }

        def summarize_experience(self, *, history: list[dict]) -> str:
            self.summarize_calls += 1
            return "用户提到工作安排突然改变，仍在考虑要不要把已经答应的事情做完。"

    app = create_app(corpus_records=seed_records)
    adapter = CountingAdapter()
    app.state.service.model_adapter = adapter

    with TestClient(app) as client:
        session_id, brief = _session_with_cloud(client)
        transcript: list[dict[str, str]] = []
        messages = [
            "安排改了以后，我有些犹豫。",
            "我更在意会不会对不起答应过的人。",
            "我想先把眼下这一件做完再看。",
            "目前最想保留的是把话说清楚的机会。",
            "如果还要补充，就是希望对方也能理解我的限度。",
        ]

        def post_turn(text: str) -> dict:
            response = client.post(
                f"/api/sessions/{session_id}/conversation-turns",
                json={
                    "phase": "encounter",
                    "message": text,
                    "history": _history_from_transcript(transcript),
                },
            )
            assert response.status_code in {200, 201}, response.text
            payload = response.json()
            transcript.append({"role": "user", "text": text})
            transcript.append({"role": "assistant", "text": payload["reply"]})
            return payload

        mid_turns = [post_turn(messages[0]), post_turn(messages[1]), post_turn(messages[2])]
        assert adapter.conversation_turn_calls == 3
        assert adapter.summarize_calls == 0
        assert all(item["guidanceComplete"] is False for item in mid_turns)
        assert all(item["summary"] is None for item in mid_turns)
        assert all(item["followUpQuestion"] for item in mid_turns)

        closing = post_turn(messages[3])
        assert closing["guidanceComplete"] is True
        assert closing["turnsUsed"] == 4
        assert closing["turnBudget"] == 4
        assert closing["followUpQuestion"] == ""
        assert closing["followUpOptions"] == []
        assert closing["reply"] == closing["acknowledgement"]
        assert "摘要" in closing["acknowledgement"]
        assert closing["summary"] == "用户提到工作安排突然改变，仍在考虑要不要把已经答应的事情做完。"
        assert closing["summarySource"] == "model_adapter"
        assert adapter.conversation_turn_calls == 3
        assert adapter.summarize_calls == 1

        later = post_turn(messages[4])
        assert later["guidanceComplete"] is True
        assert later["turnsUsed"] == 5
        assert later["followUpQuestion"] == ""
        assert adapter.conversation_turn_calls == 3
        assert adapter.summarize_calls == 2

        edited = client.patch(
            f"/api/sessions/{session_id}/experience-briefs",
            json={
                "briefId": brief["id"],
                "parentVersion": brief["version"],
                "neutralSummary": "我改写了摘要：先把已经答应的事说清楚，再决定要不要继续。",
                "confirmed": False,
            },
        )
        assert edited.status_code == 200, edited.text
        body = edited.json()
        assert body["confirmed"] is False
        assert body["status"] == "draft"
        assert "我改写了摘要" in body["neutralSummary"]
