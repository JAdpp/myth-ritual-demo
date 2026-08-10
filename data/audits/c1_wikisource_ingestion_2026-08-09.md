# C1 中文维基文库单篇来源语料摄取审计

快照日期：2026-08-09  
目录版本：`c1-single-story-wikisource-2026-08-09-demo-pool-v1`  
适用路径：`data/sources/wikisource_epub/`、`data/corpus/c1_single_story/`  
审计结论：结构、哈希、分片、SQLite/FTS 和资格边界验证通过；近重复、逐页固定修订、文献学/文化/敏感内容复核仍未完成。

## 1. 数量复算

| 指标 | 数量 | 含义 |
| --- | ---: | --- |
| `raw_segments` | 12,374 | 六部 EPUB 经作品适配器得到的原始候选段 |
| `rejected_segments` | 20 | 未通过结构/内容门槛的段 |
| `accepted_segments` / SQLite 行 | 12,354 | 接受并保留来源与去重关系的记录行 |
| `exact_duplicate_segments` | 1 | 规范化后全文哈希与既有 canonical 相同的记录 |
| `unique_single_stories` / `full_text_stories` | 12,353 | canonical 全文记录；也是 FTS 索引数 |
| `near_duplicate_review_status` | `pending` | 尚未做语义/书目层面的近重复聚类与人工裁决 |
| NDJSON 分片 | 15 | 含 12,354 行，包括 1 条 exact-duplicate provenance 行 |
| `runtime_eligible` / `recommendation_pool_stories` | 12,353 | 全部 canonical 记录进入本地技术 Demo 轻量推荐池 |
| `production_eligible` | 0 | 无记录完成生产放行 |

复算关系：`12,374 - 20 = 12,354`；`12,354 - 1 = 12,353`。`data/validate_c1_story_corpus.py` 在本快照输出 `PASS`，并确认 SQLite 为 12,354 行、FTS/canonical/full-text 为 12,353 条。

### 规范化精确重复

唯一的 exact duplicate 是《太平廣記》“桓邈”：卷第118与卷第276正文在 NFKC、标点/分隔符移除及末尾出处注规范化后得到相同哈希，但可见文本的标点与末尾出处分别为《夢雋》与《幽明錄》。系统保留两条来源 provenance 行，并把卷第276记录标为 `exact_duplicate`、指向卷第118 canonical；FTS 只索引 canonical。这项处理只证明“当前规范化规则下正文精确相同”，不裁决两处书目关系或何者更权威。

### 各作品 canonical 计数

| 来源作品 | canonical/full-text 记录 |
| --- | ---: |
| 《太平廣記》 | 6,995 |
| 《夷堅志》 | 2,646 |
| 《閱微草堂筆記》 | 1,198 |
| 《子不語》 | 745 |
| 《續子不語》 | 277 |
| 《聊齋志異》 | 492 |
| 合计 | 12,353 |

## 2. “12,353 条”的声明边界

一条记录是来源作品中的标题/副标题条目，或被作品适配器明确视作一则的来源段落。12,353 只表示在当前规范化规则下获得 12,353 条 canonical 全文来源记录；它不表示：

- 12,353 个彼此独立的故事实体；
- 12,353 个传统神话，或全部都属于神话/传说/志怪；
- 12,353 条已经专家标注、底本核验或文化复核的材料；
- 12,353 个经过专家标注或具备编辑深标讲解的产品故事；当前全部可走自动整理的轻量推荐、讲解、mapping 与剧场兜底，但只有 30 个具备编辑深标；
- 六部书已经覆盖中华古典叙事的全部时代、地域、族群、体裁或口传传统。

近重复状态仍为 `pending`。待处理对象至少包括异题同文、同题异文、节本、转述、重复收录、同母题不同故事，以及一则被错误拆成多段或多则被合成一段的结构误差。在近重复聚类和人工裁决前，不得把 canonical 记录数当作故事实体数。

## 3. 原始文件与哈希

| 作品 | EPUB SHA-256 |
| --- | --- |
| 《太平廣記》 | `73e7ec4426e9826eabfd08a42f09a98028cd7e42161a22cf71a025203f389ef8` |
| 《夷堅志》 | `8301ef61ba59f87d2c38202f011d4fcebb92edc80a83a541a34377751826ad89` |
| 《閱微草堂筆記》 | `c1cbf749a36273f53e4a624cf2dbd581c0c7326180e94e2ddf8e9029e81b94de` |
| 《子不語》 | `d083a537119834121fe34a269aeb80d3c0ac286fd9de8b550c82865d6351f7ce` |
| 《續子不語》 | `4ee417e34886c1c1973cb07ca0d4d3ce3a5fd146ac1fb1b01a73d82d292c1e7a` |
| 《聊齋志異》 | `3b44caff89c7a41318dcd78ebc2d2d2d89f86f58e040e0344d674fdc99c60db5` |

当前摘要 SHA-256 为 `bbf0ecc9a3be9e1de6a2575ef0aa153ca1b0b5fb19914da7cda317c73b61366e`；SQLite SHA-256 为 `b432d4f702b8b61f0220b7965d8c6db45a725929e15a19a7e23171b68d92a74b`。15 个分片的逐文件行数、字节数与 SHA-256，以及每个 EPUB 的导出 URL、根页面、获取时间、解析器与条款链接，均以 `data/corpus/c1_single_story/manifest.json` 为权威清单。

## 4. 来源与许可裁决

### 已摄取：中文维基文库 EPUB

古籍基础文本与社区电子转录是不同权利层。本快照把基础文本标为 public-domain layer，把中文维基文库的转录/页面贡献层按 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 管理，并参考[中文维基文库版权信息](https://zh.wikisource.org/wiki/Wikisource:%E7%89%88%E6%9D%83%E4%BF%A1%E6%81%AF/%E5%85%A8%E6%96%87)和 [Wikimedia 使用条款](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/en)。项目当前要求：

1. 再发布原文或派生转录时，合理署名作品与中文维基文库贡献者，保留来源页面/导出入口、获取日期、许可名称与链接，并提供可访问的来源/哈希清单和页面历史归属路径。
2. 明示 EPUB 解析、标题/段落切分、NFKC、标点/分隔符移除、末尾出处注规范化、字段化、过滤和精确去重等修改。
3. 对适用的改编转录层采用 CC BY-SA 4.0 相同方式共享，不施加与该许可冲突的额外限制。
4. 将来源数据许可与代码、项目现代编辑文本、插画、音频及用户内容的许可分开；代码许可证不能覆盖来源数据层义务。

当前每条记录保留来源作品、页面 URL、来源定位、原始 EPUB 哈希和 CC BY-SA 字段，但 `revision_id`、`revision_timestamp` 与 `source_permalink` 仍为 `null`。这意味着文件级快照已冻结，逐页固定修订归属链仍待补齐；公开数据包必须至少保留页面历史链接，并在发布前裁决该缺口。

### 未摄取：CText 与 NLC

- **Chinese Text Project（CText）**：没有从 CText 下载、复制、切分、向量化或索引任何正文。其 [FAQ](https://ctext.org/faq) 对自动批量下载与内容再发布设有限制；项目当前仅允许人工链接核验。未经明确授权，不得把它加入批量摄取源。
- **国家图书馆 / 中华古籍资源库（NLC）**：没有从 NLC 下载、OCR、复制、切分或索引正文/扫描。根据国家图书馆[版权声明](https://www.nlc.cn/web/dsb_footer/bqsm/index.shtml)，当前只允许把具体馆藏项作为逐件书目/扫描核验候选；批量抓取、内容提取或再发布须另行取得明确依据或授权。

本轮也没有摄取 Project Gutenberg、Kanseki Repository、Library of Congress、GitHub 或 Hugging Face 数据集。平台“公开可访问”不自动等于“可批量复制和本地再发布”；未来接入必须保存具体版本、原始数据卡/仓库、许可证、上游来源与再发布条件。

## 5. 剩余阻塞项

- 近重复检测、故事实体聚类与拆分/合并抽检；
- 每条神话/传说/志怪/寓言/故事类型与母题的人工标注；
- 逐页固定 revision/history 归属链或书面替代方案；
- 对可靠扫描/底本的文献学核验；
- 敏感内容、安全、文化访问和双人文化复核；
- 对外数据包的 attribution、NOTICE、修改说明、CC BY-SA 许可文件与相同方式共享边界；
- 若转向公开生产，再补齐逐条产品遴选与审查；当前只批量开放本地 Demo runtime，仍禁止批量设为 production eligible。

当前准确表述为：“本地技术 Demo 的推荐池包含 12,353 条规范化精确文本唯一的中文维基文库来源分段记录；全部可走自动整理体验，其中 30 条 C3-dev 主文本具备编辑深标。近重复与专家标注待完成，生产资格为 0。”
