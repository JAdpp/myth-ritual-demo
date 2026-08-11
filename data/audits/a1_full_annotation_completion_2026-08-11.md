# 《梦蝶记》C1 全库 A1 自动标注完成记录

完成时间：2026-08-11（Asia/Singapore）

## 完成范围

- C1 canonical/runtime 单篇文本：12,353 条
- A1 状态：12,353 条 `valid`
- 待处理、失败、隔离：均为 0
- 模型：`deepseek-v4-flash`
- 提示词版本：`mengdie-a1-v1.1-nonthinking-r6-quality-gates`
- 标注版本：`mengdie-annotation-v1.1`
- Schema 版本：`mengdie-a1-schema-v1.1`

六部来源计数：

| 来源 | 单篇数 |
| --- | ---: |
| 《太平广记》 | 6,995 |
| 《夷坚志》 | 2,646 |
| 《阅微草堂笔记》 | 1,198 |
| 《子不语》 | 745 |
| 《聊斋志异》 | 492 |
| 《续子不语》 | 277 |

## 自动质量门

- 独立 A1 validator：`total=with_json=accepted=valid=12353`，`skipped=invalid=0`
- C1 validator：12,353 个 canonical/runtime 单篇、6 部来源古籍，校验通过
- C1 源集合与 A1 标注集合：缺失 0、越界 0、来源元数据或原文哈希不一致 0
- 所有证据均为源文本逐字子串，字符区间可回查
- 生成字段经简体中文固定点转换；古籍原文、题名、定位与证据不做繁简改写
- NDJSON 共 12,353 行，与 SQLite 按固定顺序逐字节闭合
- `annotations.valid.ndjson` SHA-256：`31a0cf6944eeefac9f088f3fbe0c3d2b2e231bb6665b9f43699019d747933e3b`
- C1 source DB SHA-256：`b432d4f702b8b61f0220b7965d8c6db45a725929e15a19a7e23171b68d92a74b`
- A1 Schema SHA-256：`cea2642f15113af47046788ccad734fa4a15badd39ad9504c1cfe8f4466d5cb6`
- System prompt SHA-256：`2e5f5f84ef7189986cba273eed3314273df53ba046a3dd8a4ec66a8b3fb1c7f7`

## 单条确定性源文本对齐

`c1ws_dbbdeed3026e0865608fe002`（《夷坚志·徐十三官人》）的模型输出连续把原文罕见字“慿附”写成“慾附”，导致逐字证据门持续拒绝。最终只对三个证据载体字段执行了源文本确定性对齐：

- `keyEntities[1].evidenceExcerpt`
- `plotBeats[0].evidenceExcerpt`
- `evidence[0].excerpt`

对齐同时满足条目 ID、源文本 SHA-256、唯一原文跨度和完整错误候选四项约束；未改摘要、标签或叙事判断。该条总置信度降为 `low`，并在 uncertainty 中记录“逐字证据中的罕见字已按源文本确定性对齐，仍待人工审核”。最后一次原始模型响应 SHA-256 为 `87774a690f30ae665980c65317a2f170a6a5d49a13980624e3722b6c03534288`。

## 使用边界

本批数据是 LLM 自动预标注加程序质量门，可用于技术 Demo 的检索、推荐与映射候选。它不是人工审核数据：所有记录仍为 `research_review_status=not_reviewed`、`research_ready=false`、`human_review.reviewed=false`，不得据此声称研究级标注可靠性或人工共识。

