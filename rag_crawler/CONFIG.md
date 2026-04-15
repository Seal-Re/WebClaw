# RAG Crawler 配置说明

## 一、API 与外部服务总览

当前流水线**一共用到 4 类**外部服务（API 或自建服务）：

| # | 服务 | 用途 | 使用方 | 必填 |
|---|------|------|--------|------|
| 1 | **LLM（Chat Completions）** | 规划子主题、精炼正文 | crawler.py | 是（若未设 OPENCLAW_SKIP_LLM=1） |
| 2 | **Tavily Search** | 按主题搜索候选 URL | crawler.py | 是 |
| 3 | **Qdrant** | 向量库：预检索、写入 | rag-query、rag-ingest | 是 |
| 4 | **Embedding** | 文本→向量（检索与写入） | rag-query、rag-ingest | 是 |

---

## 二、Crawler 中的 API（crawler.py）

| API | 环境变量 | 默认值 | 说明 |
|-----|----------|--------|------|
| **LLM Chat** | `OPENAI_API_KEY` | - | 必填 |
| | `OPENAI_BASE_URL` | `https://api.openai.com/v1` | 网关/自建地址 |
| | `OPENAI_MODEL` | `gpt-4o-mini` | **两处 LLM 调用共用**：plan_topic、refine_content |
| **Tavily** | `TAVILY_API_KEY` | - | 必填，用于 tavily_search |

可选行为：

- `OPENCLAW_SKIP_LLM=1`：不调 LLM，仅抓取 + 原文写库  
- `OPENCLAW_RAG_DATE_OVERRIDE`：日志日期（测试用）

---

## 三、Skill 中使用的 API

### 3.1 rag-query（query.mjs）

| 服务 | 环境变量 | 默认值 |
|------|----------|--------|
| Qdrant | `QDRANT_URL` | `http://127.0.0.1:6333` |
| Embedding | `EMBED_BASE_URL` | `https://api.vectorengine.ai/v1` |
| | `EMBED_API_KEY` 或 `VECTORENGINE_API_KEY` 或 `OPENAI_API_KEY` | - |
| | `RAG_INGEST_EMBED_MODEL` 或 `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` |

### 3.2 rag-ingest（ingest.mjs）

与 rag-query 相同：`QDRANT_URL`、`EMBED_BASE_URL`、`EMBED_API_KEY`（或 `OPENAI_API_KEY`）、Embedding 模型变量。

---

## 四、配置文件写法

1. 复制示例并改名：
   ```bash
   cp env.example .env
   ```
2. 按实际环境修改 `.env` 中的 key、URL、模型名。
3. systemd 中让服务加载该文件，例如：
   ```ini
   [Service]
   EnvironmentFile=/opt/rag-crawler/.env
   ```
4. 若 LLM 与 Embedding 用同一网关和 key，可只设 `OPENAI_API_KEY`、`OPENAI_BASE_URL`，并令 `EMBED_BASE_URL` 与之一致；rag-query/rag-ingest 会回退到 `OPENAI_API_KEY`。

---

## 五、未使用的 Skill 与 API

以下 skill 在 **当前 crawler 流水线中未调用**，无需为它们配置即可运行 crawler：

- web-search-plus（SERPER / EXA / YOU / Kilo 等）
- summarize、deep-research-pro、academic-deep-research、markdown-converter（crawler 内自实现或未调用）

仅为 rag-crawler 部署时，只需保证上表 4 类服务的配置正确即可。
