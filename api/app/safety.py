"""Deterministic high-precision crisis routing for the development slice."""

from __future__ import annotations

import re
from dataclasses import dataclass


_SAFE_NEGATIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:我\s*)?不想死(?:亡)?", re.IGNORECASE),
    re.compile(r"(?:不要|别)\s*(?:去)?\s*(?:自杀|伤害自己|割腕|跳楼)", re.IGNORECASE),
    re.compile(r"\bi\s+(?:do\s+not|don't)\s+want\s+to\s+die\b", re.IGNORECASE),
    re.compile(r"\b(?:do\s+not|don't)\s+(?:kill|hurt)\s+yourself\b", re.IGNORECASE),
)

_SELF_HARM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:我|本人|自己)\s*(?:现在|马上|今晚|今天|待会儿|一会儿|已经|真的|一直|总是|可能)?\s*"
        r"(?:很|就|正(?:在)?|忍不住)?\s*(?:想|要|准备|打算|决定|计划|会)\s*(?:去)?\s*"
        r"(?:自杀|割腕|跳楼|服毒|结束(?:我|自己)?的?生命|伤害自己|去死|死(?:了|掉)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:最近|现在|今晚|今天|此刻|有时|有时候)\s*(?:真的|一直|总是|突然|很)?\s*"
        r"(?:想|要|准备|打算|决定|计划|忍不住想)\s*(?:去)?\s*"
        r"(?:自杀|割腕|跳楼|服毒|结束生命|伤害自己|去死|死(?:了|掉)?)",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(?:真的|很)?\s*(?:想死|想自杀|不想活(?:了|下去)?|不如死(?:了|掉)?算了)", re.IGNORECASE),
    re.compile(r"(?:我|最近|现在|有时|有时候)\s*(?:真的|已经|一直|很)?\s*不想活(?:了|下去)?", re.IGNORECASE),
    re.compile(r"(?:我觉得|我感觉|最近|现在|^\s*)\s*活着\s*(?:没|没有|毫无)\s*(?:意思|意义|希望)", re.IGNORECASE),
    re.compile(
        r"(?:(?:我|本人)\s*(?:现在|马上|今晚|今天)?|(?:最近|现在|今晚|今天|此刻)\s*|^\s*)"
        r"(?:想|要|准备|打算|决定|计划)\s*(?:从|往)\s*.{0,8}(?:楼|桥|高处).{0,6}跳",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[，。；;、但却])\s*(?:我\s*)?(?:真的|现在|马上)?\s*"
        r"(?:想|要|准备|打算|决定|计划)\s*(?:去)?\s*"
        r"(?:自杀|割腕|跳楼|服毒|结束(?:自己)?的?生命|伤害自己|去死)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:我|本人)\s*(?:已经|刚刚|刚才)?\s*(?:吞|吃|服用|喝)\s*(?:了)?\s*"
        r"(?:很多|大量|过量|一整瓶|好多)\s*(?:药|药片|安眠药|毒药)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s+(?:want|need|plan|intend|am\s+going|(?:'m|am)\s+planning)\s+to\s+"
        r"(?:die|kill\s+myself|hurt\s+myself|end\s+my\s+life)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bi\s+(?:do\s+not|don't)\s+want\s+to\s+be\s+alive\b", re.IGNORECASE),
    re.compile(r"\b(?:life\s+is(?:n't|\s+not)\s+worth\s+living|better\s+off\s+dead)\b", re.IGNORECASE),
    re.compile(r"\b(?:thinking|thoughts?)\s+(?:about|of)\s+(?:suicide|killing\s+myself)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:am\s+going|plan|intend|want)\s+to\s+jump\s+off\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:took|swallowed|ate)\s+(?:too\s+many|a\s+lot\s+of|an\s+overdose\s+of)\s+(?:pills|medicine|medication)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:have\s+)?overdosed\b", re.IGNORECASE),
)

_HARM_TO_OTHERS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:(?:我|本人)\s*(?:现在|马上|今晚|今天)?|(?:现在|马上|今晚|今天)\s*|^\s*)"
        r"(?:真的|已经|就|正(?:在)?)?\s*"
        r"(?:想|要|准备|打算|决定|计划)\s*(?:去)?\s*"
        r"(?:杀(?:了|掉)?|弄死|伤害|砍|捅)\s*(?:他|她|他们|她们|别人|某人|人)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s+(?:want|plan|intend|am\s+going|(?:'m|am)\s+planning)\s+to\s+"
        r"(?:kill|hurt|stab)\s+(?:him|her|them|someone|people)\b",
        re.IGNORECASE,
    ),
)

_IMMEDIATE_DANGER = re.compile(
    r"(?:马上|现在就|今晚就|已经准备好|正在(?:去|做)|待会儿就|一会儿就|"
    r"已经(?:吞|吃|服用|喝)|刚刚(?:吞|吃|服用|喝)|"
    r"right\s+now|tonight|immediately|already\s+(?:have|got)|on\s+my\s+way|"
    r"(?:have\s+)?overdosed|(?:took|swallowed)\s+too\s+many)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafetyDecision:
    blocked: bool
    categories: tuple[str, ...]
    route: str


def _remove_safe_negations(text: str) -> str:
    normalized = text
    for pattern in _SAFE_NEGATIONS:
        normalized = pattern.sub(" ", normalized)
    return normalized


def route_text(text: str) -> SafetyDecision:
    normalized = _remove_safe_negations(text)
    categories: list[str] = []
    if any(pattern.search(normalized) for pattern in _SELF_HARM_PATTERNS):
        categories.append("self_harm")
    if any(pattern.search(normalized) for pattern in _HARM_TO_OTHERS_PATTERNS):
        categories.append("harm_to_others")
    if categories and _IMMEDIATE_DANGER.search(normalized):
        categories.append("immediate_danger")
    result = tuple(categories)
    return SafetyDecision(
        blocked=bool(result),
        categories=result,
        route="crisis_stop" if result else "standard",
    )
