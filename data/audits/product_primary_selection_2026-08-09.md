# 产品主文本选择审计（2026-08-09）

## 结论

- 30 个详标故事家族已各选定 **1 个产品主文本**，共 30 条，现已单独写入 C3；推荐、讲解、映射和剧场只能使用这 30 条主文本。
- 每族另保留 1 条 **后台第二见证**，共 30 条，现已隔离到 `c1_secondary_source_witnesses.json`。第二见证仅用于来源校验、文化审计和防止模型混写，**不向用户提供版本选择**。
- 选择状态统一为 `demo_editorial_selection_pending_expert_review`。这里的“主文本”是演示阶段的产品编辑选择，**不声称是学界公认的最权威定本**。
- 选择顺序是：直接原典优先于类书引文或同书转录；兼顾年代与文本完整性；能够承载大众熟悉的故事核心；条件相近时优先非成人条目；固定修订和短摘录必须可核验。
- 30 条主文本中 27 条为非成人条目；干将莫邪、孟姜女哭长城、梁山伯与祝英台 3 条因现有材料边界保留成人门控，不得进入默认未确认推荐。

机器可读选择账本：[`data/corpus/c2_primary_story_selection.json`](../corpus/c2_primary_story_selection.json)

## 逐族选择

| 故事 | 产品主文本 | 后台第二见证 | 选择理由与例外 |
|---|---|---|---|
| 盘古开天 | `pangu_yiwenleiju_sanwuliji_quote` | `pangu_shuyiji_body_cosmos` | 所引佚文较早且承载混沌、天地分判和盘古增长的开天核心；例外：现存载体是《艺文类聚》类书引文。 |
| 夸父逐日 | `kuafu_shanhaijing_haiwaibei` | `kuafu_shanhaijing_dahuangbei` | 同书异篇中本条更完整，保留逐日、焦渴、道死和弃杖化林。 |
| 大禹治水 | `gunyu_wuyuechunqiu_wuyu` | `gunyu_shanhaijing_hainei` | 例外：虽较晚，但能完整承载禹接续治水、长期奔走和过门不入的通行叙事。 |
| 嫦娥奔月 | `change_huainanzi_lanming` | `change_lingxian` | 西汉见证较早，直接保存羿求不死药和姮娥取药奔月的核心。 |
| 花木兰从军 | `mulan_ballad` | `mulan_qinv_ch13` | 《木兰诗》较早且完整，直接承载代父从军、征战和辞官返乡。 |
| 黄粱一梦 | `yellow_millet_zhenzhongji` | `yellow_millet_handanji_act04` | 唐传奇《枕中记》早于明代戏曲，并保留完整梦中一生结构。 |
| 白蛇传 | `white_snake_jingshitongyan_28` | `white_snake_leifengta_qizhuan_ch01` | 明代话本早于清代重编，人物与雷峰塔叙事结构完整。 |
| 干将莫邪 | `ganjiang_moye_wuyuechunqiu` | `ganjiang_moye_soushenji_11` | 东汉文本较早，聚焦干将、莫耶和雌雄双剑；例外：现有两条见证均为成人内容。 |
| 女娲补天 | `nvwa_huainanzi_lanming` | `nvwa_liezi_tangwen` | 西汉见证较早，灾变、补天和止水结构更完整。 |
| 精卫填海 | `jingwei_shanhaijing_beishan` | `jingwei_shuyiji_expansion` | 《山海经》较早且直接包含女娃溺海、化鸟和衔木石填海。 |
| 愚公移山 | `yugong_liezi_tangwen` | `yugong_taipingyulan_0040_quote` | 传世《列子》叙事完整，优于类书节引。 |
| 狐假虎威 | `fox_tiger_zhanguoce_chu1` | `fox_tiger_taipingyulan_0909_chunqiu_houyu` | 《战国策》是直接文本，并保留权势来源的政治说辞语境。 |
| 后羿射日 | `houyi_huainanzi_benjing` | `houyi_chuci_tianwen_commentary` | 《淮南子》是较早直接叙事见证，明确保存十日并出、射日和除害。 |
| 女娲造人 | `nvwa_taipingyulan_0078_fengsutong` | `nvwa_quanhouhanwen_fengsutong` | 两条均为佚文见证；《太平御览》载体较早且摘录更完整。 |
| 神农尝百草 | `shennong_huainanzi_xiuwu` | `shennong_soushenji_01` | 西汉见证较早，直接写播种、辨土地、尝百草和遭毒。 |
| 共工触不周山 | `gonggong_huainanzi_tianwen` | `gonggong_liezi_tangwen` | 西汉见证较早，完整连接争帝、触山和天地倾斜。 |
| 刑天舞干戚 | `xingtian_shanhaijing_haiwaixi` | `xingtian_taipingyulan_0887` | 《山海经》是较早直接见证，优于类书引文。 |
| 吴刚伐桂 | `wugang_youyangzazu_juan1` | `wugang_youyangzazu_siku_juan1` | 唐代《酉阳杂俎》通行转录直接承载完整核心；四库本只作同书核验。 |
| 牛郎织女 | `cowherd_jingchu_suishiji_qixi` | `cowherd_shuolue_qixi` | 南朝岁时记见证较早，确认七夕相会核心；例外：不是现代家庭叙事的完整底本。 |
| 孟姜女哭长城 | `mengjiangnu_lienvzhuan_04` | `mengjiangnu_taipingyulan_0561` | 《列女传》直接且较早，包含哭城崩核心；例外：成人门控，人物仍称杞梁妻且不是秦长城。 |
| 梁山伯与祝英台 | `butterfly_qingshi_leilue_10` | `butterfly_simingzhi_13` | 例外：明代文本较晚且需成人门控，但比地方志短记更完整；化蝶仍只标后世俗传。 |
| 田螺姑娘 | `snail_maiden_soushenhouji_05` | `snail_maiden_zengbu_soushenji_06` | 六朝见证较早且完整，保留拾螺、备饭、窥见和素女离去。 |
| 桃花源 | `peach_blossom_taohuayuanji` | `peach_blossom_siku_taoyuanming_05` | 单篇主文直接呈现陶潜故事；四库本只作同书传本核验。 |
| 柳毅传书 | `liuyi_original_tale` | `liuyi_taipingguangji_419` | 唐传奇单篇是直接完整文本，优于北宋类书所录见证。 |
| 塞翁失马 | `saiweng_huainanzi_renjian` | `saiweng_tianzhongji_55_quote` | 《淮南子》是较早直接核心文本，完整呈现祸福转换。 |
| 守株待兔 | `shouzhu_hanfeizi_wudu` | `shouzhu_hanfeizi_siku_19` | 《韩非子·五蠹》是直接文本；四库本只是同书异版。 |
| 刻舟求剑 | `kezhou_lushichunqiu_chajin` | `kezhou_taipingyulan_0769_quote` | 《吕氏春秋·察今》直接且完整，优于类书节引。 |
| 叶公好龙 | `yegong_xinxu_zashi_5` | `yegong_taipingyulan_0750_quote` | 《新序》直接且更完整，保留子张讽谏语境。 |
| 伯牙绝弦 | `boya_lushichunqiu_benwei` | `boya_shiwaizhuan_siku_09` | 《吕氏春秋》较早且结构更完整，保存高山流水、子期去世和绝弦。 |
| 南柯一梦 | `nanke_taishouzhuan` | `nankeji_act42_xingwu` | 唐传奇直接且完整，早于汤显祖戏曲改编。 |

## 产品层规则

1. C3 只保存 `primary_version_id` 对应的每族唯一故事；后台见证没有 Demo、运行或默认推荐资格。
2. 推荐卡可显示“本故事采用的主文本”和来源链接，但不要求用户理解或选择异本。
3. AI 只能在该主文本的 `must_preserve / may_transform / prohibited` 边界内讲解和生成映射；不得从后台第二见证偷渡情节。
4. 第二见证只用于后台检查“是否混写、是否误称原文、固定修订是否仍可追溯”。
5. 在两名文化审校者和书目复核完成前，界面不得使用“最权威版本”“学术定本”或“唯一正确版本”等表述。

## 仍待复核

- 逐条对照可靠影印本或学术整理本，而不只依赖社区转录。
- 专家复核 7 个显式例外，尤其是盘古、女娲造人的佚文载体，大禹和梁祝的后出完整叙事选择，以及牛郎织女的短母题见证。
- 对干将莫邪、孟姜女、梁祝完成成人内容与自伤叙事的产品安全审查。
- 复核完成后才能把 `selection_status` 改为专家已确认；不自动提升任何记录为 `production_eligible=true`。

## 本地结构校验

```json
{
  "status": "PASS",
  "selections": 30,
  "unique_families": 30,
  "unique_primaries": 30,
  "unique_secondary_witnesses": 30,
  "primary_secondary_overlap": 0,
  "c3_product_primary_stories": 30,
  "c1_secondary_source_witnesses": 30,
  "union_matches_c0_source_witnesses": 60,
  "family_alignment_errors": 0,
  "adult_flag_alignment_errors": 0,
  "adult_primaries": 3,
  "documented_exceptions": 7,
  "validate_corpus": "PASS",
  "validate_story_catalog": "PASS"
}
```
