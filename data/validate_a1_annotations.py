#!/usr/bin/env python3
"""流式验证《梦蝶记》C1/A1 派生标注 SQLite。

该脚本只读打开标注库与源语料库，对已接受的 JSON 标注执行：

1. JSON Schema 验证；
2. 源条目、原文与 SHA-256 回查；
3. evidence 原文子串、字符偏移与引用 ID 验证；
4. none_identified 互斥性与关键标签的证据覆盖；
5. automatically_validated 与未人工审核状态的一致性。

默认自动发现常见的 annotations 表/列名，也可用 CLI 显式指定。
验证器不会调用模型，也不会写入任何 SQLite 文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError
except ImportError:  # pragma: no cover - 在 CLI 入口给出可操作的错误
    Draft202012Validator = None  # type: ignore[assignment]
    FormatChecker = None  # type: ignore[assignment]
    SchemaError = Exception  # type: ignore[assignment,misc]

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - 在 CLI 入口给出可操作的错误
    OpenCC = None  # type: ignore[assignment,misc]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "contracts" / "a1-retrieval-annotation.schema.json"
DEFAULT_SOURCE_DB = PROJECT_ROOT / "data" / "corpus" / "c1_single_story" / "catalog.sqlite3"

JSON_COLUMN_CANDIDATES = (
    "annotation_json",
    "payload_json",
    "result_json",
    "record_json",
    "annotation",
    "payload",
    "json",
)
STATUS_COLUMN_CANDIDATES = (
    "status",
    "state",
    "pipeline_status",
    "annotation_status",
    "product_annotation_status",
)
ID_COLUMN_CANDIDATES = ("entry_id", "unit_id", "source_entry_id", "source_id", "id")
HASH_COLUMN_CANDIDATES = (
    "source_text_sha256",
    "source_sha256",
    "extracted_text_sha256",
    "text_sha256",
)
TABLE_CANDIDATES = ("annotations", "a1_annotations", "annotation_records", "results")

# 这些是流水线已经接受、因而应当具有可验证 JSON 的常见状态。
DEFAULT_ACCEPTED_STATUSES = frozenset(
    {
        "accepted",
        "success",
        "succeeded",
        "complete",
        "completed",
        "done",
        "validated",
        "automatically_validated",
        "sampled_reviewed",
        "human_reviewed",
    }
)


class ConfigurationError(RuntimeError):
    """CLI 或 SQLite 结构不可用。"""


@dataclass(frozen=True)
class SafetyPhraseRule:
    name: str
    pattern: re.Pattern[str]
    required_flags: frozenset[str]


@dataclass(frozen=True)
class SafetySignalRule:
    name: str
    pattern: re.Pattern[str]
    compatible_flags: frozenset[str]
    excluded_phrases: tuple[str, ...] = ()


EXPLICIT_DEATH_PATTERN = re.compile(
    r"暴亡|遂亡|竟亡|暴死|遂死|即死|"
    r"先後卒|先后卒|談笑而逝|谈笑而逝|"
    r"無言而卒|无言而卒|果終|果终|長已|长已|"
    r"薨|斃|殂"
)

ANIMAL_HARM_PATTERN = re.compile(
    # A 类规则宁可少报，不用“动物字 + 远处的死”这类窗口匹配。
    # 后者会把人名“牛成章”“石虎”以及“鸟使召我……某日当死”误判为动物伤害。
    r"(?:殺狗熊|杀狗熊|攫馬而食|攫马而食|煮虎肉|"
    r"(?:扨殺|扨杀|殺|杀|啖|剝皮|剥皮).{0,4}"
    r"(?:犬|狗|貓|猫|雞|鷄|鸡|鳥|鸟|魚|鱼|虎豹)|"
    r"(?:犬|狗|貓|猫|雞|鷄|鸡|鳥|鸟|魚|鱼).{0,4}"
    r"(?:被殺|被杀|殺死|杀死|剝皮|剥皮|穿腮)|"
    r"(?:牛|馬|马).{0,4}(?:頭|头|匹|隻|只).{0,8}(?:死|殺|杀)|"
    r"斷其線.{0,8}兩物皆死|断其线.{0,8}两物皆死)"
)

A_SAFETY_PHRASE_RULES = (
    SafetyPhraseRule(
        "explicit_sexual_violence",
        re.compile(
            r"姦污|姦汙|奸污|強姦|强姦|強奸|强奸|"
            r"逼姦|逼奸|輪姦|轮姦|輪奸|轮奸"
        ),
        frozenset({"sexual_violence", "sexual_content", "coercion_or_abuse"}),
    ),
    SafetyPhraseRule(
        "explicit_self_harm",
        re.compile(r"自經|自经|自縊|自缢|投繯|投缳|自刎|自殺|自杀|服毒自盡|服毒自尽"),
        frozenset({"self_harm_or_suicide"}),
    ),
    SafetyPhraseRule(
        "explicit_captivity",
        re.compile(
            r"枷鎖|枷锁|連鎖鎖枷|连锁锁枷|繫獄|系狱|鐵鏈鎖|铁链锁|"
            r"鎖閉|锁闭|捆住|捆縛|捆缚|捆綁|捆绑|"
            r"拘繫|拘系|囚之|幽閉|幽闭|監禁|监禁"
        ),
        frozenset({"captivity"}),
    ),
    SafetyPhraseRule(
        "explicit_violent_injury",
        re.compile(r"車裂|车裂|支解|穿腮|杖脊|大杖撻|大杖挞|鞭楚|亂毆|乱殴|拷掠|血流|熱血淋漓|热血淋漓"),
        frozenset({"violence", "physical_injury"}),
    ),
    SafetyPhraseRule(
        "explicit_coercion",
        re.compile(r"拐賣|拐卖|拐走|強迫|强迫|逼迫|擄走|掳走"),
        frozenset({"coercion_or_abuse"}),
    ),
    SafetyPhraseRule(
        "explicit_animal_harm",
        ANIMAL_HARM_PATTERN,
        frozenset({"animal_harm"}),
    ),
    SafetyPhraseRule(
        "explicit_death_outcome",
        EXPLICIT_DEATH_PATTERN,
        frozenset({"death"}),
    ),
    SafetyPhraseRule(
        "explicit_person_eaten",
        re.compile(r"(?:持|捉).{0,4}(?:人|僮|僕|仆|蒼頭|苍头).{0,4}(?:啖|食)"),
        frozenset({"death", "violence", "physical_injury"}),
    ),
)

SELF_HARM_DEATH_PATTERN = re.compile(
    r"(?:自經|自经|自縊|自缢|投繯|投缳|自刎|自殺|自杀|服毒自盡|服毒自尽)"
    r".{0,12}(?:死|亡|卒|斃|歿|殁|逝)"
)

B_SAFETY_SIGNAL_RULES = (
    SafetySignalRule(
        "death_language",
        re.compile(r"死|卒|亡|薨|斃|殂|歿|殁|逝|長已|长已"),
        frozenset(
            {
                "death",
                "self_harm_or_suicide",
                "animal_harm",
                "violence",
                "coercion_or_abuse",
                "supernatural_horror",
                "grief_or_bereavement",
                "illness",
            }
        ),
        (
            "士卒",
            "兵卒",
            "健卒",
            "廝卒",
            "厮卒",
            "廄卒",
            "厩卒",
            "獄卒",
            "狱卒",
            "吏卒",
            "衣卒",
            "走卒",
            "騎卒",
            "骑卒",
            "卒無",
            "卒无",
            "卒得",
            "卒不",
            "卒然",
            "卒業",
            "卒业",
            "卒為",
            "卒为",
            "卒成",
            "卒以",
            "卒能",
            "卒獲",
            "卒获",
            "卒免",
            "卒遂",
            "卒如",
            "卒用",
            "卒驗",
            "卒验",
            "逃亡",
            "亡命",
            "亡何",
            "亡羊",
            "所以亡",
            "西逝",
            "生死不能",
            "死生之理",
            "半死生",
        ),
    ),
    SafetySignalRule(
        "violence_or_injury_language",
        re.compile(r"殺|杀|傷|伤|血|杖|鞭|毆|殴|斬|斩|刺|刃|咬|齕|撻|挞"),
        frozenset(
            {
                "violence",
                "physical_injury",
                "animal_harm",
                "coercion_or_abuse",
                "self_harm_or_suicide",
                "sexual_violence",
                "death",
            }
        ),
        (
            "投刺",
            "名刺",
            "刺史",
            "譏刺",
            "讥刺",
            "刺布",
            "鞭策",
            "鞭馬",
            "鞭马",
            "葷血",
            "荤血",
            "哀傷",
            "哀伤",
            "痛傷",
            "痛伤",
            "傷悲",
            "伤悲",
            "傷哉",
            "伤哉",
            "不忍傷",
            "不忍伤",
            "曳杖",
            "拄杖",
            "扶杖",
            "手杖",
            "加鞭",
        ),
    ),
    SafetySignalRule(
        "captivity_or_coercion_language",
        re.compile(r"囚|獄|狱|枷|鎖|锁|拘|縛|缚|捆|拐|掠|押|禁"),
        frozenset({"captivity", "coercion_or_abuse", "violence", "physical_injury"}),
        (
            "禁中",
            "不禁",
            "禁令",
            "禁忌",
            "賭禁",
            "赌禁",
            "押韻",
            "押韵",
            "不明押",
            "掣鎖",
            "掣锁",
            "開金鎖",
            "开金锁",
            "鎖鑰",
            "锁钥",
            "拘於",
            "拘于",
            "地獄",
            "地狱",
            "獄吏",
            "狱吏",
            "決獄",
            "决狱",
            "刑獄",
            "刑狱",
            "東獄",
            "东狱",
            "若被縛",
            "若被缚",
        ),
    ),
    SafetySignalRule(
        "sexual_language",
        re.compile(
            r"姦污|姦汙|奸污|強姦|强姦|強奸|强奸|逼姦|逼奸|"
            r"輪姦|轮姦|輪奸|轮奸|野合|交合|"
            r"同寢|同寝|歡好|欢好|私通|狎暱|狎昵|狎邪|狎褻|狎亵|"
            r"狎妓|狎童|交歡|交欢|床笫|枕席"
        ),
        frozenset({"sexual_content", "sexual_violence"}),
    ),
    SafetySignalRule(
        "illness_language",
        re.compile(r"病|疾|瘡|疮|癑|痈|癤|疖|瘧|疯|疫"),
        frozenset({"illness", "physical_injury"}),
        (
            "病之",
            "無病",
            "无病",
            "託疾",
            "托疾",
            "無疾",
            "无疾",
            "疾風",
            "疾风",
            "疾驅",
            "疾驱",
            "疾走",
            "瘡痍",
            "疮痍",
        ),
    ),
)


_SIMPLIFIER: Any | None = None


def get_simplifier() -> Any:
    global _SIMPLIFIER
    if OpenCC is None:
        raise ConfigurationError("missing dependency opencc; generated zh-Hans fields cannot be verified")
    if _SIMPLIFIER is None:
        _SIMPLIFIER = OpenCC("t2s")
    return _SIMPLIFIER


@dataclass(frozen=True)
class AnnotationLayout:
    table: str
    json_column: str
    status_column: str | None
    id_column: str | None
    hash_column: str | None


@dataclass
class ValidationStats:
    rows_total: int = 0
    rows_with_json: int = 0
    rows_accepted: int = 0
    rows_skipped: int = 0
    records_valid: int = 0
    records_invalid: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    issue_counts: Counter[str] = field(default_factory=Counter)
    displayed_issues: list[str] = field(default_factory=list)

    def add_issue(self, code: str, record_id: str, detail: str, max_displayed: int) -> None:
        self.issue_counts[code] += 1
        if len(self.displayed_issues) < max_displayed:
            self.displayed_issues.append(f"[{code}] {record_id}: {detail}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读、流式验证 C1/A1 派生标注 SQLite，输出 PASS/FAIL 与统计。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", required=True, type=Path, help="待验证的派生标注 SQLite")
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB, help="C1 源语料 SQLite")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="A1 JSON Schema")
    parser.add_argument("--table", help="标注表名；省略时自动发现")
    parser.add_argument("--json-column", help="JSON 列名；省略时自动发现")
    parser.add_argument("--status-column", help="流水线状态列名；省略时自动发现")
    parser.add_argument("--id-column", help="源条目 ID 列名；省略时自动发现")
    parser.add_argument("--hash-column", help="源文本 SHA-256 列名；省略时自动发现")
    parser.add_argument(
        "--accepted-status",
        action="append",
        default=[],
        help="追加一个应被验证的状态（可重复）",
    )
    parser.add_argument(
        "--validate-all-json",
        action="store_true",
        help="忽略状态，验证每个非空 JSON 记录",
    )
    parser.add_argument("--batch-size", type=int, default=500, help="SQLite 游标每次 fetchmany 行数")
    parser.add_argument("--max-errors", type=int, default=40, help="最多展示的问题明细（统计仍完整）")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="允许没有任何已接受记录（用于流水线初始空库自检）",
    )
    return parser.parse_args(argv)


def quote_identifier(name: str) -> str:
    if not name or "\x00" in name:
        raise ConfigurationError("非法 SQLite 标识符")
    return '"' + name.replace('"', '""') + '"'


def connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ConfigurationError(f"SQLite 文件不存在: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ConfigurationError(f"无法只读打开 SQLite: {resolved}: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    return [str(row["name"]) for row in rows]


def user_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def first_present(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    available = set(columns)
    return next((candidate for candidate in candidates if candidate in available), None)


def discover_layout(connection: sqlite3.Connection, args: argparse.Namespace) -> AnnotationLayout:
    tables = user_tables(connection)
    if not tables:
        raise ConfigurationError("标注 SQLite 中没有用户表")

    if args.table:
        if args.table not in tables:
            raise ConfigurationError(
                f"表 {args.table!r} 不存在；可用表: {', '.join(tables)}"
            )
        table = args.table
    else:
        ranked: list[tuple[int, str]] = []
        for candidate_table in tables:
            columns = table_columns(connection, candidate_table)
            json_column = first_present(columns, JSON_COLUMN_CANDIDATES)
            if not json_column:
                continue
            score = 100 - TABLE_CANDIDATES.index(candidate_table) if candidate_table in TABLE_CANDIDATES else 0
            score += 10 if first_present(columns, STATUS_COLUMN_CANDIDATES) else 0
            score += 5 if first_present(columns, ID_COLUMN_CANDIDATES) else 0
            ranked.append((score, candidate_table))
        if not ranked:
            raise ConfigurationError(
                "无法自动发现含标注 JSON 的表；请用 --table 和 --json-column 指定"
            )
        table = sorted(ranked, key=lambda item: (-item[0], item[1]))[0][1]

    columns = table_columns(connection, table)

    def explicit_or_discover(explicit: str | None, candidates: Sequence[str], label: str) -> str | None:
        if explicit:
            if explicit not in columns:
                raise ConfigurationError(
                    f"{label} {explicit!r} 不在表 {table!r} 中；可用列: {', '.join(columns)}"
                )
            return explicit
        return first_present(columns, candidates)

    json_column = explicit_or_discover(args.json_column, JSON_COLUMN_CANDIDATES, "JSON 列")
    if not json_column:
        raise ConfigurationError("无法发现 JSON 列；请用 --json-column 指定")
    return AnnotationLayout(
        table=table,
        json_column=json_column,
        status_column=explicit_or_discover(args.status_column, STATUS_COLUMN_CANDIDATES, "状态列"),
        id_column=explicit_or_discover(args.id_column, ID_COLUMN_CANDIDATES, "ID 列"),
        hash_column=explicit_or_discover(args.hash_column, HASH_COLUMN_CANDIDATES, "hash 列"),
    )


def load_validator(schema_path: Path) -> Any:
    if Draft202012Validator is None or FormatChecker is None:
        raise ConfigurationError("缺少依赖 jsonschema；请安装 jsonschema>=4")
    resolved = schema_path.expanduser().resolve()
    if not resolved.is_file():
        raise ConfigurationError(f"JSON Schema 不存在: {resolved}")
    try:
        schema = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"无法读取 JSON Schema: {resolved}: {exc}") from exc
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ConfigurationError(f"JSON Schema 本身无效: {exc.message}") from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def verify_source_schema(connection: sqlite3.Connection) -> None:
    required = {
        "entry_id",
        "text",
        "extracted_text_sha256",
        "normalized_text_sha256",
        "dedupe_status",
        "canonical_entry_id",
        "runtime_eligible",
    }
    if "entries" not in user_tables(connection):
        raise ConfigurationError("源语料库缺少 entries 表")
    missing = sorted(required - set(table_columns(connection, "entries")))
    if missing:
        raise ConfigurationError(f"源语料 entries 表缺少列: {', '.join(missing)}")


def selected_rows(
    connection: sqlite3.Connection,
    layout: AnnotationLayout,
    batch_size: int,
) -> Iterator[sqlite3.Row]:
    select_parts: list[str] = []
    if layout.id_column:
        select_parts.append(f"{quote_identifier(layout.id_column)} AS _outer_id")
    else:
        select_parts.append("rowid AS _outer_id")
    select_parts.append(f"{quote_identifier(layout.json_column)} AS _annotation_json")
    if layout.status_column:
        select_parts.append(f"{quote_identifier(layout.status_column)} AS _pipeline_status")
    else:
        select_parts.append("NULL AS _pipeline_status")
    if layout.hash_column:
        select_parts.append(f"{quote_identifier(layout.hash_column)} AS _outer_hash")
    else:
        select_parts.append("NULL AS _outer_hash")
    query = f"SELECT {', '.join(select_parts)} FROM {quote_identifier(layout.table)}"
    cursor = connection.execute(query)
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        yield from batch


def normalized_status(value: Any) -> str:
    if value is None:
        return "<null>"
    text = str(value).strip()
    return text.lower() if text else "<empty>"


def json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def source_record(connection: sqlite3.Connection, entry_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT entry_id, text, extracted_text_sha256, normalized_text_sha256, "
        "dedupe_status, canonical_entry_id, runtime_eligible "
        "FROM entries WHERE entry_id = ?",
        (entry_id,),
    ).fetchone()


def ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def ensure_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def generated_text_fields(annotation: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield only model-generated, user-facing Chinese fields.

    Canonical source text, titles, locators, and verbatim evidence are
    intentionally excluded: converting those fields would corrupt provenance.
    """

    retrieval = ensure_mapping(annotation.get("retrieval_profile"))
    scalar_fields = (
        ("retrieval_profile.modern_retrieval_summary", retrieval.get("modern_retrieval_summary")),
        ("retrieval_profile.narrative_sufficiency_reason", retrieval.get("narrative_sufficiency_reason")),
    )
    for path, value in scalar_fields:
        if isinstance(value, str):
            yield path, value

    for index, entity in enumerate(ensure_list(retrieval.get("key_entities"))):
        item = ensure_mapping(entity)
        for field_name in ("name", "role"):
            value = item.get(field_name)
            if isinstance(value, str):
                yield f"retrieval_profile.key_entities[{index}].{field_name}", value

    for index, beat in enumerate(ensure_list(retrieval.get("plot_beats"))):
        value = ensure_mapping(beat).get("text")
        if isinstance(value, str):
            yield f"retrieval_profile.plot_beats[{index}].text", value

    for index, value in enumerate(ensure_list(retrieval.get("motif_terms"))):
        if isinstance(value, str):
            yield f"retrieval_profile.motif_terms[{index}]", value

    trigger = ensure_mapping(annotation.get("narrative_arc")).get("trigger")
    if isinstance(trigger, str):
        yield "narrative_arc.trigger", trigger

    safety = ensure_mapping(annotation.get("auto_safety_screen"))
    for index, value in enumerate(ensure_list(safety.get("uncertainties"))):
        if isinstance(value, str):
            yield f"auto_safety_screen.uncertainties[{index}]", value


def generated_text_issues(annotation: Mapping[str, Any]) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    simplifier = get_simplifier()
    for path, value in generated_text_fields(annotation):
        simplified = simplifier.convert(value)
        if simplified != value:
            issues.append(
                (
                    "GENERATED_TEXT_NOT_SIMPLIFIED",
                    f"{path} contains non-zh-Hans forms; t2s result={simplified!r}",
                )
            )
    return issues


def signal_match(rule: SafetySignalRule, source_text: str) -> re.Match[str] | None:
    candidate = source_text
    for phrase in rule.excluded_phrases:
        candidate = candidate.replace(phrase, " " * len(phrase))
    return rule.pattern.search(candidate)


def safety_quality_issues(
    annotation: Mapping[str, Any], source_text: str
) -> list[tuple[str, str]]:
    """Enforce conservative lexical safety coverage without treating unknown as safe.

    Class-A phrases have a stable, narrow interpretation and require exact
    flags. Class-B signals are intentionally broader: they require at least one
    compatible warning, but never auto-assign a specific label. An `unknown`
    screen status does not waive either gate; it remains valid only when the
    explicit content has still been conservatively covered by flags.
    """

    issues: list[tuple[str, str]] = []
    safety = ensure_mapping(annotation.get("auto_safety_screen"))
    flags = {value for value in ensure_list(safety.get("flags")) if isinstance(value, str)}

    for rule in A_SAFETY_PHRASE_RULES:
        match = rule.pattern.search(source_text)
        if match is None:
            continue
        missing = sorted(rule.required_flags - flags)
        if missing:
            issues.append(
                (
                    "SAFETY_EXPLICIT_PHRASE_MISSING_FLAGS",
                    f"rule={rule.name}, phrase={match.group(0)!r}, missing={','.join(missing)}",
                )
            )

    for rule in B_SAFETY_SIGNAL_RULES:
        match = signal_match(rule, source_text)
        if match is not None and not (flags & rule.compatible_flags):
            issues.append(
                (
                    "SAFETY_SIGNAL_UNCOVERED",
                    f"signal={rule.name}, phrase={match.group(0)!r}, compatible_flags="
                    + ",".join(sorted(rule.compatible_flags)),
                )
            )

    if "sexual_violence" in flags:
        missing = sorted({"sexual_content", "coercion_or_abuse"} - flags)
        if missing:
            issues.append(
                (
                    "SAFETY_CROSS_LABEL_MISSING",
                    "sexual_violence also requires " + ",".join(missing),
                )
            )
    if "self_harm_or_suicide" in flags and SELF_HARM_DEATH_PATTERN.search(source_text):
        if "death" not in flags:
            issues.append(
                (
                    "SAFETY_CROSS_LABEL_MISSING",
                    "explicit fatal self-harm also requires death",
                )
            )
    return issues


def evidence_semantic_issues(annotation: Mapping[str, Any], db_source_text: str) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    evidence = ensure_list(annotation.get("evidence"))
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    supports_union: set[str] = set()

    for index, raw_item in enumerate(evidence):
        item = ensure_mapping(raw_item)
        evidence_id = item.get("id")
        if isinstance(evidence_id, str):
            if evidence_id in evidence_by_id:
                issues.append(("EVIDENCE_ID_DUPLICATE", f"evidence[{index}].id={evidence_id!r} 重复"))
            else:
                evidence_by_id[evidence_id] = item

        excerpt = item.get("excerpt")
        start = item.get("start_char")
        end = item.get("end_char")
        if not isinstance(excerpt, str) or not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
            # 类型错误已由 Schema 报告，这里避免级联异常。
            continue
        if start < 0 or end < start or end > len(db_source_text):
            issues.append(
                (
                    "EVIDENCE_OFFSET_RANGE",
                    f"{evidence_id or index}: [{start}, {end}) 超出原文长度 {len(db_source_text)}",
                )
            )
        elif db_source_text[start:end] != excerpt:
            issues.append(
                (
                    "EVIDENCE_NOT_EXACT_SUBSTRING",
                    f"{evidence_id or index}: source_text[{start}:{end}] 与 excerpt 不一致",
                )
            )
        if end - start != len(excerpt):
            issues.append(
                (
                    "EVIDENCE_OFFSET_LENGTH",
                    f"{evidence_id or index}: end-start={end-start}，但 excerpt 长度={len(excerpt)}",
                )
            )
        supports_union.update(value for value in ensure_list(item.get("supports")) if isinstance(value, str))

    retrieval = ensure_mapping(annotation.get("retrieval_profile"))

    def check_evidence_ids(values: Any, field: str) -> None:
        for evidence_id in ensure_list(values):
            if isinstance(evidence_id, str) and evidence_id not in evidence_by_id:
                issues.append(("EVIDENCE_REFERENCE_MISSING", f"{field} 引用了不存在的 {evidence_id}"))

    summary_ids = ensure_list(retrieval.get("summary_evidence_ids"))
    check_evidence_ids(summary_ids, "retrieval_profile.summary_evidence_ids")
    summary_support = "retrieval_profile.modern_retrieval_summary"
    if not any(
        summary_support in ensure_list(evidence_by_id.get(evidence_id, {}).get("supports"))
        for evidence_id in summary_ids
        if isinstance(evidence_id, str)
    ):
        issues.append(
            (
                "SUMMARY_EVIDENCE_LINK_MISMATCH",
                "summary_evidence_ids 中没有证据显式支持 modern_retrieval_summary",
            )
        )

    for index, entity in enumerate(ensure_list(retrieval.get("key_entities"))):
        check_evidence_ids(ensure_mapping(entity).get("evidence_ids"), f"key_entities[{index}].evidence_ids")
    for index, beat in enumerate(ensure_list(retrieval.get("plot_beats"))):
        check_evidence_ids(ensure_mapping(beat).get("evidence_ids"), f"plot_beats[{index}].evidence_ids")

    required_supports: set[str] = {summary_support}
    for label in ensure_list(annotation.get("life_context")):
        if isinstance(label, str):
            required_supports.add(f"life_context.{label}")

    narrative = ensure_mapping(annotation.get("narrative_arc"))
    trigger = narrative.get("trigger")
    sufficiency = retrieval.get("narrative_sufficiency")
    if not (
        sufficiency in {"insufficient", "unknown"}
        and isinstance(trigger, str)
        and trigger.strip().lower() in {"unknown", "未知", "不详", "不明"}
    ):
        required_supports.add("narrative_arc.trigger")
    for label in ensure_list(narrative.get("conflict_types")):
        if isinstance(label, str):
            required_supports.add(f"narrative_arc.conflict_types.{label}")
    for label in ensure_list(narrative.get("agency_modes")):
        if isinstance(label, str) and label != "unknown":
            required_supports.add(f"narrative_arc.agency_modes.{label}")
    ending_mode = narrative.get("ending_mode")
    if isinstance(ending_mode, str) and ending_mode != "unknown":
        required_supports.add(f"narrative_arc.ending_mode.{ending_mode}")

    safety = ensure_mapping(annotation.get("auto_safety_screen"))
    for field_name in ("flags", "interpretation_risks"):
        values = [value for value in ensure_list(safety.get(field_name)) if isinstance(value, str)]
        if "none_identified" in values and len(values) != 1:
            issues.append(
                (
                    "NONE_IDENTIFIED_NOT_EXCLUSIVE",
                    f"auto_safety_screen.{field_name} 中 none_identified 必须单独出现",
                )
            )
        for label in values:
            if label != "none_identified":
                required_supports.add(f"auto_safety_screen.{field_name}.{label}")

    for support in sorted(required_supports - supports_union):
        issues.append(("CRITICAL_FIELD_WITHOUT_EVIDENCE", f"{support} 没有 evidence.supports 支持"))
    for support in sorted(supports_union):
        terminal = support.rsplit(".", 1)[-1]
        if terminal in {"unknown", "none_identified"}:
            issues.append(
                (
                    "EVIDENCE_SUPPORT_NONCLAIM",
                    f"{support} points to a non-claim and must not be evidence-supported",
                )
            )
        elif support not in required_supports:
            issues.append(
                (
                    "EVIDENCE_SUPPORT_DANGLING",
                    f"{support} does not exist in the final annotation labels",
                )
            )
    return issues


def status_semantic_issues(annotation: Mapping[str, Any]) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    meta = ensure_mapping(annotation.get("annotation_meta"))
    if meta.get("product_annotation_status") != "automatically_validated":
        return issues

    expected = {
        "research_review_status": "not_reviewed",
        "research_ready": False,
        "model_preannotation": True,
    }
    for field_name, expected_value in expected.items():
        if meta.get(field_name) != expected_value:
            issues.append(
                (
                    "AUTO_VALIDATED_STATUS_INCONSISTENT",
                    f"annotation_meta.{field_name} 应为 {expected_value!r}，实为 {meta.get(field_name)!r}",
                )
            )
    human_review = ensure_mapping(meta.get("human_review"))
    if human_review.get("reviewed") is not False:
        issues.append(
            (
                "AUTO_VALIDATED_STATUS_INCONSISTENT",
                "automatically_validated 的 human_review.reviewed 必须为 false",
            )
        )
    return issues


def source_semantic_issues(
    annotation: Mapping[str, Any],
    source: sqlite3.Row | None,
    outer_id: Any,
    outer_hash: Any,
) -> tuple[str, str, list[tuple[str, str]]]:
    issues: list[tuple[str, str]] = []
    unit = ensure_mapping(annotation.get("unit"))
    source_profile = ensure_mapping(annotation.get("source_profile"))
    unit_id = unit.get("unit_id")
    record_id = str(unit_id or outer_id or "<unknown>")

    if isinstance(outer_id, str) and isinstance(unit_id, str) and outer_id != unit_id:
        issues.append(("OUTER_ID_MISMATCH", f"SQLite ID={outer_id!r}，JSON unit_id={unit_id!r}"))
    if source is None:
        issues.append(("SOURCE_ENTRY_MISSING", f"源语料库中不存在 entry_id={unit_id!r}"))
        return record_id, "", issues

    db_text = str(source["text"])
    computed_hash = hashlib.sha256(db_text.encode("utf-8")).hexdigest()
    if source["extracted_text_sha256"] != computed_hash:
        issues.append(
            (
                "SOURCE_DB_HASH_CORRUPT",
                "源语料库 extracted_text_sha256 与 text 的实际 SHA-256 不一致",
            )
        )

    hash_values = {
        "unit.source_text_sha256": unit.get("source_text_sha256"),
        "source_profile.source_text_sha256": source_profile.get("source_text_sha256"),
    }
    if outer_hash is not None and str(outer_hash).strip():
        hash_values["SQLite outer hash"] = str(outer_hash)
    for field_name, value in hash_values.items():
        if value != computed_hash:
            issues.append(
                (
                    "SOURCE_HASH_MISMATCH",
                    f"{field_name}={value!r}，应为源 text SHA-256 {computed_hash}",
                )
            )

    profile_text = source_profile.get("source_text")
    if profile_text != db_text:
        issues.append(("SOURCE_TEXT_MISMATCH", "source_profile.source_text 与源语料 entries.text 不一致"))
    if source["dedupe_status"] != "canonical" or source["canonical_entry_id"] != source["entry_id"]:
        issues.append(("SOURCE_NOT_CANONICAL", "源条目不是 canonical 条目"))
    if int(source["runtime_eligible"]) != 1:
        issues.append(("SOURCE_NOT_RUNTIME_ELIGIBLE", "源条目 runtime_eligible != 1"))
    return record_id, db_text, issues


def validate_database(args: argparse.Namespace) -> tuple[ValidationStats, AnnotationLayout]:
    if args.batch_size <= 0:
        raise ConfigurationError("--batch-size 必须大于 0")
    if args.max_errors < 0:
        raise ConfigurationError("--max-errors 不能小于 0")

    validator = load_validator(args.schema)
    annotation_connection = connect_read_only(args.db)
    source_connection = connect_read_only(args.source_db)
    try:
        verify_source_schema(source_connection)
        layout = discover_layout(annotation_connection, args)
        accepted_statuses = DEFAULT_ACCEPTED_STATUSES | {
            str(value).strip().lower() for value in args.accepted_status if str(value).strip()
        }
        stats = ValidationStats()

        for row in selected_rows(annotation_connection, layout, args.batch_size):
            stats.rows_total += 1
            status = normalized_status(row["_pipeline_status"])
            stats.status_counts[status] += 1
            raw_json = row["_annotation_json"]
            has_json = raw_json is not None and (not isinstance(raw_json, str) or bool(raw_json.strip()))
            if has_json:
                stats.rows_with_json += 1

            accepted = has_json and (
                args.validate_all_json or layout.status_column is None or status in accepted_statuses
            )
            if not accepted:
                stats.rows_skipped += 1
                continue
            stats.rows_accepted += 1

            outer_id = row["_outer_id"]
            provisional_id = str(outer_id if outer_id is not None else f"row#{stats.rows_total}")
            record_issues: list[tuple[str, str]] = []
            try:
                payload = json.loads(raw_json) if isinstance(raw_json, (str, bytes, bytearray)) else raw_json
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
                record_issues.append(("INVALID_JSON", str(exc)))
                payload = None

            if not isinstance(payload, Mapping):
                if payload is not None:
                    record_issues.append(("JSON_ROOT_NOT_OBJECT", f"根类型为 {type(payload).__name__}"))
                annotation: Mapping[str, Any] = {}
                record_id = provisional_id
            else:
                schema_errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
                for error in schema_errors:
                    record_issues.append(
                        ("JSON_SCHEMA", f"{json_path(error.absolute_path)}: {error.message}")
                    )

                annotation = ensure_mapping(payload.get("annotation_record"))
                unit_id = ensure_mapping(annotation.get("unit")).get("unit_id")
                source = source_record(source_connection, unit_id) if isinstance(unit_id, str) else None
                record_id, db_source_text, source_issues = source_semantic_issues(
                    annotation, source, outer_id, row["_outer_hash"]
                )
                record_issues.extend(source_issues)
                record_issues.extend(generated_text_issues(annotation))
                if source is not None:
                    record_issues.extend(evidence_semantic_issues(annotation, db_source_text))
                    record_issues.extend(safety_quality_issues(annotation, db_source_text))
                record_issues.extend(status_semantic_issues(annotation))

            if record_issues:
                stats.records_invalid += 1
                for code, detail in record_issues:
                    stats.add_issue(code, record_id, detail, args.max_errors)
            else:
                stats.records_valid += 1

        if stats.rows_accepted == 0 and not args.allow_empty:
            stats.add_issue(
                "NO_ACCEPTED_ROWS",
                "<database>",
                "没有可验证的已接受 JSON 记录；空库自检可加 --allow-empty",
                args.max_errors,
            )
        return stats, layout
    finally:
        annotation_connection.close()
        source_connection.close()


def print_report(args: argparse.Namespace, stats: ValidationStats, layout: AnnotationLayout) -> bool:
    passed = stats.records_invalid == 0 and not stats.issue_counts
    print(f"A1 标注验证: {'PASS' if passed else 'FAIL'}")
    print(f"标注库: {args.db.expanduser().resolve()}")
    print(f"源语料库: {args.source_db.expanduser().resolve()}")
    print(
        f"布局: table={layout.table}, json={layout.json_column}, "
        f"status={layout.status_column or '<none>'}, id={layout.id_column or 'rowid'}, "
        f"hash={layout.hash_column or '<none>'}"
    )
    print(
        "记录: "
        f"total={stats.rows_total}, with_json={stats.rows_with_json}, "
        f"accepted={stats.rows_accepted}, skipped={stats.rows_skipped}, "
        f"valid={stats.records_valid}, invalid={stats.records_invalid}"
    )
    print("状态计数:")
    if stats.status_counts:
        for status, count in sorted(stats.status_counts.items()):
            print(f"  {status}: {count}")
    else:
        print("  <none>: 0")
    print("问题计数:")
    if stats.issue_counts:
        for code, count in sorted(stats.issue_counts.items()):
            print(f"  {code}: {count}")
    else:
        print("  none: 0")
    if stats.displayed_issues:
        print(f"问题明细（最多 {args.max_errors} 条）:")
        for issue in stats.displayed_issues:
            print(f"  {issue}")
    return passed


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        stats, layout = validate_database(args)
    except (ConfigurationError, sqlite3.Error) as exc:
        print(f"A1 标注验证: FAIL", file=sys.stderr)
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2
    return 0 if print_report(args, stats, layout) else 1


if __name__ == "__main__":
    raise SystemExit(main())
