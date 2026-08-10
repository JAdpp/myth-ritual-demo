# 神话批次详标扩库审计（2026-08-09）

> 分层说明：本文的“12 个 C3-dev 版本”是批次进入选择流程前的历史建库口径。当前每族仅 1 个主文本留在 C3，另 1 条已隔离到 C1-secondary 后台见证层；批次版本数不得解释为用户可选故事数。

## 结论

- 新增 **6 个故事家族、12 个 C3-dev 版本**，每族恰好 2 个固定修订见证。
- 六个预定家族全部保留，无替换：**后羿射日、女娲造人、神农尝百草、共工触不周山、刑天舞干戚、吴刚伐桂**。
- 12/12 条均完成固定 `oldid`、修订时间戳、原始 wikitext 可定位性、UTF-8 SHA-256 与 C0/C3 对齐核验。
- 所有记录均为 `production_eligible=false`、`development_runtime_eligible=true`；这是一批可进入本地 Demo 的编辑草稿，不是已经完成版本学、版权和双人文化复核的生产语料。

批次文件：[`data/expansion_batches/myth_batch.json`](../expansion_batches/myth_batch.json)

## 方法与边界

1. 先按现有 `c0_source_manifest.json`、`c3_story_versions.json` 与 `validate_corpus.py` 的字段和门槛建模。
2. 来源限定为中文维基文库古籍页面的修订级固定链接；通过 MediaWiki REST `revision/{oldid}` 读取该修订的 `timestamp` 与 `source`，未以当前可变页面替代固定修订。
3. 每条只保存不足 100 个 Unicode 字符的证据摘录；没有抓取或落盘整部古籍。
4. 摘录默认必须是 raw wikitext 的连续字串；只有两条刑天记录做了明确、可重放的模板规范化，见“规范化例外”。
5. 版本计数是“可审计见证数”，不是“独立起源数”。女娲两条是同一佚文的两种后世传抄，吴刚两条是同一作品的不同版本转录，刑天第二条是类书引文见证。

## 固定修订清单

| 故事家族 | 版本 ID | 古籍载体 | oldid / timestamp | 摘录 SHA-256 |
|---|---|---|---|---|
| 后羿射日 | `houyi_huainanzi_benjing` | [《淮南子·本经训》](https://zh.wikisource.org/w/index.php?title=淮南子/本經訓&oldid=2606194) | `2606194` / `2025-10-14T00:25:26Z` | `a35680f2a18a3edb63cf9ef60317e1077d24db6cbeab0bae9ab0d9f0828436d8` |
| 后羿射日 | `houyi_chuci_tianwen_commentary` | [《楚辞章句》卷三](https://zh.wikisource.org/w/index.php?title=楚辭章句/卷03&oldid=2316447) | `2316447` / `2023-10-01T23:24:34Z` | `31e8f7d9e402ef25a6c16fc6940cffbeb88a813fa528f6a268749da0fa672ce1` |
| 女娲造人 | `nvwa_taipingyulan_0078_fengsutong` | [《太平御览》卷七十八引《风俗通》](https://zh.wikisource.org/w/index.php?title=太平御覽/0078&oldid=2587331) | `2587331` / `2025-08-05T09:39:50Z` | `d8597eab983a398bdbdae34f64adc82d55891e2e0baac9908067925efa640b02` |
| 女娲造人 | `nvwa_quanhouhanwen_fengsutong` | [《全后汉文》卷三十六辑应劭文](https://zh.wikisource.org/w/index.php?title=全後漢文/卷三十六&oldid=2428941) | `2428941` / `2024-06-28T04:07:49Z` | `fb8961379902d018bebea2a2d4848160bc61caeb225057e68d717376688b9133` |
| 神农尝百草 | `shennong_huainanzi_xiuwu` | [《淮南子·脩务训》](https://zh.wikisource.org/w/index.php?title=淮南子/脩務訓&oldid=7904476) | `7904476` / `2026-06-28T01:30:55Z` | `64330449ad908285bed277e7fcc6b433711761c89d0a23e5f084bf8756f56b7d` |
| 神农尝百草 | `shennong_soushenji_01` | [《搜神记》第一卷](https://zh.wikisource.org/w/index.php?title=搜神記/第01卷&oldid=2411948) | `2411948` / `2024-05-23T10:28:29Z` | `1ff2ba42042afb6996aba3095ced2444f3c2e2e1d58a9dcd1177ca2c1f402de7` |
| 共工触不周山 | `gonggong_huainanzi_tianwen` | [《淮南子·天文训》](https://zh.wikisource.org/w/index.php?title=淮南子/天文訓&oldid=7909148) | `7909148` / `2026-07-21T03:24:52Z` | `5e6248c63d88de84be28478fd1a8706f851be89ba03307825eb58e219481953b` |
| 共工触不周山 | `gonggong_liezi_tangwen` | [《列子·汤问篇》](https://zh.wikisource.org/w/index.php?title=列子/湯問篇&oldid=2608932) | `2608932` / `2025-10-22T14:10:39Z` | `d115ec3bbce1bb1a3cff415036eee6f1f4ec949ddf39fa5aadff29540aa84a8d` |
| 刑天舞干戚 | `xingtian_shanhaijing_haiwaixi` | [《山海经·海外西经》](https://zh.wikisource.org/w/index.php?title=山海經/海外西經&oldid=427605) | `427605` / `2015-01-24T10:49:38Z` | `5abee91dcdf421889a3d0ed29861e671e36ced885321102247c058a0a036ef49` |
| 刑天舞干戚 | `xingtian_taipingyulan_0887` | [《太平御览》卷八百八十七](https://zh.wikisource.org/w/index.php?title=太平御覽/0887&oldid=2588215) | `2588215` / `2025-08-05T14:40:47Z` | `c3c2d8a27d20554c39b89495dbc4386b9d232f8428424cfebbfbf1a6d2932025` |
| 吴刚伐桂 | `wugang_youyangzazu_juan1` | [《酉阳杂俎》卷一](https://zh.wikisource.org/w/index.php?title=酉陽雜俎/卷一&oldid=2086696) | `2086696` / `2021-11-10T02:01:03Z` | `980d5689bc66881f3ce1d92471e1592bb432b24e408e5210087b936ed06cdde9` |
| 吴刚伐桂 | `wugang_youyangzazu_siku_juan1` | [《酉阳杂爼（四库全书本）》卷一](https://zh.wikisource.org/w/index.php?title=酉陽雜爼_(四庫全書本)/卷01&oldid=793713) | `793713` / `2016-10-28T05:14:57Z` | `5695ac0d8edc4cb427978ce71317f84beb62c40d0ba7af48d885e27c26c949aa` |

## 规范化例外

- `xingtian_shanhaijing_haiwaixi`：raw wikitext 为 `{{另|刑|形}}天與帝{{另|爭神|至此爭神}}`。摘录取页面模板的首选读法“刑天与帝争神”，未删除异文事实；C3 `source_canon.excerpt_normalization` 和 `review_record.known_gaps` 均保留此说明。
- `xingtian_taipingyulan_0887`：raw wikitext 的“干”为语言转换标记 `-{干}-`。摘录只移除标记，保留页面转录的“齐为口”，没有静默校成“脐”。
- `houyi_chuci_tianwen_commentary`：没有拼接模板内外文本；仅摘取章句标题模板之后的一段连续注文字串。
- `nvwa_quanhouhanwen_fengsutong`：摘录在行内异文模板之前停止，没有跨模板补字。
- `shennong_soushenji_01`：只去除段首两个全角空格，正文连续字串不改。

## 内容与文化风险标记

- 后羿：旱灾、神话性射杀和动物伤害；禁止把暴力方案迁移为现实处世建议。
- 女娲：来源后文用泥土与绳泥附会富贵贫贱。摘录没有纳入该句，但记录明确提示上下文；适配层禁止将阶序或肤色自然化。
- 神农：中毒与不可模仿的尝试；适配层禁止生成采食、剂量、偏方或诊疗建议。
- 共工：愤怒、撞击与灾变；禁止浪漫化自毁或把神话宇宙论说成现代科学。
- 刑天：斩首、身体变形与兵器；视觉层禁止血腥猎奇和“残障必须励志”的刻板叙事。
- 吴刚：无尽惩罚与重复劳动；不擅造吴刚的具体罪名，也不把过劳说成修行必经。

## 自检结果

本批次已运行以下等价检查：

- C0 family/source 与 C3 ID 集合：`6 / 12 / 12`，每族 2 版，无重复 ID。
- C3 顶层及 `source_canon`、`story_card`、`adaptation_boundary`、`theatre_asset_pack`、`rights_and_access`、`review_record` 子结构与现有 C3 样本键集合一致：`12/12 PASS`。
- 每条摘录长度 `< 100`，范围为 28–67 个 Unicode 字符。
- 本地 UTF-8 SHA-256：`12/12 PASS`。
- C0/C3 摘录、哈希、URL、oldid、revision timestamp：`12/12 PASS`。
- MediaWiki REST 固定修订远程核验：`12/12 PASS`。
- raw wikitext 直接定位：`10/12`；按上述显式模板规则规范化后：`12/12 PASS`。

远程检查的最终输出摘要：

```json
{
  "status": "PASS",
  "families": 6,
  "versions": 12,
  "hashes_verified": 12,
  "remote_revisions_verified": 12,
  "oldid_timestamp_aligned": 12,
  "raw_direct_matches": 10,
  "documented_normalization_matches": 2,
  "production_eligible_versions": 0
}
```

## 仍待完成

- 逐条对照可靠影印本或扫描页，而不只依赖社区转录。
- 核对《风俗通》佚文的引录谱系、今本《列子》的文本层次，以及《太平御览》刑天条“齐/脐”的底本或 OCR 来源。
- 完成权利裁决和双人文化复核后，才可讨论生产发布。
- 本批次只增加六个高辨识度神话家族，不声称穷尽中国古代神话材料。
