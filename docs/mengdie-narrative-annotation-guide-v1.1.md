# 《梦蝶记》叙事映照标注指南 v1.1

适用范围：全库语义召回、产品推荐、相遇阶段推荐理由、共谱阶段自动映射，以及后续 HCI 研究。

版本日期：2026-08-10

本版相对 v1.0 的主要调整：

- 标注生产方式由“双人独立标注与裁决”改为“LLM 自动标注、自动校验与人工审核”；
- 将语料来源成熟度 C1/C3 与标注深度 A0–A3 分开管理；
- 增加全库 A1 召回轻标和 `retrieval_profile.modern_retrieval_summary`；
- 技术 Demo 可使用未经人工审核但已通过自动校验的记录，研究数据必须完成人工审核；
- 以自动质量门和人工抽查/审核替代标注者间一致性统计；
- 将目标推荐链路明确为“安全轻标 → BM25 + Dense 混合召回 → Top-K 补全与重排 → 用户确认”，并将 Dense 建索引设为独立的权利与契约门；本轮 A1 不创建 embedding。

---

## 一、标注目标

标注不是判断故事“治什么情绪”，而是回答三个问题：

1. 这则中国古典神话传说呈现了什么处境、冲突和变化？
2. 它可能通过什么关系与当代个人经历形成映照？
3. 在什么情况下不应推荐，或需要警示性解释？

核心原则是：

> 情绪负责初步接近，叙事结构负责真正匹配，价值张力负责解释，推荐边界负责安全。

理论对应：

- 情绪评价：事件与目标、控制感和规范之间的关系。[情绪成分过程模型](https://archive-ouverte.unige.ch/unige%3A96934)
- 叙事认同：能动性、关系联结、意义建构等生命故事特征。[叙事认同理论](https://journals.sagepub.com/doi/10.1177/0963721413475622)
- 结构映射：匹配因果与关系结构，而非表面词语。[结构映射理论](https://groups.psych.northwestern.edu/gentner/papers/Gentner83.2b.pdf)
- 叙事实践：外化、独特结果和偏好故事应由用户协作形成，不应由系统诊断。[外化实践说明](https://dulwichcentre.com.au/articles-about-narrative-therapy/externalising/)

模型生成的标注是一种可修订的编辑解释，不是原典事实、专家结论、心理诊断或治疗建议。

---

## 二、两套正交层级

### 2.1 语料来源成熟度与标注深度不得混用

`C` 层级描述“文本从哪里来、是否已成为产品主文本”；`A` 层级描述“在该文本上完成了多深的标注”。二者必须分别记录。

例如：

- `C1 + A1`：一个来源切片已经完成全库召回轻标；
- `C3 + A2`：一份产品主文本已经完成体验深标；
- `C3 + A3`：一份产品主文本的深标已经通过人工研究审核。

`C3` 不自动等于 `A3`，模型生成的 `A2` 也不自动等于研究数据。

### 2.2 C1 与 C3 的标注单位

#### C1：`source_segment`

C1 标注单位是一段明确切分、可定位的来源文本：

- 有作品、卷次、条目或页段定位；
- 有固定来源快照和文本哈希；
- 只代表当前来源切片，不宣称已经完成跨版本故事实体归并；
- 不把其他古籍、后世改写或大众流传情节拼入当前记录。

C1 主要承载 A0 与 A1。它可以进入全库召回，但产品不得把来源切片描述成已经人工校定的“完整故事”。

#### C3：`product_primary_story`

C3 标注单位是一份经过产品选择、版本固定的主文本：

- 明确指出采用了哪一个来源或哪些可追溯段落；
- 有稳定的产品故事 ID、版本号和文本哈希；
- 编辑改写与来源原文分开保存；
- 是 A2 体验深标和 A3 研究审核的标准单位。

如果某个 C1 候选需要进入正式的选中后深度体验，应先建立或关联 C3 `product_primary_story`。技术 Demo 可临时补全 C1 候选的部分字段，但其 `unit_type` 仍须保持 `source_segment`，不得冒充 C3，也不得进入 `research_ready`。

### 2.3 A0–A3 标注深度

| 层级 | 名称 | 主要单位 | 覆盖范围 | 主要用途 | 人工审核要求 |
|---|---|---|---|---|---|
| A0 | 来源层 | C1 或 C3 | 全库 | 来源、定位、原文、版本、哈希 | 入库规则校验；不属于语义审核 |
| A1 | 全库召回轻标 | C1 `source_segment` | 全库 C1 | 现代语义桥接、BM25/FTS 与未来混合召回的候选输入、安全初筛 | 技术 Demo 可不审核；正式产品按批抽查 |
| A2 | 体验深标 | C3 `product_primary_story` | Top-K 候选、选中故事或已维护 C3 | 重排、推荐理由、共谱映射 | 技术 Demo 可不审核；正式发布宜审核高曝光与高风险记录 |
| A3 | 研究审核层 | C3 `product_primary_story` | 冻结的研究样本 | 用户实验、论文分析、研究参考集 | 必须人工审核并签署，不能由自动校验替代 |

A0–A3 是逐层增加的要求，不意味着全库必须预先完成 A2 或 A3。全库先完成 A1；只有进入 Top-K、被用户选中、进入高曝光产品集合或研究样本的故事，才按需升级。

---

## 三、通用标注原则

### 3.1 来源事实与编辑解释分开

| 类型 | 示例 | 性质 |
|---|---|---|
| 来源事实 | 人物、行动、结局、原文摘录 | 可核验，必须指向当前文本 |
| 模型或人工解释 | 可映照责任压力、关系边界 | 可讨论，必须记录生成者和置信度 |

现代检索释义、情绪轨迹、价值张力和推荐理由都属于编辑解释。任何编辑解释都不能覆盖或替代来源原文。

### 3.2 当前文本原则

- 标注只能依据记录所绑定的当前文本版本。
- 大众熟知但当前文本未出现的情节不得补入。
- 多版本之间存在冲突时分别标注，不合成为无来源的“完整版本”。
- 原文或产品主文本发生变化后，依赖旧哈希的标注必须转为 `stale` 并重新生成或审核。

### 3.3 最小充分原则

- 不确定时宁可少标或填写 `unknown`。
- 不为了提升召回率制造文本没有支持的主题、情绪或转折。
- 不从悲剧结局反推人物具有精神疾病。
- 不把古代规范包装为适用于当代用户的唯一答案。

### 3.4 用户解释权

系统提供的是“可能的映照”，不是对用户经历的定论。产品必须允许用户拒绝、修改或重新表述系统映射。

---

## 四、统一记录结构

```json
{
  "annotation_record": {
    "annotation_version": "mengdie-annotation-v1.1",
    "annotation_level": "A1",
    "unit": {
      "corpus_level": "C1",
      "unit_type": "source_segment",
      "unit_id": "c1-...",
      "source_text_sha256": "..."
    },
    "source_profile": {},
    "retrieval_profile": {},
    "reflection_profile": null,
    "evidence": [],
    "annotation_meta": {}
  }
}
```

字段存在性按层级控制：

| 字段 | A0 | A1 | A2 | A3 |
|---|---:|---:|---:|---:|
| `unit`、`source_profile`、来源哈希 | 必须 | 必须 | 必须 | 必须 |
| `retrieval_profile` | 无 | 必须 | 保留或更新 | 冻结 |
| `life_context` | 无 | 0–3 项轻标 | 1–3 项完整标注 | 人工审核 |
| `narrative_arc` | 无 | 核心子集 | 完整 | 人工审核 |
| `affective_arc` | 无 | 可选 | 完整 | 人工审核 |
| `value_tensions` | 无 | 可选 | 完整 | 人工审核 |
| `matching_profile` | 无 | 无；另用 `auto_safety_screen` | 完整 | 人工审核 |
| `evidence` | 来源定位 | 必须 | 必须 | 人工核验 |

缺失与空数组含义不同：字段未在当前层级生成时应省略或填 `null`；空数组只能表示模型已检查但未发现相应项目，并须结合审核状态解释。

---

## 五、A0 来源层

A0 至少保存：

```json
{
  "source_profile": {
    "work": "作品名",
    "juan_or_section": "卷次或章节",
    "locator": "稳定定位",
    "source_url_or_snapshot": "来源 URL 或本地快照标识",
    "source_version": "版本或抓取日期",
    "source_text": "当前标注范围的原文",
    "source_text_sha256": "...",
    "rights_status": "...",
    "embedding_allowed": false
  }
}
```

要求：

- `source_text_sha256` 必须由实际送入标注流程的文本计算；
- 来源文字与编辑后的现代文本分栏保存；
- 任何 A1–A3 记录必须能回溯到 A0；
- 来源定位缺失、文本为空或哈希不匹配的记录不得开始自动标注。

---

## 六、A1 全库召回轻标

A1 解决现代用户叙述与文言来源之间的语言域差异，不承担完整解释和研究金标准功能。

### 6.1 `retrieval_profile`

```json
{
  "retrieval_profile": {
    "modern_retrieval_summary": "忠实概括当前来源切片的现代汉语检索释义。",
    "summary_kind": "model_generated_retrieval_paraphrase",
    "summary_language": "zh-Hans",
    "narrative_sufficiency": "sufficient",
    "narrative_sufficiency_reason": null,
    "key_entities": [
      {
        "name": "人物或角色称谓",
        "role": "行动者/受影响者/帮助者",
        "evidence_ids": ["ev01"]
      }
    ],
    "plot_beats": [
      {
        "type": "trigger",
        "text": "原有处境如何被打破",
        "evidence_ids": ["ev01"]
      },
      {
        "type": "action_or_turn",
        "text": "人物采取了什么行动或发生何种转折",
        "evidence_ids": ["ev02"]
      },
      {
        "type": "outcome",
        "text": "文本呈现的结果",
        "evidence_ids": ["ev03"]
      }
    ],
    "motif_terms": ["变形", "离别"],
    "summary_evidence_ids": ["ev01", "ev02", "ev03"]
  }
}
```

#### `modern_retrieval_summary` 的边界

它是现代汉语检索释义，通常写 60–180 字，Schema 允许 20–250 字。这个范围用于适应 C1 来源切片的真实长度差异，而不是要求模型凑到固定长度。

当前 2026-08-09 C1 快照的 12,353 条 canonical 记录按 `char_count` 分布如下：

| 原文长度 | 记录数 | 占比 |
|---:|---:|---:|
| 20–59 字 | 590 | 4.78% |
| 60–119 字 | 2,074 | 16.79% |
| 120–299 字 | 5,218 | 42.24% |
| 300–999 字 | 3,975 | 32.18% |
| 1,000–2,999 字 | 450 | 3.64% |
| 3,000 字及以上 | 46 | 0.37% |

其中最短 20 字，中位数 230 字，最长 7,584 字。因此：

- 极短原文应生成忠实的短释义，达到 20 字下限即可，不得重复、扩写或补造情节来凑 60 字；
- 一般叙事以 60–180 字为生成目标；
- 情节较复杂时可扩展至 250 字，但不得因为原文长就机械铺陈细节；
- 摘要长度合规不代表语义质量合格，仍须检查证据和忠实性。

现代检索释义：

- 不是正式现代翻译；
- 不是可以替代原文的产品主文本；
- 不追求逐字对应，也不得添加原文没有的人物动机、心理诊断或道德结论；
- 不得单独作为面向用户的推荐证据；
- 必须绑定原文证据、来源哈希、模型版本和提示词版本；
- 在原文、模型或提示词发生实质变化时必须重新生成或复核。

`narrative_sufficiency` 取值为 `sufficient / insufficient / unknown`：

- `sufficient`：当前来源切片足以判断最低限度的叙事处境与行动；
- `insufficient`：极短、残缺或主要为名录/说明等非叙事文本，无法可靠填写叙事标签；
- `unknown`：自动流程无法稳定判断是否具备足够叙事信息。

现代检索释义、情节节点和母题词可作为未来 Dense 索引的候选输入，但本轮 A1 只生成与校验结构化轻标：

- 不调用 embedding 服务；
- 不生成或保存向量；
- 不建立 Dense 索引；
- 不修改来源记录中的 `embedding_allowed=false`；
- 不因记录达到 `automatically_validated` 就推断其允许向量化。

Dense 索引必须另行通过权利审查与数据契约门，明确可处理字段、派生物保存方式、许可义务和删除/重建规则后才能实施。通过该门后，标题、现代检索释义、情节节点和母题词可形成故事语义向量；原文分块向量仅用于被明确允许的记录。两种向量均为派生索引，不改变原文的事实地位。

### 6.2 来源长度路由

- 当前库最长记录为 7,584 字；只要仍处于已验证的模型上下文与输出预算内，长记录应单条提交完整当前文本，不与其他故事同批，也不截断；
- 只有模型上下文预检失败、供应商明确拒绝长度或完整输入的结构质量不稳定时，才按自然段切分；单段仍过长时再按句末标点切分；
- 进入分块路径时应有重叠，默认目标块长 1,200–1,800 字、相邻块重叠约 100–200 字或至少一个完整句子，并保留块 ID、原文绝对偏移和来源定位；
- 每块先抽取人物、行动、转折、结局与证据，再由汇总步骤合并去重并生成全篇轻标；
- 汇总步骤只能使用各块证据支持的内容，冲突或跨块关系不足时填写 `unknown`；
- 不论原文长度，都禁止用“截取开头 + 截取结尾”替代完整阅读或分块处理；模型上下文预算不足时同样走上述分块流程。

### 6.3 A1 最小叙事子集

A1 至少填写：

- `life_context`：0–3 项；文本足以判断时选择 1–3 项，极短、残缺或非叙事信息不足时允许 0 项；
- `narrative_arc.trigger`；
- `narrative_arc.conflict_types`：最多两项；
- `narrative_arc.agency_modes`：最多两项；
- `narrative_arc.ending_mode`；
- 与上述字段相连的证据；
- 自动安全初筛。

`narrative_sufficiency` 只判断当前原文是否具有可辨识的叙事结构，不与 `life_context` 是否非空绑定。A1 的完整叙事可以没有与十项生活情境直接对应的标签，此时允许 `narrative_sufficiency=sufficient` 且 `life_context=[]`；不得为了填满标签强行添加生活情境。A2 面向已固定的 C3 产品主文本，`life_context` 仍要求 1–3 项；如果 C3 仍无足够叙事证据，就不应升级为合格 A2。

`affective_arc`、完整 `value_tensions` 和完整 `matching_profile` 不是全库召回的前置条件，可在 A2 按需生成。

### 6.4 自动安全初筛

```json
{
  "auto_safety_screen": {
    "status": "auto_screened",
    "flags": ["death", "physical_injury"],
    "interpretation_risks": ["glorify_self_sacrifice"],
    "uncertainties": [],
    "model_preannotation": true
  }
}
```

安全状态必须区分：

- `not_run`：未执行安全筛查；
- `auto_screened`：仅模型筛查；
- `human_reviewed`：人工已经核对；
- `unknown`：文本或模型输出不足以判断。

`flags: []` 只表示模型未检出风险，不等于“经人工确认无风险”。`unknown` 也不得自动折算为安全。技术 Demo 可在界面或日志中明确其为模型初筛；高风险和无法判断的记录应保守降权、加边界说明或进入隔离队列。

`flags` 与 A2 `content_warnings` 共用以下英文枚举，避免索引、过滤器和界面映射使用不同词表：

| 枚举 | 中文释义 |
|---|---|
| `death` | 死亡 |
| `violence` | 暴力 |
| `physical_injury` | 身体伤害 |
| `self_harm_or_suicide` | 自伤或自杀 |
| `sexual_content` | 性相关内容 |
| `sexual_violence` | 性暴力 |
| `coercion_or_abuse` | 强迫或虐待 |
| `child_harm` | 儿童受伤害 |
| `animal_harm` | 动物受伤害 |
| `discrimination` | 歧视 |
| `captivity` | 囚禁或限制自由 |
| `supernatural_horror` | 超自然恐怖 |
| `grief_or_bereavement` | 哀伤或丧亲 |
| `illness` | 疾病 |
| `none_identified` | 当前筛查未发现明确警示；不等于人工确认安全 |

`none_identified` 不得与其他警示同时出现。文本不足以判断时使用安全状态 `unknown`，而不是填写 `none_identified`。

---

## 七、A2/A3 核心映照结构

A2 在 C3 `product_primary_story` 上补全五组产品与论文都会使用的核心标注；A3 不新增理论字段，而是对 A2 完成人工审核、修订和冻结。

```json
{
  "reflection_profile": {
    "profile_version": "mengdie-reflection-v1.1",
    "life_context": [],
    "affective_arc": {},
    "narrative_arc": {},
    "value_tensions": [],
    "matching_profile": {}
  }
}
```

### 7.1 `life_context`：生活情境

表示故事可能与哪些现实处境形成比较，不表示故事具有治疗作用。A2/A3 每则故事选择 1–3 项；A1 的例外规则见第 6.3 节。

| 标签 | 中文名称 | 判定说明 |
|---|---|---|
| `relationship_boundary` | 关系边界 | 接近、分离、拒绝、控制或重新协商关系 |
| `family_duty` | 家庭责任 | 孝、婚姻、照料、代际责任 |
| `belonging_isolation` | 归属与孤立 | 寻找群体、被排斥、失去知音 |
| `separation_loss` | 分离与失去 | 离别、死亡、失去家园或重要关系 |
| `work_study_pressure` | 工作学习压力 | 任务、竞争、评价或长期投入 |
| `long_term_responsibility` | 长期责任 | 持续承担公共或个人责任 |
| `choice_uncertainty` | 选择与不确定 | 多个方向、未知后果、是否行动 |
| `injustice_conflict` | 不公与冲突 | 压迫、复仇、权力冲突、追责 |
| `identity_transition` | 身份转变 | 离开旧身份、进入新角色、变形 |
| `persistence_change` | 坚持与改变 | 继续、转向、放弃或改变方法 |

规则：

- 不得因为“感觉可能有用”就添加标签；
- 每个标签必须至少有一处情节证据；
- 不确定时宁可少标。

### 7.2 `affective_arc`：情绪轨迹

情绪不是给整篇故事贴一个标签，而是描述开始到结束的变化。

```json
{
  "affective_arc": {
    "start": {
      "valence": "negative",
      "activation": "high",
      "control": "low",
      "dominant_emotions": ["恐惧", "迷惘"]
    },
    "end": {
      "valence": "mixed",
      "activation": "medium",
      "control": "medium",
      "dominant_emotions": ["坚定"]
    },
    "evidence_level": "inferred"
  }
}
```

取值：

- `valence`：`negative / mixed / neutral / positive / unknown`
- `activation`：`low / medium / high / unknown`
- `control`：`low / medium / high / unknown`
- `dominant_emotions`：最多两个，建议使用“恐惧、焦虑、悲伤、愤怒、羞耻、孤独、迷惘、思念、希望、平静、坚定、释然”
- `evidence_level`：`explicit / inferred / unknown`

禁止从悲剧结局反推人物一定“抑郁”，禁止使用临床诊断词，也不得为了推荐需要强行制造正向转折。

### 7.3 `narrative_arc`：叙事结构

这是故事与用户经历进行映照的核心。

```json
{
  "narrative_arc": {
    "trigger": "原有秩序被打破",
    "conflict_types": ["nature_fate"],
    "constraint": "原有方法无法解决问题",
    "agency_modes": ["transform_method", "endure"],
    "turning_point": "改变应对问题的方式",
    "ending_mode": "transformation",
    "agency_shift": "increase"
  }
}
```

`conflict_types` 最多选择两个：

- `self`：自我内部冲突
- `relational`：人物关系冲突
- `institutional_collective`：制度、群体或权力冲突
- `nature_fate`：自然、命运或超自然力量
- `knowledge_uncertainty`：信息不足、误解或未知

`agency_modes` 最多选择两个：

- `endure`：承受、等待
- `avoid`：躲避、隐瞒
- `seek_help`：寻求帮助
- `negotiate`：协商
- `confront`：直接对抗
- `transform_method`：改变方法
- `withdraw`：退出或离开
- `sacrifice`：牺牲自身利益
- `collective_action`：共同参与
- `unknown`：文本无法判断

不能把行动自动评价为“正确”。

`ending_mode`：

- `restoration`：恢复原有秩序
- `transformation`：形成新的秩序或身份
- `separation`：以离开、分离结束
- `sacrifice`：以牺牲结束
- `unresolved`：问题未解决
- `cautionary`：以负面后果形成警示
- `open`：开放结局

`agency_shift`：`decrease / stable / increase / mixed / unknown`。

### 7.4 `value_tensions`：价值张力

只标故事同时呈现的两种价值，不标唯一正确寓意。

```json
{
  "value_tensions": [
    {
      "left": "责任",
      "right": "自我保存",
      "evidence_ids": ["ev03", "ev05"],
      "confidence": "high"
    }
  ]
}
```

优先使用：责任/自我保存、家庭义务/个人选择、忠诚/自主、正义/宽恕、坚持/退出、秩序/自由、真相/保护、归属/独立、服从命运/主动行动、传统/改变、人类需求/自然秩序、记住/放下。

规则：

- 每则故事最多标两个价值对；
- 必须能在故事中同时找到两侧证据；
- 只有单一价值出现时，不得人为制造“张力”；
- 不把古代社会规范直接包装为现代正确答案。

### 7.5 `matching_profile`：推荐方式与边界

```json
{
  "matching_profile": {
    "resonance_affordances": [
      {
        "mode": "mirror",
        "life_context": "long_term_responsibility",
        "rationale": "都涉及长期失序、旧方法失效和应对方式改变",
        "confidence": "high",
        "evidence_ids": ["ev01", "ev04"]
      },
      {
        "mode": "contrast",
        "life_context": "persistence_change",
        "rationale": "可用于比较坚持与退出的不同可能",
        "confidence": "medium",
        "evidence_ids": ["ev04"]
      }
    ],
    "non_fit": [
      "用户已因过度责任明显耗竭时，不应只强调继续承担"
    ],
    "content_warnings": ["death", "physical_injury"],
    "interpretation_risks": ["glorify_self_sacrifice"]
  }
}
```

`mode`：

- `mirror`：经历结构相似
- `contrast`：故事提供不同选择
- `caution`：故事展示潜在后果
- `possibility`：故事开启新的想象方向

一则故事可以同时有多种方式，但每种必须给出理由和证据。

`interpretation_risks` 推荐词表：

- `glorify_self_sacrifice`：美化自我牺牲
- `normalize_violence`：合理化暴力
- `victim_blaming`：责怪受害者
- `fatalism`：强化宿命论
- `gender_stereotype`：强化性别角色
- `filial_coercion`：把强制孝道包装成唯一答案
- `revenge_as_justice`：把复仇等同正义
- `authority_obedience`：把服从权威当作默认正确
- `none_identified`：模型或审核者未发现明显风险

风险标签不等于禁用，而是要求推荐理由体现批判性边界。使用 `none_identified` 时必须结合 `auto_safety_screen.status` 或人工审核状态，不能将模型未发现风险写成“已确认安全”。

---

## 八、证据要求

```json
{
  "evidence": [
    {
      "id": "ev01",
      "locator": "卷一·某则",
      "excerpt": "不超过八十字的来源摘录",
      "supports": [
        "narrative_arc.trigger",
        "life_context.long_term_responsibility"
      ]
    }
  ]
}
```

规则：

- 每个 `life_context`、`value_tension`、核心情节节点和推荐映照至少对应一条证据；
- A1 的现代检索释义必须覆盖其核心叙述所依赖的证据 ID；
- 原文没有依据时填 `unknown`，不能用大众记忆补全；
- 编辑解释和原典引文必须分开保存；
- C1 证据定位指向当前 `source_segment`；C3 证据定位指向当前 `product_primary_story` 所绑定的来源；
- 自动校验应确认 `excerpt` 是当前来源文本的精确子串或保存可解释的规范化匹配结果；
- 面向用户的推荐理由必须回到原文证据，不得只引用现代检索释义。

---

## 九、标注元数据与状态

### 9.1 元数据结构

```json
{
  "annotation_meta": {
    "product_annotation_status": "automatically_validated",
    "research_review_status": "not_reviewed",
    "generated_at": "2026-08-10T00:00:00+08:00",
    "generator": {
      "type": "llm",
      "provider": "...",
      "model": "...",
      "model_version": "...",
      "prompt_version": "mengdie-a1-v1.1-nonthinking-r6-quality-gates",
      "temperature": 0
    },
    "validation": {
      "schema_valid": true,
      "source_hash_match": true,
      "evidence_links_valid": true,
      "validated_at": "2026-08-10T00:01:00+08:00"
    },
    "human_review": {
      "reviewed": false,
      "reviewer_id": null,
      "reviewed_at": null,
      "decision": null,
      "notes": []
    },
    "overall_confidence": "medium",
    "model_preannotation": true
  }
}
```

### 9.2 产品标注状态

```text
generated
→ automatically_validated
→ sampled_reviewed 或 human_reviewed
```

异常分支：

```text
generated 或 automatically_validated
→ validation_failed / quarantined / stale
```

- `automatically_validated` 表示通过格式、来源、证据链接和约束检查，不表示语义已经被人确认；
- 技术 Demo 允许使用 `automatically_validated` 的 A1 和 A2 记录；
- Demo 中使用时应保留 `model_preannotation=true`，不得展示“专家审核”或“研究验证”等错误声明；
- 正式产品阶段按风险、曝光量和抽查策略升级为 `sampled_reviewed` 或 `human_reviewed`。

### 9.3 研究审核状态

```text
not_reviewed
→ under_review
→ revisions_required
→ research_ready
```

`research_ready` 必须同时满足：

1. `annotation_level` 为 A3；
2. 单位为版本冻结的 C3 `product_primary_story`；
3. `human_review.reviewed=true`，并有审核者、日期和结论；
4. 人工核对来源忠实性、证据、解释边界和安全字段；
5. 不存在未解决的关键来源问题或高风险问题；
6. 模型、提示词、codebook、来源哈希和修订历史可追溯。

自动校验、模型自评、模型之间互评或仅有抽样审核都不能单独把记录升级为 `research_ready`。

---

## 十、LLM 自动标注与人工审核流程

### 第一步：固定来源与单位

确认作品、卷次、来源快照、原文范围、哈希、`corpus_level` 和 `unit_type`。明确不得混入的后世流传情节。

### 第二步：生成 A1 轻标

LLM 只接收当前来源文本、结构化 codebook 和必要的来源元数据，不接收现有产品推荐文案，避免被既有解释诱导。先识别事实结构，再生成现代检索释义和轻量标签。

### 第三步：绑定证据

所有关键标签与现代检索释义中的核心情节必须关联证据。没有证据支持的标签删除、降低置信度或改为 `unknown`。

### 第四步：自动校验

自动检查：

- JSON Schema 与必填字段；
- 枚举值、数量上限、长度和空值语义；
- `modern_retrieval_summary` 是否位于允许的 20–250 字范围，并将 60–180 字仅作为通常目标而非硬门槛；
- 来源哈希与版本；
- 证据 ID 引用完整性；
- 摘录是否能在当前原文中定位；
- 所有 LLM 生成的中文字段是否为简体中文；来源原文、来源题名、定位和逐字证据不得参与繁简转换；
- `evidence.supports` 是否只指向最终实际存在的声明，不得支持 `unknown`、`none_identified` 或已被模型删除的标签；
- A1/A2/A3 字段是否符合层级要求；
- C1/C3 单位是否被误写；
- 超过 3,000 字的记录是否以完整文本单条提交；若实际进入分块路径，是否保留完整分块清单、重叠、绝对偏移与汇总证据，且没有用截头尾代替；
- A1 是否保持原始 `embedding_allowed=false`，且本轮产物中不存在向量或 Dense 索引写入；
- 临床诊断、治疗承诺、无证据动机和唯一化道德结论等禁用表达；
- A 类明确安全短语是否具有规定的完整旗标，B 类广义敏感信号是否至少具有一个相容旗标；
- 安全状态是否将 `unknown`、`none_identified` 或空数组误当作人工确认安全。

自动校验只能确认可计算的约束，不能证明解释一定忠实或恰当。

### 第五步：进入 Demo 或候选升级

- A1 通过自动校验后可进入技术 Demo 的结构化轻标存储与 BM25/FTS 召回；本轮不进入 Dense 索引；
- 召回后的 Top-K 可补充候选比较字段；
- 用户选中的故事应尽量关联或建立 C3，再生成完整 A2；
- 高频、低置信度或高风险记录进入人工审核优先队列。

### 第六步：人工审核

人工审核者依据当前文本逐项检查，而不是重新做一份独立标注。审核结果可以是：

- `accept`：接受模型标注；
- `accept_with_edits`：修改后接受；
- `regenerate`：调整提示词或上下文后重生成；
- `quarantine`：来源、切分或安全问题未解决，暂不使用。

审核重点：

1. 是否忠于当前原文；
2. 是否夹带其他版本或后世情节；
3. 是否过度心理化或使用临床语言；
4. 叙事结构与价值张力是否有证据；
5. 推荐边界和安全提醒是否充分；
6. 是否把一种合理解释写成唯一答案。

一般记录可由一名合适的人工审核者签署；敏感故事或研究关键样本可增加文化文本或安全方向的专项复核，但不把“双人独立标注”设为统一前置条件。

### 第七步：研究冻结

进入 A3 的记录在人工修订后冻结来源、标注、模型、提示词和 codebook 版本。研究开始后若发生变更，应创建新版本并记录变更，不静默覆盖实验材料。

---

## 十一、自动质量门与人工抽查

### 11.1 自动质量指标

每批标注至少报告：

- 生成成功率与解析失败率；
- Schema 通过率；
- 来源哈希匹配率；
- 证据 ID 完整率；
- 原文摘录可定位率；
- 必填字段完整率与枚举违规率；
- 现代检索释义长度合规率；
- 长文完整输入率；实际进入分块路径时另报分块覆盖率、重叠与原文偏移校验通过率；
- `embedding_allowed=false` 保持率和本轮向量产物计数（必须为 0）；
- `unknown`、低置信度和安全标记比例；
- 隔离、重试和最终失败数量。

以下规则属于独立于 LLM 自报结果的硬质量门：

1. **生成字段简体门。** 对 `modern_retrieval_summary`、叙事充分性说明、生成的实体名与角色、情节节点、母题词、叙事触发和安全不确定性说明逐字段执行 OpenCC `t2s` 固定点检查；转换结果与原值不同即失败。`source_profile.*`、来源题名、原文、定位以及 `evidence.locator / excerpt` 均不检查、不改写，以免损坏版本与逐字证据。
2. **证据声明闭合门。** 精确子串和偏移正确只是定位成立；每个 `supports` 还必须指向最终标注中实际存在的声明。指向 `*.unknown`、`*.none_identified` 或已经从最终标签中删除的路径，分别记为非声明支持或悬空支持并硬失败。自动规则不能判断引文在语义上是否充分支撑解释，这一部分仍由人工抽查或逐条审核负责。
3. **A 类明确安全短语门。** 对自杀/自伤、性暴力、明确拘禁、明确伤害、胁迫、动物伤害、死亡结果和吞食人物等低歧义短语，要求补齐规定旗标；缺任一旗标即失败。
4. **B 类广义敏感信号门。** 对死亡、暴力/伤害、拘禁/胁迫、性内容和疾病的广义词表，先屏蔽已知人名、官名、成语和词义歧义，再检查是否至少存在一个相容的安全旗标。B 类命中不自动决定唯一标签，但“没有任何相容旗标”仍是硬失败，应重新标注或隔离。
5. **跨标签门。** `sexual_violence` 必须同时具有 `sexual_content` 与 `coercion_or_abuse`；原文明确显示自伤/自杀致死时，`self_harm_or_suicide` 必须同时具有 `death`。

模型经两次针对性重生成后仍未覆盖 B 类信号时，技术 Demo 的最终兜底可以只补一个最宽泛的相容旗标，并绑定命中位置的逐字证据；同时必须把安全状态降为 `unknown`、整体置信度降为 `low`，并写入“本地规则保守补标、待人工审核”的不确定性说明。该兜底不是细粒度语义裁决，也不得用于 A3 研究冻结。模型反复生成超过 250 字的现代检索释义时，最终兜底只允许在 250 字内按完整句边界截短并降为低置信度，不得改写或补造事实。

安全状态 `unknown` 可以诚实表达模型不能完成整体判断，但不能绕过已经由原文明示的 A/B 信号；`unknown` 记录仍须保守保留可确定的安全旗标。`none_identified` 只表示自动筛查未发现警示，不能用于覆盖词表命中，也不能写成“已确认安全”。

任何记录只要来源哈希不匹配、关键证据无法定位、生成字段未通过简体门、证据支持悬空、A/B 安全门缺旗标、结构解析失败或单位类型冲突，就不得标为 `automatically_validated`。

### 11.2 人工抽查

技术 Demo 阶段可以暂缓全库人工抽查，但必须保留 `model_preannotation=true`、`human_review.reviewed=false`、当前提示词版本（现行为 `mengdie-a1-v1.1-nonthinking-r6-quality-gates`）、可追溯状态和质量日志。自动质量门通过只表示“LLM 自动标注 + 程序校验通过”，不表示摘要忠实性、标签恰当性或证据语义充分性已经由人确认，也不能据此声称全库已经人工审核。

进入正式产品阶段后，A1 应按批次做分层抽查，覆盖：

- 不同作品、类型和文本长度；
- 高风险、低置信度与 `unknown` 记录；
- 高曝光和高召回频次记录；
- 模型、提示词或 codebook 更新后的新批次。

项目可在试运行期使用每批 5%–10% 的抽查比例，并根据接受率、修改率和隔离率调整。抽查应记录 `accept / accept_with_edits / regenerate / quarantine`，以这些结果修订提示词、标签定义和自动规则。

A3 研究样本不是抽查：每条都必须完成人工审核。

### 11.3 不再使用双人一致性作为统一门槛

本流程不要求为全库计算双人标注一致性，也不以 0.67、0.80 等一致性阈值作为上线条件。质量判断改为：

- 可计算约束由自动校验负责；
- 语义忠实性、解释边界和安全性由人工抽查或逐条审核负责；
- 研究材料以完成可追溯的人工审核和版本冻结为准。

若某项具体研究另有多评审者一致性需求，应在该研究方案中单独定义，不能反向成为全库 A1 的生产前置条件。

---

## 十二、用户侧信息如何对应

用户侧不使用故事的整套标注，也不建立临床画像。只在当前会话中暂存：

```json
{
  "user_reflection_profile": {
    "current_context": [],
    "expressed_emotions": [],
    "perceived_control": "unknown",
    "desired_direction": "想理解|想行动|想比较|暂未确定",
    "value_priority": [],
    "avoid_topics": []
  }
}
```

要求：

- 允许用户查看和修改；
- 不推断人格类型或精神障碍；
- 不把临时情绪保存为稳定身份；
- 会话删除时一并删除；
- 用户边界优先于模型的相似度分数。

---

## 十三、推荐算法使用顺序

### 13.1 本轮 A1 技术 Demo 链路

本轮只生成结构化轻标，不创建 embedding。实际链路为：

```text
用户叙述与回避边界
→ A1 自动安全轻标过滤或保守降权
→ BM25/FTS 原文与标题召回
  + A1 结构化字段筛选或加权
→ Top-K 候选增强、A2 补全与 LLM 重排
→ 基于原文证据的推荐理由
→ 用户确认、拒绝或修改映射
```

### 13.2 目标推荐链路（Dense 权利与契约门通过后）

以下是后续目标架构，不表示本轮 A1 已获准建立向量索引：

```text
用户叙述与回避边界
→ A1 自动安全轻标过滤或保守降权
→ BM25 原文/标题稀疏召回
  + 现代检索释义 Dense 召回
  + 原文分块 Dense 召回
→ 排名融合与去重
→ Top-K 补全 A2 字段或候选增强
→ 基于原文证据的 LLM 重排与推荐理由
→ 用户确认、拒绝或修改映射
```

具体要求：

1. 根据用户明确的 `avoid_topics` 和已知高风险标签进行硬过滤；仅有模型初筛或状态未知时采用保守降权、警示或送审策略。
2. BM25 保留标题、专名和原文词面信号；Dense 主要弥合现代叙述与文言文本之间的语义差异。
3. 融合后对 Top-K 比较 `narrative_arc`，而不是只比较情绪或主题词。
4. 候选进入最终推荐前，补全或读取 `affective_arc`、`value_tensions`、`matching_profile` 和证据。
5. 根据 `non_fit` 与 `interpretation_risks` 修正理由或排除候选。
6. 组合提供镜像、对照、警示和可能性故事，避免所有结果重复同一种价值方向。
7. 推荐理由必须回到来源证据，不能把现代检索释义当作事实出处。
8. 把映射交给用户确认和修改，并记录用户修改用于产品改进；不得将修改自动解释为心理特征。

### 13.3 推荐理由最低要求

每条理由至少回答：

```text
为什么是这则故事？
对应的是哪段关系或因果结构？
它和你的经历有什么不同？
这个比较有哪些边界或风险？
```

推荐理由应使用“可能、可以比较、也许提供另一种看法”等可修订表达，不使用“你就是、你需要、这个故事说明你”等诊断式或决定式表达。

---

## 十四、论文中的使用方式

核心实验问题可固定为：

> 叙事结构标注是否比情绪与生活情境标注更能提高中国古典神话传说推荐的贴合度、可解释性和用户能动感？

实验条件：

1. 情绪 + 生活情境；
2. 情绪 + 生活情境 + 叙事结构；
3. 条件 2 + 可修改的共谱画布。

字段对应关系：

| 字段 | 研究作用 |
|---|---|
| `life_context`、`affective_arc` | 基线条件 |
| `narrative_arc` | 核心机制 |
| `value_tensions` | 文化解释和质性分析 |
| `matching_profile` | 错误推荐、安全与边界分析 |
| 用户修改记录 | 衡量系统映射与用户理解之间的差距 |

主要结果宜测：

- 推荐贴合度；
- 推荐理由可信度；
- 结构映射理解度；
- 用户修改量；
- 最终映射控制感；
- 文化理解；
- 错误或冒犯性推荐比例。

论文中必须明确区分：

- A1/A2 的 LLM 自动标注；
- 自动校验通过但未经人工审核的 Demo 数据；
- 完成人工审核并冻结的 A3 研究数据。

模型预标或 Demo 运行记录不能写成“专家金标准”。不应在没有临床研究设计时声称或测量“治疗效果”。

---

## 十五、实施范围与启动顺序

建议按以下顺序执行：

1. 对全库 C1 运行 A0 预检，确认单位、来源定位、文本和哈希可用；
2. 按 6 部来源作品、原文长度带、叙事充分性和安全风险建立分层清单；20–59 字与 3,000 字及以上记录必须被纳入试点，不能只按总体占比抽样；
3. 先运行 120 条分层 pilot，验证短文不凑字、长文完整单条输入及必要时的分块降级、证据定位、Schema、失败重试和隔离流程，并据此修订提示词与自动校验器；
4. pilot 通过后扩展到累计约 10% 的 canary；按当前 12,353 条 canonical 记录计算约为 1,235 条，120 条 pilot 计入其中，不另行叠加；
5. 比较 pilot 与 canary 的生成成功率、证据定位率、`unknown` 分布、长文分块覆盖和安全标记漂移；自动质量门稳定后才扩展到全库；
6. 批量生成其余全库 A1：现代检索释义、叙事结构子集、证据和安全初筛；失败项进入重试或隔离队列；
7. 只有 `automatically_validated` 的记录进入技术 Demo 的结构化轻标存储与 BM25/FTS 召回；本轮不创建 embedding，所有来源记录保持 `embedding_allowed=false`；
8. 对现有 C3 故事生成 A2，供 Top-K 重排、推荐理由和共谱体验使用；
9. 技术 Demo 阶段记录模型版本、批次指标和未审核状态，不把人工审核作为运行前置门槛；
10. Demo 稳定后开展分层人工抽查，优先覆盖高风险、低置信度、高曝光和研究候选；
11. 从类型均衡的 C3 中选择研究样本，逐条人工审核并升级为 A3 `research_ready`；
12. 若后续实施 Dense，另行完成权利审查、修改数据契约并通过索引验收门，不以 A1 已完成为默认授权。

这里的累计 10% canary 是自动标注管线的渐进放量范围，不是人工抽查比例，也不改变技术 Demo 可先不进行人工审核的决定。

全库标注链路为：

```text
A0 来源与哈希
→ A1 现代语义桥接、叙事轻标、证据与安全初筛
→ 本轮 BM25/FTS 召回（Dense 通过独立门后再升级为混合召回）
→ A2 候选/选中故事深标与推荐边界
→ 用户确认和修改
→ A3 人工审核与研究冻结（仅研究样本）
```

这套流程不要求在推荐启动前深标全库。A1 为全库必需，A2 按候选与使用需求生成并缓存，A3 仅覆盖研究样本。

叙事疗法只指导助手如何协作提问和尊重用户的解释权，不直接变成临床标签或治疗承诺。

---

## 十六、版本与变更管理

- 原文、切分、产品主文本、模型、提示词、Schema 或 codebook 任一发生实质变化，都必须记录新版本；
- 来源文本变化时，所有依赖旧 `source_text_sha256` 的标注和向量标记为 `stale`；
- 标签定义变化时，不静默混用旧批次与新批次；
- 重新生成必须保留旧记录或变更日志，便于比较和复现实验；
- Demo、正式产品和研究数据导出时分别报告审核覆盖率，不用一个笼统的“已标注”状态代替。
