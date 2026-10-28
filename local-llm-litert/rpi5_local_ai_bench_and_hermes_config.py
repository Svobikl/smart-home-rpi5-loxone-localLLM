#!/usr/bin/env python3
"""Moonshine vs Faster-Whisper STT Evaluator, Gemma 4 E2B LiteRT Thread Optimizer & Hermes Fallback Config Manager."""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from typing import Any


def _normalize_text(text: str) -> str:
    """Normalize text for CER comparison by lowercasing and stripping punctuation."""
    cleaned = re.sub(r"[^\w\s]", "", (text or "").lower(), flags=re.UNICODE)
    return " ".join(cleaned.split()).strip()


def compute_cer(reference: str, hypothesis: str) -> float:
    """Compute normalized Levenshtein Character Error Rate (CER) between reference and hypothesis."""
    ref = _normalize_text(reference)
    if not ref:
        raise ValueError("reference transcript must not be empty")
    hyp = _normalize_text(hypothesis)
    if ref == hyp:
        return 0.0
    m, n = len(ref), len(hyp)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = temp
    return round(dp[n] / float(m), 4)


def select_best_stt_engine(
    candidates: list[dict[str, Any]],
    language: str,
    max_cer: float = 0.18,
) -> dict[str, Any]:
    """Select the lowest-latency STT candidate for the target language that satisfies cer <= max_cer."""
    lang = (language or "").strip().lower()
    eligible = [
        c
        for c in candidates
        if str(c.get("language", "")).strip().lower() == lang and float(c.get("cer", 1.0)) <= max_cer
    ]
    if not eligible:
        raise ValueError(f"No STT candidate for language={lang!r} met max_cer={max_cer}")
    return min(eligible, key=lambda item: (float(item.get("latency_ms", 1e9)), float(item.get("cer", 1.0))))


def should_enable_hermes_llm_fallback(decode_tps: float, min_tps: float = 5.0) -> bool:
    """Return True if measured decode TPS meets or exceeds the minimum threshold (default 5.0 TPS)."""
    return float(decode_tps) >= float(min_tps)


def select_optimal_litert_thread_config(
    runs: list[dict[str, Any]],
    min_decode_tps: float = 5.0,
) -> dict[str, Any]:
    """Select the LiteRT-LM thread/NUMA configuration achieving highest decode TPS >= min_decode_tps with passing quality."""
    eligible = [
        r
        for r in runs
        if bool(r.get("quality_passed", False))
        and should_enable_hermes_llm_fallback(float(r.get("decode_tps", 0.0)), min_tps=min_decode_tps)
    ]
    if not eligible:
        raise ValueError(f"No LiteRT configuration met min_decode_tps={min_decode_tps} with quality_passed=True")
    return max(eligible, key=lambda item: (float(item.get("decode_tps", 0.0)), float(item.get("prefill_tps", 0.0))))


def select_best_gemma4_litert_model(
    models: list[dict[str, Any]],
    min_decode_tps: float = 5.0,
    max_rss_mb: float = 6500.0,
) -> dict[str, Any]:
    """Compare Gemma 4 E2B vs E4B LiteRT runs and select the best candidate meeting TPS, RAM, and quality constraints."""
    eligible = [
        m
        for m in models
        if bool(m.get("quality_passed", False))
        and should_enable_hermes_llm_fallback(float(m.get("decode_tps", 0.0)), min_tps=min_decode_tps)
        and float(m.get("rss_mb", 0.0)) <= float(max_rss_mb)
    ]
    if not eligible:
        raise ValueError(
            f"No Gemma 4 LiteRT model met min_decode_tps={min_decode_tps}, max_rss_mb={max_rss_mb}, and quality_passed=True"
        )
    return max(
        eligible,
        key=lambda item: (
            float(item.get("quality_score", 0.0)),
            float(item.get("decode_tps", 0.0)),
            float(item.get("prefill_tps", 0.0)),
        ),
    )


def build_hermes_fallback_config(
    existing_config: dict[str, Any],
    decode_tps: float,
    local_base_url: str = "http://localhost:8091/v1",
    model_name: str = "gemma-4-E2B-it",
    min_tps: float = 5.0,
) -> dict[str, Any]:
    """Build an updated Hermes config.yaml dictionary adding local Gemma 4 E2B fallback when decode_tps >= min_tps."""
    if not should_enable_hermes_llm_fallback(decode_tps, min_tps=min_tps):
        raise ValueError(f"Measured decode TPS ({decode_tps}) is below required minimum ({min_tps} TPS)")
    clean_url = (local_base_url or "").strip().rstrip("/")
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        raise ValueError(f"local_base_url must start with http:// or https://, got {local_base_url!r}")

    cfg = copy.deepcopy(existing_config)
    entry = {
        "provider": "custom",
        "model": model_name.strip(),
        "base_url": clean_url,
    }
    current_chain = list(cfg.get("fallback_providers") or [])
    deduped_chain: list[dict[str, Any]] = []
    found = False
    for item in current_chain:
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("provider", "")).strip() == entry["provider"]
            and str(item.get("model", "")).strip() == entry["model"]
            and str(item.get("base_url", "")).strip().rstrip("/") == entry["base_url"]
        ):
            if not found:
                deduped_chain.append(entry)
                found = True
        else:
            deduped_chain.append(item)
    if not found:
        deduped_chain.append(entry)
    cfg["fallback_providers"] = deduped_chain
    cfg.pop("fallback_model", None)
    return cfg


_RESEARCH_PATTERNS = re.compile(
    r"("
    r"\b(?:research|deep[\s_-]?research|výzkum\w*|vyzkum\w*|průzkum\w*|pruzkum\w*|rešerš\w*|resers\w*|porovnání\s+protokol\w*|literatur\w*)\b"
    r"|\b(?:počas[ií]\w*|pocas[ií]\w*|předpově[dď]\w*|predpove[dď]\w*|weather\w*|forecast\w*|meteogram\w*|srážk\w*|srazk\w*|bouřk\w*|bourk\w*|pr[sš]et\w*|sn[eě][zž]it\w*)\b"
    r"|\b(?:jak\s+dnes(?:ka)?\s+je|jak\s+je\s+venku|jak\s+bude\s+zítra|jak\s+bude\s+zitra|bude\s+pršet|bude\s+prset)\b"
    r"|\b(?:vyhledej\w*|najdi\s+na\s+(?:internetu|webu)|prohledej\s+(?:internet|web)|crawl4ai|crwl|zprávy\s+z\s+internetu|aktuality)\b"
    r"|https?://\S+"
    r")",
    flags=re.IGNORECASE | re.UNICODE,
)
_PLAN_PATTERNS = re.compile(
    r"\b(implementation\s+plan|implementačn\w*\s+plán\w*|implementacn\w*\s+plan\w*|návrh\w*\s+architektur\w*|navrh\w*\s+architektur\w*|architektonick\w*\s+plán\w*)\b",
    flags=re.IGNORECASE | re.UNICODE,
)
_IMPL_PATTERNS = re.compile(
    r"\b(implementuj\w*|naimplementuj\w*|implementace|implementovat|implement\b|nakóduj\w*|nakoduj\w*|napiš\s+kód|napis\s+kod|refaktoruj\w*|refactoring|pull\s+request)\b",
    flags=re.IGNORECASE | re.UNICODE,
)


def classify_hermes_task_route(
    prompt: str,
    default_model: str = "gemma-4-E2B-it",
    specialist_model: str = "MiniMax-M3.1-Flash",
) -> dict[str, str]:
    """Classify a user task prompt to route research, implementation, and implementation planning to MiniMax-M3.1-Flash and all other work to Gemma 4 E2B."""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt must not be empty")

    if _PLAN_PATTERNS.search(text):
        return {
            "task_category": "implementation_plan",
            "target_model": specialist_model,
            "provider": "minimax",
        }
    if _RESEARCH_PATTERNS.search(text):
        return {
            "task_category": "research",
            "target_model": specialist_model,
            "provider": "minimax",
        }
    if _IMPL_PATTERNS.search(text):
        return {
            "task_category": "implementation",
            "target_model": specialist_model,
            "provider": "minimax",
        }
    return {
        "task_category": "general_local",
        "target_model": default_model,
        "provider": "custom",
    }


def build_hermes_primary_e2b_and_minimax_specialist_config(
    existing_config: dict[str, Any],
    local_base_url: str = "http://localhost:8091/v1",
    default_model: str = "gemma-4-E2B-it",
    specialist_model: str = "MiniMax-M3.1-Flash",
    specialist_base_url: str = "https://api.minimax.io/anthropic",
) -> dict[str, Any]:
    """Configure Hermes with local Gemma 4 E2B as default for general tasks and MiniMax-M3.1-Flash for research, implementation, and implementation planning."""
    clean_local_url = (local_base_url or "").strip().rstrip("/")
    if not (clean_local_url.startswith("http://") or clean_local_url.startswith("https://")):
        raise ValueError(f"local_base_url must start with http:// or https://, got {local_base_url!r}")

    cfg = copy.deepcopy(existing_config)
    cfg["model"] = {
        "default": default_model,
        "provider": "custom",
        "base_url": clean_local_url,
    }
    delegation = dict(cfg.get("delegation") or {})
    delegation["model"] = specialist_model
    delegation["provider"] = "minimax"
    delegation["base_url"] = specialist_base_url
    delegation.setdefault("max_iterations", 250)
    cfg["delegation"] = delegation

    cfg["task_model_routing"] = {
        "enabled": True,
        "default_model": default_model,
        "default_provider": "custom",
        "default_base_url": clean_local_url,
        "specialist_model": specialist_model,
        "specialist_provider": "minimax",
        "specialist_base_url": specialist_base_url,
        "specialist_categories": [
            "research",
            "implementation",
            "implementation_plan",
        ],
    }
    cfg["fallback_providers"] = [
        {
            "provider": "minimax",
            "model": specialist_model,
            "base_url": specialist_base_url,
        }
    ]
    return cfg


_SMARTHOME_LIGHT_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("zadveri", "vstup", "predsin"), "light.ovladani_osvetleni_12"),
    (("obyvak", "obyvac", "obyvaci"), "light.ovladani_osvetleni_kuchyne"),
    (("kuchyn", "kuchyni", "kuchyne"), "light.ovladani_osvetleni_3"),
    (("chodb",), "light.ovladani_osvetleni"),
    (("garaz",), "light.ovladani_osvetleni_2"),
    (("pracovn", "office", "kancelar"), "light.ovladani_osvetleni_7"),
    (("mala koupeln", "male koupeln"), "light.ovladani_osvetleni_mala_koupelna"),
    (("velka koupeln", "velke koupeln", "koupeln"), "light.ovladani_osvetleni_9"),
    (("loznice 2", "loznici 2", "detsky 1"), "light.ovladani_osvetleni_5"),
    (("loznice 3", "loznici 3", "detsky 2"), "light.ovladani_osvetleni_6"),
    (("loznice 1", "loznici 1", "loznic"), "light.ovladani_osvetleni_4"),
    (("technick",), "light.ovladani_osvetleni_8"),
    (("wc 2", "zachod 2", "toaleta 2"), "light.ovladani_osvetleni_11"),
    (("wc 1", "wc", "zachod", "toaleta"), "light.ovladani_osvetleni_10"),
    (("satn",), "light.ovladani_osvetleni_13"),
]


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()


def resolve_smarthome_light_entity(room_or_entity: str) -> str:
    """Resolve a Czech room name (with or without diacritics/inflections) or entity_id to the exact HA light entity."""
    raw = (room_or_entity or "").strip()
    if not raw:
        raise ValueError("Unknown room: empty input")
    if raw.startswith("light.") or raw.startswith("switch."):
        return raw
    norm = _strip_accents(raw)
    for keywords, entity_id in _SMARTHOME_LIGHT_ALIASES:
        if any(kw in norm for kw in keywords):
            return entity_id
    raise ValueError(f"Unknown room or light entity: {room_or_entity!r}")


_E2B_SMART_HOME_CHEATSHEET = (
    "Jsi chytrý domácí asistent Hermes na RPi5 (MySmartHome) S PŘÍSTUPEM K INTERNETU A METEOROLOGICKÝM STANICÍM. Odpovídej stručně a přesně česky. Nikdy neříkej, že nemáš přístup k internetu nebo k počasí.\n"
    "Pro ovládání domu, předpověď počasí a vyhledávání na internetu IHNED volej nástroj terminal s těmito příkazy (nevolej předtím skill_view):\n"
    "- Aktuální počasí a předpověď (dnes + další dny, srážky, vítr, FVE predikce): python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py weather\n"
    "- Rychlé vyhledávání na webu (Crawl4AI): python3 ~/.hermes/skills/crawl4ai-web/scripts/crawl4ai_cli.py search \"<dotaz>\"\n"
    "- Stažení webové stránky do Markdownu (Crawl4AI): python3 ~/.hermes/skills/crawl4ai-web/scripts/crawl4ai_cli.py crawl \"<url>\"\n"
    "- Stav domu, spotřeba, FVE, teploty: python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py status --compact\n"
    "- Stav všech světel: python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py lights\n"
    "- Zapnutí/vypnutí světla v místnosti: python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py light \"<místnost>\" <on|off|toggle>\n"
    "- Žaluzie v obýváku: python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py blinds obývák <open|close|stop>\n"
    "- Rychlá paměť: python3 ~/.hermes/skills/rpi5-fast-memory/scripts/fast_mem.py search \"<dotaz>\"\n"
    "- Zdraví RPi5 a služeb: python3 ~/.hermes/skills/heartbeat-monitor/scripts/heartbeat_check.py --compact"
)

_PRIORITY_TOOLS = ("terminal", "web_search", "web_extract", "memory", "delegate_task", "skill_view", "read_file", "write_file", "search_files")


def compact_hermes_prompt_for_e2b(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_total_chars: int = 3000,
) -> tuple[str, str]:
    """Distill Hermes system prompt, tool schemas, and conversation turns to fit safely within E2B context window."""
    if not messages:
        raise ValueError("messages must not be empty")

    convo_parts: list[str] = []
    last_role = messages[-1].get("role", "user")

    for m in messages[-6:]:
        role = m.get("role", "user")
        if role == "system":
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
        content_str = str(content or "").strip()
        if role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                fn = tcs[0].get("function", {}) if isinstance(tcs, list) and tcs else {}
                convo_parts.append(f"Asistent zavolal nástroj: {fn.get('name')}({fn.get('arguments')})")
            elif content_str:
                convo_parts.append(f"Asistent: {content_str[:400]}")
        elif role == "tool":
            t_name = m.get("name") or m.get("tool_call_id") or "tool"
            convo_parts.append(f"Výsledek nástroje ({t_name}):\n{content_str[:900]}")
        else:
            convo_parts.append(f"Uživatel: {content_str[:600]}")

    tool_lines: list[str] = []
    if tools and last_role != "tool":
        by_name: dict[str, dict[str, Any]] = {}
        for t in tools:
            fn = t.get("function", t) if isinstance(t, dict) else {}
            if isinstance(fn, dict) and fn.get("name"):
                by_name[str(fn["name"])] = fn
        selected_names = [n for n in _PRIORITY_TOOLS if n in by_name]
        if not selected_names:
            selected_names = list(by_name.keys())[:6]
        for name in selected_names:
            fn = by_name[name]
            props = fn.get("parameters", {}).get("properties", {})
            arg_keys = ", ".join(list(props.keys())[:4])
            tool_lines.append(f"- {name}({arg_keys})")

    if tool_lines:
        sys_msg = (
            _E2B_SMART_HOME_CHEATSHEET
            + "\nPokud potřebuješ zavolat nástroj, vypiš POUZE JSON na jeden řádek:\n"
            + '{"tool_call": {"name": "NAZEV_NASTROJE", "arguments": {...}}}\n'
            + "Dostupné nástroje:\n"
            + "\n".join(tool_lines)
        )
    elif last_role == "tool":
        sys_msg = (
            "Jsi chytrý domácí asistent Hermes (MySmartHome). "
            "Zde je výsledek nástroje. Odpověz uživateli přímo, stručně a přehledně česky bez volání dalších nástrojů."
        )
    else:
        sys_msg = (
            "Jsi chytrý domácí asistent Hermes na RPi5 (MySmartHome). "
            "Odpovídej přímo, stručně a přesně česky."
        )

    user_prompt = "\n\n".join(convo_parts).strip() or "Ahoj"
    budget_user = max(400, max_total_chars - len(sys_msg))
    if len(user_prompt) > budget_user:
        user_prompt = user_prompt[-budget_user:]
    return sys_msg, user_prompt


def build_stt_chain_config(
    primary_provider: str = "gemini",
    cs_fallback: str = "faster-whisper-base-int8",
    en_fallback: str = "moonshine/base-onnx",
) -> dict[str, Any]:
    """Build STT configuration with Online Gemini as primary default and Faster-Whisper (CS) + Moonshine (EN) as local fallback."""
    prov = (primary_provider or "").strip().lower()
    if prov != "gemini":
        raise ValueError(f"primary_provider must be 'gemini', got {primary_provider!r}")
    return {
        "primary": {
            "provider": "gemini",
            "models": [
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash",
                "gemini-2.5-flash",
            ],
        },
        "fallback": {
            "daemon_url": "http://localhost:8765/transcribe",
            "cs": cs_fallback,
            "en": en_fallback,
        },
    }


def _normalize_tool_arguments(name: str, raw_args: Any) -> dict[str, Any]:
    """Normalize small-model (E2B) tool arguments into the exact dict schema expected by Hermes tools."""
    args = raw_args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            args = parsed
        except Exception:
            pass
    if name == "terminal":
        if isinstance(args, list):
            cmd = " ".join(str(x) for x in args).strip()
            args = {"command": cmd}
        elif isinstance(args, str):
            args = {"command": args.strip()}
        elif isinstance(args, dict):
            args = dict(args)
            if "command" not in args:
                for alias in ("cmd", "script", "shell", "bash"):
                    if alias in args:
                        args["command"] = str(args.pop(alias))
                        break
        else:
            args = {"command": str(args)}
        cmd_str = str(args.get("command", "")).replace("/home/user/.hermes", "$HOME/.hermes")
        args["command"] = cmd_str
        return args
    if isinstance(args, dict):
        return args
    if isinstance(args, list) and args:
        return {"name": str(args[0])} if name == "skill_view" else {"query": " ".join(str(x) for x in args)}
    return {"value": str(args)}


def extract_tool_call_from_text(text: str) -> dict[str, Any] | None:
    """Extract a valid tool_call JSON object from model output using RFC-8259 raw_decode (safe for arbitrary nested braces)."""
    cleaned = re.sub(r"<\|thought\|>.*?<\|/thought\|>", "", text or "", flags=re.DOTALL).strip()
    if not cleaned or "{" not in cleaned:
        return None
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(cleaned):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(cleaned[idx:])
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        tc = obj.get("tool_call")
        if isinstance(tc, dict) and tc.get("name"):
            t_name = str(tc["name"])
            return {
                "name": t_name,
                "arguments": _normalize_tool_arguments(t_name, tc.get("arguments", {})),
            }
        if obj.get("name") and "arguments" in obj:
            t_name = str(obj["name"])
            return {
                "name": t_name,
                "arguments": _normalize_tool_arguments(t_name, obj.get("arguments", {})),
            }
    return None


class IncrementalLiteRTStreamFilter:
    """Real-time token-by-token filter for LiteRT-LM async chunks that strips <|thought|> blocks and detects JSON tool calls."""

    _OPEN_TAG = "<|thought|>"
    _CLOSE_TAG = "<|/thought|>"

    def __init__(self, has_tools: bool = False) -> None:
        self.has_tools = bool(has_tools)
        self._in_thought = False
        self._pending = ""
        self._decided_mode: str | None = None  # "text" | "tool_buffer"
        self.visible_text = ""

    def feed(self, token_text: str) -> str:
        """Process a newly generated token chunk and return any immediately streamable user-facing text delta."""
        if not token_text:
            return ""
        self._pending += token_text
        clean_delta = self._drain_thoughts()
        if not clean_delta:
            return ""
        self.visible_text += clean_delta
        if not self.has_tools:
            return clean_delta

        if self._decided_mode == "text":
            return clean_delta
        if self._decided_mode == "tool_buffer":
            return ""

        stripped = self.visible_text.lstrip()
        if not stripped:
            return ""

        if stripped.startswith("{") or stripped.startswith("```"):
            self._decided_mode = "tool_buffer"
            return ""

        self._decided_mode = "text"
        return self.visible_text

    def _drain_thoughts(self) -> str:
        out: list[str] = []
        while self._pending:
            if self._in_thought:
                end_idx = self._pending.find(self._CLOSE_TAG)
                if end_idx != -1:
                    self._pending = self._pending[end_idx + len(self._CLOSE_TAG) :]
                    self._in_thought = False
                else:
                    keep = len(self._CLOSE_TAG) - 1
                    if len(self._pending) > keep:
                        self._pending = self._pending[-keep:]
                    break
            else:
                start_idx = self._pending.find(self._OPEN_TAG)
                if start_idx != -1:
                    if start_idx > 0:
                        out.append(self._pending[:start_idx])
                    self._pending = self._pending[start_idx + len(self._OPEN_TAG) :]
                    self._in_thought = True
                else:
                    hold = 0
                    max_check = min(len(self._pending), len(self._OPEN_TAG) - 1)
                    for k in range(max_check, 0, -1):
                        if self._OPEN_TAG.startswith(self._pending[-k:]):
                            hold = k
                            break
                    emit_end = len(self._pending) - hold
                    if emit_end > 0:
                        out.append(self._pending[:emit_end])
                        self._pending = self._pending[emit_end:]
                    break
        return "".join(out)

    def finalize(self) -> tuple[str, dict[str, Any] | None]:
        """Flush any remaining buffered characters and return (final_text, tool_call_or_none)."""
        if not self._in_thought and self._pending:
            self.visible_text += self._pending
        self._pending = ""
        cleaned = self.visible_text.strip()
        if self.has_tools:
            tc = extract_tool_call_from_text(cleaned)
            if tc is not None:
                return "", tc
        return cleaned, None


def build_hermes_realtime_streaming_config(
    existing_config: dict[str, Any],
    edit_interval: float = 0.45,
    buffer_threshold: int = 8,
    cursor: str = " ▉",
) -> dict[str, Any]:
    """Configure Hermes config.yaml with low-latency real-time token streaming for Telegram and CLI."""
    if float(edit_interval) <= 0:
        raise ValueError("edit_interval must be positive")
    if int(buffer_threshold) < 1:
        raise ValueError("buffer_threshold must be >= 1")
    cfg = copy.deepcopy(existing_config)
    display = dict(cfg.get("display") or {})
    display["streaming"] = True
    cfg["display"] = display

    streaming = dict(cfg.get("streaming") or {})
    streaming["enabled"] = True
    streaming["transport"] = "auto"
    streaming["edit_interval"] = float(edit_interval)
    streaming["buffer_threshold"] = int(buffer_threshold)
    streaming["cursor"] = str(cursor)
    cfg["streaming"] = streaming
    return cfg


_SILENCE_MARKERS: frozenset[str] = frozenset(
    {
        "<noise>",
        "<silence>",
        "[noise]",
        "[silence]",
        "(noise)",
        "(silence)",
        "noise",
        "silence",
    }
)

_LOW_RMS_HALLUCINATIONS: frozenset[str] = frozenset(
    {
        "děkuji",
        "děkuji.",
        "díky",
        "titulky",
        "prosím",
    }
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


def normalize_czech_stt_transcript(raw_text: str | None, audio_rms: float = 350.0) -> str:
    """Clean Czech STT transcript, reject low-RMS hallucinations, and repair clipped initial fricatives ('zhasni'/'rozsviť')."""
    if not raw_text:
        return ""
    cleaned = raw_text.strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered in _SILENCE_MARKERS:
        return ""
    if audio_rms < 190.0 and lowered in _LOW_RMS_HALLUCINATIONS:
        return ""
    if cleaned.endswith(".") and (" " in cleaned or len(cleaned) > 3):
        cleaned = cleaned[:-1].strip()

    exact_or_substring_replacements = (
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
    )
    for src, dst in exact_or_substring_replacements:
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


def build_ha_local_whisper_fallback_url(
    stt_language: str = "cs-CZ",
    base_url: str = "http://localhost:8765/transcribe",
) -> str:
    """Build the direct local-mode fallback URL (?lang=<cs|en>&mode=local) to bypass double Online Gemini retry."""
    lang_code = "en" if (stt_language or "").strip().lower().startswith("en") else "cs"
    return f"{base_url.rstrip('/')}?lang={lang_code}&mode=local"


_BARE_AREA_SPECS: list[tuple[tuple[str, ...], str, str]] = [
    (("v obýváku", "obývák", "v obývacím pokoji", "obývací pokoj", "v obyvaku", "obyvak"), "light.ovladani_osvetleni_kuchyne", "v obýváku"),
    (("v kuchyni", "kuchyň", "kuchyně", "kuchyni"), "light.ovladani_osvetleni_3", "v kuchyni"),
    (("v zádveří", "zádveří", "v zadveri", "zadveri"), "light.ovladani_osvetleni_12", "v zádveří"),
    (("na chodbě", "v chodbě", "chodba", "chodbu", "na chodbe"), "light.ovladani_osvetleni", "na chodbě"),
    (("v garáži", "garáž", "v garazi", "garaz"), "light.ovladani_osvetleni_2", "v garáži"),
    (("v ložnici", "ložnice", "ložnici", "v loznici", "loznice"), "light.ovladani_osvetleni_4", "v ložnici"),
    (("v pracovně", "pracovna", "pracovnu", "v pracovne"), "light.ovladani_osvetleni_7", "v pracovně"),
    (("v technické", "technická", "v technické místnosti"), "light.ovladani_osvetleni_8", "v technické místnosti"),
    (("v koupelně", "koupelna", "koupelnu", "ve velké koupelně", "v koupelne"), "light.ovladani_osvetleni_9", "v koupelně"),
    (("v malé koupelně", "malá koupelna", "na wc", "wc", "na záchodě"), "light.ovladani_osvetleni_mala_koupelna", "v malé koupelně"),
    (("v šatně", "šatna", "šatnu", "v satne", "satna"), "light.ovladani_osvetleni_13", "v šatně"),
]


def expand_bare_area_command(user_text: str, entity_states: dict[str, str]) -> str:
    """Expand a bare room name utterance (e.g. 'v obýváku') into 'Zhasni/Rozsviť světla <area>' based on live light state."""
    cleaned = (user_text or "").strip()
    if not cleaned:
        return ""
    norm = cleaned.rstrip(".!?").strip().lower()
    for variants, entity_id, area_phrase in _BARE_AREA_SPECS:
        if norm in variants:
            current_state = str(entity_states.get(entity_id, "off")).strip().lower()
            verb = "Zhasni" if current_state == "on" else "Rozsviť"
            return f"{verb} světla {area_phrase}"
    return cleaned


def simulate_vad_preroll_and_onset(
    rms_chunks: list[float],
    pre_roll_chunks: int = 50,
    min_onset_chunks: int = 6,
    speech_chunks_required: int = 12,
) -> dict[str, Any]:
    """Simulate MicroVadSpeexEnhancer 6-chunk sustained onset gating + 500ms pre-roll ring buffer."""
    min_raw_rms = 120.0
    ema_raw_rms = 120.0
    consecutive_onset = 0
    in_speech = False
    speech_accum = 0
    vad_start_chunk: int | None = None

    for idx, raw_rms in enumerate(rms_chunks):
        if idx == 0:
            ema_raw_rms = raw_rms
            min_raw_rms = max(30.0, min(raw_rms, 220.0))
        else:
            ema_raw_rms = 0.35 * raw_rms + 0.65 * ema_raw_rms
            if ema_raw_rms < min_raw_rms:
                min_raw_rms = max(30.0, 0.85 * min_raw_rms + 0.15 * ema_raw_rms)
            else:
                min_raw_rms = min(220.0, 0.995 * min_raw_rms + 0.005 * ema_raw_rms)

        onset_threshold = max(165.0, min(320.0, min_raw_rms * 1.45))
        if not in_speech:
            if max(raw_rms, ema_raw_rms) >= onset_threshold:
                consecutive_onset += 1
                if consecutive_onset >= min_onset_chunks:
                    in_speech = True
                    speech_accum = consecutive_onset
            else:
                consecutive_onset = 0
        else:
            speech_accum += 1
            if vad_start_chunk is None and speech_accum >= speech_chunks_required:
                vad_start_chunk = idx
                break

    if vad_start_chunk is None:
        return {"triggered": False, "vad_start_chunk": None, "stream_start_chunk": 0}

    stream_start = max(0, vad_start_chunk - pre_roll_chunks)
    return {
        "triggered": True,
        "vad_start_chunk": vad_start_chunk,
        "stream_start_chunk": stream_start,
    }
