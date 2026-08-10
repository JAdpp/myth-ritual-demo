# 梦蝶记：中国古典叙事的AI当代共谱 Demo

“梦蝶记”取意庄周梦蝶，也把传统蝴蝶装的对页与书脊作为界面线索：古籍故事在一页，用户的当代经历在另一页，二者相遇但不混写。

`myth-ritual-demo` 是项目二的独立开发仓库。一次体验分为“相遇 / 共谱 / 再演”：先与智能助手进行半结构化对话并确认摘要，再由混合检索从统一故事池召回有出处候选、生成推荐理由；用户选定一则后，助手依据原文与摘要自动完成五节点映照初稿，用户通过画布或右侧对话小改；最后把批准的节点动态编成 4–7 幕剧场，并为每一幕请求中式美学绘本。

## 当前边界

- 当前是可运行的开发纵向切片，不是完成版语料库、临床产品或疗愈效果证据。
- `source_canon` 始终只读；任何新写内容都进入明确标注的 `user_branch`。
- 开发数据会标记为 `c3-dev`。没有独立文化复核与权利裁决的记录不得进入生产 C3。
- 入口只呈现一个打包确认框；只有勾选后，短摘要、受限候选摘录、映照请求和逐幕画面才允许调用云端服务。模型不可用时使用经过测试的确定性回退。
- 逐幕图片生成由服务端开关控制；上游未配置、超时或审核拒绝时，剧场保留项目内纸影舞台，流程不会中断。
- 剧场旁白使用浏览器/系统语音，环境音由 Web Audio 本地合成；录音完全可选、只存在页面内存且不上传。
- 照片输入、强制录音、人格匹配、故事组合和治疗性主张不在一期范围。

## 本地运行

要求 Node.js 20+ 与 Python 3.10+。

```powershell
npm.cmd install
python -m pip install -r api/requirements.txt
Copy-Item .env.example .env
npm.cmd run dev
```

- Web：`http://127.0.0.1:3001`
- API：`http://127.0.0.1:8010`
- OpenAPI：`http://127.0.0.1:8010/docs`

不要把 API Key 放在 `VITE_*` 变量中；这类变量会进入浏览器包。所有模型密钥只能由 API 进程从本地 `.env` 或部署平台的秘密管理器读取。

## 验证

```powershell
npm.cmd run typecheck
npm.cmd run test:web
npm.cmd run test:api
npm.cmd run build
```

构建和自动测试证明代码与不变量检查通过，不等同于文化复核、浏览器视觉验收、部署验收或形成性用户测试。

## 模型配置

DeepSeek 使用 OpenAI 兼容接口，模型名由 `DEEPSEEK_MODEL` 配置；默认值为 `deepseek-v4-flash`。只有同时配置服务端 `DEEPSEEK_API_KEY`、`ENABLE_LIVE_MODEL_GENERATION=true`，且当前会话确认云端处理时才会联网。相遇对话只发送短上下文；推荐链为“确认摘要 → 检索词规划 → SQLite FTS5/BM25 召回 → 最多 8 条、每条最多 500 字的候选证据 → 模型重排与逐卡理由”；共谱初稿最多携带 800 字原典摘录，并支持自由输入返回节点修改预览。所有模型输出均须通过结构、长度、来源边界和安全检查，失败时回退，不能带错继续。

阿里云图片生成需要同时配置 `DASHSCOPE_API_KEY`、该 Key 所属工作空间的 `ALIYUN_IMAGE_API_HOST` 与明确的 `ENABLE_IMAGE_GENERATION=true`。区域端点不能混用；若本地代理的伪地址模式阻断同地域端点，可显式开启 `ALIYUN_IMAGE_IPV6_FALLBACK_ENABLED`，服务只会在普通链路不可达后对受限阿里云域名使用保留证书校验的公网 IPv6 回退。生成物在进入剧场资产包前仍需人工视觉与文化审核。

## 数据目录

```text
data/
  sources/
    README.md                    原始来源、许可义务、哈希与未摄取来源
    wikisource_epub/             6 部中文维基文库 EPUB 来源快照
  corpus/
    c0_source_manifest.json      查了哪些来源、何时快照、如何处置
    c1_story_catalog.json        180 条扩库候选；metadata_only 不进入推荐
    c1_secondary_source_witnesses.json  后台第二见证；不进入推荐
    c1_single_story/             12,353 条规范化唯一来源记录；按需进入统一推荐池
    c2_primary_story_selection.json     每则故事的主文本选择与理由
    c3_story_versions.json       开发运行集；每则故事恰好一份主文本
  audits/
    rights_gap_register.md       权利、文化访问与复核缺口
    c1_wikisource_ingestion_2026-08-09.md  万级来源语料摄取审计
```

故事类型与母题分开记录；古代作品基础文本公版不代表现代点校、译注、网页转录、插图、录音或数据库可以自由复制。`metadata_only` 记录不得存全文，也不得建立全文向量。

当前统一推荐池包含 12,353 条来源切分记录和 30 条编辑主故事，共 12,383 个可选候选；前端不区分内部准备方式，所有故事都在选中后依据原文整理讲解与映照。C1 单篇来源语料来自 6 部中文维基文库 EPUB：原始切分 12,374 段，拒绝 20 段，接受 12,354 段；经 NFKC、标点/分隔符移除与末尾出处注规范化后识别出 1 条精确重复，形成 12,353 条规范化唯一全文记录。该重复为《太平广记》“桓邈”的标点/末尾出处差异。近似异文和敏感内容没有逐条人工审计，因此不能称“12,353 个专家标注神话”，且全部 `production_eligible=0`。

原有 C1 发现目录有 180 个候选故事条目，混合故事、单篇与章回研究线索；其中 150 条仍是 `metadata_only`，不能进入推荐。C3-dev 运行集有 30 则详标故事，每则恰好采用 1 份产品主文本：盘古开天、夸父逐日、大禹治水、嫦娥奔月、花木兰从军、黄粱一梦、白蛇传、干将莫邪、女娲补天、精卫填海、愚公移山、狐假虎威、后羿射日、女娲造人、神农尝百草、共工触不周山、刑天舞干戚、吴刚伐桂、牛郎织女、孟姜女哭长城、梁山伯与祝英台、田螺姑娘、桃花源、柳毅传书、塞翁失马、守株待兔、刻舟求剑、叶公好龙、伯牙绝弦、南柯一梦。用户只选故事，不比较异文。

C0 仍保留 60 条固定来源见证，用来核验 30 则深标故事的出处和防止模型混写；其中 30 条未采用见证不作为用户选项。C3 的 30 份主文本默认可推荐 27 份；干将莫邪、《列女传》孟姜女赴水与《情史类略》梁祝投墓 3 份须经显式敏感内容 opt-in。C1 轻量候选暂未逐条敏感标注，界面会统一标明“自动整理、内容未逐条复核”。所有语料仍为本地 Demo 口径、`production_eligible=false`。

## 工程结构

```text
src/                 React/Vite“相遇 / 共谱 / 再演”三阶段前端
api/app/             FastAPI 会话、状态机、安全、来源与模型网关
api/tests/           API 不变量与黄金路径测试
data/                C0/C1/C2/C3 数据、主文本选择和审核缺口
contracts/           数据与模型输出 Schema
docs/                架构和开发状态说明
```

参见 [架构与不变量](docs/architecture.md)、[开发状态](docs/implementation-status.md)、[待确认事项](docs/pending-decisions.md)、[来源快照说明](data/sources/README.md) 和 [C1 摄取审计](data/audits/c1_wikisource_ingestion_2026-08-09.md)。
