import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import shutil
import ipaddress
import socket


PROJECT_ROOT = Path(__file__).resolve().parent


def _get_embedding_model_name() -> str:
    return (
        os.environ.get("RAG_INGEST_EMBED_MODEL")
        or os.environ.get("OPENAI_EMBEDDING_MODEL")
        or "text-embedding-3-large"
    )


def run_cmd(
    cmd: List[str],
    *,
    input_text: Optional[str] = None,
    cwd: Optional[Path] = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess:

    env = os.environ.copy()
    if cmd and cmd[0] == "node":
        env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS") or "").strip() + " --max-old-space-size=4096"
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=str(cwd or PROJECT_ROOT),
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    return proc


def rag_query(
    topic: str,
    top_k: int = 5,
    topic_tags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    预检索
    返回 chunk 列表，每个包含 text/doc_id/source/topic_tags
    """
    script = (
        PROJECT_ROOT
        / "skills_impl"
        / "rag-query"
        / "scripts"
        / "query.mjs"
    )
    if not script.is_file():
        raise FileNotFoundError(f"rag-query script not found: {script}")

    cmd = ["node", str(script), "--query", topic, "--top-k", str(top_k)]
    if topic_tags:
        cmd += ["--topic-tags", ",".join(topic_tags)]

    proc = run_cmd(cmd)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "Collection `kb_main` doesn't exist" in err or "doesn't exist" in err:
            return []
        raise RuntimeError(f"rag-query failed: {err}")

    out = proc.stdout.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError as e:
        raise RuntimeError(f"rag-query returned invalid JSON: {e}\n{out}") from e


def rag_ingest(
    doc_id: str,
    topic_tags: List[str],
    content: str,
    source: Optional[str] = None,
    collection: str = "kb_main",
) -> None:
    """
    写入向量库
    使用 stdin 传入正文，只做写入，不做抓取与精炼
    """
    script = (
        PROJECT_ROOT
        / "skills_impl"
        / "rag-ingest"
        / "scripts"
        / "ingest.mjs"
    )
    if not script.is_file():
        raise FileNotFoundError(f"rag-ingest script not found: {script}")

    max_content = 12000
    if len(content) > max_content:
        content = content[:max_content]

    tags_arg = ",".join(topic_tags)
    cmd = [
        "node",
        str(script),
        "--doc-id",
        doc_id,
        "--topic-tags",
        tags_arg,
        "--collection",
        collection,
    ]
    if source:
        cmd += ["--source", source]

    proc = run_cmd(cmd, input_text=content)
    if proc.returncode != 0:
        raise RuntimeError(f"rag-ingest failed: {proc.stderr.strip()}")


def _get_collection_name() -> str:
    """
    统一从环境变量读取向量库 collection 名称。
    默认 kb_main，可通过 RAG_COLLECTION 覆盖，例如 kb_main_v4。
    """
    return os.environ.get("RAG_COLLECTION") or "kb_main"


def tavily_search(topic: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    联网搜索，返回结果列表

    环境变量 TAVILY_API_KEY
    文档：https://docs.tavily.com/documentation/api-reference/endpoint/search
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY 未设置，无法调用 Tavily 搜索")

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": topic,
        "max_results": max_results,
        "search_depth": "advanced",
        "topic": "general",
    }
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urlopen(req, timeout=60) as resp:
            resp_body = resp.read().decode("utf-8")
    except HTTPError as e:
        raise RuntimeError(f"Tavily HTTP error: {e.code} {e.reason}") from e
    except URLError as e:
        raise RuntimeError(f"Tavily URL error: {e.reason}") from e

    try:
        parsed = json.loads(resp_body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Tavily 返回非 JSON：{e}\n{resp_body}") from e

    results = parsed.get("results") or []
    if not isinstance(results, list):
        return []
    return results


def _find_uvx() -> Optional[str]:
    """优先 PATH"""
    exe = shutil.which("uvx")
    if exe:
        return exe
    for base in (
        os.environ.get("HOME", ""),
        "/root",
        str(Path.home()),
    ):
        if not base:
            continue
        cand = Path(base) / ".local" / "bin" / "uvx"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _fetch_html_fallback(url: str, timeout: int = 60) -> str:
    """无 uvx 或 markitdown 失败时：用 HTTP 抓取并做简单 HTML→文本。"""
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    charset = "utf-8"
    ct = resp.headers.get("Content-Type", "")
    if "charset=" in ct:
        m = re.search(r"charset=([^;\s]+)", ct, re.I)
        if m:
            charset = m.group(1).strip()
    try:
        html = raw.decode(charset, errors="replace")
    except Exception:
        html = raw.decode("utf-8", errors="replace")
    # 去标签，保留可见文本
    html = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    # 若最终仍无正文，则返回空字符串，由上游逻辑决定是否跳过该文档
    return text[:50000] if text else ""


def _is_private_address(host: str) -> bool:
    """
    判断主机是否解析到内网/本机地址，用于 SSRF 防护。
    - 直接 IP：10.x/172.16-31.x/192.168.x/127.x/链路本地等全部视为私网；
    - 域名：解析出任意一个私网/回环 IP 即视为风险。
    """
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        # host 不是直接 IP，当作域名解析
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for family, _, _, _, sockaddr in infos:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return True
        return False


def _is_poor_quality_markdown(md: str) -> bool:
    """
    粗略判断 Markdown/HTML 是否明显为骨架页或无效内容：
    - 太短；
    - 含有 Cloudflare / Please wait / enable JavaScript 等提示；
    - 几乎只有标题或重复结构。
    """
    sample = (md or "").strip()
    if len(sample) < 800:
        return True
    lowered = sample.lower()
    bad_keywords = [
        "please wait",
        "cloudflare",
        "enable javascript",
        "checking your browser",
        "access denied",
        "just a moment",
    ]
    if any(k in lowered for k in bad_keywords):
        return True
    return False


def _render_with_browser(url: str, timeout: int = 180) -> str:
    """
    调用 Node 脚本，使用无头浏览器渲染 URL 并输出 Markdown/HTML。
    """
    script = (
        PROJECT_ROOT
        / "skills_impl"
        / "url-render"
        / "scripts"
        / "render.mjs"
    )
    if not script.is_file():
        raise FileNotFoundError(f"url-render script not found: {script}")

    cmd = ["node", str(script), "--url", url]
    proc = run_cmd(cmd, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"url-render failed: {err}")
    out = (proc.stdout or "").strip()
    return out


def fetch_markdown_from_url(url: str, timeout: int = 300) -> str:
    """
    优先用 uvx markitdown 将 URL 转为 Markdown；找不到 uvx 或失败时用 HTTP+简单 HTML 降级。
    内置基础 SSRF 防护：仅允许 http/https 协议，且禁止访问内网/本机地址。
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise RuntimeError(f"不支持的 URL 协议: {parsed.scheme or '!unknown'} ({url})")
    if not parsed.hostname:
        raise RuntimeError(f"URL 缺少主机名: {url}")
    if _is_private_address(parsed.hostname):
        raise RuntimeError(f"禁止访问内网或本机地址（SSRF 防护）: {url}")
    # 1. 优先 uvx markitdown
    uvx_path = _find_uvx()
    if uvx_path:
        cmd = [uvx_path, "markitdown", url]
        try:
            proc = run_cmd(cmd, timeout=timeout)
            out = (proc.stdout or "").strip()
            if proc.returncode == 0 and out:
                # 如果看起来质量不错，直接返回
                if not _is_poor_quality_markdown(out):
                    return out
        except (FileNotFoundError, OSError, RuntimeError):
            out = ""
        # 若 uvx 返回明显无效内容，则尝试浏览器渲染
        try:
            rendered = _render_with_browser(url, timeout=timeout)
            if rendered and not _is_poor_quality_markdown(rendered):
                return rendered
        except Exception:
            # 浏览器渲染失败时继续走到 HTTP 降级
            pass

    # 2. 无 uvx 或上述步骤失败，使用无头浏览器作为高级 fallback
    try:
        rendered = _render_with_browser(url, timeout=timeout)
        if rendered and not _is_poor_quality_markdown(rendered):
            return rendered
    except Exception:
        # 3. 浏览器也失败，则退回简单 HTTP 抓取文本
        return _fetch_html_fallback(url, timeout=min(timeout, 60))

    raise RuntimeError(f"无法获取内容（多轮抓取均失败）: {url}")


def ensure_memory_dir() -> Path:
    mem_dir = PROJECT_ROOT / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir


def append_round_log(
    topic: str,
    pre_hits: int,
    urls: List[str],
    ingested_docs: List[str],
    skipped_reason: Optional[str] = None,
) -> None:
    """将每轮执行情况记录到 memory/openclaw-rag-YYYY-MM-DD.md。"""
    mem_dir = ensure_memory_dir()
    date = os.environ.get("OPENCLAW_RAG_DATE_OVERRIDE") or (
        __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    )
    log_path = mem_dir / f"openclaw-rag-{date}.md"
    now = __import__("datetime").datetime.utcnow().isoformat()
    line = (
        f"- [{now}] [round] topic={topic!r} "
        f"pre_hits={pre_hits} urls={len(urls)} "
        f"ingested={len(ingested_docs)}"
    )
    if skipped_reason:
        line += f" skipped_reason={skipped_reason}"
    if urls:
        sample = ", ".join(urls[:3])
        line += f" sample_urls={sample}"
    if ingested_docs:
        sample_doc = ", ".join(ingested_docs[:3])
        line += f" doc_ids={sample_doc}"
    line += "\n"
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        # 日志写入失败不影响主流程
        pass


def save_doc_snapshot_to_cos(
    doc_id: str,
    collection: str,
    topic_tags: List[str],
    content: str,
    source: Optional[str] = None,
) -> None:
    """
    将已写入向量库的文档快照同步到 COS，用于本地重建向量库

    依赖环境变量：
    - COS_SECRET_ID
    - COS_SECRET_KEY
    - COS_BUCKET
    - COS_REGION
    - COS_BASE_PATH（可选，作为前缀，如 rag-kb）

    若环境或 SDK 不完整，则静默跳过（仅输出 WARN），不影响主流程。
    """
    secret_id = os.environ.get("COS_SECRET_ID")
    secret_key = os.environ.get("COS_SECRET_KEY")
    bucket = os.environ.get("COS_BUCKET")
    region = os.environ.get("COS_REGION")
    base_path = (os.environ.get("COS_BASE_PATH") or "").strip().strip("/")

    if not (secret_id and secret_key and bucket and region):
        sys.stderr.write(
            "[WARN] COS 未配置（需 COS_SECRET_ID/COS_SECRET_KEY/COS_BUCKET/COS_REGION），跳过同步\n",
        )
        return

    try:
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore
    except Exception:  # noqa: BLE001
        sys.stderr.write(
            "[WARN] 未安装 qcloud_cos SDK，跳过 COS 同步（pip install cos-python-sdk-v5）\n",
        )
        return

    try:
        config = CosConfig(
            Region=region,
            SecretId=secret_id,
            SecretKey=secret_key,
            Token=None,
            Scheme="https",
        )
        client = CosS3Client(config)

        now = datetime.utcnow()
        date_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        y = now.strftime("%Y")
        m = now.strftime("%m")
        d = now.strftime("%d")

        embedding_model = _get_embedding_model_name()
        snapshot: Dict[str, Any] = {
            "doc_id": doc_id,
            "collection": collection,
            "topic_tags": topic_tags,
            "source": source,
            "content": content,
            "meta": {
                "ingested_at": date_str,
                "embedding_model": embedding_model,
                "version": 1,
            },
        }
        body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")

        safe_doc_id = doc_id.replace("/", "_")
        parts = [p for p in [base_path, "docs", collection, y, m, d] if p]
        key_prefix = "/".join(parts)
        key = f"{key_prefix}/{safe_doc_id}.json"

        client.put_object(
            Bucket=bucket,
            Body=body,
            Key=key,
        )
    except Exception as e:  # noqa: BLE001
        # COS 同步失败不影响主流程
        sys.stderr.write(f"[WARN] 同步 COS 失败: doc_id={doc_id} ({e})\n")


def slugify(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum():
            keep.append(ch.lower())
        elif ch in "-_":
            keep.append(ch)
        elif ch.isspace():
            keep.append("-")
    slug = "".join(keep).strip("-")
    return slug[:50] or "doc"


def has_llm() -> bool:
    """是否使用 LLM（规划/精炼）。OPENCLAW_SKIP_LLM=1 时强制不用，避免 503 等导致整条失败。"""
    if os.environ.get("OPENCLAW_SKIP_LLM", "").strip() in ("1", "true", "yes"):
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))


def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """
    调用 OpenAI 兼容 chat completions 接口
    依赖环境变量：OPENAI_API_KEY，可选 OPENAI_BASE_URL、OPENAI_MODEL
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未设置，无法调用 LLM")

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.2")
    url = f"{base_url.rstrip('/')}/chat/completions"

    #强制要求 stream=true
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = Request(url, data=data, headers=headers)
    try:
        with urlopen(req, timeout=120) as resp:
            buf = []
            for line in resp:
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str or not line_str.startswith("data:"):
                    continue
                payload = line_str[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    part = delta.get("content")
                    if part:
                        buf.append(part)
            resp_body = "".join(buf)
    except HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        err_preview = (err_body[:500] + "…") if len(err_body) > 500 else err_body
        msg = (
            f"LLM HTTP {e.code} {e.reason} | "
            f"url={base_url}…/chat/completions | "
            f"body={err_preview!r}"
        )
        raise RuntimeError(msg) from e
    except URLError as e:
        reason = getattr(e.reason, "args", None) or e.reason
        raise RuntimeError(f"LLM URL error: {e.reason} | detail={reason}") from e

    if not resp_body.strip():
        raise RuntimeError("LLM 流式返回内容为空")
    return resp_body


def _safe_json_extract(raw: str) -> Any:
    """
    从 LLM 流式结果中提取最外层 JSON 对象。
    通过寻找第一个 { 和最后一个 }，中间用 json.loads 解析。
    """
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(f"LLM 结果不含 JSON：{text[:200]}...")
    return json.loads(text[start : end + 1])


def plan_topic(topic: str, pre_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    使用 LLM 根据预检索结果规划子主题和完成度。

    返回结构示例：
    {
      "completion": "low|medium|high",
      "subtopics": [
        {"query": "...", "max_results": 8, "need_more": true}
      ]
    }
    """
    if not has_llm():
        # 无 LLM 时，退化为单一子主题
        return {
            "completion": "unknown",
            "subtopics": [
                {"query": topic, "max_results": 10, "need_more": True},
            ],
        }

    summary_chunks = []
    for ch in pre_chunks[:5]:
        text = ch.get("text") or ""
        doc_id = ch.get("doc_id") or ""
        summary_chunks.append(
            {
                "doc_id": doc_id,
                "text": text[:400],
                "topic_tags": ch.get("topic_tags") or [],
            },
        )

    sys_prompt = (
        "你是一个为渗透测试知识库做规划的助手。"
        "根据当前主题与已有知识片段，判断目前覆盖程度，"
        "并拆解出若干需要补充的子查询。"
        "你必须只输出一个合法的 JSON 对象，禁止添加任何解释性文字或前后缀。"
    )
    user_prompt = (
        "当前主题："
        + topic
        + "\n\n"
        + "已有知识片段（最多 5 条）：\n"
        + json.dumps(summary_chunks, ensure_ascii=False)
        + "\n\n"
        "请输出 JSON，字段：\n"
        '{\n  "completion": "low|medium|high",\n'
        '  "subtopics": [\n'
        '    {"query": "子主题查询语句", "max_results": 10, "need_more": true}\n'
        "  ]\n}\n"
        "completion 代表当前主题总体覆盖度，subtopics 中 need_more 为 false 表示该子主题已基本覆盖可选跳过。"
    )
    raw = call_llm(sys_prompt, user_prompt, max_tokens=1024)
    try:
        data = _safe_json_extract(raw)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"LLM 规划结果 JSON 解析失败：{e}\n{raw}") from e
    return data


def clean_markdown_with_llm(topic: str, url: str, markdown: str) -> str:
    """
    使用 LLM 对完整 Markdown 做去噪清洗，输出纯文本/轻量 Markdown。
    - 移除导航、页脚、侧边栏、评论等公共模板；
    - 保留正文标题、小节结构与关键信息；
    - 严格禁止输出解释性前后缀、JSON 或代码块围栏。
    """
    if not has_llm():
        return markdown

    # 先做一次 Python 预处理：粗粒度去污，节省 LLM 上下文
    def _preclean_markdown_for_llm(raw: str) -> str:
        """
        LLM 前置兜底预处理：
        - 解码常见 HTML 实体与转义；
        - 移除或压缩 URL（保留可读锚文本）；
        - 压缩连续空行；
        - 严格避免破坏代码块与 Markdown 结构。
        """
        text = raw
        # 全局替换常见实体（对代码和正文都相对安全）
        replacements = {
            "&nbsp;": " ",
            "&ensp;": " ",
            "&emsp;": " ",
            "&lt;": "<",
            "&gt;": ">",
            "&amp;": "&",
            "&quot;": '"',
            "&#39;": "'",
        }
        for _k, _v in replacements.items():
            text = text.replace(_k, _v)

        # 行级处理：在非代码块中删除 URL、收缩链接
        lines = text.splitlines()
        out_lines = []
        in_code = False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_code = not in_code
                out_lines.append(line)
                continue
            if not in_code:
                # Markdown 链接：[标题](url) -> 保留标题，移除 url
                line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
                # 裸露 URL：直接移除，保留周边文本
                line = re.sub(r"https?://\S+", " ", line)
                line = re.sub(r"\bwww\.[^\s)]+", " ", line)
            out_lines.append(line)

        cleaned = "\n".join(out_lines)
        # 压缩连续 3 行以上空行为 2 行，避免破坏表格/缩进
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    # 控制长度，避免超长上下文，并做预清洗
    max_len = int(os.environ.get("OPENCLAW_RAG_CLEAN_MAX_CHARS", "20000"))
    text = _preclean_markdown_for_llm(markdown[:max_len])

    sys_prompt = (
        "你是一个为 RAG 知识库做“高价值信息筛选 + 严格去噪与格式规范化”的专家助手。\n"
        "现在给你一段已经经过初步预处理的 Markdown/HTML 文本，你需要完成两件事：\n"
        "1）在不破坏技术细节的前提下，进一步删除与正文无关的噪音；\n"
        "2）将剩余内容整理成结构化且合规的 Markdown，并在文档顶部补充标准化元数据。\n\n"
        "【需要优先保留的高价值内容】包括但不限于：\n"
        "- 概念/术语定义、背景说明、原理解释；\n"
        "- 完整的步骤、操作流程、方法论；\n"
        "- 明确的结论、规则、注意事项、最佳实践；\n"
        "- 与主题直接相关的代码块、配置示例、命令行、接口说明；\n"
        "- 常见问题及解决方案、典型案例；\n\n"
        "【需要尽量删除或弱化的噪音】包括但不限于：\n"
        "- 页面导航、目录、面包屑、“返回顶部”等；\n"
        "- 纯粹的运营/推广信息，如关注公众号、点赞投币、广告文案；\n"
        "- 版权声明、备案号、站点通用页脚；\n"
        "- 作者头像、无意义的图片说明（例如头像、二维码、表情包）；\n"
        "- 与主题弱相关的闲聊、吐槽、无信息量开场白。\n\n"
        "【数据保真度（Fidelity）硬约束】：\n"
        "- 任何被 ``` 包裹的代码块、命令行、SQL、Payload、Shellcode、报错堆栈等，"
        "其内部字符、参数与语法一律禁止修改、重排或“优化”，即使看起来像乱码或恶意代码；\n"
        "- 禁止合并、删减、重写代码块中的内容，禁止将多个独立代码块合并为一个；\n"
        "- 禁止对正文技术内容做主观“总结式压缩”，技术前提条件、配置项、参数说明、报错信息必须逐字保留。\n\n"
        "【转义字符与脏格式处理】：\n"
        "- 清理明显无意义的转义/控制符号，比如 HTML 实体（&nbsp;、&gt;、&lt; 等）和杂乱的 \\r、\\t 等；\n"
        "- 避免在正文中输出看起来像转义残留的噪声（如大量的 &#x25;、\\u00A0 等）。\n\n"
        "【指令与代码的格式约束】：\n"
        "- 命令行、脚本、SQL、配置示例等，应尽量用 Markdown 代码块 ``` 包裹，并在可能时补充语言标识（如 ```bash、```python、```sql）；\n"
        "- 代码块必须语法成对、不能只开不关，也不能嵌套多层 ``` 导致解析错误；\n"
        "- 步骤/操作指令建议用有序/无序列表表示，避免散落在长段落中难以解析。\n\n"
        "【格式和结构要求】：\n"
        "- 必须保留 Markdown 的标题层级（#、##、### 等）、列表、代码块等结构，不允许把标题与其直属正文拆散；\n"
        "- 只删除明显无价值的段落或行，宁可稍微冗余，也不要误删重要知识；\n"
        "- 严禁对正文进行“摘要式缩写”，必须以原文为主、微调结构为辅；\n"
        "- 输出必须是规范的 Markdown 文本：标题、列表缩进、代码块围栏都要成对闭合。\n\n"
        "【元数据（Metadata）输出要求】：\n"
        "- 在清洗后的正文最顶部，先输出一个 JSON 元数据代码块，格式严格为：\n"
        "```json\n"
        "{\n"
        f'  \"title\": \"{topic}\",\n'
        f'  \"source_url\": \"{url}\",\n'
        "  \"language\": \"zh\",\n"
        "  \"tags\": [],\n"
        "  \"doc_type\": \"web_article\"\n"
        "}\n"
        "```\n"
        "- 代码块后空一行，再输出正文 Markdown；\n"
        "- 严禁在元数据或正文之外添加任何解释性前后缀。\n"
    )
    user_prompt = (
        f"主题：{topic}\nURL：{url}\n\n"
        "请根据上述要求清洗下面的文本，直接输出清洗后的正文：\n\n"
        + text
    )
    raw = call_llm(sys_prompt, user_prompt, max_tokens=4096)
    cleaned = raw.strip()
    # 简单裁剪常见总结语（若模型仍然违规加了总结句）
    for tail in ("以上是", "以上内容", "总结："):
        idx = cleaned.rfind(tail)
        if idx != -1 and len(cleaned) - idx < 40:
            cleaned = cleaned[:idx].rstrip()
            break
    return cleaned or markdown


def _smart_chunk_python(
    text: str,
    min_chars: int = 400,
    max_chars: int = 1200,
    overlap: int = 200,
) -> List[str]:
    """
    改进版本地分块：
    - 优先按双换行/标题/句号分段；
    - 控制长度并支持 overlap；
    - 仅作为 LLM 分块失败时的兜底。
    """
    if not text.strip():
        return []
    chunks: List[str] = []
    n = len(text)
    start = 0

    def find_break(s: int, e: int) -> int:
        segment = text[s:e]
        # 标题或段落边界
        for pat in ("\n\n#", "\n\n", "\r\n\r\n"):
            idx = segment.rfind(pat)
            if idx != -1 and s + idx >= s + min_chars:
                return s + idx + len(pat)
        # 句子边界
        for p in ["。", "！", "？", ".", "!", "?"]:
            idx = segment.rfind(p)
            if idx != -1 and s + idx >= s + min_chars:
                return s + idx + 1
        # 单词边界
        idx = segment.rfind(" ")
        if idx != -1 and s + idx >= s + min_chars:
            return s + idx + 1
        return e

    while start < n:
        max_end = min(n, start + max_chars)
        end = find_break(start, max_end)
        frag = text[start:end].strip()
        if frag:
            chunks.append(frag)
        if end >= n:
            break
        start = max(end - overlap, 0)

    return chunks


def chunk_with_llm(clean_text: str) -> List[Dict[str, str]]:
    """
    使用 LLM 按语义做分块，输出结构化的 chunks：
    [{"index": 0, "title": "...", "text": "..."}, ...]
    若 LLM 不可用或输出不合法，则回退到本地分块。
    """
    clean_text = (clean_text or "").strip()
    if not clean_text:
        return []

    # 若顶部存在 JSON 元数据代码块，先解析并保存，避免被误分块
    meta: Optional[Dict[str, Any]] = None
    body_text = clean_text
    m = re.match(r"^```json\s*\n([\s\S]*?)\n```(?:\s*\n+)?", clean_text)
    if m:
        meta_raw = m.group(1).strip()
        try:
            parsed = json.loads(meta_raw)
            if isinstance(parsed, dict):
                meta = parsed
        except Exception:
            meta = None
        body_text = clean_text[m.end() :].lstrip()

    # LLM 不可用时，直接本地分块（并把文档级 meta 挂到每个 chunk）
    if not has_llm():
        local_chunks = _smart_chunk_python(body_text or clean_text)
        return [
            {"index": i, "title": "", "text": ch, "meta": meta or {}}
            for i, ch in enumerate(local_chunks)
        ]

    max_len = int(os.environ.get("OPENCLAW_RAG_CHUNK_MAX_CHARS", "20000"))
    text = (body_text or clean_text)[:max_len]

    sys_prompt = (
        "你是“知识库高级语义分块专家”，负责将已经清洗好的 Markdown 文本切分为"
        "适合向量数据库存储与检索的语义 Chunk。\n\n"
        "【总体目标】：\n"
        "- 保证每个 Chunk 语义完整、上下文连贯，便于后续 RAG 检索；\n"
        "- 严格遵守标题绑定、代码块完整、列表连贯等规则；\n"
        "- 每个 Chunk 推荐长度 300–800 字左右，最大不要超过约 1500 字（可以有少量浮动）。\n\n"
        "【1. 标题绑定原则（Heading Attachment）】\n"
        "- 禁止将 Markdown 标题（#、##、### 等）与其直属的第一个正文段落拆分到不同 Chunk；\n"
        "- 每遇到一个新标题，必须把“该标题 + 至少一个紧随其后的正文段落”放进同一个 Chunk；\n"
        "- 若该标题下内容太长需要拆分为多个 Chunk，后续 Chunk 开头必须重复该层级标题，或加上“（续）”这类说明，"
        "例如：\"### 3. 渗透测试执行标准（续）\"。\n\n"
        "【2. 代码块与格式完整性（Block Integrity）】\n"
        "- 被 ``` 包裹的代码、JSON 或其它格式化文本，优先整体放在同一个 Chunk 中；\n"
        "- 如果单个代码块长度超过最大限制，只能在自然逻辑断点（如空行、函数结束处）切分，"
        "并在切分后的每一段前后补齐 ``` 开始/结束标记（语言标识保持一致，如 ```python）。\n"
        "- 禁止在字符串中间、变量名中间或单行代码中途硬截断。\n\n"
        "【3. 语义边界优先级（Semantic Boundaries）】\n"
        "- 寻找切分点时，应按以下优先级选择：\n"
        "  1）双换行（\\n\\n）；2）单换行（\\n）；3）句号/问号/叹号（。！？.?!）；4）分号（；;）。\n"
        "- 绝对禁止在句子中间、专业术语（如 Payload、RAG、SQL 注入等）或英文单词内部做物理截断。\n\n"
        "【4. 列表连贯性（List Continuity）】\n"
        "- 对于有序/无序列表或者步骤（1. 2. / ① ② 等），尽量让同一组完整列表落在同一个 Chunk 内；\n"
        "- 若因为长度限制必须拆分列表，则后续 Chunk 开头要补充上下文说明，"
        "例如“继续上文的『3. 常见攻击方式』列表：”或“下面是步骤 4–6，承接上一个 Chunk 中的步骤 1–3：”。\n\n"
        "【5. 冗余清洗】\n"
        "- 假设文本已经做过初步去噪，本步骤只在发现明显残留的页脚、导航等噪声时，适度忽略这些内容；\n"
        "- 不要误删正文内容。\n\n"
        "【元数据处理】：\n"
        "- 文本开头如果有一个 JSON 元数据代码块（```json ... ```），视为整篇文档的全局元数据；\n"
        "- 分块时不要修改该 JSON 的内容，允许将其视为一个单独的首块，或在需要时略过不切分；\n\n"
        "【输出格式】\n"
        "- 你必须只输出一个合法的 JSON 对象："
        '{"chunks":[{"index":0,"title":"...","text":"..."},...]}；\n'
        "- index 必须从 0 开始递增；title 为当前 Chunk 的主题/小标题（可以为空字符串）；\n"
        "- text 是该 Chunk 的完整 Markdown 文本；\n"
        "- 禁止输出任何解释性文字、注释或额外字段。\n"
    )
    user_prompt = (
        "下面是一段已经做过去噪和高价值筛选的 Markdown 文本（若顶部带有 JSON 元数据代码块，调用方已单独解析保存），"
        "请按照系统提示中的规则进行语义分块。\n"
        "注意：\n"
        "- index 必须从 0 开始递增；\n"
        "- title 可为空字符串，也可以复用块内最重要的小标题，或在续篇 Chunk 上加“（续）”；\n"
        "- text 必须是该 Chunk 的完整正文（遵守标题绑定、代码块完整、列表连贯和语义边界优先级）；\n"
        "- 只输出 JSON 对象本身，不要输出任何 JSON 外的字符。\n\n"
        "待分块文本：\n\n"
        + text
    )

    def try_llm_once() -> Optional[List[Dict[str, str]]]:
        raw = call_llm(sys_prompt, user_prompt, max_tokens=4096)
        data = _safe_json_extract(raw)
        chunks = data.get("chunks")
        if not isinstance(chunks, list):
            raise RuntimeError("LLM 分块结果缺少 chunks 数组")
        norm: List[Dict[str, str]] = []
        for item in chunks:
            if not isinstance(item, dict):
                continue
            t = str(item.get("text") or "").strip()
            if not t:
                continue
            title = str(item.get("title") or "").strip()
            norm.append(
                {
                    "index": int(item.get("index") or len(norm)),
                    "title": title,
                    "text": t,
                    "meta": meta or {},
                },
            )
        if not norm:
            return None
        # 重新按 index 排序并重编号，防止 LLM 乱序
        norm.sort(key=lambda x: x["index"])
        for i, ch in enumerate(norm):
            ch["index"] = i
        return norm

    try:
        chunks = try_llm_once()
        if chunks:
            return chunks
    except Exception:
        # 允许一次重试
        try:
            chunks = try_llm_once()
            if chunks:
                return chunks
        except Exception:
            pass

    # 彻底失败时回退到本地分块
    local_chunks = _smart_chunk_python(body_text or clean_text)
    return [
        {"index": i, "title": "", "text": ch, "meta": meta or {}}
        for i, ch in enumerate(local_chunks)
    ]


def refine_content(topic: str, url: str, markdown: str) -> str:
    """
    使用 LLM 对单篇 Markdown 文本做语义精炼，保留安全相关核心信息。
    无 LLM 时直接返回原文。
    """
    if not has_llm():
        return markdown

    sys_prompt = (
        "你是一个安全知识库构建助手，负责将原始 Markdown 文本精炼为适合 RAG 的片段。"
        "必须保留渗透测试与漏洞利用相关的关键信息：CVE 编号、影响范围、利用条件、完整复现步骤、"
        "关键命令、修复建议、重要配置项等。可以压缩冗余叙述，但禁止删除这些关键事实。"
    )
    user_prompt = (
        f"主题：{topic}\nURL：{url}\n\n"
        "请根据上述规则，对以下 Markdown 文本进行精炼，输出仍为 Markdown：\n\n"
        + markdown[:8000]
    )
    refined = call_llm(sys_prompt, user_prompt, max_tokens=2048)
    return refined


def process_topic(
    topic: str,
    *,
    max_urls: int = 5,
    top_k_rag: int = 5,
    max_chars_per_doc: int = 8000,
    topic_tags: Optional[List[str]] = None,
) -> None:
    """
    对一个主题执行完整流水线：
    预检索（rag-query）→ LLM 规划子主题 → Tavily 搜索 → 抓取 & 精炼 → rag-ingest 写库。
    """
    tags = topic_tags or []

    # 预检索（rag-query）
    pre_chunks = rag_query(topic, top_k=top_k_rag, topic_tags=tags)
    pre_hits = len(pre_chunks)

    # 规划（LLM）：根据已有知识判断完成度与子查询
    try:
        plan = plan_topic(topic, pre_chunks)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[WARN] plan_topic 失败，退化为单一查询：{e}\n")
        plan = {
            "completion": "unknown",
            "subtopics": [
                {"query": topic, "max_results": max_urls, "need_more": True},
            ],
        }

    subtopics = plan.get("subtopics") or []
    urls: List[str] = []

    # Tavily 搜索（按子主题）
    for sub in subtopics:
        if not sub.get("need_more", True):
            continue
        q = sub.get("query") or topic
        sub_max = int(sub.get("max_results") or max_urls)
        sub_max = min(sub_max, max_urls)
        try:
            results = tavily_search(q, max_results=sub_max)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[WARN] Tavily 搜索失败: {q} ({e})\n")
            continue
        for item in results:
            url = item.get("url")
            if not url:
                continue
            if url in urls:
                continue
            urls.append(url)
            if len(urls) >= max_urls:
                break
        if len(urls) >= max_urls:
            break

    ingested_docs: List[str] = []

    # 抓取 & 清洗 & 分块 & 写库
    for idx, url in enumerate(urls):
        try:
            md = fetch_markdown_from_url(url)
            if not md.strip():
                continue
            # 先用 LLM 做去噪清洗，得到更接近正文的文本
            try:
                cleaned = clean_markdown_with_llm(topic, url, md)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[WARN] clean_markdown_with_llm 失败，使用原始 Markdown: {url} ({e})\n")
                cleaned = md

            if len(cleaned) > max_chars_per_doc:
                cleaned = cleaned[:max_chars_per_doc]

            # 使用 LLM/本地逻辑做语义分块
            try:
                chunk_structs = chunk_with_llm(cleaned)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[WARN] chunk_with_llm 失败，退回本地分块: {url} ({e})\n")
                local_chunks = _smart_chunk_python(cleaned)
                chunk_structs = [
                    {"index": i, "title": "", "text": ch}
                    for i, ch in enumerate(local_chunks)
                ]

            if not chunk_structs:
                continue

            # 组装为带分隔符的大字符串，交给 rag_ingest；ingest.mjs 将识别分隔符并按块写入
            parts: List[str] = []
            for ch in chunk_structs:
                title = ch.get("title") or ""
                text = ch.get("text") or ""
                block = title + ("\n" if title and not title.endswith("\n") else "")
                block += text
                parts.append(block.strip())
            joined = "\n<<CHUNK>>\n".join(parts)

            doc_slug = slugify(f"{topic}-{idx}")
            doc_id = f"web-{doc_slug}"
            full_tags = tags + ["web", "auto", slugify(topic)]
            collection_name = _get_collection_name()
            rag_ingest(
                doc_id=doc_id,
                topic_tags=full_tags,
                content=joined[:max_chars_per_doc],
                source=url,
                collection=collection_name,
            )
            # 将文档快照同步到 COS，便于之后在本地重建向量库
            save_doc_snapshot_to_cos(
                doc_id=doc_id,
                collection=collection_name,
                topic_tags=full_tags,
                content=cleaned,
                source=url,
            )
            ingested_docs.append(doc_id)
        except Exception as e:  # noqa: BLE001
            # 不中断其他 URL
            sys.stderr.write(f"[WARN] process url failed: {url} ({e})\n")

    append_round_log(
        topic=topic,
        pre_hits=pre_hits,
        urls=urls,
        ingested_docs=ingested_docs,
        skipped_reason=None,
    )


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="RAG 知识库爬虫",
    )
    parser.add_argument("--topic", help="单次运行的主题")
    parser.add_argument("--topics-file", help="按行读取主题列表的文件路径")
    parser.add_argument(
        "--max-urls",
        type=int,
        default=5,
        help="每个主题最多处理的 URL 数量",
    )
    args = parser.parse_args(argv)

    topics: List[str] = []
    if args.topic:
        topics.append(args.topic)
    if args.topics_file:
        path = Path(args.topics_file)
        if not path.is_file():
            raise FileNotFoundError(f"topics file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if t and not t.startswith("#"):
                topics.append(t)

    if not topics:
        raise SystemExit("必须通过 --topic 或 --topics-file 提供至少一个主题")

    for t in topics:
        sys.stderr.write(f"[INFO] process topic: {t}\n")
        process_topic(t, max_urls=args.max_urls)


if __name__ == "__main__":
    main()

