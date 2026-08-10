"""Historical helper for the 2026-08-08 two-witness expansion.

This file is retained as an audit artifact, not a current build entrypoint.  Its
old layout wrote both witnesses into C3 and is incompatible with the current
one-product-primary-story policy.  ``main`` therefore refuses to mutate data.
Every page
revision and excerpt below was checked through the public MediaWiki revision
API before being frozen here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DATA_ROOT = Path(__file__).resolve().parents[1]
C0_PATH = DATA_ROOT / "corpus" / "c0_source_manifest.json"
C3_PATH = DATA_ROOT / "corpus" / "c3_story_versions.json"
RETRIEVED_AT = "2026-08-08T23:57:06+08:00"


FAMILIES = [
    {
        "story_family_id": "nvwa_mends_sky",
        "title": "女娲补天",
        "version_ids": ["nvwa_huainanzi_lanming", "nvwa_liezi_tangwen"],
        "coverage_note": "《淮南子》叙事段与《列子》传本中的宇宙论异文；炼石、断鳌足相近，但灾变语境和叙事范围不同。",
    },
    {
        "story_family_id": "jingwei_fills_sea",
        "title": "精卫填海",
        "version_ids": ["jingwei_shanhaijing_beishan", "jingwei_shuyiji_expansion"],
        "coverage_note": "《山海经》发鸠山叙事与《述异记》较晚扩展；后者新增海燕婚配、后代与禁水等材料。",
    },
    {
        "story_family_id": "yugong_moves_mountains",
        "title": "愚公移山",
        "version_ids": ["yugong_liezi_tangwen", "yugong_taipingyulan_0040_quote"],
        "coverage_note": "《列子》传世文本与北宋《太平御览》所录《列子》引文；第二条是类书引文见证，不是独立起源文本。",
    },
    {
        "story_family_id": "fox_borrows_tiger_might",
        "title": "狐假虎威",
        "version_ids": ["fox_tiger_zhanguoce_chu1", "fox_tiger_taipingyulan_0909_chunqiu_houyu"],
        "coverage_note": "《战国策》楚策一的游说寓言与《太平御览》所引《春秋后语》异文；两条均保留政治修辞语境。",
    },
]


CONFIGS: list[dict[str, Any]] = [
    {
        "id": "nvwa_huainanzi_lanming",
        "family": "nvwa_mends_sky",
        "c0_work": "淮南子",
        "section": "览冥训",
        "compiler": "刘安及门客编",
        "period": "西汉",
        "title": "女娲补天：《淮南子·览冥训》",
        "era": "西汉",
        "genre": "子书中的神话材料",
        "story_type": "myth",
        "work": "《淮南子·览冥训》",
        "author": "刘安及门客编",
        "evidence_anchor": "往古之时至苍天补、四极正段",
        "source_url": "https://zh.wikisource.org/wiki/淮南子/覽冥訓",
        "permalink": "https://zh.wikisource.org/w/index.php?title=淮南子/覽冥訓&oldid=3933631",
        "revid": 3933631,
        "timestamp": "2026-05-09T09:20:16Z",
        "excerpt": "於是女媧煉五色石以補蒼天，斷鼇足以立四極。殺黑龍以濟冀州，積蘆灰以止淫水。",
        "relation": "early_han_narrative_witness",
        "relation_note": "本段把补天置于四极废、九州裂、水火失序的连续灾变中，并列炼石、断鳌足、杀黑龙与积芦灰。",
        "relation_confidence": "high_for_page_text_medium_until_edition_review",
        "ending": "秩序恢复后，文本继续称述女娲之功及其不彰功名；本项目不把这一段简化成单一的“拯救世界”口号。",
        "motifs": ["四极倾废", "炼五色石", "断鳌足", "止洪水", "重建秩序"],
        "summary": "天地秩序崩坏，火水与猛兽共同威胁众人。女娲炼五色石补天、断鳌足立四极，并处理黑龙与洪水，使天地重新可居。",
        "characters": ["女娲", "受灾众民", "黑龙"],
        "conflict": "当承载众人的世界结构失效时，如何修补裂隙并恢复可持续的秩序。",
        "imagery": ["裂开的苍天", "五色石", "四根鳌足", "止水的芦灰"],
        "emotional_arc": "天地失序 → 多线修补 → 四极复正",
        "resonance": "可联想到修补一个已经失效的共同环境，但不预设用户必须承担救世责任。",
        "non_fit": "若用户不想接触灾难、洪水、巨型动物肢体或宏大责任隐喻，可换选其他故事。",
        "warnings": ["灾难", "洪水", "杀黑龙", "断鳌足"],
        "preserve": ["五色石补天、断鳌足立四极", "同段还有杀黑龙与积芦灰止水", "这是《览冥训》的连续灾变语境"],
        "may": ["把补天映射为修复公共关系或制度裂缝", "让用户决定修补责任是否需要分担", "以抽象色块表现五色石"],
        "prohibit": ["把女娲简化成性别本质主义符号", "省略同段的洪水与多重治理语境却声称是完整原典", "宣称故事具有治疗效果"],
        "scenes": ["四极倾斜的幕框", "五色石逐片嵌回", "水线退去后留下可行走的地面"],
        "visuals": ["矿物五色", "裂纹", "鳌足抽象支柱", "芦灰水线"],
        "visual_prohibit": ["写实肢解巨鳌", "默认套用西方创世母题", "把女娲塑成现代民族或性别宣传符号"],
        "gaps": ["尚未逐字对照可靠影印本", "需复核今本《淮南子》的版本与标点层", "现代改编需独立文化与性别表述复核"],
    },
    {
        "id": "nvwa_liezi_tangwen",
        "family": "nvwa_mends_sky",
        "c0_work": "列子",
        "section": "汤问篇",
        "compiler": "传本题列御寇；今本成书与整理层次待复核",
        "period": "传世文本层次待文献学复核",
        "title": "女娲补天：《列子·汤问篇》异文",
        "era": "传世文本层次待复核",
        "genre": "子书宇宙论/神话材料",
        "story_type": "myth",
        "work": "《列子·汤问篇》",
        "author": "传本题列御寇；今本成书与整理层次待复核",
        "evidence_anchor": "天地亦物、物有不足段",
        "source_url": "https://zh.wikisource.org/wiki/列子/湯問篇",
        "permalink": "https://zh.wikisource.org/w/index.php?title=列子/湯問篇&oldid=2608932",
        "revid": 2608932,
        "timestamp": "2025-10-22T14:10:39Z",
        "excerpt": "然則天地亦物也。物有不足，故昔者女媧氏練五色石以補其闕，斷鼇之足以立四極。",
        "relation": "transmitted_philosophical_variant_witness",
        "relation_note": "本段以“天地亦物、物有不足”解释补天，随后接共工触不周山；不可与《淮南子》灾变段逐句混写。",
        "relation_confidence": "high_for_page_text_low_for_unreviewed_textual_strata",
        "ending": "补天只作为天地有缺的例证；本段随即转入共工触山与天倾地陷的解释，叙事功能不同于《览冥训》。",
        "motifs": ["天地有缺", "炼五色石", "断鳌足", "四极", "宇宙论解释"],
        "summary": "在关于天地是否有边界的问答中，文本提出“天地也是物，物会有所不足”，并以女娲炼五色石补缺、断鳌足立四极为例。",
        "characters": ["女娲", "殷汤", "夏革"],
        "conflict": "一个并不完满的世界，如何被理解、承认并修补。",
        "imagery": ["有缺口的天幕", "五色石", "四极支点", "问答席"],
        "emotional_arc": "追问边界 → 承认不足 → 修补缺口",
        "resonance": "可讨论承认系统不完满与着手修补之间的关系，不把“补好一切”强加给用户。",
        "non_fit": "若用户不喜欢抽象宇宙论、巨型动物或承担修补责任的隐喻，可换选其他故事。",
        "warnings": ["断鳌足", "宇宙灾变"],
        "preserve": ["天地亦物、物有不足的论述语境", "练五色石补阙与断鳌足立四极", "今本《列子》文本层次尚待复核"],
        "may": ["把“承认不足”映射为发现系统缺口", "用用户可撤销的节点表达修补步骤", "让修补由多人而非单一英雄完成"],
        "prohibit": ["把传本题署和成书年代写成已定论", "混入《淮南子》杀黑龙、积芦灰并冒充本段", "宣称故事提供心理疗效"],
        "scenes": ["问答中的空白天幕", "五块不同色泽的补片", "四个支点缓慢复位"],
        "visuals": ["留白", "矿物色补片", "四点结构", "细金裂线"],
        "visual_prohibit": ["写实断足", "把文本年代争议隐去", "性别刻板化女娲形象"],
        "gaps": ["今本《列子》的成书与整理层次待文献学复核", "尚未对照影印本", "需判断网页标点与底本文字的关系"],
    },
    {
        "id": "jingwei_shanhaijing_beishan",
        "family": "jingwei_fills_sea",
        "c0_work": "山海经",
        "section": "北山经·发鸠之山",
        "compiler": "佚名，历代累积文本",
        "period": "先秦至汉初传承文本",
        "title": "精卫填海：《山海经·北山经》",
        "era": "先秦至汉初传承文本",
        "genre": "地理博物志/神话材料",
        "story_type": "myth",
        "work": "《山海经·北山经》发鸠之山条",
        "author": "佚名，历代累积文本",
        "evidence_anchor": "发鸠之山精卫条",
        "source_url": "https://zh.wikisource.org/wiki/山海經/北山經",
        "permalink": "https://zh.wikisource.org/w/index.php?title=山海經/北山經&oldid=442532",
        "revid": 442532,
        "timestamp": "2015-07-11T03:36:40Z",
        "excerpt": "有鳥焉，其狀如烏，文首、白喙、赤足，名曰精衛，其鳴自詨。是炎帝之少女，名曰女娃，女娃游于東海，溺而不返，故為精衛，常銜西山之木石，以堙于東海。",
        "relation": "early_geographic_myth_witness",
        "relation_note": "本条把鸟的形貌、鸣声、女娃溺亡与衔木石堙海连在发鸠山地理条目中。",
        "relation_confidence": "high_for_page_text_medium_until_edition_review",
        "ending": "摘录以精卫持续衔西山木石填东海收束；原条没有写成“终于填平大海”，不得补造成功结局。",
        "motifs": ["女娃溺海", "化为精卫", "衔木石", "填海", "未完成行动"],
        "summary": "发鸠山有一种名为精卫的鸟；文本将它认作炎帝少女女娃溺海后的变化。精卫不断从西山衔木石投入东海，但没有给出完成的结局。",
        "characters": ["女娃/精卫", "东海"],
        "conflict": "有限而反复的行动面对几乎不可衡量的大海。",
        "imagery": ["文首白喙赤足的鸟", "西山木石", "东海", "往返航线"],
        "emotional_arc": "失去归途 → 身份变化 → 持续往返",
        "resonance": "可联想到给无法一次解决的问题设置可见的小步行动，也允许用户质疑这种坚持是否值得。",
        "non_fit": "包含溺亡与无尽劳动；若与用户的丧亲、自伤或过度坚持经历冲突，应停止推荐或换选。",
        "warnings": ["溺亡", "死亡后变形", "无尽劳动"],
        "preserve": ["精卫是炎帝少女女娃溺海后的变化", "衔西山木石以堙东海", "原条未写填海成功"],
        "may": ["把往返动作映射为小步计划", "让用户明确暂停、退出或重新评估坚持", "用抽象鸟影与轨迹表现"],
        "prohibit": ["把溺亡美化为必要牺牲", "把不停止等同于唯一正确选择", "补造原典未载的成功结局或治疗效果"],
        "scenes": ["发鸠山鸟影", "一枚木石落入海面", "可暂停的往返轨迹"],
        "visuals": ["赤足白喙", "细小木石", "深蓝海面", "重复轨迹"],
        "visual_prohibit": ["写实溺亡", "尸体", "把精卫画成现代民族主义徽记", "强迫性重复的英雄化"],
        "gaps": ["尚未对照可靠影印本", "需复核“自詨”等字形与网页转录", "丧失与坚持主题需安全和文化双重复核"],
    },
    {
        "id": "jingwei_shuyiji_expansion",
        "family": "jingwei_fills_sea",
        "c0_work": "述异记（四库全书本）",
        "section": "卷上·精卫条",
        "compiler": "题梁任昉撰；题署与文本层累待复核",
        "period": "南朝题署作品的四库传本",
        "title": "精卫填海：《述异记》扩展",
        "era": "南朝题署作品的四库传本",
        "genre": "志怪/博物异闻",
        "story_type": "zhiguai",
        "work": "《述异记》（四库全书本）卷上",
        "author": "题梁任昉撰；题署与文本层累待复核",
        "evidence_anchor": "昔炎帝女溺死东海中条",
        "source_url": "https://zh.wikisource.org/wiki/述異記_(四庫全書本)/卷上",
        "permalink": "https://zh.wikisource.org/w/index.php?title=述異記_(四庫全書本)/卷上&oldid=793545",
        "revid": 793545,
        "timestamp": "2016-10-28T04:46:56Z",
        "excerpt": "昔炎帝女溺死東海中化為精衛其名自呼每銜西山木石填東海偶海燕而生子生雌狀如精衛生雄如海燕",
        "relation": "later_zhiguai_expansion",
        "relation_note": "较晚志怪条目保留溺海、化鸟和填海，并新增与海燕婚配、雌雄后代及别名等材料。",
        "relation_confidence": "high_for_page_text_low_for_unreviewed_attribution",
        "ending": "后文还说精卫在曾溺之处誓不饮水，并列鸟誓、冤禽、志鸟、帝女雀等名；这些材料不能倒灌为《山海经》原文。",
        "motifs": ["炎帝女溺海", "化为精卫", "衔木石填海", "海燕婚配", "后代分形"],
        "summary": "这条较晚异闻把炎帝之女溺海、化为精卫与持续填海连在一起，又加入精卫与海燕生育后代、雌雄分别承袭外形的扩展。",
        "characters": ["炎帝之女/精卫", "海燕", "后代"],
        "conflict": "一段关于失去与抵抗的旧叙事，如何在后世被加入亲缘、后代和禁忌。",
        "imagery": ["西山木石", "海燕", "雌雄不同的鸟群", "禁饮水域"],
        "emotional_arc": "溺亡 → 化鸟填海 → 形成后代与禁忌",
        "resonance": "可讨论一个故事如何被后世继续扩写；映射时应允许用户保留或删除亲缘延续节点。",
        "non_fit": "含溺亡、婚配与后代设定；不适合希望避开死亡、生育或家庭延续话题的用户。",
        "warnings": ["溺亡", "死亡后变形", "婚配与生育", "无尽劳动"],
        "preserve": ["本条属于《述异记》较晚扩展", "海燕婚配和雌雄后代为本条新增重点", "不得把新增材料冒充《山海经》原条"],
        "may": ["比较同族版本新增或删除的节点", "让用户选择是否保留亲缘延续", "用鸟群剪影而非写实人物表现"],
        "prohibit": ["把生育或家族延续设为默认正确结局", "把较晚扩展倒写成最早版本", "写实呈现溺亡或宣称疗效"],
        "scenes": ["单鸟往返", "海燕加入同一片海", "分成两种形态的后代鸟影"],
        "visuals": ["鸟群剪影", "木石", "海面禁线", "两种羽色"],
        "visual_prohibit": ["写实溺亡", "强制异性婚育价值观", "把不同版本拼成唯一标准故事"],
        "gaps": ["《述异记》题署与文本层累待复核", "四库页无现代标点且含特殊字形模板", "尚未对照扫描与第二来源"],
        "transcription_status": "community_transcription_unverified_against_scan_no_modern_punctuation",
    },
    {
        "id": "yugong_liezi_tangwen",
        "family": "yugong_moves_mountains",
        "c0_work": "列子",
        "section": "汤问篇·愚公移山",
        "compiler": "传本题列御寇；今本成书与整理层次待复核",
        "period": "传世文本层次待文献学复核",
        "title": "愚公移山：《列子·汤问篇》",
        "era": "传世文本层次待复核",
        "genre": "子书寓言",
        "story_type": "fable",
        "work": "《列子·汤问篇》愚公移山段",
        "author": "传本题列御寇；今本成书与整理层次待复核",
        "evidence_anchor": "北山愚公与河曲智叟问答段",
        "source_url": "https://zh.wikisource.org/wiki/列子/湯問篇",
        "permalink": "https://zh.wikisource.org/w/index.php?title=列子/湯問篇&oldid=2608932",
        "revid": 2608932,
        "timestamp": "2025-10-22T14:10:39Z",
        "excerpt": "雖我之死，有子存焉；子又生孫，孫又生子；子又有子，子又有孫，子子孫孫無窮匱也，而山不加增，何苦而不平？",
        "relation": "transmitted_liezi_fable_witness",
        "relation_note": "传世《列子》保留完整的家庭商议、劳作、智叟质疑与神力移山结局；成书层次仍需文献学复核。",
        "relation_confidence": "high_for_page_text_low_for_unreviewed_textual_strata",
        "ending": "操蛇之神因愚公不止而告帝，帝命夸娥氏二子移走两山；不能把神力结局删掉后称为完整原典。",
        "motifs": ["太行王屋", "集体劳作", "代际延续", "愚智辩论", "神力移山"],
        "summary": "年近九十的愚公因两山阻路，和家人商议移山。智叟质疑其能力，愚公以代际延续回应；最终神将移走两山。",
        "characters": ["愚公", "愚公妻", "子孙与邻人遗男", "河曲智叟", "操蛇之神", "夸娥氏二子"],
        "conflict": "长期目标、有限个人能力、集体劳动与外部质疑如何被放在同一张行动图上。",
        "imagery": ["太行王屋", "箕畚", "往返渤海", "代际时间线", "被移走的两山"],
        "emotional_arc": "受阻 → 集体商议 → 漫长劳作 → 质疑交锋 → 神力移山",
        "resonance": "可用于讨论长期任务如何拆分、谁承担劳动以及何时需要外援，也允许用户拒绝无限坚持。",
        "non_fit": "可能触及过劳、家庭义务、代际牺牲与宏大目标；不应推荐给正在表达被责任压垮的用户。",
        "warnings": ["高龄劳动", "代际义务", "长期体力劳动", "神力介入"],
        "preserve": ["家庭商议与妻子的现实质疑", "智叟与愚公关于有限生命和代际延续的争论", "最后由神力移山而非人力完工"],
        "may": ["把移山映射为拆分长期项目", "显式标注参与者、劳动量、退出权和外援", "允许把目标改为绕行、协商或缩小范围"],
        "prohibit": ["鼓励无休止过劳或代际牺牲", "把妻子与智叟简单污名化为阻碍者", "删去神力结局后宣称人力必然成功"],
        "scenes": ["两山形成阻路画布", "多人的箕畚路线", "智叟问题卡", "外援移动山体"],
        "visuals": ["山形", "路径线", "多人节点", "代际刻度"],
        "visual_prohibit": ["把家庭劳动者画成无名耗材", "过劳英雄化", "把质疑者丑化"],
        "gaps": ["今本《列子》文本层次待复核", "尚未对照可靠影印本", "长期劳动与家庭责任的现代映射需伦理复核"],
    },
    {
        "id": "yugong_taipingyulan_0040_quote",
        "family": "yugong_moves_mountains",
        "c0_work": "太平御览",
        "section": "卷四十·地部五·王屋山引《列子》",
        "compiler": "李昉等奉敕编",
        "period": "北宋类书所保存的《列子》引文",
        "title": "愚公移山：《太平御览》卷四十引文",
        "era": "北宋类书所存较早文献引文",
        "genre": "类书引文/寓言见证",
        "story_type": "fable",
        "work": "《太平御览》卷四十引《列子》",
        "author": "李昉等奉敕编；条目标引《列子》",
        "evidence_anchor": "地部五·王屋山·《列子》曰",
        "source_url": "https://zh.wikisource.org/wiki/太平御覽/0040",
        "permalink": "https://zh.wikisource.org/w/index.php?title=太平御覽/0040&oldid=2587290",
        "revid": 2587290,
        "timestamp": "2025-08-05T09:33:19Z",
        "excerpt": "雖我之死，有子存焉；子又生孫，孫又生子。子子孫孫，無窮匱也，而山不加增，何苦而不力乎？",
        "relation": "song_encyclopedia_quotation_of_liezi",
        "relation_note": "这是北宋类书所录《列子》引文见证，出现“不力乎”等文字差异；不得称为独立起源版本。",
        "relation_confidence": "high_for_quotation_medium_until_scan_collation",
        "ending": "类书引文同样保留帝命夸娥氏二子负山的结局，但个别字句与传世《列子》页面不同，需逐字校勘。",
        "motifs": ["类书引文", "太行王屋", "代际延续", "智叟质疑", "神力移山"],
        "summary": "北宋类书在“王屋山”条下引录愚公故事。主干与传世《列子》相近，但在措辞和节略上存在可见差异，适合作为文本见证比较。",
        "characters": ["愚公", "家人与邻童", "河曲智叟", "操蛇之神", "夸娥氏二子"],
        "conflict": "同一寓言在后世类书中被怎样摘录、压缩并改变字句。",
        "imagery": ["类书栏框", "王屋山条目", "两列异文", "代际路线"],
        "emotional_arc": "条目定位 → 劳作节录 → 辩论异文 → 神力结局",
        "resonance": "适合让用户比较版本并决定保留哪些节点，而不是把类书引文当作另一个完全独立故事。",
        "non_fit": "若用户不希望做文本比较，或对家庭责任与长期劳动敏感，可换选其他故事。",
        "warnings": ["高龄劳动", "代际义务", "类书节录"],
        "preserve": ["明确来源是《太平御览》引《列子》", "保留与传世页的措辞差异", "不得声称是独立起源文本"],
        "may": ["在映射画布上并列两个版本节点", "让用户选择采用哪个措辞或情节粒度", "把类书节录作为来源比较层"],
        "prohibit": ["把类书引文伪装成第二部独立古籍故事", "用异文证明单一寓意", "鼓励过劳或强制家人承担目标"],
        "scenes": ["类书页签", "两列句读差异", "山与路径的节略图"],
        "visuals": ["页栏", "异文标记", "山形", "短路线"],
        "visual_prohibit": ["伪造古籍扫描", "把文本差异抹平", "劳动者匿名化"],
        "gaps": ["尚未对照《太平御览》影印本", "需校勘“不力乎”等差异是否来自底本或转录", "引文与今本《列子》的谱系关系未审定"],
    },
    {
        "id": "fox_tiger_zhanguoce_chu1",
        "family": "fox_borrows_tiger_might",
        "c0_work": "战国策（士礼居丛书本）",
        "section": "楚一·荆宣王问群臣",
        "compiler": "传世《战国策》经刘向编定；具体页面底本待复核",
        "period": "战国游说材料的传世整理本",
        "title": "狐假虎威：《战国策·楚策一》",
        "era": "战国游说材料的传世整理本",
        "genre": "策士说辞中的寓言",
        "story_type": "fable",
        "work": "《战国策》（士礼居丛书本）楚一",
        "author": "战国游说材料；经刘向编定，具体篇章作者不详",
        "evidence_anchor": "荆宣王问群臣、江一对曰段",
        "source_url": "https://zh.wikisource.org/wiki/戰國策_(士禮居叢書本)/楚/一",
        "permalink": "https://zh.wikisource.org/w/index.php?title=戰國策_(士禮居叢書本)/楚/一&oldid=2458633",
        "revid": 2458633,
        "timestamp": "2024-10-07T08:13:07Z",
        "excerpt": "虎求百獸而食之，得狐。狐曰：『子無敢食我也。天帝使我長百獸，今子食我，是逆天帝命也。",
        "relation": "warring_states_rhetorical_fable_witness",
        "relation_note": "寓言嵌在江一向楚宣王解释诸侯所畏实为楚国甲兵的政治说辞中，不是脱离语境的动物童话。",
        "relation_confidence": "high_for_page_text_medium_until_edition_review",
        "ending": "百兽因畏虎而逃，虎误以为畏狐；江一借此说明北方所畏的是楚王甲兵，而非昭奚恤本人。",
        "motifs": ["虎捕狐", "借天帝之命", "百兽逃散", "误认威势来源", "政治说辞"],
        "summary": "虎抓到狐狸，狐狸声称自己奉天帝之命统领百兽，并让虎跟在身后验证。百兽见虎而逃，虎却误以为它们害怕狐狸；江一用此比喻权势来源。",
        "characters": ["狐狸", "老虎", "百兽", "江一", "楚宣王", "昭奚恤"],
        "conflict": "表面影响力与真正权力来源之间的错认。",
        "imagery": ["狐在前虎在后", "四散百兽", "看不见的甲兵", "宫廷问答"],
        "emotional_arc": "被捕危机 → 借势设计 → 百兽逃散 → 权力真相揭示",
        "resonance": "可用于辨认一项资源、身份或平台背后的真实权力来源，也可讨论借势的伦理边界。",
        "non_fit": "若用户正在遭遇权力压迫、职场报复或安全威胁，应避免把情境轻率寓言化，并优先提供退出与求助选项。",
        "warnings": ["捕食威胁", "权力操纵", "政治比喻"],
        "preserve": ["百兽实际畏虎而非狐", "寓言服务于江一关于昭奚恤与楚国甲兵的说辞", "狐狸的策略发生在被捕食威胁下"],
        "may": ["把权势来源画成可追溯的依赖图", "让用户区分生存策略、欺骗与结构性权力", "用动物剪影表现而不贴现实人群标签"],
        "prohibit": ["把弱者在威胁下的策略简单道德化", "用狐或虎影射现实族群、性别或职业", "脱离政治语境只讲成炫耀骗术"],
        "scenes": ["虎口前的谈判", "狐前虎后的队列", "百兽逃散后显现权力来源线"],
        "visuals": ["狐与虎双影", "百兽足迹", "权力来源箭头", "策士席"],
        "visual_prohibit": ["拟人化为特定现实群体", "血腥捕食", "把操纵包装成无条件成功秘诀"],
        "gaps": ["尚未对照士礼居丛书扫描", "页面题作江一，常见现代转述或作江乙，姓名异文需复核", "政治寓言的现代映射需避免污名化"],
    },
    {
        "id": "fox_tiger_taipingyulan_0909_chunqiu_houyu",
        "family": "fox_borrows_tiger_might",
        "c0_work": "太平御览",
        "section": "卷九百九·兽部二十一·狐引《春秋后语》",
        "compiler": "李昉等奉敕编；条目标引《春秋后语》",
        "period": "北宋类书所保存的较早材料引文",
        "title": "狐假虎威：《太平御览》引《春秋后语》",
        "era": "北宋类书所存较早材料引文",
        "genre": "类书引文/政治寓言见证",
        "story_type": "fable",
        "work": "《太平御览》卷九百九引《春秋后语》",
        "author": "李昉等奉敕编；引文题《春秋后语》",
        "evidence_anchor": "兽部二十一·狐·《春秋后语》曰",
        "source_url": "https://zh.wikisource.org/wiki/太平御覽/0909",
        "permalink": "https://zh.wikisource.org/w/index.php?title=太平御覽/0909&oldid=2588238",
        "revid": 2588238,
        "timestamp": "2025-08-05T14:44:18Z",
        "excerpt": "虎求百獸而食之。得狐，狐曰：『子無啖我，天帝令我長百獸，子若食我，是逆天帝之命。",
        "relation": "song_encyclopedia_quotation_of_chunqiu_houyu",
        "relation_note": "北宋类书明确标作《春秋后语》引文，词句与《战国策》页有差异；这是引文见证，不是现存《春秋后语》原本。",
        "relation_confidence": "high_for_quotation_low_for_lost_source_context",
        "ending": "引文同样将百兽逃散解释为畏虎，并以君威说明昭奚恤受畏；其来源载体和字句必须与《战国策》分开保存。",
        "motifs": ["类书引文", "虎捕狐", "天帝之命", "百兽逃散", "君威"],
        "summary": "《太平御览》在狐类条目中引《春秋后语》，保存了狐借虎威的相近叙事，但用“无啖我”“天帝令我长百兽”等不同措辞。",
        "characters": ["狐狸", "老虎", "百兽", "江乙", "楚宣王", "昭奚恤"],
        "conflict": "一则政治寓言经类书引录后，如何保留主干并产生措辞差异。",
        "imagery": ["类书狐条", "狐前虎后", "逃散足迹", "君威标注"],
        "emotional_arc": "类书定位 → 被捕谈判 → 借势脱险 → 君威解释",
        "resonance": "适合在映射画布上比较来源、措辞与权力解释，而不是把两个见证自动合并。",
        "non_fit": "若用户不愿做异文比较，或正处在现实权力威胁中，应换用更中性的故事或先处理安全需求。",
        "warnings": ["捕食威胁", "权力操纵", "佚书/引文边界"],
        "preserve": ["明确现存载体是《太平御览》引《春秋后语》", "保留与《战国策》的措辞差异", "保留君威而非狐自身威力的解释"],
        "may": ["并排显示两条引文的差异", "把真正权力来源画成可编辑依赖图", "让用户选择保留或拒绝借势策略"],
        "prohibit": ["称作《春秋后语》完整原本", "自动与《战国策》合并为唯一标准文本", "把操纵或欺骗宣传成普遍处世准则"],
        "scenes": ["类书页签标明引书", "两列异文", "权力来源从狐移向虎与甲兵"],
        "visuals": ["引号框", "狐虎双影", "来源线", "差异标记"],
        "visual_prohibit": ["伪造佚书原页", "现实群体标签化", "血腥捕食"],
        "gaps": ["《春秋后语》引文的底本谱系待查", "尚未对照《太平御览》扫描", "网页转换标记与页面显示层需逐字复核"],
    },
]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_c0(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "story_version_id": config["id"],
        "story_family_id": config["family"],
        "work_title": config["c0_work"],
        "section": config["section"],
        "attributed_author_or_compiler": config["compiler"],
        "period": config["period"],
        "story_type": config["story_type"],
        "genre": config["genre"],
        "source_repository": "zhwikisource",
        "source_url": config["source_url"],
        "source_permalink": config["permalink"],
        "page_revision_id": config["revid"],
        "page_revision_timestamp": config["timestamp"],
        "excerpt": config["excerpt"],
        "excerpt_sha256": sha256(config["excerpt"]),
        "relation_to_family": config["relation"],
        "transcription_status": config.get(
            "transcription_status", "community_transcription_unverified_against_scan"
        ),
        "rights_status": "development_short_excerpt_only",
        "production_eligible": False,
    }


def make_c3(config: dict[str, Any]) -> dict[str, Any]:
    relation = {
        "type": config["relation"],
        "note": config["relation_note"],
        "confidence": config["relation_confidence"],
    }
    excerpt_hash = sha256(config["excerpt"])
    return {
        "story_version_id": config["id"],
        "story_family_id": config["family"],
        "corpus_tier": "c3-dev",
        "editorial_status": "editorial_draft",
        "eligible_for_demo": True,
        "production_eligible": False,
        "development_runtime_eligible": True,
        "adult_only": False,
        "default_offer_eligible": True,
        "requires_explicit_adult_opt_in": False,
        "title": config["title"],
        "era": config["era"],
        "genre": config["genre"],
        "story_type": config["story_type"],
        "motifs": config["motifs"],
        "source_canon": {
            "immutable": True,
            "work": config["work"],
            "author": config["author"],
            "edition": "中文维基文库固定修订，非学术定本",
            "evidence_anchor": config["evidence_anchor"],
            "source_repository": "中文维基文库",
            "source_url": config["source_url"],
            "source_permalink": config["permalink"],
            "page_revision_id": config["revid"],
            "page_revision_timestamp": config["timestamp"],
            "retrieved_at": RETRIEVED_AT,
            "original_excerpt": config["excerpt"],
            "excerpt_sha256": excerpt_hash,
            "excerpt_normalization": "UTF-8；去除页面模板标记，保留固定修订页面所见字形与标点；无标点页面不擅加标点",
            "ending": "本项目编辑草稿（非原典引文）：" + config["ending"],
            "ending_origin": "project_editorial_draft_not_source_text",
            "relation_to_family": relation,
        },
        "story_card": {
            "editorial_origin": "本项目编辑草稿，非原典译文、原典原意或学术定本",
            "title": FAMILIES[[f["story_family_id"] for f in FAMILIES].index(config["family"])]["title"],
            "subtitle": config["work"],
            "summary": config["summary"],
            "characters": config["characters"],
            "conflict": config["conflict"],
            "motifs": config["motifs"],
            "imagery": config["imagery"],
            "emotional_arc": config["emotional_arc"],
            "resonance": config["resonance"],
            "non_fit": config["non_fit"],
            "content_warnings": config["warnings"],
        },
        "adaptation_boundary": {
            "must_preserve": config["preserve"],
            "may_transform": config["may"],
            "prohibited": config["prohibit"],
        },
        "theatre_asset_pack": {
            "status": "editorial_draft",
            "asset_mode": "local_svg_css_placeholder",
            "scene_anchors": config["scenes"],
            "visual_motifs": config["visuals"],
            "prohibited_visuals": config["visual_prohibit"],
            "image_assets": [],
            "audio_assets": [],
            "needs_visual_cultural_review": True,
        },
        "rights_and_access": {
            "status": "development_short_excerpt_open_review",
            "access_mode": "short_excerpt",
            "copyright_basis": "古代基础文本推定公版；所用网页转录按 CC BY-SA 4.0 提供",
            "transcription_license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attribution": f"{config['work']}；中文维基文库；oldid {config['revid']}",
            "allowed_uses": [
                "local_development_runtime",
                "source_linking",
                "attributed_short_excerpt_display",
            ],
            "prohibited_until_review": [
                "production_release",
                "full_text_ingestion",
                "full_text_vectorization",
                "reuse_of_modern_translation_image_or_audio",
            ],
            "share_alike_assessment": "open",
            "cultural_access_status": "open",
        },
        "review_record": {
            "status": "c3_dev_pending_dual_review",
            "editorial_status": "editorial_draft",
            "reviewers": [],
            "retrieval_checked_at": "2026-08-08",
            "required_reviews": [
                "compare_excerpt_with_scan",
                "bibliographic_review",
                "rights_review",
                "two_person_cultural_review",
            ],
            "known_gaps": config["gaps"],
        },
        "provenance": [
            {
                "id": f"ws-oldid-{config['revid']}",
                "title": f"{config['work']}固定修订",
                "work": config["c0_work"],
                "author": config["compiler"],
                "era": config["period"],
                "edition": "中文维基文库社区转录",
                "locator": config["evidence_anchor"],
                "url": config["permalink"],
                "rights": "CC BY-SA 4.0 transcription; underlying ancient text public-domain-believed",
            }
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    raise RuntimeError(
        "superseded historical helper: use data/merge_expansion_batches.py, "
        "which projects one product primary into C3 and one secondary witness into C1"
    )

    # Historical implementation retained below for audit reproducibility only.
    c0 = json.loads(C0_PATH.read_text(encoding="utf-8"))
    c3 = json.loads(C3_PATH.read_text(encoding="utf-8"))
    new_ids = {config["id"] for config in CONFIGS}
    new_family_ids = {family["story_family_id"] for family in FAMILIES}

    baseline_c0 = [item for item in c0["source_items"] if item["story_version_id"] not in new_ids]
    baseline_c3 = [item for item in c3["story_versions"] if item["story_version_id"] not in new_ids]
    if len(baseline_c0) != 16 or len(baseline_c3) != 16:
        raise RuntimeError("baseline changed: expected the original 16 C0/C3 records")

    c0["schema_version"] = "0.2.0"
    c0["corpus_version"] = "c3-dev-2026-08-08"
    c0["snapshot_date"] = "2026-08-08"
    c0["retrieved_at"] = RETRIEVED_AT
    c0["coverage_claim"] = "可审计开发样本：12 个故事族、每族 2 个版本，共 24 条；含独立寓言类型，但不声称穷尽中国古典神话、传说、志怪、寓言或故事材料。"
    for repo in c0["source_repositories"]:
        if repo.get("repository_id") == "zhwikisource":
            repo["status"] = "used_for_24_short_excerpts"
    c0["story_families"] = [
        family
        for family in c0["story_families"]
        if family["story_family_id"] not in new_family_ids
    ] + FAMILIES
    c0["source_items"] = baseline_c0 + [make_c0(config) for config in CONFIGS]

    c3["schema_version"] = "0.2.0"
    c3["corpus_version"] = "c3-dev-2026-08-08"
    c3["snapshot_date"] = "2026-08-08"
    c3["coverage_claim"] = "12 个故事族、24 个具体版本的开发样本，新增女娲补天、精卫填海、愚公移山与狐假虎威，并纳入独立寓言类型；不是穷尽性中国古典故事语料库。"
    c3["story_versions"] = baseline_c3 + [make_c3(config) for config in CONFIGS]

    if len(c0["story_families"]) != 12:
        raise RuntimeError("expected 12 story families")
    if len(c0["source_items"]) != 24 or len(c3["story_versions"]) != 24:
        raise RuntimeError("expected 24 aligned C0/C3 records")

    write_json(C0_PATH, c0)
    write_json(C3_PATH, c3)


if __name__ == "__main__":
    main()
