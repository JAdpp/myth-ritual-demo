from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ModelUnavailable
from app.corpus import CorpusRepository
from app.main import create_app
from app.safety import route_text


_REQUIRED_CONSENT = {
    "adultConfirmed": True,
    "nonClinicalAcknowledged": True,
}


def _create_confirmed_session(
    client: TestClient,
    *,
    offer_limit: int = 3,
    cloud_processing_accepted: bool = False,
) -> tuple[str, dict]:
    session_response = client.post(
        "/api/sessions",
        json={
            "consent": {
                "adultConfirmed": True,
                "nonClinicalAcknowledged": True,
                "cloudProcessingAccepted": cloud_processing_accepted,
            }
        },
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]
    brief_response = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        json={"inputMode": "preset", "presetId": "quiet-transition"},
    )
    assert brief_response.status_code == 201
    brief = brief_response.json()
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
    offer_response = client.post(
        f"/api/sessions/{session_id}/story-offers",
        json={"limit": offer_limit, "excludedStoryVersionIds": []},
    )
    assert offer_response.status_code == 200
    return session_id, offer_response.json()


def _select_first(client: TestClient, session_id: str, offer: dict) -> dict:
    response = client.post(
        f"/api/sessions/{session_id}/story-selection",
        json={
            "action": "select",
            "offerId": offer["offerId"],
            "storyVersionId": offer["cards"][0]["storyVersionId"],
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize(
    "consent",
    [
        {},
        {"adultConfirmed": "true", "nonClinicalAcknowledged": True},
        {"adultContentOptIn": True, "nonClinicalAcknowledged": True},
        {**_REQUIRED_CONSENT, "unknownConsent": True},
    ],
)
def test_consent_is_strictly_typed_and_required_acknowledgements_cannot_be_bypassed(
    client: TestClient,
    consent: dict,
) -> None:
    response = client.post("/api/sessions", json={"consent": consent})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_c3_is_fail_closed_but_current_development_corpus_is_admitted(
    seed_records: list[dict],
    tmp_path: Path,
) -> None:
    current = CorpusRepository()
    assert len(current) == 30
    assert len(current.offerable_ids()) == 27
    assert len(current.offerable_ids(adult_content_opt_in=True)) == 30
    overview = current.summary()
    assert overview["productStories"] == 30
    assert overview["sourceWitnesses"] == 60
    assert overview["storyFamilies"] == 30
    assert overview["storyVersions"] == 30
    summary_path = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "corpus"
        / "c1_single_story"
        / "summary.json"
    )
    source_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert source_summary["unique_single_stories"] >= 10_000
    assert overview["catalogEntries"] == source_summary["unique_single_stories"]
    assert overview["catalogUniqueStories"] == source_summary["unique_single_stories"]
    assert overview["catalogRawSegments"] == source_summary["raw_segments"]
    assert overview["catalogRejectedSegments"] == source_summary["rejected_segments"]
    assert overview["catalogFullTextStories"] == source_summary["full_text_stories"]
    assert overview["catalogSourceWorks"] == source_summary["source_works"]
    assert overview["catalogDedupeScope"] == source_summary["dedupe_scope"]
    assert len(overview["catalogWorks"]) == source_summary["source_works"]
    assert sum(item["count"] for item in overview["catalogWorks"]) == overview["catalogUniqueStories"]

    variants: list[dict] = []
    missing_explicit = copy.deepcopy(seed_records[0])
    missing_explicit.pop("eligible_for_demo")
    variants.append(missing_explicit)
    missing_product_primary = copy.deepcopy(seed_records[0])
    missing_product_primary.pop("product_primary")
    variants.append(missing_product_primary)
    missing_runtime_gate = copy.deepcopy(seed_records[0])
    missing_runtime_gate.pop("development_runtime_eligible")
    variants.append(missing_runtime_gate)
    corrupt_excerpt = copy.deepcopy(seed_records[0])
    corrupt_excerpt["source_canon"]["original_excerpt"] += "被篡改"
    variants.append(corrupt_excerpt)
    unknown_review_state = copy.deepcopy(seed_records[0])
    unknown_review_state["review_record"]["status"] = "looks_fine"
    variants.append(unknown_review_state)
    malformed_adult_gate = copy.deepcopy(seed_records[0])
    malformed_adult_gate.update(
        {
            "adult_only": True,
            "default_offer_eligible": True,
            "requires_explicit_adult_opt_in": False,
        }
    )
    variants.append(malformed_adult_gate)

    assert all(len(CorpusRepository(records=[record])) == 0 for record in variants)

    missing_envelope_gate = tmp_path / "missing-runtime-gate.json"
    missing_envelope_gate.write_text(
        json.dumps(
            {
                "corpus_version": "c3-dev-test",
                "snapshot_date": "2026-08-07",
                "corpus_tier": "c3-dev",
                "production_eligible": False,
                "story_versions": [seed_records[0]],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert len(CorpusRepository(path=missing_envelope_gate)) == 0


def test_single_story_summary_overrides_legacy_count_without_loading_story_rows(
    seed_records: list[dict],
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "c1_story_catalog.json"
    legacy_path.write_text(
        json.dumps(
            {
                "snapshot_date": "2026-08-08",
                "story_entries": [
                    {
                        "story_family_id": "legacy_one",
                        "catalog_status": "metadata_only",
                        "tradition_group": "传统分区一",
                    },
                    {
                        "story_family_id": "legacy_two",
                        "catalog_status": "c3_dev_detailed",
                        "tradition_group": "传统分区二",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "catalog_version": "c1-single-story-test",
                "snapshot_date": "2026-08-09",
                "raw_segments": 10_040,
                "rejected_segments": 40,
                "unique_single_stories": 10_000,
                "full_text_stories": 9_700,
                "source_works": 3,
                "status_counts": {"full_text": 9_700, "metadata_only": 300},
                "dedupe_scope": "source_locator_plus_exact_text_hash",
                "work_counts": [
                    {"source_work_id": "work_a", "title": "作品甲", "count": 7_000},
                    {"source_work_id": "work_b", "title": "作品乙", "count": 2_700},
                    {"source_work_id": "work_c", "title": "作品丙", "count": 300},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    repository = CorpusRepository(
        records=seed_records,
        catalog_path=legacy_path,
        single_story_summary_path=summary_path,
    )
    overview = repository.summary()

    assert overview["productStories"] == 4
    assert overview["catalogEntries"] == overview["catalogUniqueStories"] == 10_000
    assert overview["catalogRawSegments"] == 10_040
    assert overview["catalogRejectedSegments"] == 40
    assert overview["catalogFullTextStories"] == 9_700
    assert overview["catalogSourceWorks"] == 3
    assert overview["catalogSnapshotDate"] == "2026-08-09"
    assert overview["catalogGroups"] == [
        {"name": "传统分区一", "count": 1},
        {"name": "传统分区二", "count": 1},
    ]
    assert overview["catalogWorks"] == [
        {"sourceWorkId": "work_a", "title": "作品甲", "count": 7_000},
        {"sourceWorkId": "work_b", "title": "作品乙", "count": 2_700},
        {"sourceWorkId": "work_c", "title": "作品丙", "count": 300},
    ]
    serialized = json.dumps(overview, ensure_ascii=False)
    assert len(serialized.encode("utf-8")) < 25_000
    assert "story_entries" not in serialized
    assert "original_excerpt" not in serialized
    assert '"text"' not in serialized
    assert '"excerpt"' not in serialized


def test_invalid_single_story_summary_falls_back_to_legacy_catalog(
    seed_records: list[dict],
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "c1_story_catalog.json"
    legacy_path.write_text(
        json.dumps(
            {
                "snapshot_date": "2026-08-08",
                "story_entries": [
                    {
                        "story_family_id": "legacy_only",
                        "catalog_status": "metadata_only",
                        "tradition_group": "传统分区",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    invalid_summary_path = tmp_path / "summary.json"
    invalid_summary_path.write_text(
        json.dumps(
            {
                "catalog_version": "broken",
                "snapshot_date": "2026-08-09",
                "raw_segments": 10,
                "rejected_segments": 2,
                "unique_single_stories": 10_000,
                "full_text_stories": 10_000,
                "source_works": 0,
                "status_counts": {},
                "dedupe_scope": "exact_hash",
                "work_counts": [],
            }
        ),
        encoding="utf-8",
    )

    repository = CorpusRepository(
        records=seed_records,
        catalog_path=legacy_path,
        single_story_summary_path=invalid_summary_path,
    )
    overview = repository.summary()

    assert overview["catalogEntries"] == overview["catalogUniqueStories"] == 1
    assert overview["catalogRawSegments"] == 1
    assert overview["catalogWorks"] == []


def test_secondary_source_witnesses_never_enter_runtime() -> None:
    current = CorpusRepository()
    secondary_id = "pangu_shuyiji_body_cosmos"
    secondary_path = Path(__file__).resolve().parents[2] / "data" / "corpus" / "c1_secondary_source_witnesses.json"

    assert current.get(secondary_id) is None
    assert secondary_id not in current.offerable_ids(adult_content_opt_in=True)
    assert len(CorpusRepository(path=secondary_path)) == 0


def test_runtime_rejects_every_record_in_a_duplicate_story_family(
    seed_records: list[dict],
) -> None:
    duplicate = copy.deepcopy(seed_records[0])
    duplicate["story_version_id"] = "jingwei-v2"

    repository = CorpusRepository(records=[*seed_records, duplicate])

    assert len(repository) == 3
    assert repository.get("jingwei-v1") is None
    assert repository.get("jingwei-v2") is None
    assert "jingwei" in repository.rejected_duplicate_family_ids
    overview = repository.summary()
    assert overview["productStories"] == overview["storyVersions"] == 3


def test_expanded_corpus_is_reachable_in_offer_ranking_not_only_counted() -> None:
    reached: set[str] = set()
    with TestClient(create_app()) as client:
        for preset_id in ("new-beginning", "relationship-boundary", "plan-changed"):
            session_id = client.post(
                "/api/sessions",
                json={"consent": {**_REQUIRED_CONSENT, "cloudProcessingAccepted": False}},
            ).json()["id"]
            brief = client.post(
                f"/api/sessions/{session_id}/experience-briefs",
                json={"inputMode": "preset", "presetId": preset_id},
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
            )
            assert offer.status_code == 200
            reached.update(card["storyFamilyId"] for card in offer.json()["cards"])

    assert {
        "peach_blossom_spring",
        "cowherd_weaver_girl",
        "liu_yi_delivers_letter",
        "nanke_dream",
    }.issubset(reached)


def test_deleted_session_cannot_replay_cached_mutations(client: TestClient) -> None:
    create_headers = {"Idempotency-Key": "session-before-delete"}
    created = client.post(
        "/api/sessions", headers=create_headers, json={"consent": _REQUIRED_CONSENT}
    )
    assert created.status_code == 201
    deleted_session_id = created.json()["id"]
    brief_body = {"inputMode": "preset", "presetId": "new-beginning"}
    brief_headers = {"Idempotency-Key": "brief-before-delete"}
    first_brief = client.post(
        f"/api/sessions/{deleted_session_id}/experience-briefs",
        headers=brief_headers,
        json=brief_body,
    )
    assert first_brief.status_code == 201

    deleted = client.delete(
        f"/api/sessions/{deleted_session_id}",
        headers={"Idempotency-Key": "delete-session"},
    )
    assert deleted.status_code == 200

    replay_brief = client.post(
        f"/api/sessions/{deleted_session_id}/experience-briefs",
        headers=brief_headers,
        json=brief_body,
    )
    assert replay_brief.status_code == 404
    assert replay_brief.headers.get("idempotency-replayed") is None
    replay_delete = client.delete(
        f"/api/sessions/{deleted_session_id}",
        headers={"Idempotency-Key": "delete-session"},
    )
    assert replay_delete.status_code == 404

    recreated = client.post(
        "/api/sessions", headers=create_headers, json={"consent": _REQUIRED_CONSENT}
    )
    assert recreated.status_code == 409
    assert recreated.json()["code"] == "idempotency_replay_unavailable"
    assert deleted_session_id not in json.dumps(recreated.json())


def test_session_ttl_is_enforced_before_idempotency_replay(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"consent": _REQUIRED_CONSENT}).json()
    session_id = created["id"]
    body = {"inputMode": "preset", "presetId": "plan-changed"}
    headers = {"Idempotency-Key": "expires-with-session"}
    assert client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        headers=headers,
        json=body,
    ).status_code == 201

    client.app.state.service._sessions[session_id].expires_at = "2000-01-01T00:00:00Z"
    replay = client.post(
        f"/api/sessions/{session_id}/experience-briefs",
        headers=headers,
        json=body,
    )
    assert replay.status_code == 404
    assert session_id not in client.app.state.service._sessions
    assert all(
        not scope.startswith(f"{session_id}:")
        for scope, _ in client.app.state.service._idempotency
    )


def test_refresh_uses_unseen_batches_and_reports_exhaustion(client: TestClient) -> None:
    session_id, first = _create_confirmed_session(client, offer_limit=2)
    first_ids = {card["storyVersionId"] for card in first["cards"]}
    assert len(first_ids) == 2
    assert first["exhausted"] is False
    assert first["canRefresh"] is True

    second_response = client.post(
        f"/api/sessions/{session_id}/story-offers",
        json={"limit": 2, "refresh": True, "excludedStoryVersionIds": []},
    )
    assert second_response.status_code == 200
    second = second_response.json()
    second_ids = {card["storyVersionId"] for card in second["cards"]}
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert second["exhausted"] is True
    assert second["canRefresh"] is False

    final = client.post(
        f"/api/sessions/{session_id}/story-offers",
        json={"limit": 2, "refresh": True, "excludedStoryVersionIds": []},
    ).json()
    assert final["cards"] == []
    assert final["exhausted"] is True
    assert final["canRefresh"] is False


def test_frontend_preset_ids_have_reviewed_semantic_summaries(client: TestClient) -> None:
    session_id = client.post("/api/sessions", json={"consent": _REQUIRED_CONSENT}).json()["id"]
    expected = {
        "new-beginning": "熟悉环境",
        "relationship-boundary": "边界",
        "plan-changed": "计划",
        "quiet-transition": "说不清",
    }
    for preset_id, phrase in expected.items():
        response = client.post(
            f"/api/sessions/{session_id}/experience-briefs",
            json={"inputMode": "preset", "presetId": preset_id},
        )
        assert response.status_code == 201
        summary = response.json()["neutralSummary"]
        assert phrase in summary
        assert preset_id not in summary


def test_server_rejects_branch_approval_until_readiness_invariants_hold(
    client: TestClient,
    frontend_nodes: list[dict],
) -> None:
    session_id, offer = _create_confirmed_session(client)
    selected = _select_first(client, session_id, offer)["selectedStory"]
    branch_response = client.post(
        f"/api/sessions/{session_id}/branches",
        json={"selectedStoryVersionId": selected["storyVersionId"], "nodes": frontend_nodes},
    )
    assert branch_response.status_code == 201
    branch = branch_response.json()
    assert branch["readyForApproval"] is False

    approval = client.post(
        f"/api/sessions/{session_id}/branches/{branch['id']}/approve",
        headers={"If-Match": branch["etag"]},
        json={"branchVersionId": branch["id"], "parentVersion": branch["version"]},
    )
    assert approval.status_code == 409
    assert approval.json()["code"] == "branch_not_ready"


def test_unsaved_ritual_artifact_never_enters_session_or_provenance(
    client: TestClient,
    frontend_nodes: list[dict],
) -> None:
    session_id, offer = _create_confirmed_session(client)
    selected = _select_first(client, session_id, offer)["selectedStory"]
    branch = client.post(
        f"/api/sessions/{session_id}/branches",
        json={
            "selectedStoryVersionId": selected["storyVersionId"],
            "nodes": frontend_nodes,
            "hopeAnchor": {"type": "open", "detail": "保留下一次尝试。"},
        },
    ).json()
    assert branch["readyForApproval"] is True
    approved = client.post(
        f"/api/sessions/{session_id}/branches/{branch['id']}/approve",
        headers={"If-Match": branch["etag"]},
        json={"branchVersionId": branch["id"], "parentVersion": branch["version"]},
    )
    assert approved.status_code == 200
    theatre = client.post(
        f"/api/sessions/{session_id}/theatre-scripts",
        json={"branchVersionId": branch["id"]},
    ).json()
    ritual = client.post(
        f"/api/sessions/{session_id}/ritual-actions",
        json={
            "theatreScriptId": theatre["id"],
            "ritualGesture": "seal",
            "saveArtifact": False,
            "savePreference": "delete",
        },
    )
    assert ritual.status_code == 201
    ephemeral = ritual.json()
    assert ephemeral["saved"] is False

    provenance = client.get(f"/api/sessions/{session_id}/provenance").json()
    assert provenance["ritualActions"] == []
    assert all(event["eventType"] != "ritual_action_completed" for event in provenance["events"])
    assert ephemeral["ritualActionId"] not in json.dumps(provenance, ensure_ascii=False)


@pytest.mark.parametrize(
    "text",
    [
        "《搜神记》里有人自杀，这是原典情节。",
        "我在研究自杀议题。",
        "我不想死，我想活下去。",
        "不要伤害自己，我们去求助。",
        "正在发生一次普通的工作调整。",
        "变化正在发生，我想先看看。",
        "《史记》中的人物想杀人。",
        "I don't want to die; I want to live.",
    ],
)
def test_safety_router_avoids_context_and_negation_false_positives(text: str) -> None:
    assert route_text(text).blocked is False


@pytest.mark.parametrize(
    "text,category",
    [
        ("我现在想自杀，马上就去。", "self_harm"),
        ("活着没意思了。", "self_harm"),
        ("我准备从楼上跳下去。", "self_harm"),
        ("我已经吞了很多药。", "self_harm"),
        ("I don't want to be alive anymore.", "self_harm"),
        ("我要杀他。", "harm_to_others"),
        ("想弄死他。", "harm_to_others"),
    ],
)
def test_safety_router_catches_direct_intent_variants(text: str, category: str) -> None:
    decision = route_text(text)
    assert decision.blocked is True
    assert category in decision.categories


def test_direct_crisis_conversation_blocks_before_external_adapter(client: TestClient) -> None:
    class FakeConfig:
        model = "external-test-model"
        available = True

    class CountingAdapter:
        config = FakeConfig()
        calls = 0

        def conversation_turn(self, **_: object) -> dict[str, object]:
            self.calls += 1
            return {
                "acknowledgement": "不应被调用。",
                "followUpQuestion": "不应被调用吗？",
                "followUpOptions": ["不应被调用一", "不应被调用二"],
            }

    adapter = CountingAdapter()
    client.app.state.service.model_adapter = adapter
    session_response = client.post(
        "/api/sessions",
        json={
            "consent": {
                "adultConfirmed": True,
                "nonClinicalAcknowledged": True,
                "cloudProcessingAccepted": True,
            }
        },
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    response = client.post(
        f"/api/sessions/{session_id}/conversation-turns",
        json={"phase": "encounter", "message": "我现在想自杀，马上就去。", "history": []},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "safety_blocked"
    assert adapter.calls == 0
    provenance = client.get(f"/api/sessions/{session_id}/provenance").json()
    assert provenance["safetyRoute"]["blocked"] is True
    assert provenance["safetyRoute"]["route"] == "crisis_stop"
    assert "self_harm" in provenance["safetyRoute"]["categories"]


def test_provenance_does_not_label_an_arbitrary_adapter_as_deepseek(
    client: TestClient,
    frontend_nodes: list[dict],
) -> None:
    class FakeConfig:
        model = "deepseek-v4-flash"
        available = True

    class FakeAdapter:
        config = FakeConfig()

        def suggest(self, *, node_number: int | None = None, **_: object) -> list[str]:
            return [f"fake-{node_number}-a", f"fake-{node_number}-b"]

    client.app.state.service.model_adapter = FakeAdapter()
    session_id, offer = _create_confirmed_session(client, cloud_processing_accepted=True)
    selected = _select_first(client, session_id, offer)["selectedStory"]
    branch = client.post(
        f"/api/sessions/{session_id}/branches",
        json={
            "selectedStoryVersionId": selected["storyVersionId"],
            "nodes": frontend_nodes,
            "requestSuggestionsFor": 1,
        },
    ).json()
    assert branch["suggestionSource"] == "model_adapter"
    provenance = client.get(f"/api/sessions/{session_id}/provenance").json()
    assert provenance["branchVersions"][0]["suggestionSource"] == "model_adapter"
    assert provenance["modelVersion"] is None
    assert '"suggestionSource": "deepseek"' not in json.dumps(provenance)


def test_external_adapter_is_not_called_without_cloud_consent(
    client: TestClient,
    frontend_nodes: list[dict],
) -> None:
    class FakeConfig:
        model = "external-test-model"
        available = True

    class CountingAdapter:
        config = FakeConfig()
        suggestion_calls = 0
        chat_calls = 0

        def suggest(self, *, node_number: int | None = None, **_: object) -> list[str]:
            self.suggestion_calls += 1
            return [f"external-{node_number}"]

        def chat(self, *, user_message: str, **_: object) -> str:
            self.chat_calls += 1
            return f"external-chat:{user_message}"

    adapter = CountingAdapter()
    client.app.state.service.model_adapter = adapter
    session_id, offer = _create_confirmed_session(client, cloud_processing_accepted=False)
    selected = _select_first(client, session_id, offer)["selectedStory"]
    branch = client.post(
        f"/api/sessions/{session_id}/branches",
        json={
            "selectedStoryVersionId": selected["storyVersionId"],
            "nodes": frontend_nodes,
            "requestSuggestionsFor": 1,
        },
    ).json()
    assert branch["suggestionSource"] == "deterministic_fallback"
    assert adapter.suggestion_calls == 0
    conversation = client.post(
        f"/api/sessions/{session_id}/conversation-turns",
        json={"phase": "encounter", "message": "我在适应一个新环境。", "history": []},
    )
    assert conversation.status_code == 201
    payload = conversation.json()
    assert payload["source"] == "deterministic_fallback"
    assert "？" not in payload["acknowledgement"]
    assert payload["followUpQuestion"].count("？") == 1
    assert payload["followUpQuestion"].endswith("？")
    assert payload["reply"] == (
        f'{payload["acknowledgement"]}\n\n{payload["followUpQuestion"]}'
    )
    assert len(payload["followUpOptions"]) == 3
    assert all("新环境" in option for option in payload["followUpOptions"])
    assert all("？" not in option and "?" not in option for option in payload["followUpOptions"])
    assert adapter.chat_calls == 0


def test_consented_conversation_uses_bounded_adapter_response(client: TestClient) -> None:
    class FakeConfig:
        model = "external-test-model"
        available = True

    class FakeAdapter:
        config = FakeConfig()

        def conversation_turn(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
        ) -> dict[str, object]:
            assert user_message == "我在适应一个新环境。"
            assert len(history) == 1
            return {
                "reply": "你正在比较旧节奏和新环境；哪一部分最想先保留下来？",
                "followUpOptions": [
                    "从新环境的第一天说起",
                    "说说旧节奏被打乱的那个细节",
                ],
            }

    client.app.state.service.model_adapter = FakeAdapter()
    session_id, _ = _create_confirmed_session(client, cloud_processing_accepted=True)
    response = client.post(
        f"/api/sessions/{session_id}/conversation-turns",
        json={
            "phase": "encounter",
            "message": "我在适应一个新环境。",
            "history": [{"role": "assistant", "text": "最近发生了什么变化？"}],
        },
    )
    assert response.status_code == 201
    assert response.json() == {
        "phase": "encounter",
        "reply": "你正在比较旧节奏和新环境。\n\n哪一部分最想先保留下来？",
        "acknowledgement": "你正在比较旧节奏和新环境。",
        "followUpQuestion": "哪一部分最想先保留下来？",
        "followUpOptions": [
            "沿着“适应一个新环境”：从新环境的第一天说起",
            "沿着“适应一个新环境”：说说旧节奏被打乱的那个细节",
        ],
        "source": "model_adapter",
        "modelVersion": None,
        "turnsUsed": 1,
        "turnBudget": 4,
        "guidanceComplete": False,
        "summarySource": None,
    }


def test_consented_conversation_preserves_explicit_two_beat_adapter_response(
    client: TestClient,
) -> None:
    class FakeConfig:
        model = "external-test-model"
        available = True

    class FakeAdapter:
        config = FakeConfig()

        def conversation_turn(self, **_: object) -> dict[str, object]:
            return {
                "acknowledgement": "你把任务拆开交给大家，这个决定改变了现场的分工。",
                "followUpQuestion": "接下来最先发生了什么？",
                "followUpOptions": [
                    "从任务拆开后的第一个变化说起",
                    "补充分工之后大家最先做的事",
                ],
            }

    client.app.state.service.model_adapter = FakeAdapter()
    session_id, _ = _create_confirmed_session(client, cloud_processing_accepted=True)
    response = client.post(
        f"/api/sessions/{session_id}/conversation-turns",
        json={
            "phase": "encounter",
            "message": "我决定把任务拆开交给大家。",
            "history": [],
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["acknowledgement"] == "你把任务拆开交给大家，这个决定改变了现场的分工。"
    assert payload["followUpQuestion"] == "接下来最先发生了什么？"
    assert payload["reply"] == (
        "你把任务拆开交给大家，这个决定改变了现场的分工。\n\n"
        "接下来最先发生了什么？"
    )
    assert payload["source"] == "model_adapter"


def test_malformed_two_beat_adapter_response_falls_back_atomically(
    client: TestClient,
) -> None:
    class FakeConfig:
        model = "external-test-model"
        available = True

    class FakeAdapter:
        config = FakeConfig()

        def conversation_turn(self, **_: object) -> dict[str, object]:
            return {
                "acknowledgement": "这一句本来来自模型。",
                "followUpQuestion": "这一问本来来自模型？",
                "followUpOptions": ["现在要继续吗？", "还是换一个话题？"],
            }

    client.app.state.service.model_adapter = FakeAdapter()
    session_id, _ = _create_confirmed_session(client, cloud_processing_accepted=True)
    response = client.post(
        f"/api/sessions/{session_id}/conversation-turns",
        json={"phase": "encounter", "message": "我刚接手了一个新任务。", "history": []},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["source"] == "deterministic_fallback"
    assert payload["acknowledgement"] != "这一句本来来自模型。"
    assert payload["reply"] == (
        f'{payload["acknowledgement"]}\n\n{payload["followUpQuestion"]}'
    )
    assert all("？" not in option and "?" not in option for option in payload["followUpOptions"])


def test_failed_conversation_model_falls_back_to_user_specific_options(
    client: TestClient,
) -> None:
    class FakeConfig:
        model = "external-test-model"
        available = True

    class FailingAdapter:
        config = FakeConfig()
        calls = 0

        def conversation_turn(self, **_: object) -> dict[str, object]:
            self.calls += 1
            raise ValueError("simulated malformed provider response")

    adapter = FailingAdapter()
    client.app.state.service.model_adapter = adapter
    session_id, _ = _create_confirmed_session(client, cloud_processing_accepted=True)
    response = client.post(
        f"/api/sessions/{session_id}/conversation-turns",
        json={
            "phase": "encounter",
            "message": "我搬到新城市后，第一次独自参加了邻里活动。",
            "history": [],
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["source"] == "deterministic_fallback"
    assert payload["modelVersion"] is None
    assert payload["reply"] == (
        f'{payload["acknowledgement"]}\n\n{payload["followUpQuestion"]}'
    )
    assert "？" not in payload["acknowledgement"]
    assert payload["followUpQuestion"].count("？") == 1
    assert len(payload["followUpOptions"]) == 3
    assert all(
        "新城市" in option or "邻里活动" in option
        for option in payload["followUpOptions"]
    )
    assert adapter.calls == 1


def test_conversation_message_length_gate_runs_before_cloud_adapter(
    client: TestClient,
) -> None:
    class FakeConfig:
        model = "external-test-model"
        available = True

    class CountingAdapter:
        config = FakeConfig()
        calls = 0

        def conversation_turn(self, **_: object) -> dict[str, object]:
            self.calls += 1
            return {
                "reply": "不应被调用",
                "followUpOptions": ["不应被调用一", "不应被调用二"],
            }

    adapter = CountingAdapter()
    client.app.state.service.model_adapter = adapter
    session_id, _ = _create_confirmed_session(client, cloud_processing_accepted=True)
    response = client.post(
        f"/api/sessions/{session_id}/conversation-turns",
        json={"phase": "encounter", "message": "换岗" * 251, "history": []},
    )

    assert response.status_code == 422
    assert adapter.calls == 0
