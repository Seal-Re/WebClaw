# OpenClaw 知识库架构（kb-openclaw）

本目录为 **RAG 知识库 · 最终设计（OpenClaw 驱动）** 的落地开发根目录。设计文档见：`../docs/RAG知识库-最终设计-OpenClaw驱动.md`。**总架构已完全由 OpenClaw 驱动**，无固定流水线主入口。

## 架构概览

- **编排者**：OpenClaw Agent（24/7），系统提示词 = 知识获取使命 + 可用 Skill 说明 + 检索优先。
- **三大 Skill 组**：
  1. **发现与抓取**：tavily-search、baidu-search、url-reader、markdown-converter、pdf-extract
  2. **解读与精炼**：deep-research-pro、summarize、literature-review
  3. **检索与入库**：rag-query（查知识库）、rag-ingest（仅写入：chunk + embedding + 写 Qdrant）
- **存储**：Qdrant（collection `kb_main`）+ Vector Engine（embedding）。

单轮流程：**rag-query → tavily-search → 按 URL 抓取（url-reader/pdf-extract/markdown-converter）→ 解读（deep-research-pro/summarize/literature-review）→ rag-ingest 入库**。抓取与解读均由 Agent 通过上述 Skill 完成；rag-ingest 只做写入，不抓取、不精炼。

## 本目录结构

```
kb-openclaw/
├── README.md              # 本文件：架构说明与入口
├── IMPLEMENTATION.md      # 实施清单与验收
├── mission/
│   ├── system_prompt.md   # 知识获取使命（可直接粘贴到 OpenClaw）
│   └── runbook.md         # 何时调用哪个 Skill 的 runbook
└── scripts/               # 可选：环境检查、本地验证等
```

## 与技能代码的关系

| 组件 | 位置 | 说明 |
|------|------|------|
| rag-query | `../skills/rag-query/` | 语义检索 Qdrant，返回 top-k 片段；检索优先时由 Agent 调用。 |
| rag-ingest | `../skills/rag-ingest/` | **仅写入**：接收已解读正文（`--content` 或 stdin），chunk → embedding → 写入 Qdrant。 |
| 使命提示词 | `mission/system_prompt.md` | 粘贴到 OpenClaw 作为系统提示或常驻任务。 |

## 快速开始

1. 阅读 `../docs/RAG知识库-最终设计-OpenClaw驱动.md` 确认目标与原则。
2. 在 OpenClaw 中配置系统提示词：使用 `mission/system_prompt.md` 内容；参考 `mission/runbook.md` 做 Skill 调用约定。
3. 保证 OpenClaw 常驻或定时拉起，并配置 `QDRANT_URL`、`EMBED_*`（或 `OPENAI_API_KEY`）等环境变量。

## 环境要求

- OpenClaw 常驻或定时拉起
- 环境变量：`QDRANT_URL`、`EMBED_BASE_URL`、`EMBED_API_KEY`（或 `OPENAI_API_KEY`）、`EMBEDDING_MODEL`（可选），与 `rag-ingest` / `rag-query` 一致。
