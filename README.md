# WebClaw

**基于大模型的自动化渗透测试平台** — 第十七届中国大学生服务外包创新创业大赛 · A10 赛题

WebClaw 将大语言模型的推理决策能力与持续更新的 RAG 知识库相结合，实现「决策—执行—验证—留痕」的自动化安全测试闭环。平台采用模块化智能体协作架构，支持 GPT、Claude、DeepSeek 等主流模型接口，集成覆盖全渗透测试流程的安全工具，并生成可追溯的企业级渗透测试报告。

---

## 架构概览

```
Orchestrator（编排器）— 状态机 + 阶段内任务表（唯一事实来源）
        │
        ├── Knowledge Module（知识模块）
        │       ├── (OpenClaw)  24/7 联网爬取 → 精炼 → 写入 Qdrant
        │       └── (RAG)       语义检索，为决策引擎提供知识增强
        │
        ├── LLM Decision Engine（决策引擎）
        │       接收阶段/目标上下文/知识片段，输出下一步动作
        │
        ├── Skill Executor（工具执行器）
        │       注册并执行原子化安全工具（Nmap、SQLMap 等），可选沙箱
        │
        └── Trace & Report Service（留痕与报告服务）
                收集事件流，生成含复现步骤、修复建议、合规映射的企业级报告
```

**攻击流程（PTES 七阶段映射）**：Pre-engagement → Intelligence Gathering → Threat Modeling → Vulnerability Analysis → Exploitation → Post-Exploitation → Reporting

**设计原则**：
- Orchestrator 是唯一有状态的模块；攻击图仅作为静态规范
- 短时任务同步调用（含超时），长时/批量任务异步队列
- 阶段内所有子任务终态后方可迁移至下一阶段
- 重复防控以「重复警告」注入 LLM 输入，无独立状态
- 所有跨模块接口具备 JSON Schema，Mock 通过 schema 校验

---

## 技术栈

| 层次 | 技术 |
|------|------|
| 大模型 | GPT-4 / Claude / DeepSeek（OpenAI 兼容接口） |
| 向量数据库 | Qdrant（collection: `kb_main`） |
| Embedding | Vector Engine / OpenAI text-embedding-3-large |
| 知识爬虫编排 | OpenClaw Agent（24/7 常驻） |
| 网络搜索 | Tavily Search（主）/ Baidu Search（备） |
| 爬虫脚本 | Python 3 |
| Skill 实现 | Node.js (ESM) |

---

## 目录结构

```
ragclaw/
├── skills/                  # RAG 核心 Skill（Node.js）
│   ├── rag-query/           # 语义检索：query.mjs → Qdrant top-k
│   └── rag-ingest/          # 向量写入：ingest.mjs → chunk → embed → Qdrant
├── openclawSkills/          # OpenClaw 预置 Skill 包
│   ├── deep-research-pro/   # 深度解读与结构化摘要
│   ├── pdf-extract/         # PDF 文本抽取
│   ├── markdown-converter/  # 网页/文档转 Markdown
│   └── ...
├── rag_crawler/             # Python 知识获取流水线
│   ├── crawler.py           # 主循环：搜索 → 抓取 → LLM 精炼 → 入库
│   ├── skills_impl/         # 爬虫本地 Skill 实现
│   ├── topics.txt           # 爬取主题列表
│   ├── CONFIG.md            # 详细 API/环境变量配置说明
│   └── requirements.txt     # Python 依赖
├── kb-openclaw/             # 知识库架构文档与使命配置
│   └── mission/             # OpenClaw 系统提示词与 Runbook
├── systemPrompt/            # OpenClaw Agent 系统提示文件
├── docs/                    # 设计文档
├── 基本要求.md              # 赛题说明与量化技术指标
└── 自动化渗透测试平台-架构设计文档.md   # 完整架构设计文档
```

---

## 快速开始

### 1. 环境准备

```bash
# Python 依赖
pip install -r rag_crawler/requirements.txt

# Node.js 依赖（每个 Skill 独立）
cd skills/rag-query && npm install
cd skills/rag-ingest && npm install
```

### 2. 配置环境变量

复制并修改 `rag_crawler/env.example`：

```bash
cp rag_crawler/env.example rag_crawler/.env
```

最小配置（仅运行 RAG 爬虫）：

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
TAVILY_API_KEY=tvly-...
QDRANT_URL=http://127.0.0.1:6333
EMBED_API_KEY=...              # 可与 OPENAI_API_KEY 相同
EMBED_BASE_URL=...             # Embedding 服务地址
```

详细配置说明见 `rag_crawler/CONFIG.md`。

### 3. 启动 Qdrant

```bash
docker run -p 6333:6333 qdrant/qdrant
```

### 4. 运行知识爬虫

```bash
cd rag_crawler
python crawler.py

# 不调 LLM，仅抓取原文入库
OPENCLAW_SKIP_LLM=1 python crawler.py
```

### 5. 知识库检索

```bash
node skills/rag-query/scripts/query.mjs "Struts2 S2-045 漏洞检测"
node skills/rag-query/scripts/query.mjs --query "CVE-2017-5638" --top-k 5 --topic-tags "cve,poc"
```

---

## 知识获取流水线

OpenClaw Agent 作为持续运行的知识编排者，执行以下单轮流程：

```
rag-query（查重）→ tavily-search（发现 URL）→ url-reader / pdf-extract（抓取）
    → deep-research-pro / summarize（精炼）→ rag-ingest（写入 Qdrant）
```

`rag-ingest` 仅负责 chunk → embedding → 写入，不含抓取或清洗逻辑。

---

## 核心 API 接口（模块间通信）

| 调用方 → 被调方 | Endpoint | 关键字段 |
|---|---|---|
| Orchestrator → LLM 决策 | `POST /v1/decide` | 输入：`task_id, current_phase, target_context, history_summary`<br>输出：`action_type, skill_id, params, reasoning` |
| Orchestrator → Skill 执行 | `POST /v1/execute` | 输入：`task_id, skill_id, target, params`<br>输出：`status, parsed_artifacts, raw_stdout` |
| 任意模块 → 留痕 | `POST /v1/events` | `task_id, timestamp, event_type, source_module, payload` |
| Orchestrator → RAG 检索 | `POST /v1/retrieve` | 输入：`task_id, phase, query, target_context_snapshot`<br>输出：`chunks[]`（含 `source, type, cve_id`） |
| 任意 → 报告生成 | `POST /v1/reports/generate` | 输入：`task_id, options`<br>输出：`report_id, status, download_url` |

完整 JSON Schema 定义见 `自动化渗透测试平台-架构设计文档.md` §12。

---

## 量化技术指标

| 指标 | 基础要求 | 进阶要求 |
|------|---------|---------|
| 漏洞检测率 | ≥ 90% | ≥ 95% |
| 误报率 | ≤ 10% | ≤ 5% |
| 工具集成数量 | ≥ 30 个 | ≥ 50 个 |
| 单目标测试时间 | ≤ 30 分钟 | ≤ 15 分钟 |
| 并发测试能力 | ≥ 1 个目标 | ≥ 3 个目标 |
| 多阶段攻击支持 | 单阶段 | 多阶段链式 |
| 报告生成 | 基础报告 | 详细报告 + 修复建议 |
| 目标平台 | Linux 或 Windows | Linux + Windows |

---

## 靶机环境

平台验证覆盖以下靶机环境：

**Vulhub（简单）**：Struts2 S2-045/S2-057、ThinkPHP 5.0.23-RCE、WebLogic CVE-2023-21839、Tomcat CVE-2017-12615、PHP CVE-2019-11043、ActiveMQ CVE-2022-41678、JBoss CVE-2017-7504、Shiro CVE-2016-4437、Fastjson 1.2.24/1.2.47 RCE、Django CVE-2022-34265、Flask SSTI、GeoServer CVE-2024-36401

**Vulnhub（中等/困难）**：Tomato、Earth、Jangow、Phineas、Odin
