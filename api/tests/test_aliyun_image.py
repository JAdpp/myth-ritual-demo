from __future__ import annotations

from typing import Any

import httpx

from app.aliyun_image import AliyunImageAdapter, AliyunImageConfig


class FakeResponse:
    def __init__(self, body: object, *, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("provider failure", request=request, response=response)

    def json(self) -> object:
        return self._body


class RecordingClient:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def config(
    *,
    model: str = "z-image-turbo",
    enabled: bool = True,
    fallback_api_host: str | None = None,
    proxy_url: str | None = None,
    ipv6_fallback_enabled: bool = False,
) -> AliyunImageConfig:
    return AliyunImageConfig(
        api_host="https://llm-testworkspace.cn-beijing.maas.aliyuncs.com",
        model=model,
        api_key="test-only-key",
        timeout_seconds=12,
        live_enabled=enabled,
        fallback_api_host=fallback_api_host,
        proxy_url=proxy_url,
        ipv6_fallback_enabled=ipv6_fallback_enabled,
    )


def scene_kwargs() -> dict[str, str]:
    return {
        "scene_title": "渡口相逢",
        "narration": "主角在水边停住，重新看见同行者留下的灯。",
        "stage_direction": "纸偶从左侧入场，远山与水纹缓慢亮起。",
        "story_title": "精卫填海",
        "source_title": "山海经",
    }


def successful_body() -> dict[str, object]:
    return {
        "output": {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": [
                            {
                                "image": "https://example-oss.aliyuncs.com/scene.png?Expires=1",
                                "type": "image",
                            }
                        ]
                    },
                }
            ]
        }
    }


def cover_kwargs() -> dict[str, str]:
    return {
        "story_title": "柳毅传书",
        "summary": "柳毅为龙女传书，往返洞庭与泾水。",
        "motifs": "书信、洞庭、龙女",
        "source_title": "柳毅传",
    }


def test_disabled_generation_fails_closed_without_calling_provider() -> None:
    client = RecordingClient(FakeResponse(successful_body()))
    adapter = AliyunImageAdapter(config(enabled=False), client=client)

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "fallback"
    assert result.image_url is None
    assert result.retryable is False
    assert client.calls == []
    assert "test-only-key" not in repr(adapter)
    assert "test-only-key" not in repr(adapter.config)


def test_z_image_uses_sync_multimodal_route_and_horizontal_payload() -> None:
    client = RecordingClient(FakeResponse(successful_body()))
    adapter = AliyunImageAdapter(config(), client=client)

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "ready"
    assert result.image_url == "https://example-oss.aliyuncs.com/scene.png?Expires=1"
    assert result.public_payload(act_id="act-2") == {
        "actId": "act-2",
        "generationSource": "aliyun_image_model",
        "fallbackReason": None,
        "status": "ready",
        "imageUrl": result.image_url,
        "altText": "渡口相逢的中式连环画画面",
        "message": None,
        "retryable": True,
    }
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"].endswith("/api/v1/services/aigc/multimodal-generation/generation")
    assert call["timeout"].connect == 8
    assert call["timeout"].read == 12
    payload = call["json"]
    assert payload["model"] == "z-image-turbo"
    assert payload["parameters"] == {"prompt_extend": False, "size": "1536*864"}
    prompt = payload["input"]["messages"][0]["content"][0]["text"]
    assert "渡口相逢" in prompt
    assert "纸偶从左侧入场" in prompt
    assert "《精卫填海》" in prompt
    assert "《山海经》" in prompt
    assert len(prompt) <= 790


def test_story_cover_uses_baimiao_prompt_and_browser_safe_payload() -> None:
    client = RecordingClient(FakeResponse(successful_body()))
    adapter = AliyunImageAdapter(config(), client=client)

    result = adapter.generate_story_cover(**cover_kwargs())

    assert result.status == "ready"
    assert result.cover_payload(story_version_id="liuyi-v1") == {
        "storyVersionId": "liuyi-v1",
        "status": "ready",
        "imageUrl": "https://example-oss.aliyuncs.com/scene.png?Expires=1",
        "altText": "《柳毅传书》的白描题图",
        "message": None,
        "retryable": True,
        "generationSource": "aliyun_image_model",
        "fallbackReason": None,
    }
    prompt = client.calls[0]["json"]["input"]["messages"][0]["content"][0]["text"]
    assert "纯水墨白描" in prompt
    assert "不设色" in prompt
    assert "画面不出现任何文字" in prompt
    assert "z-image" not in str(result.cover_payload(story_version_id="liuyi-v1"))


def test_story_cover_retries_once_with_safe_symbolic_prompt_after_rejection() -> None:
    class SequenceClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.responses = [
                FakeResponse({"code": "DataInspectionFailed"}, status_code=400),
                FakeResponse(successful_body()),
            ]

        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            self.calls.append({"url": url, **kwargs})
            return self.responses.pop(0)

    client = SequenceClient()
    adapter = AliyunImageAdapter(config(), client=client)

    result = adapter.generate_story_cover(**cover_kwargs())

    assert result.status == "ready"
    assert len(client.calls) == 2
    first_prompt = client.calls[0]["json"]["input"]["messages"][0]["content"][0]["text"]
    retry_prompt = client.calls[1]["json"]["input"]["messages"][0]["content"][0]["text"]
    assert "柳毅为龙女传书" in first_prompt
    assert "柳毅为龙女传书" not in retry_prompt
    assert "不画人物冲突、伤害、死亡、惊悚场景" in retry_prompt
    assert "《柳毅传书》" in retry_prompt


def test_wan_uses_one_watermark_free_horizontal_image_and_negative_prompt() -> None:
    client = RecordingClient(FakeResponse(successful_body()))
    adapter = AliyunImageAdapter(config(model="wan2.6-t2i"), client=client)

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "ready"
    parameters = client.calls[0]["json"]["parameters"]
    assert parameters["size"] == "1696*960"
    assert parameters["n"] == 1
    assert parameters["watermark"] is False
    assert parameters["prompt_extend"] is False
    assert "文字" in parameters["negative_prompt"]


def test_provider_error_and_malformed_or_insecure_url_are_safe_fallbacks() -> None:
    failure = RecordingClient(httpx.TimeoutException("secret upstream detail"))
    failed_result = AliyunImageAdapter(config(), client=failure).generate_scene(**scene_kwargs())
    assert failed_result.status == "fallback"
    assert failed_result.message == "本幕画面暂未生成，可继续观看或重试。"
    assert "secret upstream detail" not in str(failed_result)

    malformed = RecordingClient(
        FakeResponse(
            {
                "output": {
                    "choices": [
                        {"message": {"content": [{"image": "http://unsafe.example/scene.png"}]}}
                    ]
                }
            }
        )
    )
    malformed_result = AliyunImageAdapter(config(), client=malformed).generate_scene(**scene_kwargs())
    assert malformed_result.status == "fallback"
    assert malformed_result.failure_reason == "invalid_response"


def test_unknown_model_never_reaches_provider() -> None:
    client = RecordingClient(FakeResponse(successful_body()))
    adapter = AliyunImageAdapter(config(model="unknown-image-model"), client=client)

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "fallback"
    assert result.failure_reason == "unsupported_model"
    assert client.calls == []


def test_transport_failure_retries_only_the_same_region_public_endpoint() -> None:
    class SequenceClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            self.calls.append({"url": url, **kwargs})
            if len(self.calls) == 1:
                raise httpx.ConnectError("workspace TLS unavailable")
            return FakeResponse(successful_body())

    client = SequenceClient()
    adapter = AliyunImageAdapter(
        config(fallback_api_host="https://dashscope.aliyuncs.com"),
        client=client,
    )

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "ready"
    assert len(client.calls) == 2
    assert client.calls[0]["url"].startswith(
        "https://llm-testworkspace.cn-beijing.maas.aliyuncs.com/"
    )
    assert client.calls[1]["url"].startswith("https://dashscope.aliyuncs.com/")


def test_cross_region_fallback_is_never_called() -> None:
    client = RecordingClient(httpx.ConnectError("primary unavailable"))
    adapter = AliyunImageAdapter(
        config(fallback_api_host="https://dashscope-intl.aliyuncs.com"),
        client=client,
    )

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "fallback"
    assert result.failure_reason == "transport_unreachable"
    assert len(client.calls) == 1


def test_proxy_is_server_only_and_masked_health_status_contains_no_endpoint() -> None:
    client = RecordingClient(FakeResponse(successful_body()))
    image_config = config(proxy_url="http://proxy-user:proxy-pass@example.invalid:8080")
    adapter = AliyunImageAdapter(image_config, client=client)

    result = adapter.generate_scene(**scene_kwargs())
    status = image_config.public_status()

    assert result.status == "ready"
    assert client.calls[0]["proxy"] == "http://proxy-user:proxy-pass@example.invalid:8080"
    assert status == {
        "configured": True,
        "enabled": True,
        "available": True,
        "model": "z-image-turbo",
        "region": "cn-beijing",
        "endpointType": "workspace",
        "sameRegionFallbackConfigured": False,
        "proxyConfigured": True,
        "ipv6FallbackEnabled": False,
    }
    assert "proxy-pass" not in repr(image_config)
    assert "test-only-key" not in repr(image_config)
    assert "llm-testworkspace.cn-beijing.maas.aliyuncs.com" not in str(status)


def test_auth_rejection_is_a_retryable_public_fallback_without_provider_details() -> None:
    client = RecordingClient(FakeResponse({"code": "InvalidApiKey"}, status_code=401))
    adapter = AliyunImageAdapter(
        config(fallback_api_host="https://dashscope.aliyuncs.com"),
        client=client,
    )

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "fallback"
    assert result.failure_reason == "provider_auth"
    assert result.retryable is True
    assert len(client.calls) == 1
    assert "InvalidApiKey" not in str(result.public_payload(act_id="act-1"))


def test_ipv6_fallback_uses_only_public_aaaa_with_original_host_and_sni(
    caplog: Any,
) -> None:
    public_address = "2606:4700:4700::1111"

    class DnsResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "Status": 0,
                "Answer": [
                    {"type": 28, "data": "fd00::1"},
                    {"type": 1, "data": "203.0.113.9"},
                    {"type": 28, "data": public_address},
                ],
            }

    class DnsClient:
        calls: list[dict[str, Any]] = []

        def get(self, url: str, **kwargs: Any) -> DnsResponse:
            self.calls.append({"url": url, **kwargs})
            return DnsResponse()

    class DirectClient:
        requests: list[httpx.Request] = []

        def send(self, request: httpx.Request) -> FakeResponse:
            self.requests.append(request)
            return FakeResponse(successful_body())

    normal_client = RecordingClient(httpx.ConnectError("fake-IP TLS failure"))
    dns_client = DnsClient()
    direct_client = DirectClient()
    adapter = AliyunImageAdapter(
        config(ipv6_fallback_enabled=True),
        client=normal_client,
        doh_client=dns_client,
        direct_ipv6_client=direct_client,
    )

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "ready"
    assert len(dns_client.calls) == 1
    assert dns_client.calls[0]["url"] == "https://dns.alidns.com/resolve"
    assert dns_client.calls[0]["params"] == {
        "name": "llm-testworkspace.cn-beijing.maas.aliyuncs.com",
        "type": "AAAA",
    }
    assert dns_client.calls[0]["trust_env"] is False
    assert len(direct_client.requests) == 1
    request = direct_client.requests[0]
    assert request.url.host == public_address
    assert request.headers["host"] == "llm-testworkspace.cn-beijing.maas.aliyuncs.com"
    assert request.extensions["sni_hostname"] == "llm-testworkspace.cn-beijing.maas.aliyuncs.com"
    assert "test-only-key" not in caplog.text
    assert public_address not in caplog.text
    assert scene_kwargs()["narration"] not in caplog.text


def test_ipv6_fallback_rejects_non_allowlisted_endpoint_before_doh() -> None:
    class DnsClient:
        calls = 0

        def get(self, *_: object, **__: object) -> FakeResponse:
            self.calls += 1
            raise AssertionError("DoH must not be called for a custom host")

    image_config = AliyunImageConfig(
        api_host="https://images.example.invalid",
        model="z-image-turbo",
        api_key="test-only-key",
        timeout_seconds=12,
        live_enabled=True,
        ipv6_fallback_enabled=True,
    )
    normal_client = RecordingClient(httpx.ConnectError("custom host unavailable"))
    dns_client = DnsClient()
    adapter = AliyunImageAdapter(
        image_config,
        client=normal_client,
        doh_client=dns_client,
    )

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "fallback"
    assert result.failure_reason == "transport_unreachable"
    assert dns_client.calls == 0


def test_ipv6_fallback_never_runs_after_provider_http_rejection() -> None:
    class DnsClient:
        calls = 0

        def get(self, *_: object, **__: object) -> FakeResponse:
            self.calls += 1
            raise AssertionError("DoH must run only after a transport error")

    normal_client = RecordingClient(FakeResponse({"code": "InvalidApiKey"}, status_code=401))
    dns_client = DnsClient()
    adapter = AliyunImageAdapter(
        config(ipv6_fallback_enabled=True),
        client=normal_client,
        doh_client=dns_client,
    )

    result = adapter.generate_scene(**scene_kwargs())

    assert result.status == "fallback"
    assert result.failure_reason == "provider_auth"
    assert dns_client.calls == 0
