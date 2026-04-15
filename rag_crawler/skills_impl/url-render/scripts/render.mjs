#!/usr/bin/env node

/**
 * 使用无头浏览器渲染 URL，并返回渲染后的 HTML 或 Markdown。
 *
 * 设计目标：
 * - 解决 SPA / Cloudflare 等站点仅返回「Please wait...」等占位内容的问题；
 * - 尽量保持依赖简单，优先使用 Playwright，若不可用则尝试 Puppeteer；
 * - 输出尽量简单：stdout 仅为正文（HTML 或 Markdown），stderr 用于日志。
 *
 * 环境变量（可选）：
 * - URL_RENDER_OUTPUT: "html" | "markdown"（默认 "markdown"）
 * - MARKITDOWN_BIN: 自定义 markitdown 可执行文件路径（默认使用 `uvx markitdown`）
 */

import { spawn } from "child_process";
import { fileURLToPath } from "url";
import { dirname } from "path";

const argv = process.argv.slice(2);

function getArg(name, def) {
  const i = argv.indexOf(`--${name}`);
  if (i === -1 || i + 1 >= argv.length) return def;
  return argv[i + 1];
}

const url = getArg("url");
if (!url) {
  console.error("必填参数 --url <http(s)://...>");
  process.exit(1);
}

const OUTPUT_MODE = (process.env.URL_RENDER_OUTPUT || "markdown").toLowerCase();
const wantMarkdown = OUTPUT_MODE === "markdown";

async function loadBrowser() {
  try {
    const mod = await import("playwright");
    return { type: "playwright", mod };
  } catch {
    try {
      const mod = await import("puppeteer");
      return { type: "puppeteer", mod };
    } catch {
      console.error("需要安装 playwright 或 puppeteer 作为无头浏览器依赖");
      process.exit(1);
    }
  }
}

async function renderWithPlaywright(playwright) {
  const browser = await playwright.chromium.launch({
    headless: true,
  });
  try {
    const context = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
    });
    const page = await context.newPage();
    await page.goto(url, {
      waitUntil: "networkidle",
      timeout: 60000,
    });

    // 等待可能的懒加载内容
    await page.waitForTimeout(2000);

    const html = await page.evaluate(() => {
      return document.documentElement.outerHTML || document.body?.innerHTML || "";
    });
    return html || "";
  } finally {
    await browser.close();
  }
}

async function renderWithPuppeteer(puppeteer) {
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  try {
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
    );
    await page.goto(url, {
      waitUntil: "networkidle0",
      timeout: 60000,
    });
    await page.waitForTimeout(2000);
    const html = await page.evaluate(() => {
      return document.documentElement.outerHTML || document.body?.innerHTML || "";
    });
    return html || "";
  } finally {
    await browser.close();
  }
}

function runMarkitdownOnHtml(html) {
  return new Promise((resolve, reject) => {
    const markitBin = process.env.MARKITDOWN_BIN || "uvx";
    const args =
      markitBin === "uvx"
        ? ["markitdown", "-"] // uvx markitdown -  从 stdin 读 HTML
        : ["-"]; // 自定义 markitdown，可通过 MARKITDOWN_BIN 指向支持 stdin 的可执行文件

    const child = spawn(markitBin, args, {
      stdio: ["pipe", "pipe", "pipe"],
    });

    let out = "";
    let err = "";
    child.stdout.on("data", (d) => {
      out += d.toString("utf8");
    });
    child.stderr.on("data", (d) => {
      err += d.toString("utf8");
    });
    child.on("error", (e) => {
      reject(e);
    });
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`markitdown 退出码 ${code}: ${err}`));
      } else {
        resolve(out);
      }
    });

    child.stdin.write(html);
    child.stdin.end();
  });
}

async function main() {
  try {
    const browser = await loadBrowser();
    let html = "";
    if (browser.type === "playwright") {
      html = await renderWithPlaywright(browser.mod);
    } else {
      html = await renderWithPuppeteer(browser.mod);
    }

    if (!html || !html.trim()) {
      console.error("渲染后 HTML 为空");
      process.exit(1);
    }

    if (!wantMarkdown) {
      process.stdout.write(html);
      return;
    }

    try {
      const md = await runMarkitdownOnHtml(html);
      if (!md || !md.trim()) {
        console.error("markitdown 返回空内容，回退为 HTML");
        process.stdout.write(html);
      } else {
        process.stdout.write(md);
      }
    } catch (e) {
      console.error("markitdown 失败，回退为 HTML:", e?.message || e);
      process.stdout.write(html);
    }
  } catch (e) {
    console.error("url-render 错误:", e?.message || e);
    process.exit(1);
  }
}

main();

