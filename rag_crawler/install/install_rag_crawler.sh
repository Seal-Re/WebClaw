#!/usr/bin/env bash
#
# 一键安装 rag_crawler 到 Linux 服务器，并配置 systemd 开机自启。
#
# 使用方式（在服务器上）：
#   1. 将本机的 rag_crawler 目录打包上传到服务器，例如 /tmp/rag_crawler
#   2. cd /tmp/rag_crawler/install
#   3. sudo bash install_rag_crawler.sh
#   4. 编辑 ${INSTALL_DIR}/.env（见 env.example 与 CONFIG.md）填入各 API 配置
#

set -euo pipefail

INSTALL_DIR="/opt/rag-crawler"
SERVICE_NAME="rag-crawler"
ENV_FILE_DEFAULT="${INSTALL_DIR}/.env"

echo "[INFO] Installing rag_crawler to ${INSTALL_DIR} ..."

if [[ $EUID -ne 0 ]]; then
  echo "[ERROR] 请以 root 身份运行本脚本（sudo bash install_rag_crawler.sh）" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "[INFO] 源目录: ${SOURCE_ROOT}"

mkdir -p "${INSTALL_DIR}"

echo "[INFO] 复制 rag_crawler 文件到 ${INSTALL_DIR} ..."
rsync -a --delete "${SOURCE_ROOT}/" "${INSTALL_DIR}/"

cd "${INSTALL_DIR}"

# 确保 topics.txt 存在（rsync 已复制；若缺失则从源码或生成默认）
if [[ ! -f "${INSTALL_DIR}/topics.txt" ]]; then
  if [[ -f "${SOURCE_ROOT}/topics.txt" ]]; then
    cp "${SOURCE_ROOT}/topics.txt" "${INSTALL_DIR}/topics.txt"
    echo "[INFO] 已从源码复制 topics.txt 到 ${INSTALL_DIR}"
  else
    echo -e "# 主题列表（一行一个，# 为注释）\n渗透测试 全流程 PTES\nWeb 安全 SQL 注入 XSS" > "${INSTALL_DIR}/topics.txt"
    echo "[WARN] 已生成默认 topics.txt，请按需编辑"
  fi
fi

# 若无 .env，则自动尝试从已有文件生成一份：
#  1) 优先使用源目录中的 .env（若有）
#  2) 否则使用安装目录中的 env.example
#  3) 否则尝试 /root/.openclaw/.env 作为最后兜底
if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  if [[ -f "${SOURCE_ROOT}/.env" ]]; then
    cp "${SOURCE_ROOT}/.env" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
    echo "[INFO] 已从源码目录复制 .env 到 ${INSTALL_DIR}/.env，请根据 CONFIG.md 校对配置"
  elif [[ -f "${INSTALL_DIR}/env.example" ]]; then
    cp "${INSTALL_DIR}/env.example" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
    echo "[INFO] 已从 env.example 生成 ${INSTALL_DIR}/.env，请编辑填入真实 key/URL"
  elif [[ -f "/root/.openclaw/.env" ]]; then
    cp "/root/.openclaw/.env" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
    echo "[INFO] 已从 /root/.openclaw/.env 复制到 ${INSTALL_DIR}/.env，请按需裁剪为 rag_crawler 专用配置"
  else
    echo "[WARN] 未找到可用的 env 模板，请手动创建 ${INSTALL_DIR}/.env（参考 CONFIG.md 与 env.example）"
  fi
fi

echo "[INFO] 检查依赖：python, node, uvx ..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "[WARN] 未找到 python3，请自行安装（例如: sudo apt install python3）"
fi
if ! command -v node >/dev/null 2>&1; then
  echo "[WARN] 未找到 node，请自行安装（Node.js 18+）"
fi
if ! command -v uvx >/dev/null 2>&1; then
  echo "[WARN] 未找到 uvx，markdown-converter 将无法使用 markitdown，请按需安装 uv/markitdown。"
fi

echo "[INFO] 创建 Python 虚拟环境（可选） ..."
if command -v python3 >/dev/null 2>&1; then
  if [[ ! -d "${INSTALL_DIR}/venv" ]]; then
    python3 -m venv "${INSTALL_DIR}/venv"
  fi
else
  echo "[WARN] 未创建 venv，因为未检测到 python3，将使用系统 Python。"
fi

PY_BIN="python3"
if [[ -x "${INSTALL_DIR}/venv/bin/python" ]]; then
  PY_BIN="${INSTALL_DIR}/venv/bin/python"
fi

echo "[INFO] 使用 Python 可执行文件: ${PY_BIN}"

# 安装 Python 依赖（如 requirements.txt 存在）
if [[ -f "${INSTALL_DIR}/requirements.txt" ]]; then
  echo "[INFO] 安装 Python 依赖（requirements.txt）..."
  if command -v "${PY_BIN}" >/dev/null 2>&1; then
    "${PY_BIN}" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "${PY_BIN}" -m pip install -r "${INSTALL_DIR}/requirements.txt"
  else
    echo "[WARN] 未能使用 ${PY_BIN} 安装依赖，请手动执行: ${PY_BIN} -m pip install -r ${INSTALL_DIR}/requirements.txt"
  fi
fi

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "[INFO] 生成 systemd 服务文件: ${SERVICE_FILE}"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=RAG Crawler - OpenClaw Knowledge Builder
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE_DEFAULT}
ExecStart=${PY_BIN} ${INSTALL_DIR}/crawler.py --topics-file ${INSTALL_DIR}/topics.txt --max-urls 5
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF

echo "[INFO] 重新加载 systemd 守护进程 ..."
systemctl daemon-reload

echo "[INFO] 启用并启动服务 ${SERVICE_NAME} ..."
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

echo
echo "[DONE] 安装完成。"
echo "  - 服务名: ${SERVICE_NAME}"
echo "  - 目录:   ${INSTALL_DIR}"
echo "  - 日志:   使用 journalctl -u ${SERVICE_NAME} 查看运行日志"
echo
echo "[NEXT]"
echo "  1. 编辑 ${ENV_FILE_DEFAULT}，填入 LLM / Tavily / Qdrant / Embedding 配置（见 CONFIG.md、env.example）。"
echo "  2. 在 ${INSTALL_DIR}/topics.txt 中维护要定期抓取的主题列表（一行一个主题）。"
echo "  3. 如需修改服务行为，可编辑 ${SERVICE_FILE} 后执行: systemctl daemon-reload && systemctl restart ${SERVICE_NAME}.service"

