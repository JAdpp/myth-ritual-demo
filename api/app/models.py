"""Pydantic request models for the public API."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


def _to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class APIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class SessionConsent(APIModel):
    """Explicit, strictly typed consent flags accepted by the development API."""

    adult_confirmed: StrictBool = False
    adult_content_opt_in: StrictBool = False
    non_clinical_acknowledged: StrictBool = False
    cloud_processing_accepted: StrictBool = False

    @model_validator(mode="after")
    def validate_required_acknowledgements(self) -> "SessionConsent":
        if not self.adult_confirmed:
            raise ValueError("adultConfirmed must be true to create a session")
        if not self.non_clinical_acknowledged:
            raise ValueError("nonClinicalAcknowledged must be true to create a session")
        if self.adult_content_opt_in and not self.adult_confirmed:
            raise ValueError("adultContentOptIn requires adultConfirmed")
        return self


class SessionCreate(APIModel):
    locale: str = "zh-CN"
    consent: SessionConsent = Field(default_factory=SessionConsent)
    corpus_version: str | None = None


class ExperienceBriefCreate(APIModel):
    input_mode: Literal["text", "preset"] | None = None
    text: str | None = Field(default=None, max_length=1000)
    original_text: str | None = Field(default=None, max_length=1000)
    preset_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_input(self) -> "ExperienceBriefCreate":
        has_text = bool(
            (self.text and self.text.strip())
            or (self.original_text and self.original_text.strip())
        )
        has_preset = bool(self.preset_id and self.preset_id.strip())
        if not has_text and not has_preset:
            raise ValueError("text/originalText or presetId is required")
        if self.input_mode == "text" and not has_text:
            raise ValueError("text/originalText is required when inputMode is text")
        if self.input_mode == "preset" and not has_preset:
            raise ValueError("presetId is required when inputMode is preset")
        return self


class ExperienceBriefPatch(APIModel):
    brief_id: str | None = None
    parent_version: int | None = Field(default=None, ge=1)
    neutral_summary: str | None = Field(default=None, max_length=500)
    confirmed_summary: str | None = Field(default=None, max_length=500)
    confirmed: bool = True
    delete_draft: bool = False


class ConversationMessage(APIModel):
    role: Literal["assistant", "user"]
    text: str = Field(min_length=1, max_length=500)


class ConversationTurnCreate(APIModel):
    phase: Literal["encounter"] = "encounter"
    message: str = Field(min_length=1, max_length=500)
    history: list[ConversationMessage] = Field(default_factory=list, max_length=8)


class ConversationTurnResponse(APIModel):
    phase: Literal["encounter"] = "encounter"
    reply: str = Field(min_length=1, max_length=260)
    # Wider than the question because the first beat now opens with empathy
    # before it lands on the detail; at 80 the model's warmer openings were
    # being rejected, which dropped the turn back to the flat local wording.
    acknowledgement: str = Field(min_length=1, max_length=120)
    # Empty on the closing turn: once guidance is complete 栖蝶 stops asking.
    follow_up_question: str = Field(default="", max_length=80)
    follow_up_options: list[str] = Field(default_factory=list, max_length=3)
    source: str
    model_version: str | None = None
    turns_used: int = Field(default=0, ge=0)
    turn_budget: int = Field(default=0, ge=0)
    guidance_complete: bool = False
    summary_source: str | None = None
    # The summary the closing turn just wrote onto the brief. Returned so the
    # browser shows the model's summary rather than the brief it created
    # earlier in the same round-trip, whose text is only the user's own
    # sentences pasted together.
    summary: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_two_beat_turn(self) -> "ConversationTurnResponse":
        if self.summary is not None and not self.guidance_complete:
            raise ValueError("a summary is only returned by the closing turn")

        if "？" in self.acknowledgement or "?" in self.acknowledgement:
            raise ValueError("acknowledgement must not contain a question")

        if self.guidance_complete:
            if self.follow_up_question or self.follow_up_options:
                raise ValueError("a closing turn must not ask a further question")
            if self.reply != self.acknowledgement:
                raise ValueError("a closing turn's reply is its acknowledgement")
            return self

        if not 2 <= len(self.follow_up_question) <= 80:
            raise ValueError("followUpQuestion is required until guidance completes")
        if not 2 <= len(self.follow_up_options) <= 3:
            raise ValueError("followUpOptions must contain two or three options")
        question_mark_count = self.follow_up_question.count(
            "？"
        ) + self.follow_up_question.count("?")
        if question_mark_count != 1 or self.follow_up_question[-1] not in {"？", "?"}:
            raise ValueError("followUpQuestion must contain exactly one question")
        if any("？" in option or "?" in option for option in self.follow_up_options):
            raise ValueError("followUpOptions must not contain questions")
        if self.reply != f"{self.acknowledgement}\n\n{self.follow_up_question}":
            raise ValueError("reply must compose the two conversation beats")
        return self


class StoryOfferCreate(APIModel):
    limit: int = Field(default=3, ge=2, le=3)
    refresh: bool = False
    excluded_story_version_ids: list[str] = Field(default_factory=list)


class StorySelectionCreate(APIModel):
    action: Literal["select", "reject_all", "refresh"] = "select"
    offer_id: str | None = None
    story_version_id: str | None = None
    story_version_ids: list[str] | None = None
    combine: bool = False
    resonance: str | None = Field(default=None, max_length=500)


class BranchNode(APIModel):
    text: str | None = Field(default=None, max_length=2000)
    skipped: bool = False
    contribution: Literal["user", "model_expression"] = "user"

    @model_validator(mode="after")
    def validate_content(self) -> "BranchNode":
        if not self.skipped and not (self.text and self.text.strip()):
            raise ValueError("text is required unless the node is skipped")
        return self


class BranchNodeDraft(APIModel):
    id: Literal[
        "world_crack",
        "cross_threshold",
        "allies_resources",
        "new_understanding",
        "bring_back",
    ]
    title: str = Field(default="", max_length=200)
    prompt: str = Field(default="", max_length=1000)
    value: str = Field(default="", max_length=2000)
    skipped: bool = False
    suggestions: list[str] = Field(default_factory=list)
    expression_origin: Literal["user", "model_edited"] | None = None


class HopeAnchor(APIModel):
    type: Literal["action", "relationship", "meaning", "open"]
    text: str | None = Field(default=None, max_length=1000)
    detail: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_detail(self) -> "HopeAnchor":
        if not ((self.text and self.text.strip()) or (self.detail and self.detail.strip())):
            raise ValueError("text or detail is required")
        return self


class BranchWrite(APIModel):
    action: Literal["suggest", "save"] | None = None
    assistant_message: str | None = Field(default=None, max_length=500)
    branch_version_id: str | None = None
    parent_version: int | None = Field(default=None, ge=0)
    selected_story_version_id: str | None = None
    node_id: Literal[
        "world_crack",
        "cross_threshold",
        "allies_resources",
        "new_understanding",
        "bring_back",
    ] | None = None
    nodes: list[BranchNodeDraft] | None = None
    node_updates: dict[str, BranchNode] = Field(default_factory=dict)
    hope_anchor: HopeAnchor | None = None
    request_suggestions_for: int | None = Field(default=None, ge=1, le=5)
    preview: str | None = Field(default=None, max_length=8000)

    @field_validator("node_updates")
    @classmethod
    def validate_node_keys(cls, value: dict[str, BranchNode]) -> dict[str, BranchNode]:
        invalid = [key for key in value if not re.fullmatch(r"[1-5]", key)]
        if invalid:
            raise ValueError(f"nodeUpdates keys must be 1 through 5: {invalid}")
        return value


class TheatreScriptCreate(APIModel):
    branch_version: int | None = Field(default=None, ge=1)
    branch_version_id: str | None = None
    setting: str | None = None
    mood: str | None = None
    asset_refs: list[str] = Field(default_factory=list, alias="assetRefs")
    image_prompt: str | None = Field(default=None, alias="imagePrompt")


class BranchApprove(APIModel):
    branch_version_id: str | None = None
    parent_version: int | None = Field(default=None, ge=0)


class RitualActionCreate(APIModel):
    action_type: Literal[
        "name_story",
        "last_line",
        "light_finale",
        "open_curtain",
        "close_curtain",
        "stamp_card",
    ] | None = None
    theatre_script_id: str | None = None
    value: str | None = Field(default=None, max_length=1000)
    story_title: str | None = Field(default=None, max_length=200)
    final_line: str | None = Field(default=None, max_length=500)
    ritual_gesture: Literal["seal", "light"] | None = None
    save_artifact: bool | None = None
    save_preference: Literal["save", "delete", "undecided"] = "undecided"

    @model_validator(mode="after")
    def require_value_for_text_actions(self) -> "RitualActionCreate":
        if self.action_type is None and self.ritual_gesture is None:
            raise ValueError("actionType or ritualGesture is required")
        if self.action_type in {"name_story", "last_line"} and not (self.value and self.value.strip()):
            raise ValueError("value is required for this ritual action")
        return self
