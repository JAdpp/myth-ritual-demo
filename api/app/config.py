"""Runtime configuration and an opt-in DeepSeek adapter.

Credentials are read only from the server process environment.  Live calls
require a separate feature flag and otherwise fail closed to the reviewed
deterministic suggestions in the service layer.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)


_GENERIC_RETRIEVAL_TERMS = frozenset(
    {
        "事情",
        "故事",
        "生活",
        "最近",
        "工作",
        "关系",
        "变化",
        "改变",
        "选择",
        "决定",
        "行动",
        "责任",
        "自己",
        "希望",
        "困难",
        "压力",
    }
)
_BANNED_RECOMMENDATION_FILLER = (
    "来源片段含",
    "来源片段中有",
    "相关性弱",
    "可作为比较材料",
    "提供了可比较的线索",
)


def _specific_retrieval_term(value: object) -> str | None:
    term = re.sub(r"\s+", "", str(value or "")).strip("，。！？；：、,.!?;:()（）")
    if not 3 <= len(term) <= 12 or term in _GENERIC_RETRIEVAL_TERMS:
        return None
    remainder = term
    for generic in sorted(_GENERIC_RETRIEVAL_TERMS, key=len, reverse=True):
        remainder = remainder.replace(generic, "")
    return term if remainder else None


def _clean_generated_chinese(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    while "《《" in text or "》》" in text:
        text = text.replace("《《", "《").replace("》》", "》")
    text = re.sub(r"([。！？；，、])\1+", r"\1", text)
    return text


def _is_structured_mapping_value(value: str) -> bool:
    return (
        24 <= len(value) <= 800
        and all(label in value for label in ("现实线索：", "原典线索：", "映照差异："))
        and not any(
            filler in value
            for filler in (
                "可把现实中的人",
                "这仍由用户修改确认",
                "选择下一步能够承受的行动",
            )
        )
    )


class ModelUnavailable(RuntimeError):
    """Raised when live model generation is not enabled for this build."""


@dataclass(frozen=True)
class DeepSeekConfig:
    model: str
    base_url: str
    api_key: str | None
    timeout_seconds: float
    live_enabled: bool

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        return cls(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
            live_enabled=os.getenv("ENABLE_LIVE_MODEL_GENERATION", "false").lower()
            in {"1", "true", "yes", "on"},
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def available(self) -> bool:
        return self.configured and self.live_enabled


class DeepSeekAdapter:
    """Opt-in DeepSeek gateway for bounded narrative assistance.

    ``suggest`` never receives user prose. ``chat`` may receive a short user
    message only after the service has verified explicit cloud consent and run
    the safety router. Both calls fail closed to deterministic local behavior.
    """

    def __init__(
        self,
        config: DeepSeekConfig | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self.config = config or DeepSeekConfig.from_env()
        self._client = client

    def _complete_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        if not self.config.available:
            raise ModelUnavailable("Live DeepSeek generation is not explicitly enabled")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        client = self._client or httpx
        try:
            response = client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            if not isinstance(parsed, dict):
                raise ValueError("structured response must be an object")
            return parsed
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelUnavailable("DeepSeek structured generation failed safely") from exc

    def retrieval_plan(self, *, user_summary: str) -> dict[str, list[str]]:
        summary = user_summary.strip()[:500]
        if not summary:
            raise ModelUnavailable("A confirmed summary is required")
        parsed = self._complete_json(
            system_prompt=(
                "你是中国古典叙事检索规划器。只把用户已确认的生活摘要转换为检索主题，"
                "不得诊断人格、预测结局或补写用户经历。检索词必须是3至12个汉字、适合在繁体古文全文中检索的具体短语，"
                "必须提取摘要中已经出现的具体事件、关系动作、冲突或物件；"
                "工作、关系、变化、选择、决定、行动、责任等宽泛标签不得单独作为检索词，也不得为了凑数臆造细节。"
                "返回2至8个非空检索词；摘要没有足够具体信息时可以返回空数组。"
                "每个核心概念尽量同时给出简体和繁体等价词，作为terms中的两个独立字符串，"
                "以便跨字形召回；主题标签只用简体中文且最多4个。"
                "例如摘要涉及进入新环境和关系边界时，可返回："
                "{\"terms\":[\"离家远行\",\"離家遠行\",\"关系边界\",\"關係邊界\"],"
                "\"themes\":[\"新环境\",\"关系边界\"]}。只返回同结构JSON。"
            ),
            user_payload={"confirmedSummary": summary},
            temperature=0.1,
        )
        terms: list[str] = []
        for value in parsed.get("terms", []):
            term = _specific_retrieval_term(value)
            if term and term not in terms:
                terms.append(term)
            if len(terms) >= 8:
                break
        themes: list[str] = []
        for value in parsed.get("themes", []):
            theme = str(value).strip()
            if 2 <= len(theme) <= 20 and theme not in themes:
                themes.append(theme)
            if len(themes) >= 4:
                break
        if not terms:
            raise ModelUnavailable("DeepSeek returned no usable retrieval terms")
        return {"terms": terms, "themes": themes}

    def rerank_candidates(
        self,
        *,
        user_summary: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not 1 <= len(candidates) <= 12:
            raise ModelUnavailable("Reranking requires between one and twelve candidates")
        bounded: list[dict[str, str]] = []
        allowed_ids: set[str] = set()
        for item in candidates:
            story_id = str(item.get("storyVersionId") or "").strip()
            if not story_id or story_id in allowed_ids:
                continue
            allowed_ids.add(story_id)
            bounded.append(
                {
                    "storyVersionId": story_id,
                    "title": str(item.get("title") or "")[:120],
                    "sourceTitle": str(item.get("sourceTitle") or "")[:120],
                    "locator": str(item.get("locator") or "")[:160],
                    "excerpt": str(item.get("excerpt") or "")[:500],
                    "storySummary": str(item.get("storySummary") or "")[:360],
                    "conflict": str(item.get("conflict") or "")[:240],
                }
            )
        parsed = self._complete_json(
            system_prompt=(
                "你是可解释的中国古典神话传说推荐重排器。只能依据用户确认摘要和给定的有出处候选，"
                "不得引入候选之外的故事事实，不得诊断、说教或声称人格匹配。按相关性返回候选ID、0到1分数、"
                "每个输入候选必须恰好返回一次；只有宽泛主题相似时分数必须低于0.55。"
                "每条理由必须依次包含‘用户线索：’‘原典情节：’‘关键差异：’三部分，"
                "分别引用用户摘要中的具体内容、候选中给出的具体情节，并说明两者不可等同之处；"
                "不得使用‘来源片段含相关线索’‘可作为比较材料’等填充话。"
                "理由不超过160字，故事线索不超过20字；理由和故事线索只用简体中文，"
                "引用的原始繁体证据不得改写。返回JSON："
                "{\"rankings\":[{\"storyVersionId\":\"...\",\"score\":0.8,"
                "\"reason\":\"...\",\"storySignal\":\"...\"}]}。"
            ),
            user_payload={
                "confirmedSummary": user_summary.strip()[:500],
                "candidates": bounded,
            },
            temperature=0.15,
        )
        rankings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in parsed.get("rankings", []):
            if not isinstance(item, dict):
                continue
            story_id = str(item.get("storyVersionId") or "").strip()
            reason = _clean_generated_chinese(item.get("reason"))
            signal = _clean_generated_chinese(item.get("storySignal"))
            try:
                score = float(item.get("score"))
            except (TypeError, ValueError):
                continue
            if (
                story_id not in allowed_ids
                or story_id in seen
                or not 0 <= score <= 1
                or not reason
                or len(reason) > 160
                or not signal
                or len(signal) > 30
                or not all(
                    label in reason
                    for label in ("用户线索：", "原典情节：", "关键差异：")
                )
                or any(filler in reason for filler in _BANNED_RECOMMENDATION_FILLER)
            ):
                continue
            seen.add(story_id)
            rankings.append(
                {
                    "storyVersionId": story_id,
                    "score": score,
                    "reason": reason,
                    "storySignal": signal,
                }
            )
        if seen != allowed_ids:
            raise ModelUnavailable("DeepSeek returned an incomplete candidate ranking")
        return rankings

    def generate_mapping_draft(
        self,
        *,
        user_summary: str,
        source_canon: dict[str, Any],
    ) -> dict[str, Any]:
        source_payload = {
            "title": str(source_canon.get("title") or "")[:120],
            "sourceTitle": str(
                source_canon.get("work")
                or source_canon.get("sourceTitle")
                or ""
            )[:120],
            "summary": str(source_canon.get("summary") or "")[:500],
            "excerpt": str(
                source_canon.get("excerpt")
                or source_canon.get("originalExcerpt")
                or source_canon.get("original_excerpt")
                or ""
            )[:800],
            "originalEnding": str(
                source_canon.get("originalEnding")
                or source_canon.get("original_ending")
                or ""
            )[:600],
            "motifs": [str(value)[:80] for value in source_canon.get("motifs", [])[:8]],
            "characters": [
                str(value)[:80] for value in source_canon.get("characters", [])[:8]
            ],
            "conflict": str(source_canon.get("conflict") or "")[:360],
        }
        node_ids = [
            "world_crack",
            "cross_threshold",
            "allies_resources",
            "new_understanding",
            "bring_back",
        ]
        parsed = self._complete_json(
            system_prompt=(
                "你是叙事映照画布助手。根据用户确认摘要与固定来源故事，生成完整但可编辑的五节点映射初稿。"
                "必须区分原典与用户处境，不得把推测写成用户事实，不得诊断或给治疗建议。"
                "所有value和hopeAnchor只用简体中文。每个value必须由三段组成："
                "‘现实线索：’引用摘要中已确认的具体事件；‘原典线索：’写明给定故事的具体人物与行动；"
                "‘映照差异：’说明两者在处境、行动或结局上的关键不同。不得整段复制摘要，不得写通用模板。"
                "每个value为1至3句、具体且中性，不得出现重复句号或双重书名号。"
                "严格使用给定五个nodeId各一次。返回JSON："
                "{\"nodes\":[{\"nodeId\":\"...\",\"value\":\"...\"}],"
                "\"hopeAnchor\":{\"type\":\"action|relationship|meaning|open\",\"detail\":\"...\"}}。"
            ),
            user_payload={
                "confirmedSummary": user_summary.strip()[:500],
                "sourceCanon": source_payload,
                "nodeIds": node_ids,
            },
            temperature=0.25,
        )
        by_id: dict[str, str] = {}
        for item in parsed.get("nodes", []):
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("nodeId") or "")
            value = _clean_generated_chinese(item.get("value"))
            if (
                node_id in node_ids
                and node_id not in by_id
                and _is_structured_mapping_value(value)
            ):
                by_id[node_id] = value
        if set(by_id) != set(node_ids):
            raise ModelUnavailable("DeepSeek returned an incomplete mapping draft")
        raw_anchor = parsed.get("hopeAnchor")
        if not isinstance(raw_anchor, dict):
            raise ModelUnavailable("DeepSeek returned no hope anchor")
        anchor_type = str(raw_anchor.get("type") or "")
        anchor_detail = str(
            raw_anchor.get("detail") or raw_anchor.get("text") or ""
        ).strip()
        if anchor_type not in {"action", "relationship", "meaning", "open"} or not (
            1 <= len(anchor_detail) <= 500
        ):
            raise ModelUnavailable("DeepSeek returned an invalid hope anchor")
        return {
            "nodes": [
                {"nodeId": node_id, "value": by_id[node_id]} for node_id in node_ids
            ],
            "hopeAnchor": {"type": anchor_type, "detail": anchor_detail},
        }

    def revise_mapping(
        self,
        *,
        user_message: str,
        user_summary: str,
        source_canon: dict[str, Any],
        nodes: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        message = user_message.strip()
        if not message or len(message) > 500:
            raise ModelUnavailable("A short mapping instruction is required")
        allowed_ids = {
            "world_crack",
            "cross_threshold",
            "allies_resources",
            "new_understanding",
            "bring_back",
        }
        parsed = self._complete_json(
            system_prompt=(
                "你是映照画布编辑助手。根据用户的自然语言修改要求，只返回确实需要变动的节点。"
                "先识别用户点名的节点；不得擅自改动其他节点。value必须是可以直接替换该节点的具体预览，"
                "并继续区分现实线索、原典线索与映照差异；rationale要复述这次明确修改了什么。"
                "保留用户原意，不得诊断或替用户做决定；value和rationale只用简体中文。返回JSON："
                "{\"nodeUpdates\":[{\"nodeId\":\"...\",\"value\":\"...\",\"rationale\":\"...\"}]}。"
            ),
            user_payload={
                "instruction": message,
                "confirmedSummary": user_summary.strip()[:500],
                "sourceCanon": {
                    "sourceTitle": str(
                        source_canon.get("work") or source_canon.get("sourceTitle") or ""
                    )[:120],
                    "summary": str(source_canon.get("summary") or "")[:500],
                    "excerpt": str(
                        source_canon.get("excerpt")
                        or source_canon.get("originalExcerpt")
                        or source_canon.get("original_excerpt")
                        or ""
                    )[:800],
                    "originalEnding": str(
                        source_canon.get("originalEnding")
                        or source_canon.get("original_ending")
                        or ""
                    )[:400],
                },
                "nodes": [
                    {
                        "nodeId": str(item.get("id") or ""),
                        "value": str(item.get("value") or "")[:800],
                    }
                    for item in nodes[:7]
                ],
            },
            temperature=0.2,
        )
        updates: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in parsed.get("nodeUpdates", []):
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("nodeId") or "")
            value = _clean_generated_chinese(item.get("value"))
            rationale = _clean_generated_chinese(item.get("rationale"))
            if (
                node_id in allowed_ids
                and node_id not in seen
                and _is_structured_mapping_value(value)
                and 4 <= len(rationale) <= 160
            ):
                seen.add(node_id)
                updates.append(
                    {"nodeId": node_id, "value": value, "rationale": rationale}
                )
        if not updates:
            raise ModelUnavailable("DeepSeek returned no valid mapping updates")
        return updates

    def suggest(
        self,
        *,
        node_number: int | None = None,
        node_title: str = "",
        node_prompt: str = "",
        story_context: dict[str, Any] | None = None,
        user_summary: str = "",
        **_: object,
    ) -> list[str]:
        if not self.config.available:
            raise ModelUnavailable("Live DeepSeek generation is not explicitly enabled")
        if node_number not in {1, 2, 3, 4, 5}:
            raise ModelUnavailable("A valid story node number is required")

        # Without the selected story and the user's own summary the model can
        # only produce generic filler, which is why suggestions used to have
        # nothing to do with the story on screen.
        story = story_context or {}
        context_lines = [
            f"古典故事：《{story.get('title', '')}》" if story.get("title") else "",
            f"原典情节：{story.get('summary', '')}" if story.get("summary") else "",
            f"核心冲突：{story.get('conflict', '')}" if story.get("conflict") else "",
            f"主要角色：{story.get('characters', '')}" if story.get("characters") else "",
            f"母题：{story.get('motifs', '')}" if story.get("motifs") else "",
            f"原典结局：{story.get('originalEnding', '')}" if story.get("originalEnding") else "",
            f"用户经历摘要：{user_summary}" if user_summary else "",
        ]
        context = "\n".join(line for line in context_lines if line)

        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是中文叙事共谱助手。用户正在把一则中国古典故事的结构借来，"
                        "书写自己经历的现代支线。"
                        "请直接输出具体、完整的叙事段落，所有输出只用简体中文。"
                        "两条建议必须同时扣住两件事：用户经历摘要里的真实处境，"
                        "以及所给古典故事的角色、冲突或母题——把古典意象化用到现代场景中，"
                        "不要直接复述原典情节，也不得改写原典本身。"
                        "写现代生活场景，不要出现神仙、法术或古代官职。"
                        "不要使用“让主角……”或“把……写成……”这样的指令句。"
                        "必须包含具体的动作、情绪和情境。不得诊断用户或给出治疗建议，"
                        "不得断言用户必须成长、原谅或成功。"
                        "两条建议要给出不同的走向，不能只是换词。"
                        "返回 JSON：{\"suggestions\":[\"...\",\"...\"]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"{context}\n\n"
                        f"当前是五节点中的第 {node_number} 节"
                        f"{f'：{node_title}' if node_title else ''}。"
                        f"{f'这一节要回答：{node_prompt}' if node_prompt else ''}\n"
                        "请提供两条中性、含蓄的叙事段落（每段 1-2 句话），"
                        "直接写出现代支线的故事内容，不要写成写作提示。"
                    ),
                },
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        client = self._client or httpx
        try:
            response = client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            values = parsed.get("suggestions", []) if isinstance(parsed, dict) else []
            suggestions = [str(value).strip() for value in values if str(value).strip()]
            if len(suggestions) != 2 or any(len(value) > 120 for value in suggestions):
                raise ValueError("suggestions must contain exactly two short strings")
            return suggestions
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelUnavailable("DeepSeek suggestion generation failed safely") from exc

    def _conversation_completion(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if not self.config.available:
            raise ModelUnavailable("Live DeepSeek generation is not explicitly enabled")
        message = user_message.strip()
        if not message or len(message) > 500:
            raise ModelUnavailable("A short user message is required")

        bounded_history = []
        for item in (history or [])[-6:]:
            role = item.get("role")
            text = str(item.get("text") or "").strip()
            if role in {"assistant", "user"} and text:
                bounded_history.append({"role": role, "content": text[:500]})

        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是中国古典神话传说体验中的半结构化叙事助手，可以自称栖蝶。"
                        "用户会先分享一件自己的具体经历；你只沿用户刚讲的这件事追问。"
                        "所有回复只用简体中文，并采用清楚的两拍式回应。"
                        "第一拍 acknowledgement 要先共情、再承接，让用户觉得被听见，"
                        "而不是把原话换个说法复述一遍或干巴巴地总结信息。"
                        "如果用户同时说了事件和自己最在意的顾虑、矛盾或边界，必须点出后者，"
                        "不能只截取句子开头的事件；例如‘主动接下任务，却担心责任全落自己身上’，"
                        "应承接‘想帮忙与怕责任失去边界同时存在’，而不是只复述‘接下任务’。"
                        "先用一句体贴的话回应这件事里对用户不容易、费力或要紧的地方，"
                        "再落到用户最新提到的具体事件、行动、关系或变化上。"
                        "共情要有分寸：只回应用户已经说出或明显流露的处境与心情，"
                        "用“听起来”“这一段”“像是”这类留有余地的说法，不把情绪断言成事实；"
                        "语气温和自然，像朋友而不像客服，不要用“感谢分享”“我完全理解你”这类套话。"
                        "不得诊断、贴标签、评判对错、给建议、灌鸡汤，"
                        "不得夸奖用户，不得臆测用户没有说出的原因与动机。"
                        "它不得包含问号。"
                        "第二拍 followUpQuestion 只问一个具体、开放、可跳过的问题，"
                        "自然延续最新细节；优先追问用户尚未展开的顾虑、情绪、责任边界或关系张力，"
                        "不要泛问‘接下来发生了什么’。不得连续抛出多个问题，也不得像问卷一样切换主题。"
                        "同时给出2至3条可点击的下一步回答方向；每条都必须复用用户最新消息中的"
                        "具体行动、关系、地点或变化线索，不得返回固定主题菜单，不得替用户补造事实。"
                        "每条都写成用户能直接说出口的陈述句，而不是问句，"
                        "整条不得出现问号，长度在6到40个汉字之间。"
                        "不要诊断、治疗、说教、预测或替用户选择故事；不要声称你理解其人格。"
                        "此阶段不要推荐、暗示或硬编码任何中国古典神话传说。"
                        "不要索取姓名、联系方式、单位或其他身份信息。"
                        "acknowledgement 不超过70个汉字，followUpQuestion 不超过40个汉字，"
                        "每条回答方向不超过60个汉字。返回 JSON："
                        "{\"acknowledgement\":\"...\",\"followUpQuestion\":\"...？\","
                        "\"followUpOptions\":[\"...\",\"...\"]}。"
                    ),
                },
                *bounded_history,
                {"role": "user", "content": message},
            ],
            "temperature": 0.35,
            "response_format": {"type": "json_object"},
        }
        client = self._client or httpx
        try:
            # DeepSeek occasionally returns an empty or truncated completion
            # despite response_format=json_object. That is a provider hiccup, not
            # a reason to hand the user the flat local wording, so try once more.
            parsed: Any = None
            for attempt in range(2):
                response = client.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                try:
                    parsed = json.loads(content) if isinstance(content, str) else content
                except json.JSONDecodeError:
                    if attempt == 0:
                        logger.info("conversation completion returned unparsable JSON; retrying")
                        continue
                    raise
                break
            if not isinstance(parsed, dict):
                raise ValueError("conversation turn must be a JSON object")

            acknowledgement = re.sub(
                r"\s+", " ", str(parsed.get("acknowledgement") or "")
            ).strip()
            follow_up_question = re.sub(
                r"\s+",
                " ",
                str(parsed.get("followUpQuestion") or parsed.get("follow_up_question") or ""),
            ).strip()
            legacy_reply = re.sub(r"\s+", " ", str(parsed.get("reply") or "")).strip()
            if not acknowledgement or not follow_up_question:
                question_match = re.search(r"([^。！？?；;：:\n]+[？?])$", legacy_reply)
                if not legacy_reply or not question_match:
                    raise ValueError("two-beat conversation fields are required")
                follow_up_question = question_match.group(1).strip()
                acknowledgement = legacy_reply[: question_match.start()].rstrip("；;：:，, ")
                if acknowledgement and acknowledgement[-1] not in {"。", "！", "!"}:
                    acknowledgement += "。"

            if (
                not 1 <= len(acknowledgement) <= 120
                or "？" in acknowledgement
                or "?" in acknowledgement
            ):
                raise ValueError("acknowledgement must be one short non-question sentence")
            question_mark_count = follow_up_question.count("？") + follow_up_question.count("?")
            if (
                not 2 <= len(follow_up_question) <= 80
                or question_mark_count != 1
                or follow_up_question[-1] not in {"？", "?"}
            ):
                raise ValueError("followUpQuestion must contain exactly one short question")
            reply = f"{acknowledgement}\n\n{follow_up_question}"
            if len(reply) > 260:
                raise ValueError("reply must be one short non-empty string")
            raw_options = parsed.get("followUpOptions", []) if isinstance(parsed, dict) else []
            options: list[str] = []
            if isinstance(raw_options, list):
                for value in raw_options:
                    option = str(value).strip()
                    if (
                        4 <= len(option) <= 100
                        and "？" not in option
                        and "?" not in option
                        and option not in options
                    ):
                        options.append(option)
            # A short chip count is not a failure. The two beats are the reply;
            # the caller substitutes locally-built chips when these fall short,
            # rather than throwing away a good answer over its garnish.
            return {
                "reply": reply,
                "acknowledgement": acknowledgement,
                "followUpQuestion": follow_up_question,
                "followUpOptions": options[:3],
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # The constraint names below are ours, not the user's words, so this
            # is safe to log -- and without it every rejection looks identical.
            logger.info(
                "conversation completion rejected type=%s detail=%s",
                type(exc).__name__,
                str(exc)[:200],
            )
            raise ModelUnavailable("DeepSeek conversation generation failed safely") from exc

    def conversation_turn(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return self._conversation_completion(
            user_message=user_message,
            history=history,
        )

    def compose_theatre(
        self,
        *,
        story_context: dict[str, Any] | None = None,
        user_summary: str = "",
        nodes: list[dict[str, Any]] | None = None,
        hope_text: str = "",
    ) -> list[dict[str, str]]:
        """Weave the classical story and the user's branch into one new story.

        The deterministic assembly copies each node verbatim and narrates the
        canon/branch split, which reads as a comparison rather than a story.
        Here the model is asked to fold the classical characters, imagery and
        conflict into the user's modern situation as a single continuous piece.
        """

        if not self.config.available:
            raise ModelUnavailable("Live DeepSeek generation is not explicitly enabled")

        story = story_context or {}
        beats = [
            f"{index}. {str(node.get('title') or '').strip()}：{str(node.get('value') or '').strip()}"
            for index, node in enumerate(nodes or [], start=1)
            if str(node.get("value") or "").strip()
        ]
        if not beats:
            raise ModelUnavailable("Approved branch content is required")

        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你要把一则中国古典故事，与用户写下的现代经历，"
                        "熔铸成一个连续的新故事，用于分幕演出。"
                        "只用简体中文。"
                        "核心要求一：古典故事中的角色必须作为真正的角色登场，"
                        "在现代场景里与主角同处一个空间、有对白或动作上的往来。"
                        "登场方式可任选其一并贯穿全剧：其一，该角色本人跨时空来到现代，"
                        "带着原典中的身份、器物与执念，与现代环境格格不入却真实可见；"
                        "其二，现代生活中出现一个与之形神相似的人物"
                        "（同事、路人、亲属、店主等），其处境、选择与姿态呼应原典角色，"
                        "但不必点破身份。"
                        "严禁只让主角在心里想起、随口提到或被旁白点名该角色——"
                        "那样等于没有出场。请给该角色具体的动作、位置与至少一次互动。"
                        "核心要求二：把古典故事的意象、冲突与母题织进现代处境，"
                        "写成有场景、有动作、有细节的叙事，人物在现代生活中行动。"
                        "严禁写成对照、比较、解读或说明，"
                        "不得出现“原典”“对应”“象征着”“正如”“就像”“这说明”等分析性说法；"
                        "不得分别复述古典故事再复述用户经历。"
                        "必须忠于用户已确认的事实，不得替用户补造新的事实、关系或结局；"
                        "不得断言用户必须成长、原谅、胜利或释怀。"
                        "不改写古典故事的原结局；该角色可以带着原典的结局来到现代，"
                        "但不得在现代情节里推翻原典已经发生的事。"
                        "输出 5 幕。每幕 title 不超过 14 字，"
                        "narration 为 2-3 句、40 到 110 字的叙事，"
                        "stageDirection 为一句画面调度，不超过 40 字。"
                        "返回 JSON：{\"acts\":[{\"title\":\"...\",\"narration\":\"...\","
                        "\"stageDirection\":\"...\"}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"古典故事：《{story.get('title', '')}》\n"
                        f"原典情节：{story.get('summary', '')}\n"
                        f"主要角色：{story.get('characters', '')}\n"
                        f"核心冲突：{story.get('conflict', '')}\n"
                        f"母题：{story.get('motifs', '')}\n\n"
                        f"用户经历摘要：{user_summary}\n\n"
                        "用户写下的现代支线节点：\n" + "\n".join(beats)
                        + (f"\n\n用户选定的希望支点：{hope_text}" if hope_text else "")
                    ),
                },
            ],
            "temperature": 0.6,
            "response_format": {"type": "json_object"},
        }
        client = self._client or httpx
        try:
            response = client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            raw_acts = parsed.get("acts") if isinstance(parsed, dict) else None
            if not isinstance(raw_acts, list):
                raise ValueError("acts must be a list")

            acts: list[dict[str, str]] = []
            for item in raw_acts:
                if not isinstance(item, dict):
                    continue
                title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
                narration = re.sub(r"\s+", " ", str(item.get("narration") or "")).strip()
                direction = re.sub(r"\s+", " ", str(item.get("stageDirection") or "")).strip()
                if not title or not narration:
                    continue
                if len(title) > 24 or not 20 <= len(narration) <= 200:
                    raise ValueError("act fields are out of bounds")
                acts.append(
                    {
                        "title": title,
                        "narration": narration,
                        "stageDirection": direction or "镜头随人物动作推进，保留留白。",
                    }
                )
            # Must land inside the 4..7 act contract.
            if not 4 <= len(acts) <= 7:
                raise ValueError("theatre must contain four to seven acts")
            return acts
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelUnavailable("DeepSeek theatre composition failed safely") from exc

    def summarize_experience(
        self,
        *,
        history: list[dict[str, str]],
    ) -> str:
        """Summarise the finished 栖蝶 conversation into one neutral paragraph.

        Called once, after guidance ends -- not per turn.  The previous
        behaviour merely concatenated the user's own sentences, which is why
        the editable summary read as a transcript rather than a summary.
        """

        user_texts = [
            str(item.get("text") or "").strip()[:500]
            for item in history
            if item.get("role") == "user" and str(item.get("text") or "").strip()
        ]
        if not user_texts:
            raise ModelUnavailable("A conversation is required before summarising")

        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你要把用户刚讲述的一段个人经历整理成一段中立摘要，供后续检索古典故事使用。"
                        "只用简体中文，只写一段连续文字，不分点、不加标题、不使用引号。"
                        "只概括用户明确说过的事实：发生了什么、涉及哪些关系、用户在意什么、"
                        "目前处在什么状态。"
                        "不得诊断、不得评价、不得安慰、不得给建议、不得预测结果，"
                        "不得臆测用户没有说出的情绪或原因，不得补造任何细节。"
                        "不要出现姓名、联系方式、单位等身份信息；若用户提到，请改写为泛称。"
                        "不得提及任何神话、传说或故事。"
                        "全文不超过140个汉字。返回 JSON：{\"summary\":\"...\"}。"
                    ),
                },
                {"role": "user", "content": "\n".join(f"- {text}" for text in user_texts)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        client = self._client or httpx
        try:
            response = client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            if not isinstance(parsed, dict):
                raise ValueError("summary must be a JSON object")
            summary = re.sub(r"\s+", " ", str(parsed.get("summary") or "")).strip()
            if not 10 <= len(summary) <= 240:
                raise ValueError("summary must be a single short paragraph")
            return summary
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelUnavailable("DeepSeek summary generation failed safely") from exc

    def chat(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Backward-compatible reply-only adapter entrypoint."""

        return str(
            self._conversation_completion(
                user_message=user_message,
                history=history,
            )["reply"]
        )

    def __repr__(self) -> str:
        return (
            f"DeepSeekAdapter(model={self.config.model!r}, "
            f"configured={self.config.configured!r}, "
            f"live_enabled={self.config.live_enabled!r})"
        )
