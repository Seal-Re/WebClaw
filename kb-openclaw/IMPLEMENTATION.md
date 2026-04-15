# OpenClaw 知识库架构 · 实施清单

总架构已完全由 OpenClaw 驱动；无固定流水线主入口。以下为当前实现与验收要点。

## 一、rag-query

- **位置**：`../skills/rag-query/`
- **行为**：对 Qdrant `kb_main` 做语义检索，返回 top-k 片段（text、doc_id、source、text_type、topic_tags）。
- **验收**：支持位置参数或 `--query`、`--top-k`、`--topic-tags`、`--collection`；OpenClaw 在每轮开始前调用，实现检索优先。

## 二、rag-ingest（仅写入）

- **位置**：`../skills/rag-ingest/`
- **行为**：仅做 chunk → embedding → 写入 Qdrant。不抓取、不精炼；抓取与解读由 Agent 通过 url-reader / pdf-extract / deep-research 等完成。
- **输入**：必填 `--doc-id`、`--topic-tags`；正文通过 `--content "..."` 或 stdin（如 `--source -`）；可选 `--source`（仅作 payload 来源标识）、`--collection`。
- **验收**：`rag-ingest --doc-id xxx --topic-tags "主题" --content "已解读正文"` 或 `echo "正文" | rag-ingest --doc-id xxx --topic-tags "主题"` 能正确入库。

## 三、使命与 Runbook

- **使命**：`mission/system_prompt.md` 作为 OpenClaw 系统提示词或常驻任务描述。
- **Runbook**：`mission/runbook.md` 写明各 Skill 调用时机与输入输出。
- **验收**：Agent 按「检索 → 搜索 → 抓取 → 解读 → 入库」循环选 Skill；入库一律通过 rag-ingest 的仅写入模式。

---

## 本目录后续可增项

- `scripts/`：环境检查（QDRANT_URL、EMBED_*）、本地单轮验证（rag-query + rag-ingest --content）。
- 新增 Skill 时，在 `mission/runbook.md` 与设计文档中补充「何时调用、输入输出」。
