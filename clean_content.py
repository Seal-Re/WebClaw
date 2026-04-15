#!/usr/bin/env python3
"""
清洗脚本：对原始 HTML / 文本做规范化，输出适合 RAG 入库的纯文本。

用法：
  # 从文件
  python3 clean_content.py input.html
  python3 clean_content.py --type html raw.html > clean.txt

  # 从 stdin
  cat page.html | python3 clean_content.py --type html
  curl -s URL | python3 clean_content.py --type html

  # 仅做轻量清洗（已是 Markdown）
  python3 clean_content.py --type text doc.md
"""
import argparse
import re
import sys
from pathlib import Path


# 常见页面噪音块（正则，命中则整段删）
BOILERPLATE_BLOCKS = [
    r"版权\s*[©@]\s*[\d\s\-]+[\s\S]*?保留所有权利",
    r"Copyright\s*[©@][\s\S]*?All\s*Rights\s*Reserved",
    r"隐私政策[\s\S]{0,200}条款",
    r"Privacy\s*Policy[\s\S]{0,300}Terms",
    r"使用Cookie[\s\S]{0,150}同意",
    r"备案号[\s\S]{0,100}",
    r"京ICP[\s\S]{0,80}",
    r"\[?\s*广告\s*\]?",
    r"Advertisement",
    r"请\s*登录\s*后\s*查看",
    r"Login\s*to\s*view",
    r"^[\s\S]{0,50}导航\s*[\s\S]{0,200}首页",
    r"<nav[\s\S]*?</nav>",
    r"<footer[\s\S]*?</footer>",
    r"<header[\s\S]*?</header>",
]
BOILERPLATE = [re.compile(p, re.IGNORECASE) for p in BOILERPLATE_BLOCKS]

# 简单 HTML 标签保留为换行或空
TAG_BR = re.compile(r"<br\s*/?>", re.I)
TAG_P = re.compile(r"</p>", re.I)
TAG_DIV = re.compile(r"</div>", re.I)
TAG_LI = re.compile(r"</li>", re.I)
TAG_TR = re.compile(r"</tr>", re.I)
TAG_H = re.compile(r"</h[1-6]>", re.I)


def strip_html(html: str) -> str:
    """粗略去 HTML，保留一点结构（换行）。"""
    if not html or not html.strip():
        return ""
    text = html
    text = TAG_BR.sub("\n", text)
    text = TAG_P.sub("\n", text)
    text = TAG_DIV.sub("\n", text)
    text = TAG_LI.sub("\n", text)
    text = TAG_TR.sub("\n", text)
    text = TAG_H.sub("\n", text)
    text = re.sub(r"<script[\s\S]*?</script>", "\n", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text, flags=re.I)
    text = re.sub(r"&lt;", "<", text, flags=re.I)
    text = re.sub(r"&gt;", ">", text, flags=re.I)
    text = re.sub(r"&amp;", "&", text, flags=re.I)
    text = re.sub(r"&quot;", '"', text, flags=re.I)
    return text


def normalize_whitespace(text: str) -> str:
    """合并多余空白、空行。"""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def remove_boilerplate(text: str) -> str:
    """按预定义规则删大段噪音。"""
    for pat in BOILERPLATE:
        text = pat.sub("\n", text)
    return text


def clean_html(html: str) -> str:
    """HTML → 清洗后纯文本。"""
    text = strip_html(html)
    text = remove_boilerplate(text)
    text = normalize_whitespace(text)
    return text


def clean_text(text: str) -> str:
    """已是纯文本/Markdown，只做空白与噪音清理。"""
    text = remove_boilerplate(text)
    text = normalize_whitespace(text)
    return text


def main():
    ap = argparse.ArgumentParser(description="清洗 HTML/文本，输出适合 RAG 的纯文本")
    ap.add_argument("file", nargs="?", default="-", help="输入文件，- 表示 stdin")
    ap.add_argument("--type", choices=("html", "text"), default="html", help="输入类型：html 或 text")
    ap.add_argument("-o", "--output", default="-", help="输出文件，- 表示 stdout")
    args = ap.parse_args()

    if args.file == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.file).read_text(encoding="utf-8", errors="replace")

    if args.type == "html":
        out = clean_html(raw)
    else:
        out = clean_text(raw)

    if args.output == "-":
        sys.stdout.write(out)
        if out and not out.endswith("\n"):
            sys.stdout.write("\n")
    else:
        Path(args.output).write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
