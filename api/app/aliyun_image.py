"""Fail-closed Alibaba Cloud Model Studio scene-image adapter.

The browser never receives the API key or a provider prompt.  This module reads
credentials from the server environment, sends a bounded prompt to the
workspace-specific Model Studio endpoint, and reduces every provider failure to
a non-blocking local-stage fallback.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()


SceneImageStatus = Literal["ready", "fallback"]
_SUPPORTED_MODELS = frozenset({"z-image-turbo", "wan2.6-t2i"})
_GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_FALLBACK_MESSAGE = "本幕画面暂未生成，可继续观看或重试。"
_PUBLIC_REGION_HOSTS = {
    "cn-beijing": "https://dashscope.aliyuncs.com",
    "ap-southeast-1": "https://dashscope-intl.aliyuncs.com",
    "us-east-1": "https://dashscope-us.aliyuncs.com",
}
_IPV6_DOH_URL = "https://dns.alidns.com/resolve"
_IPV6_PUBLIC_HOSTS = frozenset(urlparse(value).hostname for value in _PUBLIC_REGION_HOSTS.values())
_IPV6_WORKSPACE_HOST = re.compile(
    r"^llm-[a-z0-9-]{3,64}\.(?:cn-beijing|ap-southeast-1|us-east-1|"
    r"eu-central-1|ap-northeast-1)\.maas\.aliyuncs\.com$"
)
logger = logging.getLogger(__name__)


def _env_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _compact(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _valid_image_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return candidate


def _host_region(api_host: str) -> str:
    hostname = (urlparse(api_host).hostname or "").lower()
    if ".cn-beijing." in hostname or hostname == "dashscope.aliyuncs.com":
        return "cn-beijing"
    if ".ap-southeast-1." in hostname or hostname == "dashscope-intl.aliyuncs.com":
        return "ap-southeast-1"
    if ".us-east-1." in hostname or hostname == "dashscope-us.aliyuncs.com":
        return "us-east-1"
    if ".eu-central-1." in hostname:
        return "eu-central-1"
    if ".ap-northeast-1." in hostname:
        return "ap-northeast-1"
    return "unknown"


def _endpoint_type(api_host: str) -> str:
    hostname = (urlparse(api_host).hostname or "").lower()
    if hostname.startswith("llm-") and ".maas.aliyuncs.com" in hostname:
        return "workspace"
    if hostname.startswith("dashscope") and hostname.endswith(".aliyuncs.com"):
        return "dashscope"
    if hostname.startswith("trial.") and ".maas.aliyuncs.com" in hostname:
        return "trial"
    return "custom"


def _default_fallback_host(api_host: str) -> str | None:
    public_host = _PUBLIC_REGION_HOSTS.get(_host_region(api_host))
    if public_host and public_host.rstrip("/") != api_host.rstrip("/"):
        return public_host
    return None


def _ipv6_fallback_host_allowed(api_host: str) -> bool:
    hostname = (urlparse(api_host).hostname or "").lower()
    return hostname in _IPV6_PUBLIC_HOSTS or _IPV6_WORKSPACE_HOST.fullmatch(hostname) is not None


def _public_ipv6_addresses(api_host: str, *, client: Any | None = None) -> tuple[str, ...]:
    """Resolve a tightly allowlisted Alibaba endpoint through trusted DNS-over-HTTPS."""

    hostname = (urlparse(api_host).hostname or "").lower()
    if not _ipv6_fallback_host_allowed(api_host):
        return ()
    requester = client or httpx
    response = requester.get(
        _IPV6_DOH_URL,
        params={"name": hostname, "type": "AAAA"},
        headers={"Accept": "application/dns-json"},
        timeout=10,
        trust_env=False,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("Status") != 0:
        return ()
    addresses: list[str] = []
    for answer in body.get("Answer") or []:
        if not isinstance(answer, dict) or answer.get("type") != 28:
            continue
        try:
            address = ipaddress.ip_address(str(answer.get("data", "")).rstrip("."))
        except ValueError:
            continue
        if address.version != 6 or not address.is_global:
            continue
        candidate = address.compressed
        if candidate not in addresses:
            addresses.append(candidate)
        if len(addresses) == 4:
            break
    return tuple(addresses)


@dataclass(frozen=True)
class AliyunImageConfig:
    api_host: str
    model: str
    api_key: str | None = field(repr=False)
    timeout_seconds: float
    live_enabled: bool
    fallback_api_host: str | None = None
    proxy_url: str | None = field(default=None, repr=False)
    ipv6_fallback_enabled: bool = False

    @classmethod
    def from_env(cls) -> "AliyunImageConfig":
        api_host = os.getenv(
            "ALIYUN_IMAGE_API_HOST",
            "https://dashscope.aliyuncs.com",
        ).rstrip("/")
        configured_fallback = os.getenv("ALIYUN_IMAGE_FALLBACK_API_HOST", "").strip()
        return cls(
            api_host=api_host,
            model=os.getenv("ALIYUN_IMAGE_MODEL", "z-image-turbo").strip(),
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            timeout_seconds=float(os.getenv("ALIYUN_IMAGE_TIMEOUT_SECONDS", "60")),
            live_enabled=_env_enabled("ENABLE_IMAGE_GENERATION"),
            fallback_api_host=(
                configured_fallback.rstrip("/")
                if configured_fallback
                else _default_fallback_host(api_host)
            ),
            proxy_url=os.getenv("ALIYUN_IMAGE_PROXY_URL", "").strip() or None,
            ipv6_fallback_enabled=_env_enabled("ALIYUN_IMAGE_IPV6_FALLBACK_ENABLED"),
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

    @property
    def region(self) -> str:
        return _host_region(self.api_host)

    def public_status(self) -> dict[str, object]:
        """Return masked, prompt-free configuration metadata for health checks."""

        return {
            "configured": self.configured,
            "enabled": self.live_enabled,
            "available": self.available,
            "model": self.model,
            "region": self.region,
            "endpointType": _endpoint_type(self.api_host),
            "sameRegionFallbackConfigured": bool(self.fallback_api_host),
            "proxyConfigured": bool(self.proxy_url),
            "ipv6FallbackEnabled": self.ipv6_fallback_enabled,
        }


@dataclass(frozen=True)
class SceneImageResult:
    status: SceneImageStatus
    image_url: str | None
    alt_text: str
    message: str | None
    retryable: bool
    failure_reason: str | None = None

    @classmethod
    def fallback(
        cls,
        *,
        alt_text: str,
        retryable: bool,
        reason: str,
        message: str = _FALLBACK_MESSAGE,
    ) -> "SceneImageResult":
        return cls(
            status="fallback",
            image_url=None,
            alt_text=alt_text,
            message=message,
            retryable=retryable,
            failure_reason=reason,
        )

    def public_payload(self, *, act_id: str) -> dict[str, object]:
        """Return only browser-safe fields; provider details stay server-side."""

        return {
            "actId": act_id,
            "status": self.status,
            "imageUrl": self.image_url,
            "altText": self.alt_text,
            "message": self.message,
            "retryable": self.retryable,
        }

    def cover_payload(self, *, story_version_id: str) -> dict[str, object]:
        """The same browser-safe fields, keyed to a story card instead of an act."""

        return {
            "storyVersionId": story_version_id,
            "status": self.status,
            "imageUrl": self.image_url,
            "altText": self.alt_text,
            "message": self.message,
            "retryable": self.retryable,
        }


class AliyunImageAdapter:
    """Generate one horizontal lianhuanhua scene for one theatre act."""

    def __init__(
        self,
        config: AliyunImageConfig | None = None,
        *,
        client: Any | None = None,
        doh_client: Any | None = None,
        direct_ipv6_client: Any | None = None,
    ) -> None:
        self.config = config or AliyunImageConfig.from_env()
        self._client = client
        self._doh_client = doh_client
        self._direct_ipv6_client = direct_ipv6_client

    @staticmethod
    def build_prompt(
        *,
        scene_title: str,
        narration: str,
        stage_direction: str,
        story_title: str,
        source_title: str,
    ) -> str:
        # The per-field limits are deliberately tight.  The style clause and the
        # exclusion clause are what keep the picture out of textbook-illustration
        # territory, and they sit at the two ends of the prompt -- if the act text
        # is allowed to grow the tail gets truncated away and the style drifts.
        scene = _compact(scene_title, limit=40) or "未题名的一幕"
        narration_text = _compact(narration, limit=170) or "人物在留白中停驻，准备迈向下一步。"
        direction = _compact(stage_direction, limit=80) or "以留白、构图与人物动作呈现。"
        story = _compact(story_title, limit=40) or "中国古典神话传说"
        source = _compact(source_title, limit=50) or "所选古籍"
        prompt = (
            "横向十六比九的中国工笔连环画，一幅完整画面。"
            "笔法取宋元院体工笔：铁线描、游丝描勾轮廓，线条匀细挺劲、起收有锋；"
            "再以三矾九染层层罩染，绢本设色的温润质地，见绢丝底纹与淡墨晕染。"
            "石青、石绿、赭石、朱砂、藤黄为主，色相沉着内敛。"
            "衣纹、器物、草木以细笔交代纹样，人物面相清秀、手势有戏；"
            "散点透视，前中远景层层推远，边角留白。"
            f"本幕题目：{scene}。本幕内容：{narration_text}。画面调度：{direction}。"
            f"故事取意于《{story}》，采用出处《{source}》。"
            "若本幕落在当代场景，人物服饰器物照实描绘，但一律沿用上述工笔笔法与设色，不改画种。"
            "画面不出现任何文字、题签、水印、界面、边框、品牌标识；"
            "避免教科书插图与儿童读物风、平涂色块、粗黑均匀描边、矢量扁平化、"
            "照片写实、三维塑料感、过度饱和、日式动漫脸、肢体畸形、血腥与惊悚特写。"
        )
        # z-image-turbo accepts at most 800 characters.  Keeping one shared
        # bound makes switching models an environment-only operation.
        return prompt[:790]

    def _parameters(self) -> dict[str, object]:
        if self.config.model == "wan2.6-t2i":
            return {
                "prompt_extend": False,
                "watermark": False,
                "n": 1,
                "negative_prompt": (
                    "文字，题签，水印，标志，界面，边框，照片写实，三维塑料感，"
                    "过度饱和，日式动漫脸，肢体畸形，多余手指，血腥，惊悚特写"
                ),
                "size": "1696*960",
            }
        return {
            "prompt_extend": False,
            "size": "1536*864",
        }

    @staticmethod
    def _extract_image_url(body: object) -> str | None:
        if not isinstance(body, dict):
            return None
        output = body.get("output")
        if not isinstance(output, dict):
            return None
        choices = output.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        for item in content:
            if isinstance(item, dict):
                image_url = _valid_image_url(item.get("image"))
                if image_url:
                    return image_url
        return None

    def _candidate_hosts(self) -> tuple[str, ...]:
        hosts = [self.config.api_host.rstrip("/")]
        fallback = (self.config.fallback_api_host or "").rstrip("/")
        if fallback and fallback not in hosts:
            # Never silently cross regions: API keys and access domains are
            # region-bound in Model Studio.
            if _host_region(fallback) == self.config.region:
                hosts.append(fallback)
        return tuple(hosts)

    def _post(self, client: Any, host: str, payload: dict[str, object]) -> Any:
        timeout = httpx.Timeout(
            self.config.timeout_seconds,
            connect=min(8.0, self.config.timeout_seconds),
        )
        kwargs: dict[str, object] = {
            "headers": {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            "json": payload,
            "timeout": timeout,
        }
        if self.config.proxy_url:
            kwargs["proxy"] = self.config.proxy_url
        return client.post(f"{host}{_GENERATION_PATH}", **kwargs)

    def _post_direct_ipv6(
        self,
        host: str,
        address: str,
        payload: dict[str, object],
    ) -> Any:
        hostname = (urlparse(host).hostname or "").lower()
        parsed_address = ipaddress.ip_address(address)
        if (
            not _ipv6_fallback_host_allowed(host)
            or parsed_address.version != 6
            or not parsed_address.is_global
        ):
            raise ValueError("IPv6 direct target is not allowed")
        request = httpx.Request(
            "POST",
            f"https://[{parsed_address.compressed}]{_GENERATION_PATH}",
            headers={
                "Host": hostname,
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            extensions={"sni_hostname": hostname},
        )
        if self._direct_ipv6_client is not None:
            return self._direct_ipv6_client.send(request)
        # trust_env=False bypasses the fake-IP/system-proxy route.  Certificate
        # verification remains enabled by default and uses the original
        # allowlisted hostname supplied as SNI.
        with httpx.Client(
            trust_env=False,
            timeout=httpx.Timeout(
                self.config.timeout_seconds,
                connect=min(8.0, self.config.timeout_seconds),
            ),
            follow_redirects=False,
        ) as direct_client:
            return direct_client.send(request)

    def _generate_via_ipv6(
        self,
        *,
        hosts: tuple[str, ...],
        payload: dict[str, object],
        alt_text: str,
    ) -> SceneImageResult | None:
        for host in hosts:
            if not _ipv6_fallback_host_allowed(host):
                continue
            try:
                addresses = _public_ipv6_addresses(host, client=self._doh_client)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                self._log_fallback(
                    reason="ipv6_dns_unavailable",
                    host=host,
                    error_type=type(exc).__name__,
                )
                continue
            for address in addresses:
                try:
                    response = self._post_direct_ipv6(host, address, payload)
                    response.raise_for_status()
                    image_url = self._extract_image_url(response.json())
                    if image_url is None:
                        self._log_fallback(reason="invalid_response", host=host)
                        return SceneImageResult.fallback(
                            alt_text=alt_text,
                            retryable=True,
                            reason="invalid_response",
                        )
                    logger.info(
                        "scene image IPv6 fallback succeeded model=%s region=%s "
                        "endpoint_type=%s",
                        self.config.model,
                        _host_region(host),
                        _endpoint_type(host),
                    )
                    return SceneImageResult(
                        status="ready",
                        image_url=image_url,
                        alt_text=alt_text,
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
                    self._log_fallback(
                        reason=reason,
                        host=host,
                        status_code=status_code,
                    )
                    if status_code >= 500:
                        continue
                    return SceneImageResult.fallback(
                        alt_text=alt_text,
                        retryable=status_code == 429,
                        reason=reason,
                    )
                except httpx.TransportError as exc:
                    self._log_fallback(
                        reason="ipv6_transport_unreachable",
                        host=host,
                        error_type=type(exc).__name__,
                    )
                    continue
                except (KeyError, TypeError, ValueError) as exc:
                    self._log_fallback(
                        reason="invalid_response",
                        host=host,
                        error_type=type(exc).__name__,
                    )
                    return SceneImageResult.fallback(
                        alt_text=alt_text,
                        retryable=True,
                        reason="invalid_response",
                    )
        return None

    def _log_fallback(
        self,
        *,
        reason: str,
        host: str,
        status_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        # Deliberately exclude the API key, provider body, prompt and scene
        # text.  This log is operational metadata only.
        logger.warning(
            "scene image provider fallback reason=%s model=%s region=%s "
            "endpoint_type=%s status=%s error_type=%s",
            reason,
            self.config.model,
            _host_region(host),
            _endpoint_type(host),
            status_code,
            error_type,
        )

    def generate_scene(
        self,
        *,
        scene_title: str,
        narration: str,
        stage_direction: str,
        story_title: str,
        source_title: str,
    ) -> SceneImageResult:
        compact_title = _compact(scene_title, limit=80) or "这一幕"
        alt_text = f"{compact_title}的中式连环画画面"
        if not self.config.available:
            reason = "unsupported_model" if not self.config.supported else "disabled"
            return SceneImageResult.fallback(
                alt_text=alt_text,
                retryable=False,
                reason=reason,
                message="智能生成画面尚未开启，本幕继续使用纸影舞台。",
            )

        return self._request_image(
            prompt=self.build_prompt(
                scene_title=scene_title,
                narration=narration,
                stage_direction=stage_direction,
                story_title=story_title,
                source_title=source_title,
            ),
            alt_text=alt_text,
        )

    @staticmethod
    def build_cover_prompt(
        *,
        story_title: str,
        summary: str,
        motifs: str,
        source_title: str,
    ) -> str:
        """One ink line-drawing for a story card's header image.

        Deliberately unlike the act scenes: no colour and no narrative, because
        the card is a title plate rather than a moment in a performance.
        """

        title = _compact(story_title, limit=40) or "中国古典神话传说"
        gist = _compact(summary, limit=180) or "故事中最具代表性的一个场面。"
        imagery = _compact(motifs, limit=70)
        source = _compact(source_title, limit=50) or "所选古籍"
        prompt = (
            "横幅中国画白描小品，一幅完整画面。"
            "纯水墨白描：只以墨线勾勒，不设色、不皴擦、不渲染；"
            "线条取铁线描与高古游丝描，匀细流畅、起收有锋，疏密有致；"
            "宣纸本色留白为底，画面清简，构图取一角半边，大面积留白。"
            f"题材：《{title}》，出自《{source}》。故事梗概：{gist}。"
            + (f"核心意象：{imagery}。" if imagery else "")
            + "只画这则故事最有代表性的一个意象或一个瞬间，不画连续情节，不拼贴多格。"
            "画面不出现任何文字、题签、印章、水印、边框、界面、品牌标识；"
            "避免彩色、水彩、油画、照片写实、三维渲染、日式动漫脸、"
            "教科书插图、剪贴画与矢量扁平化。"
        )
        return prompt[:790]

    @staticmethod
    def build_safe_cover_retry_prompt(
        *,
        story_title: str,
        motifs: str,
        source_title: str,
    ) -> str:
        """A conservative second pass for a rejected or malformed cover.

        Some source summaries contain violence, death or supernatural body
        detail even when the requested image is only a quiet title plate.  A
        provider can reject that first prompt before drawing anything.  The
        retry keeps the title/source grounding and bounded motifs, but asks for
        one non-violent symbolic object or landscape instead of forwarding the
        plot summary again.
        """

        title = _compact(story_title, limit=40) or "中国古典神话传说"
        imagery = _compact(motifs, limit=60)
        source = _compact(source_title, limit=50) or "所选古籍"
        prompt = (
            "横幅中国画白描题图，一幅完整画面。"
            "纯水墨线描，宣纸本色，大面积留白；只画一件传统器物、植物、山水或自然意象，"
            "构图清简含蓄，不画人物冲突、伤害、死亡、惊悚场景或连续情节。"
            "线条取铁线描与高古游丝描，匀细流畅，不设色、不皴擦、不渲染。"
            f"题材取意于《{title}》，出处《{source}》。"
            + (f"可参考的象征意象：{imagery}。" if imagery else "")
            + "画面不出现任何文字、题签、印章、水印、边框、界面或品牌标识；"
            "避免彩色、照片写实、三维渲染、动漫、教科书插图、剪贴画与矢量扁平化。"
        )
        return prompt[:790]

    def generate_story_cover(
        self,
        *,
        story_title: str,
        summary: str,
        motifs: str,
        source_title: str,
    ) -> SceneImageResult:
        compact_title = _compact(story_title, limit=40) or "这则故事"
        alt_text = f"《{compact_title}》的白描题图"
        if not self.config.available:
            reason = "unsupported_model" if not self.config.supported else "disabled"
            return SceneImageResult.fallback(
                alt_text=alt_text,
                retryable=False,
                reason=reason,
                message="智能生成题图尚未开启，本卡继续使用线描图形。",
            )
        result = self._request_image(
            prompt=self.build_cover_prompt(
                story_title=story_title,
                summary=summary,
                motifs=motifs,
                source_title=source_title,
            ),
            alt_text=alt_text,
        )
        if result.status == "ready" or result.failure_reason not in {
            "provider_rejected",
            "invalid_response",
        }:
            return result

        # Card covers are fetched independently after the recommendation has
        # rendered, so one bounded retry does not hold up the recommendation
        # response.  Keep the retry provider-agnostic and never expose either
        # prompt or failure reason to the browser.
        return self._request_image(
            prompt=self.build_safe_cover_retry_prompt(
                story_title=story_title,
                motifs=motifs,
                source_title=source_title,
            ),
            alt_text=alt_text,
        )

    def _request_image(self, *, prompt: str, alt_text: str) -> SceneImageResult:
        payload = {
            "model": self.config.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": self._parameters(),
        }
        client = self._client or httpx
        last_reason = "upstream_failure"
        hosts = self._candidate_hosts()
        for index, host in enumerate(hosts):
            try:
                response = self._post(client, host, payload)
                response.raise_for_status()
                image_url = self._extract_image_url(response.json())
                if image_url is None:
                    last_reason = "invalid_response"
                    self._log_fallback(reason=last_reason, host=host)
                    break
                return SceneImageResult(
                    status="ready",
                    image_url=image_url,
                    alt_text=alt_text,
                    message=None,
                    retryable=True,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in {401, 403}:
                    last_reason = "provider_auth"
                elif status_code == 429:
                    last_reason = "provider_rate_limited"
                elif status_code >= 500:
                    last_reason = "provider_unavailable"
                else:
                    last_reason = "provider_rejected"
                self._log_fallback(
                    reason=last_reason,
                    host=host,
                    status_code=status_code,
                )
                # A workspace endpoint and the public DashScope endpoint in
                # the same region accept the same regional key.  Retry only
                # transient server failures; never retry auth or content
                # rejections against another region.
                if status_code >= 500 and index < len(hosts) - 1:
                    continue
                break
            except httpx.TransportError as exc:
                last_reason = "transport_unreachable"
                self._log_fallback(
                    reason=last_reason,
                    host=host,
                    error_type=type(exc).__name__,
                )
                if self.config.ipv6_fallback_enabled:
                    ipv6_result = self._generate_via_ipv6(
                        hosts=(host,),
                        payload=payload,
                        alt_text=alt_text,
                    )
                    if ipv6_result is not None:
                        return ipv6_result
                if index < len(hosts) - 1:
                    continue
                break
            except (KeyError, TypeError, ValueError) as exc:
                last_reason = "invalid_response"
                self._log_fallback(
                    reason=last_reason,
                    host=host,
                    error_type=type(exc).__name__,
                )
                break
        return SceneImageResult.fallback(
            alt_text=alt_text,
            retryable=True,
            reason=last_reason,
        )

    def __repr__(self) -> str:
        return (
            f"AliyunImageAdapter(model={self.config.model!r}, "
            f"configured={self.config.configured!r}, "
            f"live_enabled={self.config.live_enabled!r})"
        )
