#!/usr/bin/env bash
set -euo pipefail
# Install Google LiteRT-LM (litert-lm), Crawl4AI, and download Gemma 4 E2B & E4B models on Raspberry Pi 5 (ARM64)

AI_DIR="${AI_DIR:-/mnt/nvme/ai_models}"
mkdir -p "$AI_DIR/gemma4_e2b" "$AI_DIR/gemma4_e4b" "$AI_DIR/hf_cache"

echo "[1/3] Creating Python 3.11 virtual environment and installing litert-lm + crawl4ai..."
python3 -m venv "$AI_DIR/litert_env"
"$AI_DIR/litert_env/bin/pip" install --upgrade pip
"$AI_DIR/litert_env/bin/pip" install litert-lm huggingface_hub crawl4ai ddgs
ln -sf "$AI_DIR/litert_env/bin/crwl" /usr/local/bin/crwl || true
ln -sf "$AI_DIR/litert_env/bin/crawl4ai-doctor" /usr/local/bin/crawl4ai-doctor || true

echo "[2/3] Downloading Google Gemma 4 E2B-it (.litertlm) from HuggingFace..."
"$AI_DIR/litert_env/bin/huggingface-cli" download google/gemma-4-E2B-it-litert-lm gemma-4-E2B-it.litertlm --local-dir "$AI_DIR/gemma4_e2b"

echo "[3/3] (Optional) To download Gemma 4 E4B-it (.litertlm), uncomment below:"
echo "# $AI_DIR/litert_env/bin/huggingface-cli download google/gemma-4-E4B-it-litert-lm gemma-4-E4B-it.litertlm --local-dir $AI_DIR/gemma4_e4b"
echo "Installation complete!"
