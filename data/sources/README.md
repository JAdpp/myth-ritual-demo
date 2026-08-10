# C1 原始来源快照

本目录保存 `c1-single-story-wikisource-2026-08-09-demo-pool-v1` 使用的原始来源文件。它与 `corpus/c1_single_story/` 的派生分片、SQLite 索引以及 C3 深标运行集分开管理。任何原始文件都不得静默覆盖；新下载必须生成新快照并重新计算哈希。

## 已摄取来源

6 个 EPUB 均于 2026-08-09 通过 Wikimedia 社区维护的 [Wikisource Export](https://ws-export.wmcloud.org/) 从中文维基文库导出。文件级来源、导出 URL、根页面、获取时间、解析器与所有派生产物哈希以 [`../corpus/c1_single_story/manifest.json`](../corpus/c1_single_story/manifest.json) 为准。

| 作品 | 本地文件 | 字节数 | 获取时间（UTC+8） | EPUB SHA-256 |
| --- | --- | ---: | --- | --- |
| 《太平廣記》 | `wikisource_epub/taiping_guangji.epub` | 5,137,617 | 2026-08-09 02:09:22 | `73e7ec4426e9826eabfd08a42f09a98028cd7e42161a22cf71a025203f389ef8` |
| 《夷堅志》 | `wikisource_epub/yi_jian_zhi.epub` | 2,723,113 | 2026-08-09 02:11:35 | `8301ef61ba59f87d2c38202f011d4fcebb92edc80a83a541a34377751826ad89` |
| 《閱微草堂筆記》 | `wikisource_epub/yue_wei_cao_tang_bi_ji.epub` | 708,643 | 2026-08-09 02:13:27 | `c1cbf749a36273f53e4a624cf2dbd581c0c7326180e94e2ddf8e9029e81b94de` |
| 《子不語》 | `wikisource_epub/zi_bu_yu.epub` | 540,476 | 2026-08-09 02:19:55 | `d083a537119834121fe34a269aeb80d3c0ac286fd9de8b550c82865d6351f7ce` |
| 《續子不語》 | `wikisource_epub/xu_zi_bu_yu.epub` | 224,992 | 2026-08-09 02:20:10 | `4ee417e34886c1c1973cb07ca0d4d3ce3a5fd146ac1fb1b01a73d82d292c1e7a` |
| 《聊齋志異》 | `wikisource_epub/liao_zhai_zhi_yi.epub` | 844,694 | 2026-08-09 02:20:25 | `3b44caff89c7a41318dcd78ebc2d2d2d89f86f58e040e0344d674fdc99c60db5` |

## 权利分层与再发布条件

- 古籍作品的基础文本通常已进入公版，不等于中文维基文库贡献者形成的电子转录、标点与页面组织可以无条件复制。
- 本快照把转录层标记为 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)，并遵守 [中文维基文库版权信息](https://zh.wikisource.org/wiki/Wikisource:%E7%89%88%E6%9D%83%E4%BF%A1%E6%81%AF/%E5%85%A8%E6%96%87) 与 [Wikimedia 使用条款](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/en) 所说明的贡献者归属机制。
- 对外分发原文或派生转录时，至少保留作品名、中文维基文库贡献者、来源页面或导出入口、获取日期、许可名称与链接，并提供可访问的来源/哈希清单；具体页面的修订历史用于追溯贡献者。
- 本项目对 EPUB 做了结构解析、标题/段落切分、NFKC、标点/分隔符与末尾出处注规范化、字段化和过滤。这些修改必须明确标示；不得把派生记录描述为未经修改的维基页面原文。
- 适用的改编转录层须按 CC BY-SA 4.0 相同方式共享。代码许可证、现代编辑文本许可证和来源数据许可证必须分层说明，不能用代码许可证覆盖来源数据的 CC BY-SA 条件。
- 当前 EPUB 快照有文件级 SHA-256，但每条记录的 `revision_id`、`revision_timestamp` 与 `source_permalink` 仍为空；逐页固定修订归属链尚待补齐。公开再发布前应保留页面历史链接，并完成这项缺口或形成书面替代方案。

以上是项目的当前合规控制记录，不替代面向具体发布地区与发布方式的法律意见。

## 明确未摄取的来源

- **Chinese Text Project（CText）**：本轮没有下载、复制、切分、向量化或索引其正文。其 [FAQ](https://ctext.org/faq) 对自动批量下载与内容再发布设有限制；当前只允许人工打开链接做书目或文本核验。若未来批量接入，须先取得明确授权并把许可范围写入新清单。
- **国家图书馆 / 中华古籍资源库（NLC）**：本轮没有下载、OCR、切分、复制或索引其正文/扫描。依据国家图书馆的[版权声明](https://www.nlc.cn/web/dsb_footer/bqsm/index.shtml)，当前只把馆藏项作为逐件书目或扫描核验候选；批量抓取、内容提取或再发布须另行取得明确依据或授权。
- Project Gutenberg、Kanseki Repository、Library of Congress、GitHub 与 Hugging Face 数据集也未进入本快照。未来使用必须逐个核对原始仓库/数据卡、具体版本、许可证、上游来源和再发布条件，不能只凭平台名称推定可用。

## 派生产物与资格边界

派生产物位于 [`../corpus/c1_single_story/`](../corpus/c1_single_story/)；摄取说明见 [`../audits/c1_wikisource_ingestion_2026-08-09.md`](../audits/c1_wikisource_ingestion_2026-08-09.md)。当前有 12,354 条接受记录，其中 1 条是规范化精确重复，canonical/full-text/FTS/轻量推荐池均为 12,353 条。canonical 记录保持 `source_segmented_unreviewed`，但按当前技术 Demo 决策全部 `runtime_eligible=true`，选中后使用自动讲解、mapping 与剧场兜底；30 条 C3 故事另有编辑深标。全部 `production_eligible=false`，不能表述为“万篇已专家标注神话”。
