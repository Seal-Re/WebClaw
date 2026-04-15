# RAG 知识库 · 最终设计（OpenClaw 驱动）

## 一、目标与原则

- **目标**：由 OpenClaw 作为唯一编排者，用 Agent 控制各 Skill，实现 24/7 知识获取、解读与入库；知识库同时反哺 OpenClaw，提升搜索与解读质量。
- **原则**：容错优先、能由 Agent 选的都交给 Agent；固定流水线脚本仅保留“必须程序化”的部分（如纯 embedding+写入，OpenClaw 无此能力）。

---

## 二、最终架构

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                  OpenClaw Agent (24/7)                     │
                    │  系统提示词：知识获取使命 + 可用 Skill 说明 + 检索优先    │
                    └─────────────────────────────────────────────────────────┘
                                         │
         ┌───────────────────────────────┼───────────────────────────────┐
         ▼                               ▼                               ▼
  ┌──────────────┐              ┌──────────────┐              ┌──────────────┐
  │ 发现与抓取   │              │ 解读与精炼   │              │ 检索与入库   │
  │ · tavily-    │              │ · deep-      │              │ · rag-query  │
  │   search     │ ── URL ──▶   │   research-  │ ── 文本 ──▶   │   (查知识库) │
  │ · baidu-     │              │   pro        │               │ · rag-ingest │
  │   search     │              │ · summarize  │               │   (写知识库) │
  │ · url-reader │              │ · literature-│               │   (仅写模式) │
  │ · markdown-  │              │   review     │               │              │
  │   converter  │              │ · (其他你提   │               │              │
  │ · pdf-       │              │   供的 skill)│               │              │
  │   extract    │              │              │               │              │
  └──────────────┘              └──────────────┘               └──────────────┘
         │                               │                               │
         └───────────────────────────────┼───────────────────────────────┘
                                         ▼
                    ┌─────────────────────────────────────────────────────────┐
                    │  Qdrant (kb_main)  +  Vector Engine (embedding)          │
                    └─────────────────────────────────────────────────────────┘
```

- **发现与抓取**：Agent 按主题/任务调用 **tavily-search**（或 baidu-search）拿到 URL 列表；对每个 URL 按需选 **url-reader** / **markdown-converter**（网页）或 **pdf-extract**（PDF），拿到原始文本。
- **解读与精炼**：对抓到的页面/PDF 文本，Agent 调用 **deep-research-pro** / **summarize** / **literature-review** 等做解读与结构化；输出为“可入库”的干净摘要/要点/问答（你现有 ingest 里的**提示词可迁移到 Agent 系统提示或单独解读 Skill 的说明**）。
- **检索与入库**：
  - **rag-query**：根据当前主题或用户问题查 Qdrant，把 top-k 片段注入 Agent 上下文，用于“先看知识库再搜/再解读”。
  - **rag-ingest**：只保留“**写入**”能力——接收 Agent 已解读好的文本（或结构化 JSON），做 **embedding + 写入 Qdrant**；不再在 ingest 内做“抓取 + 规则清洗”，由 Agent 用上述 Skill 完成。

---

## 三、rag-ingest 的最终定位

- **OpenClaw 不具备原生 embedding 与向量写入能力**，因此必须保留一个“入库”Skill，内部调用 Vector Engine + Qdrant。
- **最终决定**：
  - **保留** `rag-ingest` 这个名字与 Skill 注册，但将职责**彻底收缩为“仅写入”模式**：
    - 输入：`--doc-id`、`--topic-tags`、可选 `--source`（来源标识）以及 **`--content` / stdin**（Agent 已解读好的正文）。
    - 行为：**只做** chunk → embedding → 写入 Qdrant；**不再**在 ingest 内做任何“URL 抓取 + 规则清洗 + 精炼”逻辑。
  - 抓取与精炼全部前移到 OpenClaw 流程，由 Agent 按需调用 **url-reader / markdown-converter / pdf-extract / deep-research-pro / summarize / literature-review** 等 Skill 完成，rag-ingest 仅作为“向量库写入器”存在。

---

## 四、知识库如何反哺 OpenClaw

- **检索优先**：在 Agent 开始一轮“搜索 + 解读 + 入库”前，先根据本轮主题（或用户问题）调用 **rag-query**，将返回的 top-k 片段写入系统消息或上下文，使 Agent“带着已有知识”去搜和读，减少重复、提升针对性。
- **可选**：在“解读”步骤前，对当前 URL 或当前主题再查一次 rag-query，把相关旧知识一并交给 deep-research/summarize，使解读更连贯、少重复入库。

实现上需要新增一个 **rag-query** Skill（见下），由 OpenClaw 在适当时机调用。

---

## 五、你已安装 Skill 的用法归纳

| Skill | 在最终设计中的角色 |
|-------|---------------------|
| **tavily-search** / **tavily** | 联网发现 URL（主用 tavily-search）。 |
| **baidu-search** | 备用或国内主题发现。 |
| **url-reader** | 按 URL 拉取网页正文。 |
| **markdown-converter** | 网页/PDF/Office 转 Markdown（uvx markitdown）。 |
| **pdf-extract** | 纯 PDF 抽文本（pdftotext），Agent 遇 PDF 时选用。 |
| **deep-research-pro** | 对单页/多源做深度解读与报告，输出结构化摘要，再交给入库。 |
| **summarize** | 对长文做摘要，可与 deep-research 配合。 |
| **literature-review** | 文献/技术类解读，适合论文与长文档。 |
| **trend-watcher** | 趋势与热点，可驱动“搜什么主题”。 |
| **elite-longterm-memory** / **memory-setup** | Agent 自身记忆，与知识库分离；知识库专注 RAG。 |
| **rag-ingest** | 仅负责 embedding + 写 Qdrant（及可选的“给 URL 时全链路”兜底）。 |
| **rag-query**（待建） | 按 query 查 Qdrant，返回片段，供 Agent 增强上下文。 |
| 其他（obsidian、notion、github、playwright-mcp、agent-browser 等） | 按你后续提供的用法，在系统提示词或 runbook 中写明何时调用。 |

---

## 六、要落地的具体改动

1. **OpenClaw 24/7 运行与使命**
   - 用 system prompt 或独立“使命文件”明确：当前任务是“按主题列表/周期进行知识获取”；先 **rag-query** 再 **tavily-search**；对每个 URL 按类型选 **pdf-extract** / **markdown-converter** / **url-reader**；用 **deep-research-pro** / **summarize** / **literature-review** 解读；最后用 **rag-ingest** 入库。
   - 部署上：OpenClaw 以 daemon/进程方式 24/7 跑，或由 cron/systemd 定时拉起并注入“本轮主题”参数（若 OpenClaw 支持）。

2. **新增 rag-query Skill**
   - 输入：query 字符串（及可选 top_k、topic_tags 过滤）。
   - 行为：用 Vector Engine 对 query 做 embedding，在 Qdrant `kb_main` 中检索，返回 payload（含 `text`、`doc_id`、`source` 等）。
   - 供 OpenClaw 在“搜索前/解读前”调用，实现“借助知识库强化搜索与解读”。

3. **rag-ingest 精简为仅写入模式**
   - 删除原有“给 URL 全链路”能力（抓取 + 精炼 + embedding + 写入），不再在脚本内部做任何网络抓取与 LLM 精炼。
   - 仅保留：“接受已解读正文（`--content` 或 stdin）→ 智能 chunk → Embedding → 写入 Qdrant”，用于承接 OpenClaw 在前面步骤完成的解读结果。

4. **提示词迁移**
   - 原先 `ingest.mjs` 中用于 web/pdf/markdown/code/video 的精炼提示词，不再由 rag-ingest 使用；相关提示迁移到 OpenClaw 侧（系统提示词或解读类 Skill 的说明），由 Agent 决定何时、用什么 prompt 来做 deep-research / summarize。

5. **后续你提供更多 Skill 时**
   - 在本文档或 OpenClaw 的 runbook 中补充“何时调用、输入输出”，并纳入 Agent 系统提示，使 24/7 流程自动选 Skill。

---

## 七、实施顺序建议

1. **先做**：新增并接好 **rag-query** Skill；将 **rag-ingest** 精简为仅写入模式（`--content` / stdin），删除 URL 全链路与内部精炼逻辑。
2. **再做**：编写并启用 OpenClaw 的**知识获取使命**系统提示词（或 runbook），明确调用顺序与可用 Skill 列表。
3. **最后**：在实际项目中完全采用 OpenClaw 驱动的流水线（检索 → 搜索 → 抓取 → 解读 → 仅写入入库），不再依赖任何固定 Python 脚本作为主入口。

以上为最终设计；后续实现以本文档为准，直接按此推翻原“仅固定流水线”的用法并逐步迁移。

---

## 八、OpenClaw 知识获取使命提示词（可直接粘贴）

以下内容可作为 OpenClaw 的**系统提示词**或**常驻任务描述**，用于 24/7 知识获取。你只需在 OpenClaw 中新建会话或配置“使命”时粘贴，并确保已安装并注册 rag-query、rag-ingest、tavily-search、url-reader、markdown-converter、pdf-extract、deep-research-pro、summarize、literature-review 等 Skill。

```
你是一个 24/7 运行的知识库构建 Agent。你的唯一使命是：按给定主题持续发现、解读并写入 RAG 知识库（Qdrant），且每次行动前先查知识库以利用已有知识。

## 可用 Skill（必须按需调用）

- **rag-query**：查知识库。先调用它，传入当前主题或问题，拿到 top-k 相关片段，再决定搜什么、读什么。
  - 例：`rag-query "渗透测试流程" --top-k 5` 或 `rag-query --query "TCP/IP 模型" --topic-tags net_basic`
- **tavily-search** / **tavily**：联网搜索，得到 URL 列表。
- **url-reader** / **markdown-converter**：按 URL 抓网页并转 Markdown；遇 PDF 用 **pdf-extract**。
- **deep-research-pro** / **summarize** / **literature-review**：对抓到的正文做深度解读与结构化摘要。
- **rag-ingest**：写入知识库。只接收已解读正文，做 chunk + embedding + 写入，不抓取、不精炼。
  - 例：`rag-ingest --doc-id <id> --topic-tags <主题> --content "解读后的正文"` 或 `echo "正文" | rag-ingest --doc-id <id> --topic-tags <主题>`

## 单轮流程（对每个主题重复）

1. 用 **rag-query** 查当前主题，把返回的片段纳入上下文。
2. 用 **tavily-search** 搜索该主题，得到一批 URL。
3. 对每个 URL：若为 PDF 则用 **pdf-extract**，否则用 **url-reader** 或 **markdown-converter** 拿正文；若失败可重试或跳过。
4. 对拿到的正文用 **deep-research-pro** 或 **summarize** / **literature-review** 做解读，得到“可入库”的摘要/要点。
5. 用 **rag-ingest** 将解读结果写入知识库（优先用 --content 或 stdin 的“仅写入”模式，避免重复抓取）。

## 主题来源

- 若本轮有指定主题列表，则按列表依次执行上述流程。
- 若未指定，则从预设知识领域（如：渗透测试、计算机网络、Web 安全、CVE、CTF writeup、工具使用）中选一个主题执行一轮，再进入下一轮。
```

将上述提示词放入 OpenClaw 后，Agent 会按“检索 → 搜索 → 抓取 → 解读 → 入库”循环；你只需保证 OpenClaw 常驻或定时拉起，并配置好 QDRANT_URL、Vector Engine（EMBED_*）、OPENAI 等环境变量即可。
