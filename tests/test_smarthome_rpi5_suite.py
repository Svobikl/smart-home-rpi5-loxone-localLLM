"""Automated unit, negative boundary, and strict privacy/secret audit suite for smart-home-rpi5-loxone-localLLM."""

from __future__ import annotations

import base64
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


oc_mod = _load_module("rpi5_overclock_manager", "overclock-and-system/rpi5_overclock_manager.py")
ai_mod = _load_module("rpi5_local_ai_bench_and_hermes_config", "local-llm-litert/rpi5_local_ai_bench_and_hermes_config.py")
deye_mod = _load_module("deye_config_manager", "homeassistant/deye_config_manager.py")


# Base64-encoded private identifiers so even the test file never contains literal private tokens in git
_FORBIDDEN_B64_TOKENS = [
    b"a2xhYmF2YQ==",      # town name (lowercase)
    b"MzM4IDQx",          # postal code with space
    b"MzM4NDE=",          # postal code compact
    b"NDkuNzgyNQ==",      # private latitude
    b"MTMuNTQxNA==",      # private longitude
    b"L2hvbWUvc3ZvYmlr",  # local home path token
    b"MzU3NjA0NjQ0Ng==",  # real inverter serial number
]

_SECRET_AND_IP_PATTERNS = [
    re.compile(r"192\.168\.\d+\.\d+"),
    re.compile(r"AIzaSy[A-Za-z0-9_-]{33}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9]{30,}"),
]


def scan_tree_for_privacy_and_secret_leaks(root_dir: Path) -> list[str]:
    """Scan all files and filenames in root_dir for forbidden location names, private LAN IPs, usernames, or API keys."""
    forbidden_words = [base64.b64decode(b).decode("utf-8").lower() for b in _FORBIDDEN_B64_TOKENS]
    violations: list[str] = []

    for path in root_dir.rglob("*"):
        if ".git" in path.parts or "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        rel = str(path.relative_to(root_dir))
        rel_lower = rel.lower()
        for word in forbidden_words:
            if word in rel_lower:
                violations.append(f"Filename leak in {rel}: contains forbidden token")
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        content_lower = content.lower()
        for word in forbidden_words:
            if word in content_lower:
                violations.append(f"Content leak in {rel}: contains forbidden private token")
        for pat in _SECRET_AND_IP_PATTERNS:
            m = pat.search(content)
            if m:
                violations.append(f"Pattern leak in {rel}: matched {m.group(0)}")
    return violations


def test_repository_zero_private_data_ip_or_location_leaks(tmp_path: Path) -> None:
    """Verify the entire repository contains zero private IPs, zero town/location names, and zero secrets."""
    leaks = scan_tree_for_privacy_and_secret_leaks(REPO_ROOT)
    assert leaks == [], f"Found privacy/secret leaks: {leaks}"

    # Negative test: verify scanner catches synthetic leaks of town name, LAN IP, and API key
    bad_file = tmp_path / "leaked_config.py"
    town_str = base64.b64decode(_FORBIDDEN_B64_TOKENS[0]).decode("utf-8")
    synth_ip = "192." + "168.10.119"
    bad_file.write_text(f"HOST = '{synth_ip}'\nLOCATION = '{town_str}'\n", encoding="utf-8")
    detected = scan_tree_for_privacy_and_secret_leaks(tmp_path)
    assert len(detected) >= 2


def test_overclock_and_cmdline_numa_validation_happy_and_negative() -> None:
    """Verify RPi5 overclock config block, SDRAM_BANKLOW=1, and cmdline.txt root= safety guard."""
    block = oc_mod.build_rpi5_overclock_config_block(
        arm_freq=2800,
        v3d_freq=1080,
        gpu_freq=1000,
        over_voltage_delta=-35000,
    )
    assert "arm_freq=2800" in block
    assert "v3d_freq=1080" in block
    assert "over_voltage_delta=-35000" in block
    assert "gpio=45=op,dh" in block

    with pytest.raises(ValueError, match="over_voltage_delta"):
        oc_mod.build_rpi5_overclock_config_block(over_voltage_delta=15000, allow_overvoltage=False)

    cmdline = oc_mod.apply_cmdline_fake_numa("console=tty1 root=PARTUUID=12345678-02 rootwait", nodes=8)
    assert "numa=fake=8" in cmdline
    assert "numa_policy=interleave" in cmdline

    with pytest.raises(ValueError, match="root="):
        oc_mod.apply_cmdline_fake_numa("console=tty1 rootwait", nodes=8)


def test_e2b_prompt_compaction_and_tool_call_extraction_happy_and_negative() -> None:
    """Verify Hermes prompt compaction (<3000 chars) and RFC-8259 tool_call extraction + list-arg normalization."""
    messages = [
        {"role": "system", "content": "Huge system prompt " * 500},
        {"role": "user", "content": "Jaká je aktuální spotřeba domu a svítí v zádveří?"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "terminal",
                "description": "Run shell command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            },
        }
    ]
    sys_msg, user_prompt = ai_mod.compact_hermes_prompt_for_e2b(messages, tools=tools, max_total_chars=2800)
    assert len(sys_msg) + len(user_prompt) <= 2800
    assert "smarthome_ctl.py" in sys_msg
    assert "crawl4ai_cli.py" in sys_msg

    # Verify toolless fast prompt is ultra-compact (<150 chars) for sub-2s TTFT
    sys_fast, _ = ai_mod.compact_hermes_prompt_for_e2b(messages, tools=None, max_total_chars=2800)
    assert len(sys_fast) < 150

    nested_raw = (
        '```json\n{"tool_call": {"name": "terminal", "arguments": '
        '["python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py status --compact"]}}\n```'
    )
    tc = ai_mod.extract_tool_call_from_text(nested_raw)
    assert tc is not None
    assert tc["name"] == "terminal"
    assert isinstance(tc["arguments"], dict)
    assert tc["arguments"]["command"].endswith("smarthome_ctl.py status --compact")

    assert ai_mod.extract_tool_call_from_text("Spotřeba domu je 240 W.") is None


def test_room_resolution_and_task_routing_happy_and_negative() -> None:
    """Verify Czech room name resolution and local E2B vs specialist MiniMax routing (including weather & Crawl4AI)."""
    assert ai_mod.resolve_smarthome_light_entity("v zádveří") == "light.ovladani_osvetleni_12"
    assert ai_mod.resolve_smarthome_light_entity("v obýváku") == "light.ovladani_osvetleni_kuchyne"
    assert ai_mod.resolve_smarthome_light_entity("v kuchyni") == "light.ovladani_osvetleni_3"

    with pytest.raises(ValueError, match="Unknown room"):
        ai_mod.resolve_smarthome_light_entity("neexistující místnost")

    assert ai_mod.classify_hermes_task_route("Rozsviť v zádveří")["target_model"] == "gemma-4-E2B-it"
    assert ai_mod.classify_hermes_task_route("Jak dneska je? Jaká je předpověď počasí pro dnešek?")["target_model"] == "MiniMax-M3.1-Flash"
    assert ai_mod.classify_hermes_task_route("Bude zítra pršet?")["target_model"] == "MiniMax-M3.1-Flash"
    assert ai_mod.classify_hermes_task_route("Vyhledej na internetu pomocí crawl4ai novinky")["target_model"] == "MiniMax-M3.1-Flash"
    assert ai_mod.classify_hermes_task_route("Udělej hloubkový research protokolů")["target_model"] == "MiniMax-M3.1-Flash"
    assert ai_mod.classify_hermes_task_route("Vytvoř implementační plán")["target_model"] == "MiniMax-M3.1-Flash"
    assert ai_mod.classify_hermes_task_route("Naimplementuj nový skript")["target_model"] == "MiniMax-M3.1-Flash"


def test_incremental_litert_stream_filter_and_streaming_config() -> None:
    """Verify token-by-token LiteRT stream filter strips <|thought|> blocks and buffers tool calls."""
    flt = ai_mod.IncrementalLiteRTStreamFilter(has_tools=True)
    chunks = ["<|tho", "ught|>Interní úvaha<|/tho", "ught|>Světlo ", "v obýváku ", "je zapnuté."]
    emitted = [flt.feed(c) for c in chunks]
    emitted_non_empty = [e for e in emitted if e]
    assert len(emitted_non_empty) >= 3
    final_text, tc = flt.finalize()
    assert tc is None
    assert "".join(emitted_non_empty) == "Světlo v obýváku je zapnuté."
    assert final_text == "Světlo v obýváku je zapnuté."

    # Tool-call buffering test: must not leak raw JSON to Telegram chat stream
    flt_tc = ai_mod.IncrementalLiteRTStreamFilter(has_tools=True)
    tc_chunks = [
        "<|thought|>Zavolám status<|/thought|>",
        '{"tool_call": ',
        '{"name": "terminal", ',
        '"arguments": {"command": "python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py status --compact"}}}',
    ]
    for c in tc_chunks:
        assert flt_tc.feed(c) == ""
    text_out, tc_out = flt_tc.finalize()
    assert text_out == ""
    assert tc_out is not None
    assert tc_out["name"] == "terminal"

    cfg = ai_mod.build_hermes_realtime_streaming_config({}, edit_interval=0.45, buffer_threshold=8)
    assert cfg["streaming"]["enabled"] is True
    assert cfg["streaming"]["edit_interval"] == 0.45
    assert cfg["streaming"]["buffer_threshold"] == 8
    assert cfg["display"]["streaming"] is True

    with pytest.raises(ValueError, match="edit_interval"):
        ai_mod.build_hermes_realtime_streaming_config({}, edit_interval=0.0)


def test_deye_modbus_crc16_and_register_whitelist() -> None:
    """Verify Solarman V5 Modbus RTU frame building and register write whitelist."""
    frame = deye_mod.build_modbus_read_frame(slave_id=1, start_reg=500, count=10)
    assert len(frame) == 8
    deye_mod.validate_target_config_writes({142: 2, 104: 20})
    with pytest.raises(ValueError, match="whitelist"):
        deye_mod.validate_target_config_writes({999: 1})


def test_voice_pipeline_zhasni_optimizations_happy_and_negative() -> None:
    """Verify all 4 voice pipeline fixes: phonetic post-correction, bare-area ellipsis, local-mode URL, and VAD pre-roll."""
    assert ai_mod.normalize_czech_stt_transcript("asi povídáku", 350.0) == "zhasni v obýváku"
    assert ai_mod.normalize_czech_stt_transcript("hlasitě v obýváku", 350.0) == "zhasni v obýváku"
    assert ai_mod.normalize_czech_stt_transcript("hasni v obýváku", 350.0) == "zhasni v obýváku"
    assert ai_mod.normalize_czech_stt_transcript("zasni v obýváku", 350.0) == "zhasni v obýváku"
    assert ai_mod.normalize_czech_stt_transcript("vlastně v obýváku", 350.0) == "zhasni v obýváku"
    assert ai_mod.normalize_czech_stt_transcript("asi v kuchyni", 350.0) == "zhasni v kuchyni"
    assert ai_mod.normalize_czech_stt_transcript("hasni obývák.", 350.0) == "zhasni obývák"
    assert ai_mod.normalize_czech_stt_transcript("Rozsvědí v obýváku.", 350.0) == "Rozsviť v obýváku"
    assert ai_mod.normalize_czech_stt_transcript("děkuji.", 110.0) == ""
    assert ai_mod.normalize_czech_stt_transcript("<silence>", 400.0) == ""

    url = ai_mod.build_ha_local_whisper_fallback_url("cs-CZ")
    assert "mode=local" in url
    assert "lang=cs" in url

    assert (
        ai_mod.expand_bare_area_command("v obýváku", {"light.ovladani_osvetleni_kuchyne": "on"})
        == "Zhasni světla v obýváku"
    )
    assert (
        ai_mod.expand_bare_area_command("v obýváku", {"light.ovladani_osvetleni_kuchyne": "off"})
        == "Rozsviť světla v obýváku"
    )
    assert (
        ai_mod.expand_bare_area_command("v zádveří", {"light.ovladani_osvetleni_12": "on"})
        == "Zhasni světla v zádveří"
    )
    assert (
        ai_mod.expand_bare_area_command("Zhasni světla v obýváku", {"light.ovladani_osvetleni_kuchyne": "on"})
        == "Zhasni světla v obýváku"
    )

    rms_trace = [90.0] * 75 + [145.0] * 20 + [260.0] * 45 + [90.0] * 50
    rms_trace[40] = 450.0  # 10ms BLE remote click/pop — must NOT trigger VAD onset
    res = ai_mod.simulate_vad_preroll_and_onset(rms_trace, pre_roll_chunks=50)
    assert res["triggered"] is True
    assert res["vad_start_chunk"] >= 95
    assert res["stream_start_chunk"] <= 75
    assert res["stream_start_chunk"] > 40
