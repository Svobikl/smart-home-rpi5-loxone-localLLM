#!/usr/bin/env python3
"""CLI wrapper for Hermes Agent STT: uses Online Gemini primary -> Faster-Whisper (CS) & Moonshine (EN) local fallback."""
import argparse
import json
import os
import urllib.request
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("input_path")
    p.add_argument("--model", default="base")
    p.add_argument("--output_format", default="txt")
    p.add_argument("--output_dir", required=True)
    p.add_argument("--language", default="cs")
    args, _ = p.parse_known_args()

    lang = (args.language or "cs").strip().lower()
    raw_audio = Path(args.input_path).read_bytes()
    url = f"http://localhost:8765/transcribe?lang={lang}"
    req = urllib.request.Request(url, data=raw_audio, headers={"Content-Type": "audio/wav"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        text = res.get("text", "").strip()

    stem = Path(args.input_path).stem
    out_file = Path(args.output_dir) / f"{stem}.txt"
    out_file.write_text(text + "\n", encoding="utf-8")
    print(text)

if __name__ == "__main__":
    main()
