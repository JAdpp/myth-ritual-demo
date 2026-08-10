"""Fail-closed Alibaba Cloud Model Studio (DashScope) CosyVoice narration adapter.

Mirrors ``aliyun_image``: the browser never receives the API key or the
narration prompt.  Credentials come from the server environment, the request is
bounded, and every provider failure degrades to silent-with-subtitles rather
than blocking playback.

Enable with ``ENABLE_TTS_NARRATION=true`` and ``DASHSCOPE_API_KEY``; the same
key already used for scene images works here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()


NarrationStatus = Literal["ready", "fallback"]

# CosyVoice speech-synthesis models available through DashScope.
_SUPPORTED_MODELS = frozenset({"cosyvoice-v1", "cosyvoice-v2"})
_SYNTHESIS_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_FALLBACK_MESSAGE = "本幕旁白暂未生成，可继续观看字幕。"

# Bound on what may be sent to the provider, per act.
_MAX_NARRATION_CHARS = 320

logger = logging.getLogger(__name__)


def _env_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _compact(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _valid_audio_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return candidate


@dataclass(frozen=True)
class AliyunTtsConfig:
    api_host: str
    model: str
    voice: str
    api_key: str | None = field(repr=False)
    timeout_seconds: float
    live_enabled: bool

    @classmethod
    def from_env(cls) -> "AliyunTtsConfig":
        return cls(
            api_host=os.getenv(
                "ALIYUN_TTS_API_HOST",
                os.getenv("ALIYUN_IMAGE_API_HOST", "https://dashscope.aliyuncs.com"),
            ).rstrip("/"),
            model=os.getenv("ALIYUN_TTS_MODEL", "cosyvoice-v2").strip(),
            # longxiaochun is a warm, unhurried narration voice; override per deploy.
            voice=os.getenv("ALIYUN_TTS_VOICE", "longxiaochun").strip(),
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            timeout_seconds=float(os.getenv("ALIYUN_TTS_TIMEOUT_SECONDS", "45")),
            live_enabled=_env_enabled("ENABLE_TTS_NARRATION"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_host)

    @property
    def supported(self) -> bool:
        return self.model in _SUPPORTED_MODELS

    @property
    def available(self) -> bool:
        return self.configured and self.live_enabled and self.supported

    def public_status(self) -> dict[str, object]:
        """Masked, text-free configuration metadata for health checks."""

        return {
            "configured": self.configured,
            "enabled": self.live_enabled,
            "available": self.available,
            "model": self.model,
            "voice": self.voice,
        }


@dataclass(frozen=True)
class NarrationResult:
    status: NarrationStatus
    audio_url: str | None
    message: str | None
    retryable: bool
    failure_reason: str | None = None

    @classmethod
    def fallback(
        cls,
        *,
        retryable: bool,
        reason: str,
        message: str = _FALLBACK_MESSAGE,
    ) -> "NarrationResult":
        return cls(
            status="fallback",
            audio_url=None,
            message=message,
            retryable=retryable,
            failure_reason=reason,
        )

    def public_payload(self, *, act_id: str) -> dict[str, object]:
        return {
            "actId": act_id,
            "status": self.status,
            "audioUrl": self.audio_url,
            "message": self.message,
            "retryable": self.retryable,
        }


class AliyunTtsAdapter:
    """Synthesise one act's narration with CosyVoice."""

    def __init__(
        self,
        config: AliyunTtsConfig | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self.config = config or AliyunTtsConfig.from_env()
        self._client = client

    @staticmethod
    def build_text(*, narration: str) -> str:
        """Only the act's own narration is sent -- never the user's raw input."""

        return _compact(narration, limit=_MAX_NARRATION_CHARS)

    def _log_fallback(self, *, reason: str, **fields: object) -> None:
        logger.info(
            "narration fallback reason=%s model=%s %s",
            reason,
            self.config.model,
            " ".join(f"{key}={value}" for key, value in fields.items()),
        )

    @staticmethod
    def _extract_audio_url(body: object) -> str | None:
        if not isinstance(body, dict):
            return None
        output = body.get("output")
        if not isinstance(output, dict):
            return None
        audio = output.get("audio")
        if isinstance(audio, dict):
            candidate = _valid_audio_url(audio.get("url"))
            if candidate:
                return candidate
        choices = output.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                for part in message.get("content") or []:
                    if isinstance(part, dict):
                        candidate = _valid_audio_url(part.get("audio") or part.get("url"))
                        if candidate:
                            return candidate
        return None

    def synthesize(self, *, narration: str) -> NarrationResult:
        """Return a narration audio URL, or a non-blocking fallback."""

        if not self.config.available:
            reason = (
                "not_configured"
                if not self.config.configured
                else "not_enabled"
                if not self.config.live_enabled
                else "unsupported_model"
            )
            self._log_fallback(reason=reason)
            return NarrationResult.fallback(retryable=False, reason=reason)

        text = self.build_text(narration=narration)
        if not text:
            return NarrationResult.fallback(retryable=False, reason="empty_narration")

        payload: dict[str, object] = {
            "model": self.config.model,
            "input": {
                "text": text,
                "voice": self.config.voice,
            },
            "parameters": {
                "text_type": "PlainText",
                "format": "mp3",
                "sample_rate": 24000,
            },
        }

        client = self._client or httpx
        url = f"{self.config.api_host}{_SYNTHESIS_PATH}"
        try:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            audio_url = self._extract_audio_url(response.json())
            if audio_url is None:
                self._log_fallback(reason="invalid_response")
                return NarrationResult.fallback(retryable=True, reason="invalid_response")
            return NarrationResult(
                status="ready",
                audio_url=audio_url,
                message=None,
                retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                reason = "provider_auth"
            elif status_code == 429:
                reason = "provider_rate_limited"
            elif status_code >= 500:
                reason = "provider_unavailable"
            else:
                reason = "provider_rejected"
            self._log_fallback(reason=reason, status_code=status_code)
            return NarrationResult.fallback(
                retryable=status_code == 429 or status_code >= 500,
                reason=reason,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self._log_fallback(reason="transport_error", error_type=type(exc).__name__)
            return NarrationResult.fallback(retryable=True, reason="transport_error")
