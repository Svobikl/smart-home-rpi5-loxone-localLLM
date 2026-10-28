#!/usr/bin/env python3
"""Unified STT Daemon on localhost:8765:
- Primary Default (unless ?mode=local): Online Gemini (gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-3.5-flash, gemini-2.5-flash)
- Automatic Local Fallback (when Gemini fails/429/offline or ?mode=local):
  - Czech (lang=cs): Warm Faster-Whisper base (int8, 4 threads, vad_filter=False for zero initial-fricative clipping)
  - English (lang=en): Warm Moonshine Base ONNX
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np
from faster_whisper import WhisperModel
from moonshine_onnx import MoonshineOnnxModel, load_tokenizer

HOST = "localhost"
PORT = 8765
WHISPER_DIR = "/mnt/nvme/ai_models/hf_cache"
MOONSHINE_BASE_DIR = "/mnt/nvme/ai_models/hf_cache/hub/models--UsefulSensors--moonshine/snapshots/48b4e427b587bcf67797a5be706d6ddc4a298149/onnx/merged/base/float"
HERMES_ENV = Path.home() / ".hermes" / ".env"
STATE_FILE = Path.home() / ".hermes" / "cache" / "stt-venv" / "last_gemini_model"

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]
GEMINI_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

CZECH_PROMPT = (
    "Rozsviť v zádveří, zhasni v obýváku, rozsviť v obýváku, zhasni obývák, rozsviť obývák, "
    "zhasni světla v obýváku, v kuchyni, v ložnici, v koupelně, na chodbě, v pracovně, v garáži. "
    "Jaká je dneska denní spotřeba domu, aktuální odběr, výkon fotovoltaiky, stav baterie, teplota."
)

_CLIPPED_ZHASNI_RE = re.compile(
    r"^(?:asi|hasni|zasni|zhasný|zhasní|vlastně|hlasitě)\s+"
    r"(v\s+obýváku|v\s+obývacím\s+pokoji|obývák|v\s+kuchyni|kuchyň|kuchyni|"
    r"v\s+ložnici|ložnici|v\s+zádveří|zádveří|v\s+koupelně|koupelnu|"
    r"na\s+chodbě|chodbu|v\s+pracovně|pracovnu|v\s+garáži|garáž|"
    r"v\s+šatně|šatnu|na\s+terase|terasu|v\s+sauně|saunu|světla\b.*|světlo\b.*)$",
    flags=re.IGNORECASE | re.UNICODE,
)

_CLIPPED_ROZSVIT_RE = re.compile(
    r"^(?:rozsvědí|rozsvěd|rozhodně|prosím\s+tě)\s+"
    r"(v\s+obýváku|v\s+obývacím\s+pokoji|obývák|v\s+kuchyni|kuchyň|kuchyni|"
    r"v\s+ložnici|ložnici|v\s+zádveří|zádveří|v\s+koupelně|koupelnu|"
    r"na\s+chodbě|chodbu|v\s+pracovně|pracovnu|v\s+garáži|garáž|"
    r"v\s+šatně|šatnu|světla\b.*|světlo\b.*)$",
    flags=re.IGNORECASE | re.UNICODE,
)


def normalize_czech_transcript(raw_text: str) -> str:
    """Repair clipped initial fricatives ('zhasni'/'rozsviť') and common Czech smart-home phonetic confusions."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return ""
    if cleaned.endswith(".") and (" " in cleaned or len(cleaned) > 3):
        cleaned = cleaned[:-1].strip()
    for src, dst in (
        ("asi povídáku", "zhasni v obýváku"),
        ("Asi povídáku", "Zhasni v obýváku"),
        ("zhasni povídáku", "zhasni v obýváku"),
        ("Zhasni povídáku", "Zhasni v obýváku"),
        ("hlasitě v obýváku", "zhasni v obýváku"),
        ("Hlasitě v obýváku", "Zhasni v obýváku"),
        ("Denis potřeba", "denní spotřeba"),
        ("denis potřeba", "denní spotřeba"),
        ("Rozsvědí ", "Rozsviť "),
        ("rozsvědí ", "rozsviť "),
        ("Rozsvěd ", "Rozsviť "),
        ("rozsvěd ", "rozsviť "),
        ("Zhasný ", "Zhasni "),
        ("zhasný ", "zhasni "),
        ("Zhasní ", "Zhasni "),
        ("zhasní ", "zhasni "),
        ("vzár veří", "v zádveří"),
        ("zadveří", "zádveří"),
        ("vzádveří", "v zádveří"),
        ("jak se maž", "jak se máš"),
    ):
        if src in cleaned:
            cleaned = cleaned.replace(src, dst)
    cleaned = re.sub(r"(?i)^(?:hasni|zasni)\s+", "zhasni ", cleaned)
    m_zhasni = _CLIPPED_ZHASNI_RE.match(cleaned)
    if m_zhasni:
        cleaned = f"zhasni {m_zhasni.group(1)}"
    else:
        m_rozsvit = _CLIPPED_ROZSVIT_RE.match(cleaned)
        if m_rozsvit:
            cleaned = f"Rozsviť {m_rozsvit.group(1)}"
    return cleaned


print("[stt-daemon] Loading Faster-Whisper base (int8, 4 threads) for Czech fallback...", flush=True)
WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4, download_root=WHISPER_DIR)

print("[stt-daemon] Loading Moonshine base (ONNX) from local NVMe for English fallback...", flush=True)
MOONSHINE_MODEL = MoonshineOnnxModel(models_dir=MOONSHINE_BASE_DIR, model_name="base", model_precision="float")
MOONSHINE_TOKENIZER = load_tokenizer()
print("[stt-daemon] Hybrid STT Daemon Ready (Primary: Online Gemini -> Fallback: Whisper CS + Moonshine EN)!", flush=True)


def get_gemini_key() -> str:
    try:
        for line in HERMES_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m and m.group(1) in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                val = m.group(2).strip().strip('"').strip("'")
                if val:
                    return val
    except Exception:
        pass
    return ""


def transcribe_online_gemini(wav_bytes: bytes, lang: str) -> tuple[str, str]:
    key = get_gemini_key()
    if not key:
        raise RuntimeError("No GEMINI_API_KEY in ~/.hermes/.env")
    prompt = (
        "Přepiš tuto českou nahrávku. Přepis musí být plynulá, čistá česká věta bez opakování a bez fillerů. Piš jen přepis."
        if lang == "cs"
        else "Transcribe this English audio clip cleanly and accurately. Output only the transcript."
    )
    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav_bytes).decode("ascii")}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.0, "thinkingConfig": {"thinkingBudget": 0}},
    }
    payload = json.dumps(body).encode("utf-8")
    order = list(GEMINI_MODELS)
    try:
        last = STATE_FILE.read_text(encoding="utf-8").strip()
        if last in order:
            order.remove(last)
            order.insert(0, last)
    except Exception:
        pass

    errors = []
    for model in order:
        req = urllib.request.Request(
            GEMINI_URL_TMPL.format(model=model),
            data=payload,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                if lang == "cs":
                    text = normalize_czech_transcript(text)
                try:
                    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    STATE_FILE.write_text(model, encoding="utf-8")
                except Exception:
                    pass
                return text, f"gemini:{model}"
        except Exception as e:
            errors.append(f"{model}:{type(e).__name__}")
            continue
    raise RuntimeError("; ".join(errors))


def wav_bytes_to_float32(raw_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(raw_bytes), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        return pcm.astype(np.float32) / 32768.0


def transcribe_local_fallback(raw_bytes: bytes, lang: str) -> tuple[str, str]:
    if lang == "en":
        audio_f32 = wav_bytes_to_float32(raw_bytes)
        tokens = MOONSHINE_MODEL.generate(audio_f32[np.newaxis, :])
        text = MOONSHINE_TOKENIZER.decode_batch(tokens)[0].strip()
        return text, "moonshine/base-onnx"
    else:
        segments, _ = WHISPER_MODEL.transcribe(
            io.BytesIO(raw_bytes),
            language=lang or "cs",
            beam_size=1,
            best_of=1,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.20,
                "min_speech_duration_ms": 80,
                "min_silence_duration_ms": 600,
                "speech_pad_ms": 800,
            },
            condition_on_previous_text=False,
            initial_prompt=CZECH_PROMPT if (lang or "cs") == "cs" else None,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if (lang or "cs") == "cs":
            text = normalize_czech_transcript(text)
        return text, "faster-whisper-base-int8"


class STTHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            payload = json.dumps(
                {
                    "status": "ok",
                    "primary_stt": "gemini-online",
                    "gemini_models": GEMINI_MODELS,
                    "fallback_cs": "faster-whisper-base-int8",
                    "fallback_en": "moonshine/base-onnx",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/transcribe"):
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        lang = (qs.get("lang", ["cs"])[0] or "cs").strip().lower()
        mode = (qs.get("mode", ["auto"])[0] or "auto").strip().lower()
        length = int(self.headers.get("Content-Length", "0"))
        raw_bytes = self.rfile.read(length)
        t0 = time.perf_counter()
        try:
            if mode == "local":
                text, engine_used = transcribe_local_fallback(raw_bytes, lang)
            else:
                try:
                    text, engine_used = transcribe_online_gemini(raw_bytes, lang)
                except Exception as gem_err:
                    print(f"[stt-daemon] Online Gemini failed ({gem_err}), falling back to local ({lang})", flush=True)
                    text, engine_used = transcribe_local_fallback(raw_bytes, lang)
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            resp = json.dumps(
                {
                    "text": text,
                    "engine": engine_used,
                    "language": lang,
                    "latency_ms": elapsed_ms,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(resp)
        except Exception as exc:
            err = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    HTTPServer((HOST, PORT), STTHandler).serve_forever()
