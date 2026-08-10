"""FastAPI routes for the Myth Ritual development demo."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from fastapi import Body, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .aliyun_image import AliyunImageAdapter
from .corpus import CorpusRepository
from .models import (
    BranchApprove,
    BranchWrite,
    ConversationTurnCreate,
    ConversationTurnResponse,
    ExperienceBriefCreate,
    ExperienceBriefPatch,
    RitualActionCreate,
    SessionCreate,
    StoryOfferCreate,
    StorySelectionCreate,
    TheatreScriptCreate,
)
from .service import ServiceError, SessionService


def _configure_app_logging() -> None:
    """Let this package's diagnostics reach the process log.

    uvicorn configures only its own loggers and leaves the root at WARNING, so
    every ``logger.info`` in ``app.*`` was silently dropped -- including the
    provider-fallback lines that explain why a turn or a narration degraded.
    The root stays at WARNING so third-party INFO chatter does not come along.
    """

    level = os.getenv("APP_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(level)


def create_app(
    *,
    corpus_path: Path | str | None = None,
    corpus_records: Iterable[Mapping[str, Any]] | None = None,
    single_story_db_path: Path | str | None = None,
    single_story_summary_path: Path | str | None = None,
    image_adapter: AliyunImageAdapter | None = None,
) -> FastAPI:
    _configure_app_logging()
    corpus = CorpusRepository(
        path=corpus_path,
        records=corpus_records,
        single_story_db_path=single_story_db_path,
        single_story_summary_path=single_story_summary_path,
    )
    service = SessionService(corpus, image_adapter=image_adapter)
    api = FastAPI(
        title="Myth Ritual Demo API",
        version="0.1.0",
        description="Development in-memory API; not a clinical or production service.",
    )
    api.state.service = service
    api.state.scene_image_adapter = service.image_adapter
    api.state.tts_adapter = service.tts_adapter
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:3001",
            "http://localhost:3001",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Idempotency-Key", "If-Match"],
        expose_headers=["ETag", "Idempotency-Replayed"],
    )

    @api.exception_handler(ServiceError)
    async def handle_service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "detail": {"code": exc.code, "message": exc.message},
            },
        )

    @api.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for item in exc.errors():
            errors.append(
                {
                    "loc": [str(value) for value in item.get("loc", ())],
                    "msg": item.get("msg", "Invalid request"),
                    "type": item.get("type", "value_error"),
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Request validation failed",
                "detail": errors,
            },
        )

    def run_idempotent(
        *,
        scope: str,
        key: str | None,
        payload: dict[str, Any],
        operation: Any,
        response: Response,
    ) -> dict[str, Any]:
        result, replayed = service.idempotent(scope, key, payload, operation)
        response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
        etag = result.get("etag")
        if etag is None and isinstance(result.get("branch"), dict):
            etag = result["branch"].get("etag")
        if etag:
            response.headers["ETag"] = str(etag)
        return result

    @api.get("/api/health")
    def health() -> dict[str, Any]:
        overview = corpus.summary()
        return {
            "status": "ok",
            "service": "myth-ritual-demo",
            "corpusVersion": corpus.corpus_version,
            "eligibleC3Records": len(corpus),
            "recommendationPoolRecords": corpus.recommendation_candidate_count(
                adult_content_opt_in=True
            ),
            "sourceSegmentedRecommendationRecords": overview[
                "sourceSegmentedRecommendationStories"
            ],
            "corpusOverview": overview,
            "model": service.model_adapter.config.model,
            "liveModelEnabled": service.model_adapter.config.available,
            "imageGeneration": service.image_adapter.config.public_status(),
            "ttsNarration": service.tts_adapter.config.public_status(),
        }

    @api.post("/api/sessions", status_code=201)
    def create_session(
        request: SessionCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope="sessions:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.create_session(request),
            response=response,
        )

    @api.post("/api/sessions/{session_id}/experience-briefs", status_code=201)
    def create_experience_brief(
        session_id: str,
        request: ExperienceBriefCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:experience-briefs:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.create_experience_brief(session_id, request),
            response=response,
        )

    @api.patch("/api/sessions/{session_id}/experience-briefs")
    def patch_experience_brief(
        session_id: str,
        request: ExperienceBriefPatch,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:experience-briefs:patch",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.patch_experience_brief(session_id, request),
            response=response,
        )

    @api.post(
        "/api/sessions/{session_id}/conversation-turns",
        status_code=201,
        response_model=ConversationTurnResponse,
    )
    def create_conversation_turn(
        session_id: str,
        request: ConversationTurnCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:conversation-turns:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.create_conversation_turn(session_id, request),
            response=response,
        )

    @api.post("/api/sessions/{session_id}/story-offers")
    def create_story_offer(
        session_id: str,
        request: StoryOfferCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:story-offers:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.create_story_offer(session_id, request),
            response=response,
        )

    @api.post("/api/sessions/{session_id}/story-selection")
    def create_story_selection(
        session_id: str,
        request: StorySelectionCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:story-selection:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.select_story(session_id, request),
            response=response,
        )

    @api.post("/api/sessions/{session_id}/branches", status_code=201)
    def create_branch(
        session_id: str,
        request: BranchWrite,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:branches:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.write_branch(session_id, request, if_match),
            response=response,
        )

    @api.patch("/api/sessions/{session_id}/branches")
    def patch_branch(
        session_id: str,
        request: BranchWrite,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        operation = (
            (lambda: service.suggest_branch(session_id, request, if_match))
            if request.action == "suggest"
            else (lambda: service.write_branch(session_id, request, if_match))
        )
        return run_idempotent(
            scope=f"{session_id}:branches:{request.action or 'write'}",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=operation,
            response=response,
        )

    @api.post("/api/sessions/{session_id}/branches/{branch_identifier}/approve")
    def approve_branch(
        session_id: str,
        branch_identifier: str,
        response: Response,
        request: BranchApprove | None = Body(default=None),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        body = request or BranchApprove()
        if body.branch_version_id is not None and body.branch_version_id != branch_identifier:
            raise ServiceError(409, "branch_version_conflict", "Body and path branchVersionId differ")
        return run_idempotent(
            scope=f"{session_id}:branches:{branch_identifier}:approve",
            key=idempotency_key,
            payload=body.model_dump(mode="json", by_alias=True),
            operation=lambda: service.approve_branch(session_id, branch_identifier, if_match),
            response=response,
        )

    @api.post("/api/sessions/{session_id}/theatre-scripts", status_code=201)
    def create_theatre_script(
        session_id: str,
        request: TheatreScriptCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:theatre-scripts:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.create_theatre_script(session_id, request),
            response=response,
        )

    @api.post(
        "/api/sessions/{session_id}/theatre-scripts/{script_id}/acts/{act_id}/scene-image"
    )
    def create_scene_image(
        session_id: str,
        script_id: str,
        act_id: str,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:theatre-scripts:{script_id}:acts:{act_id}:scene-image",
            key=idempotency_key,
            payload={"scriptId": script_id, "actId": act_id},
            operation=lambda: service.create_scene_image(session_id, script_id, act_id),
            response=response,
        )

    @api.post("/api/sessions/{session_id}/stories/{story_version_id}/cover")
    def create_story_cover(
        session_id: str,
        story_version_id: str,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:stories:{story_version_id}:cover",
            key=idempotency_key,
            payload={"storyVersionId": story_version_id},
            operation=lambda: service.create_story_cover(session_id, story_version_id),
            response=response,
        )

    @api.post(
        "/api/sessions/{session_id}/theatre-scripts/{script_id}/acts/{act_id}/narration"
    )
    def create_act_narration(
        session_id: str,
        script_id: str,
        act_id: str,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:theatre-scripts:{script_id}:acts:{act_id}:narration",
            key=idempotency_key,
            payload={"scriptId": script_id, "actId": act_id},
            operation=lambda: service.create_act_narration(session_id, script_id, act_id),
            response=response,
        )

    @api.post("/api/sessions/{session_id}/ritual-actions", status_code=201)
    def create_ritual_action(
        session_id: str,
        request: RitualActionCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:ritual-actions:create",
            key=idempotency_key,
            payload=request.model_dump(mode="json", by_alias=True),
            operation=lambda: service.create_ritual_action(session_id, request),
            response=response,
        )

    @api.get("/api/sessions/{session_id}/provenance")
    def get_provenance(session_id: str) -> dict[str, Any]:
        return service.provenance(session_id)

    @api.delete("/api/sessions/{session_id}")
    def delete_session(
        session_id: str,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return run_idempotent(
            scope=f"{session_id}:delete",
            key=idempotency_key,
            payload={"sessionId": session_id},
            operation=lambda: service.delete_session(session_id),
            response=response,
        )

    return api


app = create_app()
