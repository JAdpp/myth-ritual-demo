#!/usr/bin/env python3
"""Run a resumable, non-destructive semantic review over valid C1/A1 records.

The canonical C1 and A1 databases are always opened read-only.  Review results
are written to a separate SQLite ledger and never overwrite annotation_json.
Every provider input is retained in full in ``semantic_review_jobs.input_json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock
from typing import Any, Sequence

import httpx
from dotenv import load_dotenv
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

try:  # package import in tests and repair tooling
    from data.annotate_c1_retrieval import (
        acquire_process_lock,
        release_process_lock,
        resolve_source_excerpt,
    )
    from data.validate_a1_annotations import (
        A_SAFETY_PHRASE_RULES,
        SELF_HARM_DEATH_PATTERN,
    )
except ImportError:  # direct ``python data/review_a1_semantics.py`` execution
    from annotate_c1_retrieval import (  # type: ignore[no-redef]
        acquire_process_lock,
        release_process_lock,
        resolve_source_excerpt,
    )
    from validate_a1_annotations import (  # type: ignore[no-redef]
        A_SAFETY_PHRASE_RULES,
        SELF_HARM_DEATH_PATTERN,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT / "data" / "corpus" / "c1_single_story" / "catalog.sqlite3"
DEFAULT_ANNOTATION_DB = (
    ROOT / "data" / "corpus" / "a1_retrieval_annotations" / "annotations.sqlite3"
)
DEFAULT_SIDECAR_DB = (
    ROOT / "data" / "corpus" / "a1_semantic_reviews" / "reviews.sqlite3"
)
DEFAULT_SCHEMA = ROOT / "contracts" / "a1-semantic-review.schema.json"
DEFAULT_MANIFEST = ROOT / "data" / "corpus" / "a1_semantic_reviews" / "manifest.json"

REVIEW_VERSION = "mengdie-a1-semantic-review-v3-nonthinking-field-checklist"
PROMPT_VERSION = "mengdie-a1-semantic-review-v3-nonthinking-field-checklist"
SQLITE_BUSY_TIMEOUT_MS = 30_000

ISSUE_FIELD_BY_CODE = {
    "summary_actor": "modernRetrievalSummary",
    "summary_action_object": "modernRetrievalSummary",
    "summary_modality_negation": "modernRetrievalSummary",
    "summary_time": "modernRetrievalSummary",
    "summary_outcome": "modernRetrievalSummary",
    "summary_location": "modernRetrievalSummary",
    "summary_quantity": "modernRetrievalSummary",
    "summary_relation": "modernRetrievalSummary",
    "summary_causality": "modernRetrievalSummary",
    "summary_other_fact": "modernRetrievalSummary",
    "other_version": "modernRetrievalSummary",
    "narrative_sufficiency_mismatch": "narrativeSufficiency",
    "key_entity_mismatch": "keyEntities",
    "plot_beat_mismatch": "plotBeats",
    "motif_mismatch": "motifTerms",
    "label_life_context": "lifeContext",
    "trigger_mismatch": "narrativeArc.trigger",
    "label_conflict": "narrativeArc.conflictTypes",
    "label_agency": "narrativeArc.agencyModes",
    "label_ending": "narrativeArc.endingMode",
    "safety_status_mismatch": "autoSafetyScreen.status",
    "safety_omission": "autoSafetyScreen.flags",
    "safety_overreach": "autoSafetyScreen.flags",
    "safety_uncertainty_mismatch": "autoSafetyScreen.uncertainties",
    "interpretation_risk_mismatch": "autoSafetyScreen.interpretationRisks",
    "evidence_support_mismatch": "evidence.supports",
}

CHECKLIST_FIELDS = (
    "modernRetrievalSummary",
    "narrativeSufficiency",
    "keyEntities",
    "plotBeats",
    "motifTerms",
    "lifeContext",
    "narrativeArc.trigger",
    "narrativeArc.conflictTypes",
    "narrativeArc.agencyModes",
    "narrativeArc.endingMode",
    "autoSafetyScreen.status",
    "autoSafetyScreen.flags",
    "autoSafetyScreen.interpretationRisks",
    "autoSafetyScreen.uncertainties",
    "evidence.supports",
)

SOFT_REVISE_HINT_MARKERS = (
    "基本相符",
    "基本一致",
    "正确",
    "正確",
    "合理",
    "可保留",
    "保留即可",
    "无需修改",
    "無需修改",
    "不需修改",
    "需确认",
    "需確認",
    "可能",
    "建议",
    "建議",
)

FIELD_ALIASES = {
    "retrieval_profile.modern_retrieval_summary": "modernRetrievalSummary",
    "modern_retrieval_summary": "modernRetrievalSummary",
    "narrative_sufficiency": "narrativeSufficiency",
    "key_entities": "keyEntities",
    "plot_beats": "plotBeats",
    "motif_terms": "motifTerms",
    "life_context": "lifeContext",
    "trigger": "narrativeArc.trigger",
    "conflictTypes": "narrativeArc.conflictTypes",
    "conflict_types": "narrativeArc.conflictTypes",
    "agencyModes": "narrativeArc.agencyModes",
    "agency_modes": "narrativeArc.agencyModes",
    "endingMode": "narrativeArc.endingMode",
    "ending_mode": "narrativeArc.endingMode",
    "autoSafetyScreen": "autoSafetyScreen.status",
    "auto_safety_screen.status": "autoSafetyScreen.status",
    "auto_safety_screen.flags": "autoSafetyScreen.flags",
    "auto_safety_screen.interpretation_risks": "autoSafetyScreen.interpretationRisks",
    "auto_safety_screen.uncertainties": "autoSafetyScreen.uncertainties",
    "interpretationRisks": "autoSafetyScreen.interpretationRisks",
    "uncertainties": "autoSafetyScreen.uncertainties",
    "evidence": "evidence.supports",
}

CODE_ALIASES = {
    "agency_mode_overreach": "label_agency",
    "agency_mode_mismatch": "label_agency",
    "safety_flag_overreach": "safety_overreach",
    "safety_flag_omission": "safety_omission",
    "interpretation_risk_overreach": "interpretation_risk_mismatch",
    "narrativeSufficiency": "narrative_sufficiency_mismatch",
    "keyEntities": "key_entity_mismatch",
    "plotBeats": "plot_beat_mismatch",
    "motifTerms": "motif_mismatch",
    "lifeContext": "label_life_context",
    "trigger": "trigger_mismatch",
    "conflictTypes": "label_conflict",
    "agencyModes": "label_agency",
    "endingMode": "label_ending",
}

DEFAULT_CODE_BY_FIELD = {
    "modernRetrievalSummary": "summary_other_fact",
    "narrativeSufficiency": "narrative_sufficiency_mismatch",
    "keyEntities": "key_entity_mismatch",
    "plotBeats": "plot_beat_mismatch",
    "motifTerms": "motif_mismatch",
    "lifeContext": "label_life_context",
    "narrativeArc.trigger": "trigger_mismatch",
    "narrativeArc.conflictTypes": "label_conflict",
    "narrativeArc.agencyModes": "label_agency",
    "narrativeArc.endingMode": "label_ending",
    "autoSafetyScreen.status": "safety_status_mismatch",
    "autoSafetyScreen.interpretationRisks": "interpretation_risk_mismatch",
    "autoSafetyScreen.uncertainties": "safety_uncertainty_mismatch",
    "evidence.supports": "evidence_support_mismatch",
}

LEGACY_SYSTEM_PROMPT_V2 = """你是《梦蝶记》的反证式 A1 语义审校器。candidate 可能有错，唯一事实来源是每条当前 sourceText；不得调用常识、其他版本或后世流传情节，也不得修改原文。

对每条先在内部只依据原文核对主体、动作、对象、否定、条件、时态、传闻或梦境和结果，再审查 candidate。主动寻找能推翻 candidate 的原文反例：
1. 现代释义不得换主体、动作、对象、地点、数量、人物关系、因果，把未然/条件/传闻/梦境写成既成事实，改变时间或夸大结果；无法归入专门代码的明确事实错误用 summary_other_fact。
2. narrativeSufficiency、keyEntities、plotBeats、motifTerms、trigger、lifeContext、conflictTypes、agencyModes、endingMode 都必须符合原文，不能因证据中偶然出现相关词就成立。尤其注意目录/残文不应强标为完整叙事，任官不自动等于工作压力，被动受害不等于忍耐/牺牲/对抗，任意变化不等于转化结局。
3. evidence 摘录即使逐字可定位，也必须在语义上真正支持其 supports 声明；实体角色、情节节点、解释风险亦不得由无关摘录支撑。
4. 安全审校既要指出明确漏标，也可用 safety_overreach 指出模型旗标或 B 类保守补标在当前语境中明显不恰当；interpretationRisks 也须有当前原文语义依据。lockedSafetyFlags 由 A 类明确规则从原文重算并含必要跨标签，只可保留，不得质疑或删除。

判定标签时使用以下边界：
- narrativeSufficiency：sufficient=足以形成可理解事件；insufficient=目录、残缺或信息不足；unknown=无法可靠判断。
- lifeContext：relationship_boundary=关系边界；family_duty=亲属义务；belonging_isolation=接纳/排斥/孤立；separation_loss=分离或丧失；work_study_pressure=明确工作/学业压力；long_term_responsibility=持续义务；choice_uncertainty=明确选择犹豫；injustice_conflict=不公/冤屈/权利冲突；identity_transition=身份角色实质改变；persistence_change=持续努力或改变过程。
- conflictTypes：self=内在冲突；relational=人物间冲突；institutional_collective=制度/群体权力；nature_fate=自然、超自然或命运限制；knowledge_uncertainty=事实真伪/认知不确定。
- agencyModes：endure=有意识承受持续困境；avoid=主动回避；seek_help=明确求助；negotiate=协商；confront=主动正面对抗；transform_method=主动改换方法；withdraw=主动退出；sacrifice=主动放弃重要利益；collective_action=多人协调行动；证据不足为 unknown。
- endingMode：restoration=恢复原秩序；transformation=形成持续新状态；separation=以分离告终；sacrifice=以主动牺牲告终；unresolved=冲突未决；cautionary=明确警示后果；open=开放结局；证据不足为 unknown。
- autoSafetyScreen.status：auto_screened=候选已按当前原文完成自动初筛，不能解释成人工确认安全；unknown=文本或语境使自动判断仍不可靠。若明确风险已有逐字证据却仅因没有 lifeContext 而写 unknown，应指出 status 错配；uncertainties 若与原文、flags 或 status 自相矛盾，也必须指出。
- flags：death=人死亡；violence=攻击、杀害、酷刑或其他暴力行为；physical_injury=人身体受伤；self_harm_or_suicide=自伤或自杀行为/企图；sexual_content=明确性行为或性情境；sexual_violence=强奸、逼奸等性暴力；coercion_or_abuse=逼迫、威胁、虐待或权力滥用；child_harm=儿童受伤害或被置于明确危险；animal_harm=动物被伤害、杀死、剥皮、烹食；discrimination=基于身份的贬损或排斥；captivity=囚禁、禁锢或强制拘束；supernatural_horror=具有明确恐惧、威胁或伤害的超自然情境；grief_or_bereavement=人物明确经历哀伤或丧亲，不是仅提到他人死亡；illness=明确疾病、病痛或身体/精神耗损；确无这些风险才可 none_identified。sexual_violence 必须同时有 sexual_content 与 coercion_or_abuse；lockedSafetyFlags 中的 A 类明确标签不可删除或质疑。
- interpretationRisks：只能判断当前文本是否确有美化自我牺牲、常态化暴力、责怪受害者、宿命论、性别刻板、孝道强迫、复仇即正义或权威服从风险；不能因出现相邻题材自动成立。

verdict=pass 时 issues 必须为空；发现明确错误用 revise 并至少给一项 issue；无法可靠判断用 uncertain 并说明疑点。每个 sourceExcerpt 必须是该条 sourceText 中连续、逐字一致、2至80字的原文。不要输出修订后的标注，不要输出思考过程。

只输出 JSON 对象 {"reviews":[...]}。issue 只含 code、field、sourceExcerpt、correctionHint，并严格使用给定枚举。不得跨条借用事实，必须原样返回每个 entryId，且返回 ID 集合与请求完全一致。"""


SYSTEM_PROMPT = """你是《梦蝶记》的独立 A1 语义审校器。candidate 可能有错；每条当前 sourceText 是唯一事实来源。不得调用常识、其他版本、篇名典故或后世流传情节，不得改写原文，也不得把模型自己的偏好当成错误。

【强制核验程序】每条必须依次完成，不能因 candidate 看似合理而跳过：
1. 先逐项读取 candidate 的实际值，包括空数组、unknown、none_identified、摘要、每个标签和每条 evidence.supports；不得臆测 candidate 没写的内容。
2. 再通读完整 sourceText，不只看 candidate evidence。按主体、动作、对象、否定、条件、时态、地点、数量、关系、因果、传闻/梦境、结果逐项核对；一个 sourceText 可含附记、次要人物或多个故事/见闻单元。
3. 对 checklist 的 15 个字段逐一做双向核验：既查 candidate 中每个值是否有原文依据，也从原文反向扫描 candidate 是否漏掉必须标注的明确内容。空数组和 verdict=pass 尤其要反向查漏。
4. 只有字段完全不需要修改才写 true；发现至少一个必须修改的明确错误写 false，并为该字段给出 issue。issues 中出现的字段必须为 false；每个 false 字段必须至少有一个同字段 issue。

【判错阈值】只报告 candidate 必须修改的明确、实质性错误。另一种也可接受的标签、更佳措辞、偏好的详略、需结合外部材料或证据不足，都不得判 revise。摘要是检索用压缩释义，不要求穷尽原文；只有改写事实或遗漏导致实质误导时才报错。keyEntities、plotBeats、motifTerms 也不是穷举清单，不得因少写次要人物或事件就报错。不得把 candidate 已经写出的事实再次报为遗漏。

若 correctionHint 会包含“正确、合理、可保留、保留即可、无需修改、不需修改、需确认、可能、建议”等软语义，说明这不是明确的 revise issue：不要输出该 revise issue。真正无法可靠判断时用 verdict=uncertain，而不是伪装成 revise。不得输出“基本相符但可优化”之类 correct-but-issue。

【字段边界】
- modernRetrievalSummary：不得换主体、动作、对象、地点、数量、关系或因果；不得把未然、条件、传闻、梦境写成既成事实，不得改变时间或夸大结果。每条 summary evidence 只需支持摘要相应局部，不要求单条摘录支持整篇。
- narrativeSufficiency：sufficient=足以形成可理解事件；insufficient=目录、残缺或信息不足；unknown=原文确实无法可靠判断。发现—转卖—识别等短而完整的事件仍可 sufficient。
- keyEntities / plotBeats / motifTerms：只核验候选实际项与足以支撑检索/映射的关键遗漏，不要求覆盖全部细节。多故事/见闻单元可以有多个 trigger 和多组节点。
- lifeContext：relationship_boundary=关系边界；family_duty=亲属义务；belonging_isolation=接纳/排斥/孤立；separation_loss=分离或丧失；work_study_pressure=明确工作/学业压力；long_term_responsibility=持续义务；choice_uncertainty=明确选择犹豫；injustice_conflict=不公、冤屈或权利冲突；identity_transition=身份/角色实质改变；persistence_change=持续努力或改变过程。乞儿成为将军等身份变化必须反向检查；任官本身不自动等于工作压力。
- conflictTypes：self=内在冲突；relational=人物间冲突；institutional_collective=制度/群体权力；nature_fate=自然、超自然或命运限制；knowledge_uncertainty=事实真伪/认知不确定。
- agencyModes：endure=人物有意识承受持续困境，不能把悲伤、患病、被动受害或事情落在身上自动写成 endure；avoid=主动回避；seek_help=明确求助；negotiate=协商；confront=主动正面抵抗，包括阻挡、叫阵、攻击和自卫，不要求人物先挑起冲突；transform_method=主动改换方法；withdraw=主动退出；sacrifice=主动放弃重要利益；collective_action=多人协调行动；证据不足才为 unknown。不同 agency 可共存，按动作判断，不能因善意、逃生、祈祷等动机删掉实际 confront。
- endingMode：transformation=形成持续新状态，包括任职、致富/继承财富、无后、改过、筑成工程、长期隐居等；不能因后文另有人死亡就自动抹去已形成的持续状态。明确永久诀别或离去可为 separation。restoration=恢复原秩序；sacrifice=以主动牺牲告终；unresolved=冲突未决；cautionary=明确警示后果；open=开放结局；证据不足为 unknown。普通瞬时变化不自动等于 transformation。

【安全字段必须双向逐词表核验】安全 flags 是描述性内容警示，不是道德或法律判断；附记、次要故事和次要人物也属于整条 sourceText 的扫描范围。
- status：auto_screened 表示候选已按当前原文完成自动初筛，可与非空 flags 共存，不表示人工确认安全；unknown 只能表示文本/语境使自动判断不可靠，不能仅因“未人工复核”或已有明确风险而写 unknown。status、flags、uncertainties 不得自相矛盾。
- flags：death=任何人物死亡；violence=攻击、杀害、杖责、惩戒、自卫、酷刑等暴力行为；physical_injury=人体受伤；self_harm_or_suicide=自伤、自杀、企图或有意割伤自己身体，不要求想死，也不因逃生/孝行等动机豁免；sexual_content=明确性行为、同寝/交欢等性情境，双方同意或篇幅短仍成立；sexual_violence=强奸、逼奸等，且必须同时有 sexual_content 与 coercion_or_abuse；coercion_or_abuse=逼迫、威胁、虐待或权力滥用；child_harm=儿童受伤害或被置于明确危险；animal_harm=动物受伤、被杀、剥皮或烹食；discrimination=基于身份的贬损或排斥；captivity=囚禁、禁锢、投辖下钥或强制拘束，合法羁押仍成立；supernatural_horror=明确恐惧、威胁或伤害的超自然情境；grief_or_bereavement=人物明确经历哀伤或丧亲；illness=明确疾病、病痛或身体/精神耗损，短暂疾病也成立。确无风险才可 none_identified。尤其反向检查 death、child_harm、animal_harm、illness、supernatural_horror 及空 flags。
- interpretationRisks：只有当前文本确有美化自我牺牲、常态化暴力、责怪受害者、宿命论、性别刻板、孝道强迫、复仇即正义或权威服从风险才标；仅描写复仇、孝行、暴力或权威不自动成立。
- lockedSafetyFlags 是依据 A 类逐字规则重算并包含必要跨标签的不可删除子集，不得质疑。safety_overreach 只用于当前语境确定为词形误触、反事实/引文等明确误标；不得因羁押合法、性行为自愿、暴力属于惩戒/自卫、疾病短暂或行为动机良善而删标。

【证据与标签的分离】evidence 摘录必须逐字可定位且语义上支持其 supports 声明。但若某标签在 candidate evidence 中被错配、在 sourceText 其他位置仍有明确支持，只把 evidence.supports 设为 false 并报告 evidence_support_mismatch；不得因此把正确的全局标签字段设为 false，或建议删除标签。例如某处“小棺滴血”不证明死亡，但后文“党羽尽诛，陈尸”仍可支持 death/violence；一处击打未写伤，不代表后文“破头烂额”不能支持 physical_injury。

【已知假阳性，明确禁止】
- candidate 已写“藏于密室并同寝”“设计使人得千金”“称米为蛆虫”或“死后复现/题诗/入山”时，不得再报这些事实遗漏。
- 不得说“合法囹圄所以不是 captivity”“自愿同寝所以不是 sexual_content”“管教/自卫所以不是 violence”“割自己身体但无自杀意图所以不是 self_harm”。
- 不得说“横身阻挡、当面叫阵、自卫斲虎不算 confront”，也不得把悲伤、成疾或主动绝粒修道自动判为 endure。
- 不得说“继承万金、仇家无后、任官、改过、筑城完成不算 transformation”。
- 不得因某一 evidence 不足就删除原文其他位置已支持的标签；不得因标签来自附记或次要人物就忽略。

【issue 合同与代码—字段映射】
summary_actor / summary_action_object / summary_modality_negation / summary_time / summary_outcome / summary_location / summary_quantity / summary_relation / summary_causality / summary_other_fact / other_version -> modernRetrievalSummary；narrative_sufficiency_mismatch -> narrativeSufficiency；key_entity_mismatch -> keyEntities；plot_beat_mismatch -> plotBeats；motif_mismatch -> motifTerms；label_life_context -> lifeContext；trigger_mismatch -> narrativeArc.trigger；label_conflict -> narrativeArc.conflictTypes；label_agency -> narrativeArc.agencyModes；label_ending -> narrativeArc.endingMode；safety_status_mismatch -> autoSafetyScreen.status；safety_omission 或 safety_overreach -> autoSafetyScreen.flags；safety_uncertainty_mismatch -> autoSafetyScreen.uncertainties；interpretation_risk_mismatch -> autoSafetyScreen.interpretationRisks；evidence_support_mismatch -> evidence.supports。

verdict=pass 时 15 项 checklist 必须全部 true 且 issues=[]。发现明确错误用 revise；真正无法可靠判断用 uncertain。每个 issue 只含 code、field、sourceExcerpt、correctionHint；sourceExcerpt 必须是该条 sourceText 中连续、逐字一致的 2 至 80 字。不要输出修订后的标注、评分或思考过程。不得跨条借用事实，必须原样返回每个 entryId，返回 ID 集合与请求完全一致。

只输出严格 JSON 对象 {"reviews":[...]}，结构示例：
{"reviews":[{"entryId":"c1ws_0123456789abcdef01234567","verdict":"pass","checklist":{"modernRetrievalSummary":true,"narrativeSufficiency":true,"keyEntities":true,"plotBeats":true,"motifTerms":true,"lifeContext":true,"narrativeArc.trigger":true,"narrativeArc.conflictTypes":true,"narrativeArc.agencyModes":true,"narrativeArc.endingMode":true,"autoSafetyScreen.status":true,"autoSafetyScreen.flags":true,"autoSafetyScreen.interpretationRisks":true,"autoSafetyScreen.uncertainties":true,"evidence.supports":true},"issues":[]}]}"""


class ReviewValidationError(RuntimeError):
    """A provider response cannot be admitted to the semantic-review ledger."""


class RetryableProviderError(RuntimeError):
    """A provider or transport problem can be resumed later."""


class FatalProviderError(RuntimeError):
    """Provider configuration is invalid and the run must stop."""


@dataclass(frozen=True)
class ReviewCandidate:
    entry_id: str
    source_work_id: str
    source_work_title: str
    title: str
    source_locator: str
    entry_ordinal: int
    char_count: int
    source_text: str
    source_text_sha256: str
    candidate_annotation_sha256: str
    candidate_projection: dict[str, Any]
    locked_safety_flags: tuple[str, ...]

    def provider_input(self) -> dict[str, Any]:
        """Return the complete, never-truncated input retained in the sidecar."""
        return {
            "entryId": self.entry_id,
            "work": self.source_work_title,
            "title": self.title,
            "locator": self.source_locator,
            "sourceTextSha256": self.source_text_sha256,
            "candidateAnnotationSha256": self.candidate_annotation_sha256,
            "sourceText": self.source_text,
            "lockedSafetyFlags": list(self.locked_safety_flags),
            "candidate": self.candidate_projection,
        }


@dataclass(frozen=True)
class WorkerResult:
    worker_id: int
    batches_processed: int
    provider_calls: int
    fatal_reason: str | None = None
    unexpected_reason: str | None = None


class ProviderCallBudget:
    """A process-wide hard cap shared by all reviewer workers."""

    def __init__(self, max_calls: int) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        self.max_calls = max_calls
        self.used = 0
        self.exhausted = False
        self._lock = Lock()

    def reserve(self) -> None:
        with self._lock:
            if self.used >= self.max_calls:
                self.exhausted = True
                raise ProviderCallBudgetExceeded("provider call budget exhausted")
            self.used += 1


class ProviderCallBudgetExceeded(RuntimeError):
    """The shared paid-call ceiling stopped a resumable review run."""


class ProgressTracker:
    def __init__(self, total_batches: int) -> None:
        self.total_batches = total_batches
        self.completed_batches = 0
        self.provider_calls = 0
        self._lock = Lock()

    def record_batch(
        self,
        *,
        worker_id: int,
        batch_index: int,
        records_in_batch: int,
        provider_calls: int,
        local_counts: dict[str, int],
        global_counts: dict[str, int],
        outcome: str,
    ) -> None:
        with self._lock:
            self.completed_batches += 1
            self.provider_calls += provider_calls
            print(
                json.dumps(
                    {
                        "event": "progress",
                        "worker": worker_id,
                        "batch": batch_index,
                        "batches": self.total_batches,
                        "completedBatches": self.completed_batches,
                        "recordsInBatch": records_in_batch,
                        "providerCallsThisBatch": provider_calls,
                        "currentRunProviderCalls": self.provider_calls,
                        "batchStatusCounts": local_counts,
                        "statusCounts": global_counts,
                        "outcome": outcome,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compact_error(exc: BaseException) -> str:
    message = re.sub(r"\s+", " ", str(exc)).strip()
    message = re.sub(r"sk-[A-Za-z0-9._-]+", "[redacted]", message)
    return message[:400]


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def deterministic_locked_safety_flags(source_text: str) -> tuple[str, ...]:
    """Recompute only A-class flags and their deterministic cross-labels."""
    flags: set[str] = set()
    for rule in A_SAFETY_PHRASE_RULES:
        if rule.pattern.search(source_text):
            flags.update(rule.required_flags)
    if SELF_HARM_DEATH_PATTERN.search(source_text):
        flags.update({"self_harm_or_suicide", "death"})
    if "sexual_violence" in flags:
        flags.update({"sexual_content", "coercion_or_abuse"})
    return tuple(sorted(flags))


def ensure_locked_safety_flags_present(
    *, entry_id: str, candidate_flags: Iterable[str], locked_flags: Iterable[str]
) -> None:
    missing = sorted(set(locked_flags) - set(candidate_flags))
    if missing:
        raise RuntimeError(
            f"valid A1 row is missing deterministic safety flags for "
            f"{entry_id}: {','.join(missing)}"
        )


def annotation_projection(annotation: dict[str, Any]) -> dict[str, Any]:
    record = annotation["annotation_record"]
    retrieval = record["retrieval_profile"]
    arc = record["narrative_arc"]
    safety = record["auto_safety_screen"]
    return {
        "modernRetrievalSummary": retrieval["modern_retrieval_summary"],
        "narrativeSufficiency": retrieval["narrative_sufficiency"],
        "narrativeSufficiencyReason": retrieval["narrative_sufficiency_reason"],
        "keyEntities": retrieval["key_entities"],
        "plotBeats": retrieval["plot_beats"],
        "motifTerms": retrieval["motif_terms"],
        "summaryEvidenceIds": retrieval["summary_evidence_ids"],
        "lifeContext": record["life_context"],
        "narrativeArc": {
            "trigger": arc["trigger"],
            "conflictTypes": arc["conflict_types"],
            "agencyModes": arc["agency_modes"],
            "endingMode": arc["ending_mode"],
        },
        "autoSafetyScreen": {
            "status": safety["status"],
            "flags": safety["flags"],
            "interpretationRisks": safety["interpretation_risks"],
            "uncertainties": safety["uncertainties"],
        },
        "evidence": [
            {
                "id": item["id"],
                "excerpt": item["excerpt"],
                "supports": item["supports"],
            }
            for item in record["evidence"]
        ],
    }


def load_review_candidates(
    source_db: Path, annotation_db: Path
) -> tuple[list[ReviewCandidate], int]:
    """Join valid A1 rows to canonical C1 source rows without writing either DB."""
    with readonly_connection(source_db) as conn:
        source_rows = conn.execute(
            """
            SELECT entry_id, source_work_id, source_work_title, title,
                   source_locator, entry_ordinal, char_count, text,
                   extracted_text_sha256
            FROM entries
            WHERE dedupe_status='canonical' AND runtime_eligible=1
            ORDER BY source_work_id, entry_ordinal, entry_id
            """
        ).fetchall()
    if not source_rows:
        raise RuntimeError("source query returned no canonical runtime records")

    with readonly_connection(annotation_db) as conn:
        annotation_rows = conn.execute(
            """
            SELECT entry_id, source_text_sha256, annotation_json
            FROM annotation_jobs WHERE status='valid'
            """
        ).fetchall()
    annotations = {row["entry_id"]: row for row in annotation_rows}
    source_ids = {row["entry_id"] for row in source_rows}
    orphan_ids = sorted(set(annotations) - source_ids)
    if orphan_ids:
        raise RuntimeError(f"valid A1 rows include {len(orphan_ids)} non-canonical source IDs")

    candidates: list[ReviewCandidate] = []
    for source in source_rows:
        row = annotations.get(source["entry_id"])
        if row is None:
            continue
        source_text = str(source["text"])
        if int(source["char_count"]) != len(source_text):
            raise RuntimeError(f"C1 char_count mismatch for {source['entry_id']}")
        source_hash = sha256_text(source_text)
        if source_hash != source["extracted_text_sha256"]:
            raise RuntimeError(f"C1 source hash mismatch for {source['entry_id']}")
        if source_hash != row["source_text_sha256"]:
            raise RuntimeError(f"A1 ledger source hash mismatch for {source['entry_id']}")
        raw_annotation = row["annotation_json"]
        if not isinstance(raw_annotation, str) or not raw_annotation:
            raise RuntimeError(f"valid A1 row lacks annotation JSON for {source['entry_id']}")
        try:
            annotation = json.loads(raw_annotation)
            record = annotation["annotation_record"]
            if record["unit"]["unit_id"] != source["entry_id"]:
                raise KeyError("unit_id")
            if record["unit"]["source_text_sha256"] != source_hash:
                raise KeyError("unit.source_text_sha256")
            if record["source_profile"]["source_text_sha256"] != source_hash:
                raise KeyError("source_profile.source_text_sha256")
            if record["source_profile"]["source_text"] != source_text:
                raise KeyError("source_profile.source_text")
            projection = annotation_projection(annotation)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"valid A1 JSON/source contract mismatch for {source['entry_id']}"
            ) from exc
        canonical_annotation = canonical_json(annotation)
        candidate_flags = set(projection["autoSafetyScreen"]["flags"])
        locked_flags = set(deterministic_locked_safety_flags(source_text))
        ensure_locked_safety_flags_present(
            entry_id=source["entry_id"],
            candidate_flags=candidate_flags,
            locked_flags=locked_flags,
        )
        candidates.append(
            ReviewCandidate(
                entry_id=source["entry_id"],
                source_work_id=source["source_work_id"],
                source_work_title=source["source_work_title"],
                title=source["title"],
                source_locator=source["source_locator"],
                entry_ordinal=int(source["entry_ordinal"]),
                char_count=int(source["char_count"]),
                source_text=source_text,
                source_text_sha256=source_hash,
                candidate_annotation_sha256=sha256_text(canonical_annotation),
                candidate_projection=projection,
                locked_safety_flags=tuple(sorted(locked_flags)),
            )
        )
    if not candidates:
        raise RuntimeError("annotation DB contains no valid A1 records to review")
    return candidates, len(source_rows)


def length_bucket(char_count: int) -> int:
    if char_count <= 100:
        return 0
    if char_count <= 500:
        return 1
    if char_count <= 1000:
        return 2
    if char_count <= 3000:
        return 3
    if char_count <= 4000:
        return 4
    return 5


def stratified_sample(
    candidates: Sequence[ReviewCandidate], limit: int
) -> list[ReviewCandidate]:
    groups: dict[tuple[str, int], list[ReviewCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(
            (candidate.source_work_id, length_bucket(candidate.char_count)), []
        ).append(candidate)
    for key, values in groups.items():
        seed = int(sha256_text(f"{key[0]}:{key[1]}:semantic-review")[:16], 16)
        random.Random(seed).shuffle(values)
    keys = sorted(groups)
    selected: list[ReviewCandidate] = []
    cursor = 0
    while keys and len(selected) < limit:
        key = keys[cursor % len(keys)]
        values = groups[key]
        if values:
            selected.append(values.pop())
        if not values:
            keys.remove(key)
            cursor = 0
        else:
            cursor += 1
    return selected


def select_candidates(
    candidates: Sequence[ReviewCandidate], *, limit: int | None, sample_mode: str
) -> list[ReviewCandidate]:
    if limit is None:
        return list(candidates)
    if limit <= 0:
        raise RuntimeError("--limit must be positive")
    capped = min(limit, len(candidates))
    if sample_mode == "stratified":
        return stratified_sample(candidates, capped)
    return list(candidates[:capped])


def select_candidates_from_id_file(
    candidates: Sequence[ReviewCandidate], path: Path
) -> list[ReviewCandidate]:
    """Select an exact, ordered calibration set without sampling drift."""
    requested = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not requested or len(requested) != len(set(requested)):
        raise SystemExit("--entry-id-file must contain unique non-empty IDs")
    by_id = {candidate.entry_id: candidate for candidate in candidates}
    missing = [entry_id for entry_id in requested if entry_id not in by_id]
    if missing:
        raise SystemExit(
            "--entry-id-file contains IDs without current valid A1 rows: "
            + ",".join(missing[:5])
        )
    return [by_id[entry_id] for entry_id in requested]


def enforce_live_scope_confirmation(
    *, live: bool, selected_count: int, corpus_count: int, confirmed: bool
) -> None:
    """Require an explicit acknowledgement whenever a live run selects all rows."""
    if live and selected_count == corpus_count and not confirmed:
        raise SystemExit("full live semantic review requires --confirm-full-run")


def enforce_disjoint_paths(
    *, source_db: Path, annotation_db: Path, sidecar_db: Path, schema: Path, manifest: Path
) -> None:
    """Prevent output parameters from overwriting an input or each other."""
    named = {
        "source-db": source_db,
        "annotation-db": annotation_db,
        "sidecar-db": sidecar_db,
        "schema": schema,
        "manifest": manifest,
    }
    by_path: dict[Path, list[str]] = {}
    for name, path in named.items():
        by_path.setdefault(path.resolve(), []).append(name)
    collisions = [names for names in by_path.values() if len(names) > 1]
    if collisions:
        rendered = "; ".join("=".join(names) for names in collisions)
        raise SystemExit(f"semantic-review input/output paths must be distinct: {rendered}")


def pack_review_batches(
    candidates: Sequence[ReviewCandidate], *, batch_size: int, char_limit: int
) -> list[list[ReviewCandidate]]:
    """Pack complete source texts; oversize records remain complete singletons."""
    batches: list[list[ReviewCandidate]] = []
    current: list[ReviewCandidate] = []
    current_chars = 0
    for candidate in candidates:
        if candidate.char_count > char_limit:
            if current:
                batches.append(current)
                current, current_chars = [], 0
            batches.append([candidate])
            continue
        if current and (
            len(current) >= batch_size
            or current_chars + candidate.char_count > char_limit
        ):
            batches.append(current)
            current, current_chars = [], 0
        current.append(candidate)
        current_chars += candidate.char_count
    if current:
        batches.append(current)
    return batches


def per_worker_rpm(total_rpm: float, active_workers: int) -> float:
    """Divide, rather than multiply, the single global request budget."""
    if active_workers <= 0:
        return 0.0
    return total_rpm / active_workers


def configure_sidecar_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def sidecar_connection(path: Path) -> sqlite3.Connection:
    return configure_sidecar_connection(
        sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    )


def init_sidecar_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sidecar_connection(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS semantic_review_jobs (
            entry_id TEXT PRIMARY KEY,
            source_text_sha256 TEXT NOT NULL,
            candidate_annotation_sha256 TEXT NOT NULL,
            source_work_id TEXT NOT NULL,
            source_work_title TEXT NOT NULL,
            title TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            input_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'queued','leased','pass','revise','uncertain',
                'retryable_failed','quarantined','stale'
            )),
            verdict TEXT CHECK(verdict IN ('pass','revise','uncertain') OR verdict IS NULL),
            review_json TEXT,
            issues_json TEXT,
            raw_response_json TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            error_kind TEXT,
            error_message TEXT,
            provider_response_id TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            model TEXT NOT NULL,
            provider_reported_model TEXT,
            prompt_version TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            review_schema_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_semantic_review_jobs_status
            ON semantic_review_jobs(status);
        CREATE TABLE IF NOT EXISTS semantic_review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail_json TEXT NOT NULL
        );
        """
    )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(semantic_review_jobs)").fetchall()
    }
    if "provider_reported_model" not in columns:
        conn.execute(
            "ALTER TABLE semantic_review_jobs ADD COLUMN provider_reported_model TEXT"
        )
    if "prompt_sha256" not in columns:
        conn.execute("ALTER TABLE semantic_review_jobs ADD COLUMN prompt_sha256 TEXT")
    conn.commit()
    return conn


def enqueue_candidates(
    conn: sqlite3.Connection,
    candidates: Sequence[ReviewCandidate],
    *,
    model: str,
    schema_sha256: str,
) -> int:
    now = utc_now()
    invalidated = 0
    with conn:
        for candidate in candidates:
            input_json = canonical_json(candidate.provider_input())
            existing = conn.execute(
                """
                SELECT source_text_sha256,candidate_annotation_sha256,
                       model,prompt_version,prompt_sha256,review_schema_sha256
                FROM semantic_review_jobs WHERE entry_id=?
                """,
                (candidate.entry_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO semantic_review_jobs(
                        entry_id,source_text_sha256,candidate_annotation_sha256,
                        source_work_id,source_work_title,title,char_count,input_json,
                        status,model,prompt_version,review_schema_sha256,
                        prompt_sha256,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?)
                    """,
                    (
                        candidate.entry_id,
                        candidate.source_text_sha256,
                        candidate.candidate_annotation_sha256,
                        candidate.source_work_id,
                        candidate.source_work_title,
                        candidate.title,
                        candidate.char_count,
                        input_json,
                        model,
                        PROMPT_VERSION,
                        schema_sha256,
                        sha256_text(SYSTEM_PROMPT),
                        now,
                        now,
                    ),
                )
                continue
            compatible = (
                existing["source_text_sha256"] == candidate.source_text_sha256
                and existing["candidate_annotation_sha256"]
                == candidate.candidate_annotation_sha256
                and existing["model"] == model
                and existing["prompt_version"] == PROMPT_VERSION
                and existing["prompt_sha256"] == sha256_text(SYSTEM_PROMPT)
                and existing["review_schema_sha256"] == schema_sha256
            )
            if not compatible:
                invalidated += 1
                conn.execute(
                    """
                    UPDATE semantic_review_jobs
                    SET source_text_sha256=?,candidate_annotation_sha256=?,
                        source_work_id=?,source_work_title=?,title=?,char_count=?,
                        input_json=?,status='stale',verdict=NULL,review_json=NULL,
                        issues_json=NULL,raw_response_json=NULL,error_kind='provenance_changed',
                        error_message=NULL,provider_response_id=NULL,prompt_tokens=NULL,
                        completion_tokens=NULL,total_tokens=NULL,model=?,
                        provider_reported_model=NULL,prompt_version=?,prompt_sha256=?,
                        review_schema_sha256=?,updated_at=?
                    WHERE entry_id=?
                    """,
                    (
                        candidate.source_text_sha256,
                        candidate.candidate_annotation_sha256,
                        candidate.source_work_id,
                        candidate.source_work_title,
                        candidate.title,
                        candidate.char_count,
                        input_json,
                        model,
                        PROMPT_VERSION,
                        sha256_text(SYSTEM_PROMPT),
                        schema_sha256,
                        now,
                        candidate.entry_id,
                    ),
                )
    return invalidated


def pending_candidates(
    conn: sqlite3.Connection,
    selected: Sequence[ReviewCandidate],
    *,
    resume: bool,
    retry_quarantined: bool = False,
) -> list[ReviewCandidate]:
    states = {
        row["entry_id"]: row["status"]
        for row in conn.execute("SELECT entry_id,status FROM semantic_review_jobs")
    }
    if not resume:
        nonfresh = [
            candidate.entry_id
            for candidate in selected
            if states.get(candidate.entry_id) not in {"queued", "stale"}
        ]
        if nonfresh:
            raise RuntimeError(
                f"{len(nonfresh)} selected reviews already started; pass --resume"
            )
        allowed = {"queued", "stale"}
    else:
        allowed = {"queued", "stale", "leased", "retryable_failed"}
        if retry_quarantined:
            allowed.add("quarantined")
    return [candidate for candidate in selected if states.get(candidate.entry_id) in allowed]


def mark_leased(conn: sqlite3.Connection, candidates: Sequence[ReviewCandidate]) -> None:
    now = utc_now()
    with conn:
        conn.executemany(
            """
            UPDATE semantic_review_jobs
            SET status='leased',attempts=attempts+1,updated_at=? WHERE entry_id=?
            """,
            [(now, candidate.entry_id) for candidate in candidates],
        )


def mark_reviews(
    conn: sqlite3.Connection,
    reviews: dict[str, dict[str, Any]],
    candidates: Sequence[ReviewCandidate],
    raw_response: dict[str, Any],
    provider_meta: dict[str, Any],
) -> None:
    usage = provider_meta.get("usage") or {}
    raw_json = canonical_json(raw_response)
    now = utc_now()
    with conn:
        for candidate in candidates:
            review = reviews[candidate.entry_id]
            verdict = review["verdict"]
            conn.execute(
                """
                UPDATE semantic_review_jobs
                SET status=?,verdict=?,review_json=?,issues_json=?,raw_response_json=?,
                    error_kind=NULL,error_message=NULL,provider_response_id=?,
                    prompt_tokens=?,completion_tokens=?,total_tokens=?,model=?,
                    provider_reported_model=?,prompt_version=?,updated_at=? WHERE entry_id=?
                """,
                (
                    verdict,
                    verdict,
                    canonical_json(review),
                    canonical_json(review["issues"]),
                    raw_json,
                    provider_meta.get("response_id") or None,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    provider_meta.get("requested_model")
                    or provider_meta.get("model")
                    or "unknown",
                    provider_meta.get("model") or None,
                    PROMPT_VERSION,
                    now,
                    candidate.entry_id,
                ),
            )


def mark_failed(
    conn: sqlite3.Connection,
    candidates: Sequence[ReviewCandidate],
    exc: BaseException,
    *,
    retryable: bool,
    raw_response: dict[str, Any] | None,
    model: str,
) -> None:
    status = "retryable_failed" if retryable else "quarantined"
    raw_json = canonical_json(raw_response) if raw_response is not None else None
    now = utc_now()
    with conn:
        conn.executemany(
            """
            UPDATE semantic_review_jobs
            SET status=?,verdict=NULL,review_json=NULL,issues_json=NULL,
                raw_response_json=COALESCE(?,raw_response_json),error_kind=?,error_message=?,model=?,
                prompt_version=?,updated_at=? WHERE entry_id=?
            """,
            [
                (
                    status,
                    raw_json,
                    type(exc).__name__,
                    compact_error(exc),
                    model,
                    PROMPT_VERSION,
                    now,
                    candidate.entry_id,
                )
                for candidate in candidates
            ],
        )


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status,COUNT(*) count FROM semantic_review_jobs GROUP BY status ORDER BY status"
        )
    }


def selected_status_counts(
    conn: sqlite3.Connection, selected: Sequence[ReviewCandidate]
) -> dict[str, int]:
    selected_ids = {candidate.entry_id for candidate in selected}
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT entry_id,status FROM semantic_review_jobs"):
        if row["entry_id"] in selected_ids:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
    return dict(sorted(counts.items()))


def batch_status_counts(
    conn: sqlite3.Connection, candidates: Sequence[ReviewCandidate]
) -> dict[str, int]:
    placeholders = ",".join("?" for _ in candidates)
    if not placeholders:
        return {}
    return {
        row["status"]: int(row["count"])
        for row in conn.execute(
            f"""
            SELECT status,COUNT(*) count FROM semantic_review_jobs
            WHERE entry_id IN ({placeholders}) GROUP BY status ORDER BY status
            """,
            [candidate.entry_id for candidate in candidates],
        )
    }


def log_event(conn: sqlite3.Connection, event_type: str, detail: dict[str, Any]) -> None:
    with conn:
        conn.execute(
            "INSERT INTO semantic_review_events(occurred_at,event_type,detail_json) VALUES(?,?,?)",
            (utc_now(), event_type, canonical_json(detail)),
        )


class DeepSeekReviewClient:
    def __init__(
        self,
        *,
        rpm: float,
        timeout: float,
        max_tokens: int,
        http_client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        live_enabled: bool | None = None,
        call_budget: ProviderCallBudget | None = None,
    ) -> None:
        load_dotenv(ROOT / ".env", override=False)
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY")
        self.base_url = (
            base_url if base_url is not None else os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")
        self.model = model if model is not None else os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        if live_enabled is None:
            live_enabled = os.getenv("ENABLE_LIVE_MODEL_GENERATION", "false").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        self.live_enabled = live_enabled
        if rpm <= 0:
            raise FatalProviderError("worker RPM must be positive")
        self.min_interval = 60.0 / rpm
        self.last_request_at = 0.0
        self.max_tokens = max_tokens
        self.call_count = 0
        self.call_budget = call_budget
        self.client = http_client if http_client is not None else httpx.Client(timeout=timeout)

    @property
    def available(self) -> bool:
        return bool(self.api_key) and bool(self.live_enabled)

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def complete(
        self,
        candidates: Sequence[ReviewCandidate],
        retries: int,
        *,
        correction: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.available:
            raise FatalProviderError("live DeepSeek semantic review is not enabled/configured")
        user_payload: dict[str, Any] = {
            "reviewVersion": REVIEW_VERSION,
            "records": [candidate.provider_input() for candidate in candidates],
        }
        if correction:
            user_payload["previousValidationError"] = correction
            user_payload["correctionInstruction"] = (
                "上一输出未通过本地契约。请重新审校全部请求记录并只返回完整 JSON；"
                "不要解释错误，不得截断或改写 sourceText。"
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
        }
        for attempt in range(retries + 1):
            wait = self.min_interval - (time.monotonic() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            self.last_request_at = time.monotonic()
            if self.call_budget is not None:
                self.call_budget.reserve()
            self.call_count += 1
            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.HTTPError as exc:
                if attempt >= retries:
                    raise RetryableProviderError(type(exc).__name__) from exc
                time.sleep(min(30.0, 2.0**attempt))
                continue
            if response.status_code in {401, 403, 404}:
                raise FatalProviderError(
                    f"provider rejected configuration ({response.status_code})"
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= retries:
                    raise RetryableProviderError(
                        f"provider status {response.status_code}"
                    )
                retry_after = response.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else min(30.0, 2.0**attempt)
                )
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise FatalProviderError(
                    f"provider rejected request ({response.status_code})"
                )
            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                if isinstance(content, str):
                    candidate_text = content.strip()
                    if candidate_text.startswith("```"):
                        candidate_text = re.sub(
                            r"^```(?:json)?\s*", "", candidate_text, flags=re.I
                        )
                        candidate_text = re.sub(r"\s*```$", "", candidate_text)
                    if not candidate_text.startswith("{"):
                        start, end = candidate_text.find("{"), candidate_text.rfind("}")
                        if start >= 0 and end > start:
                            candidate_text = candidate_text[start : end + 1]
                    parsed = json.loads(candidate_text)
                else:
                    parsed = content
                if not isinstance(parsed, dict):
                    raise ValueError("response content is not an object")
                reported_model = str(body.get("model") or self.model)
                if reported_model != self.model:
                    raise FatalProviderError(
                        "provider reported model does not match the requested model"
                    )
                return parsed, {
                    "response_id": str(body.get("id") or ""),
                    "usage": body.get("usage") or {},
                    "model": reported_model,
                    "requested_model": self.model,
                }
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ReviewValidationError("provider returned malformed JSON") from exc
        raise AssertionError("unreachable")


def canonicalize_review_response(response: dict[str, Any]) -> dict[str, Any]:
    """Map harmless JSON-mode naming drift onto the frozen review contract.

    This never changes a verdict, checklist, excerpt, or correction claim.  It
    only maps field/code aliases.  V3 deliberately rejects rather than drops
    correct-but-issue and soft revise claims so the checklist cannot be made
    internally consistent by local post-processing.
    """
    canonical = json.loads(json.dumps(response, ensure_ascii=False))
    reviews = canonical.get("reviews")
    if not isinstance(reviews, list):
        return canonical
    removal_markers = ("不应", "不應", "删除", "刪除", "移除", "过度", "過度", "误标", "誤標")
    for review in reviews:
        if not isinstance(review, dict) or not isinstance(review.get("issues"), list):
            continue
        normalized_issues: list[Any] = []
        for issue in review["issues"]:
            if not isinstance(issue, dict):
                normalized_issues.append(issue)
                continue
            hint = issue.get("correctionHint")
            raw_field = issue.get("field")
            if isinstance(raw_field, str):
                if re.fullmatch(r"evidence\[[^\]]+\]\.supports", raw_field):
                    issue["field"] = "evidence.supports"
                else:
                    issue["field"] = FIELD_ALIASES.get(raw_field, raw_field)
            field = issue.get("field")
            raw_code = issue.get("code")
            if isinstance(raw_code, str):
                issue["code"] = CODE_ALIASES.get(raw_code, raw_code)
            code = issue.get("code")
            if isinstance(field, str) and ISSUE_FIELD_BY_CODE.get(code) != field:
                if field == "autoSafetyScreen.flags":
                    issue["code"] = (
                        "safety_overreach"
                        if isinstance(hint, str)
                        and any(marker in hint for marker in removal_markers)
                        else "safety_omission"
                    )
                else:
                    inferred = DEFAULT_CODE_BY_FIELD.get(field)
                    if inferred:
                        issue["code"] = inferred
            normalized_issues.append(issue)
        review["issues"] = normalized_issues
    return canonical


def resolve_issue_excerpt(source_text: str, candidate: str) -> str | None:
    """Recover a short exact reviewer quote without admitting free-form fuzz."""
    resolved = resolve_source_excerpt(source_text, candidate)
    if resolved is not None:
        return resolved
    fragments = [
        fragment.strip()
        for fragment in re.split(r"[，。；：！？,;:!?\s]+", candidate)
        if len(fragment.strip()) >= 4
    ]
    for fragment in sorted(fragments, key=len, reverse=True):
        resolved = resolve_source_excerpt(source_text, fragment)
        if resolved is not None:
            return resolved
    return None


def parse_reviews(
    response: dict[str, Any],
    candidates: Sequence[ReviewCandidate],
    *,
    schema_validator: Draft202012Validator,
) -> dict[str, dict[str, Any]]:
    response = canonicalize_review_response(response)
    errors = sorted(
        schema_validator.iter_errors(response), key=lambda error: list(error.path)
    )
    if errors:
        first = errors[0]
        raise ReviewValidationError(
            f"review schema failed at {list(first.path)}: {first.message}"
        )
    raw_reviews = response["reviews"]
    expected = {candidate.entry_id for candidate in candidates}
    received = [review["entryId"] for review in raw_reviews]
    if set(received) != expected or len(received) != len(set(received)):
        raise ReviewValidationError(
            "response IDs do not exactly match the requested batch"
        )
    by_id = {candidate.entry_id: candidate for candidate in candidates}
    parsed: dict[str, dict[str, Any]] = {}
    for review in raw_reviews:
        candidate = by_id[review["entryId"]]
        verdict = review["verdict"]
        checklist = review["checklist"]
        issues = review["issues"]
        if verdict == "pass" and issues:
            raise ReviewValidationError("pass verdict must have no issues")
        if verdict in {"revise", "uncertain"} and not issues:
            raise ReviewValidationError(f"{verdict} verdict requires at least one issue")
        false_fields = {
            field for field in CHECKLIST_FIELDS if checklist[field] is False
        }
        if verdict == "pass" and false_fields:
            raise ReviewValidationError("pass verdict requires every checklist field true")
        seen_issues: set[tuple[str, str, str]] = set()
        issue_fields: set[str] = set()
        for issue in issues:
            code = issue["code"]
            field = issue["field"]
            if ISSUE_FIELD_BY_CODE.get(code) != field:
                raise ReviewValidationError(f"issue {code} targets an invalid field")
            issue_fields.add(field)
            hint = issue["correctionHint"]
            if verdict == "revise" and any(
                marker in hint for marker in SOFT_REVISE_HINT_MARKERS
            ):
                raise ReviewValidationError(
                    "revise issue correctionHint contains soft or correct-but-issue language"
                )
            excerpt = issue["sourceExcerpt"]
            resolved_excerpt = resolve_issue_excerpt(candidate.source_text, excerpt)
            if resolved_excerpt is None:
                raise ReviewValidationError(
                    f"issue excerpt is not exact source text for {candidate.entry_id}"
                )
            issue["sourceExcerpt"] = resolved_excerpt
            excerpt = resolved_excerpt
            signature = (code, field, excerpt)
            if signature in seen_issues:
                raise ReviewValidationError("review repeats an identical issue")
            seen_issues.add(signature)
            if code == "safety_overreach":
                candidate_flags = set(
                    candidate.candidate_projection["autoSafetyScreen"]["flags"]
                ) - {"none_identified"}
                unlocked_flags = candidate_flags - set(candidate.locked_safety_flags)
                if not unlocked_flags:
                    raise ReviewValidationError(
                        "safety_overreach requires at least one non-locked candidate flag"
                    )
        if false_fields != issue_fields:
            missing = sorted(false_fields - issue_fields)
            wrongly_checked = sorted(issue_fields - false_fields)
            detail: list[str] = []
            if missing:
                detail.append(f"false fields without issues: {','.join(missing)}")
            if wrongly_checked:
                detail.append(f"issue fields marked true: {','.join(wrongly_checked)}")
            raise ReviewValidationError(
                "checklist and issue fields do not match (" + "; ".join(detail) + ")"
            )
        parsed[candidate.entry_id] = review
    return parsed


def revalidate_stored_quarantines(
    conn: sqlite3.Connection,
    selected: Sequence[ReviewCandidate],
    *,
    schema_validator: Draft202012Validator,
    expected_model: str,
) -> int:
    """Admit same-provenance raw responses after deterministic parser upgrades."""
    by_id = {candidate.entry_id: candidate for candidate in selected}
    recovered = 0
    rows = conn.execute(
        """
        SELECT entry_id,raw_response_json,provider_response_id,prompt_tokens,
               completion_tokens,total_tokens,model,provider_reported_model,
               prompt_version
        FROM semantic_review_jobs
        WHERE status='quarantined' AND raw_response_json IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        candidate = by_id.get(row["entry_id"])
        if (
            candidate is None
            or row["prompt_version"] != PROMPT_VERSION
            or row["model"] != expected_model
        ):
            continue
        try:
            raw_response = json.loads(row["raw_response_json"])
            reviews = parse_reviews(
                raw_response, [candidate], schema_validator=schema_validator
            )
        except (json.JSONDecodeError, ReviewValidationError):
            continue
        mark_reviews(
            conn,
            reviews,
            [candidate],
            raw_response,
            {
                "response_id": row["provider_response_id"],
                "usage": {
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                    "total_tokens": row["total_tokens"],
                },
                "model": row["provider_reported_model"] or expected_model,
                "requested_model": expected_model,
            },
        )
        recovered += 1
    return recovered


def process_batch(
    conn: sqlite3.Connection,
    client: DeepSeekReviewClient,
    candidates: Sequence[ReviewCandidate],
    *,
    schema_validator: Draft202012Validator,
    retries: int,
    semantic_retries: int = 1,
    correction: str | None = None,
) -> None:
    mark_leased(conn, candidates)
    response: dict[str, Any] | None = None
    try:
        response, provider_meta = client.complete(
            candidates, retries, correction=correction
        )
        reviews = parse_reviews(
            response, candidates, schema_validator=schema_validator
        )
        mark_reviews(conn, reviews, candidates, response, provider_meta)
    except FatalProviderError:
        mark_failed(
            conn,
            candidates,
            FatalProviderError("fatal provider configuration"),
            retryable=True,
            raw_response=response,
            model=client.model,
        )
        raise
    except RetryableProviderError as exc:
        mark_failed(
            conn,
            candidates,
            exc,
            retryable=True,
            raw_response=response,
            model=client.model,
        )
    except ReviewValidationError as exc:
        if len(candidates) > 1:
            for candidate in candidates:
                process_batch(
                    conn,
                    client,
                    [candidate],
                    schema_validator=schema_validator,
                    retries=retries,
                    semantic_retries=max(0, semantic_retries - 1),
                    correction=compact_error(exc),
                )
        elif semantic_retries > 0:
            process_batch(
                conn,
                client,
                candidates,
                schema_validator=schema_validator,
                retries=retries,
                semantic_retries=semantic_retries - 1,
                correction=compact_error(exc),
            )
        else:
            mark_failed(
                conn,
                candidates,
                exc,
                retryable=False,
                raw_response=response,
                model=client.model,
            )


def review_worker(
    worker_id: int,
    work_queue: Queue[tuple[int, list[ReviewCandidate]]],
    stop_event: Event,
    progress: ProgressTracker,
    *,
    sidecar_db: Path,
    schema: dict[str, Any],
    expected_model: str,
    worker_rpm: float,
    startup_delay: float,
    timeout: float,
    max_tokens: int,
    retries: int,
    call_budget: ProviderCallBudget,
) -> WorkerResult:
    client: DeepSeekReviewClient | None = None
    conn: sqlite3.Connection | None = None
    batches_processed = 0
    fatal_reason: str | None = None
    unexpected_reason: str | None = None
    try:
        client = DeepSeekReviewClient(
            rpm=worker_rpm,
            timeout=timeout,
            max_tokens=max_tokens,
            call_budget=call_budget,
        )
        if not client.available:
            raise FatalProviderError(
                "live DeepSeek semantic review is not enabled/configured"
            )
        if client.model != expected_model:
            raise FatalProviderError("provider model changed while workers were starting")
        conn = sidecar_connection(sidecar_db)
        schema_validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        if startup_delay > 0:
            stop_event.wait(startup_delay)
        while not stop_event.is_set():
            try:
                batch_index, batch = work_queue.get_nowait()
            except Empty:
                break
            if stop_event.is_set():
                work_queue.task_done()
                break
            calls_before = client.call_count
            outcome = "completed"
            stop_after_batch = False
            try:
                process_batch(
                    conn,
                    client,
                    batch,
                    schema_validator=schema_validator,
                    retries=retries,
                )
            except FatalProviderError as exc:
                fatal_reason = compact_error(exc)
                outcome = "fatal_configuration"
                stop_after_batch = True
                stop_event.set()
            except ProviderCallBudgetExceeded as exc:
                outcome = "provider_call_budget_exhausted"
                stop_after_batch = True
                stop_event.set()
                mark_failed(
                    conn,
                    batch,
                    exc,
                    retryable=True,
                    raw_response=None,
                    model=client.model,
                )
            except Exception as exc:
                unexpected_reason = compact_error(exc)
                outcome = "unexpected_error"
                stop_after_batch = True
                stop_event.set()
                try:
                    mark_failed(
                        conn,
                        batch,
                        exc,
                        retryable=True,
                        raw_response=None,
                        model=client.model,
                    )
                except sqlite3.Error:
                    pass
            finally:
                batches_processed += 1
                try:
                    local_counts = batch_status_counts(conn, batch)
                    global_counts = status_counts(conn)
                except sqlite3.Error:
                    local_counts, global_counts = {}, {}
                progress.record_batch(
                    worker_id=worker_id,
                    batch_index=batch_index,
                    records_in_batch=len(batch),
                    provider_calls=client.call_count - calls_before,
                    local_counts=local_counts,
                    global_counts=global_counts,
                    outcome=outcome,
                )
                work_queue.task_done()
            if stop_after_batch:
                break
    except FatalProviderError as exc:
        fatal_reason = compact_error(exc)
        stop_event.set()
    except Exception as exc:
        unexpected_reason = compact_error(exc)
        stop_event.set()
    finally:
        if conn is not None:
            conn.close()
        if client is not None:
            client.close()
    return WorkerResult(
        worker_id=worker_id,
        batches_processed=batches_processed,
        provider_calls=client.call_count if client is not None else 0,
        fatal_reason=fatal_reason,
        unexpected_reason=unexpected_reason,
    )


def write_manifest(
    conn: sqlite3.Connection,
    manifest_path: Path,
    *,
    source_db: Path,
    annotation_db: Path,
    sidecar_db: Path,
    schema_path: Path,
    selected: Sequence[ReviewCandidate],
    batch_size: int,
    char_limit: int,
    model: str,
    provider_calls: int,
    workers: int,
    max_provider_calls: int,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    counts = status_counts(conn)
    selected_counts = selected_status_counts(conn, selected)
    selected_ids = {candidate.entry_id for candidate in selected}
    full_input_saved = 0
    for row in conn.execute("SELECT entry_id,input_json FROM semantic_review_jobs"):
        if row["entry_id"] in selected_ids and row["input_json"]:
            full_input_saved += 1
    manifest = {
        "review_version": REVIEW_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "review_schema": display_path(schema_path),
        "review_schema_sha256": sha256_file(schema_path),
        "source_db": display_path(source_db),
        "annotation_db_read_only": display_path(annotation_db),
        "sidecar_db": display_path(sidecar_db),
        "canonical_overwrite_count": 0,
        "model": model,
        "thinking": "disabled",
        "temperature": 0,
        "selected_records": len(selected),
        "ledger_records": sum(counts.values()),
        "status_counts": counts,
        "selected_status_counts": selected_counts,
        "current_run_provider_calls": provider_calls,
        "current_run_provider_call_limit": max_provider_calls,
        "current_run_workers": workers,
        "batch_size_max": batch_size,
        "source_character_limit_for_multi_record_batches": char_limit,
        "source_input_policy": "complete_never_truncated",
        "full_input_storage": "semantic_review_jobs.input_json",
        "full_input_saved_records": full_input_saved,
        "oversized_complete_single_records": sum(
            candidate.char_count > char_limit for candidate in selected
        ),
        "maximum_source_characters": max(
            (candidate.char_count for candidate in selected), default=0
        ),
        "truncated_source_records": 0,
        "updated_at": utc_now(),
    }
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(manifest_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Review valid A1 semantics into a resumable sidecar without changing canonical data."
        )
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--annotation-db", type=Path, default=DEFAULT_ANNOTATION_DB)
    parser.add_argument("--sidecar-db", type=Path, default=DEFAULT_SIDECAR_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--entry-id-file",
        type=Path,
        help="Exact newline-delimited entry IDs for a stable calibration cohort.",
    )
    parser.add_argument(
        "--sample-mode", choices=["stable", "stratified"], default="stable"
    )
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--batch-char-limit", type=int, default=4000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rpm", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=0,
        help="Hard process-wide call cap; 0 derives four times the planned base batches.",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-quarantined",
        action="store_true",
        help="After local revalidation, retry remaining quarantined rows through the provider.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--confirm-full-run",
        action="store_true",
        help="Required with --live when --limit is omitted.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.batch_size <= 6:
        raise SystemExit("--batch-size must be between 1 and 6")
    if not 1 <= args.workers <= 4:
        raise SystemExit("--workers must be between 1 and 4")
    if args.limit is not None and args.entry_id_file is not None:
        raise SystemExit("--limit and --entry-id-file are mutually exclusive")
    if args.batch_char_limit != 4000:
        raise SystemExit("--batch-char-limit is fixed at 4000 for this contract")
    if (
        args.rpm <= 0
        or args.retries < 0
        or args.max_tokens <= 0
        or args.max_provider_calls < 0
    ):
        raise SystemExit("RPM/max-tokens must be positive and retries non-negative")
    source_db = args.source_db.resolve()
    annotation_db = args.annotation_db.resolve()
    sidecar_db = args.sidecar_db.resolve()
    schema_path = args.schema.resolve()
    manifest_path = args.manifest.resolve()
    enforce_disjoint_paths(
        source_db=source_db,
        annotation_db=annotation_db,
        sidecar_db=sidecar_db,
        schema=schema_path,
        manifest=manifest_path,
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise RuntimeError(f"semantic-review schema is invalid: {exc}") from exc

    all_candidates, source_count = load_review_candidates(source_db, annotation_db)
    selected = (
        select_candidates_from_id_file(all_candidates, args.entry_id_file.resolve())
        if args.entry_id_file is not None
        else select_candidates(
            all_candidates, limit=args.limit, sample_mode=args.sample_mode
        )
    )
    enforce_live_scope_confirmation(
        live=bool(args.live),
        selected_count=len(selected),
        corpus_count=source_count,
        confirmed=bool(args.confirm_full_run),
    )
    if (
        args.live
        and args.limit is None
        and args.entry_id_file is None
        and len(all_candidates) != source_count
    ):
        raise SystemExit(
            "full live semantic review requires every canonical runtime C1 row "
            "to have a valid A1 annotation"
        )
    batches = pack_review_batches(
        selected, batch_size=args.batch_size, char_limit=args.batch_char_limit
    )
    max_provider_calls = args.max_provider_calls or max(1, len(batches) * 4)
    plan = {
        "sourceCanonicalRuntimeRecords": source_count,
        "annotationValidRecords": len(all_candidates),
        "selectedRecords": len(selected),
        "plannedBatches": len(batches),
        "plannedSourceCharacters": sum(candidate.char_count for candidate in selected),
        "oversizedCompleteSingleRecords": sum(
            candidate.char_count > args.batch_char_limit for candidate in selected
        ),
        "maximumSourceCharacters": max(
            (candidate.char_count for candidate in selected), default=0
        ),
        "truncatedSourceRecords": 0,
        "sampleMode": args.sample_mode,
        "batchSize": args.batch_size,
        "batchCharacterLimit": args.batch_char_limit,
        "workers": args.workers,
        "totalRequestsPerMinute": args.rpm,
        "maxProviderCalls": max_provider_calls,
        "live": bool(args.live),
    }
    print(json.dumps({"event": "plan", **plan}, ensure_ascii=False), flush=True)
    if not args.live:
        return 0

    probe = DeepSeekReviewClient(
        rpm=float(args.rpm), timeout=args.timeout, max_tokens=args.max_tokens
    )
    requested_model = probe.model
    provider_available = probe.available
    probe.close()
    if not provider_available:
        print(
            json.dumps(
                {
                    "event": "fatal",
                    "reason": "live DeepSeek semantic review is not enabled/configured",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return 3

    schema_sha256 = sha256_file(schema_path)
    run_lock = acquire_process_lock(sidecar_db.with_suffix(".reviewer.lock"))
    conn = init_sidecar_db(sidecar_db)
    try:
        invalidated = enqueue_candidates(
            conn, selected, model=requested_model, schema_sha256=schema_sha256
        )
        schema_validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        locally_recovered = revalidate_stored_quarantines(
            conn,
            selected,
            schema_validator=schema_validator,
            expected_model=requested_model,
        )
        todo = pending_candidates(
            conn,
            selected,
            resume=args.resume,
            retry_quarantined=args.retry_quarantined,
        )
        todo_batches = pack_review_batches(
            todo, batch_size=args.batch_size, char_limit=args.batch_char_limit
        )
        active_workers = min(args.workers, len(todo_batches))
        worker_rpm = per_worker_rpm(args.rpm, active_workers)
        run_detail = {
            **plan,
            "pendingRecords": len(todo),
            "pendingBatches": len(todo_batches),
            "provenanceInvalidated": invalidated,
            "locallyRecovered": locally_recovered,
            "activeWorkers": active_workers,
            "requestsPerMinutePerWorker": worker_rpm,
        }
        log_event(conn, "run_started", run_detail)
        print(
            json.dumps({"event": "run_started", **run_detail}, ensure_ascii=False),
            flush=True,
        )

        progress = ProgressTracker(len(todo_batches))
        call_budget = ProviderCallBudget(max_provider_calls)
        worker_results: list[WorkerResult] = []
        if active_workers:
            work_queue: Queue[tuple[int, list[ReviewCandidate]]] = Queue()
            for index, batch in enumerate(todo_batches, start=1):
                work_queue.put((index, batch))
            stop_event = Event()
            with ThreadPoolExecutor(
                max_workers=active_workers, thread_name_prefix="a1-semantic-reviewer"
            ) as executor:
                futures = {
                    executor.submit(
                        review_worker,
                        worker_id,
                        work_queue,
                        stop_event,
                        progress,
                        sidecar_db=sidecar_db,
                        schema=schema,
                        expected_model=requested_model,
                        worker_rpm=worker_rpm,
                        startup_delay=(worker_id - 1) * (60.0 / args.rpm),
                        timeout=args.timeout,
                        max_tokens=args.max_tokens,
                        retries=args.retries,
                        call_budget=call_budget,
                    ): worker_id
                    for worker_id in range(1, active_workers + 1)
                }
                for future in as_completed(futures):
                    worker_id = futures[future]
                    try:
                        worker_results.append(future.result())
                    except Exception as exc:
                        stop_event.set()
                        worker_results.append(
                            WorkerResult(
                                worker_id=worker_id,
                                batches_processed=0,
                                provider_calls=0,
                                unexpected_reason=compact_error(exc),
                            )
                        )

        provider_calls = sum(result.provider_calls for result in worker_results)
        fatal_reasons = [
            result.fatal_reason for result in worker_results if result.fatal_reason
        ]
        unexpected_reasons = [
            result.unexpected_reason
            for result in worker_results
            if result.unexpected_reason
        ]
        if provider_calls != progress.provider_calls:
            unexpected_reasons.append(
                "provider call accounting mismatch between workers and progress"
            )
        if provider_calls != call_budget.used:
            unexpected_reasons.append(
                "provider call accounting mismatch with the shared hard budget"
            )
        write_manifest(
            conn,
            manifest_path,
            source_db=source_db,
            annotation_db=annotation_db,
            sidecar_db=sidecar_db,
            schema_path=schema_path,
            selected=selected,
            batch_size=args.batch_size,
            char_limit=args.batch_char_limit,
            model=requested_model,
            provider_calls=provider_calls,
            workers=active_workers,
            max_provider_calls=max_provider_calls,
        )
        final_counts = status_counts(conn)
        selected_counts = selected_status_counts(conn, selected)
        terminal_detail = {
            "statusCounts": final_counts,
            "selectedStatusCounts": selected_counts,
            "providerCalls": provider_calls,
            "workers": active_workers,
            "completedBatches": progress.completed_batches,
            "plannedBatches": len(todo_batches),
            "truncatedSourceRecords": 0,
            "providerCallBudgetExhausted": call_budget.exhausted,
        }
        if fatal_reasons or unexpected_reasons:
            terminal_detail["fatalReasons"] = fatal_reasons
            terminal_detail["unexpectedReasons"] = unexpected_reasons
            log_event(conn, "run_stopped", terminal_detail)
            print(
                json.dumps(
                    {"event": "run_stopped", **terminal_detail}, ensure_ascii=False
                ),
                file=sys.stderr,
                flush=True,
            )
            return 3
        incomplete = sum(
            selected_counts.get(status, 0)
            for status in (
                "queued",
                "leased",
                "retryable_failed",
                "quarantined",
                "stale",
            )
        )
        event = "run_completed" if incomplete == 0 else "run_incomplete"
        log_event(conn, event, terminal_detail)
        print(
            json.dumps({"event": event, **terminal_detail}, ensure_ascii=False),
            flush=True,
        )
        return 0 if incomplete == 0 else 2
    finally:
        conn.close()
        release_process_lock(run_lock)


if __name__ == "__main__":
    raise SystemExit(main())
