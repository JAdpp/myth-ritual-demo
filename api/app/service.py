"""In-memory application service for the first vertical product slice."""

from __future__ import annotations

import copy
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from .aliyun_image import AliyunImageAdapter, SceneImageResult
from .aliyun_tts import AliyunTtsAdapter, NarrationResult
from .config import DeepSeekAdapter, ModelUnavailable
from .corpus import CorpusRepository, stable_hash
from .models import (
    BranchWrite,
    ConversationTurnCreate,
    ExperienceBriefCreate,
    ExperienceBriefPatch,
    RitualActionCreate,
    SessionCreate,
    StoryOfferCreate,
    StorySelectionCreate,
    TheatreScriptCreate,
)
from .safety import route_text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ServiceError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass
class SessionState:
    session_id: str
    locale: str
    consent: dict[str, bool]
    corpus_version: str
    created_at: str
    expires_at: str
    experience_briefs: list[dict[str, Any]] = field(default_factory=list)
    story_offers: list[dict[str, Any]] = field(default_factory=list)
    selection: dict[str, Any] | None = None
    source_snapshot: dict[str, Any] | None = None
    source_canon_hash: str | None = None
    branches: list[dict[str, Any]] = field(default_factory=list)
    approved_branch_version: int | None = None
    theatre_scripts: list[dict[str, Any]] = field(default_factory=list)
    ritual_actions: list[dict[str, Any]] = field(default_factory=list)
    safety_route: dict[str, Any] = field(
        default_factory=lambda: {"blocked": False, "route": "standard", "categories": []}
    )
    provenance_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class IdempotencyRecord:
    fingerprint: str
    response: dict[str, Any]


T = TypeVar("T", bound=dict[str, Any])


# How many user turns 栖蝶 may take before it stops asking and summarises.
# Bounded so the guidance cannot turn into an open-ended interview.
_MAX_GUIDANCE_TURNS = 4

_GUIDANCE_COMPLETE_ACKNOWLEDGEMENT = (
    "这些已经够用了。右边整理了一份摘要，你可以直接改，确认后我再去找故事。"
)

_PRESET_SUMMARIES = {
    "change": "最近生活中出现了一次变化，用户希望从故事中看看不同的理解角度。",
    "choice": "用户正在面对一个普通生活选择，希望通过故事梳理可保留的可能性。",
    "relationship": "一段关系正在变化，用户希望在不预设结论的情况下整理自己的感受。",
    "identity": "用户正在思考身份或角色变化，希望借故事探索可选择的表达。",
    "new-beginning": "用户离开了熟悉环境，希望重新找到适合自己的生活步调。",
    "relationship-boundary": "用户希望在一段关系中重新辨认并划定边界。",
    "plan-changed": "原来的计划发生变化，用户正在面对一次新的普通生活选择。",
    "quiet-transition": "生活正在悄然变化，用户暂时还说不清这次变化意味着什么。",
}


_FALLBACK_SUGGESTIONS: dict[int, tuple[str, str]] = {
    1: (
        "新的要求进入日常后，原有安排不再够用，变化由此变得具体。",
        "原本稳定的关系、计划或节奏发生改变，主角需要重新判断下一步。",
    ),
    2: (
        "主角暂不急着下结论，先确认自己愿意尝试、拒绝或推迟什么。",
        "主角联系一位可信任的人，也为自己保留暂缓决定的空间。",
    ),
    3: (
        "考验落在时间、沟通与具体琐事里，主角开始逐项处理。",
        "过往经验、可求助的人和现有资源，构成了下一步的条件。",
    ),
    4: (
        "主角发现，继续硬撑只会重复旧办法，于是开始调整顺序、分工或边界。",
        "主角接受事情无法回到原样，先选择一件当下能改变的事。",
    ),
    5: (
        "我会保留已经确认的部分，再用一个小步骤试着往前走。",
        "下一步还没有答案；我先写下可以求助的人和可以尝试的事。",
    ),
}

_NODE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("world_crack", "原来的世界与裂缝", "什么发生了变化？主角现在面对什么？"),
    ("cross_threshold", "跨过门槛", "主角愿意尝试、拒绝或暂缓什么？"),
    ("allies_resources", "考验、盟友与资源", "谁、什么经验或文化意象能够提供帮助？"),
    ("new_understanding", "新的理解或行动方式", "主角如何重新理解或处理处境？"),
    ("bring_back", "带着什么回来", "故事希望保留什么可能性？"),
)

_NODE_NUMBER = {node_id: index for index, (node_id, _, _) in enumerate(_NODE_DEFINITIONS, 1)}


# Story cards use familiar family-level names.  The exact fixed source version
# remains visible in the subtitle and source panel, so a readable title never
# erases version differences.
_CANONICAL_STORY_TITLES: dict[str, str] = {
    "pangu_cosmogony": "盘古开天",
    "kuafu_sun_chase": "夸父逐日",
    "gun_yu_flood_control": "大禹治水",
    "change_flight_to_moon": "嫦娥奔月",
    "mulan_substitution": "花木兰从军",
    "yellow_millet_dream": "黄粱一梦",
    "white_snake_legend": "白蛇传",
    "ganjiang_moye": "干将莫邪",
    "nuwa_mends_sky": "女娲补天",
    "nuwa_repairs_sky": "女娲补天",
    "nvwa_mends_sky": "女娲补天",
    "jingwei_fills_sea": "精卫填海",
    "jingwei_reclamation": "精卫填海",
    "yugong_moves_mountains": "愚公移山",
    "zhuangzi_butterfly_dream": "庄周梦蝶",
    "zhuangzhou_butterfly_dream": "庄周梦蝶",
    "fox_borrows_tiger_might": "狐假虎威",
    "houyi_shoots_suns": "后羿射日",
    "nvwa_creates_humans": "女娲造人",
    "shennong_tastes_herbs": "神农尝百草",
    "gonggong_hits_buzhou": "共工触不周山",
    "xingtian_dances": "刑天舞干戚",
    "wu_gang_cuts_osmanthus": "吴刚伐桂",
    "cowherd_weaver_girl": "牛郎织女",
    "mengjiangnu_great_wall": "孟姜女哭长城",
    "butterfly_lovers": "梁山伯与祝英台",
    "snail_maiden": "田螺姑娘",
    "peach_blossom_spring": "桃花源",
    "painted_skin": "画皮",
    "liu_yi_delivers_letter": "柳毅传书",
    "old_man_lost_horse": "塞翁失马",
    "farmer_waits_for_rabbit": "守株待兔",
    "marking_boat_for_sword": "刻舟求剑",
    "lord_ye_loves_dragons": "叶公好龙",
    "boya_breaks_strings": "伯牙绝弦",
    "nanke_dream": "南柯一梦",
}

_FAMILY_THEMES: dict[str, tuple[str, ...]] = {
    "pangu_cosmogony": ("变化", "新开始", "秩序", "未知"),
    "kuafu_sun_chase": ("追求", "坚持", "限度", "资源"),
    "gun_yu_flood_control": ("压力", "行动", "改变方法", "长期"),
    "change_flight_to_moon": ("离开", "选择", "关系", "代价"),
    "mulan_substitution": ("身份", "责任", "角色", "家庭"),
    "yellow_millet_dream": ("选择", "得失", "期待", "时间"),
    "white_snake_legend": ("关系", "边界", "信任", "身份"),
    "ganjiang_moye": ("伤害", "复仇", "正义", "边界"),
    "nuwa_mends_sky": ("修复", "危机", "责任", "秩序"),
    "nuwa_repairs_sky": ("修复", "危机", "责任", "秩序"),
    "nvwa_mends_sky": ("修复", "危机", "责任", "秩序"),
    "jingwei_fills_sea": ("坚持", "失去", "行动", "限度"),
    "jingwei_reclamation": ("坚持", "失去", "行动", "限度"),
    "yugong_moves_mountains": ("长期", "协作", "阻碍", "行动"),
    "zhuangzi_butterfly_dream": ("身份", "变化", "真实", "未知"),
    "zhuangzhou_butterfly_dream": ("身份", "变化", "真实", "未知"),
    "fox_borrows_tiger_might": ("权力", "身份", "借势", "判断"),
    "houyi_shoots_suns": ("危机", "行动", "责任", "限度"),
    "nvwa_creates_humans": ("身份", "创造", "关系", "归属"),
    "shennong_tastes_herbs": ("探索", "风险", "知识", "照料"),
    "gonggong_hits_buzhou": ("冲突", "后果", "失序", "修复"),
    "xingtian_dances": ("失去", "抗争", "坚持", "限度"),
    "wu_gang_cuts_osmanthus": ("循环", "劳作", "惩罚", "坚持"),
    "cowherd_weaver_girl": ("关系", "分离", "边界", "重逢"),
    "mengjiangnu_great_wall": ("失去", "关系", "权力", "哀悼"),
    "butterfly_lovers": ("关系", "身份", "选择", "阻碍"),
    "snail_maiden": ("照料", "信任", "关系", "边界"),
    "peach_blossom_spring": ("离开", "新环境", "选择", "归属"),
    "painted_skin": ("信任", "表象", "边界", "判断"),
    "liu_yi_delivers_letter": ("关系", "托付", "行动", "边界"),
    "old_man_lost_horse": ("得失", "变化", "判断", "未知"),
    "farmer_waits_for_rabbit": ("行动", "期待", "方法", "变化"),
    "marking_boat_for_sword": ("变化", "方法", "判断", "行动"),
    "lord_ye_loves_dragons": ("期待", "真实", "判断", "身份"),
    "boya_breaks_strings": ("关系", "理解", "失去", "表达"),
    "nanke_dream": ("得失", "身份", "时间", "选择"),
}

_USER_THEME_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("关系与边界", ("关系", "边界", "家人", "朋友", "同事", "伴侣", "相处", "拒绝", "信任")),
    ("离开与变化", ("变化", "改变", "离开", "告别", "搬家", "陌生", "新环境", "转变", "失去")),
    ("选择与行动", ("选择", "决定", "犹豫", "计划", "行动", "尝试", "坚持", "放弃", "困难", "压力")),
    ("身份与角色", ("身份", "角色", "自己", "期待", "责任", "工作", "家庭")),
    ("目标与限度", ("目标", "理想", "梦想", "追逐", "想要", "极限", "资源")),
)


def _canonical_story_title(record: dict[str, Any]) -> str:
    return _CANONICAL_STORY_TITLES.get(record["familyId"], str(record["title"]))


def _detect_user_theme(summary: str) -> tuple[str, str | None, set[str]]:
    matches: list[tuple[int, int, str, str, set[str]]] = []
    for order, (label, keywords) in enumerate(_USER_THEME_SIGNALS):
        present = {keyword for keyword in keywords if keyword in summary}
        if present:
            matches.append((len(present), -order, label, sorted(present)[0], present))
    if not matches:
        return "尚未归类的变化", None, set()
    _, _, label, evidence, present = max(matches)
    return label, evidence, present


def _story_relevance(record: dict[str, Any], summary: str) -> tuple[int, str, str | None, str]:
    label, evidence, user_signals = _detect_user_theme(summary)
    family_themes = _FAMILY_THEMES.get(record["familyId"], ())
    card = record.get("storyCard", {})
    searchable = " ".join(
        str(value)
        for value in (
            record.get("title"),
            _mapping_value(card, "summary", default=""),
            _mapping_value(card, "conflict", default=""),
            " ".join(str(item) for item in _as_list(_mapping_value(card, "motifs", default=[]))),
            " ".join(family_themes),
        )
    )
    direct_hits = sum(3 for signal in user_signals if signal in searchable)
    theme_hits = sum(1 for theme in family_themes if theme in summary or theme in user_signals)
    # A stable tie breaker is added by the caller; this score is explainable,
    # intentionally small, and never presented as a psychological assessment.
    score = direct_hits + theme_hits
    story_signal = family_themes[0] if family_themes else str(
        _mapping_value(card, "motifs", default=["叙事变化"])[0]
        if _as_list(_mapping_value(card, "motifs", default=[]))
        else "叙事变化"
    )
    return score, label, evidence, story_signal


def _recommendation_reason(record: dict[str, Any], summary: str) -> dict[str, Any]:
    _, label, evidence, story_signal = _story_relevance(record, summary)
    lead = f"你确认的摘要里出现了“{evidence}”" if evidence else "你正在尝试把当下的变化说清楚"
    is_c1_demo = str(record.get("corpusTier") or "").startswith("c1")
    return {
        "text": (
            (
                f"{lead}；《{_canonical_story_title(record)}》的来源片段中有“{story_signal}”相关线索，"
                "可以先作为一份比较材料，再由你决定是否继续。"
            )
            if is_c1_demo
            else (
                f"{lead}；《{_canonical_story_title(record)}》把“{story_signal}”放进一个有结局、也有代价的叙事中，"
                "可以作为一面比较用的镜子。它不是诊断，也不预设你应该照着故事行动。"
            )
        ),
        "userSignal": label,
        "storySignal": story_signal,
        "mode": "source_catalog_demo_match" if is_c1_demo else "source_grounded_theme_match",
    }


def _plot_beats(summary: str, original_ending: str) -> list[str]:
    sentences = [part.strip() for part in re.split(r"[。！？；]", summary) if part.strip()]
    beats = sentences[:3]
    if original_ending and len(beats) < 3:
        ending = re.sub(r"^本项目编辑草稿（非原典引文）：", "", original_ending).strip()
        if ending and ending not in beats:
            beats.append(ending)
    return beats or ["请在下方出处摘录中核对这份主文本的情节。"]


def _compact_evidence_text(value: str, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_CONVERSATION_PRIVATE_DETAILS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    re.compile(r"(?<!\d)\d{6,}(?!\d)"),
)
_GENERIC_CONVERSATION_CUES = {
    "嗯",
    "哦",
    "好",
    "好的",
    "可以",
    "继续",
    "随便",
    "都行",
    "不知道",
    "不确定",
    "没什么",
    "说不清",
}


def _privacy_safe_conversation_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    for pattern in _CONVERSATION_PRIVATE_DETAILS:
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _conversation_cues(
    message: str,
    *,
    history_user_texts: list[str],
    neutral_summary: str,
) -> list[str]:
    """Extract at most two short, privacy-filtered phrases from the user's own line."""

    sources = [message, *history_user_texts, neutral_summary]
    for source in sources:
        source_cues: list[str] = []
        safe_source = _privacy_safe_conversation_text(source)
        safe_source = re.sub(r"^你提到[：:]\s*", "", safe_source)
        for raw_clause in re.split(r"[。！？!?；;，,\n]+", safe_source):
            clause = raw_clause.strip(" \t\r\n：:、“”‘’\"'")
            clause = re.sub(
                r"^(?:其实|就是|然后|后来|不过|但是|而且|我想(?:先)?(?:说|聊)(?:的是|一下)?|我提到(?:的是)?)+",
                "",
                clause,
            ).strip()
            clause = re.sub(
                r"^(?:我|我们)(?:最近|这段时间|前几天|上周|昨天|今天|刚刚|现在)?(?:正在|在|刚|又|也|一直)?",
                "",
                clause,
            ).strip()
            if clause in _GENERIC_CONVERSATION_CUES or len(clause) < 2:
                continue
            if len(clause) > 36:
                clause = clause[:35].rstrip() + "…"
            if clause not in source_cues:
                source_cues.append(clause)
            if len(source_cues) == 2:
                break
        if source_cues:
            return source_cues

    fallback = _privacy_safe_conversation_text(message).strip("。！？!?；;，, ")
    if fallback:
        return [fallback[:35].rstrip() + ("…" if len(fallback) > 35 else "")]
    return ["刚才分享的这件事"]


def _deterministic_conversation_follow_up(
    cues: list[str],
    *,
    user_turns: int,
) -> tuple[str, str, list[str]]:
    primary = cues[0]
    secondary = cues[1] if len(cues) > 1 else primary
    if user_turns == 1:
        acknowledgement = f"我先记下“{primary}”；这是这件事目前最清楚的一条线索。"
        follow_up_question = "如果从这里继续，接下来最先发生了什么？"
        options = [
            f"从“{primary}”发生的那一刻说起",
            f"补充“{secondary}”之前和之后的变化",
            f"说说“{primary}”里我当时做了什么",
        ]
    elif user_turns == 2:
        acknowledgement = f"你刚补充的“{primary}”，让事情前后的变化更清楚了。"
        follow_up_question = "沿着这个细节往后，事情最明显的变化是什么？"
        options = [
            f"说说“{primary}”里最难忘的一个细节",
            f"补充“{secondary}”当时我是怎么回应的",
            f"沿着“{primary}”说说事情后来怎样了",
        ]
    else:
        acknowledgement = f"“{primary}”这一处我先保留下来，不让后面的概括把它盖过去。"
        follow_up_question = "还有哪一个细节，是你希望我不要概括掉的？"
        options = [
            f"说说“{primary}”里我最想保留的部分",
            f"补充“{secondary}”现在还留下了什么影响",
            f"指出“{primary}”中不希望被替我解释的地方",
        ]
    return acknowledgement, follow_up_question, options


def _validate_conversation_beats(
    acknowledgement: Any,
    follow_up_question: Any,
) -> tuple[str, str]:
    acknowledgement_text = re.sub(r"\s+", " ", str(acknowledgement or "")).strip()
    question_text = re.sub(r"\s+", " ", str(follow_up_question or "")).strip()
    if not 1 <= len(acknowledgement_text) <= 80:
        raise ModelUnavailable("Conversation acknowledgement must be one short sentence")
    if "？" in acknowledgement_text or "?" in acknowledgement_text:
        raise ModelUnavailable("Conversation acknowledgement must not contain a question")
    question_mark_count = question_text.count("？") + question_text.count("?")
    if (
        not 2 <= len(question_text) <= 80
        or question_mark_count != 1
        or question_text[-1] not in {"？", "?"}
    ):
        raise ModelUnavailable("Conversation follow-up must contain exactly one short question")
    if len(acknowledgement_text) + len(question_text) + 2 > 180:
        raise ModelUnavailable("Conversation turn is too long")
    return acknowledgement_text, question_text


def _conversation_beats_from_legacy_reply(
    reply: Any,
) -> tuple[str, str, str]:
    """Split a legacy reply-only adapter result without breaking old adapters."""

    reply_text = re.sub(r"\s+", " ", str(reply or "")).strip()
    if not 1 <= len(reply_text) <= 180:
        raise ModelUnavailable("Conversation model returned an invalid reply")

    question_match = re.search(r"([^。！？?；;：:\n]+[？?])$", reply_text)
    if not question_match:
        raise ModelUnavailable("Legacy conversation reply cannot be split into two beats")
    question = question_match.group(1).strip()
    acknowledgement = reply_text[: question_match.start()].rstrip("；;：:，, ")
    if not acknowledgement:
        raise ModelUnavailable("Legacy conversation reply is missing an acknowledgement")
    if acknowledgement[-1] not in {"。", "！", "!"}:
        acknowledgement += "。"
    acknowledgement, question = _validate_conversation_beats(
        acknowledgement,
        question,
    )
    return acknowledgement, question, f"{acknowledgement}\n\n{question}"


def _ground_model_follow_up_options(options: Any, cues: list[str]) -> list[str]:
    if not isinstance(options, list) or not 2 <= len(options) <= 3:
        raise ModelUnavailable("Conversation follow-up options must contain two or three items")
    grounded: list[str] = []
    for index, raw_option in enumerate(options):
        option = re.sub(r"\s+", " ", str(raw_option)).strip()
        if not 4 <= len(option) <= 100:
            raise ModelUnavailable("Conversation follow-up options must be short non-empty strings")
        if "？" in option or "?" in option:
            raise ModelUnavailable("Conversation follow-up options must not contain questions")
        cue = cues[index % len(cues)]
        if cue not in option:
            option = f"沿着“{cue}”：{option}"
        if len(option) > 100:
            option = option[:99].rstrip() + "…"
        if option not in grounded:
            grounded.append(option)
    if len(grounded) < 2:
        raise ModelUnavailable("Conversation follow-up options must be distinct")
    return grounded


def _mapping_value(mapping: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


class SessionService:
    def __init__(
        self,
        corpus: CorpusRepository,
        model_adapter: DeepSeekAdapter | None = None,
        *,
        image_adapter: AliyunImageAdapter | None = None,
        tts_adapter: AliyunTtsAdapter | None = None,
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.corpus = corpus
        self.model_adapter = model_adapter or DeepSeekAdapter()
        self.image_adapter = image_adapter or AliyunImageAdapter()
        self.tts_adapter = tts_adapter or AliyunTtsAdapter()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._session_ttl = session_ttl
        self._sessions: dict[str, SessionState] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[tuple[str, str], IdempotencyRecord] = {}
        self._invalidated_idempotency: set[str] = set()
        self._lock = threading.RLock()

    def _now_datetime(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _isoformat(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _now(self) -> str:
        return self._isoformat(self._now_datetime())

    @staticmethod
    def _session_id_from_scope(scope: str) -> str | None:
        if scope.startswith("sessions:"):
            return None
        session_id, separator, _ = scope.partition(":")
        return session_id if separator and session_id else None

    @staticmethod
    def _idempotency_digest(cache_key: tuple[str, str]) -> str:
        return stable_hash({"scope": cache_key[0], "key": cache_key[1]})

    def _purge_session_idempotency(self, session_id: str) -> None:
        stale_keys = []
        for cache_key, record in self._idempotency.items():
            scope, _ = cache_key
            response_session_id = record.response.get("sessionId") or record.response.get("id")
            if scope.startswith(f"{session_id}:") or response_session_id == session_id:
                stale_keys.append(cache_key)
        for cache_key in stale_keys:
            self._invalidated_idempotency.add(self._idempotency_digest(cache_key))
            self._idempotency.pop(cache_key, None)

    def _expire_session(self, session: SessionState) -> None:
        self._sessions.pop(session.session_id, None)
        self._purge_session_idempotency(session.session_id)
        expired_at = self._now()
        self._tombstones[session.session_id] = {
            "sessionId": session.session_id,
            "expired": True,
            "expiredAt": expired_at,
            "deletionProof": stable_hash(
                {"sessionId": session.session_id, "expiredAt": expired_at, "state": "expired"}
            ),
        }

    def idempotent(
        self,
        scope: str,
        key: str | None,
        request_payload: dict[str, Any],
        operation: Callable[[], T],
    ) -> tuple[T, bool]:
        if not key:
            return operation(), False

        fingerprint = stable_hash(request_payload)
        cache_key = (scope, key)
        with self._lock:
            scoped_session_id = self._session_id_from_scope(scope)
            if scoped_session_id is not None:
                self._session(scoped_session_id)
            if self._idempotency_digest(cache_key) in self._invalidated_idempotency:
                raise ServiceError(
                    409,
                    "idempotency_replay_unavailable",
                    "The original response for this idempotency key was securely invalidated",
                )
            existing = self._idempotency.get(cache_key)
            if existing is not None:
                if scope == "sessions:create":
                    replay_session_id = existing.response.get("sessionId") or existing.response.get("id")
                    if replay_session_id:
                        try:
                            self._session(str(replay_session_id))
                        except ServiceError:
                            self._idempotency.pop(cache_key, None)
                            raise ServiceError(
                                409,
                                "idempotency_replay_unavailable",
                                "The session created by this idempotency key is no longer available",
                            )
                if existing.fingerprint != fingerprint:
                    raise ServiceError(
                        409,
                        "idempotency_key_reused",
                        "Idempotency-Key was already used with a different request",
                    )
                return copy.deepcopy(existing.response), True
            result = operation()
            if not scope.endswith(":delete"):
                self._idempotency[cache_key] = IdempotencyRecord(
                    fingerprint=fingerprint,
                    response=copy.deepcopy(result),
                )
            return result, False

    def _session(self, session_id: str) -> SessionState:
        session = self._sessions.get(session_id)
        if session is None:
            raise ServiceError(404, "session_not_found", "Session does not exist or was deleted")
        expires_at = datetime.fromisoformat(session.expires_at.replace("Z", "+00:00"))
        if self._now_datetime() >= expires_at:
            self._expire_session(session)
            raise ServiceError(404, "session_not_found", "Session does not exist or was deleted")
        return session

    @staticmethod
    def _event(session: SessionState, event_type: str, **details: Any) -> None:
        session.provenance_events.append(
            {
                "eventType": event_type,
                "occurredAt": utc_now(),
                **copy.deepcopy(details),
            }
        )

    @staticmethod
    def _session_payload(session: SessionState) -> dict[str, Any]:
        return {
            "id": session.session_id,
            "sessionId": session.session_id,
            "status": "active",
            "locale": session.locale,
            "consent": copy.deepcopy(session.consent),
            "corpusVersion": session.corpus_version,
            "createdAt": session.created_at,
            "expiresAt": session.expires_at,
            "safetyBlocked": session.safety_route["blocked"],
        }

    def create_session(self, request: SessionCreate) -> dict[str, Any]:
        with self._lock:
            session_id = str(uuid.uuid4())
            created_at_value = self._now_datetime()
            session = SessionState(
                session_id=session_id,
                locale=request.locale,
                consent=request.consent.model_dump(mode="json", by_alias=True),
                corpus_version=request.corpus_version or self.corpus.corpus_version,
                created_at=self._isoformat(created_at_value),
                expires_at=self._isoformat(created_at_value + self._session_ttl),
            )
            self._sessions[session_id] = session
            self._event(session, "session_created", corpusVersion=session.corpus_version)
            return self._session_payload(session)

    @staticmethod
    def _neutral_summary(text: str) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) > 180:
            normalized = normalized[:177].rstrip() + "…"
        return f"你提到：{normalized}"

    def create_experience_brief(
        self,
        session_id: str,
        request: ExperienceBriefCreate,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Personalized generation is disabled for this session")

            text = (request.text or request.original_text or "").strip()
            preset_id = (request.preset_id or "").strip() or None
            input_mode = request.input_mode or ("preset" if preset_id else "text")
            safety = route_text(text) if text else route_text("")
            version = len(session.experience_briefs) + 1
            brief_id = (
                session.experience_briefs[-1]["id"]
                if session.experience_briefs
                else f"brief-{uuid.uuid4()}"
            )

            if safety.blocked:
                session.safety_route = {
                    "blocked": True,
                    "route": safety.route,
                    "categories": list(safety.categories),
                    "blockedAt": utc_now(),
                    "support": {
                        "chinaMentalHealthHotline": "12356",
                        "emergency": ["110", "120"],
                        "realTimeMonitoring": False,
                    },
                }
                brief = {
                    "id": brief_id,
                    "version": version,
                    "briefVersion": version,
                    "status": "safety_blocked",
                    "confirmed": False,
                    "inputMode": input_mode,
                    "presetId": preset_id,
                    "neutralSummary": "",
                    "draftSummary": None,
                    "createdAt": utc_now(),
                    "safetyRoute": "stopped",
                }
                session.experience_briefs.append(brief)
                self._event(
                    session,
                    "safety_route_triggered",
                    categories=list(safety.categories),
                    rawTextStored=False,
                )
                return {**copy.deepcopy(brief), "safetyRoute": copy.deepcopy(session.safety_route)}

            draft_summary = (
                _PRESET_SUMMARIES.get(preset_id, f"用户选择了预设情境：{preset_id}。")
                if preset_id
                else self._neutral_summary(text)
            )
            brief = {
                "id": brief_id,
                "version": version,
                "briefVersion": version,
                "status": "draft",
                "confirmed": False,
                "inputMode": input_mode,
                "presetId": preset_id,
                "originalText": text or None,
                "neutralSummary": draft_summary,
                "draftSummary": draft_summary,
                "confirmedSummary": None,
                "createdAt": utc_now(),
                "safetyRoute": "preset_only" if input_mode == "preset" else "standard",
            }
            session.experience_briefs.append(brief)
            self._event(session, "experience_brief_created", briefVersion=version)
            return copy.deepcopy(brief)

    def create_conversation_turn(
        self,
        session_id: str,
        request: ConversationTurnCreate,
    ) -> dict[str, Any]:
        """Return one bounded conversational follow-up.

        The safety router always runs first.  User text reaches the external
        adapter only when this session explicitly accepted cloud processing;
        otherwise the same UI receives a deterministic local follow-up.
        """

        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Personalized conversation is disabled")
            safety = route_text(request.message)
            if safety.blocked:
                session.safety_route = {
                    "blocked": True,
                    "route": safety.route,
                    "categories": list(safety.categories),
                    "blockedAt": utc_now(),
                    "support": {
                        "chinaMentalHealthHotline": "12356",
                        "emergency": ["110", "120"],
                        "realTimeMonitoring": False,
                    },
                }
                self._event(
                    session,
                    "safety_route_triggered",
                    categories=list(safety.categories),
                    rawTextStored=False,
                )
                raise ServiceError(403, "safety_blocked", "Personalized conversation is disabled")

            user_turns = sum(1 for item in request.history if item.role == "user") + 1
            latest_brief = session.experience_briefs[-1] if session.experience_briefs else {}
            neutral_summary = str(
                latest_brief.get("neutralSummary")
                or latest_brief.get("confirmedSummary")
                or latest_brief.get("draftSummary")
                or ""
            )
            cues = _conversation_cues(
                request.message,
                history_user_texts=[
                    item.text for item in reversed(request.history) if item.role == "user"
                ],
                neutral_summary=neutral_summary,
            )
            acknowledgement, follow_up_question, follow_up_options = (
                _deterministic_conversation_follow_up(
                    cues,
                    user_turns=user_turns,
                )
            )
            acknowledgement, follow_up_question = _validate_conversation_beats(
                acknowledgement,
                follow_up_question,
            )
            reply = f"{acknowledgement}\n\n{follow_up_question}"
            source = "deterministic_fallback"
            model_version: str | None = None
            if session.consent.get("cloudProcessingAccepted") is True:
                try:
                    history = [
                        item.model_dump(mode="json", by_alias=True)
                        for item in request.history
                    ]
                    conversation_turn = getattr(self.model_adapter, "conversation_turn", None)
                    if callable(conversation_turn):
                        generated = conversation_turn(
                            user_message=request.message,
                            history=history,
                        )
                        if not isinstance(generated, dict):
                            raise ModelUnavailable("Conversation model must return a structured turn")
                        generated_acknowledgement = generated.get("acknowledgement")
                        generated_question = generated.get(
                            "followUpQuestion", generated.get("follow_up_question")
                        )
                        if generated_acknowledgement is not None or generated_question is not None:
                            if generated_acknowledgement is None or generated_question is None:
                                raise ModelUnavailable(
                                    "Conversation model returned an incomplete two-beat turn"
                                )
                            candidate_acknowledgement, candidate_question = (
                                _validate_conversation_beats(
                                    generated_acknowledgement,
                                    generated_question,
                                )
                            )
                            candidate_reply = (
                                f"{candidate_acknowledgement}\n\n{candidate_question}"
                            )
                        else:
                            (
                                candidate_acknowledgement,
                                candidate_question,
                                candidate_reply,
                            ) = (
                                _conversation_beats_from_legacy_reply(
                                    generated.get("reply"),
                                )
                            )
                        generated_options = generated.get(
                            "followUpOptions", generated.get("follow_up_options")
                        )
                        grounded_options = _ground_model_follow_up_options(
                            generated_options,
                            cues,
                        )
                        acknowledgement = candidate_acknowledgement
                        follow_up_question = candidate_question
                        reply = candidate_reply
                        follow_up_options = grounded_options
                    else:
                        legacy_reply = self.model_adapter.chat(
                            user_message=request.message,
                            history=history,
                        )
                        acknowledgement, follow_up_question, reply = (
                            _conversation_beats_from_legacy_reply(
                                legacy_reply,
                            )
                        )
                    is_live_deepseek = (
                        type(self.model_adapter) is DeepSeekAdapter
                        and getattr(getattr(self.model_adapter, "config", None), "available", False) is True
                    )
                    source = "deepseek" if is_live_deepseek else "model_adapter"
                    model_version = self.model_adapter.config.model if is_live_deepseek else None
                except (ModelUnavailable, AttributeError, TypeError, ValueError):
                    pass

            # Guidance is bounded: past this many user turns the assistant stops
            # asking and hands over to the summary step, so the conversation
            # cannot wander indefinitely.
            guidance_complete = user_turns >= _MAX_GUIDANCE_TURNS
            summary_source = "deterministic_fallback"
            if guidance_complete:
                follow_up_question = ""
                follow_up_options = []
                acknowledgement = _GUIDANCE_COMPLETE_ACKNOWLEDGEMENT
                reply = acknowledgement
                model_summary = self._summarize_conversation(
                    session,
                    history=[
                        item.model_dump(mode="json", by_alias=True)
                        for item in request.history
                    ],
                    latest_message=request.message,
                )
                if model_summary is not None:
                    summary_source = model_summary[1]
                    self._store_brief_summary(session, model_summary[0])

            self._event(
                session,
                "conversation_turn_returned",
                phase=request.phase,
                conversationSource=source,
                followUpOptionCount=len(follow_up_options),
                guidanceComplete=guidance_complete,
                rawTextStored=False,
            )
            return {
                "phase": request.phase,
                "reply": reply,
                "acknowledgement": acknowledgement,
                "followUpQuestion": follow_up_question,
                "followUpOptions": follow_up_options,
                "source": source,
                "modelVersion": model_version,
                "turnsUsed": user_turns,
                "turnBudget": _MAX_GUIDANCE_TURNS,
                "guidanceComplete": guidance_complete,
                "summarySource": summary_source if guidance_complete else None,
            }

    def _summarize_conversation(
        self,
        session: SessionState,
        *,
        history: list[dict[str, Any]],
        latest_message: str,
    ) -> tuple[str, str] | None:
        """Return (summary, source) for the finished conversation, or None."""

        full_history = [*history, {"role": "user", "text": latest_message}]
        if session.consent.get("cloudProcessingAccepted") is True:
            summarize = getattr(self.model_adapter, "summarize_experience", None)
            if callable(summarize):
                try:
                    generated = str(summarize(history=full_history)).strip()
                    if 10 <= len(generated) <= 240:
                        is_live_deepseek = (
                            type(self.model_adapter) is DeepSeekAdapter
                            and getattr(
                                getattr(self.model_adapter, "config", None), "available", False
                            )
                            is True
                        )
                        return generated, ("deepseek" if is_live_deepseek else "model_adapter")
                except (ModelUnavailable, AttributeError, TypeError, ValueError):
                    pass

        user_texts = [
            str(item.get("text") or "").strip()
            for item in full_history
            if item.get("role") == "user" and str(item.get("text") or "").strip()
        ]
        if not user_texts:
            return None
        return self._neutral_summary("；".join(user_texts)), "deterministic_fallback"

    @staticmethod
    def _store_brief_summary(session: SessionState, summary: str) -> None:
        if not session.experience_briefs:
            return
        brief = session.experience_briefs[-1]
        if brief.get("confirmed") is True:
            return
        brief["neutralSummary"] = summary
        brief["draftSummary"] = summary

    def patch_experience_brief(
        self,
        session_id: str,
        request: ExperienceBriefPatch,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Personalized generation is disabled for this session")
            if not session.experience_briefs:
                raise ServiceError(409, "brief_missing", "Create an experience brief before confirming it")

            previous = session.experience_briefs[-1]
            if request.brief_id is not None and request.brief_id != previous.get("id"):
                raise ServiceError(409, "brief_id_conflict", "briefId does not match the latest brief")
            if request.parent_version is not None and request.parent_version != previous["briefVersion"]:
                raise ServiceError(
                    409,
                    "parent_version_conflict",
                    f"Expected parentVersion {previous['briefVersion']}",
                )
            version = len(session.experience_briefs) + 1
            if request.delete_draft:
                previous.pop("originalText", None)
                brief = {
                    "id": previous["id"],
                    "version": version,
                    "briefVersion": version,
                    "parentVersion": previous["briefVersion"],
                    "status": "deleted",
                    "confirmed": False,
                    "inputMode": previous.get("inputMode", "text"),
                    "neutralSummary": "",
                    "draftSummary": None,
                    "confirmedSummary": None,
                    "createdAt": utc_now(),
                    "safetyRoute": previous.get("safetyRoute", "standard"),
                }
                session.experience_briefs.append(brief)
                self._event(session, "experience_brief_deleted", briefVersion=version)
                return copy.deepcopy(brief)

            summary = (
                request.neutral_summary
                or request.confirmed_summary
                or previous.get("draftSummary")
                or ""
            ).strip()
            if request.confirmed and not summary:
                raise ServiceError(422, "summary_required", "A non-empty summary is required for confirmation")
            brief = {
                "id": previous["id"],
                "version": version,
                "briefVersion": version,
                "parentVersion": previous["briefVersion"],
                "status": "confirmed" if request.confirmed else "draft",
                "confirmed": request.confirmed,
                "inputMode": previous.get("inputMode", "text"),
                "presetId": previous.get("presetId"),
                "originalText": previous.get("originalText"),
                "neutralSummary": summary,
                "draftSummary": summary,
                "confirmedSummary": summary if request.confirmed else None,
                "createdAt": utc_now(),
                "safetyRoute": previous.get("safetyRoute", "standard"),
            }
            session.experience_briefs.append(brief)
            self._event(
                session,
                "experience_brief_confirmed" if request.confirmed else "experience_brief_updated",
                briefVersion=version,
            )
            return copy.deepcopy(brief)

    @staticmethod
    def _confirmed_brief(session: SessionState) -> dict[str, Any]:
        for brief in reversed(session.experience_briefs):
            if brief.get("confirmed"):
                return brief
        raise ServiceError(409, "brief_not_confirmed", "Confirm the experience brief before requesting stories")

    @staticmethod
    def _adult_content_enabled(session: SessionState) -> bool:
        return (
            session.consent.get("adultConfirmed") is True
            and session.consent.get("adultContentOptIn") is True
        )

    @staticmethod
    def _public_story_card(
        record: dict[str, Any],
        confirmed_summary: str = "",
        *,
        recommendation_override: dict[str, Any] | None = None,
        retrieval_terms: list[str] | None = None,
        retrieval_plan_source: str = "deterministic_fallback",
        rerank_source: str = "deterministic_fallback",
    ) -> dict[str, Any]:
        source = record.get("sourceCanon", {})
        card = record.get("storyCard", {})
        boundary = record.get("adaptationBoundary", {})
        is_c1_demo = str(record.get("corpusTier") or "").startswith("c1")
        work_title = _mapping_value(source, "work", "source_title", "sourceTitle", "title", default="")
        excerpt = _mapping_value(
            source,
            "original_excerpt",
            "originalExcerpt",
            "excerpt",
            "summary",
            default="",
        )
        original_ending = _mapping_value(
            source,
            "ending",
            "original_ending",
            "originalEnding",
            default="",
        )
        motifs = _as_list(_mapping_value(card, "motifs", default=[]))
        must_keep = _as_list(_mapping_value(boundary, "must_preserve", "mustPreserve", "must_keep", "mustKeep"))
        allowed = _as_list(
            _mapping_value(
                boundary,
                "may_transform",
                "mayTransform",
                "allowed_transformations",
                "allowedTransformations",
            )
        )
        prohibited = _as_list(_mapping_value(boundary, "prohibited", "prohibited_changes", "prohibitedChanges"))

        references: list[dict[str, Any]] = []
        provenance = record.get("provenance", [])
        if isinstance(provenance, list):
            for index, item in enumerate(provenance, 1):
                if not isinstance(item, dict):
                    continue
                references.append(
                    {
                        "id": str(_mapping_value(item, "id", default=f"ref-{index}")),
                        "title": str(_mapping_value(item, "title", "label", default=work_title or record["title"])),
                        "workTitle": _mapping_value(item, "work", "workTitle", default=work_title),
                        "author": _mapping_value(item, "author", default=_mapping_value(source, "author")),
                        "era": _mapping_value(item, "era", default=record.get("era")),
                        "edition": _mapping_value(item, "edition"),
                        "locator": str(
                            _mapping_value(
                                item,
                                "locator",
                                "evidence_anchor",
                                "evidenceAnchor",
                                default=_mapping_value(source, "evidence_anchor", "evidenceAnchor", default=""),
                            )
                        ),
                        "url": _mapping_value(item, "url", "source_url", "sourceUrl"),
                        "rights": _mapping_value(item, "rights"),
                    }
                )
        if not references:
            references.append(
                {
                    "id": f"source-{record['storyVersionId']}",
                    "title": str(work_title or record["title"]),
                    "workTitle": work_title or None,
                    "author": _mapping_value(source, "author"),
                    "era": record.get("era"),
                    "edition": _mapping_value(source, "edition"),
                    "locator": str(_mapping_value(source, "evidence_anchor", "evidenceAnchor", default="")),
                    "url": _mapping_value(source, "source_url", "sourceUrl", "url"),
                    "rights": _mapping_value(record.get("rightsAndAccess"), "copyright_basis", "copyrightBasis"),
                }
            )

        source_canon = {
            "storyVersionId": record["storyVersionId"],
            "title": record["title"],
            "sourceTitle": str(work_title or record["title"]),
            "excerpt": str(excerpt or ""),
            "originalEnding": str(original_ending or ""),
            "motifs": [str(value) for value in motifs],
            "mustKeep": [str(value) for value in must_keep],
            "allowedTransformations": [str(value) for value in allowed],
            "prohibitedChanges": [str(value) for value in prohibited],
            "references": references,
            "hash": record["sourceCanonHash"],
        }
        recommendation = _recommendation_reason(record, confirmed_summary)
        if recommendation_override is not None:
            recommendation = {
                **recommendation,
                "text": str(recommendation_override.get("reason") or recommendation["text"]),
                "storySignal": str(
                    recommendation_override.get("storySignal") or recommendation["storySignal"]
                ),
                "mode": "hybrid_rag_rerank",
            }
        model_generated = recommendation_override is not None
        retrieval_evidence = [
            {
                "sourceTitle": str(work_title or record["title"]),
                "locator": str(
                    _mapping_value(source, "evidence_anchor", "evidenceAnchor", default="")
                ),
                "excerpt": _compact_evidence_text(str(excerpt or ""), limit=360),
            }
        ]
        version_label_parts = [str(work_title or record["title"])]
        if record.get("era"):
            version_label_parts.append(str(record["era"]))
        return {
            "storyVersionId": record["storyVersionId"],
            "storyFamilyId": record["familyId"],
            "experienceMode": "generated" if is_c1_demo else "prepared",
            "deepAnnotated": not is_c1_demo,
            "title": _canonical_story_title(record),
            "subtitle": " · ".join(version_label_parts),
            "summary": str(_mapping_value(card, "summary", default=_mapping_value(source, "summary", default=""))),
            "characters": [str(value) for value in _as_list(_mapping_value(card, "characters"))],
            "conflict": str(_mapping_value(card, "conflict", default="")),
            "motifs": [str(value) for value in motifs],
            "imagery": [str(value) for value in _as_list(_mapping_value(card, "imagery", "images"))],
            "emotionalArc": str(_mapping_value(card, "emotional_arc", "emotionalArc", default="")),
            "possibleResonance": str(
                _mapping_value(card, "resonance", "possible_resonance", "possibleResonance", default="")
            ),
            "mayNotFit": str(_mapping_value(card, "non_fit", "nonFit", "may_not_fit", "mayNotFit", default="")),
            "contentWarnings": [
                str(value)
                for value in _as_list(_mapping_value(card, "content_warnings", "contentWarnings"))
            ],
            "recommendationReason": recommendation["text"],
            "recommendationBasis": {
                "userSignal": recommendation["userSignal"],
                "storySignal": recommendation["storySignal"],
                "mode": recommendation["mode"],
                "modelGenerated": model_generated,
                "retrievalMode": "hybrid_rag" if model_generated else "deterministic_fallback",
                "candidateRecallMode": str(record.get("retrievalMode") or "curated_deep_record"),
                "retrievalTerms": list(retrieval_terms or []),
                "retrievalPlanSource": retrieval_plan_source,
                "rerankSource": rerank_source,
                "evidence": retrieval_evidence,
                "rerankScore": (
                    float(recommendation_override["score"])
                    if recommendation_override is not None
                    and recommendation_override.get("score") is not None
                    else None
                ),
            },
            "illustrationKey": record["familyId"],
            "explanation": {
                "overview": str(
                    _mapping_value(card, "summary", default=_mapping_value(source, "summary", default=""))
                ),
                "plotBeats": _plot_beats(
                    str(_mapping_value(card, "summary", default="")),
                    str(original_ending or ""),
                ),
                "sourceVersion": " · ".join(version_label_parts),
                "editorialStatus": (
                    "来源库演示候选 · 自动生成基础卡"
                    if is_c1_demo
                    else "产品主文本 · 待专家与双人文化复核"
                ),
            },
            "sourceCanon": source_canon,
            "adaptationBoundary": copy.deepcopy(boundary),
            "sourceCanonHash": record["sourceCanonHash"],
        }

    def _retrieval_plan(
        self,
        session: SessionState,
        confirmed_summary: str,
    ) -> tuple[dict[str, list[str]], str]:
        label, _, detected_terms = _detect_user_theme(confirmed_summary)
        fallback = {
            "terms": sorted(detected_terms)[:8],
            "themes": [] if label == "尚未归类的变化" else [label],
        }
        if session.consent.get("cloudProcessingAccepted") is not True:
            return fallback, "deterministic_fallback"
        try:
            plan = self.model_adapter.retrieval_plan(user_summary=confirmed_summary)
            terms = [str(value) for value in plan.get("terms", [])][:8]
            themes = [str(value) for value in plan.get("themes", [])][:4]
            if not terms:
                raise ModelUnavailable("No usable model retrieval terms")
            is_live_deepseek = (
                type(self.model_adapter) is DeepSeekAdapter
                and getattr(getattr(self.model_adapter, "config", None), "available", False) is True
            )
            return {"terms": terms, "themes": themes}, (
                "deepseek" if is_live_deepseek else "model_adapter"
            )
        except (ModelUnavailable, AttributeError, TypeError, ValueError):
            return fallback, "deterministic_fallback"

    def _rerank_with_model(
        self,
        session: SessionState,
        confirmed_summary: str,
        ranked: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], str]:
        if session.consent.get("cloudProcessingAccepted") is not True:
            return {}, "deterministic_fallback"
        c1 = [
            item
            for item in ranked
            if str(item.get("corpusTier") or "").startswith("c1")
        ][:6]
        c3 = [
            item
            for item in ranked
            if not str(item.get("corpusTier") or "").startswith("c1")
        ][:2]
        bounded_records = [*c1, *c3][:8]
        if not bounded_records:
            return {}, "deterministic_fallback"
        payload: list[dict[str, Any]] = []
        for item in bounded_records:
            source = item.get("sourceCanon", {})
            references = _as_list(_mapping_value(source, "references", default=[]))
            first_reference = references[0] if references and isinstance(references[0], dict) else {}
            payload.append(
                {
                    "storyVersionId": item["storyVersionId"],
                    "title": _canonical_story_title(item),
                    "sourceTitle": _mapping_value(
                        source,
                        "work",
                        "source_title",
                        "sourceTitle",
                        default=item["title"],
                    ),
                    "locator": _mapping_value(
                        first_reference,
                        "locator",
                        default=_mapping_value(
                            source,
                            "evidence_anchor",
                            "evidenceAnchor",
                            default="",
                        ),
                    ),
                    "excerpt": _compact_evidence_text(
                        str(
                            _mapping_value(
                                source,
                                "excerpt",
                                "original_excerpt",
                                "originalExcerpt",
                                "summary",
                                default="",
                            )
                        ),
                        limit=500,
                    ),
                }
            )
        try:
            rankings = self.model_adapter.rerank_candidates(
                user_summary=confirmed_summary,
                candidates=payload,
            )
        except (ModelUnavailable, AttributeError, TypeError, ValueError):
            return {}, "deterministic_fallback"
        by_id = {
            str(item["storyVersionId"]): item
            for item in rankings
            if isinstance(item, dict) and item.get("storyVersionId")
        }
        if not by_id:
            return {}, "deterministic_fallback"
        is_live_deepseek = (
            type(self.model_adapter) is DeepSeekAdapter
            and getattr(getattr(self.model_adapter, "config", None), "available", False) is True
        )
        return by_id, "deepseek" if is_live_deepseek else "model_adapter"

    def create_story_offer(
        self,
        session_id: str,
        request: StoryOfferCreate,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Personalized story offers are disabled")
            confirmed_brief = self._confirmed_brief(session)
            confirmed_summary = str(
                confirmed_brief.get("confirmedSummary")
                or confirmed_brief.get("neutralSummary")
                or ""
            )
            adult_content_enabled = self._adult_content_enabled(session)
            candidate_count = self.corpus.recommendation_candidate_count(
                adult_content_opt_in=adult_content_enabled
            )
            if candidate_count < 2:
                raise ServiceError(
                    503,
                    "c3_corpus_unavailable",
                    "At least two eligible recommendation stories are required",
                )

            excluded: set[str] = set(request.excluded_story_version_ids)
            refreshing_existing_offer = request.refresh and bool(session.story_offers)
            if refreshing_existing_offer:
                excluded.update(
                    item["storyVersionId"]
                    for previous_offer in session.story_offers
                    for item in previous_offer["candidates"]
                )
            retrieval_plan, retrieval_plan_source = self._retrieval_plan(
                session,
                confirmed_summary,
            )
            corpus_candidates = self.corpus.recommendation_candidates(
                query=confirmed_summary,
                query_terms=retrieval_plan["terms"],
                limit=max(12, request.limit * 6),
                excluded_ids=excluded,
                adult_content_opt_in=adult_content_enabled,
                seed=f"{session.session_id}:{len(session.story_offers) + 1}",
            )
            previously_seen_families = {
                item["storyFamilyId"]
                for previous_offer in session.story_offers
                for item in previous_offer["candidates"]
            }
            ranked = sorted(
                corpus_candidates,
                key=lambda item: (
                    -_story_relevance(item, confirmed_summary)[0],
                    item["familyId"],
                    item["storyVersionId"],
                ),
            )
            rerank_by_id, rerank_source = self._rerank_with_model(
                session,
                confirmed_summary,
                ranked,
            )
            if rerank_by_id:
                ranked = sorted(
                    ranked,
                    key=lambda item: (
                        -float(
                            rerank_by_id.get(item["storyVersionId"], {}).get("score", -1.0)
                        ),
                        -_story_relevance(item, confirmed_summary)[0],
                        item["familyId"],
                        item["storyVersionId"],
                    ),
                )
            # C3 contains exactly one adopted main text per story. Keep a
            # defensive family check here so malformed data can never surface
            # a second source witness as another product card.
            family_unique: list[dict[str, Any]] = []
            seen_in_offer: set[str] = set()
            for item in ranked:
                if item["familyId"] in seen_in_offer:
                    continue
                if refreshing_existing_offer and item["familyId"] in previously_seen_families:
                    continue
                family_unique.append(item)
                seen_in_offer.add(item["familyId"])
            # Keep the familiar, deep-annotated cards compatible while making
            # the source catalog visibly real: when both tiers are available,
            # every offer reserves at least one slot for each tier.
            if request.limit >= 2:
                best_c3 = next(
                    (
                        item
                        for item in family_unique
                        if not str(item.get("corpusTier") or "").startswith("c1")
                    ),
                    None,
                )
                best_c1 = next(
                    (
                        item
                        for item in family_unique
                        if str(item.get("corpusTier") or "").startswith("c1")
                    ),
                    None,
                )
                if best_c3 is not None and best_c1 is not None:
                    mixed = [best_c3, best_c1]
                    mixed_ids = {item["storyVersionId"] for item in mixed}
                    mixed.extend(
                        item
                        for item in family_unique
                        if item["storyVersionId"] not in mixed_ids
                    )
                    family_unique = mixed
            corpus_candidates = family_unique[: request.limit]
            if len(corpus_candidates) < 2 and not refreshing_existing_offer:
                raise ServiceError(
                    503,
                    "c3_corpus_unavailable",
                    "Not enough eligible recommendation stories",
                )
            candidates = [
                self._public_story_card(
                    item,
                    confirmed_summary,
                    recommendation_override=rerank_by_id.get(item["storyVersionId"]),
                    retrieval_terms=retrieval_plan["terms"],
                    retrieval_plan_source=retrieval_plan_source,
                    rerank_source=rerank_source,
                )
                for item in corpus_candidates
            ]
            offered_ids = {item["storyVersionId"] for item in corpus_candidates}
            exhausted = self.corpus.remaining_recommendation_count(
                excluded | offered_ids,
                adult_content_opt_in=adult_content_enabled,
            ) == 0

            offer_id = str(uuid.uuid4())
            offer = {
                "id": offer_id,
                "offerId": offer_id,
                "offerIndex": len(session.story_offers) + 1,
                "candidates": candidates,
                "cards": candidates,
                "corpusVersion": session.corpus_version,
                "retrievalPlan": {
                    **retrieval_plan,
                    "source": retrieval_plan_source,
                },
                "rerankSource": rerank_source,
                "ragCandidateLimit": 8,
                "exhausted": exhausted,
                "canRejectAll": True,
                "canRefresh": not exhausted,
                "createdAt": utc_now(),
            }
            session.story_offers.append(offer)
            self._event(
                session,
                "story_offer_created",
                offerId=offer["offerId"],
                storyVersionIds=[item["storyVersionId"] for item in candidates],
                retrievalPlanSource=retrieval_plan_source,
                rerankSource=rerank_source,
            )
            return copy.deepcopy(offer)

    def select_story(
        self,
        session_id: str,
        request: StorySelectionCreate,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Story selection is disabled")
            if request.combine or request.story_version_ids:
                raise ServiceError(422, "story_combination_forbidden", "Select at most one story version")
            if session.branches:
                raise ServiceError(409, "branch_already_started", "Story selection is frozen after co-creation begins")

            if request.action == "refresh":
                offer = self.create_story_offer(session_id, StoryOfferCreate(refresh=True))
                return {"action": "refresh", "selection": None, "offer": offer}
            if request.action == "reject_all":
                session.selection = {
                    "action": "reject_all",
                    "storyVersionId": None,
                    "resonance": None,
                    "selectedAt": utc_now(),
                }
                session.source_snapshot = None
                session.source_canon_hash = None
                self._event(session, "story_offer_rejected")
                return {
                    "status": "rejected",
                    "action": "reject_all",
                    "selection": copy.deepcopy(session.selection),
                }

            story_version_id = (request.story_version_id or "").strip()
            if not story_version_id:
                raise ServiceError(422, "story_version_required", "storyVersionId is required")
            if not session.story_offers:
                raise ServiceError(409, "story_offer_missing", "Request a story offer before selecting")
            if request.offer_id is not None and request.offer_id != session.story_offers[-1]["offerId"]:
                raise ServiceError(409, "offer_id_conflict", "offerId does not match the latest offer")
            offered_ids = {
                candidate["storyVersionId"]
                for candidate in session.story_offers[-1]["candidates"]
            }
            if story_version_id not in offered_ids:
                raise ServiceError(422, "story_not_offered", "The story was not in the latest offer")

            record = self.corpus.get(story_version_id)
            if record is None:
                raise ServiceError(
                    409,
                    "story_no_longer_eligible",
                    "The story is no longer eligible for the demo recommendation pool",
                )
            if (
                (record.get("adultOnly") or record.get("requiresExplicitAdultOptIn"))
                and not self._adult_content_enabled(session)
            ):
                raise ServiceError(
                    403,
                    "adult_content_opt_in_required",
                    "This story requires adultConfirmed and a separate adultContentOptIn consent",
                )
            source_snapshot = copy.deepcopy(record["sourceCanon"])
            source_hash = stable_hash(source_snapshot)
            if source_hash != record["sourceCanonHash"]:
                raise ServiceError(500, "source_integrity_error", "Story source hash does not match")

            session.selection = {
                "action": "select",
                "storyVersionId": story_version_id,
                "resonance": request.resonance,
                "selectedAt": utc_now(),
            }
            session.source_snapshot = source_snapshot
            session.source_canon_hash = source_hash
            self._event(
                session,
                "story_selected",
                storyVersionId=story_version_id,
                sourceCanonHash=source_hash,
            )
            offered_card = next(
                (
                    candidate
                    for candidate in session.story_offers[-1]["candidates"]
                    if candidate["storyVersionId"] == story_version_id
                ),
                None,
            )
            return {
                "status": "selected",
                "action": "select",
                "selection": copy.deepcopy(session.selection),
                "selectedStory": (
                    copy.deepcopy(offered_card)
                    if offered_card is not None
                    else self._public_story_card(
                        record,
                        str(
                            self._confirmed_brief(session).get("confirmedSummary")
                            or self._confirmed_brief(session).get("neutralSummary")
                            or ""
                        ),
                    )
                ),
                "sourceCanon": copy.deepcopy(source_snapshot),
                "sourceCanonHash": source_hash,
                "adaptationBoundary": copy.deepcopy(record["adaptationBoundary"]),
            }

    @staticmethod
    def _check_source_integrity(session: SessionState) -> None:
        if session.source_snapshot is None or session.source_canon_hash is None:
            raise ServiceError(409, "story_not_selected", "Select exactly one story before co-creation")
        if stable_hash(session.source_snapshot) != session.source_canon_hash:
            raise ServiceError(500, "source_canon_mutated", "The immutable source canon changed")

    @staticmethod
    def _check_branch_parent(
        session: SessionState,
        parent_version: int | None,
        if_match: str | None,
    ) -> None:
        latest = session.branches[-1] if session.branches else None
        if latest is None:
            if parent_version not in (None, 0):
                raise ServiceError(409, "parent_version_conflict", "The first branch has no parent version")
            if if_match not in (None, "", '"0"'):
                raise ServiceError(409, "etag_conflict", "If-Match does not match an empty branch")
            return
        if parent_version != latest["branchVersion"]:
            raise ServiceError(
                409,
                "parent_version_conflict",
                f"Expected parentVersion {latest['branchVersion']}",
            )
        if if_match is not None and if_match != latest["etag"]:
            raise ServiceError(409, "etag_conflict", "If-Match does not match the latest branch ETag")

    def _selected_story_context(self, session: SessionState) -> dict[str, Any]:
        """Build a bounded mapping context without mutating the source snapshot.

        The immutable snapshot deliberately contains only source-canon fields.
        Mapping quality also benefits from the selected card's plot summary and
        motifs, so join those fields at call time and keep the stored source hash
        unchanged.
        """

        context = copy.deepcopy(session.source_snapshot or {})
        story_version_id = str((session.selection or {}).get("storyVersionId") or "")
        record = self.corpus.get(story_version_id) if story_version_id else None
        if record is not None:
            card = record.get("storyCard") or {}
            context.setdefault("title", str(record.get("title") or ""))
            if not str(context.get("summary") or "").strip():
                context["summary"] = str(_mapping_value(card, "summary", default=""))
            if not _as_list(context.get("motifs")):
                context["motifs"] = [
                    str(value)
                    for value in _as_list(_mapping_value(card, "motifs", default=[]))[:8]
                ]
        if not str(
            _mapping_value(context, "originalEnding", "original_ending", default="")
        ).strip():
            ending = str(_mapping_value(context, "ending", default="")).strip()
            if ending:
                context["originalEnding"] = ending
        return context

    def _deterministic_mapping_draft(
        self,
        session: SessionState,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        brief = self._confirmed_brief(session)
        user_summary = str(
            brief.get("confirmedSummary") or brief.get("neutralSummary") or "用户正在面对一次变化。"
        )
        source = self._selected_story_context(session)
        source_title = str(
            _mapping_value(source, "work", "sourceTitle", "title", default="所选故事")
        )
        source_summary = _compact_evidence_text(
            str(
                _mapping_value(
                    source,
                    "summary",
                    "excerpt",
                    "originalExcerpt",
                    "original_excerpt",
                    default="",
                )
            ),
            limit=180,
        )
        values = [
            f"用户确认的处境是：{user_summary}。在《{source_title}》中，可以先从“原有秩序如何被打破”这一处开始比较。",
            f"映照的第二步不是照搬原典，而是辨认用户愿意尝试、拒绝或暂缓的选择；原典线索是：{source_summary}",
            "可把现实中的人、已有经验和可调用资源放在这里，与故事中的助力或阻力分别对应，并保留不确定之处。",
            "新的理解暂定为：变化不只要求得出结论，也可能要求调整路径、边界或行动顺序；这仍由用户修改确认。",
            "带回现实的内容先写成一个小而可改的可能性：保留自己的判断，并选择下一步能够承受的行动。",
        ]
        nodes = []
        for (node_id, title, prompt), value in zip(_NODE_DEFINITIONS, values):
            nodes.append(
                {
                    "id": node_id,
                    "title": title,
                    "prompt": prompt,
                    "value": value,
                    "skipped": False,
                    "suggestions": [],
                    "expressionOrigin": "model_edited",
                }
            )
        return nodes, {
            "type": "action",
            "detail": "先保留自己的判断，再完成一个能够承受、可以修改的小步骤。",
            "text": "先保留自己的判断，再完成一个能够承受、可以修改的小步骤。",
        }

    def _auto_mapping_draft(
        self,
        session: SessionState,
    ) -> tuple[list[dict[str, Any]], dict[str, str], str, bool]:
        fallback_nodes, fallback_anchor = self._deterministic_mapping_draft(session)
        if session.consent.get("cloudProcessingAccepted") is not True:
            return fallback_nodes, fallback_anchor, "deterministic_mapping_draft", False
        brief = self._confirmed_brief(session)
        user_summary = str(
            brief.get("confirmedSummary") or brief.get("neutralSummary") or ""
        )
        try:
            generated = self.model_adapter.generate_mapping_draft(
                user_summary=user_summary,
                source_canon=self._selected_story_context(session),
            )
            raw_nodes = generated.get("nodes", [])
            raw_anchor = generated.get("hopeAnchor", {})
            by_id = {
                str(item.get("nodeId") or ""): str(item.get("value") or "").strip()
                for item in raw_nodes
                if isinstance(item, dict)
            }
            if set(by_id) != set(_NODE_NUMBER):
                raise ModelUnavailable("Incomplete mapping draft")
            nodes = []
            for node_id, title, prompt in _NODE_DEFINITIONS:
                value = by_id[node_id]
                if not value:
                    raise ModelUnavailable("Empty mapping node")
                nodes.append(
                    {
                        "id": node_id,
                        "title": title,
                        "prompt": prompt,
                        "value": value,
                        "skipped": False,
                        "suggestions": [],
                        "expressionOrigin": "model_edited",
                    }
                )
            anchor_type = str(raw_anchor.get("type") or "")
            anchor_detail = str(
                raw_anchor.get("detail") or raw_anchor.get("text") or ""
            ).strip()
            if anchor_type not in {"action", "relationship", "meaning", "open"} or not anchor_detail:
                raise ModelUnavailable("Invalid hope anchor")
            is_live_deepseek = (
                type(self.model_adapter) is DeepSeekAdapter
                and getattr(getattr(self.model_adapter, "config", None), "available", False) is True
            )
            return (
                nodes,
                {"type": anchor_type, "detail": anchor_detail, "text": anchor_detail},
                "deepseek_mapping_draft" if is_live_deepseek else "model_adapter_mapping_draft",
                True,
            )
        except (ModelUnavailable, AttributeError, TypeError, ValueError):
            return fallback_nodes, fallback_anchor, "deterministic_mapping_draft", False

    @staticmethod
    def _should_auto_draft(latest: dict[str, Any] | None, request: BranchWrite) -> bool:
        if latest is not None or request.node_updates:
            return False
        if request.nodes is None or len(request.nodes) == 0:
            return True
        return all(not str(node.value or "").strip() for node in request.nodes)

    def _suggestion_story_context(self, session: SessionState) -> dict[str, Any]:
        """Flatten the selected story into the few fields a suggestion needs."""

        source = self._selected_story_context(session)

        def join(value: Any) -> str:
            if isinstance(value, (list, tuple)):
                return "、".join(str(item) for item in value if str(item).strip())
            return str(value or "")

        return {
            "title": str(_mapping_value(source, "title", "work", "sourceTitle", default="")),
            "summary": _compact_evidence_text(
                str(_mapping_value(source, "summary", "excerpt", "originalExcerpt", default="")),
                limit=320,
            ),
            "conflict": str(_mapping_value(source, "conflict", default="")),
            "characters": join(_mapping_value(source, "characters", default=[])),
            "motifs": join(_mapping_value(source, "motifs", default=[])),
            "originalEnding": str(_mapping_value(source, "originalEnding", default="")),
        }

    def _fallback_suggestions(
        self,
        node_number: int | None,
        *,
        allow_external_model: bool = False,
        session: SessionState | None = None,
    ) -> tuple[list[str], str | None]:
        if node_number is None:
            return [], None
        if not allow_external_model:
            return list(_FALLBACK_SUGGESTIONS[node_number]), "deterministic_fallback"
        try:
            node_id, node_title, node_prompt = _NODE_DEFINITIONS[node_number - 1]
            story_context: dict[str, Any] = {}
            user_summary = ""
            if session is not None:
                story_context = self._suggestion_story_context(session)
                brief = self._confirmed_brief(session)
                user_summary = str(
                    brief.get("confirmedSummary") or brief.get("neutralSummary") or ""
                )
            suggestions = self.model_adapter.suggest(
                node_number=node_number,
                node_title=node_title,
                node_prompt=node_prompt,
                story_context=story_context,
                user_summary=user_summary,
            )
            is_live_deepseek = (
                type(self.model_adapter) is DeepSeekAdapter
                and getattr(getattr(self.model_adapter, "config", None), "available", False) is True
            )
            return list(suggestions)[:2], "deepseek" if is_live_deepseek else "model_adapter"
        except ModelUnavailable:
            return list(_FALLBACK_SUGGESTIONS[node_number]), "deterministic_fallback"

    @staticmethod
    def _default_branch_nodes() -> list[dict[str, Any]]:
        return [
            {
                "id": node_id,
                "title": title,
                "prompt": prompt,
                "value": "",
                "skipped": False,
                "suggestions": [],
                "expressionOrigin": None,
            }
            for node_id, title, prompt in _NODE_DEFINITIONS
        ]

    def _branch_nodes(
        self,
        latest: dict[str, Any] | None,
        request: BranchWrite,
    ) -> list[dict[str, Any]]:
        current = copy.deepcopy(latest["nodes"]) if latest else self._default_branch_nodes()
        by_id = {node["id"]: node for node in current}
        if request.nodes is not None:
            seen: set[str] = set()
            for node in request.nodes:
                if node.id in seen:
                    raise ServiceError(422, "duplicate_branch_node", f"Duplicate node {node.id}")
                seen.add(node.id)
                candidate = node.model_dump(mode="json", by_alias=True)
                baseline = by_id[node.id]
                by_id[node.id] = {**baseline, **candidate}

        for key, update in request.node_updates.items():
            node_id, _, _ = _NODE_DEFINITIONS[int(key) - 1]
            baseline = by_id[node_id]
            data = update.model_dump(mode="json", by_alias=True)
            by_id[node_id] = {
                **baseline,
                "value": data.get("text") or "",
                "skipped": data.get("skipped", False),
                "expressionOrigin": (
                    "model_edited" if data.get("contribution") == "model_expression" else "user"
                ),
            }
        return [by_id[node_id] for node_id, _, _ in _NODE_DEFINITIONS]

    @staticmethod
    def _branch_ready(nodes: list[dict[str, Any]], hope_anchor: dict[str, Any] | None) -> bool:
        addressed = [
            node
            for node in nodes
            if node.get("skipped") is True or str(node.get("value") or "").strip()
        ]
        written = [
            node
            for node in nodes
            if node.get("skipped") is not True and str(node.get("value") or "").strip()
        ]
        hope_text = "" if not isinstance(hope_anchor, dict) else str(
            hope_anchor.get("detail") or hope_anchor.get("text") or ""
        ).strip()
        hope_type = None if not isinstance(hope_anchor, dict) else hope_anchor.get("type")
        return (
            len(nodes) == len(_NODE_DEFINITIONS)
            and len(addressed) == len(_NODE_DEFINITIONS)
            and len(written) >= 2
            and hope_type in {"action", "relationship", "meaning", "open"}
            and bool(hope_text)
        )

    def suggest_branch(
        self,
        session_id: str,
        request: BranchWrite,
        if_match: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            self._check_source_integrity(session)
            if not session.branches:
                raise ServiceError(409, "branch_missing", "Create a branch before requesting suggestions")
            latest = session.branches[-1]
            if request.branch_version_id is not None and request.branch_version_id != latest["id"]:
                raise ServiceError(409, "branch_version_conflict", "branchVersionId is stale")
            self._check_branch_parent(session, request.parent_version, if_match)
            assistant_message = str(request.assistant_message or "").strip()
            if assistant_message:
                safety = route_text(assistant_message)
                if safety.blocked:
                    session.safety_route = {
                        "blocked": True,
                        "route": safety.route,
                        "categories": list(safety.categories),
                        "blockedAt": utc_now(),
                        "support": {
                            "chinaMentalHealthHotline": "12356",
                            "emergency": ["110", "120"],
                            "realTimeMonitoring": False,
                        },
                    }
                    self._event(
                        session,
                        "safety_route_triggered",
                        categories=list(safety.categories),
                        rawTextStored=False,
                    )
                    raise ServiceError(403, "safety_blocked", "Mapping assistance is disabled")

                updates: list[dict[str, Any]] = []
                source = "deterministic_fallback"
                if session.consent.get("cloudProcessingAccepted") is True:
                    brief = self._confirmed_brief(session)
                    try:
                        raw_updates = self.model_adapter.revise_mapping(
                            user_message=assistant_message,
                            user_summary=str(
                                brief.get("confirmedSummary")
                                or brief.get("neutralSummary")
                                or ""
                            ),
                            source_canon=self._selected_story_context(session),
                            nodes=copy.deepcopy(latest["nodes"]),
                        )
                        for item in raw_updates:
                            node_id = str(item.get("nodeId") or "")
                            value = str(item.get("value") or "").strip()
                            if node_id in _NODE_NUMBER and value:
                                updates.append(
                                    {
                                        "nodeId": node_id,
                                        "value": value,
                                        "rationale": str(item.get("rationale") or ""),
                                        "contribution": "model_expression",
                                    }
                                )
                        if updates:
                            is_live_deepseek = (
                                type(self.model_adapter) is DeepSeekAdapter
                                and getattr(
                                    getattr(self.model_adapter, "config", None),
                                    "available",
                                    False,
                                )
                                is True
                            )
                            source = "deepseek" if is_live_deepseek else "model_adapter"
                    except (ModelUnavailable, AttributeError, TypeError, ValueError):
                        updates = []
                self._event(
                    session,
                    "mapping_assistant_returned",
                    branchVersion=latest["version"],
                    suggestionSource=source,
                    nodeIds=[item["nodeId"] for item in updates],
                    rawTextStored=False,
                )
                return {
                    "branch": copy.deepcopy(latest),
                    "assistantMessage": (
                        "已根据你的说明整理出可预览的节点修改；保存前仍可逐项确认。"
                        if updates
                        else "当前未调用外部模型；你仍可选择节点并使用本地建议，或直接手动修改。"
                    ),
                    "nodeUpdates": updates,
                    "suggestions": [],
                    "suggestionSource": source,
                    "modelGenerated": bool(updates),
                }
            node_id = request.node_id
            node_number = _NODE_NUMBER.get(node_id) if node_id else request.request_suggestions_for
            if node_number is None:
                raise ServiceError(422, "node_id_required", "nodeId is required for suggestions")
            suggestions, source = self._fallback_suggestions(
                node_number,
                allow_external_model=session.consent.get("cloudProcessingAccepted") is True,
                session=session,
            )
            self._event(
                session,
                "branch_suggestions_returned",
                branchVersion=latest["version"],
                nodeId=node_id or _NODE_DEFINITIONS[node_number - 1][0],
                suggestionSource=source,
            )
            return {
                "branch": copy.deepcopy(latest),
                "nodeId": node_id or _NODE_DEFINITIONS[node_number - 1][0],
                "suggestions": suggestions,
                "suggestionSource": source,
            }

    def write_branch(
        self,
        session_id: str,
        request: BranchWrite,
        if_match: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Co-creation is disabled")
            self._check_source_integrity(session)
            self._check_branch_parent(session, request.parent_version, if_match)

            latest = session.branches[-1] if session.branches else None
            selected_story_version_id = session.selection["storyVersionId"] if session.selection else None
            if (
                request.selected_story_version_id is not None
                and request.selected_story_version_id != selected_story_version_id
            ):
                raise ServiceError(409, "selected_story_conflict", "selectedStoryVersionId changed")
            if latest and request.branch_version_id is not None and request.branch_version_id != latest["id"]:
                raise ServiceError(409, "branch_version_conflict", "branchVersionId is stale")
            auto_draft = self._should_auto_draft(latest, request)
            generated_anchor: dict[str, str] | None = None
            generation_mode = "human_authored"
            model_generated = False
            if auto_draft:
                nodes, generated_anchor, generation_mode, model_generated = self._auto_mapping_draft(
                    session
                )
            else:
                nodes = self._branch_nodes(latest, request)
                if latest is not None:
                    generation_mode = "human_refined"

            hope_anchor = (
                request.hope_anchor.model_dump(mode="json", by_alias=True)
                if request.hope_anchor is not None
                else copy.deepcopy(generated_anchor)
                if generated_anchor is not None
                else copy.deepcopy(latest.get("hopeAnchor"))
                if latest
                else None
            )
            if hope_anchor is not None:
                detail = hope_anchor.get("detail") or hope_anchor.get("text")
                hope_anchor["detail"] = detail
                hope_anchor["text"] = detail
            branch_version = len(session.branches) + 1
            node_number = (
                _NODE_NUMBER.get(request.node_id)
                if request.node_id is not None
                else request.request_suggestions_for
            )
            suggestions, suggestion_source = self._fallback_suggestions(
                node_number,
                allow_external_model=session.consent.get("cloudProcessingAccepted") is True,
                session=session,
            )
            if node_number is not None:
                nodes[node_number - 1]["suggestions"] = suggestions
            preview = request.preview or " ".join(
                node["value"].strip()
                for node in nodes
                if not node["skipped"] and node["value"].strip()
            )
            branch_id = f"branch-v{branch_version}"
            branch_content = {
                "id": branch_id,
                "version": branch_version,
                "branchVersion": branch_version,
                "parentVersion": latest["branchVersion"] if latest else None,
                "parentVersionId": latest["id"] if latest else None,
                "storyVersionId": selected_story_version_id,
                "selectedStoryVersionId": selected_story_version_id,
                "nodes": nodes,
                "hopeAnchor": hope_anchor,
                "preview": preview,
                "readyForApproval": self._branch_ready(nodes, hope_anchor),
                "sourceCanonHash": session.source_canon_hash,
                "generationMode": generation_mode,
                "modelGenerated": model_generated,
            }
            etag = f'"{stable_hash(branch_content)[:24]}"'
            branch = {
                **branch_content,
                "status": "draft",
                "etag": etag,
                "suggestions": suggestions,
                "suggestionSource": suggestion_source,
                "createdAt": utc_now(),
            }
            session.branches.append(branch)
            self._event(
                session,
                "branch_version_created",
                branchVersion=branch_version,
                parentVersion=branch["parentVersion"],
                sourceCanonHash=session.source_canon_hash,
                generationMode=generation_mode,
                modelGenerated=model_generated,
            )
            self._check_source_integrity(session)
            return copy.deepcopy(branch)

    def approve_branch(
        self,
        session_id: str,
        branch_identifier: str | int,
        if_match: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            self._check_source_integrity(session)
            if not session.branches:
                raise ServiceError(404, "branch_not_found", "Branch version does not exist")
            latest = session.branches[-1]
            identifier = str(branch_identifier)
            if identifier not in {latest["id"], str(latest["branchVersion"])}:
                raise ServiceError(409, "stale_branch_approval", "Only the latest branch can be approved")
            if if_match is not None and if_match != latest["etag"]:
                raise ServiceError(409, "etag_conflict", "If-Match does not match the branch ETag")
            if not self._branch_ready(latest["nodes"], latest.get("hopeAnchor")):
                raise ServiceError(
                    409,
                    "branch_not_ready",
                    "Address every branch node, write at least two nodes, and add a hope anchor",
                )

            latest["status"] = "approved"
            latest["approvedAt"] = utc_now()
            session.approved_branch_version = latest["branchVersion"]
            self._event(session, "branch_approved", branchVersion=latest["branchVersion"])
            return {
                **copy.deepcopy(latest),
                "approvedVersion": latest["branchVersion"],
            }

    @staticmethod
    def _node_text(branch: dict[str, Any], *numbers: int) -> str:
        fragments: list[str] = []
        for number in numbers:
            node = branch["nodes"][number - 1]
            if node.get("skipped"):
                continue
            text = (node.get("value") or "").strip()
            if text:
                fragments.append(text)
        return " ".join(fragments)

    def create_theatre_script(
        self,
        session_id: str,
        request: TheatreScriptCreate,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Theatre generation is disabled")
            self._check_source_integrity(session)
            approved_version = session.approved_branch_version
            if approved_version is None:
                raise ServiceError(409, "branch_not_approved", "Approve a branch before compiling theatre")
            approved_branch_id = f"branch-v{approved_version}"
            requested_version = request.branch_version or approved_version
            if request.branch_version_id is not None and request.branch_version_id != approved_branch_id:
                raise ServiceError(409, "branch_not_approved", "Only the approved branch may enter theatre")
            if requested_version != approved_version:
                raise ServiceError(409, "branch_not_approved", "Only the approved branch may enter theatre")
            branch = next(
                (item for item in session.branches if item["branchVersion"] == approved_version),
                None,
            )
            if branch is None or branch.get("status") != "approved":
                raise ServiceError(409, "branch_not_approved", "Approved branch state is inconsistent")

            selected = self.corpus.get(session.selection["storyVersionId"]) if session.selection else None
            story_title = _canonical_story_title(selected) if selected else "现代神话"

            hope_anchor = branch.get("hopeAnchor") or {}
            active_nodes = [
                node
                for node in branch["nodes"]
                if not node.get("skipped") and str(node.get("value") or "").strip()
            ]
            if len(active_nodes) < 2:
                raise ServiceError(
                    409,
                    "branch_not_ready",
                    "At least two approved mapping nodes are required for theatre",
                )
            acts: list[dict[str, Any]] = []

            def append_act(
                *,
                title: str,
                narration: str,
                mood: str,
                source_node_ids: list[str],
                stage_direction: str,
            ) -> None:
                index = len(acts) + 1
                acts.append(
                    {
                        "id": f"act-{index}",
                        "actNumber": index,
                        "title": title,
                        "narration": narration,
                        "subtitle": narration,
                        "stageDirection": stage_direction,
                        "durationSeconds": max(20, min(40, 18 + len(narration) // 12)),
                        "setting": "paper-shadow-stage",
                        "mood": mood,
                        "assetRefs": [f"stage:{mood}"],
                        "sourceNodeIds": source_node_ids,
                    }
                )

            hope_text = str(hope_anchor.get("detail") or hope_anchor.get("text") or "").strip()
            moods = ["opening", "threshold", "choice", "allies", "turning", "return", "closing"]
            composition_source = "deterministic_fallback"

            # Preferred path: the model folds the classical characters and
            # imagery into the user's modern situation as one continuous story.
            if session.consent.get("cloudProcessingAccepted") is True:
                compose = getattr(self.model_adapter, "compose_theatre", None)
                if callable(compose):
                    try:
                        woven = compose(
                            story_context=self._suggestion_story_context(session),
                            user_summary=str(
                                self._confirmed_brief(session).get("confirmedSummary")
                                or self._confirmed_brief(session).get("neutralSummary")
                                or ""
                            ),
                            nodes=copy.deepcopy(active_nodes),
                            hope_text=hope_text,
                        )
                        node_ids = [str(node.get("id")) for node in active_nodes]
                        for position, act in enumerate(woven):
                            append_act(
                                title=str(act.get("title") or f"第 {position + 1} 幕"),
                                narration=str(act.get("narration") or "").strip(),
                                mood=moods[min(position, len(moods) - 1)],
                                source_node_ids=node_ids,
                                stage_direction=str(act.get("stageDirection") or ""),
                            )
                        is_live_deepseek = (
                            type(self.model_adapter) is DeepSeekAdapter
                            and getattr(
                                getattr(self.model_adapter, "config", None), "available", False
                            )
                            is True
                        )
                        composition_source = (
                            "deepseek" if is_live_deepseek else "model_adapter"
                        )
                    except (ModelUnavailable, AttributeError, TypeError, ValueError):
                        acts = []

            if not acts:
                append_act(
                    title="序幕 · 幕布拉开",
                    narration=(
                        f"幕布拉开。《{story_title}》里的人物走进你写下的这段日子，"
                        "接下来的每一幕都由你确认过的内容展开。"
                    ),
                    mood="opening",
                    source_node_ids=[],
                    stage_direction="幕布缓缓拉开，先见人物身影，再转入现代场景。",
                )
                for node in active_nodes:
                    node_number = _NODE_NUMBER.get(str(node.get("id") or ""), len(acts))
                    append_act(
                        title=str(node.get("title") or f"映照节点 {node_number}"),
                        narration=str(node.get("value") or "").strip(),
                        mood=moods[min(max(node_number, 1), len(moods) - 1)],
                        source_node_ids=[str(node.get("id"))],
                        stage_direction="保留当前节点的用户措辞，以画面与字幕呈现，不补写用户未确认的事实。",
                    )
                append_act(
                    title="尾声 · 回到此刻",
                    narration=(
                        f"故事在这里停下。你为现实留下这一句：{hope_text}"
                        if hope_text
                        else "故事在这里停下，接下来仍由你决定。"
                    ),
                    mood="closing",
                    source_node_ids=[],
                    stage_direction="灯光收束，字幕保留最后一句，幕布在用户确认后合上。",
                )

            script_version = len(session.theatre_scripts) + 1
            script_id = f"theatre-v{script_version}"
            
            script = {
                "id": script_id,
                "version": script_version,
                "theatreScriptVersion": script_version,
                "branchVersion": approved_version,
                "branchVersionId": approved_branch_id,
                "storyVersionId": session.selection["storyVersionId"] if session.selection else None,
                "title": f"{story_title} · 现代支线剧场",
                "sourceCanonHash": session.source_canon_hash,
                "acts": acts,
                "totalDurationSeconds": sum(act["durationSeconds"] for act in acts),
                "finalLineSuggestions": list(_FALLBACK_SUGGESTIONS[5]),
                "renderMode": "canvas_svg_css",
                "mediaMode": "reviewed_assets",
                "generationMode": (
                    "model_woven_narrative"
                    if composition_source != "deterministic_fallback"
                    else "dynamic_mapping_node_compiler"
                ),
                "compositionSource": composition_source,
                "modelGenerated": composition_source != "deterministic_fallback",
                "status": "compiled",
                "createdAt": utc_now(),
            }
            session.theatre_scripts.append(script)
            self._event(
                session,
                "theatre_script_compiled",
                theatreScriptVersion=script_version,
                branchVersion=approved_version,
                actCount=len(acts),
                generationMode=(
                    "model_woven_narrative"
                    if composition_source != "deterministic_fallback"
                    else "dynamic_mapping_node_compiler"
                ),
                compositionSource=composition_source,
            )
            self._check_source_integrity(session)
            return copy.deepcopy(script)

    def create_scene_image(
        self,
        session_id: str,
        script_id: str,
        act_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Scene image generation is disabled")
            self._check_source_integrity(session)
            script = next(
                (item for item in session.theatre_scripts if item.get("id") == script_id),
                None,
            )
            if script is None or script.get("status") != "compiled":
                raise ServiceError(404, "theatre_script_not_found", "Theatre script does not exist")
            act = next(
                (item for item in script.get("acts", []) if item.get("id") == act_id),
                None,
            )
            if act is None:
                raise ServiceError(404, "theatre_act_not_found", "Theatre act does not exist")
            source = session.source_snapshot or {}
            scene_fields = {
                "scene_title": str(act.get("title") or "这一幕"),
                "narration": str(act.get("narration") or ""),
                "stage_direction": str(act.get("stageDirection") or ""),
                "story_title": str(script.get("title") or "中国古典神话传说的现代支线"),
                "source_title": str(
                    _mapping_value(
                        source,
                        "work",
                        "sourceTitle",
                        "title",
                        default="所选古籍",
                    )
                ),
            }
            cloud_allowed = session.consent.get("cloudProcessingAccepted") is True

        # Never hold the session lock while waiting for an image provider.
        if cloud_allowed:
            result = self.image_adapter.generate_scene(
                **scene_fields,
            )
        else:
            result = SceneImageResult.fallback(
                alt_text=f"{scene_fields['scene_title']}的本地纸影舞台画面",
                retryable=False,
                reason="cloud_consent_required",
                message="未同意云端处理，本幕继续使用本地纸影舞台。",
            )
        payload = {
            "scriptId": script_id,
            **result.public_payload(act_id=act_id),
            "createdAt": utc_now(),
        }
        with self._lock:
            current_session = self._sessions.get(session_id)
            if current_session is not None:
                self._event(
                    current_session,
                    "scene_image_returned",
                    theatreScriptId=script_id,
                    actId=act_id,
                    imageStatus=payload["status"],
                )
        return payload

    def create_act_narration(
        self,
        session_id: str,
        script_id: str,
        act_id: str,
    ) -> dict[str, Any]:
        """Synthesise one act's narration; never blocks playback on failure."""

        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Narration is disabled")
            self._check_source_integrity(session)
            script = next(
                (item for item in session.theatre_scripts if item.get("id") == script_id),
                None,
            )
            if script is None or script.get("status") != "compiled":
                raise ServiceError(404, "theatre_script_not_found", "Theatre script does not exist")
            act = next(
                (item for item in script.get("acts", []) if item.get("id") == act_id),
                None,
            )
            if act is None:
                raise ServiceError(404, "theatre_act_not_found", "Theatre act does not exist")
            narration = str(act.get("narration") or "")
            cloud_allowed = session.consent.get("cloudProcessingAccepted") is True

        # Never hold the session lock while waiting for the speech provider.
        if cloud_allowed:
            result = self.tts_adapter.synthesize(narration=narration)
        else:
            result = NarrationResult.fallback(
                retryable=False,
                reason="cloud_consent_required",
                message="未同意云端处理，本幕继续使用字幕。",
            )
        payload = {
            "scriptId": script_id,
            **result.public_payload(act_id=act_id),
            "createdAt": utc_now(),
        }
        with self._lock:
            current_session = self._sessions.get(session_id)
            if current_session is not None:
                self._event(
                    current_session,
                    "act_narration_returned",
                    theatreScriptId=script_id,
                    actId=act_id,
                    narrationStatus=payload["status"],
                )
        return payload

    def create_ritual_action(
        self,
        session_id: str,
        request: RitualActionCreate,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.safety_route["blocked"]:
                raise ServiceError(403, "safety_blocked", "Ritual actions are disabled")
            self._check_source_integrity(session)
            if not session.theatre_scripts:
                raise ServiceError(409, "theatre_missing", "Compile the theatre script first")

            selected = self.corpus.get(session.selection["storyVersionId"]) if session.selection else None
            default_title = f"{selected['title']}·现代支线" if selected else "我们的现代神话"
            value = (request.value or "").strip() or None
            latest_script = session.theatre_scripts[-1]
            if (
                request.theatre_script_id is not None
                and request.theatre_script_id != latest_script["id"]
            ):
                raise ServiceError(409, "theatre_script_conflict", "theatreScriptId is stale")
            action_type = request.action_type or (
                "stamp_card" if request.ritual_gesture == "seal" else "light_finale"
            )
            ritual_gesture = request.ritual_gesture or (
                "seal" if action_type == "stamp_card" else "light"
            )
            saved = False if request.save_preference == "delete" else (
                request.save_artifact
                if request.save_artifact is not None
                else request.save_preference == "save"
            )
            artifact_id = str(uuid.uuid4())
            artifact = {
                "id": artifact_id,
                "title": request.story_title or (value if action_type == "name_story" else default_title),
                "finalLine": request.final_line or (value if action_type == "last_line" else ""),
                "ritualGesture": ritual_gesture,
                "saved": saved,
                "createdAt": utc_now(),
                "storyVersionId": session.selection["storyVersionId"] if session.selection else None,
                "branchVersion": session.approved_branch_version,
                "theatreScriptVersion": latest_script["theatreScriptVersion"],
                "savePreference": request.save_preference,
            }
            action = {
                "ritualActionId": str(uuid.uuid4()),
                "actionType": action_type,
                "value": value,
                "completed": True,
                "artifact": artifact,
                "createdAt": utc_now(),
            }
            if saved:
                session.ritual_actions.append(action)
                self._event(
                    session,
                    "ritual_action_completed",
                    ritualActionId=action["ritualActionId"],
                    actionType=action_type,
                )
            return {**copy.deepcopy(artifact), **copy.deepcopy(action)}

    def provenance(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.source_snapshot is not None:
                self._check_source_integrity(session)
            briefs = [
                {
                    "briefVersion": item["briefVersion"],
                    "status": item["status"],
                    "confirmed": item.get("confirmed", False),
                    "summary": item.get("confirmedSummary") or item.get("draftSummary"),
                    "presetId": item.get("presetId"),
                }
                for item in session.experience_briefs
            ]
            provenance_entries: list[dict[str, Any]] = []
            if session.source_snapshot is not None:
                source_title = str(
                    _mapping_value(session.source_snapshot, "work", "source_title", "sourceTitle", default="原典")
                )
                provenance_entries.append(
                    {
                        "id": "source-canon",
                        "segment": "source_canon",
                        "text": str(
                            _mapping_value(
                                session.source_snapshot,
                                "summary",
                                "original_excerpt",
                                "originalExcerpt",
                                default="",
                            )
                        ),
                        "origin": "source_canon",
                        "sourceTitle": source_title,
                        "sourceLocator": _mapping_value(
                            session.source_snapshot,
                            "evidence_anchor",
                            "evidenceAnchor",
                        ),
                    }
                )
            provenance_branch = None
            if session.approved_branch_version is not None:
                provenance_branch = next(
                    (
                        branch
                        for branch in session.branches
                        if branch["branchVersion"] == session.approved_branch_version
                    ),
                    None,
                )
            elif session.branches:
                provenance_branch = session.branches[-1]
            for node in provenance_branch["nodes"] if provenance_branch else []:
                if node.get("skipped") or not node.get("value"):
                    continue
                provenance_entries.append(
                    {
                        "id": f"{provenance_branch['id']}:{node['id']}",
                        "segment": node["id"],
                        "text": node["value"],
                        "origin": (
                            "model_expression"
                            if node.get("expressionOrigin") == "model_edited"
                            else "user_created"
                        ),
                    }
                )
            public_source = None
            if session.selection and session.selection.get("storyVersionId"):
                selected = self.corpus.get(session.selection["storyVersionId"])
                public_source = self._public_story_card(selected)["sourceCanon"] if selected else None
            deepseek_suggestion_was_returned = any(
                branch.get("suggestionSource") == "deepseek" for branch in session.branches
            ) or any(
                event.get("suggestionSource") == "deepseek"
                for event in session.provenance_events
            )
            model_expression_was_adopted = any(
                node.get("expressionOrigin") == "model_edited"
                for branch in session.branches
                for node in branch.get("nodes", [])
            )
            adopted_deepseek_model = (
                self.model_adapter.config.model
                if deepseek_suggestion_was_returned and model_expression_was_adopted
                else None
            )
            return {
                "sessionId": session.session_id,
                "corpusVersion": session.corpus_version,
                "modelVersion": adopted_deepseek_model,
                "selectedStoryVersionId": (
                    session.selection.get("storyVersionId") if session.selection else None
                ),
                "sourceCanon": public_source,
                "sourceCanonHash": session.source_canon_hash,
                "entries": provenance_entries,
                "experienceBriefVersions": briefs,
                "branchVersions": copy.deepcopy(session.branches),
                "theatreScriptVersions": copy.deepcopy(session.theatre_scripts),
                "ritualActions": copy.deepcopy(session.ritual_actions),
                "safetyRoute": copy.deepcopy(session.safety_route),
                "events": copy.deepcopy(session.provenance_events),
            }

    def delete_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            del self._sessions[session.session_id]
            self._purge_session_idempotency(session.session_id)
            deleted_at = self._now()
            tombstone = {
                "sessionId": session.session_id,
                "deleted": True,
                "deletedAt": deleted_at,
                "deletionProof": stable_hash(
                    {"sessionId": session.session_id, "deletedAt": deleted_at, "state": "deleted"}
                ),
            }
            self._tombstones[session.session_id] = copy.deepcopy(tombstone)
            return tombstone
