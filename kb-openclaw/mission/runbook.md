# OpenClaw 知识库 · Runbook（Skill 调用约定）

本文档说明在知识获取使命下，**何时调用哪个 Skill**、**输入输出约定**。总架构由 OpenClaw 驱动；rag-ingest 仅做写入，不做 URL 抓取与精炼。

---

## 检索与入库

| Skill | 调用时机 | 输入 | 输出 |
|-------|----------|------|------|
| **rag-query** | 每轮开始前（检索优先）；可选在解读某 URL 前再查一次 | `query`（主题/问题），可选 `--top-k`、`--topic-tags`、`--collection` | JSON：top-k 片段（text、doc_id、source、text_type、topic_tags） |
| **rag-ingest** | 解读完成后写入 | 必填：`--doc-id`、`--topic-tags`；正文：`--content "..."` 或 stdin；可选：`--source`（来源 URL，仅作 payload）、`--collection` | 写入 Qdrant；无 URL 抓取/精炼 |

---

## 发现与抓取

| Skill | 调用时机 | 输入 | 输出 |
|-------|----------|------|------|
| **tavily-search** / **tavily** | 拿到主题后，获取 URL 列表 | 搜索 query | URL/摘要列表 |
| **baidu-search** | 国内主题或 tavily 不可用时 | 搜索 query | URL/摘要列表 |
| **url-reader** | 对网页 URL 拉取正文 | URL | 网页正文 |
| **markdown-converter** | 网页/PDF/Office 转 Markdown（如 uvx markitdown） | 文件或 URL | Markdown 文本 |
| **pdf-extract** | 遇 PDF 链接或文件时抽文本 | PDF 路径或 URL | 纯文本 |

---

## 解读与精炼

| Skill | 调用时机 | 输入 | 输出 |
|-------|----------|------|------|
| **deep-research-pro** | 对单页/多源做深度解读与报告 | 原始正文或多源内容 | 结构化摘要/报告，再交给 rag-ingest |
| **summarize** | 对长文做摘要 | 长文本 | 摘要文本 |
| **literature-review** | 文献/技术类解读（论文、长文档） | 正文或文献列表 | 综述/要点，再交给 rag-ingest |

---

## 其他 Skill（按需纳入使命）

- **trend-watcher**：趋势与热点，可驱动「搜什么主题」。
- **elite-longterm-memory** / **memory-setup**：Agent 自身记忆，与知识库分离；知识库专注 RAG。
- **obsidian / notion / github / playwright-mcp / agent-browser** 等：在系统提示或本 runbook 中补充「何时调用、输入输出」后，纳入 24/7 流程。

---

## 单轮流程简图

```
主题 → rag-query → tavily-search → 对每个 URL：pdf-extract / url-reader / markdown-converter
  → deep-research-pro / summarize / literature-review → rag-ingest（--content 或 stdin）
```
