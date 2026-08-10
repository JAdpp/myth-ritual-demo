# 项目二 C1 来源语料、产品主文本与后台见证：权利、来源与文化复核缺口登记

快照日期：2026-08-09  
适用语料：`c1-single-story-wikisource-2026-08-09` 与 `c3-dev-primary-2026-08-09-30f`；C1 来源层有 12,354 条接受行、1 条规范化精确重复、12,353 条 canonical 全文记录；C3 有 30 个故事家族、每族 1 个产品主文本，C1-secondary 有 30 条后台第二见证，C0 合计 60 条固定来源；另有 C1 发现目录 180 个候选条目

当前结论：技术 Demo 推荐池包含 C1 的 12,353 条 canonical 全文记录，均可走自动整理体验；C2/C3 指定的 30 条产品主文本另有编辑深标。C1 保持 `source_segmented_unreviewed`、`production_eligible=false`，近重复和故事实体裁决未完成。30 条后台第二见证与 150 条 `metadata_only` 研究线索仍不能推荐。这里的“古籍基础文本公版”不等于网页转录、现代点校、译注、图像、音频或数据库可以自由复制。

## 决策状态

| ID | 状态 | 缺口与风险 | 当前开发控制 | 关闭条件 |
| --- | --- | --- | --- | --- |
| R-001 | BLOCKING_PRODUCTION | 60 条短摘录与 6 部 EPUB 派生的 12,353 条 canonical 全文记录来自中文维基文库。站内转录按 CC BY-SA 4.0 管理；公开数据包须落实归属、许可链接、修改说明与适用改编材料的相同方式共享。 | C1 保留作品/页面 URL、EPUB 与派生产物哈希、获取时间、许可和修改口径；来源数据层与代码层分开，全部 runtime/production false。 | 形成可发布的 attribution/NOTICE、CC BY-SA 许可副本、修改说明、页面历史归属路径及下游相同方式共享边界并完成权利签字；或停止发布维基转录，改从权利清晰的公版扫描独立转录。 |
| R-002 | BLOCKING_PRODUCTION | 古代作者作品通常已过财产权保护期，但现代标点、校注、翻译、排印、网页转录、扫描、插图和录音是独立权利层。 | `rights_and_access` 逐条拆分基础文本与转录层；不引入现代译文、注释、图像或音频。 | 每一生产记录完成 `underlying_work`、`transcription`、`scan`、`translation`、`image`、`audio` 六维裁决。 |
| R-003 | BLOCKING_PRODUCTION | 60 条短摘录已冻结到 oldid，但 C1 EPUB 只有文件级快照哈希；12,354 行的 `revision_id`、`revision_timestamp` 与 `source_permalink` 仍为空。两层材料都未逐条与可靠影印本或独立底本核对。网页/文件自洽不等于文本可靠。 | 60 条保留 revid/timestamp/oldid；C1 保留原始 EPUB、页面 URL、来源定位和文件/文本哈希，并标记 `per_page_revision_id_pending`。 | 补齐 C1 逐页 revision/history 归属链或书面替代方案；每个拟进入 C3 的条目由文本与文化复核者核对扫描页或校勘本。 |
| R-004 | DEV_CONTROLLED | Chinese Text Project 适合作人工核验，但 FAQ 明确禁止自动批量下载；较大作品的 API 还可能需要认证。 | 不从 CText 复制正文、不抓取、不向量化；只保留人工核验入口。 | 若未来需要接入，取得明确许可并在数据清单记录 API 权限与使用范围。 |
| R-005 | OPEN | Project Gutenberg 可作部分作品的备用镜像，但其许可按美国版权与 Gutenberg 商标规则组织，电子书底本也可能混合多个纸本版本。 | 当前未摄取 Gutenberg 正文，只在来源审计中作为备用。 | 使用前核对部署地版权、剥离或遵守 Gutenberg 头尾与商标条款，并确认具体底本。 |
| R-006 | OPEN | Library of Congress 中国古籍馆藏集合的展示许可不能推定为所有馆藏项都允许第三方再利用；不同项目权利说明可不同。 | 只把馆藏影像作为逐件扫描核验候选，不复制未裁决扫描。 | 每一引用扫描保存馆藏项 URL、IIIF manifest、item-level rights 文本和裁决日期。 |
| R-007 | BLOCKING_PRODUCTION | 书目层仍有不确定性：盘古《三五历纪》只存类书引文；《述异记》题署与层累待查；今本《列子》的成书/整理层次待查；《太平御览》所引《春秋后语》只是引文载体；《木兰诗》年代有争议；《木兰奇女传》《雷峰塔奇传》底本、题署和刊刻信息未定。 | 版本关系采用 `indirect_quotation_of_lost_work`、`song_encyclopedia_quotation_of_liezi` 等诚实标签，不把类书引文或推测写成独立原本。 | 文献学复核者为每条补充底本、卷次、版本说明和可复查证据。 |
| R-008 | BLOCKING_PRODUCTION | 60 条来源见证均尚无双人文化裁决，也没有记录具体审校者。现代梗概、共鸣提示和改编边界可能压缩复杂传统。 | 所有现代文本标为“本项目编辑草稿，非原典译文/原意/学术定本”；`reviewers=[]`。产品只读取 30 条 C3 主文本，第二见证不能混入。 | 两名独立复核者签名；分歧与处置进入 review record；至少一人具备相关古典文学/民俗或宗教叙事能力。 |
| R-009 | BLOCKING_RUNTIME_DEFAULT | 60 条见证中有 4 条成人内容：干将莫邪 2 条、《列女传》杞梁妻与《情史类略》梁祝。当前 C3 主文本含其中 3 条，三王墓条已隔离到 C1-secondary。仅有数据标记不能代替 API 过滤。 | 3 条成人主文本均 `adult_only=true`、`default_offer_eligible=false`，只允许显式 opt-in 运行；后台成人见证无任何运行权限，即使 opt-in 也不可推荐。 | API 自动测试证明：未 opt-in 与危机路由不会看到或选中成人主文本，且所有路径均无法推荐 C1-secondary；安全专家完成复核。 |
| R-010 | BLOCKING_PRODUCTION | 本项目新写的故事卡、共鸣提示、改编边界和舞台提示尚未确定对外许可证。 | 标为 `editorial_draft`，不把它们冒充公版原典或 CC 来源内容。 | 项目负责人决定现代编辑文本许可证，并确认与 CC BY-SA 转录层的组合方式。 |
| R-011 | DEV_CONTROLLED | 古籍公版不提供现代图片、配乐、朗读或影视造型的再利用权。 | 所有 `image_assets=[]`、`audio_assets=[]`；仅允许本地 SVG/CSS 象征性占位。 | 逐件资产保存创作者、模型/版本、提示词、生成时间、许可、文化审查与视觉审查记录。 |
| R-012 | DEV_CONTROLLED | 原文摘录中的异体字、未解析字形模板和贡献者新增标点会影响哈希和展示。特别是《邯郸记》页面明确说明站内补加句读。 | 摘录哈希只覆盖写入 JSON 的确切 UTF-8 字符串；记录 normalization 和 transcription status。 | 对照扫描确认字形与标点层；必要时同时保存外交式转录和项目显示层，分别哈希。 |
| R-013 | OPEN | 维基页面会继续编辑；C1 普通页面 URL 没有固定 revision，可能与已哈希 EPUB 快照发生内容漂移。 | EPUB 快照与派生产物均哈希冻结，普通 URL 只用于归属和查看；运行时 60 条来源仍使用 oldid 固定链接。 | 建立页面历史/修订映射与定期差异审计，不自动把新修订覆盖已审核 source canon。 |
| R-014 | DEV_CONTROLLED | `metadata_only` 来源误入全文检索会形成未经授权的复制；C1 全文按 CC BY-SA 摄取，当前仅开放本地技术 Demo 推荐。 | 150 条 metadata-only 不存正文、不建向量；`embedding_allowed=false`；12,353 条 canonical 可运行但 production 全部关闭，C1-secondary 仍无运行资格。 | 管线测试确认只有 canonical C1 进入轻量推荐，metadata_only、exact duplicate 与 C1-secondary 不进入；公开部署前另做发布裁决。 |
| R-015 | OPEN | 中国著作权法中的署名、修改和保护作品完整等人身权不能因为财产权期限届满而忽略。 | 每条保留作品名、题署作者/编者、卷次、来源库、oldid 和转录许可。 | 发布前由权利审校者确认署名方式、改编标识和不歪曲原作的流程。 |
| R-016 | DEV_CONTROLLED | 用户曾在对话中提供第三方 API 凭据；凭据不属于数据语料，也不能写入仓库或日志。 | 本目录未写入任何 API key；数据抓取只访问无需密钥的公开页面。 | 凭据由用户轮换，并只通过服务端秘密管理器注入；仓库扫描保持零命中。 |
| R-017 | BLOCKING_PRODUCTION | 女娲、精卫、愚公与狐假虎威等来源含显著文本层次差异：今本《列子》年代未决、《述异记》题署待查，部分《太平御览》记录只是引文见证。 | 每条 `relation_to_family` 在后台明确写出传世文本、较晚扩展或类书引文；产品只使用 C2 指定的一个主文本，不展示版本选择，也不得从第二见证混入情节。 | 文献学复核者确认各见证的底本、引书关系、异文和主文本的可安全展示范围。 |
| R-018 | BLOCKING_PRODUCTION | 新增现代转译有安全与价值风险：精卫涉及溺亡和无尽劳动；愚公可能被用于赞美过劳与代际牺牲；狐假虎威可能污名化弱者的生存策略；女娲容易被套入性别本质主义。 | `non_fit`、内容警告、改编禁区和视觉禁区逐条写入；不宣称治疗效果，不把坚持、婚育或救世责任设为唯一正解。 | 安全复核、性别/文化复核及目标用户试测完成，并记录拒绝推荐、退出和替代路线。 |
| R-019 | DEV_CONTROLLED | 新增八条虽已逐字命中固定修订 raw content，但共享同一页面的不同故事段（如《列子》女娲/愚公）仍可能受到页面整体改版和转录错误影响。 | 每条独立保存摘录、哈希、定位、oldid；同一 revid 可以对应不同 `story_version_id`，不共享一个可变“全文对象”。 | 建立扫描页级定位与逐段校勘，之后分别签署来源核验。 |
| R-020 | OPEN | C1 已有 6 部书的 12,353 条 canonical 来源记录并全部进入技术 Demo 推荐池，C3 有 30 条编辑深标主文本。数量增加没有解决汉文传世文献偏向，也不能声称覆盖地域、少数民族、口传或所有朝代。 | 产品区分“12,353 条轻量推荐”与“30 条已备讲解”；前者按“规范化精确文本唯一记录”报告，不称“最全”“完整”或“12,353 个专家确认的独立故事”。 | 若转向公开生产，再按作品、时代、地域、类型、族群、口传/书面与敏感内容建立配额和缺口矩阵。 |
| R-021 | DEMO_CLAIM | C1 两套目录粒度不同：12,353 条来源分段按标题/段落切分；180 条旧策展目录又混合故事家族、单篇作品、子母题与章回片段。两者不能直接相加，也不能把来源段说成专家确认的独立故事实体。 | 只把 12,353 条 canonical 全文记录称为“技术 Demo 轻量推荐池”；明确 accepted row、exact duplicate、canonical record 与 near duplicate pending。 | 若转向公开生产，再建立近重复聚类、家族/作品关系和人工合并/拆分日志。 |
| R-022 | OPEN | 《夷堅志》《閱微草堂筆記》《子不語》等已完成来源级系统切分，但尚未完成逐条时代、地域、类型、敏感内容和故事实体统计；《剪燈新話》等其他宋明清材料仍未进入本快照。 | 分别报告六部书的 canonical 计数与未知字段，不以总数冒充时代/体裁均衡。 | 完成六部书的分层抽样标注与缺口矩阵，再按权利清晰度和覆盖缺口决定新增作品。 |
| R-023 | BLOCKING_INGEST | 国家图书馆/中华古籍资源库（NLC）的网页与影像可用于逐件核验，但公开访问不等于允许自动抓取、内容提取或本地再发布。 | 本轮没有从 NLC 下载、OCR、复制、切分、向量化或索引正文/扫描；只保留官方版权声明与具体馆藏项链接。 | 批量接入前取得明确授权或逐项可复用权利依据，并把允许用途、范围、期限、署名和再发布条件写入新来源清单。 |

## 已核验的来源规则

- [中文维基文库版权信息](https://zh.wikisource.org/wiki/Wikisource:版权信息/全文)：站内文本按 CC BY-SA 4.0 与 GFDL 等条件提供；当前 60 条短摘录保留固定修订，6 部 EPUB 的转录层按 CC BY-SA 4.0 管理并保留来源/哈希清单。
- [Wikimedia 使用条款](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/en) 与 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)：对外分发须落实合理署名、许可链接、修改说明和适用改编材料的相同方式共享；C1 每页固定 revision/history 归属链仍待补齐。
- [MediaWiki API 文档](https://www.mediawiki.org/wiki/API/en)：60 条来源已用于冻结页面 `revid` 与时间戳；C1 EPUB 尚未完成逐页 revision 映射。
- [Chinese Text Project API](https://ctext.org/tools/api) 与 [FAQ](https://ctext.org/faq)：仅作人工核验候选；不自动批量下载或复制全文。
- [国家图书馆版权声明](https://www.nlc.cn/web/dsb_footer/bqsm/index.shtml)：本轮未摄取国家图书馆/中华古籍资源库的正文、OCR 或扫描，只允许逐件权利核验或取得明确授权后另建来源快照。
- [Project Gutenberg License](https://www.gutenberg.org/policy/license.html)：只作备用镜像；需单独处理地域版权、商标和电子书底本问题。
- [Kanseki Repository API](https://www.kanripo.org/api)：可作为后续第二机器可读来源；使用前逐部核底本与 CC BY-SA 署名。
- [Library of Congress Chinese Rare Books rights and access](https://www.loc.gov/collections/chinese-rare-books/about-this-collection/rights-and-access/)：必须逐件读取权利说明；例如 [WDL《山海经》馆藏项](https://www.loc.gov/item/2001530410/) 可作为《山海经》扫描核验候选，但不能外推到整个集合。
- [中华人民共和国著作权法](https://www.npc.gov.cn/c2/c30834/202011/t20201119_308796.html)：古籍基础文本的财产权期限判断不能替代署名、修改、完整权及现代贡献层审查。

## 生产放行最小证据

每个拟从 C1 放行到产品的 `entry_id` / `story_version_id` 必须同时具备：

1. 可复查底本或扫描页、逐字核验记录、来源 revision/history 与文本哈希；
2. 基础文本、转录、扫描、现代编辑文本及视听资产的分层权利裁决；
3. 两名独立文化复核者及分歧处置；
4. 明确的内容警告、默认过滤和安全测试；
5. `production_eligible=true` 的签字变更记录。

在上述条件完成前，不得只因 JSON、构建或 API 测试通过而宣称语料已经“权威”“完整”或“可生产发布”。
