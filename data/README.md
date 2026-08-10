# 项目二数据层

本目录把“候选发现”“来源见证”“产品主文本”和“开发运行”分开管理。当前口径是：

- C1 单篇来源语料：6 部中文维基文库 EPUB 切分所得 12,353 条规范化唯一全文记录，均进入本地技术 Demo 的轻量推荐池；未逐条人工标注，不可生产使用；
- C1 发现目录：180 个候选故事条目，其中 150 个仍为 `metadata_only`，不能推荐；这是早期策展目录，不与上述 12,353 条来源分段相加冒充“故事总数”；
- C0 来源清单：30 个详标故事、60 条固定修订来源见证；
- C2 选择账本：每个详标故事指定 1 个产品主文本和 1 个后台第二见证；
- C3 开发运行集：30 个产品主文本，每个故事恰好 1 条；
- C1-secondary：30 条后台第二来源见证，只用于来源校验和防止模型混写，不能推荐，也不让用户选择版本。

这些材料覆盖先秦至清代传承文本中的神话、传说、志怪、寓言、传奇、话本、戏曲和章回小说样本，但不声称是“中国古典神话传说/故事全集”。“产品主文本”是演示阶段的编辑选择，状态为 `demo_editorial_selection_pending_expert_review`，不等于学界公认的最权威定本。

## 文件

- `corpus/c0_source_manifest.json`：30 个故事家族、60 条修订级来源记录、短摘录、SHA-256，以及每族的主文本/后台见证指针。
- `corpus/c1_story_catalog.json`：180 条扩库候选；组级来源只作检索入口，`metadata_only` 条目没有固定引文且不能推荐。
- `corpus/c1_single_story/summary.json`：12,353 条规范化唯一来源分段的计数、口径和资格状态摘要。
- `corpus/c1_single_story/manifest.json`：6 个 EPUB、15 个 NDJSON 分片、SQLite/FTS 索引的路径、SHA-256、来源链接、获取时间与许可清单；这是文件级审计的权威入口。
- `corpus/c1_single_story/shards/` 与 `catalog.sqlite3`：C1 来源层全文分片与本地检索索引；API 按需从 SQLite 取轻量候选，不在启动时整库载入。
- `corpus/c1_secondary_source_witnesses.json`：30 条完整后台第二见证；运行、默认推荐和 Demo 资格全部关闭。
- `corpus/c2_primary_story_selection.json`：30 条主文本选择决定、第二见证映射、理由与例外；明确 `authoritative_claim=false`。
- `corpus/c3_story_versions.json`：30 条编辑深标主文本；它们使用完整讲解、映射和剧场字段，C1 候选则使用确定性自动兜底。
- `audits/product_primary_selection_2026-08-09.md`：逐族选择理由、例外、产品规则和结构校验。
- `audits/c1_wikisource_ingestion_2026-08-09.md`：12,353 条 C1 规范化唯一来源分段的数量复算、哈希、去重口径、权利义务与未摄取来源审计。
- `audits/rights_gap_register.md`：权利、底本、文化复核、安全与生产放行缺口。
- `audits/baseline_audit_2026-08-09.md`、`audits/source_expansion_2026-08-08.md` 与三份 `corpus_expansion_*_audit.md`：60 条来源见证在分层前的采集和固定修订审计；其中历史性的“C3 版本”表述指详标建库记录，不表示当前仍有多个用户可选版本。
- `merge_expansion_batches.py`：合并审计批次后，严格按 C2 账本拆成 30 条 C3 主文本和 30 条 C1-secondary 后台见证。
- `build_story_catalog.py`：从审阅种子重建 180 条发现目录，并拒绝每族多于 1 条 C3 主文本。
- `validate_corpus.py`：离线校验 C0/C1-secondary/C2/C3 数量、主次分区、哈希、oldid、权利和成人门控。
- `validate_story_catalog.py`：校验 180 条目录唯一性、30 条详标目录与唯一 C3 主文本对齐、metadata-only 隔离和组级来源边界。
- `ingest_wikisource_c1.py`：从已保存的中文维基文库 EPUB 按作品适配器切分、规范化、精确去重并生成 C1 来源层产物。
- `validate_c1_story_corpus.py`：只读校验 EPUB、分片、SQLite/FTS、摘要、清单哈希及 C1 资格边界。
- `sources/README.md`：原始 EPUB 来源、哈希、许可义务和明确未摄取来源。

## C1 单篇来源语料快照

`c1-single-story-wikisource-2026-08-09` 的实际构建结果为：原始切分 12,374 段，拒绝 20 段，接受并落盘 12,354 条；规范化精确重复 1 条，形成 12,353 条 canonical/full-text/FTS 记录。15 个 NDJSON 分片与 SQLite 保存 12,354 行（含 1 条指向 canonical 的精确重复记录），FTS 只索引 12,353 条 canonical 记录。各书表格统计 canonical 记录：

| 来源作品 | 落盘记录数 |
| --- | ---: |
| 《太平廣記》 | 6,995 |
| 《夷堅志》 | 2,646 |
| 《閱微草堂筆記》 | 1,198 |
| 《子不語》 | 745 |
| 《續子不語》 | 277 |
| 《聊齋志異》 | 492 |
| 合计 | 12,353 |

这里的“一条”是作品适配器识别的标题/副标题条目，或被该作品适配器明确当作一则的来源段落。当前唯一性只覆盖 NFKC、标点/分隔符移除和末尾出处注规范化后的精确文本哈希；验证结果为 `exact_duplicate_segments=1`，即《太平廣記》“桓邈”的标点/末尾出处差异。近重复仍未审查，包括同一故事在不同卷次的重复记载、异题、节本、转述和同母题异文，因此不得把 12,353 解释为 12,353 个互不相关的故事实体。它们也尚未完成神话/传说/志怪/寓言分类、文献学复核、文化复核或 C3 产品编辑。

## 当前分层状态

### C3 产品主文本

每条 C3 记录必须满足：

- `product_primary: true`
- `record_role: product_primary_story`
- `primary_selection_status: demo_editorial_selection_pending_expert_review`
- `eligible_for_demo: true`
- `development_runtime_eligible: true`
- `production_eligible: false`

产品标题使用大众熟悉的故事名；卡片副标题只标明当前采用的一个来源，不展示异本比较，也不要求用户选择版本。文本关系、异文和第二见证只保留在来源与审计层。

### C1-secondary 后台见证

每条后台第二见证必须满足：

- `product_primary: false`
- `record_role: secondary_source_witness`
- `eligible_for_demo: false`
- `development_runtime_eligible: false`
- `default_offer_eligible: false`
- `production_eligible: false`
- `rights_and_access.allowed_uses` 不含任何本地运行权限，只允许内部来源审计、来源链接和受约束的署名短摘录使用。

后台见证不能作为候选卡、讲解对象、映射底本或剧场脚本来源。AI 若把后台见证情节写入产品主文本，属于数据边界错误。

## 成人内容门控

60 条固定来源见证中有 4 条 `adult_only=true`：

- C3 产品主文本 3 条：《吴越春秋》干将莫邪、《列女传》杞梁妻、《情史类略》梁祝；
- C1-secondary 后台见证 1 条：《搜神记》三王墓。

3 条成人产品主文本保持 `default_offer_eligible=false`、`requires_explicit_adult_opt_in=true`，且权利字段只允许 `local_development_runtime_with_adult_opt_in`。消费端没有显式成人确认时必须排除。后台成人见证即使取得成人确认也不能进入推荐。

## 记录结构

主文本与后台见证都保留完整标注结构，便于逐条审计：

- `story_type`：叙事类型，如 myth、legend、zhiguai、chuanqi；
- `motifs`：母题数组，与故事类型分开；
- `source_canon`：只读来源、固定修订、短摘录、哈希、文本关系和编辑性结局说明；
- `story_card`：本项目现代编辑草稿，不是原典译文或学术定本；
- `adaptation_boundary`：必须保留、可以转译和禁止改写的边界；
- `theatre_asset_pack`：舞台锚点与视听资产限制；
- `rights_and_access`：基础文本、网页转录和运行权限；
- `review_record`：待完成的底本、权利、文化和安全复核；
- `provenance`：作品、卷次、作者/编者、固定链接与署名信息。

`source_canon.ending` 是带前缀的项目编辑概述，不是原文引句。原文只存于 `source_canon.original_excerpt`，其 UTF-8 字节必须匹配 `excerpt_sha256`。

## 来源与许可边界

当前有两类中文维基文库材料：60 条固定修订短摘录，以及从 6 部 EPUB 切分的 12,353 条 canonical C1 全文来源记录。古代作品的基础文本与维基贡献者形成的电子转录是两个权利层；项目不能因为前者通常已进入公版，就把后者当作无条件公版。C1 清单按 CC BY-SA 4.0 记录转录层许可，项目当前按下列条件处理：

1. 对外提供转录文本或其改编版本时，保留作品、中文维基文库、来源页面/导出链接、获取日期和 CC BY-SA 4.0 链接等合理署名；页面修订历史是贡献者归属链的一部分。
2. 明示项目所做的 EPUB 解析、段落切分、NFKC、标点/分隔符与末尾出处注规范化、字段化与过滤，不把派生产物描述成未经修改的原始页面。
3. 以 CC BY-SA 4.0 允许的方式分发改编材料，并对适用的改编转录层维持同许可；不得用代码仓库的许可证覆盖来源数据层许可证。
4. 若不接受该发布条件，须改为从权利清晰的公版扫描独立转录、校对并保存证据，且不能沿用维基转录文本。

Chinese Text Project（CText）与国家图书馆/中华古籍资源库（NLC）均未被本轮摄取：没有从它们批量下载、复制正文或建立全文索引。CText 只保留人工核验入口；NLC 只可在逐项权利裁决后作为扫描/书目核验来源。Project Gutenberg、Kanseki Repository 和 Library of Congress 也只是备用或二次核验来源，不能在未逐条审核条款时自动复制。

目前仍须特别保留以下边界：

- 盘古主文本是《艺文类聚》保存的《三五历纪》佚书引文，不是完整佚书原本；
- 女娲造人主文本同样来自佚文引录；
- 牛郎织女主文本只直接承载七夕会合核心，不把现代完整家庭叙事冒充原文；
- 孟姜女产品入口采用大众标题，但主文本人物为杞梁妻，城墙也不能直接说成秦长城；
- 梁祝主文本中的化蝶属于篇末流传层，不能覆盖婚约、病逝和墓裂的主要叙事；
- 所有这些考据说明只用于来源真实性和生成边界，不转化为用户的版本选择任务。

## 更新流程

1. 批量摄取前先完成来源级条款审查；只允许进入明确获授权的来源快照。CText 与 NLC 当前不在允许批量摄取清单。
2. 原始来源文件不得被静默替换；每次快照都保存下载时间、原始 URL、字节数和 SHA-256，并生成新的版本化清单与摄取审计。
3. 来源分段进入 C1 后，canonical 记录按当前技术 Demo 决策全部 `runtime_eligible=true`、`production_eligible=false`；唯一 exact duplicate 不重复推荐。
4. C1 轻量候选不等待逐条审计即可推荐，但界面必须标明自动整理；C3 深标故事继续使用编辑完成的讲解和映射字段。`metadata_only` 条目仍不得混入全文推荐池。
5. 为拟详标故事固定两条可区分的来源见证，保存 oldid、timestamp、短摘录和 SHA-256；在 C2 指定唯一 `primary_version_id` 和 `secondary_witness_id`。
6. 运行分层构建与验证。脚本把主文本写入 C3、第二见证写入 C1-secondary；主次重叠、缺失或来源覆盖不完整时必须失败。
7. 一名文本复核者对照扫描，一名独立文化复核者审阅解释与改编边界；成人内容另做安全复核。只有完成权利裁决和双人复核后，才能以有签字记录的变更讨论单条生产放行。

不要把第三方 API key、模型凭据、用户私人文本或会话日志写入本目录。

## 本地校验

从项目根目录运行：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python data\merge_expansion_batches.py
python data\build_story_catalog.py
python data\validate_corpus.py
python data\validate_story_catalog.py
python data\validate_c1_story_corpus.py
```

运行集校验必须输出：30 个故事家族、30 条产品主文本、30 条后台第二见证、60 条固定来源，60/60 哈希与来源对齐，成人来源总数 4、成人产品主文本 3。目录校验必须输出 180 条早期策展候选、30 条详标、150 条 metadata-only。C1 来源层校验必须输出 6 部作品、15 个分片、原始 12,374、拒绝 20、接受/SQLite 12,354、精确重复 1、canonical/full-text/FTS/推荐池 12,353、生产资格 0。离线检查证明数据和运行契约一致，不代表逐条内容审计已经完成。
