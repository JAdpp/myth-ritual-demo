"""Merge audited batches, then materialize one product story per family.

The 60 fixed source witnesses remain auditable in C0. Exactly 30 records named
by the C2 selection ledger are emitted to C3 as product stories; the other 30
are emitted to the internal C1 secondary-witness layer. Re-running this script
must never recreate a two-record-per-family C3 runtime corpus.
"""

from __future__ import annotations

from copy import deepcopy
import json
from collections import Counter
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent
C0_PATH = ROOT / "corpus" / "c0_source_manifest.json"
C1_SECONDARY_PATH = ROOT / "corpus" / "c1_secondary_source_witnesses.json"
C2_SELECTION_PATH = ROOT / "corpus" / "c2_primary_story_selection.json"
C3_PATH = ROOT / "corpus" / "c3_story_versions.json"
BATCH_PATHS = [
    ROOT / "expansion_batches" / "myth_batch.json",
    ROOT / "expansion_batches" / "legend_batch.json",
    ROOT / "expansion_batches" / "fable_batch.json",
]
TARGET_FAMILIES = 30
TARGET_SOURCE_WITNESSES = 60
TARGET_PRODUCT_STORIES = 30
TARGET_SECONDARY_WITNESSES = 30
PRIMARY_SELECTION_STATUS = "demo_editorial_selection_pending_expert_review"
CORPUS_VERSION = "c3-dev-primary-2026-08-09-30f"
SNAPSHOT_DATE = "2026-08-09"


PRODUCT_CARD_OVERRIDES: dict[str, dict[str, object]] = {
    "change_huainanzi_lanming": {
        "summary": "羿向西王母求得不死药，姮娥取药奔月。羿失去药、无法再续，只能在离开与失落中怅然；姮娥的动机保持留白。",
    },
    "cowherd_jingchu_suishiji_qixi": {
        "summary": "七月七日是牵牛与织女相会之夜。夜空星辰、庭中乞巧与短暂会合，共同形成关于距离、等待和重逢的故事。",
        "conflict": "相隔的牵牛与织女只有在特定时间相会，短暂重逢之后仍要面对等待。",
    },
    "mengjiangnu_lienvzhuan_04": {
        "summary": "杞梁战死后，妻子失去亲人，也失去可依附的生活位置。她在城下哀哭十日，城墙随之崩塌，最终投淄水而死。",
    },
    "butterfly_qingshi_leilue_10": {
        "summary": "梁、祝同学，梁后来才知道祝是女子，却发现她已许配马氏。梁病死后，祝出嫁途经其墓，哀恸之下投进裂开的墓地；故事末尾还流传着二人化蝶的说法。",
    },
    "peach_blossom_taohuayuanji": {
        "summary": "武陵渔人偶然进入避乱者生活的桃花源。离开时他沿途做下标记并报告太守，却再也找不到入口。",
    },
    "nanke_taishouzhuan": {
        "summary": "淳于棼醉卧古槐下，被紫衣使者迎入槐安国，经历婚姻、任官、战争与荣辱；醒来后，他在槐穴中辨认出梦境世界与现实空间的尺度对应。",
    },
}


PRODUCT_ADAPTATION_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "pangu_yiwenleiju_sanwuliji_quote": {
        "must_preserve": [
            "混沌如鸡子",
            "阳清为天、阴浊为地",
            "盘古立于天地之间并随之增长",
        ],
        "may_transform": [
            "把天地分判转译为现代生活中的边界建立",
            "改变时代、职业与空间",
            "由用户决定是否采用宏大尺度",
        ],
        "prohibited": [
            "擅自补写盘古身体化生万物并说成当前故事事实",
            "把短篇来源扩成完整定本而不标明再创作",
            "宣称故事具有治疗或心理改善效果",
        ],
    },
    "kuafu_shanhaijing_haiwaibei": {
        "must_preserve": [
            "夸父逐日、饮河渭、未至大泽而死",
            "手杖化为邓林",
            "故事在未竟追逐后留下新的生长",
        ],
        "may_transform": [
            "把太阳转译为难以接近的目标",
            "把邓林转译为可留给他人的资源",
            "允许角色主动停下而非复制原结局",
        ],
        "prohibited": [
            "把死亡美化为坚持到底的必要代价",
            "擅自增加应龙杀夸父的情节",
            "宣称坚持即可获得治疗效果",
        ],
    },
    "change_huainanzi_lanming": {
        "must_preserve": [
            "羿向西王母请得不死药",
            "姮娥取药奔月",
            "故事以羿怅然且无以续之收束",
            "姮娥的动机保持留白",
        ],
        "may_transform": [
            "让现代支线探索多种可能动机并明确标为再创作",
            "转译为离开、资源与不确定性",
            "允许用户从任一角色视角书写",
        ],
        "prohibited": [
            "擅自补入团圆、玉兔或完整月宫生活",
            "把姮娥定性为天生贪婪",
            "以关系分离作治疗性类比",
        ],
    },
    "mulan_ballad": {
        "must_preserve": [
            "军帖有父名且家中无长兄",
            "木兰主动提出代父从军",
            "故事结尾包含辞官、返乡与身份被同行者发现",
        ],
        "may_transform": [
            "把征役转译为现代角色冲突",
            "让用户选择是否公开身份",
            "突出辞官归家的自主决定",
        ],
        "prohibited": [
            "擅自增加法术、固定年龄或其他未采用情节",
            "把孝道写成无条件自我牺牲命令",
            "把性别揭示做成羞辱或笑料",
        ],
    },
    "yellow_millet_zhenzhongji": {
        "must_preserve": [
            "邯郸旅舍、吕翁瓷枕和主人蒸黄粱",
            "梦中经历完整功名人生",
            "醒来时黄粱未熟",
            "结尾由卢生反观欲望而非外界替他宣布单一答案",
        ],
        "may_transform": [
            "把功名转译为现代目标",
            "缩放梦中时间",
            "让用户保留而非否定某些愿望",
        ],
        "prohibited": [
            "把所有抱负病理化",
            "把梦醒写成临床疗愈",
            "复演或鼓励梦中自伤",
            "把再创作情节说成古籍事实",
        ],
    },
    "white_snake_jingshitongyan_28": {
        "must_preserve": [
            "人物名为许宣",
            "白娘子自述不曾杀生害命，但仍被置于妖怪框架",
            "青青在故事中被写为青鱼",
            "结尾是镇塔与许宣出家",
        ],
        "may_transform": [
            "让现代支线质疑谁有权定义异类",
            "以非贬损方式改写沟通与边界",
            "保留历史宗教框架同时允许多声部讨论",
        ],
        "prohibited": [
            "擅自改成一家团圆并声称来源如此",
            "把宗教传统整体 caricature 化或污名化",
            "复演威胁、自杀或强制控制作为用户任务",
            "把白娘子单一定性为危险女性",
        ],
    },
    "ganjiang_moye_wuyuechunqiu": {
        "must_preserve": [
            "王命铸造雌雄双剑",
            "金铁最初不融",
            "故事讨论夫妻入炉的身体献祭旧说",
            "实际投入炉中的是断发剪爪而不是莫耶整个人",
        ],
        "may_transform": [
            "只用不伤身的象征物表现成本",
            "把王命转译为不合理期限",
            "让现代角色拒绝献祭逻辑并寻求协作",
        ],
        "prohibited": [
            "要求用户提交身体、疼痛或自伤行为",
            "写实表现献祭或伤害",
            "把自我伤害美化为工匠精神",
            "擅自增加斩首复仇情节",
        ],
    },
    "cowherd_jingchu_suishiji_qixi": {
        "must_preserve": [
            "故事围绕七月七日相会",
            "牵牛与织女以星辰身份出现",
            "相会与乞巧共同构成七夕夜的仪式场景",
        ],
        "may_transform": [
            "将星河距离映射为可编辑时间线",
            "讨论等待是否值得与如何维持连接",
            "用双星和织线作非写实视觉",
        ],
        "prohibited": [
            "擅自补写凡人家庭身世并说成当前故事事实",
            "把被迫分离包装成爱情证明",
            "把乞巧简化成女性天职",
        ],
    },
    "mengjiangnu_lienvzhuan_04": {
        "must_preserve": [
            "故事主角为杞梁妻",
            "她在城下哀哭十日后城墙崩塌",
            "故事以赴淄水收束并保持成人内容门控",
        ],
        "may_transform": [
            "让用户拒绝无所归的单一路径",
            "加入现实支持节点并标明当代改写",
            "把城墙映射为制度压力而非个人意志万能",
        ],
        "prohibited": [
            "把自尽美化为爱情完成",
            "擅自增加秦长城或千里寻夫情节并说成当前故事事实",
            "要求用户模拟自伤或尸体场面",
        ],
    },
    "butterfly_qingshi_leilue_10": {
        "must_preserve": [
            "梁后来才知道祝是女子",
            "祝已许马氏形成婚约阻隔",
            "化蝶作为故事末尾流传的说法呈现",
        ],
        "may_transform": [
            "增加拒绝强制婚约的安全支线并标明当代改写",
            "把身份表达设为用户掌控节点",
            "以不伤害的离别替代投墓",
        ],
        "prohibited": [
            "把投死美化为真爱证明",
            "把化蝶说成唯一结局",
            "让用户模拟自杀或被迫婚姻",
        ],
    },
    "peach_blossom_taohuayuanji": {
        "must_preserve": [
            "桃源居民先世为避秦乱而来",
            "渔人离开后做标记并报告太守",
            "重寻失败而非永久占有桃源",
        ],
        "may_transform": [
            "把是否公开入口变成伦理选择",
            "让居民参与决定边界",
            "用可撤回地图表现隐私",
        ],
        "prohibited": [
            "把桃源说成可供征服开发的空地",
            "抹掉避乱背景",
            "把桃源解释为唯一正确的人生归宿",
        ],
    },
}


def load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object envelope: {path}")
    return payload


def merge_unique(
    destination: list[dict[str, object]],
    additions: list[dict[str, object]],
    key: str,
    signature: Callable[[dict[str, object]], object],
) -> None:
    """Append missing records and reject evidence conflicts on existing IDs."""

    existing = {str(item[key]): item for item in destination}
    for item in additions:
        identity = str(item[key])
        if identity in existing:
            if signature(existing[identity]) != signature(item):
                raise ValueError(f"conflicting {key}: {identity}")
            continue
        destination.append(deepcopy(item))
        existing[identity] = destination[-1]


def family_signature(item: dict[str, object]) -> tuple[object, frozenset[str]]:
    return item.get("title"), frozenset(str(value) for value in item.get("version_ids", []))


def source_signature(item: dict[str, object]) -> tuple[object, ...]:
    return (
        item.get("story_family_id"),
        item.get("source_permalink"),
        item.get("page_revision_id"),
        item.get("page_revision_timestamp"),
        item.get("excerpt_sha256"),
    )


def version_signature(item: dict[str, object]) -> tuple[object, ...]:
    source = item.get("source_canon")
    if not isinstance(source, dict):
        raise ValueError(f"version lacks source_canon: {item.get('story_version_id')}")
    return (
        item.get("story_family_id"),
        source.get("source_permalink"),
        source.get("page_revision_id"),
        source.get("page_revision_timestamp"),
        source.get("excerpt_sha256"),
    )


def normalize_primary(record: dict[str, object], canonical_title: str) -> dict[str, object]:
    normalized = deepcopy(record)
    adult_only = normalized.get("adult_only") is True
    normalized.update(
        {
            "corpus_tier": "c3-dev",
            "product_primary": True,
            "record_role": "product_primary_story",
            "primary_selection_status": PRIMARY_SELECTION_STATUS,
            "eligible_for_demo": True,
            "development_runtime_eligible": True,
            "default_offer_eligible": not adult_only,
            "requires_explicit_adult_opt_in": adult_only,
            "title": canonical_title,
        }
    )
    version_id = str(normalized.get("story_version_id"))
    story_card = normalized.get("story_card")
    source_canon = normalized.get("source_canon")
    if not isinstance(story_card, dict) or not isinstance(source_canon, dict):
        raise ValueError(f"primary lacks story_card/source_canon: {version_id}")
    story_card["title"] = canonical_title
    story_card["subtitle"] = str(source_canon.get("work", "固定来源主文本"))
    story_card.update(PRODUCT_CARD_OVERRIDES.get(version_id, {}))
    if version_id == "ganjiang_moye_wuyuechunqiu":
        warnings = story_card.get("content_warnings")
        if isinstance(warnings, list):
            story_card["content_warnings"] = [
                warning for warning in warnings if warning != "同族另一版本含斩首与复仇"
            ]
    if version_id in PRODUCT_ADAPTATION_OVERRIDES:
        normalized["adaptation_boundary"] = deepcopy(
            PRODUCT_ADAPTATION_OVERRIDES[version_id]
        )
    rights = normalized.get("rights_and_access")
    if not isinstance(rights, dict):
        raise ValueError(f"primary lacks rights_and_access: {normalized.get('story_version_id')}")
    uses = [
        str(use)
        for use in rights.get("allowed_uses", [])
        if use not in {"local_development_runtime", "local_development_runtime_with_adult_opt_in", "internal_source_audit"}
    ]
    runtime_use = (
        "local_development_runtime_with_adult_opt_in"
        if adult_only
        else "local_development_runtime"
    )
    rights["allowed_uses"] = [runtime_use, *uses]
    review = normalized.get("review_record")
    if isinstance(review, dict):
        review["status"] = "c3_dev_pending_dual_review"
    return normalized


def normalize_secondary(record: dict[str, object]) -> dict[str, object]:
    normalized = deepcopy(record)
    adult_only = normalized.get("adult_only") is True
    normalized.update(
        {
            "corpus_tier": "c1-secondary-witness",
            "product_primary": False,
            "record_role": "secondary_source_witness",
            "primary_selection_status": "not_selected_secondary_source_witness",
            "eligible_for_demo": False,
            "development_runtime_eligible": False,
            "default_offer_eligible": False,
            "requires_explicit_adult_opt_in": adult_only,
        }
    )
    rights = normalized.get("rights_and_access")
    if not isinstance(rights, dict):
        raise ValueError(f"secondary lacks rights_and_access: {normalized.get('story_version_id')}")
    uses = [
        str(use)
        for use in rights.get("allowed_uses", [])
        if use not in {"local_development_runtime", "local_development_runtime_with_adult_opt_in"}
    ]
    if "internal_source_audit" not in uses:
        uses.insert(0, "internal_source_audit")
    rights["allowed_uses"] = uses
    review = normalized.get("review_record")
    if isinstance(review, dict):
        review["status"] = "c1_secondary_pending_dual_review"
    return normalized


def main() -> None:
    required = [C0_PATH, C2_SELECTION_PATH, C3_PATH, *BATCH_PATHS]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"corpus inputs are incomplete: {missing}")

    c0 = load(C0_PATH)
    c2 = load(C2_SELECTION_PATH)
    c3 = load(C3_PATH)
    secondary_envelope = load(C1_SECONDARY_PATH) if C1_SECONDARY_PATH.exists() else None

    c0_families = c0.get("story_families")
    c0_sources = c0.get("source_items")
    c3_versions = c3.get("story_versions")
    prior_secondary = (
        secondary_envelope.get("source_witnesses", [])
        if isinstance(secondary_envelope, dict)
        else []
    )
    selections = c2.get("story_selections")
    if not all(isinstance(items, list) for items in (c0_families, c0_sources, c3_versions, prior_secondary, selections)):
        raise ValueError("invalid central corpus arrays")

    all_versions = [deepcopy(item) for item in c3_versions]
    merge_unique(all_versions, prior_secondary, "story_version_id", version_signature)

    for path in BATCH_PATHS:
        batch = load(path)
        families = batch.get("c0_story_families")
        sources = batch.get("c0_source_items")
        versions = batch.get("c3_story_versions")
        if not all(isinstance(items, list) for items in (families, sources, versions)):
            raise ValueError(f"invalid batch arrays: {path}")
        if len(families) != 6 or len(sources) != 12 or len(versions) != 12:
            raise ValueError(f"batch must be 6 families / 12 witnesses: {path}")
        merge_unique(c0_families, families, "story_family_id", family_signature)
        merge_unique(c0_sources, sources, "story_version_id", source_signature)
        merge_unique(all_versions, versions, "story_version_id", version_signature)

    if len(c0_families) != TARGET_FAMILIES or len(c0_sources) != TARGET_SOURCE_WITNESSES:
        raise ValueError(
            f"C0 target mismatch: families={len(c0_families)}, sources={len(c0_sources)}"
        )
    if len(all_versions) != TARGET_SOURCE_WITNESSES:
        raise ValueError(f"expected {TARGET_SOURCE_WITNESSES} fully annotated witnesses")

    all_by_id = {str(item["story_version_id"]): item for item in all_versions}
    if len(all_by_id) != TARGET_SOURCE_WITNESSES:
        raise ValueError("duplicate annotated witness ID")
    c0_source_ids = {str(item["story_version_id"]) for item in c0_sources}
    if set(all_by_id) != c0_source_ids:
        raise ValueError("annotated witness union does not match C0 source IDs")

    if len(selections) != TARGET_FAMILIES:
        raise ValueError(f"expected {TARGET_FAMILIES} C2 selections")
    if c2.get("source_corpus_version") != CORPUS_VERSION:
        raise ValueError("C2 selection ledger targets a different source corpus version")
    selection_by_family = {str(item["story_family_id"]): item for item in selections}
    if len(selection_by_family) != TARGET_FAMILIES:
        raise ValueError("duplicate C2 family selection")

    primary_records: list[dict[str, object]] = []
    secondary_records: list[dict[str, object]] = []
    primary_ids: set[str] = set()
    secondary_ids: set[str] = set()
    for selection in selections:
        family_id = str(selection["story_family_id"])
        primary_id = str(selection["primary_version_id"])
        secondary_id = str(selection["secondary_witness_id"])
        if primary_id not in all_by_id or secondary_id not in all_by_id:
            raise ValueError(f"C2 selection references unknown witness: {family_id}")
        if primary_id == secondary_id:
            raise ValueError(f"primary and secondary are identical: {family_id}")
        if all_by_id[primary_id].get("story_family_id") != family_id:
            raise ValueError(f"primary family mismatch: {family_id}")
        if all_by_id[secondary_id].get("story_family_id") != family_id:
            raise ValueError(f"secondary family mismatch: {family_id}")
        primary_records.append(
            normalize_primary(all_by_id[primary_id], str(selection["title"]))
        )
        secondary_records.append(normalize_secondary(all_by_id[secondary_id]))
        primary_ids.add(primary_id)
        secondary_ids.add(secondary_id)

    if len(primary_ids) != TARGET_PRODUCT_STORIES or len(secondary_ids) != TARGET_SECONDARY_WITNESSES:
        raise ValueError("duplicate primary or secondary selection")
    if primary_ids & secondary_ids or primary_ids | secondary_ids != c0_source_ids:
        raise ValueError("C2 primary/secondary partition does not exactly cover C0")
    if set(Counter(str(item["story_family_id"]) for item in primary_records).values()) != {1}:
        raise ValueError("C3 must contain exactly one product story per family")
    if set(Counter(str(item["story_family_id"]) for item in secondary_records).values()) != {1}:
        raise ValueError("C1 secondary layer must contain exactly one witness per family")

    manifest_by_family = {str(item["story_family_id"]): item for item in c0_families}
    if set(manifest_by_family) != set(selection_by_family):
        raise ValueError("C0 family set does not match C2 selections")
    for family_id, selection in selection_by_family.items():
        manifest = manifest_by_family[family_id]
        primary_id = str(selection["primary_version_id"])
        secondary_id = str(selection["secondary_witness_id"])
        if set(str(value) for value in manifest.get("version_ids", [])) != {primary_id, secondary_id}:
            raise ValueError(f"C0 version list does not match C2 selection: {family_id}")
        manifest["product_primary_version_id"] = primary_id
        manifest["secondary_witness_id"] = secondary_id
        manifest["version_policy"] = "每则故事仅有一个产品主文本；第二见证只用于后台来源审计，不向用户提供版本选择。"

    c0["corpus_version"] = CORPUS_VERSION
    c0["snapshot_date"] = SNAPSHOT_DATE
    c0["coverage_claim"] = (
        "可审计产品数据：30 则故事各有 1 个产品主文本，共 30 条 C3 运行记录；"
        "另保留 30 条后台第二来源见证，C0 合计 60 条固定来源。"
        "不声称穷尽中国古典神话、传说、志怪、寓言或故事材料。"
    )
    source_policy = c0.get("source_policy")
    if isinstance(source_policy, dict):
        source_policy["product_runtime_policy"] = "每个故事家族只允许 C2 账本指定的一条产品主文本进入 C3 推荐、讲解、映射和剧场。"
        source_policy["secondary_witness_policy"] = "每族第二见证只用于后台来源核验与防混写，不得进入推荐或让用户选择版本。"
        source_policy["production_gate"] = "主文本完成独立来源核验、权利裁决和双人文化复核前，只能作为 c3-dev/editorial_draft 供开发端点使用；后台第二见证始终不具运行资格。"

    c3.update(
        {
            "corpus_version": CORPUS_VERSION,
            "snapshot_date": SNAPSHOT_DATE,
            "corpus_tier": "c3-dev",
            "editorial_status": "editorial_draft",
            "production_eligible": False,
            "development_runtime_eligible": True,
            "coverage_claim": (
                "产品运行集：30 则故事各保留 1 个产品主文本，共 30 条；"
                "用户不选择异本，后台第二见证另存于 C1-secondary。"
                "所有主文本均为待专家复核的演示编辑选择。"
            ),
            "record_policy": "one_product_primary_story_per_family",
            "primary_selection_status": PRIMARY_SELECTION_STATUS,
            "story_versions": primary_records,
        }
    )

    c1_secondary = {
        "schema_version": c3.get("schema_version", "0.2.0"),
        "corpus_version": CORPUS_VERSION,
        "snapshot_date": SNAPSHOT_DATE,
        "corpus_tier": "c1-secondary-witness",
        "editorial_status": "editorial_draft",
        "production_eligible": False,
        "development_runtime_eligible": False,
        "coverage_claim": (
            "后台来源审计层：30 则故事各保留 1 条第二见证，共 30 条；"
            "不可推荐、不可作为用户版本选项，也不得向产品主文本混入情节。"
        ),
        "record_policy": "internal_source_evidence_only_not_user_selectable",
        "source_witnesses": secondary_records,
    }

    C0_PATH.write_text(json.dumps(c0, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    C1_SECONDARY_PATH.write_text(
        json.dumps(c1_secondary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    C3_PATH.write_text(json.dumps(c3, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "story_families": len(c0_families),
                "product_primary_stories": len(primary_records),
                "secondary_source_witnesses": len(secondary_records),
                "fixed_source_witnesses": len(c0_sources),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
