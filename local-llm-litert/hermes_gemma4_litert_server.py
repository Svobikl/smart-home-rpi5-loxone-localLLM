#!/usr/bin/env python3
"""OpenAI-compatible HTTP gateway for Google LiteRT-LM Gemma 4 E2B (real-time token streaming) + MiniMax-M3.1-Flash (specialist router) on RPi5."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litert_lm

MODEL_PATH = os.environ.get(
    "LITERT_MODEL_PATH",
    "/mnt/nvme/ai_models/litert_cache/gemma-4-E2B-it.litertlm",
)
NUM_THREADS = int(os.environ.get("LITERT_THREADS", "4"))
MAX_NUM_TOKENS = int(os.environ.get("LITERT_MAX_NUM_TOKENS", "2048"))
PORT = int(os.environ.get("LITERT_PORT", "8091"))
ENABLE_YNNPACK = os.environ.get("LITERT_ENABLE_YNNPACK", "1") == "1"

SPECIALIST_MODEL = os.environ.get("HERMES_SPECIALIST_MODEL", "MiniMax-M3.1-Flash")
SPECIALIST_OPENAI_URL = os.environ.get("HERMES_SPECIALIST_OPENAI_URL", "https://api.minimax.io/v1/chat/completions")

_PLAN_RE = re.compile(
    r"\b(implementa[cč]n[ií]\s+pl[aá]n|implementation\s+plan|architektonick[yý]\s+pl[aá]n|vytvo[rř]\s+pl[aá]n|create\s+a?\s*plan|n[aá]vrh\s+implementace|design\s+plan)\b",
    re.IGNORECASE,
)
_RESEARCH_RE = re.compile(
    r"("
    r"\b(?:research|deep[\s_-]?research|výzkum\w*|vyzkum\w*|průzkum\w*|pruzkum\w*|re[sš]er[sš]\w*|hloubkov[yý]\s+pr[uů]zkum|deep\s+dive|analyzuj\s+do\s+hloubky|porovnej\s+architektury|porovnání\s+protokol\w*|literature\s+review|literatur\w*)\b"
    r"|\b(?:počas[ií]\w*|pocas[ií]\w*|předpově[dď]\w*|predpove[dď]\w*|weather\w*|forecast\w*|meteogram\w*|srážk\w*|srazk\w*|bouřk\w*|bourk\w*|pr[sš]et\w*|sn[eě][zž]it\w*)\b"
    r"|\b(?:jak\s+dnes(?:ka)?\s+je|jak\s+je\s+venku|jak\s+bude\s+z[ií]tra|bude\s+pr[sš]et)\b"
    r"|\b(?:vyhledej\w*|najdi\s+na\s+(?:internetu|webu)|prohledej\s+(?:internet|web)|crawl4ai|crwl|zprávy\s+z\s+internetu|aktuality)\b"
    r"|https?://\S+"
    r")",
    re.IGNORECASE,
)
_IMPL_RE = re.compile(
    r"\b(implementuj|naimplementuj|implement\b|refaktoruj|refactor|napi[sš]\s+k[oó]d|write\s+code|uprav\s+skript|oprav\s+bug|fix\s+bug|vytvo[rř]\s+skript|programov[aá]n[ií])\b",
    re.IGNORECASE,
)

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
    "- Otevření vstupních dveří (zádveří): python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py door open\n"
    "- Rychlá paměť: python3 ~/.hermes/skills/rpi5-fast-memory/scripts/fast_mem.py search \"<dotaz>\"\n"
    "- Zdraví RPi5 a služeb: python3 ~/.hermes/skills/heartbeat-monitor/scripts/heartbeat_check.py --compact"
)

_PRIORITY_TOOLS = ("terminal", "web_search", "web_extract", "memory", "delegate_task", "skill_view", "read_file", "write_file", "search_files")


def load_minimax_api_key():
    env_file = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("MINIMAX_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("MINIMAX_API_KEY", "")


def classify_task_category(text: str) -> str:
    if not text:
        return "general_local"
    if _PLAN_RE.search(text):
        return "implementation_plan"
    if _RESEARCH_RE.search(text):
        return "research"
    if _IMPL_RE.search(text):
        return "implementation"
    return "general_local"


def compact_prompt_for_e2b(messages, tools=None, max_total_chars=2800):
    convo_parts = []
    last_role = messages[-1].get("role", "user") if messages else "user"
    if last_role == "tool":
        latest_user = ""
        for m in reversed(messages[:-1]):
            if m.get("role") == "user":
                c = m.get("content", "")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
                latest_user = str(c or "").strip()
                break
        tool_msg = messages[-1]
        t_name = tool_msg.get("name") or tool_msg.get("tool_call_id") or "tool"
        t_out = str(tool_msg.get("content") or "").strip()[:700]
        if latest_user:
            convo_parts.append(f"Uživatel: {latest_user[:240]}")
        convo_parts.append(f"Výsledek nástroje ({t_name}):\n{t_out}")
    else:
        for m in reversed(messages):
            if m.get("role") == "user":
                c = m.get("content", "")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
                convo_parts.append(f"Uživatel: {str(c or '').strip()[:500]}")
                break

    tool_lines = []
    if tools and last_role != "tool":
        by_name = {}
        for t in tools:
            fn = t.get("function", t) if isinstance(t, dict) else {}
            if isinstance(fn, dict) and fn.get("name"):
                by_name[str(fn["name"])] = fn
        selected = [n for n in _PRIORITY_TOOLS if n in by_name]
        if not selected:
            selected = list(by_name.keys())[:6]
        for name in selected:
            fn = by_name[name]
            props = fn.get("parameters", {}).get("properties", {})
            arg_keys = ", ".join(list(props.keys())[:4])
            tool_lines.append(f"- {name}({arg_keys})")

    if tool_lines:
        sys_msg = (
            _E2B_SMART_HOME_CHEATSHEET
            + "\nPokud potřebuješ zavolat nástroj, vypiš POUZE JSON na jeden řádek v tomto formátu:\n"
            + '{"tool_call": {"name": "terminal", "arguments": {"command": "python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py status --compact"}}}\n'
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


print(f"[hermes-gemma4] Loading {MODEL_PATH} with {NUM_THREADS} threads, max_num_tokens={MAX_NUM_TOKENS}, ynnpack={ENABLE_YNNPACK}...", flush=True)
t0 = time.perf_counter()
litert_lm.set_min_log_severity(2)
import inspect
_eng_params = inspect.signature(litert_lm.Engine.__init__).parameters
_cpu_params = inspect.signature(litert_lm.Backend.CPU.__init__).parameters
_cpu_kwargs = {"thread_count": NUM_THREADS} if "thread_count" in _cpu_params else {}
_eng_kwargs = {"backend": litert_lm.Backend.CPU(**_cpu_kwargs), "max_num_tokens": MAX_NUM_TOKENS}
if "enable_ynnpack" in _eng_params:
    _eng_kwargs["enable_ynnpack"] = ENABLE_YNNPACK
if "num_cpu_threads" in _eng_params:
    _eng_kwargs["num_cpu_threads"] = NUM_THREADS
ENGINE = litert_lm.Engine(MODEL_PATH, **_eng_kwargs)
LOCK = threading.Lock()
print(f"[hermes-gemma4] Engine ready in {time.perf_counter() - t0:.2f}s", flush=True)


def _normalize_tool_arguments(name, raw_args):
    args = raw_args
    if isinstance(args, str):
        try:
            args = json.loads(args)
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
        cmd_str = str(args.get("command", "")).replace("/home/user/.hermes", os.path.expanduser("~/.hermes"))
        args["command"] = cmd_str
        return args
    if isinstance(args, dict):
        return args
    if isinstance(args, list) and args:
        return {"name": str(args[0])} if name == "skill_view" else {"query": " ".join(str(x) for x in args)}
    return {"value": str(args)}


def extract_tool_call(text):
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
            return {"name": t_name, "arguments": _normalize_tool_arguments(t_name, tc.get("arguments", {}))}
        if obj.get("name") and "arguments" in obj:
            t_name = str(obj["name"])
            return {"name": t_name, "arguments": _normalize_tool_arguments(t_name, obj.get("arguments", {}))}
    return None


class IncrementalLiteRTStreamFilter:
    """Real-time token-by-token filter for LiteRT-LM async chunks that strips <|thought|> blocks and detects JSON tool calls."""

    _OPEN_TAG = "<|thought|>"
    _CLOSE_TAG = "<|/thought|>"

    def __init__(self, has_tools: bool = False):
        self.has_tools = bool(has_tools)
        self._in_thought = False
        self._pending = ""
        self._decided_mode = None  # "text" | "tool_buffer"
        self.visible_text = ""

    def feed(self, token_text: str) -> str:
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
        out = []
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

    def finalize(self):
        if not self._in_thought and self._pending:
            self.visible_text += self._pending
        self._pending = ""
        cleaned = self.visible_text.strip()
        if self.has_tools:
            tc = extract_tool_call(cleaned)
            if tc is not None:
                return "", tc
        return cleaned, None


def forward_to_minimax(payload: dict, task_category: str):
    api_key = load_minimax_api_key()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not found for specialist routing")
    out_payload = dict(payload)
    out_payload["model"] = SPECIALIST_MODEL
    out_payload["stream"] = False
    msgs = list(out_payload.get("messages") or [])
    specialist_hint = (
        "\n\n[RPi5 Specialist Research & Smart-Home Cheatsheet]\n"
        "- Pro dotazy na počasí a předpověď (dnes i další dny) IHNED zavolej nástroj terminal s příkazem:\n"
        "  python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py weather\n"
        "- Pro vyhledávání na internetu pomocí Crawl4AI zavolej nástroj terminal s příkazem:\n"
        "  python3 ~/.hermes/skills/crawl4ai-web/scripts/crawl4ai_cli.py search \"<dotaz>\"\n"
        "- Pro stažení konkrétní webové stránky do Markdownu (Crawl4AI):\n"
        "  python3 ~/.hermes/skills/crawl4ai-web/scripts/crawl4ai_cli.py crawl \"<url>\"\n"
        "- Pro stav domu (spotřeba, FVE, teploty, světla):\n"
        "  python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py status --compact\n"
        "Odpovídej vždy česky, věcně a s konkrétními čísly."
    )
    if msgs and msgs[0].get("role") == "system":
        first_msg = dict(msgs[0])
        first_msg["content"] = str(first_msg.get("content", "")) + specialist_hint
        msgs[0] = first_msg
    else:
        msgs.insert(0, {"role": "system", "content": specialist_hint.strip()})
    out_payload["messages"] = msgs

    req = urllib.request.Request(
        SPECIALIST_OPENAI_URL,
        data=json.dumps(out_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        choices = res_data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            m_obj = choices[0].get("message") or {}
            if isinstance(m_obj.get("content"), str):
                m_obj["content"] = re.sub(r"<think>.*?</think>", "", m_obj["content"], flags=re.DOTALL).strip()
        res_data["x_routed_provider"] = "minimax"
        res_data["x_routed_model"] = SPECIALIST_MODEL
        res_data["x_task_category"] = task_category
        return res_data


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _emit_sse_chunk(self, chunk_dict: dict):
        line = f"data: {json.dumps(chunk_dict, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(line)
        self.wfile.flush()

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send_json(200, {
                "status": "ok",
                "default_model": "gemma-4-E2B-it",
                "specialist_model": SPECIALIST_MODEL,
                "specialist_categories": ["research", "implementation", "implementation_plan"],
                "backend": "litert_lm_cpu_ynnpack" if ENABLE_YNNPACK else "litert_lm_cpu_xnnpack",
                "threads": NUM_THREADS,
                "max_num_tokens": MAX_NUM_TOKENS,
                "enable_ynnpack": ENABLE_YNNPACK,
                "realtime_token_streaming": True,
            })
            return
        if self.path in ("/v1/props", "/props"):
            self._send_json(200, {
                "default_generation_settings": {
                    "n_ctx": 131072,
                }
            })
            return
        if self.path.startswith("/v1/models") or self.path.startswith("/api/v1/models") or self.path.startswith("/api/tags"):
            model_entry = {
                "id": "gemma-4-E2B-it",
                "object": "model",
                "owned_by": "google-litert-local",
                "context_window": 131072,
                "context_length": 131072,
                "max_model_len": 131072,
            }
            if "/models/" in self.path:
                self._send_json(200, model_entry)
                return
            self._send_json(200, {
                "object": "list",
                "data": [
                    model_entry,
                    {
                        "id": SPECIALIST_MODEL,
                        "object": "model",
                        "owned_by": "minimax-specialist",
                        "context_window": 200000,
                        "context_length": 200000,
                        "max_model_len": 200000,
                    },
                ],
            })
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/api/show", "/v1/api/show"):
            self._send_json(200, {
                "model_info": {
                    "gemma4.context_length": 131072,
                }
            })
            return
        if not self.path.startswith("/v1/chat/completions"):
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw_body) if raw_body else {}
        messages = data.get("messages", [])
        tools = data.get("tools", [])
        req_model = str(data.get("model", "gemma-4-E2B-it"))
        stream_req = bool(data.get("stream", False))

        last_user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                c = m.get("content", "")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
                last_user_text = str(c)
                break

        task_cat = classify_task_category(last_user_text)
        # Fast-path for Hermes automatic session title generation prompts (saves ~18s after chat completion)
        full_msgs_str = " ".join(str(m.get("content", "")) for m in messages).lower()
        if not tools and ("session title" in full_msgs_str or "short title" in full_msgs_str or "concise title" in full_msgs_str or "generate a title" in full_msgs_str):
            words = re.sub(r"[^\w\sáéěíóúůýčďňřšťžÁÉĚÍÓÚŮÝČĎŇŘŠŤŽ]", "", last_user_text).split()
            short_title = " ".join(words[:5]).strip() or "Chytrá domácnost MySmartHome"
            self._send_json(200, {
                "id": f"chatcmpl-title-{int(time.time()*1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "gemma-4-E2B-it",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": short_title},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })
            return

        use_specialist = (
            req_model.lower().startswith("minimax")
            or task_cat in ("research", "implementation", "implementation_plan")
        )

        if use_specialist:
            try:
                res_minimax = forward_to_minimax(data, task_cat)
                if stream_req:
                    choices = res_minimax.get("choices") or [{}]
                    msg_obj = choices[0].get("message") or {}
                    finish_reason = choices[0].get("finish_reason") or ("tool_calls" if msg_obj.get("tool_calls") else "stop")
                    cmpl_id = res_minimax.get("id") or f"chatcmpl-minimax-{int(time.time()*1000)}"
                    created_ts = int(res_minimax.get("created") or time.time())
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    delta_obj = {"role": "assistant"}
                    if msg_obj.get("tool_calls"):
                        tcs_out = []
                        for idx, tc0 in enumerate(msg_obj["tool_calls"]):
                            tcs_out.append({
                                "index": idx,
                                "id": tc0.get("id", f"call_{int(time.time()*1000)}_{idx}"),
                                "type": "function",
                                "function": tc0.get("function", {}),
                            })
                        delta_obj["tool_calls"] = tcs_out
                        if msg_obj.get("content"):
                            delta_obj["content"] = msg_obj["content"]
                    else:
                        delta_obj["content"] = msg_obj.get("content") or ""
                    self._emit_sse_chunk({
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": SPECIALIST_MODEL,
                        "choices": [{"index": 0, "delta": delta_obj, "finish_reason": None}],
                    })
                    self._emit_sse_chunk({
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": SPECIALIST_MODEL,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                        "usage": res_minimax.get("usage", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}),
                    })
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                self._send_json(200, res_minimax)
                return
            except Exception as e:
                print(f"[hermes-gemma4] Specialist MiniMax failed ({e}), falling back to local Gemma 4 E2B...", flush=True)

        sys_msg, user_prompt = compact_prompt_for_e2b(messages, tools=tools, max_total_chars=2800)
        cmpl_id = f"chatcmpl-litert-{int(time.time()*1000)}"
        created_ts = int(time.time())
        model_name = data.get("model", "gemma-4-E2B-it")

        try:
            t_start = time.perf_counter()
            if stream_req:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                stream_filter = IncrementalLiteRTStreamFilter(has_tools=bool(tools))
                role_sent = False
                streamed_chars = 0
                ttft_ms = None

                with LOCK:
                    with ENGINE.create_conversation(
                        system_message=sys_msg,
                        thinking_config=litert_lm.ThinkingConfig(enable_thinking=False, thinking_token_budget=0),
                    ) as conv:
                        for chunk in conv.send_message_async(user_prompt):
                            piece = ""
                            if isinstance(chunk, dict):
                                for item in chunk.get("content", []):
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        piece += str(item.get("text", ""))
                            else:
                                piece = str(chunk)
                            if not piece:
                                continue

                            emit_delta = stream_filter.feed(piece)
                            if emit_delta:
                                if ttft_ms is None:
                                    ttft_ms = (time.perf_counter() - t_start) * 1000.0
                                delta_payload = {"content": emit_delta}
                                if not role_sent:
                                    delta_payload["role"] = "assistant"
                                    role_sent = True
                                streamed_chars += len(emit_delta)
                                self._emit_sse_chunk({
                                    "id": cmpl_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model_name,
                                    "choices": [{"index": 0, "delta": delta_payload, "finish_reason": None}],
                                })

                final_text, tc = stream_filter.finalize()
                elapsed_ms = (time.perf_counter() - t_start) * 1000.0

                if tc is not None:
                    args_val = tc.get("arguments", {})
                    if not isinstance(args_val, str):
                        args_val = json.dumps(args_val, ensure_ascii=False)
                    tc_delta = {
                        "role": "assistant",
                        "tool_calls": [{
                            "index": 0,
                            "id": f"call_{int(time.time()*1000)}",
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": args_val,
                            },
                        }],
                    }
                    self._emit_sse_chunk({
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [{"index": 0, "delta": tc_delta, "finish_reason": None}],
                    })
                    finish_reason = "tool_calls"
                else:
                    if streamed_chars == 0 and final_text:
                        if ttft_ms is None:
                            ttft_ms = elapsed_ms
                        self._emit_sse_chunk({
                            "id": cmpl_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model_name,
                            "choices": [{"index": 0, "delta": {"role": "assistant", "content": final_text}, "finish_reason": None}],
                        })
                    finish_reason = "stop"

                prompt_toks = len(sys_msg.split()) + len(user_prompt.split())
                compl_toks = max(1, len((final_text or "").split()))
                self._emit_sse_chunk({
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                    "usage": {
                        "prompt_tokens": prompt_toks,
                        "completion_tokens": compl_toks,
                        "total_tokens": prompt_toks + compl_toks,
                    },
                })
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                print(
                    f"[hermes-gemma4] REALTIME-SSE finish={finish_reason} tc={tc['name'] if tc else None} "
                    f"ttft={ttft_ms or elapsed_ms:.1f}ms total={elapsed_ms:.1f}ms text={final_text[:100]!r}",
                    flush=True,
                )
                return

            # Non-streaming request path
            with LOCK:
                with ENGINE.create_conversation(
                    system_message=sys_msg,
                    thinking_config=litert_lm.ThinkingConfig(enable_thinking=False, thinking_token_budget=0),
                ) as conv:
                    resp = conv.send_message(user_prompt)
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            raw_text = resp.get("content", [{"text": str(resp)}])[0].get("text", "") if isinstance(resp, dict) else str(resp)
            cleaned = re.sub(r"<\|thought\|>.*?<\|/thought\|>", "", raw_text, flags=re.DOTALL).strip()

            tc = extract_tool_call(cleaned) if tools else None
            if tc:
                args_val = tc.get("arguments", {})
                if not isinstance(args_val, str):
                    args_val = json.dumps(args_val, ensure_ascii=False)
                msg_obj = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": f"call_{int(time.time()*1000)}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": args_val,
                        },
                    }],
                }
                finish_reason = "tool_calls"
            else:
                msg_obj = {
                    "role": "assistant",
                    "content": cleaned,
                }
                finish_reason = "stop"

            print(f"[hermes-gemma4] JSON finish={finish_reason} tc={tc['name'] if tc else None} latency={elapsed_ms:.1f}ms raw={cleaned[:120]!r}", flush=True)
            self._send_json(200, {
                "id": cmpl_id,
                "object": "chat.completion",
                "created": created_ts,
                "model": model_name,
                "choices": [{
                    "index": 0,
                    "message": msg_obj,
                    "finish_reason": finish_reason,
                }],
                "usage": {
                    "prompt_tokens": len(sys_msg.split()) + len(user_prompt.split()),
                    "completion_tokens": len(cleaned.split()),
                    "total_tokens": len(sys_msg.split()) + len(user_prompt.split()) + len(cleaned.split()),
                },
                "x_litert_latency_ms": round(elapsed_ms, 1),
                "x_routed_provider": "local-litert-ynnpack",
                "x_routed_model": "gemma-4-E2B-it",
                "x_task_category": task_cat,
            })
        except Exception as e:
            import traceback
            print(f"[hermes-gemma4] ERROR in do_POST: {e}", flush=True)
            traceback.print_exc()
            if not stream_req:
                self._send_json(500, {"error": str(e)})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"[hermes-gemma4] Listening on http://localhost:{PORT}/v1 (real-time token streaming enabled)", flush=True)
    srv.serve_forever()
