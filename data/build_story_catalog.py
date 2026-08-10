"""Build the broad C1 discovery catalog from a reviewed seed list.

The catalog is intentionally separate from the C3 runtime corpus. A title in
this file is a discovery lead, not evidence that its source text has already
been fixed, excerpted, rights-reviewed, or made offerable by the demo.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
C0_PATH = ROOT / "corpus" / "c0_source_manifest.json"
C1_SECONDARY_PATH = ROOT / "corpus" / "c1_secondary_source_witnesses.json"
C2_SELECTION_PATH = ROOT / "corpus" / "c2_primary_story_selection.json"
C3_PATH = ROOT / "corpus" / "c3_story_versions.json"
OUTPUT_PATH = ROOT / "corpus" / "c1_story_catalog.json"


GROUPS: list[dict[str, object]] = [
    {
        "tradition_group": "上古创世与神祇",
        "story_type": "myth",
        "source_hints": ["山海经", "淮南子", "庄子", "艺文类聚"],
        "stories": [
            ("pangu_cosmogony", "盘古开天"),
            ("nvwa_mends_sky", "女娲补天"),
            ("nvwa_creates_humans", "女娲造人"),
            ("fuxi_draws_trigrams", "伏羲画卦"),
            ("shennong_tastes_herbs", "神农尝百草"),
            ("suiren_makes_fire", "燧人取火"),
            ("cangjie_creates_writing", "仓颉造字"),
            ("gonggong_hits_buzhou", "共工触不周山"),
            ("zhulong_rules_day_night", "烛龙司昼夜"),
            ("hundun_openings", "混沌开窍"),
            ("xihe_births_suns", "羲和生十日"),
            ("changxi_births_moons", "常羲生十二月"),
            ("nuba_stops_rain", "女魃止雨"),
            ("yinglong_aids_yu", "应龙助禹"),
            ("gun_steals_magic_soil", "鲧窃息壤"),
        ],
    },
    {
        "tradition_group": "日月洪水与文化英雄",
        "story_type": "myth",
        "source_hints": ["山海经", "淮南子", "搜神记", "太平御览"],
        "stories": [
            ("houyi_shoots_suns", "后羿射日"),
            ("change_flight_to_moon", "嫦娥奔月"),
            ("kuafu_sun_chase", "夸父逐日"),
            ("jingwei_fills_sea", "精卫填海"),
            ("xingtian_dances", "刑天舞干戚"),
            ("wu_gang_cuts_osmanthus", "吴刚伐桂"),
            ("gun_yu_flood_control", "大禹治水"),
            ("dayu_slays_xiangliu", "大禹诛相柳"),
            ("dayu_chains_wuzhiqi", "大禹锁无支祁"),
            ("yellow_emperor_fights_chiyou", "黄帝战蚩尤"),
            ("xuannv_teaches_warfare", "九天玄女授兵法"),
            ("leizu_sericulture", "嫘祖养蚕"),
            ("dukang_makes_wine", "杜康造酒"),
            ("houji_teaches_farming", "后稷教稼"),
            ("pengzu_longevity", "彭祖长寿"),
        ],
    },
    {
        "tradition_group": "先秦法家与杂家寓言",
        "story_type": "fable",
        "source_hints": ["韩非子", "吕氏春秋"],
        "stories": [
            ("farmer_waits_for_rabbit", "守株待兔"),
            ("self_contradictory_spear_shield", "自相矛盾"),
            ("zheng_man_buys_shoes", "郑人买履"),
            ("buy_casket_return_pearl", "买椟还珠"),
            ("nanguo_plays_yu", "滥竽充数"),
            ("old_horse_knows_way", "老马识途"),
            ("bianque_warns_ruler", "讳疾忌医"),
            ("zengzi_kills_pig", "曾子杀彘"),
            ("fierce_dog_sours_wine", "狗猛酒酸"),
            ("heshi_offers_jade", "和氏献璧"),
            ("wise_son_suspects_neighbor", "智子疑邻"),
            ("lu_man_moves_to_yue", "鲁人徙越"),
            ("ying_letter_yan_explanation", "郢书燕说"),
            ("wei_man_marries_daughter", "卫人嫁女"),
        ],
    },
    {
        "tradition_group": "战国两汉说辞与历史寓言",
        "story_type": "historical_story",
        "source_hints": ["战国策", "史记", "吕氏春秋", "新序"],
        "stories": [
            ("fox_borrows_tiger_might", "狐假虎威"),
            ("draw_snake_add_feet", "画蛇添足"),
            ("snipe_clam_struggle", "鹬蚌相争"),
            ("southward_carriage_north", "南辕北辙"),
            ("three_men_make_tiger", "三人成虎"),
            ("startled_bird_bow", "惊弓之鸟"),
            ("cunning_hare_three_burrows", "狡兔三窟"),
            ("mao_sui_recommends_himself", "毛遂自荐"),
            ("return_jade_intact", "完璧归赵"),
            ("carry_thorns_apologize", "负荆请罪"),
            ("jingke_assassinates_qin", "荆轲刺秦"),
            ("feng_xuan_guest_mengchang", "冯谖客孟尝君"),
            ("marking_boat_for_sword", "刻舟求剑"),
            ("cover_ears_steal_bell", "掩耳盗铃"),
            ("one_cry_astonishes", "一鸣惊人"),
            ("lord_ye_loves_dragons", "叶公好龙"),
        ],
    },
    {
        "tradition_group": "先秦两汉子书寓言",
        "story_type": "fable",
        "source_hints": ["列子", "庄子", "孟子", "淮南子"],
        "stories": [
            ("yugong_moves_mountains", "愚公移山"),
            ("man_of_qi_fears_sky", "杞人忧天"),
            ("two_children_debate_sun", "两小儿辩日"),
            ("jichang_learns_archery", "纪昌学射"),
            ("xuetan_learns_singing", "薛谭学讴"),
            ("boya_breaks_strings", "伯牙绝弦"),
            ("zhuangzhou_butterfly_dream", "庄周梦蝶"),
            ("butcher_ding_ox", "庖丁解牛"),
            ("dongshi_imitates_frown", "东施效颦"),
            ("handan_learns_walk", "邯郸学步"),
            ("frog_in_well", "井底之蛙"),
            ("three_morning_four_evening", "朝三暮四"),
            ("mantis_blocks_chariot", "螳臂当车"),
            ("pull_seedlings_to_help", "揠苗助长"),
            ("qi_man_wife_concubine", "齐人有一妻一妾"),
            ("old_man_lost_horse", "塞翁失马"),
        ],
    },
    {
        "tradition_group": "汉魏至宋人物传说",
        "story_type": "historical_legend",
        "source_hints": ["史记", "汉书", "后汉书", "晋书", "宋史"],
        "stories": [
            ("su_wu_herds_sheep", "苏武牧羊"),
            ("wang_zhaojun_frontier", "昭君出塞"),
            ("hongmen_banquet", "鸿门宴"),
            ("besieged_songs_of_chu", "四面楚歌"),
            ("sleep_brushwood_taste_gall", "卧薪尝胆"),
            ("guan_bao_friendship", "管鲍之交"),
            ("cheng_men_stands_in_snow", "程门立雪"),
            ("kong_rong_yields_pears", "孔融让梨"),
            ("sima_guang_breaks_vat", "司马光砸缸"),
            ("dance_at_rooster_crow", "闻鸡起舞"),
            ("zu_ti_oar_oath", "祖逖击楫"),
            ("wang_xiang_lies_on_ice", "王祥卧冰"),
            ("meng_zong_weeps_bamboo", "孟宗哭竹"),
            ("huang_xiang_warms_bed", "黄香温席"),
        ],
    },
    {
        "tradition_group": "汉晋六朝志怪与记叙",
        "story_type": "zhiguai",
        "source_hints": ["搜神记", "搜神后记", "幽明录", "陶渊明集"],
        "stories": [
            ("ganjiang_moye", "干将莫邪"),
            ("snail_maiden", "田螺姑娘"),
            ("dong_yong_weaver_maiden", "董永与织女"),
            ("li_ji_slays_serpent", "李寄斩蛇"),
            ("han_ping_couple", "韩凭夫妇"),
            ("song_dingbo_sells_ghost", "宋定伯卖鬼"),
            ("ziyu_han_zhong", "紫玉韩重"),
            ("tan_sheng_ghost_wife", "谈生娶鬼妻"),
            ("ruan_zhan_meets_ghost", "阮瞻遇鬼"),
            ("wang_daoping_pang_e", "王道平与庞阿"),
            ("jiang_ziwen_manifestation", "蒋子文显灵"),
            ("jiaohu_temple_blessing", "焦湖庙祝"),
            ("xianchao_goddess", "弦超遇神女"),
            ("lu_chong_ghost_marriage", "卢充幽婚"),
            ("du_lanxiang_descends", "杜兰香下嫁"),
            ("peach_blossom_spring", "桃花源"),
        ],
    },
    {
        "tradition_group": "魏晋人物与世说逸事",
        "story_type": "anecdote",
        "source_hints": ["世说新语", "三国志", "晋书"],
        "stories": [
            ("wang_rong_identifies_plums", "王戎识李"),
            ("chen_taiqiu_keeps_faith", "陈太丘与友期"),
            ("xie_daoyun_snow_verse", "谢道韫咏雪"),
            ("guan_ning_cuts_mat", "管宁割席"),
            ("wang_lantian_eats_eggs", "王蓝田食鸡子"),
            ("snow_night_visit_dai", "雪夜访戴"),
            ("new_pavilion_tears", "新亭对泣"),
            ("east_bed_son_in_law", "坦腹东床"),
            ("seven_step_poem", "七步成诗"),
            ("ji_kang_guangling_san", "嵇康绝奏广陵散"),
            ("cao_chong_weighs_elephant", "曹冲称象"),
            ("no_whole_egg_under_nest", "覆巢之下无完卵"),
            ("look_at_plums_quench_thirst", "望梅止渴"),
            ("look_with_new_eyes", "刮目相看"),
            ("clever_when_young", "小时了了"),
        ],
    },
    {
        "tradition_group": "唐代传奇",
        "story_type": "chuanqi",
        "source_hints": ["太平广记", "唐人传奇"],
        "stories": [
            ("yellow_millet_dream", "黄粱一梦"),
            ("nanke_dream", "南柯一梦"),
            ("liu_yi_delivers_letter", "柳毅传书"),
            ("story_of_yingying", "莺莺传"),
            ("huo_xiaoyu", "霍小玉传"),
            ("li_wa", "李娃传"),
            ("ren_shi_fox", "任氏传"),
            ("kunlun_slave", "昆仑奴"),
            ("hongxian_steals_box", "红线盗盒"),
            ("nie_yinniang", "聂隐娘"),
            ("curly_bearded_guest", "虬髯客传"),
            ("qianniang_soul_leaves_body", "倩娘离魂"),
            ("white_ape_legend", "补江总白猿传"),
            ("xie_xiaoe_revenge", "谢小娥传"),
            ("wushuang_legend", "无双传"),
        ],
    },
    {
        "tradition_group": "民间传说与戏曲叙事",
        "story_type": "legend",
        "source_hints": ["敦煌变文", "宋元话本", "元明清戏曲", "明清通俗小说"],
        "stories": [
            ("white_snake_legend", "白蛇传"),
            ("cowherd_weaver_girl", "牛郎织女"),
            ("mengjiangnu_great_wall", "孟姜女哭长城"),
            ("butterfly_lovers", "梁山伯与祝英台"),
            ("chenxiang_splits_mountain", "沉香劈山救母"),
            ("eight_immortals_cross_sea", "八仙过海"),
            ("liu_hai_plays_golden_toad", "刘海戏金蟾"),
            ("mulan_substitution", "花木兰从军"),
            ("orphan_of_zhao", "赵氏孤儿"),
            ("injustice_to_dou_e", "窦娥冤"),
            ("romance_western_chamber", "西厢记"),
            ("peony_pavilion_return_soul", "牡丹亭还魂"),
            ("mu_guiying_takes_command", "穆桂英挂帅"),
            ("yue_mother_tattoos_words", "岳母刺字"),
        ],
    },
    {
        "tradition_group": "西游封神章回故事",
        "story_type": "novel_episode",
        "source_hints": ["西游记", "封神演义"],
        "stories": [
            ("havoc_in_heaven", "大闹天宫"),
            ("three_fights_white_bone_demon", "三打白骨精"),
            ("kingdom_of_women", "女儿国"),
            ("true_false_monkey_king", "真假美猴王"),
            ("havoc_five_villages_temple", "大闹五庄观"),
            ("borrow_plantain_fan_three_times", "三借芭蕉扇"),
            ("erlang_fights_monkey", "二郎神斗孙悟空"),
            ("crossing_tongtian_river", "通天河降妖"),
            ("spider_demons_cave", "盘丝洞"),
            ("nezha_stirs_sea", "哪吒闹海"),
            ("jiang_ziya_enfeoffs_gods", "姜子牙封神"),
            ("leizhenzi_is_born", "雷震子出世"),
            ("yang_jian_subdues_meishan", "杨戬收梅山七怪"),
            ("daji_bewitches_zhou", "妲己惑纣"),
            ("king_wu_overthrows_zhou", "武王伐纣"),
        ],
    },
    {
        "tradition_group": "聊斋志怪",
        "story_type": "zhiguai",
        "source_hints": ["聊斋志异"],
        "stories": [
            ("painted_skin", "画皮"),
            ("nie_xiaoqian", "聂小倩"),
            ("yingning", "婴宁"),
            ("a_bao", "阿宝"),
            ("xiao_cui", "小翠"),
            ("cricket", "促织"),
            ("scholar_ye", "叶生"),
            ("rakshasa_sea_market", "罗刹海市"),
            ("laoshan_taoist", "崂山道士"),
            ("wang_liulang", "王六郎"),
            ("qingfeng", "青凤"),
            ("xin_shisiniang", "辛十四娘"),
            ("zhuqing", "竹青"),
            ("fox_wedding", "狐嫁女"),
            ("green_clothed_maiden", "绿衣女"),
        ],
    },
]


ENTRY_OVERRIDES: dict[str, dict[str, object]] = {
    "dong_yong_weaver_maiden": {
        "canonical_title": "董永与七仙女",
        "related_story_ids": ["cowherd_weaver_girl"],
        "relationship_note": "与牛郎织女共享人神婚恋母题，但人物谱系和文本传统不同，不合并为同一故事。",
    },
    "cowherd_weaver_girl": {
        "related_story_ids": ["dong_yong_weaver_maiden"],
        "relationship_note": "与董永遇天女传统相关但不等同；详标时需分别固定来源。",
    },
    "gun_steals_magic_soil": {
        "related_story_ids": ["gun_yu_flood_control"],
        "relationship_note": "属于鲧禹治水传统中的鲧支线，可独立检索但不表示已建立独立家族谱系。",
    },
    "yinglong_aids_yu": {
        "related_story_ids": ["gun_yu_flood_control"],
        "relationship_note": "属于禹治水相关神助母题，详标前先作为候选条目。",
    },
    "dayu_slays_xiangliu": {
        "related_story_ids": ["gun_yu_flood_control"],
        "relationship_note": "属于禹神话的相柳支线，是否独立成族需版本聚类复核。",
    },
    "dayu_chains_wuzhiqi": {
        "related_story_ids": ["gun_yu_flood_control"],
        "relationship_note": "属于禹神话的无支祁支线，是否独立成族需版本聚类复核。",
    },
}


def main() -> None:
    c0 = json.loads(C0_PATH.read_text(encoding="utf-8"))
    c1_secondary = json.loads(C1_SECONDARY_PATH.read_text(encoding="utf-8"))
    c2 = json.loads(C2_SELECTION_PATH.read_text(encoding="utf-8"))
    c3 = json.loads(C3_PATH.read_text(encoding="utf-8"))
    product_records = c3["story_versions"]
    secondary_records = c1_secondary["source_witnesses"]
    selections = c2["story_selections"]
    product_family_ids = [item["story_family_id"] for item in product_records]
    secondary_family_ids = [item["story_family_id"] for item in secondary_records]
    selection_family_ids = [item["story_family_id"] for item in selections]
    if len(product_records) != 30 or len(set(product_family_ids)) != 30:
        raise ValueError("C3 must contain exactly one product story for each of 30 families")
    if len(secondary_records) != 30 or len(set(secondary_family_ids)) != 30:
        raise ValueError("C1-secondary must contain exactly one witness for each of 30 families")
    if len(selections) != 30 or len(set(selection_family_ids)) != 30:
        raise ValueError("C2 must contain exactly one primary selection for each of 30 families")
    if set(product_family_ids) != set(secondary_family_ids) or set(product_family_ids) != set(selection_family_ids):
        raise ValueError("C1-secondary/C2/C3 family sets differ")
    if any(
        item.get("product_primary") is not True
        or item.get("record_role") != "product_primary_story"
        for item in product_records
    ):
        raise ValueError("C3 contains a non-primary record")
    c0_titles = {
        item["story_family_id"]: item["title"] for item in c0["story_families"]
    }
    detailed_types: dict[str, set[str]] = {}
    for version in product_records:
        detailed_types.setdefault(version["story_family_id"], set()).add(version["story_type"])

    seed_rows: list[dict[str, object]] = []
    discovery_groups: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, group in enumerate(GROUPS, 1):
        group_id = f"tradition_{index:02d}"
        discovery_groups.append(
            {
                "discovery_group_id": group_id,
                "title": group["tradition_group"],
                "source_work_leads": group["source_hints"],
                "lead_scope": "组级检索入口，不表示每部作品都直接包含组内每一个候选条目。",
            }
        )
        for family_id, title in group["stories"]:  # type: ignore[index]
            if family_id in seen:
                raise ValueError(f"duplicate catalog family id: {family_id}")
            seen.add(family_id)
            is_detailed = family_id in detailed_types
            row = {
                    "story_family_id": family_id,
                    "canonical_title": c0_titles.get(family_id, title),
                    # Family-level catalog classification is curated here and
                    # does not depend on whichever version happens to appear first.
                    "story_type": group["story_type"],
                    "runtime_story_types": sorted(detailed_types.get(family_id, set())),
                    "tradition_group": group["tradition_group"],
                    "discovery_group_id": group_id,
                    "source_evidence_status": "fixed_in_c0_primary_selected" if is_detailed else "not_fixed",
                    "catalog_status": "c3_dev_detailed" if is_detailed else "metadata_only",
                    "runtime_eligible": is_detailed,
                    "production_eligible": False,
                    "next_gate": (
                        "primary_story_expert_cultural_rights_review_pending"
                        if is_detailed
                        else "fix_source_witnesses_select_primary_and_complete_c3_annotation"
                    ),
                }
            row.update(ENTRY_OVERRIDES.get(family_id, {}))
            seed_rows.append(row)

    # A completed batch must never disappear merely because it was not in the
    # initial discovery seed. Add it and make the divergence visible.
    for family_id, story_types in detailed_types.items():
        if family_id in seen:
            continue
        seed_rows.append(
            {
                "story_family_id": family_id,
                "canonical_title": c0_titles.get(family_id, family_id),
                "story_type": next(iter(story_types)) if len(story_types) == 1 else "mixed",
                "runtime_story_types": sorted(story_types),
                "tradition_group": "新增详标批次",
                "discovery_group_id": "new_detailed",
                "source_evidence_status": "fixed_in_c0_primary_selected",
                "catalog_status": "c3_dev_detailed",
                "runtime_eligible": True,
                "production_eligible": False,
                "next_gate": "primary_story_expert_cultural_rights_review_pending",
            }
        )

    if any(item["discovery_group_id"] == "new_detailed" for item in seed_rows):
        discovery_groups.append(
            {
                "discovery_group_id": "new_detailed",
                "title": "新增详标批次",
                "source_work_leads": [],
                "lead_scope": "具体固定来源见 C0；产品主文本由 C2 指定并进入 C3；后台第二见证另存 C1-secondary。",
            }
        )
    seed_rows.sort(key=lambda item: (str(item["tradition_group"]), str(item["story_family_id"])))
    detailed_count = sum(item["catalog_status"] == "c3_dev_detailed" for item in seed_rows)
    payload = {
        "schema_version": "0.1.0",
        "catalog_version": "c1-discovery-2026-08-09-primary",
        "snapshot_date": "2026-08-09",
        "corpus_tier": "c1-discovery",
        "coverage_claim": (
            f"发现目录共 {len(seed_rows)} 个候选故事条目，混合家族、单篇与章回片段；"
            f"其中 {detailed_count} 个已各选定 1 个 C3-dev 产品主文本，并各保留 1 条后台第二来源见证；"
            "其余仅为待聚类、固定来源、"
            "摘录、权利和文化复核的 metadata_only 研究线索。"
        ),
        "status_semantics": {
            "metadata_only": "仅证明已纳入扩库队列；组级来源作品只是检索入口，不是逐条固定引文，不得进入推荐。",
            "c3_dev_detailed": "已选择一个产品主文本进入本地 Demo；第二见证仅供后台来源审计，主文本仍未达到生产发布门槛。",
        },
        "known_coverage_gaps": [
            "候选条目的粒度仍混合家族、单篇与章回片段；进入 C3 前必须先聚类、固定来源并选择唯一产品主文本。",
            "尚未系统展开《夷坚志》《剪灯新话》《阅微草堂笔记》《子不语》等宋明清志怪笔记。",
            "候选条目尚未逐条补齐可统计的最早见证年代，不能据此宣称均衡覆盖先秦至清代。",
            "组级来源作品只供检索，不得被界面或文档改写成逐条固定出处。",
        ],
        "catalog_counts": {
            "story_entries": len(seed_rows),
            "c3_dev_detailed": detailed_count,
            "metadata_only": len(seed_rows) - detailed_count,
            "production_eligible": 0,
        },
        "discovery_groups": discovery_groups,
        "story_entries": seed_rows,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["catalog_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
