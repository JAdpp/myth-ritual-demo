# 传说/志怪详标扩库审计（2026-08-09）

> 分层说明：本文的“12 个固定古籍版本”是来源采集历史口径。当前每族仅 1 个主文本留在 C3，另 1 条已隔离到 C1-secondary 后台见证层；用户不选择异本。

## 结论

- 本批次新增 **6 个故事家族、12 个固定古籍版本**，每族恰好 2 个版本。
- 12 条短摘录都以 UTF-8 计算 SHA-256；本地复算与 C0、C3 中的散列完全一致。
- 12 条摘录均是相应固定修订 `raw wikitext` 中的**连续原样子串**，没有在摘录内部展开模板、添加标点或做简繁转换。
- 全部记录均为 `c3-dev`、`editorial_draft`、`production_eligible=false`、`development_runtime_eligible=true`。
- 仅 `mengjiangnu_lienvzhuan_04`（赴水）与 `butterfly_qingshi_leilue_10`（投墓）2 个明确包含自尽情节的版本设为 `adult_only=true`、`default_offer_eligible=false`，必须显式成人选择；其余 10 条可进入默认开发推荐，但仍保留死亡、哀伤或家庭暴力警示。
- 两个成人版本的 `rights_and_access.access_mode` 均为 `short_excerpt_adult_gated`；`allowed_uses` 只使用成人门运行权限 `local_development_runtime_with_adult_opt_in`，没有同时保留普通 `local_development_runtime`。非成人版本继续使用 `short_excerpt` 与普通开发运行权限。
- 这仍是开发详标集，不是学术定本，也不是“中华故事穷尽集”。扫描核对、书目复核、权利复核和双人文化复核尚未完成。

## 方法

2026-08-09T01:03:51+08:00 使用中文维基文库 MediaWiki API 复核页面固定修订。批次 12 条 `source_canon.retrieved_at` 均记录这一实际核验时刻，不使用晚于系统当前时间的占位值：

```text
action=query
prop=revisions
rvprop=ids|timestamp|content
rvslots=main
format=json
formatversion=2
redirects=1
```

请求使用可识别的学术原型 User-Agent，并一次批量查询多个标题以避免高频请求。对每条记录执行：

1. 记录 `page_revision_id` 与 UTC `page_revision_timestamp`；
2. 建立带数字 `oldid` 的固定链接；
3. 在该修订 `raw wikitext` 中执行大小写敏感的连续子串匹配；
4. 对匹配摘录的 UTF-8 字节计算 SHA-256；
5. 检查 C0/C3 摘录、散列、版本号、时间戳和 URL 一致；
6. 按故事家族检查每族两个版本，并人工区分“早期母题、异本、类书引文、后世扩写”。

## 固定版本清单

| 家族 | 版本 ID | 古籍见证 | oldid | UTC timestamp | 摘录长度 | SHA-256 前 12 位 | raw 命中 |
|---|---|---|---:|---|---:|---|---|
| 牛郎织女 | `cowherd_jingchu_suishiji_qixi` | [《荆楚岁时记》四库本](https://zh.wikisource.org/w/index.php?title=荆楚嵗時記_(四庫全書本)&oldid=574658) | 574658 | 2016-10-05T05:22:36Z | 13 | `425e095480c0` | 是 |
| 牛郎织女 | `cowherd_shuolue_qixi` | [《说略》卷四](https://zh.wikisource.org/w/index.php?title=說畧_(四庫全書本)/卷04&oldid=792294) | 792294 | 2016-10-28T01:25:16Z | 58 | `577ea767b3ba` | 是 |
| 孟姜女哭长城 | `mengjiangnu_taipingyulan_0561` | [《太平御览》卷五百六十一](https://zh.wikisource.org/w/index.php?title=太平御覽/0561&oldid=2587868) | 2587868 | 2025-08-05T13:06:47Z | 33 | `08a14711d458` | 是 |
| 孟姜女哭长城 | `mengjiangnu_lienvzhuan_04` | [《列女传》卷四](https://zh.wikisource.org/w/index.php?title=列女傳/卷4&oldid=2195360) | 2195360 | 2022-11-20T14:54:00Z | 59 | `eacb6e43846a` | 是 |
| 梁山伯与祝英台 | `butterfly_simingzhi_13` | [《宝庆四明志》卷十三](https://zh.wikisource.org/w/index.php?title=寶慶四明志_(四庫全書本)/卷13&oldid=757790) | 757790 | 2016-10-24T01:22:49Z | 48 | `6ee126ce0758` | 是 |
| 梁山伯与祝英台 | `butterfly_qingshi_leilue_10` | [《情史类略》卷十](https://zh.wikisource.org/w/index.php?title=情史類略/10&oldid=823518) | 823518 | 2017-02-09T06:15:20Z | 46 | `85c0c22a587c` | 是 |
| 田螺姑娘 | `snail_maiden_soushenhouji_05` | [《搜神后记》卷五](https://zh.wikisource.org/w/index.php?title=搜神後記/05&oldid=815501) | 815501 | 2017-01-10T05:25:49Z | 33 | `56af6d81098d` | 是 |
| 田螺姑娘 | `snail_maiden_zengbu_soushenji_06` | [增补《搜神记》卷之六](https://zh.wikisource.org/w/index.php?title=新刻出像增補搜神記/卷之六&oldid=2548402) | 2548402 | 2025-04-06T05:27:51Z | 44 | `314fae484d24` | 是 |
| 桃花源 | `peach_blossom_taohuayuanji` | [《桃花源记》](https://zh.wikisource.org/w/index.php?title=桃花源記&oldid=2620894) | 2620894 | 2025-11-21T04:04:19Z | 39 | `46031490c5c7` | 是 |
| 桃花源 | `peach_blossom_siku_taoyuanming_05` | [《陶渊明集》四库本卷五](https://zh.wikisource.org/w/index.php?title=陶淵明集_(四庫全書本)/卷5&oldid=716630) | 716630 | 2016-10-16T07:51:11Z | 42 | `651d2743f776` | 是 |
| 柳毅传书 | `liuyi_original_tale` | [《柳毅传》](https://zh.wikisource.org/w/index.php?title=柳毅傳&oldid=2118835) | 2118835 | 2022-03-30T21:47:08Z | 36 | `7b0da508aea7` | 是 |
| 柳毅传书 | `liuyi_taipingguangji_419` | [《太平广记》卷四百一十九](https://zh.wikisource.org/w/index.php?title=太平廣記/卷第419&oldid=812419) | 812419 | 2016-12-30T09:01:38Z | 35 | `2f6a86370e0a` | 是 |

## 传承边界与例外

### 孟姜女不是直接从早期古籍中读出的姓名

本批次以大众标题“孟姜女哭长城”提供检索入口，但两个详标文本实际是“杞梁妻”传统：

- 《太平御览》见证聚焦迎柩与拒绝不合礼的郊吊，没有哭城崩；
- 《列女传》出现哭城崩、无所归与赴淄水，但没有“孟姜女”姓名，也没有秦始皇、秦长城或千里寻夫。

因此推荐卡和改编边界明确禁止把后世通行情节冒充早期原文。

### 梁祝的化蝶不是最早核心

- 《宝庆四明志》只简记同学三年、山伯不知英台为女、同葬和墓庙；
- 《情史类略》扩展婚约、病死和投墓，并把二蝶说明另标为“俗传”“好事者”。

所以剧场若出现蝶影，必须标为后世传播层，不能作为南宋地方志原文图解。

### 桃花源是同一作品的异本，不是两个独立起源

《桃花源记》带异文标记页面和《陶渊明集》四库本用于异本对照。二者均属陶渊明同一作品的传承见证。数量统计可以算两个 `story_version`，但不能声称是两个独立故事。

### 为什么用“柳毅传书”替代“画皮”

本轮检索确认《聊斋志异》“画皮”在中文维基文库有可用正文页，但没有找到第二个可与之区分、且能按同样规则冻结为独立古籍见证的公开转录。把同一页面的两个历史 oldid 当作两个“古籍版本”会制造虚假的版本多样性。因此本批次改用：

- 独立唐传奇《柳毅传》；
- 北宋《太平广记》卷四百一十九所录《柳毅》。

两者都有独立固定页面，可诚实标作单篇见证与类书传承见证。`family_id` 使用目录既有值 `liu_yi_delivers_letter`。

## 安全与默认推荐

成人门按**版本内容**而不是整族标题设置，避免较早的丧礼或墓志见证因后世扩写而被一并隐藏：

| 版本 | 成人门 | 默认推荐 | access mode | 本地运行权限 | 主要警示 |
|---|---|---|---|---|---|
| 牛郎织女两版 | 否 | 是 | `short_excerpt` | `local_development_runtime` | 长期分离、婚姻债务异说、性别化节俗 |
| `mengjiangnu_taipingyulan_0561` | 否 | 是 | `short_excerpt` | `local_development_runtime` | 战死、丧亲、灵柩与吊丧、父权礼制 |
| `mengjiangnu_lienvzhuan_04` | 是 | 否 | `short_excerpt_adult_gated` | `local_development_runtime_with_adult_opt_in` | 尸体、哭城崩、投水自尽、贞节规训 |
| `butterfly_simingzhi_13` | 否 | 是 | `short_excerpt` | `local_development_runtime` | 死亡与合葬、性别身份隐匿、历史礼制 |
| `butterfly_qingshi_leilue_10` | 是 | 否 | `short_excerpt_adult_gated` | `local_development_runtime_with_adult_opt_in` | 病死、投墓自尽、包办婚约、殉情浪漫化风险 |
| 田螺姑娘两版 | 否 | 是 | `short_excerpt` | `local_development_runtime` | 隐私窥视、性别化家务、孤儿处境 |
| 桃花源两版 | 否 | 是 | `short_excerpt` | `local_development_runtime` | 战争避难、流离、隐秘空间被暴露 |
| 柳毅传书两版 | 否 | 是 | `short_excerpt` | `local_development_runtime` | 婚姻虐待、家庭排斥、暴力救援与复仇 |

成人门并不表示其他版本“无风险”；它只是针对明确自尽情节的开发运行门控。《太平御览》的杞梁妻和《宝庆四明志》的梁祝短记恢复默认推荐后，仍必须在卡片中显示死亡与哀伤警示。柳毅传书同样要求聊天阶段先显示家庭暴力警示并提供退出。

## 本地自检结果

```json
{
  "status": "PASS",
  "families": 6,
  "sources": 12,
  "versions": 12,
  "adult_only_versions": 2,
  "default_offer_versions": 10,
  "retrieved_at": "2026-08-09T01:03:51+08:00",
  "future_retrieved_at_values": 0,
  "hashes_verified": 12,
  "c0_c3_excerpt_aligned": 12,
  "versions_per_family": 2
}
```

## 尚未完成的生产门槛

- 未逐页对照四库、四部丛刊或其他纸本/影印底本；
- 未完成题署、编者、刊本和类书引书的专业书目复核；
- 未完成两个独立文化审校者签名；
- 未完成自尽、丧亲、家庭暴力和性别身份内容的专业安全审查；
- 未完成页面图像、现代标点、注释与转录层的逐项权利裁决；
- 没有任何记录可标记为 `production_eligible=true`。
