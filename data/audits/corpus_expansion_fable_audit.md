# 寓言与历史故事详标扩库审计（2026-08-09）

> 分层说明：本文的“12 个固定版本记录”是来源采集历史口径。当前每族仅 1 个主文本留在 C3，另 1 条已隔离到 C1-secondary 后台见证层；版本见证数不得冒充产品故事数。

## 结论

- 新增 6 个故事家族、12 个固定版本记录；每族 2 个版本。
- 家族均使用大众熟悉标题：塞翁失马、守株待兔、刻舟求剑、叶公好龙、伯牙绝弦、南柯一梦。
- 12 条记录均为 `c3-dev` / `editorial_draft`，`development_runtime_eligible=true`、`production_eligible=false`。
- 12 个摘录均在所列 `oldid` 对应的 MediaWiki raw wikitext 中逐字命中；长度为 15–48 个 Unicode 字符，未超过 100 字上限。
- C0 与 C3 的版本 ID、摘录、revision ID、timestamp 和 UTF-8 SHA-256 已离线逐条比对，无差异。

本批次是可运行开发样本，不是生产定本，也不代表寓言、历史故事或中国古典叙事的穷尽性覆盖。

## 核验方法

2026-08-09 通过中文维基文库 MediaWiki API 的 revision 查询一次性读取 12 个固定 `revid`：

```text
action=query
prop=revisions
rvprop=ids|timestamp|content
rvslots=main
revids=<12 个固定 revision ID>
format=json
formatversion=2
```

逐条执行以下检查：

1. API 返回的 `revid` 与记录中的 `page_revision_id` 相等；
2. API 返回的 UTC timestamp 与 `page_revision_timestamp` 相等；
3. `excerpt` 是该固定 revision raw wikitext 的连续子串；
4. `SHA-256(UTF-8(excerpt))` 同时等于 C0 和 C3 中的 `excerpt_sha256`；
5. 每个摘录少于 100 个 Unicode 字符；
6. 每个家族恰有 2 个版本，12 个 `story_version_id` 均唯一；
7. C3 必备顶层与嵌套字段与现有 `c3_story_versions.json` 记录结构一致。

## 固定版本清单

| 家族 | version ID | 固定页面 | oldid | timestamp (UTC) | 字符数 | 关系边界 |
|---|---|---|---:|---|---:|---|
| 塞翁失马 | `saiweng_huainanzi_renjian` | [《淮南子·人间训》](https://zh.wikisource.org/w/index.php?title=淮南子/人間訓&oldid=1809124) | 1809124 | 2020-05-14T05:47:29Z | 37 | 西汉传世核心见证 |
| 塞翁失马 | `saiweng_tianzhongji_55_quote` | [《天中记》卷五十五](https://zh.wikisource.org/w/index.php?title=天中記_(四庫全書本)/卷55&oldid=773900) | 773900 | 2016-10-25T22:53:35Z | 29 | 明代类书所引《淮南子》材料，不是独立起源文本 |
| 守株待兔 | `shouzhu_hanfeizi_wudu` | [《韩非子·五蠹》](https://zh.wikisource.org/w/index.php?title=韓非子/五蠹&oldid=2642850) | 2642850 | 2026-01-19T01:06:47Z | 48 | 标点转录的传世文本 |
| 守株待兔 | `shouzhu_hanfeizi_siku_19` | [《韩非子》四库本卷十九](https://zh.wikisource.org/w/index.php?title=韓非子_(四庫全書本)/卷19&oldid=629723) | 629723 | 2016-10-14T03:40:13Z | 40 | 同书异版见证，不是第二个独立故事 |
| 刻舟求剑 | `kezhou_lushichunqiu_chajin` | [《吕氏春秋》卷十五《察今》](https://zh.wikisource.org/w/index.php?title=呂氏春秋/卷十五&oldid=2327722) | 2327722 | 2023-10-29T10:07:50Z | 46 | 战国末期论辩寓言传世见证 |
| 刻舟求剑 | `kezhou_taipingyulan_0769_quote` | [《太平御览》卷七百六十九](https://zh.wikisource.org/w/index.php?title=太平御覽/0769&oldid=2588097) | 2588097 | 2025-08-05T14:21:57Z | 42 | 北宋类书引《吕氏春秋》，不是原创改写 |
| 叶公好龙 | `yegong_xinxu_zashi_5` | [《新序·杂事》第五](https://zh.wikisource.org/w/index.php?title=新序/雜事/卷五&oldid=2178815) | 2178815 | 2022-09-13T08:49:54Z | 43 | 子张谏鲁哀公语境中的完整譬喻 |
| 叶公好龙 | `yegong_taipingyulan_0750_quote` | [《太平御览》卷七百五十](https://zh.wikisource.org/w/index.php?title=太平御覽/0750&oldid=2588077) | 2588077 | 2025-08-05T14:18:47Z | 45 | 画部引《新序》的缩写，省略政治语境 |
| 伯牙绝弦 | `boya_lushichunqiu_benwei` | [《吕氏春秋》卷十四《本味》](https://zh.wikisource.org/w/index.php?title=呂氏春秋/卷十四&oldid=2327725) | 2327725 | 2023-10-29T10:07:59Z | 30 | 战国末期伯牙、钟子期见证 |
| 伯牙绝弦 | `boya_shiwaizhuan_siku_09` | [《诗外传》四库本卷九](https://zh.wikisource.org/w/index.php?title=詩外傳_(四庫全書本)/卷09&oldid=538728) | 538728 | 2016-10-03T14:27:58Z | 15 | 西汉相近见证；与《吕氏春秋》的文本关系待复核 |
| 南柯一梦 | `nanke_taishouzhuan` | [李公佐《南柯太守传》](https://zh.wikisource.org/w/index.php?title=南柯太守傳&oldid=2264164) | 2264164 | 2023-02-24T14:37:33Z | 42 | 唐传奇文本 |
| 南柯一梦 | `nankeji_act42_xingwu` | [汤显祖《南柯记》第四十二出](https://zh.wikisource.org/w/index.php?title=南柯記/42&oldid=2288310) | 2288310 | 2023-05-12T18:09:43Z | 28 | 明代戏曲改编，不是唐传奇异版 |

## 摘录 SHA-256

| version ID | SHA-256 (UTF-8) |
|---|---|
| `saiweng_huainanzi_renjian` | `7356820ed14ac254181dc1623739c92308ed678fb5f696eafd211ef2ef642471` |
| `saiweng_tianzhongji_55_quote` | `3645a8855f02fd74e27d9b800937fb299993e1532270b0d0104d31a154d45072` |
| `shouzhu_hanfeizi_wudu` | `0131b06da90e24baa2327a214c0be3e0215d5f350be3ed92c7a89d74e6ad5cf8` |
| `shouzhu_hanfeizi_siku_19` | `b1af4791ef547e586daf0410a923f26e9dcae78402b9221d62200cd18d51bb1b` |
| `kezhou_lushichunqiu_chajin` | `21e19bc032e3da249b60f8aac49f1e73a1ac1f1d9fbcea7f26abd7cdba3da695` |
| `kezhou_taipingyulan_0769_quote` | `05b2c5372b2327bad588c54c44095c5bddfabb7e6a6f3ca4156f7446f9b567ff` |
| `yegong_xinxu_zashi_5` | `cc5ae19944c6d914cd20763f2b385772d42b5a2088715cea1ef956e1e84fb5b7` |
| `yegong_taipingyulan_0750_quote` | `b2aff679c4ff8862861f842d2704537552a7105e58db6ff349b5c5874a70edba` |
| `boya_lushichunqiu_benwei` | `10a34c6aa6a716cfc98d5bfe0755ad0886829ef8992fce996083504309ef3416` |
| `boya_shiwaizhuan_siku_09` | `afa1a8fcc6a6d52838113862151b1c3d6f91c6234a6a500d17e45b660767e61c` |
| `nanke_taishouzhuan` | `cdda608b7da43ca528dd6614436aff382cf04e87f5f76e12a072d4550bafba11` |
| `nankeji_act42_xingwu` | `7c9364768cd8c2b6f0e8dfc1362ca06fbc2a9ab73cfe31a5aef363802bc50613` |

## 不能越过的来源边界

- 《天中记》塞翁条、两条《太平御览》记录都是类书引文见证，不能称为独立起源故事。
- 《韩非子》四库本是同一作品的另一版面/字形见证，不能拿来虚增“故事数量”。
- 《诗外传》四库本的“绝弦”位置仍有 `SKchar` 未解析字形模板；本批摘录刻意选取模板后的连续原文，完整句仍需对照扫描。
- 《南柯记》是汤显祖对唐传奇的戏曲改编，宗教结构和结局不能与《南柯太守传》自动合并。
- 所有现代概要、推荐理由、共鸣提示、改编边界与剧场方案均为项目编辑草稿，不是原典译文、原典原意或学术定本。
- 古代基础文本与网页转录、扫描、现代标点、图片、音频、数据库权利是不同维度；本批仅允许开发环境中的署名短摘录与来源链接。

## 生产前缺口

1. 逐条对照可靠影印本或学术整理本；
2. 复核《天中记》条末来源标签与未解析字形；
3. 复核《韩非子》不同传本、《诗外传》与《吕氏春秋》之间的文本关系；
4. 完成权利评估、双人文化复核与安全映射审查；
5. 对丧失、战争、伤残、酗酒和虚实错位等内容建立运行时可跳过机制；
6. 在上述工作完成前保持 `production_eligible=false`。
