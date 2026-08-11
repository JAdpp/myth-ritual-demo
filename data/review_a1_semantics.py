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
from typing import Any, Iterable, Sequence

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
DEFAULT_STAGE_SCHEMA = ROOT / "contracts" / "a1-semantic-review-stage.schema.json"
DEFAULT_MANIFEST = ROOT / "data" / "corpus" / "a1_semantic_reviews" / "manifest.json"

REVIEW_VERSION = "mengdie-a1-semantic-review-v4.3-nonthinking-codebook-audit"
PROMPT_VERSION = "mengdie-a1-semantic-review-v4.3-nonthinking-codebook-audit"
SQLITE_BUSY_TIMEOUT_MS = 30_000
THINKING_MODE = "disabled"
REASONING_EFFORT: str | None = None

REVIEW_MODES = ("omission", "contradiction")
STAGE_PROMPT_VERSIONS = {
    "omission": "mengdie-a1-semantic-review-v4.3a-nonthinking-omission-codebook",
    "contradiction": "mengdie-a1-semantic-review-v4.3b-nonthinking-contradiction-codebook",
}

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

# The two reviewers use directional codes.  The merged review deliberately maps
# them back onto the frozen v3 issue vocabulary consumed by the repair runner.
STAGE_ISSUE_FIELD_BY_CODE = {
    # omission-only
    "summary_material_omission": "modernRetrievalSummary",
    "narrative_sufficiency_omission": "narrativeSufficiency",
    "key_entity_omission": "keyEntities",
    "plot_beat_omission": "plotBeats",
    "motif_omission": "motifTerms",
    "life_context_omission": "lifeContext",
    "trigger_omission": "narrativeArc.trigger",
    "conflict_omission": "narrativeArc.conflictTypes",
    "agency_omission": "narrativeArc.agencyModes",
    "ending_omission": "narrativeArc.endingMode",
    "safety_status_omission": "autoSafetyScreen.status",
    "safety_flag_omission": "autoSafetyScreen.flags",
    "safety_uncertainty_omission": "autoSafetyScreen.uncertainties",
    "interpretation_risk_omission": "autoSafetyScreen.interpretationRisks",
    # contradiction-only
    "summary_actor_contradiction": "modernRetrievalSummary",
    "summary_action_object_contradiction": "modernRetrievalSummary",
    "summary_modality_negation_contradiction": "modernRetrievalSummary",
    "summary_time_contradiction": "modernRetrievalSummary",
    "summary_outcome_contradiction": "modernRetrievalSummary",
    "summary_location_contradiction": "modernRetrievalSummary",
    "summary_quantity_contradiction": "modernRetrievalSummary",
    "summary_relation_contradiction": "modernRetrievalSummary",
    "summary_causality_contradiction": "modernRetrievalSummary",
    "summary_other_fact_contradiction": "modernRetrievalSummary",
    "other_version_intrusion": "modernRetrievalSummary",
    "narrative_sufficiency_contradiction": "narrativeSufficiency",
    "key_entity_overreach": "keyEntities",
    "plot_beat_overreach": "plotBeats",
    "motif_overreach": "motifTerms",
    "life_context_overreach": "lifeContext",
    "trigger_contradiction": "narrativeArc.trigger",
    "conflict_overreach": "narrativeArc.conflictTypes",
    "agency_overreach": "narrativeArc.agencyModes",
    "ending_contradiction": "narrativeArc.endingMode",
    "safety_status_contradiction": "autoSafetyScreen.status",
    "safety_flag_overreach": "autoSafetyScreen.flags",
    "safety_uncertainty_contradiction": "autoSafetyScreen.uncertainties",
    "interpretation_risk_overreach": "autoSafetyScreen.interpretationRisks",
    "evidence_support_contradiction": "evidence.supports",
}

OMISSION_STAGE_CODES = frozenset(
    code for code in STAGE_ISSUE_FIELD_BY_CODE if code.endswith("_omission")
)
CONTRADICTION_STAGE_CODES = frozenset(STAGE_ISSUE_FIELD_BY_CODE) - OMISSION_STAGE_CODES

UNIFIED_CODE_BY_STAGE_CODE = {
    "summary_material_omission": "summary_other_fact",
    "narrative_sufficiency_omission": "narrative_sufficiency_mismatch",
    "key_entity_omission": "key_entity_mismatch",
    "plot_beat_omission": "plot_beat_mismatch",
    "motif_omission": "motif_mismatch",
    "life_context_omission": "label_life_context",
    "trigger_omission": "trigger_mismatch",
    "conflict_omission": "label_conflict",
    "agency_omission": "label_agency",
    "ending_omission": "label_ending",
    "safety_status_omission": "safety_status_mismatch",
    "safety_flag_omission": "safety_omission",
    "safety_uncertainty_omission": "safety_uncertainty_mismatch",
    "interpretation_risk_omission": "interpretation_risk_mismatch",
    "summary_actor_contradiction": "summary_actor",
    "summary_action_object_contradiction": "summary_action_object",
    "summary_modality_negation_contradiction": "summary_modality_negation",
    "summary_time_contradiction": "summary_time",
    "summary_outcome_contradiction": "summary_outcome",
    "summary_location_contradiction": "summary_location",
    "summary_quantity_contradiction": "summary_quantity",
    "summary_relation_contradiction": "summary_relation",
    "summary_causality_contradiction": "summary_causality",
    "summary_other_fact_contradiction": "summary_other_fact",
    "other_version_intrusion": "other_version",
    "narrative_sufficiency_contradiction": "narrative_sufficiency_mismatch",
    "key_entity_overreach": "key_entity_mismatch",
    "plot_beat_overreach": "plot_beat_mismatch",
    "motif_overreach": "motif_mismatch",
    "life_context_overreach": "label_life_context",
    "trigger_contradiction": "trigger_mismatch",
    "conflict_overreach": "label_conflict",
    "agency_overreach": "label_agency",
    "ending_contradiction": "label_ending",
    "safety_status_contradiction": "safety_status_mismatch",
    "safety_flag_overreach": "safety_overreach",
    "safety_uncertainty_contradiction": "safety_uncertainty_mismatch",
    "interpretation_risk_overreach": "interpretation_risk_mismatch",
    "evidence_support_contradiction": "evidence_support_mismatch",
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


LEGACY_SYSTEM_PROMPT_V3 = """你是《梦蝶记》的独立 A1 语义审校器。candidate 可能有错；每条当前 sourceText 是唯一事实来源。不得调用常识、其他版本、篇名典故或后世流传情节，不得改写原文，也不得把模型自己的偏好当成错误。

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


SHARED_SEMANTIC_CODEBOOK = """【共享封闭词表与判定边界】
以下英文值是 candidate 使用的完整封闭词表。必须按定义逐项核验，不得凭词面相似、
道德评价或个人偏好改标；同一字段可有多个值时，逐值分别判断。

- modernRetrievalSummary：是检索用压缩释义，不要求穷尽原文，但不得换主体、动作、对象、
地点、数量、关系或因果，不得把未然、条件、传闻、梦境写成既成事实，不得改变时间或
夸大结果。narrativeSufficiency：sufficient=原文足以形成可理解事件，短事件也可成立；
insufficient=目录、残缺或信息不足；unknown=原文确实无法可靠判断。keyEntities、plotBeats、
motifTerms 不是穷举，只核验足以影响检索或映射的关键项；trigger 是推动当前叙事启动的明确
事件或条件，多故事/见闻单元可以有多个关键节点，不能仅因篇幅短强判 unknown。

- lifeContext（最多 3 项）：relationship_boundary=关系中的边界、亲疏或关系冲突；
family_duty=明确的亲属照护、义务或家庭责任冲突；belonging_isolation=接纳、排斥或孤立；
separation_loss=人物经历分离或丧失；work_study_pressure=明确的工作、学业任务或压力，
不是仅仅任官、经商、作诗；long_term_responsibility=持续承担的义务，不是只出现较长时间；
choice_uncertainty=人物明确犹豫、难以选择或面对多种选择；injustice_conflict=不公、冤屈、
权利受损或与不公抗争；identity_transition=身份、角色或社会位置的实质转变；
persistence_change=持续努力、坚持或逐步改变的过程，不是一次动作。

- narrativeArc.conflictTypes（最多 2 项）：self=人物内在欲望、信念或选择冲突；
relational=人物之间的关系或利益冲突；institutional_collective=制度、官府、组织或群体权力
造成的冲突，不是普通家庭争执；nature_fate=自然、超自然力量、身体限制或命运造成的冲突；
knowledge_uncertainty=事实真伪、身份、征兆、梦境或认知上的不确定。

- narrativeArc.agencyModes（最多 2 项）：endure=人物有意识承受持续困境，不是悲伤、患病、
被动受害或事情落在其身上；avoid=人物主动回避人、事或风险；seek_help=人物明确主动求助，
不是传话、考证或被别人主动帮助；negotiate=协商、议价或寻求折中；confront=主动正面抵抗、
阻挡、叫阵、攻击或自卫，不要求人物先挑起冲突；transform_method=主动改变办法、策略或路径，
不是升官或结局自然变化；withdraw=主动退出关系、职位、行动或争端，不是被逐或被抛弃；
sacrifice=主动放弃生命、利益、身份或重要关系，不是被害；collective_action=多人协调、组织并
共同采取行动，不是单人资助或仅仅从军；unknown=原文没有足够证据判断能动方式。

- narrativeArc.endingMode（恰好 1 项）：restoration=恢复原有秩序、关系或状态；
transformation=结局形成持续的新身份、关系、处境或生活状态，包括任职、致富或继承财富、
无后、改过、工程完成、长期隐居等，不是任意瞬时变化；separation=以永久或明确分离、离去
告终；sacrifice=以人物主动牺牲重要利益或生命告终；unresolved=主要冲突仍未解决；
cautionary=以明确警示性后果或劝诫告终；open=结局有意开放、走向未定；unknown=原文证据
不足以可靠判断。人物死亡本身不是 endingMode 枚举值，须按叙事结局含义判断。

【安全字段完整词表】安全标签是描述性内容提示，不是道德、动机或法律判断；必须扫描完整
sourceText，包括附记、次要故事与次要人物。
- autoSafetyScreen.status：auto_screened=候选已按当前原文完成自动初筛，可与非空 flags
并存，不等于人工确认安全；unknown=文本残缺、指代或语境歧义等使自动判断仍不可靠，不能
仅因“未人工复核”、lifeContext 为空或已经检出明确风险而使用。status、flags、
interpretationRisks、uncertainties 必须相互一致。
- autoSafetyScreen.flags：death=任何人物明确死亡；violence=攻击、杀害、杖责、惩戒、
自卫、酷刑或其他暴力行为；physical_injury=人物身体明确受伤，不由危险、咬、推或短暂
昏厥自动推定；self_harm_or_suicide=明确自伤、自杀行为或企图，包括有意割伤自己身体，
不要求想死；sexual_content=明确性行为、同寝、交欢或性情境；sexual_violence=强奸、逼奸
等性暴力，并必须同时有 sexual_content 与 coercion_or_abuse；coercion_or_abuse=逼迫、
威胁、虐待或权力滥用；child_harm=儿童受伤害或被置于明确危险；animal_harm=动物受伤、
被杀、剥皮或烹食，不是动物仅仅出现；discrimination=基于身份的贬损、排斥或不平等对待；
captivity=囚禁、禁锢、投辖下钥或其他强制拘束；supernatural_horror=超自然情境中有明确
恐惧、威胁或伤害，不是妖鬼意象仅仅出现；grief_or_bereavement=人物明确经历哀伤或丧亲，
不是仅提到他人死亡；illness=明确疾病、病痛、衰弱、消瘦或身体/精神耗损，短暂疾病也成立；
none_identified=完整扫描后确无上述风险。none_identified 不得与任何其他 flag 并列。
- autoSafetyScreen.interpretationRisks：glorify_self_sacrifice=文本明确美化或鼓励自我牺牲；
normalize_violence=把暴力呈现为理所当然、常规或无需反思；victim_blaming=把伤害归咎于受害者；
fatalism=把人的处境绝对归因于不可改变的命运；gender_stereotype=强化性别刻板角色或能力判断；
filial_coercion=以孝道强迫人物伤害或放弃自身重要利益；revenge_as_justice=把复仇直接等同正义；
authority_obedience=把无条件服从权威呈现为应然。仅描写牺牲、暴力、受害、命运、性别、孝行、
复仇或权威不自动成立相应风险；none_identified=完整扫描后确无上述解释风险，且不得与其他值
并列。
- autoSafetyScreen.uncertainties：这是最多 6 条自由文本，仅记录会影响安全判断的具体、尚无法
消解的指代、否定、对象、文本残缺或语境歧义；没有具体歧义时应为空数组。不得写泛化的
“未人工复核”“可能有风险”，不得重复已明确的 flags，也不得与 status 或原文事实冲突。

【必须保留的关键反例】
- 合法羁押仍可为 captivity；双方同意或篇幅短仍可为 sexual_content；惩戒、自卫仍可为
violence；自伤出于逃生、孝行等动机也不取消 self_harm_or_suicide；短暂疾病仍可为 illness。
- 被动受害不自动是 endure、confront 或 sacrifice；横身阻挡、当面叫阵、攻击和自卫可为
confront。任官不自动是 work_study_pressure，但形成持续新身份可为 identity_transition 或
transformation；继承财富、仇家无后、改过、筑成工程、长期隐居也可形成 transformation。
- evidence.supports 只判断该摘录当前声明的 supports；一处摘录错配不代表原文其他位置支持的
全局标签应删除。supports 不要求穷尽该摘录可能支持的所有标签。
- 不能因标签来自附记、次要人物或次要故事就忽略；也不能把 candidate 已写出的事实再次报遗漏。
"""


STAGE_RESPONSE_CONTRACT = """本请求固定关闭 thinking。直接完成逐字段、逐枚举核验，
不得输出推理过程；最终只返回合同规定的 JSON 对象。

只报告 sourceText 明确证明且 candidate 必须修改的问题；
不确定、仅可优化、粒度偏好或另一种也可接受的标签一律不报。每个 issue 只能描述一个
字段中的一个具体事实；不得把同一事实机械复制成 15 个字段的问题。每条最多 10 个
issues，若超过，只保留对检索、映射或内容提示有实质影响且证据最明确的项目。

输出形状是硬合同：每条必须返回全部 15 个 fieldChecks；fieldChecks 必须是 JSON 对象，
禁止输出数组，禁止使用
autoSafetyScreen、narrativeArc、evidence 等聚合键，也禁止添加其他键。下列 15 个键
必须逐字照抄、每个恰好出现一次。即使没有问题，也必须显式返回 "issues":[]；不得省略
issues。每项只能是 {"checked":true,"issueCodes":[]}；若有问题，issueCodes 必须逐项
列出本字段 issues 实际使用的代码，集合须完全相等。

仅返回 JSON 对象，严格使用以下骨架；批量请求时 reviews 中为每个原样 entryId 重复一个同形对象：
{"mode":"当前模式","reviews":[{"entryId":"原样entryId","fieldChecks":{
"modernRetrievalSummary":{"checked":true,"issueCodes":[]},
"narrativeSufficiency":{"checked":true,"issueCodes":[]},
"keyEntities":{"checked":true,"issueCodes":[]},
"plotBeats":{"checked":true,"issueCodes":[]},
"motifTerms":{"checked":true,"issueCodes":[]},
"lifeContext":{"checked":true,"issueCodes":[]},
"narrativeArc.trigger":{"checked":true,"issueCodes":[]},
"narrativeArc.conflictTypes":{"checked":true,"issueCodes":[]},
"narrativeArc.agencyModes":{"checked":true,"issueCodes":[]},
"narrativeArc.endingMode":{"checked":true,"issueCodes":[]},
"autoSafetyScreen.status":{"checked":true,"issueCodes":[]},
"autoSafetyScreen.flags":{"checked":true,"issueCodes":[]},
"autoSafetyScreen.interpretationRisks":{"checked":true,"issueCodes":[]},
"autoSafetyScreen.uncertainties":{"checked":true,"issueCodes":[]},
"evidence.supports":{"checked":true,"issueCodes":[]}},"issues":[]}]}

每个 issue 只含 code、field、targetValue、sourceExcerpt、correctionHint。targetValue 必须是
本模式实际要新增或质疑的一个值，不得用“整体”“若干”“相关标签”等泛称。
sourceExcerpt 必须直接复制 sourceText 中连续、逐字一致的 2 至 80 字，不得简繁转换、
改标点、拼接或省略。不得输出思考、评分、修订后 candidate 或泛化建议。ID 集合与请求
必须完全一致。"""


OMISSION_SYSTEM_PROMPT = f"""你是《梦蝶记》A1 审校的独立 A 阶段：只查明确遗漏。
每条 sourceText 是唯一事实来源。不得使用篇名典故、常识、其他版本或后世情节。

你的唯一任务是：从完整 sourceText 反向检查 candidate 是否漏掉了会实质改变检索、
映射或内容提示的明确信息。你绝对不得删除、质疑、改写 candidate 已有的摘要事实、
标签、实体、节点或证据 supports，也不得报告“已有值不够好”。targetValue 必须是
candidate 当前确实没有的具体值；已有值再次报遗漏会被本地拒绝。
不得把 candidate 已经写出的事实再次报为遗漏。

【遗漏阶段强制扫描】先逐项读取 candidate 的实际值，包括空数组、unknown、
none_identified 和每个已有枚举；再通读完整 sourceText，对共享 codebook 中每一个
lifeContext、conflictTypes、agencyModes、endingMode、安全 flag、interpretationRisk、status
与具体 uncertainty 条件逐枚举反向查漏。candidate 字段为空也必须逐项检查，绝不能把空数组、
unknown 或 none_identified 直接当成“没有遗漏”。这是“候选已有值清点 + 完整原文反向扫描”的
双向查漏；最终只输出 candidate 当前缺少且必须补入的明确值。

{SHARED_SEMANTIC_CODEBOOK}

逐字段强制检查：modernRetrievalSummary、narrativeSufficiency、keyEntities、plotBeats、
motifTerms、lifeContext、narrativeArc.trigger、narrativeArc.conflictTypes、
narrativeArc.agencyModes、narrativeArc.endingMode、autoSafetyScreen.status/flags/
interpretationRisks/uncertainties、evidence.supports。摘要、实体、节点和母题不是穷举，
只在遗漏主角、主事件、关键条件/否定/结果或关键安全内容，致使 candidate 实质误导时
报错；次要细节、可接受的另一种粒度和更佳措辞不是遗漏。endingMode、status 等单值
字段只能在 candidate 为 unknown/空而原文给出唯一明确值时报告遗漏，不得建议并列第二
个结局。evidence.supports 明确不是穷举清单：A 阶段一律不得因为某摘录还可以支持其他
标签而报错。

安全 flags 必须先读取 candidate 实际数组再查漏。candidate 已有 violence、
physical_injury 等值时不得重复报告。death 指人物死亡；violence 指实际攻击、杀害、
酷刑、殴打、强制暴力或明确暴力围困；physical_injury 需要明确人体损伤，不可由咬、
推、短暂昏厥或危险动作自动推定；animal_harm 只指动物受伤、被杀、剥皮或烹食，
不是动物出现；captivity 指强制拘束；illness 指人物明确疾病。none_identified 与非空
风险不得并存。lockedSafetyFlags 是不可删除的确定子集，但它们在有效 candidate 中已存在，
不得重复报遗漏。

只允许以下 omission 代码，并严格对应字段：summary_material_omission；
narrative_sufficiency_omission；key_entity_omission；plot_beat_omission；motif_omission；
life_context_omission；trigger_omission；conflict_omission；agency_omission；
ending_omission；safety_status_omission；safety_flag_omission；
safety_uncertainty_omission；interpretation_risk_omission。不得使用 contradiction/
overreach 代码。correctionHint 必须明确写“补入/添加/改为”的具体值，不得包含删除、
移除、误标、过度或“不应”。

{STAGE_RESPONSE_CONTRACT}

mode 必须原样返回 omission。"""


CONTRADICTION_SYSTEM_PROMPT = f"""你是《梦蝶记》A1 审校的独立 B 阶段：只查明确矛盾。
每条 sourceText 是唯一事实来源。不得使用篇名典故、常识、其他版本或后世情节。

你的唯一任务是：逐项检查 candidate 已实际写出的值是否被 sourceText 明确反驳，或
candidate 的 evidence.supports 声明是否与该摘录语义明确不符。你绝对不得报告遗漏，
不得要求新增人物、情节、标签、风险或 supports；sourceText 没明确提到不等于已有值
必错，另一种也可接受的标签也不是矛盾。targetValue 必须逐字指向 candidate 当前已有的
一个具体值或摘要中的具体事实；质疑不存在的值会被本地拒绝。

【矛盾阶段强制扫描】逐字段读取 candidate 当前实际写出的每一个值，包括数组中的每一项、
unknown、none_identified、每条 uncertainty 和每个 evidence.supports；再按共享 codebook 的
对应定义逐项回到完整 sourceText 核验。每个现值都必须单独检查，但 candidate 未写的值和空数组
不能在本阶段报错；不得把“还可以补什么”混成矛盾。

{SHARED_SEMANTIC_CODEBOOK}

逐字段强制检查全部 15 项。摘要只查换主体、动作/对象、否定/条件/梦境/传闻、时间、
地点、数量、关系、因果或结果等明确事实矛盾；压缩省略不是矛盾。keyEntities、plotBeats、
motifTerms 只查实际项的明确错误，不以粒度偏好判错。lifeContext/arc 标签仅在定义明显
不成立时判错：被动遭遇不自动是 endure；confront 包括阻挡、叫阵、攻击和自卫；
任职、致富/继承财富、无后、改过、筑成工程、长期隐居可为 transformation；
乞儿成为将军属于明确身份转变；附记、次要故事和次要人物只在 candidate 实际写错时审查，不能
因其不是主线就自动删除；死亡不是 endingMode 枚举值。

安全标签是保守内容提示。只有原文明确证明 targetValue 是词形误触、对象并非人/动物、
否定、反事实、假设或纯引文，才可判 safety_flag_overreach；仅因行为合法、自愿、短暂、
自卫、惩戒、动机善良或未详写伤害，不足以删除相邻的保守标签：合法羁押仍成立，
双方同意或篇幅短仍成立，惩戒、自卫也不自动取消风险。self_harm_or_suicide 只要求明确自伤/
自杀行为或企图，不要求想死。lockedSafetyFlags 中任何值均不得质疑。围城可支持 violence；
同寝/交欢可支持 sexual_content；割伤自己身体可支持 self_harm_or_suicide；强制关押可
支持 captivity。interpretationRisks 只在候选实际值与
当前文本立场明确相反时判错，不因个人解释偏好删除。

evidence.supports 只检查摘录当前已经声明的 supports 是否成立；supports 不要求穷尽该
摘录可能支持的全部标签，绝对不得因“还应支持 X”报错。某一摘录不支持全局标签时，只能
质疑该摘录里现有的 supports 值（合并后只把 evidence.supports 设为 false）；
不得因此把正确的全局标签字段设为 false，也不得据此删除 sourceText 其他位置仍支持的全局标签。

只允许 contradiction/overreach 代码：summary_actor_contradiction、
summary_action_object_contradiction、summary_modality_negation_contradiction、
summary_time_contradiction、summary_outcome_contradiction、summary_location_contradiction、
summary_quantity_contradiction、summary_relation_contradiction、
summary_causality_contradiction、summary_other_fact_contradiction、other_version_intrusion、
narrative_sufficiency_contradiction、key_entity_overreach、plot_beat_overreach、
motif_overreach、life_context_overreach、trigger_contradiction、conflict_overreach、
agency_overreach、ending_contradiction、safety_status_contradiction、
safety_flag_overreach、safety_uncertainty_contradiction、interpretation_risk_overreach、
evidence_support_contradiction。不得使用 omission 代码。correctionHint 必须明确说明现有
targetValue 与哪项原文事实矛盾以及应删除或替换为何值；不得包含遗漏、补入、添加、增补。

{STAGE_RESPONSE_CONTRACT}

mode 必须原样返回 contradiction。"""


SYSTEM_PROMPTS = {
    "omission": OMISSION_SYSTEM_PROMPT,
    "contradiction": CONTRADICTION_SYSTEM_PROMPT,
}

# A single deterministic bundle is used for final-row provenance while each
# checkpointed stage also stores its own prompt hash.
SYSTEM_PROMPT = OMISSION_SYSTEM_PROMPT + "\n---CONTRADICTION---\n" + CONTRADICTION_SYSTEM_PROMPT


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
    candidate_origin: str = "canonical_a1"
    repair_iteration: int = 0

    def provider_input(self) -> dict[str, Any]:
        """Return the complete, never-truncated input retained in the sidecar."""
        return {
            "entryId": self.entry_id,
            "work": self.source_work_title,
            "title": self.title,
            "locator": self.source_locator,
            "sourceTextSha256": self.source_text_sha256,
            "candidateAnnotationSha256": self.candidate_annotation_sha256,
            "candidateOrigin": self.candidate_origin,
            "repairIteration": self.repair_iteration,
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


def validate_effective_overlay_row(
    row: sqlite3.Row,
    *,
    entry_id: str,
    source_text: str,
    source_hash: str,
    canonical_root_sha256: str,
) -> tuple[dict[str, Any], str, int]:
    """Fail closed unless a valid repair row has complete hash/lineage closure."""

    def object_json(column: str) -> dict[str, Any]:
        raw = row[column]
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(
                f"valid effective repair lacks {column} for {entry_id}"
            )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"valid effective repair has malformed {column} for {entry_id}"
            ) from exc
        if not isinstance(value, dict):
            raise RuntimeError(
                f"valid effective repair {column} is not an object for {entry_id}"
            )
        if raw != canonical_json(value):
            raise RuntimeError(
                f"valid effective repair {column} is not canonical JSON for {entry_id}"
            )
        return value

    if (
        row["source_text_sha256"] != source_hash
        or row["canonical_root_sha256"] != canonical_root_sha256
    ):
        raise RuntimeError(
            f"valid effective repair provenance mismatch for {entry_id}"
        )
    try:
        repair_iteration = int(row["repair_iteration"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"valid effective repair iteration is invalid for {entry_id}"
        ) from exc
    if repair_iteration < 1:
        raise RuntimeError(
            f"valid effective repair iteration is invalid for {entry_id}"
        )

    effective_annotation = object_json("effective_annotation_json")
    effective_sha = sha256_text(canonical_json(effective_annotation))
    if row["effective_annotation_sha256"] != effective_sha:
        raise RuntimeError(f"valid effective repair hash mismatch for {entry_id}")
    base_annotation = object_json("base_annotation_json")
    review = object_json("review_json")
    if sha256_text(canonical_json(base_annotation)) != row["base_candidate_sha256"]:
        raise RuntimeError(
            f"valid effective repair immediate-base hash mismatch for {entry_id}"
        )
    if sha256_text(canonical_json(review)) != row["review_sha256"]:
        raise RuntimeError(
            f"valid effective repair review hash mismatch for {entry_id}"
        )

    raw_lineage = row["lineage_json"]
    try:
        lineage = json.loads(raw_lineage) if isinstance(raw_lineage, str) else None
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"valid effective repair lineage is malformed for {entry_id}"
        ) from exc
    if (
        not isinstance(lineage, list)
        or len(lineage) != repair_iteration
        or raw_lineage != canonical_json(lineage)
    ):
        raise RuntimeError(
            f"valid effective repair lineage length/encoding mismatch for {entry_id}"
        )
    seen_effective: set[str] = set()
    previous_effective: str | None = None
    for expected_iteration, item in enumerate(lineage, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"valid effective repair lineage item is invalid for {entry_id}"
            )
        item_effective = str(item.get("effective_annotation_sha256") or "")
        item_base = item.get("immediate_base_sha256")
        item_parent = item.get("parent_effective_sha256")
        if (
            item.get("repair_iteration") != expected_iteration
            or item.get("canonical_root_sha256") != canonical_root_sha256
            or not re.fullmatch(r"[0-9a-f]{64}", item_effective)
            or item_effective in seen_effective
        ):
            raise RuntimeError(
                f"valid effective repair lineage iteration/root/hash mismatch for {entry_id}"
            )
        if expected_iteration == 1:
            if item_base != canonical_root_sha256 or item_parent is not None:
                raise RuntimeError(
                    f"valid effective repair first-parent closure failed for {entry_id}"
                )
        elif item_base != previous_effective or item_parent != previous_effective:
            raise RuntimeError(
                f"valid effective repair parent closure failed for {entry_id}"
            )
        seen_effective.add(item_effective)
        previous_effective = item_effective
    if previous_effective != effective_sha:
        raise RuntimeError(
            f"valid effective repair lineage does not end at current candidate for {entry_id}"
        )

    expected_origin = "canonical_a1" if repair_iteration == 1 else "effective_repair"
    expected_parent = None if repair_iteration == 1 else lineage[-2][
        "effective_annotation_sha256"
    ]
    expected_base = canonical_root_sha256 if repair_iteration == 1 else expected_parent
    if (
        row["immediate_base_origin"] != expected_origin
        or row["parent_effective_sha256"] != expected_parent
        or row["base_candidate_sha256"] != expected_base
    ):
        raise RuntimeError(
            f"valid effective repair current-parent closure failed for {entry_id}"
        )
    current_lineage = lineage[-1]
    if (
        current_lineage.get("review_sha256") != row["review_sha256"]
        or current_lineage.get("immediate_base_sha256") != row["base_candidate_sha256"]
        or current_lineage.get("parent_effective_sha256")
        != row["parent_effective_sha256"]
    ):
        raise RuntimeError(
            f"valid effective repair current-lineage metadata mismatch for {entry_id}"
        )

    envelope = object_json("effective_envelope_json")
    envelope_checks = {
        "entry_id": entry_id,
        "source_text_sha256": source_hash,
        "canonical_root_sha256": canonical_root_sha256,
        "base_candidate_sha256": row["base_candidate_sha256"],
        "parent_effective_sha256": row["parent_effective_sha256"],
        "repair_iteration": repair_iteration,
        "immediate_base_origin": row["immediate_base_origin"],
        "review_sha256": row["review_sha256"],
        "effective_annotation_sha256": effective_sha,
        "human_reviewed": False,
        "research_ready": False,
    }
    if any(envelope.get(key) != value for key, value in envelope_checks.items()):
        raise RuntimeError(
            f"valid effective repair envelope provenance mismatch for {entry_id}"
        )
    if (
        envelope.get("lineage") != lineage
        or envelope.get("base_annotation") != base_annotation
        or envelope.get("review") != review
        or envelope.get("effective_annotation") != effective_annotation
    ):
        raise RuntimeError(
            f"valid effective repair envelope content closure failed for {entry_id}"
        )

    try:
        record = effective_annotation["annotation_record"]
        meta = record["annotation_meta"]
        provenance = meta["repair_provenance"]
        if (
            record["unit"]["unit_id"] != entry_id
            or record["unit"]["source_text_sha256"] != source_hash
            or record["source_profile"]["source_text_sha256"] != source_hash
            or record["source_profile"]["source_text"] != source_text
            or meta["research_review_status"] != "not_reviewed"
            or meta["research_ready"] is not False
            or meta["human_review"]["reviewed"] is not False
            or provenance["canonical_root_sha256"] != canonical_root_sha256
            or provenance["base_candidate_sha256"] != row["base_candidate_sha256"]
            or provenance["immediate_base_sha256"] != row["base_candidate_sha256"]
            or provenance["parent_effective_sha256"]
            != row["parent_effective_sha256"]
            or provenance["repair_iteration"] != repair_iteration
            or provenance["immediate_base_origin"] != row["immediate_base_origin"]
            or provenance["review_sha256"] != row["review_sha256"]
            or provenance["automatic_semantic_repair"] is not True
            or provenance["human_reviewed"] is not False
        ):
            raise KeyError("effective provenance")
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"valid effective repair annotation provenance mismatch for {entry_id}"
        ) from exc
    return effective_annotation, effective_sha, repair_iteration


def load_review_candidates(
    source_db: Path,
    annotation_db: Path,
    effective_db: Path | None = None,
) -> tuple[list[ReviewCandidate], int]:
    """Join C1 to A1 and overlay the latest valid effective repair if supplied.

    All three inputs are opened read-only.  A valid effective row is admitted
    only when its source hash, canonical root hash, stored effective hash, and
    embedded source/unit identity close exactly.  Non-valid repair rows are
    ignored, so a partial repair ledger cannot shadow canonical A1 data.
    """
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

    effective_rows: dict[str, sqlite3.Row] = {}
    if effective_db is not None:
        if not effective_db.exists():
            raise RuntimeError(f"effective repair DB does not exist: {effective_db}")
        try:
            with readonly_connection(effective_db) as conn:
                rows = conn.execute(
                    """
                    SELECT entry_id,source_text_sha256,canonical_root_sha256,
                           base_candidate_sha256,parent_effective_sha256,
                           repair_iteration,immediate_base_origin,lineage_json,
                           review_sha256,base_annotation_json,review_json,
                           effective_annotation_json,effective_annotation_sha256,
                           effective_envelope_json
                    FROM effective_repair_jobs WHERE status='valid'
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("effective repair DB contract is unavailable") from exc
        effective_rows = {str(row["entry_id"]): row for row in rows}
        orphan_effective = sorted(set(effective_rows) - source_ids)
        if orphan_effective:
            raise RuntimeError(
                f"valid effective rows include {len(orphan_effective)} "
                "non-canonical source IDs"
            )

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
            canonical_annotation = json.loads(raw_annotation)
            record = canonical_annotation["annotation_record"]
            if record["unit"]["unit_id"] != source["entry_id"]:
                raise KeyError("unit_id")
            if record["unit"]["source_text_sha256"] != source_hash:
                raise KeyError("unit.source_text_sha256")
            if record["source_profile"]["source_text_sha256"] != source_hash:
                raise KeyError("source_profile.source_text_sha256")
            if record["source_profile"]["source_text"] != source_text:
                raise KeyError("source_profile.source_text")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"valid A1 JSON/source contract mismatch for {source['entry_id']}"
            ) from exc

        canonical_annotation_text = canonical_json(canonical_annotation)
        canonical_annotation_sha = sha256_text(canonical_annotation_text)
        annotation = canonical_annotation
        candidate_annotation_sha = canonical_annotation_sha
        candidate_origin = "canonical_a1"
        repair_iteration = 0
        effective = effective_rows.get(str(source["entry_id"]))
        if effective is not None:
            annotation, candidate_annotation_sha, repair_iteration = (
                validate_effective_overlay_row(
                    effective,
                    entry_id=str(source["entry_id"]),
                    source_text=source_text,
                    source_hash=source_hash,
                    canonical_root_sha256=canonical_annotation_sha,
                )
            )
            candidate_origin = "effective_repair"

        try:
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
                f"effective review candidate/source contract mismatch for {source['entry_id']}"
            ) from exc
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
                candidate_annotation_sha256=candidate_annotation_sha,
                candidate_projection=projection,
                locked_safety_flags=tuple(sorted(locked_flags)),
                candidate_origin=candidate_origin,
                repair_iteration=repair_iteration,
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
    *,
    source_db: Path,
    annotation_db: Path,
    sidecar_db: Path,
    schema: Path,
    manifest: Path,
    stage_schema: Path | None = None,
    effective_db: Path | None = None,
) -> None:
    """Prevent output parameters from overwriting an input or each other."""
    named = {
        "source-db": source_db,
        "annotation-db": annotation_db,
        "sidecar-db": sidecar_db,
        "schema": schema,
        "manifest": manifest,
    }
    if stage_schema is not None:
        named["stage-schema"] = stage_schema
    if effective_db is not None:
        named["effective-db"] = effective_db
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
            thinking_mode TEXT NOT NULL,
            reasoning_effort TEXT,
            provider_reported_model TEXT,
            prompt_version TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            review_schema_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_semantic_review_jobs_status
            ON semantic_review_jobs(status);
        CREATE TABLE IF NOT EXISTS semantic_review_stages (
            entry_id TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('omission','contradiction')),
            source_text_sha256 TEXT NOT NULL,
            candidate_annotation_sha256 TEXT NOT NULL,
            input_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'queued','leased','valid','retryable_failed','quarantined','stale'
            )),
            stage_review_json TEXT,
            raw_response_json TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            error_kind TEXT,
            error_message TEXT,
            provider_response_id TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            model TEXT NOT NULL,
            thinking_mode TEXT NOT NULL,
            reasoning_effort TEXT,
            provider_reported_model TEXT,
            prompt_version TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            stage_schema_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(entry_id,mode)
        );
        CREATE INDEX IF NOT EXISTS idx_semantic_review_stages_status
            ON semantic_review_stages(status,mode);
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
    for table in ("semantic_review_jobs", "semantic_review_stages"):
        table_columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "thinking_mode" not in table_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN thinking_mode TEXT")
        if "reasoning_effort" not in table_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN reasoning_effort TEXT")
    conn.commit()
    return conn


def enqueue_candidates(
    conn: sqlite3.Connection,
    candidates: Sequence[ReviewCandidate],
    *,
    model: str,
    schema_sha256: str,
    stage_schema_sha256: str | None = None,
    thinking_mode: str = THINKING_MODE,
    reasoning_effort: str | None = REASONING_EFFORT,
) -> int:
    if thinking_mode != THINKING_MODE:
        raise ValueError("v4.3 semantic review requires thinking_mode=disabled")
    if reasoning_effort is not None:
        raise ValueError("v4.3 does not send a separate reasoning_effort")
    stage_schema_sha256 = stage_schema_sha256 or schema_sha256
    now = utc_now()
    invalidated = 0
    with conn:
        for candidate in candidates:
            input_json = canonical_json(candidate.provider_input())
            candidate_invalidated = False
            existing = conn.execute(
                """
                SELECT source_text_sha256,candidate_annotation_sha256,
                       model,thinking_mode,reasoning_effort,prompt_version,
                       prompt_sha256,review_schema_sha256,status
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
                        status,model,thinking_mode,reasoning_effort,
                        prompt_version,review_schema_sha256,
                        prompt_sha256,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?)
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
                        thinking_mode,
                        reasoning_effort,
                        PROMPT_VERSION,
                        schema_sha256,
                        sha256_text(SYSTEM_PROMPT),
                        now,
                        now,
                    ),
                )
            else:
                compatible = (
                    existing["source_text_sha256"] == candidate.source_text_sha256
                    and existing["candidate_annotation_sha256"]
                    == candidate.candidate_annotation_sha256
                    and existing["model"] == model
                    and existing["thinking_mode"] == thinking_mode
                    and existing["reasoning_effort"] == reasoning_effort
                    and existing["prompt_version"] == PROMPT_VERSION
                    and existing["prompt_sha256"] == sha256_text(SYSTEM_PROMPT)
                    and existing["review_schema_sha256"] == schema_sha256
                )
                if not compatible:
                    candidate_invalidated = True
                    conn.execute(
                        """
                        UPDATE semantic_review_jobs
                        SET source_text_sha256=?,candidate_annotation_sha256=?,
                            source_work_id=?,source_work_title=?,title=?,char_count=?,
                            input_json=?,status='stale',verdict=NULL,review_json=NULL,
                            issues_json=NULL,raw_response_json=NULL,error_kind='provenance_changed',
                            error_message=NULL,provider_response_id=NULL,prompt_tokens=NULL,
                            completion_tokens=NULL,total_tokens=NULL,model=?,
                            thinking_mode=?,reasoning_effort=?,
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
                            thinking_mode,
                            reasoning_effort,
                            PROMPT_VERSION,
                            sha256_text(SYSTEM_PROMPT),
                            schema_sha256,
                            now,
                            candidate.entry_id,
                        ),
                    )
            stage_changed = False
            for mode in REVIEW_MODES:
                stage_prompt = SYSTEM_PROMPTS[mode]
                stage_existing = conn.execute(
                    """
                    SELECT source_text_sha256,candidate_annotation_sha256,model,
                           thinking_mode,reasoning_effort,prompt_version,
                           prompt_sha256,stage_schema_sha256
                    FROM semantic_review_stages WHERE entry_id=? AND mode=?
                    """,
                    (candidate.entry_id, mode),
                ).fetchone()
                stage_provenance = (
                    candidate.source_text_sha256,
                    candidate.candidate_annotation_sha256,
                    model,
                    thinking_mode,
                    reasoning_effort,
                    STAGE_PROMPT_VERSIONS[mode],
                    sha256_text(stage_prompt),
                    stage_schema_sha256,
                )
                if stage_existing is None:
                    conn.execute(
                        """
                        INSERT INTO semantic_review_stages(
                            entry_id,mode,source_text_sha256,
                            candidate_annotation_sha256,input_json,status,model,
                            thinking_mode,reasoning_effort,
                            prompt_version,prompt_sha256,stage_schema_sha256,
                            created_at,updated_at
                        ) VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?,?,?)
                        """,
                        (
                            candidate.entry_id,
                            mode,
                            candidate.source_text_sha256,
                            candidate.candidate_annotation_sha256,
                            input_json,
                            model,
                            thinking_mode,
                            reasoning_effort,
                            STAGE_PROMPT_VERSIONS[mode],
                            sha256_text(stage_prompt),
                            stage_schema_sha256,
                            now,
                            now,
                        ),
                    )
                    stage_changed = existing is not None
                    continue
                if tuple(stage_existing[key] for key in stage_existing.keys()) != stage_provenance:
                    stage_changed = True
                    conn.execute(
                        """
                        UPDATE semantic_review_stages
                        SET source_text_sha256=?,candidate_annotation_sha256=?,
                            input_json=?,status='stale',stage_review_json=NULL,
                            raw_response_json=NULL,error_kind='provenance_changed',
                            error_message=NULL,provider_response_id=NULL,
                            prompt_tokens=NULL,completion_tokens=NULL,total_tokens=NULL,
                            model=?,thinking_mode=?,reasoning_effort=?,
                            provider_reported_model=NULL,prompt_version=?,
                            prompt_sha256=?,stage_schema_sha256=?,updated_at=?
                        WHERE entry_id=? AND mode=?
                        """,
                        (
                            candidate.source_text_sha256,
                            candidate.candidate_annotation_sha256,
                            input_json,
                            model,
                            thinking_mode,
                            reasoning_effort,
                            STAGE_PROMPT_VERSIONS[mode],
                            sha256_text(stage_prompt),
                            stage_schema_sha256,
                            now,
                            candidate.entry_id,
                            mode,
                        ),
                    )
            if existing is not None and existing["status"] in {
                "pass",
                "revise",
                "uncertain",
            }:
                current_stage_states = {
                    str(row["mode"]): str(row["status"])
                    for row in conn.execute(
                        "SELECT mode,status FROM semantic_review_stages WHERE entry_id=?",
                        (candidate.entry_id,),
                    )
                }
                if current_stage_states != {
                    "omission": "valid",
                    "contradiction": "valid",
                }:
                    stage_changed = True
            if stage_changed and existing is not None:
                candidate_invalidated = True
                conn.execute(
                    """
                    UPDATE semantic_review_jobs
                    SET status='stale',verdict=NULL,review_json=NULL,
                        issues_json=NULL,raw_response_json=NULL,error_kind='provenance_changed',
                        error_message=NULL,provider_response_id=NULL,prompt_tokens=NULL,
                        completion_tokens=NULL,total_tokens=NULL,updated_at=?
                    WHERE entry_id=?
                    """,
                    (now, candidate.entry_id),
                )
            if candidate_invalidated:
                invalidated += 1
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
    stage_states: dict[str, dict[str, str]] = {}
    for row in conn.execute(
        "SELECT entry_id,mode,status FROM semantic_review_stages"
    ):
        stage_states.setdefault(str(row["entry_id"]), {})[str(row["mode"])] = str(
            row["status"]
        )

    def stages_complete(candidate: ReviewCandidate) -> bool:
        return stage_states.get(candidate.entry_id) == {
            "omission": "valid",
            "contradiction": "valid",
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
    pending: list[ReviewCandidate] = []
    for candidate in selected:
        final_state = states.get(candidate.entry_id)
        if final_state in allowed:
            pending.append(candidate)
            continue
        if resume and final_state in {"pass", "revise", "uncertain"} and not stages_complete(
            candidate
        ):
            directional_states = set(stage_states.get(candidate.entry_id, {}).values())
            if "quarantined" not in directional_states or retry_quarantined:
                pending.append(candidate)
    return pending


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


def load_valid_stage_reviews(
    conn: sqlite3.Connection,
    candidates: Sequence[ReviewCandidate],
    *,
    mode: str,
    expected_model: str,
    expected_stage_schema_sha256: str,
    stage_schema_validator: Draft202012Validator,
    expected_thinking_mode: str = THINKING_MODE,
    expected_reasoning_effort: str | None = REASONING_EFFORT,
) -> dict[str, dict[str, Any]]:
    if not candidates:
        return {}
    placeholders = ",".join("?" for _ in candidates)
    rows = conn.execute(
        f"""
        SELECT entry_id,source_text_sha256,candidate_annotation_sha256,
               input_json,stage_review_json,model,thinking_mode,reasoning_effort,
               prompt_version,prompt_sha256,stage_schema_sha256
        FROM semantic_review_stages
        WHERE mode=? AND status='valid' AND entry_id IN ({placeholders})
        """,
        [mode, *[candidate.entry_id for candidate in candidates]],
    ).fetchall()
    parsed: dict[str, dict[str, Any]] = {}
    by_id = {candidate.entry_id: candidate for candidate in candidates}
    for row in rows:
        candidate = by_id[str(row["entry_id"])]
        expected_input = canonical_json(candidate.provider_input())
        if (
            row["source_text_sha256"] != candidate.source_text_sha256
            or row["candidate_annotation_sha256"]
            != candidate.candidate_annotation_sha256
            or row["input_json"] != expected_input
            or row["model"] != expected_model
            or row["thinking_mode"] != expected_thinking_mode
            or row["reasoning_effort"] != expected_reasoning_effort
            or row["prompt_version"] != STAGE_PROMPT_VERSIONS[mode]
            or row["prompt_sha256"] != sha256_text(SYSTEM_PROMPTS[mode])
            or row["stage_schema_sha256"] != expected_stage_schema_sha256
        ):
            raise RuntimeError(
                f"valid {mode} stage provenance mismatch for {row['entry_id']}"
            )
        try:
            value = json.loads(row["stage_review_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"valid {mode} stage JSON is malformed for {row['entry_id']}"
            ) from exc
        if not isinstance(value, dict) or value.get("entryId") != row["entry_id"]:
            raise RuntimeError(
                f"valid {mode} stage identity mismatch for {row['entry_id']}"
            )
        if row["stage_review_json"] != canonical_json(value):
            raise RuntimeError(
                f"valid {mode} stage JSON is not canonical for {row['entry_id']}"
            )
        try:
            verified = parse_stage_reviews(
                {"mode": mode, "reviews": [value]},
                [candidate],
                mode=mode,
                schema_validator=stage_schema_validator,
            )
        except ReviewValidationError as exc:
            raise RuntimeError(
                f"valid {mode} stage no longer passes the current parser for "
                f"{row['entry_id']}: {compact_error(exc)}"
            ) from exc
        parsed[str(row["entry_id"])] = verified[str(row["entry_id"])]
    return parsed


def mark_stage_leased(
    conn: sqlite3.Connection,
    candidates: Sequence[ReviewCandidate],
    *,
    mode: str,
) -> None:
    now = utc_now()
    with conn:
        conn.executemany(
            """
            UPDATE semantic_review_stages
            SET status='leased',attempts=attempts+1,updated_at=?
            WHERE entry_id=? AND mode=? AND status!='valid'
            """,
            [(now, candidate.entry_id, mode) for candidate in candidates],
        )


def provider_thinking_provenance(
    provider_meta: dict[str, Any],
) -> tuple[str, None]:
    """Fail closed unless provider metadata carries the fixed v4.3 contract."""
    if provider_meta.get("thinking_mode") != THINKING_MODE:
        raise RuntimeError("provider metadata lacks thinking_mode=disabled")
    if "reasoning_effort" not in provider_meta:
        raise RuntimeError("provider metadata lacks reasoning_effort provenance")
    if provider_meta["reasoning_effort"] is not REASONING_EFFORT:
        raise RuntimeError("provider metadata reasoning_effort is not the v4.3 contract")
    return THINKING_MODE, None


def mark_stage_reviews(
    conn: sqlite3.Connection,
    reviews: dict[str, dict[str, Any]],
    candidates: Sequence[ReviewCandidate],
    *,
    mode: str,
    raw_response: dict[str, Any],
    provider_meta: dict[str, Any],
) -> None:
    thinking_mode, reasoning_effort = provider_thinking_provenance(provider_meta)
    usage = provider_meta.get("usage") or {}
    raw_json = canonical_json(raw_response)
    now = utc_now()
    with conn:
        for candidate in candidates:
            conn.execute(
                """
                UPDATE semantic_review_stages
                SET status='valid',stage_review_json=?,raw_response_json=?,
                    error_kind=NULL,error_message=NULL,provider_response_id=?,
                    prompt_tokens=?,completion_tokens=?,total_tokens=?,model=?,
                    thinking_mode=?,reasoning_effort=?,provider_reported_model=?,
                    prompt_version=?,prompt_sha256=?,
                    updated_at=? WHERE entry_id=? AND mode=?
                """,
                (
                    canonical_json(reviews[candidate.entry_id]),
                    raw_json,
                    provider_meta.get("response_id") or None,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    provider_meta.get("requested_model")
                    or provider_meta.get("model")
                    or "unknown",
                    thinking_mode,
                    reasoning_effort,
                    provider_meta.get("model") or None,
                    STAGE_PROMPT_VERSIONS[mode],
                    sha256_text(SYSTEM_PROMPTS[mode]),
                    now,
                    candidate.entry_id,
                    mode,
                ),
            )


def mark_stage_failed(
    conn: sqlite3.Connection,
    candidates: Sequence[ReviewCandidate],
    exc: BaseException,
    *,
    mode: str,
    retryable: bool,
    raw_response: dict[str, Any] | None,
    model: str,
    thinking_mode: str = THINKING_MODE,
    reasoning_effort: str | None = REASONING_EFFORT,
) -> None:
    status = "retryable_failed" if retryable else "quarantined"
    raw_json = canonical_json(raw_response) if raw_response is not None else None
    now = utc_now()
    with conn:
        conn.executemany(
            """
            UPDATE semantic_review_stages
            SET status=?,stage_review_json=NULL,
                raw_response_json=COALESCE(?,raw_response_json),error_kind=?,
                error_message=?,model=?,thinking_mode=?,reasoning_effort=?,
                prompt_version=?,prompt_sha256=?,updated_at=?
            WHERE entry_id=? AND mode=? AND status!='valid'
            """,
            [
                (
                    status,
                    raw_json,
                    type(exc).__name__,
                    compact_error(exc),
                    model,
                    thinking_mode,
                    reasoning_effort,
                    STAGE_PROMPT_VERSIONS[mode],
                    sha256_text(SYSTEM_PROMPTS[mode]),
                    now,
                    candidate.entry_id,
                    mode,
                )
                for candidate in candidates
            ],
        )


def mark_reviews(
    conn: sqlite3.Connection,
    reviews: dict[str, dict[str, Any]],
    candidates: Sequence[ReviewCandidate],
    raw_response: dict[str, Any],
    provider_meta: dict[str, Any],
) -> None:
    thinking_mode, reasoning_effort = provider_thinking_provenance(provider_meta)
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
                    thinking_mode=?,reasoning_effort=?,provider_reported_model=?,
                    prompt_version=?,updated_at=? WHERE entry_id=?
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
                    thinking_mode,
                    reasoning_effort,
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
    thinking_mode: str = THINKING_MODE,
    reasoning_effort: str | None = REASONING_EFFORT,
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
                thinking_mode=?,reasoning_effort=?,prompt_version=?,updated_at=? WHERE entry_id=?
            """,
            [
                (
                    status,
                    raw_json,
                    type(exc).__name__,
                    compact_error(exc),
                    model,
                    thinking_mode,
                    reasoning_effort,
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


def stage_status_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {mode: {} for mode in REVIEW_MODES}
    for row in conn.execute(
        """
        SELECT mode,status,COUNT(*) count FROM semantic_review_stages
        GROUP BY mode,status ORDER BY mode,status
        """
    ):
        counts[str(row["mode"])][str(row["status"])] = int(row["count"])
    return counts


def selected_stage_status_counts(
    conn: sqlite3.Connection, selected: Sequence[ReviewCandidate]
) -> dict[str, dict[str, int]]:
    selected_ids = {candidate.entry_id for candidate in selected}
    counts: dict[str, dict[str, int]] = {mode: {} for mode in REVIEW_MODES}
    for row in conn.execute(
        "SELECT entry_id,mode,status FROM semantic_review_stages"
    ):
        if row["entry_id"] not in selected_ids:
            continue
        mode = str(row["mode"])
        status = str(row["status"])
        counts[mode][status] = counts[mode].get(status, 0) + 1
    return counts


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
        thinking_mode: str = THINKING_MODE,
        reasoning_effort: str | None = REASONING_EFFORT,
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
        if thinking_mode != THINKING_MODE:
            raise FatalProviderError(
                "v4.3 semantic reviewer requires thinking_mode=disabled"
            )
        if reasoning_effort is not None:
            raise FatalProviderError(
                "v4.3 does not send a separate reasoning_effort"
            )
        self.thinking_mode = thinking_mode
        self.reasoning_effort = reasoning_effort
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
        mode: str,
        correction: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.available:
            raise FatalProviderError("live DeepSeek semantic review is not enabled/configured")
        if mode not in REVIEW_MODES:
            raise FatalProviderError(f"unsupported semantic-review mode: {mode}")
        user_payload: dict[str, Any] = {
            "reviewVersion": REVIEW_VERSION,
            "reviewMode": mode,
            "records": [candidate.provider_input() for candidate in candidates],
        }
        if correction:
            user_payload["previousValidationError"] = correction
            user_payload["correctionInstruction"] = (
                f"上一 {mode} 输出未通过本地契约。请按同一模式重新审校全部请求记录"
                "并只返回完整 JSON；"
                "不要解释错误，不得截断或改写 sourceText。"
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS[mode]},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "thinking": {"type": self.thinking_mode},
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
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
                    "review_mode": mode,
                    "thinking_mode": self.thinking_mode,
                    "reasoning_effort": self.reasoning_effort,
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


def _stage_checks_explicitly_report_no_issues(field_checks: Any) -> bool:
    """Return true only when all 15 exact checks explicitly close to no issues."""
    if not isinstance(field_checks, dict):
        return False
    if set(field_checks) != set(CHECKLIST_FIELDS):
        return False
    for check in field_checks.values():
        if not isinstance(check, dict):
            return False
        if set(check) != {"checked", "issueCodes"}:
            return False
        if check.get("checked") is not True or check.get("issueCodes") != []:
            return False
    return True


def canonicalize_stage_response_shape(response: dict[str, Any]) -> dict[str, Any]:
    """Normalize only directional JSON shapes that preserve every model claim.

    Safe repairs are deliberately narrow: a field-labelled list can become an
    object without guessing field identity, and a per-field ``issues`` key can
    be renamed to ``issueCodes``.  A missing top-level ``issues`` is supplied
    only when every one of the 15 exact checks explicitly declares no codes.
    Aggregate keys, field-less lists, duplicates, and missing issue details are
    left invalid for the schema/semantic closure checks to reject.
    """
    canonical = json.loads(json.dumps(response, ensure_ascii=False))
    reviews = canonical.get("reviews")
    if not isinstance(reviews, list):
        return canonical
    for review in reviews:
        if not isinstance(review, dict):
            continue
        field_checks = review.get("fieldChecks")
        if isinstance(field_checks, list):
            mapped: dict[str, Any] = {}
            convertible = True
            for item in field_checks:
                if not isinstance(item, dict) or not isinstance(item.get("field"), str):
                    convertible = False
                    break
                field = item["field"]
                if field in mapped:
                    convertible = False
                    break
                check = dict(item)
                del check["field"]
                mapped[field] = check
            if convertible:
                field_checks = mapped
                review["fieldChecks"] = field_checks
        if isinstance(field_checks, dict):
            for field, raw_check in list(field_checks.items()):
                if not isinstance(raw_check, dict):
                    continue
                check = dict(raw_check)
                if "issueCodes" not in check and "issues" in check:
                    check["issueCodes"] = check.pop("issues")
                field_checks[field] = check
        if "issues" not in review and _stage_checks_explicitly_report_no_issues(
            field_checks
        ):
            review["issues"] = []
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


def _candidate_values_for_field(
    candidate: ReviewCandidate, field: str
) -> tuple[str, ...]:
    """Return concrete current values usable as directional issue targets."""
    projection = candidate.candidate_projection
    if field == "modernRetrievalSummary":
        return (str(projection["modernRetrievalSummary"]),)
    if field == "narrativeSufficiency":
        return (str(projection["narrativeSufficiency"]),)
    if field == "keyEntities":
        values: list[str] = []
        for item in projection["keyEntities"]:
            values.extend(
                str(value)
                for key, value in item.items()
                if key in {"name", "role"} and isinstance(value, str)
            )
        return tuple(values)
    if field == "plotBeats":
        values = []
        for item in projection["plotBeats"]:
            values.extend(
                str(value)
                for key, value in item.items()
                if key in {"type", "text"} and isinstance(value, str)
            )
        return tuple(values)
    if field == "motifTerms":
        return tuple(str(value) for value in projection["motifTerms"])
    if field == "lifeContext":
        return tuple(str(value) for value in projection["lifeContext"])
    if field == "narrativeArc.trigger":
        return (str(projection["narrativeArc"]["trigger"]),)
    if field == "narrativeArc.conflictTypes":
        return tuple(str(value) for value in projection["narrativeArc"]["conflictTypes"])
    if field == "narrativeArc.agencyModes":
        return tuple(str(value) for value in projection["narrativeArc"]["agencyModes"])
    if field == "narrativeArc.endingMode":
        return (str(projection["narrativeArc"]["endingMode"]),)
    if field == "autoSafetyScreen.status":
        return (str(projection["autoSafetyScreen"]["status"]),)
    if field == "autoSafetyScreen.flags":
        return tuple(str(value) for value in projection["autoSafetyScreen"]["flags"])
    if field == "autoSafetyScreen.interpretationRisks":
        return tuple(
            str(value)
            for value in projection["autoSafetyScreen"]["interpretationRisks"]
        )
    if field == "autoSafetyScreen.uncertainties":
        return tuple(
            str(value) for value in projection["autoSafetyScreen"]["uncertainties"]
        )
    if field == "evidence.supports":
        return tuple(
            str(value)
            for item in projection["evidence"]
            for value in item["supports"]
        )
    raise ReviewValidationError(f"unknown stage-review field: {field}")


def _candidate_contains_target(
    candidate: ReviewCandidate, field: str, target: str
) -> bool:
    values = _candidate_values_for_field(candidate, field)
    if field in {
        "modernRetrievalSummary",
        "narrativeArc.trigger",
        "autoSafetyScreen.uncertainties",
    }:
        return any(target == value or target in value for value in values)
    return target in values


def parse_stage_reviews(
    response: dict[str, Any],
    candidates: Sequence[ReviewCandidate],
    *,
    mode: str,
    schema_validator: Draft202012Validator,
) -> dict[str, dict[str, Any]]:
    """Validate one independent directional pass without semantic repair.

    Unlike the legacy parser, this parser never infers fields or recovers quotes.
    It admits only semantics-preserving JSON-shape normalization; v4 stage
    evidence must already be a byte-for-byte substring of sourceText.
    """
    if mode not in REVIEW_MODES:
        raise ReviewValidationError(f"unknown semantic-review mode: {mode}")
    response = canonicalize_stage_response_shape(response)
    errors = sorted(
        schema_validator.iter_errors(response), key=lambda error: list(error.path)
    )
    if errors:
        first = errors[0]
        raise ReviewValidationError(
            f"stage-review schema failed at {list(first.path)}: {first.message}"
        )
    if response["mode"] != mode:
        raise ReviewValidationError(
            f"stage response mode {response['mode']} does not match {mode}"
        )
    raw_reviews = response["reviews"]
    expected = {candidate.entry_id for candidate in candidates}
    received = [review["entryId"] for review in raw_reviews]
    if set(received) != expected or len(received) != len(set(received)):
        raise ReviewValidationError(
            "stage response IDs do not exactly match the requested batch"
        )
    allowed_codes = (
        OMISSION_STAGE_CODES if mode == "omission" else CONTRADICTION_STAGE_CODES
    )
    deletion_markers = (
        "删除", "刪除", "移除", "去除", "去掉", "误标", "誤標", "过度", "過度",
        "不应", "不應",
    )
    addition_markers = (
        "遗漏", "遺漏", "补入", "補入", "补充", "補充", "添加", "加入", "增加",
        "增补", "增補",
    )
    omission_action_markers = (*addition_markers[2:], "改为", "改為", "改成")
    contradiction_action_markers = (
        *deletion_markers,
        "替换", "替換", "改为", "改為", "改成", "应为", "應為",
    )
    by_id = {candidate.entry_id: candidate for candidate in candidates}
    parsed: dict[str, dict[str, Any]] = {}
    for review in raw_reviews:
        candidate = by_id[review["entryId"]]
        checks = review["fieldChecks"]
        issues = review["issues"]
        issue_codes_by_field: dict[str, set[str]] = {
            field: set() for field in CHECKLIST_FIELDS
        }
        seen: set[tuple[str, str, str, str]] = set()
        for issue in issues:
            code = issue["code"]
            field = issue["field"]
            target = issue["targetValue"].strip()
            hint = issue["correctionHint"]
            excerpt = issue["sourceExcerpt"]
            if code not in allowed_codes:
                raise ReviewValidationError(
                    f"{mode} stage cannot emit directional code {code}"
                )
            if STAGE_ISSUE_FIELD_BY_CODE.get(code) != field:
                raise ReviewValidationError(
                    f"stage issue {code} targets an invalid field"
                )
            if target != issue["targetValue"]:
                raise ReviewValidationError("stage issue targetValue has edge whitespace")
            if not target or target in {"整体", "整體", "若干", "相关标签", "相關標籤", "相关内容", "相關內容", "字段"}:
                raise ReviewValidationError("stage issue targetValue is empty or generic")
            present = _candidate_contains_target(candidate, field, target)
            if mode == "omission" and present:
                raise ReviewValidationError(
                    f"omission target already exists in candidate field {field}: {target}"
                )
            if mode == "contradiction" and not present:
                raise ReviewValidationError(
                    f"contradiction target is absent from candidate field {field}: {target}"
                )
            if mode == "omission" and any(marker in hint for marker in deletion_markers):
                raise ReviewValidationError(
                    "omission issue correctionHint attempts deletion or criticism"
                )
            if mode == "contradiction" and any(marker in hint for marker in addition_markers):
                raise ReviewValidationError(
                    "contradiction issue correctionHint attempts to report an omission"
                )
            if mode == "omission" and not any(
                marker in hint for marker in omission_action_markers
            ):
                raise ReviewValidationError(
                    "omission issue correctionHint lacks an explicit addition action"
                )
            if mode == "contradiction" and not any(
                marker in hint for marker in contradiction_action_markers
            ):
                raise ReviewValidationError(
                    "contradiction issue correctionHint lacks an explicit removal/replacement action"
                )
            if any(marker in hint for marker in SOFT_REVISE_HINT_MARKERS):
                raise ReviewValidationError(
                    "stage issue correctionHint contains soft or advisory language"
                )
            if excerpt not in candidate.source_text:
                raise ReviewValidationError(
                    f"stage issue excerpt is not byte-exact source text for {candidate.entry_id}"
                )
            if code == "safety_flag_overreach":
                if target in candidate.locked_safety_flags:
                    raise ReviewValidationError(
                        f"contradiction stage cannot question locked safety flag {target}"
                    )
                candidate_flags = set(
                    candidate.candidate_projection["autoSafetyScreen"]["flags"]
                ) - {"none_identified"}
                if target not in candidate_flags:
                    raise ReviewValidationError(
                        "safety_flag_overreach target is not a current candidate flag"
                    )
            if code == "evidence_support_contradiction":
                linked = any(
                    target in item["supports"]
                    and (
                        excerpt == item["excerpt"]
                        or excerpt in item["excerpt"]
                        or item["excerpt"] in excerpt
                    )
                    for item in candidate.candidate_projection["evidence"]
                )
                if not linked:
                    raise ReviewValidationError(
                        "evidence_support_contradiction is not linked to one current evidence item"
                    )
            signature = (code, field, target, excerpt)
            if signature in seen:
                raise ReviewValidationError("stage review repeats an identical issue")
            seen.add(signature)
            issue_codes_by_field[field].add(code)
        for field in CHECKLIST_FIELDS:
            declared = set(checks[field]["issueCodes"])
            actual = issue_codes_by_field[field]
            if declared != actual:
                raise ReviewValidationError(
                    f"fieldChecks issueCodes mismatch for {field}: "
                    f"declared={sorted(declared)} actual={sorted(actual)}"
                )
        parsed[candidate.entry_id] = review
    return parsed


def merge_stage_reviews(
    omission_reviews: dict[str, dict[str, Any]],
    contradiction_reviews: dict[str, dict[str, Any]],
    candidates: Sequence[ReviewCandidate],
    *,
    schema_validator: Draft202012Validator | None = None,
) -> dict[str, dict[str, Any]]:
    """Deterministically merge two valid stage maps into the repair contract."""
    expected = {candidate.entry_id for candidate in candidates}
    if set(omission_reviews) != expected or set(contradiction_reviews) != expected:
        raise ReviewValidationError("both stage maps must exactly cover the batch")
    unified_reviews: list[dict[str, Any]] = []
    for candidate in candidates:
        issues: list[dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        false_fields: set[str] = set()
        for stage in (
            omission_reviews[candidate.entry_id],
            contradiction_reviews[candidate.entry_id],
        ):
            for issue in stage["issues"]:
                code = UNIFIED_CODE_BY_STAGE_CODE[issue["code"]]
                field = issue["field"]
                excerpt = issue["sourceExcerpt"]
                hint = issue["correctionHint"]
                signature = (code, field, excerpt, hint)
                if signature in seen:
                    continue
                seen.add(signature)
                false_fields.add(field)
                issues.append(
                    {
                        "code": code,
                        "field": field,
                        "sourceExcerpt": excerpt,
                        "correctionHint": hint,
                    }
                )
        issues.sort(
            key=lambda item: (
                CHECKLIST_FIELDS.index(item["field"]),
                item["code"],
                item["sourceExcerpt"],
                item["correctionHint"],
            )
        )
        unified_reviews.append(
            {
                "entryId": candidate.entry_id,
                "verdict": "revise" if issues else "pass",
                "checklist": {
                    field: field not in false_fields for field in CHECKLIST_FIELDS
                },
                "issues": issues,
            }
        )
    response = {"reviews": unified_reviews}
    if schema_validator is None:
        return {review["entryId"]: review for review in unified_reviews}
    return parse_reviews(
        response, candidates, schema_validator=schema_validator
    )


def revalidate_stored_quarantines(
    conn: sqlite3.Connection,
    selected: Sequence[ReviewCandidate],
    *,
    schema_validator: Draft202012Validator,
    expected_model: str,
    expected_schema_sha256: str,
    expected_thinking_mode: str = THINKING_MODE,
    expected_reasoning_effort: str | None = REASONING_EFFORT,
) -> int:
    """Admit same-provenance raw responses after deterministic parser upgrades."""
    by_id = {candidate.entry_id: candidate for candidate in selected}
    recovered = 0
    rows = conn.execute(
        """
        SELECT entry_id,source_text_sha256,candidate_annotation_sha256,input_json,
               raw_response_json,provider_response_id,prompt_tokens,
               completion_tokens,total_tokens,model,provider_reported_model,
               thinking_mode,reasoning_effort,prompt_version,prompt_sha256,
               review_schema_sha256
        FROM semantic_review_jobs
        WHERE status='quarantined' AND raw_response_json IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        candidate = by_id.get(row["entry_id"])
        if (
            candidate is None
            or row["prompt_version"] != PROMPT_VERSION
            or row["prompt_sha256"] != sha256_text(SYSTEM_PROMPT)
            or row["review_schema_sha256"] != expected_schema_sha256
            or row["model"] != expected_model
            or row["thinking_mode"] != expected_thinking_mode
            or row["reasoning_effort"] != expected_reasoning_effort
            or row["source_text_sha256"] != candidate.source_text_sha256
            or row["candidate_annotation_sha256"]
            != candidate.candidate_annotation_sha256
            or row["input_json"] != canonical_json(candidate.provider_input())
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
                "thinking_mode": expected_thinking_mode,
                "reasoning_effort": expected_reasoning_effort,
            },
        )
        recovered += 1
    return recovered


def revalidate_stored_stage_quarantines(
    conn: sqlite3.Connection,
    selected: Sequence[ReviewCandidate],
    *,
    stage_schema_validator: Draft202012Validator,
    expected_model: str,
    expected_stage_schema_sha256: str,
    expected_thinking_mode: str = THINKING_MODE,
    expected_reasoning_effort: str | None = REASONING_EFFORT,
) -> int:
    """Admit exact same-provenance directional responses after parser upgrades."""
    by_id = {candidate.entry_id: candidate for candidate in selected}
    recovered = 0
    rows = conn.execute(
        """
        SELECT entry_id,mode,source_text_sha256,candidate_annotation_sha256,
               input_json,raw_response_json,provider_response_id,prompt_tokens,
               completion_tokens,total_tokens,model,provider_reported_model,
               thinking_mode,reasoning_effort,prompt_version,prompt_sha256,
               stage_schema_sha256
        FROM semantic_review_stages
        WHERE status='quarantined' AND raw_response_json IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        candidate = by_id.get(str(row["entry_id"]))
        mode = str(row["mode"])
        if (
            candidate is None
            or mode not in REVIEW_MODES
            or row["prompt_version"] != STAGE_PROMPT_VERSIONS[mode]
            or row["prompt_sha256"] != sha256_text(SYSTEM_PROMPTS[mode])
            or row["stage_schema_sha256"] != expected_stage_schema_sha256
            or row["model"] != expected_model
            or row["thinking_mode"] != expected_thinking_mode
            or row["reasoning_effort"] != expected_reasoning_effort
            or row["source_text_sha256"] != candidate.source_text_sha256
            or row["candidate_annotation_sha256"]
            != candidate.candidate_annotation_sha256
            or row["input_json"] != canonical_json(candidate.provider_input())
        ):
            continue
        try:
            raw_response = json.loads(row["raw_response_json"])
            reviews = parse_stage_reviews(
                raw_response,
                [candidate],
                mode=mode,
                schema_validator=stage_schema_validator,
            )
        except (json.JSONDecodeError, ReviewValidationError):
            continue
        mark_stage_reviews(
            conn,
            reviews,
            [candidate],
            mode=mode,
            raw_response=raw_response,
            provider_meta={
                "response_id": row["provider_response_id"],
                "usage": {
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                    "total_tokens": row["total_tokens"],
                },
                "model": row["provider_reported_model"] or expected_model,
                "requested_model": expected_model,
                "thinking_mode": expected_thinking_mode,
                "reasoning_effort": expected_reasoning_effort,
            },
        )
        recovered += 1
    return recovered


def process_stage_batch(
    conn: sqlite3.Connection,
    client: DeepSeekReviewClient,
    candidates: Sequence[ReviewCandidate],
    *,
    mode: str,
    stage_schema_validator: Draft202012Validator,
    stage_schema_sha256: str,
    retries: int,
    semantic_retries: int = 1,
    correction: str | None = None,
) -> None:
    if not candidates:
        return
    mark_stage_leased(conn, candidates, mode=mode)
    response: dict[str, Any] | None = None
    try:
        response, provider_meta = client.complete(
            candidates, retries, mode=mode, correction=correction
        )
        reviews = parse_stage_reviews(
            response,
            candidates,
            mode=mode,
            schema_validator=stage_schema_validator,
        )
        mark_stage_reviews(
            conn,
            reviews,
            candidates,
            mode=mode,
            raw_response=response,
            provider_meta=provider_meta,
        )
    except FatalProviderError:
        mark_stage_failed(
            conn,
            candidates,
            FatalProviderError("fatal provider configuration"),
            mode=mode,
            retryable=True,
            raw_response=response,
            model=client.model,
            thinking_mode=client.thinking_mode,
            reasoning_effort=client.reasoning_effort,
        )
        raise
    except ProviderCallBudgetExceeded as exc:
        mark_stage_failed(
            conn,
            candidates,
            exc,
            mode=mode,
            retryable=True,
            raw_response=response,
            model=client.model,
            thinking_mode=client.thinking_mode,
            reasoning_effort=client.reasoning_effort,
        )
        raise
    except RetryableProviderError as exc:
        mark_stage_failed(
            conn,
            candidates,
            exc,
            mode=mode,
            retryable=True,
            raw_response=response,
            model=client.model,
            thinking_mode=client.thinking_mode,
            reasoning_effort=client.reasoning_effort,
        )
    except ReviewValidationError as exc:
        if len(candidates) > 1:
            for candidate in candidates:
                process_stage_batch(
                    conn,
                    client,
                    [candidate],
                    mode=mode,
                    stage_schema_validator=stage_schema_validator,
                    stage_schema_sha256=stage_schema_sha256,
                    retries=retries,
                    semantic_retries=max(0, semantic_retries - 1),
                    correction=compact_error(exc),
                )
        elif semantic_retries > 0:
            process_stage_batch(
                conn,
                client,
                candidates,
                mode=mode,
                stage_schema_validator=stage_schema_validator,
                stage_schema_sha256=stage_schema_sha256,
                retries=retries,
                semantic_retries=semantic_retries - 1,
                correction=compact_error(exc),
            )
        else:
            mark_stage_failed(
                conn,
                candidates,
                exc,
                mode=mode,
                retryable=False,
                raw_response=response,
                model=client.model,
                thinking_mode=client.thinking_mode,
                reasoning_effort=client.reasoning_effort,
            )


def _merged_provider_artifacts(
    conn: sqlite3.Connection,
    candidates: Sequence[ReviewCandidate],
    *,
    model: str,
    thinking_mode: str = THINKING_MODE,
    reasoning_effort: str | None = REASONING_EFFORT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    placeholders = ",".join("?" for _ in candidates)
    rows = conn.execute(
        f"""
        SELECT entry_id,mode,stage_review_json,raw_response_json,provider_response_id,
               prompt_tokens,completion_tokens,total_tokens,
               provider_reported_model,thinking_mode,reasoning_effort
        FROM semantic_review_stages
        WHERE status='valid' AND entry_id IN ({placeholders})
        ORDER BY entry_id,mode
        """,
        [candidate.entry_id for candidate in candidates],
    ).fetchall()
    stages: dict[str, dict[str, Any]] = {}
    if len(rows) != len(candidates) * len(REVIEW_MODES):
        raise RuntimeError("merged review does not have exactly two valid stage rows")
    unique_usage: dict[tuple[str, str], tuple[int, int, int]] = {}
    reported_models: set[str] = set()
    thinking_modes: set[str] = set()
    reasoning_efforts: set[str | None] = set()
    for row in rows:
        entry_id = str(row["entry_id"])
        mode = str(row["mode"])
        if mode in stages.setdefault(entry_id, {}):
            raise RuntimeError(f"duplicate valid stage row for {entry_id}/{mode}")
        try:
            stage_review = json.loads(row["stage_review_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"malformed valid stage row for {entry_id}/{mode}") from exc
        stages[entry_id][mode] = stage_review
        raw_key = sha256_text(str(row["raw_response_json"] or ""))
        response_key = (
            mode,
            str(row["provider_response_id"] or f"raw:{raw_key}"),
        )
        unique_usage.setdefault(
            response_key,
            (
                int(row["prompt_tokens"] or 0),
                int(row["completion_tokens"] or 0),
                int(row["total_tokens"] or 0),
            ),
        )
        if row["provider_reported_model"]:
            reported_models.add(str(row["provider_reported_model"]))
        thinking_modes.add(str(row["thinking_mode"]))
        reasoning_efforts.add(row["reasoning_effort"])
    prompt_tokens = sum(value[0] for value in unique_usage.values())
    completion_tokens = sum(value[1] for value in unique_usage.values())
    total_tokens = sum(value[2] for value in unique_usage.values())
    if reported_models != {model}:
        raise RuntimeError("valid stage rows do not close to one requested provider model")
    if thinking_modes != {thinking_mode}:
        raise RuntimeError("valid stage rows do not close to one thinking mode")
    if reasoning_efforts != {reasoning_effort}:
        raise RuntimeError("valid stage rows do not close to one reasoning effort")
    if any(set(value) != set(REVIEW_MODES) for value in stages.values()):
        raise RuntimeError("merged review is missing a directional stage")
    raw_artifact = {
        "mode": "merged",
        "reviewVersion": REVIEW_VERSION,
        "stagePromptVersions": dict(STAGE_PROMPT_VERSIONS),
        "stagePromptSha256": {
            mode: sha256_text(SYSTEM_PROMPTS[mode]) for mode in REVIEW_MODES
        },
        "thinkingMode": thinking_mode,
        "reasoningEffort": reasoning_effort,
        "stageReviews": stages,
    }
    return (
        raw_artifact,
        {
            "response_id": "merged:" + sha256_text(canonical_json(raw_artifact))[:24],
            "usage": {
                "prompt_tokens": prompt_tokens or None,
                "completion_tokens": completion_tokens or None,
                "total_tokens": total_tokens or None,
            },
            "model": model,
            "requested_model": model,
            "thinking_mode": thinking_mode,
            "reasoning_effort": reasoning_effort,
        },
    )


def process_batch(
    conn: sqlite3.Connection,
    client: DeepSeekReviewClient,
    candidates: Sequence[ReviewCandidate],
    *,
    schema_validator: Draft202012Validator,
    stage_schema_validator: Draft202012Validator,
    stage_schema_sha256: str,
    retries: int,
    semantic_retries: int = 1,
    correction: str | None = None,
) -> None:
    """Checkpoint two independent directional passes, then merge locally."""
    if not candidates:
        return
    mark_leased(conn, candidates)
    for mode in REVIEW_MODES:
        stored = load_valid_stage_reviews(
            conn,
            candidates,
            mode=mode,
            expected_model=client.model,
            expected_stage_schema_sha256=stage_schema_sha256,
            stage_schema_validator=stage_schema_validator,
            expected_thinking_mode=client.thinking_mode,
            expected_reasoning_effort=client.reasoning_effort,
        )
        missing = [
            candidate for candidate in candidates if candidate.entry_id not in stored
        ]
        process_stage_batch(
            conn,
            client,
            missing,
            mode=mode,
            stage_schema_validator=stage_schema_validator,
            stage_schema_sha256=stage_schema_sha256,
            retries=retries,
            semantic_retries=semantic_retries,
            correction=correction,
        )
        # A failed A pass must not be hidden by attempting B as if A succeeded.
        after = load_valid_stage_reviews(
            conn,
            candidates,
            mode=mode,
            expected_model=client.model,
            expected_stage_schema_sha256=stage_schema_sha256,
            stage_schema_validator=stage_schema_validator,
            expected_thinking_mode=client.thinking_mode,
            expected_reasoning_effort=client.reasoning_effort,
        )
        if len(after) != len(candidates):
            break

    omission = load_valid_stage_reviews(
        conn,
        candidates,
        mode="omission",
        expected_model=client.model,
        expected_stage_schema_sha256=stage_schema_sha256,
        stage_schema_validator=stage_schema_validator,
        expected_thinking_mode=client.thinking_mode,
        expected_reasoning_effort=client.reasoning_effort,
    )
    contradiction = load_valid_stage_reviews(
        conn,
        candidates,
        mode="contradiction",
        expected_model=client.model,
        expected_stage_schema_sha256=stage_schema_sha256,
        stage_schema_validator=stage_schema_validator,
        expected_thinking_mode=client.thinking_mode,
        expected_reasoning_effort=client.reasoning_effort,
    )
    complete = [
        candidate
        for candidate in candidates
        if candidate.entry_id in omission and candidate.entry_id in contradiction
    ]
    if complete:
        complete_ids = {candidate.entry_id for candidate in complete}
        reviews = merge_stage_reviews(
            {entry_id: review for entry_id, review in omission.items() if entry_id in complete_ids},
            {
                entry_id: review
                for entry_id, review in contradiction.items()
                if entry_id in complete_ids
            },
            complete,
            schema_validator=schema_validator,
        )
        raw_response, provider_meta = _merged_provider_artifacts(
            conn,
            complete,
            model=client.model,
            thinking_mode=client.thinking_mode,
            reasoning_effort=client.reasoning_effort,
        )
        mark_reviews(conn, reviews, complete, raw_response, provider_meta)

    incomplete = [candidate for candidate in candidates if candidate not in complete]
    if incomplete:
        placeholders = ",".join("?" for _ in incomplete)
        stage_states = {
            str(row["entry_id"]): str(row["status"])
            for row in conn.execute(
                f"""
                SELECT entry_id,
                       CASE WHEN SUM(status='quarantined')>0 THEN 'quarantined'
                            ELSE 'retryable_failed' END status
                FROM semantic_review_stages
                WHERE entry_id IN ({placeholders}) GROUP BY entry_id
                """,
                [candidate.entry_id for candidate in incomplete],
            )
        }
        quarantined = [
            candidate
            for candidate in incomplete
            if stage_states.get(candidate.entry_id) == "quarantined"
        ]
        retryable = [candidate for candidate in incomplete if candidate not in quarantined]
        if quarantined:
            mark_failed(
                conn,
                quarantined,
                ReviewValidationError("one directional stage is quarantined"),
                retryable=False,
                raw_response=None,
                model=client.model,
                thinking_mode=client.thinking_mode,
                reasoning_effort=client.reasoning_effort,
            )
        if retryable:
            mark_failed(
                conn,
                retryable,
                RetryableProviderError("one directional stage is incomplete"),
                retryable=True,
                raw_response=None,
                model=client.model,
                thinking_mode=client.thinking_mode,
                reasoning_effort=client.reasoning_effort,
            )


def review_worker(
    worker_id: int,
    work_queue: Queue[tuple[int, list[ReviewCandidate]]],
    stop_event: Event,
    progress: ProgressTracker,
    *,
    sidecar_db: Path,
    schema: dict[str, Any],
    stage_schema: dict[str, Any],
    stage_schema_sha256: str,
    expected_model: str,
    expected_thinking_mode: str,
    expected_reasoning_effort: str | None,
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
            thinking_mode=expected_thinking_mode,
            reasoning_effort=expected_reasoning_effort,
        )
        if not client.available:
            raise FatalProviderError(
                "live DeepSeek semantic review is not enabled/configured"
            )
        if client.model != expected_model:
            raise FatalProviderError("provider model changed while workers were starting")
        if (
            client.thinking_mode != expected_thinking_mode
            or client.reasoning_effort != expected_reasoning_effort
        ):
            raise FatalProviderError(
                "thinking contract changed while workers were starting"
            )
        conn = sidecar_connection(sidecar_db)
        schema_validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        stage_schema_validator = Draft202012Validator(
            stage_schema, format_checker=FormatChecker()
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
                    stage_schema_validator=stage_schema_validator,
                    stage_schema_sha256=stage_schema_sha256,
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
                    thinking_mode=client.thinking_mode,
                    reasoning_effort=client.reasoning_effort,
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
                        thinking_mode=client.thinking_mode,
                        reasoning_effort=client.reasoning_effort,
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
    stage_schema_path: Path,
    effective_db: Path | None,
    selected: Sequence[ReviewCandidate],
    batch_size: int,
    char_limit: int,
    model: str,
    provider_calls: int,
    workers: int,
    max_provider_calls: int,
    thinking_mode: str = THINKING_MODE,
    reasoning_effort: str | None = REASONING_EFFORT,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    counts = status_counts(conn)
    selected_counts = selected_status_counts(conn, selected)
    selected_ids = {candidate.entry_id for candidate in selected}
    full_input_saved = 0
    for row in conn.execute("SELECT entry_id,input_json FROM semantic_review_jobs"):
        if row["entry_id"] in selected_ids and row["input_json"]:
            full_input_saved += 1
    stage_input_saved = 0
    stage_raw_response_saved = 0
    for row in conn.execute(
        "SELECT entry_id,input_json,raw_response_json FROM semantic_review_stages"
    ):
        if row["entry_id"] not in selected_ids:
            continue
        if row["input_json"]:
            stage_input_saved += 1
        if row["raw_response_json"]:
            stage_raw_response_saved += 1
    selected_stage_counts = selected_stage_status_counts(conn, selected)
    candidate_set_sha256 = sha256_text(
        canonical_json(
            [
                {
                    "entry_id": candidate.entry_id,
                    "source_text_sha256": candidate.source_text_sha256,
                    "candidate_annotation_sha256": candidate.candidate_annotation_sha256,
                    "candidate_origin": candidate.candidate_origin,
                    "repair_iteration": candidate.repair_iteration,
                }
                for candidate in selected
            ]
        )
    )
    manifest = {
        "review_version": REVIEW_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "review_schema": display_path(schema_path),
        "review_schema_sha256": sha256_file(schema_path),
        "stage_schema": display_path(stage_schema_path),
        "stage_schema_sha256": sha256_file(stage_schema_path),
        "stage_prompt_versions": dict(STAGE_PROMPT_VERSIONS),
        "stage_prompt_sha256": {
            mode: sha256_text(SYSTEM_PROMPTS[mode]) for mode in REVIEW_MODES
        },
        "merge_policy": "deterministic_local_directional_union_v1",
        "source_db": display_path(source_db),
        "source_db_sha256": sha256_file(source_db) if source_db.is_file() else None,
        "annotation_db_read_only": display_path(annotation_db),
        "annotation_db_sha256": (
            sha256_file(annotation_db) if annotation_db.is_file() else None
        ),
        "effective_db_read_only": display_path(effective_db) if effective_db else None,
        "effective_db_sha256": (
            sha256_file(effective_db) if effective_db and effective_db.is_file() else None
        ),
        "sidecar_db": display_path(sidecar_db),
        "canonical_overwrite_count": 0,
        "model": model,
        "thinking_mode": thinking_mode,
        "reasoning_effort": reasoning_effort,
        "temperature": 0,
        "selected_records": len(selected),
        "ledger_records": sum(counts.values()),
        "status_counts": counts,
        "selected_status_counts": selected_counts,
        "stage_status_counts": stage_status_counts(conn),
        "selected_stage_status_counts": selected_stage_counts,
        "selected_candidate_set_sha256": candidate_set_sha256,
        "selected_candidate_origin_counts": {
            origin: sum(candidate.candidate_origin == origin for candidate in selected)
            for origin in ("canonical_a1", "effective_repair")
        },
        "selected_repair_iteration_counts": {
            str(iteration): sum(
                candidate.repair_iteration == iteration for candidate in selected
            )
            for iteration in sorted({candidate.repair_iteration for candidate in selected})
        },
        "current_run_provider_calls": provider_calls,
        "current_run_provider_call_limit": max_provider_calls,
        "current_run_workers": workers,
        "batch_size_max": batch_size,
        "source_character_limit_for_multi_record_batches": char_limit,
        "source_input_policy": "complete_never_truncated",
        "full_input_storage": "semantic_review_jobs.input_json",
        "full_input_saved_records": full_input_saved,
        "directional_input_storage": "semantic_review_stages.input_json",
        "directional_input_saved_rows": stage_input_saved,
        "directional_raw_response_saved_rows": stage_raw_response_saved,
        "directional_stage_rows_expected": len(selected) * len(REVIEW_MODES),
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
    parser.add_argument(
        "--effective-db",
        type=Path,
        help="Optional valid effective-repair ledger overlaid on canonical A1.",
    )
    parser.add_argument("--sidecar-db", type=Path, default=DEFAULT_SIDECAR_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--stage-schema", type=Path, default=DEFAULT_STAGE_SCHEMA)
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
        "--thinking-mode",
        choices=[THINKING_MODE],
        default=THINKING_MODE,
        help="Fixed v4.3 nonthinking contract; recorded in every stage and merged row.",
    )
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=0,
        help=(
            "Hard process-wide call cap; 0 derives four times both planned "
            "directional stage batches."
        ),
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
    effective_db = args.effective_db.resolve() if args.effective_db is not None else None
    sidecar_db = args.sidecar_db.resolve()
    schema_path = args.schema.resolve()
    stage_schema_path = args.stage_schema.resolve()
    manifest_path = args.manifest.resolve()
    enforce_disjoint_paths(
        source_db=source_db,
        annotation_db=annotation_db,
        sidecar_db=sidecar_db,
        schema=schema_path,
        manifest=manifest_path,
        stage_schema=stage_schema_path,
        effective_db=effective_db,
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        stage_schema = json.loads(stage_schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(stage_schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise RuntimeError(f"semantic-review schema is invalid: {exc}") from exc

    all_candidates, source_count = load_review_candidates(
        source_db, annotation_db, effective_db
    )
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
    max_provider_calls = args.max_provider_calls or max(
        1, len(batches) * len(REVIEW_MODES) * 4
    )
    plan = {
        "sourceCanonicalRuntimeRecords": source_count,
        "annotationValidRecords": len(all_candidates),
        "canonicalA1Candidates": sum(
            candidate.candidate_origin == "canonical_a1" for candidate in all_candidates
        ),
        "effectiveRepairCandidates": sum(
            candidate.candidate_origin == "effective_repair" for candidate in all_candidates
        ),
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
        "independentReviewModes": list(REVIEW_MODES),
        "thinkingMode": args.thinking_mode,
        "reasoningEffort": REASONING_EFFORT,
        "live": bool(args.live),
    }
    print(json.dumps({"event": "plan", **plan}, ensure_ascii=False), flush=True)
    if not args.live:
        return 0

    probe = DeepSeekReviewClient(
        rpm=float(args.rpm),
        timeout=args.timeout,
        max_tokens=args.max_tokens,
        thinking_mode=args.thinking_mode,
        reasoning_effort=REASONING_EFFORT,
    )
    requested_model = probe.model
    requested_thinking_mode = probe.thinking_mode
    requested_reasoning_effort = probe.reasoning_effort
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
    stage_schema_sha256 = sha256_file(stage_schema_path)
    run_lock = acquire_process_lock(sidecar_db.with_suffix(".reviewer.lock"))
    conn: sqlite3.Connection | None = None
    try:
        conn = init_sidecar_db(sidecar_db)
        invalidated = enqueue_candidates(
            conn,
            selected,
            model=requested_model,
            schema_sha256=schema_sha256,
            stage_schema_sha256=stage_schema_sha256,
            thinking_mode=requested_thinking_mode,
            reasoning_effort=requested_reasoning_effort,
        )
        schema_validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        stage_schema_validator = Draft202012Validator(
            stage_schema, format_checker=FormatChecker()
        )
        locally_recovered = revalidate_stored_quarantines(
            conn,
            selected,
            schema_validator=schema_validator,
            expected_model=requested_model,
            expected_schema_sha256=schema_sha256,
            expected_thinking_mode=requested_thinking_mode,
            expected_reasoning_effort=requested_reasoning_effort,
        )
        locally_recovered += revalidate_stored_stage_quarantines(
            conn,
            selected,
            stage_schema_validator=stage_schema_validator,
            expected_model=requested_model,
            expected_stage_schema_sha256=stage_schema_sha256,
            expected_thinking_mode=requested_thinking_mode,
            expected_reasoning_effort=requested_reasoning_effort,
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
                        stage_schema=stage_schema,
                        stage_schema_sha256=stage_schema_sha256,
                        expected_model=requested_model,
                        expected_thinking_mode=requested_thinking_mode,
                        expected_reasoning_effort=requested_reasoning_effort,
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
            stage_schema_path=stage_schema_path,
            effective_db=effective_db,
            selected=selected,
            batch_size=args.batch_size,
            char_limit=args.batch_char_limit,
            model=requested_model,
            provider_calls=provider_calls,
            workers=active_workers,
            max_provider_calls=max_provider_calls,
            thinking_mode=requested_thinking_mode,
            reasoning_effort=requested_reasoning_effort,
        )
        final_counts = status_counts(conn)
        selected_counts = selected_status_counts(conn, selected)
        final_stage_counts = selected_stage_status_counts(conn, selected)
        terminal_detail = {
            "statusCounts": final_counts,
            "selectedStatusCounts": selected_counts,
            "selectedStageStatusCounts": final_stage_counts,
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
        incomplete += sum(
            len(selected) - final_stage_counts.get(mode, {}).get("valid", 0)
            for mode in REVIEW_MODES
        )
        event = "run_completed" if incomplete == 0 else "run_incomplete"
        log_event(conn, event, terminal_detail)
        print(
            json.dumps({"event": event, **terminal_detail}, ensure_ascii=False),
            flush=True,
        )
        return 0 if incomplete == 0 else 2
    finally:
        if conn is not None:
            conn.close()
        release_process_lock(run_lock)


if __name__ == "__main__":
    raise SystemExit(main())
