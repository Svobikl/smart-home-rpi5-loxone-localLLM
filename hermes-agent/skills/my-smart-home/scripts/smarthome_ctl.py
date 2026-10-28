#!/usr/bin/env python3
"""Secure MySmartHome Smart Home controller for Hermes Agent (Optimized for Gemma 4 E2B & MiniMax-M3.1-Flash).
Loads credentials internally from ~/.hermes/.env (0600) and never prints secret values.
Supports 1-step Czech room resolution & weather forecast:
  smarthome_ctl.py status [--compact]
  smarthome_ctl.py weather
  smarthome_ctl.py lights
  smarthome_ctl.py light "<místnost_nebo_entity_id>" <on|off|toggle>
  smarthome_ctl.py blinds "<místnost>" <open|close|stop>
"""
import json
import os
import socket
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path.home() / ".hermes" / ".env"
SECRET_KEYS = {
    "MINIMAX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "HA_TOKEN",
    "DEYE_PSWD",
    "LOXONE_PSWD",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
}

LIGHT_ALIASES = [
    (("zadveri", "vstup", "predsin"), "light.ovladani_osvetleni_12", "Zádveří – Osvětlení"),
    (("obyvak", "obyvac", "obyvaci"), "light.ovladani_osvetleni_kuchyne", "Obývací pokoj – Osvětlení"),
    (("kuchyn", "kuchyni", "kuchyne"), "light.ovladani_osvetleni_3", "Kuchyně – Osvětlení"),
    (("chodb",), "light.ovladani_osvetleni", "Chodba – Osvětlení"),
    (("garaz",), "light.ovladani_osvetleni_2", "Garáž – Osvětlení"),
    (("pracovn", "office", "kancelar"), "light.ovladani_osvetleni_7", "Pracovna (Office) – Osvětlení"),
    (("mala koupeln", "male koupeln"), "light.ovladani_osvetleni_mala_koupelna", "Malá koupelna – Osvětlení"),
    (("velka koupeln", "velke koupeln", "koupeln"), "light.ovladani_osvetleni_9", "Velká koupelna – Osvětlení"),
    (("loznice 2", "loznici 2", "detsky 1"), "light.ovladani_osvetleni_5", "Ložnice 2 – Osvětlení"),
    (("loznice 3", "loznici 3", "detsky 2"), "light.ovladani_osvetleni_6", "Ložnice 3 – Osvětlení"),
    (("loznice 1", "loznici 1", "loznic"), "light.ovladani_osvetleni_4", "Ložnice 1 – Osvětlení"),
    (("technick",), "light.ovladani_osvetleni_8", "Technická – Osvětlení"),
    (("wc 2", "zachod 2", "toaleta 2"), "light.ovladani_osvetleni_11", "WC 2 – Osvětlení"),
    (("wc 1", "wc", "zachod", "toaleta"), "light.ovladani_osvetleni_10", "WC 1 – Osvětlení"),
    (("satn",), "light.ovladani_osvetleni_13", "Šatna – Osvětlení"),
    (("saun", "hvezdn"), "switch.star_light_rozsviceni_stropu_v_saune", "Sauna – Hvězdná obloha"),
]


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()


def resolve_light(room_or_entity: str) -> tuple[str, str]:
    raw = (room_or_entity or "").strip()
    if raw.startswith("light.") or raw.startswith("switch."):
        return raw, raw
    norm = strip_accents(raw)
    for keywords, eid, fname in LIGHT_ALIASES:
        if any(kw in norm for kw in keywords):
            return eid, fname
    raise ValueError(f"Neznámá místnost nebo světlo: {room_or_entity!r}")


def load_secure_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k not in env:
            env[k] = v
    return env


def scrub_secrets(text: str, env: dict) -> str:
    if not text:
        return text
    out = str(text)
    for k in SECRET_KEYS:
        val = env.get(k, "")
        if val and len(val) >= 4 and val in out:
            out = out.replace(val, f"[REDACTED:{k}]")
    return out


def ha_request(env: dict, method: str, path: str, payload: dict = None) -> object:
    base_url = env.get("HA_URL", "http://localhost:8123").rstrip("/")
    token = env.get("HA_TOKEN", "")
    if not token:
        raise RuntimeError("HA_TOKEN missing in ~/.hermes/.env")
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def tcp_ping(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def cmd_weather(env: dict) -> str:
    states = ha_request(env, "GET", "/api/states")
    by_id = {s.get("entity_id"): s for s in states if isinstance(s, dict)}

    def fmt(eid: str) -> str:
        s = by_id.get(eid)
        if not s:
            return "N/A"
        st = s.get("state", "N/A")
        unit = s.get("attributes", {}).get("unit_of_measurement", "")
        return f"{st} {unit}".strip()

    om = by_id.get("sensor.open_meteo_mysmarthome", {})
    om_attrs = om.get("attributes", {}) if isinstance(om, dict) else {}
    daily = om_attrs.get("daily", {}) if isinstance(om_attrs, dict) else {}

    wf = by_id.get("weather.forecast_home", {})
    wf_attrs = wf.get("attributes", {}) if isinstance(wf, dict) else {}

    lines = [
        "Počasí a předpověď — MySmartHome (Open-Meteo + Met.no):",
        f"- Aktuálně venku: stav '{fmt('sensor.mysmarthome_aktualni_pocasi')}' ({wf.get('state', 'N/A')}), teplota {fmt('sensor.mysmarthome_venkovni_teplota')} (pocitová {fmt('sensor.mysmarthome_pocitova_teplota')}), vlhkost {fmt('sensor.mysmarthome_venkovni_vlhkost')}",
        f"- Vítr a tlak: vítr {fmt('sensor.mysmarthome_rychlost_vetru')} (nárazy {fmt('sensor.mysmarthome_narazy_vetru')}), tlak {wf_attrs.get('pressure', 'N/A')} hPa, oblačnost {wf_attrs.get('cloud_coverage', 'N/A')} %, UV {wf_attrs.get('uv_index', 'N/A')}",
        f"- Dnešní extrémy a srážky: max {fmt('sensor.mysmarthome_dnesni_maximum')}, min {fmt('sensor.mysmarthome_dnesni_minimum')}, srážky dnes {fmt('sensor.mysmarthome_srazky_dnes')} (pravděpodobnost {fmt('sensor.mysmarthome_pravdepodobnost_srazek')}), predikce výroby FVE dnes {fmt('sensor.mysmarthome_predikce_fve_dnes')}",
    ]

    dates = daily.get("time") or []
    t_max = daily.get("temperature_2m_max") or []
    t_min = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_sum") or []
    prob = daily.get("precipitation_probability_max") or []
    rad = daily.get("shortwave_radiation_sum") or []
    if dates:
        lines.append("- Vícedenní předpověď (Open-Meteo):")
        for idx in range(min(5, len(dates))):
            d_str = dates[idx]
            hi = t_max[idx] if idx < len(t_max) else "?"
            lo = t_min[idx] if idx < len(t_min) else "?"
            pr = precip[idx] if idx < len(precip) else 0
            pb = prob[idx] if idx < len(prob) else 0
            rd = rad[idx] if idx < len(rad) else "?"
            lines.append(f"  * {d_str}: min {lo} °C / max {hi} °C, srážky {pr} mm (pravděpodobnost {pb} %), solární záření {rd} MJ/m²")
    return "\n".join(lines)


def cmd_status(env: dict, compact: bool = False) -> object:
    states = ha_request(env, "GET", "/api/states")
    by_id = {s.get("entity_id"): s for s in states if isinstance(s, dict)}

    def fmt(eid: str) -> str:
        s = by_id.get(eid)
        if not s:
            return "N/A"
        st = s.get("state", "N/A")
        unit = s.get("attributes", {}).get("unit_of_measurement", "")
        return f"{st} {unit}".strip()

    lit_rooms = []
    for _kw, eid, fname in LIGHT_ALIASES:
        s = by_id.get(eid)
        if s and s.get("state") == "on":
            lit_rooms.append(fname.split("–")[0].strip())

    if compact:
        lines = [
            "MySmartHome — Aktuální přehled:",
            f"- Spotřeba domu: {fmt('sensor.spotreba_domu_celkem')} (L1: {fmt('sensor.spotreba_domu_faze_l1')}, L2: {fmt('sensor.spotreba_domu_faze_l2')}, L3: {fmt('sensor.spotreba_domu_faze_l3')})",
            f"- FVE Deye 15kW: aktuálně {fmt('sensor.fve_celkovy_vykon')} | výroba dnes: {fmt('sensor.fve_vyroba_dnes_2')} (predikce {fmt('sensor.mysmarthome_predikce_fve_dnes')}) | střídač: {fmt('sensor.teplota_stridace_deye')}",
            f"- Síť a spotřeba dnes: odběr ze sítě dnes {fmt('sensor.odber_ze_site_celkem_2')} | přetoky dnes {fmt('sensor.pretoky_do_site_celkem_2')} | aktuální tok sítě: {fmt('sensor.sit_aktualni_odber_a_pretok')}",
            f"- Baterie: {fmt('sensor.baterie_stav_nabiti')} ({fmt('sensor.baterie_stav')})",
            f"- Venkovní počasí MySmartHome: {fmt('sensor.mysmarthome_aktualni_pocasi')}, teplota {fmt('sensor.mysmarthome_venkovni_teplota')} (pocitová {fmt('sensor.mysmarthome_pocitova_teplota')}, dnes min {fmt('sensor.mysmarthome_dnesni_minimum')} / max {fmt('sensor.mysmarthome_dnesni_maximum')}), vlhkost {fmt('sensor.mysmarthome_venkovni_vlhkost')}, vítr {fmt('sensor.mysmarthome_rychlost_vetru')}, srážky dnes {fmt('sensor.mysmarthome_srazky_dnes')} ({fmt('sensor.mysmarthome_pravdepodobnost_srazek')})",
            f"- Rozsvícená světla: {', '.join(lit_rooms) if lit_rooms else 'žádné (všechna zhasnutá)'}",
        ]
        return "\n".join(lines)

    return {
        "ok": True,
        "house": "MySmartHome",
        "total_entities": len(states),
        "energy_and_climate": {
            "spotreba_domu_celkem": fmt("sensor.spotreba_domu_celkem"),
            "sit_aktualni_odber_a_pretok": fmt("sensor.sit_aktualni_odber_a_pretok"),
            "fve_celkovy_vykon": fmt("sensor.fve_celkovy_vykon"),
            "solarni_vyroba_dnes": fmt("sensor.fve_vyroba_dnes_2"),
            "predikce_fve_dnes": fmt("sensor.mysmarthome_predikce_fve_dnes"),
            "odber_ze_site_dnes": fmt("sensor.odber_ze_site_celkem_2"),
            "pretoky_do_site_dnes": fmt("sensor.pretoky_do_site_celkem_2"),
            "baterie_stav_nabiti": fmt("sensor.baterie_stav_nabiti"),
            "baterie_stav": fmt("sensor.baterie_stav"),
            "teplota_stridace_deye": fmt("sensor.teplota_stridace_deye"),
            "venkovni_teplota": fmt("sensor.mysmarthome_venkovni_teplota"),
            "pocitova_teplota": fmt("sensor.mysmarthome_pocitova_teplota"),
            "venkovni_vlhkost": fmt("sensor.mysmarthome_venkovni_vlhkost"),
            "aktualni_pocasi": fmt("sensor.mysmarthome_aktualni_pocasi"),
            "dnesni_maximum": fmt("sensor.mysmarthome_dnesni_maximum"),
            "dnesni_minimum": fmt("sensor.mysmarthome_dnesni_minimum"),
            "srazky_dnes": fmt("sensor.mysmarthome_srazky_dnes"),
            "pravdepodobnost_srazek": fmt("sensor.mysmarthome_pravdepodobnost_srazek"),
            "rozsvicena_svetla": lit_rooms,
        },
        "additional_energy_sensors": {
            "sensor.fve_celkovy_vykon": fmt("sensor.fve_celkovy_vykon"),
            "sensor.spotreba_domu_celkem": fmt("sensor.spotreba_domu_celkem"),
            "sensor.spotreba_domu_faze_l1": fmt("sensor.spotreba_domu_faze_l1"),
            "sensor.spotreba_domu_faze_l2": fmt("sensor.spotreba_domu_faze_l2"),
            "sensor.spotreba_domu_faze_l3": fmt("sensor.spotreba_domu_faze_l3"),
            "sensor.baterie_stav_nabiti": fmt("sensor.baterie_stav_nabiti"),
            "sensor.teplota_stridace_deye": fmt("sensor.teplota_stridace_deye"),
        },
        "devices_lan": {
            "home_assistant_8123": tcp_ping("localhost", 8123),
            "matter_server_5580": tcp_ping("localhost", 5580),
            "deye_fve_15kw": tcp_ping(env.get("DEYE_IP", "<DEYE_INVERTER_HOST>"), 80),
            "loxone_miniserver": tcp_ping(env.get("LOXONE_IP", "<LOXONE_MINISERVER_HOST>"), 80),
        },
    }


def cmd_lights(env: dict) -> str:
    states = ha_request(env, "GET", "/api/states")
    by_id = {s.get("entity_id"): s for s in states if isinstance(s, dict)}
    parts = []
    lit = []
    for _kw, eid, fname in LIGHT_ALIASES:
        s = by_id.get(eid)
        st = s.get("state", "unknown") if s else "unknown"
        short_name = fname.split("–")[0].strip()
        parts.append(f"{short_name}: {st}")
        if st == "on":
            lit.append(short_name)
    header = f"Rozsvíceno ({len(lit)}): {', '.join(lit)}" if lit else "Rozsvíceno (0): všechna světla jsou zhasnutá"
    return header + "\n" + " | ".join(parts)


def cmd_light_action(env: dict, room_or_entity: str, action: str) -> str:
    eid, fname = resolve_light(room_or_entity)
    domain = eid.split(".")[0]
    act = action.strip().lower()
    if act in ("on", "zapni", "rozsvit", "rozsviť", "turn_on", "1", "true"):
        svc = "turn_on"
    elif act in ("off", "vypni", "zhasni", "turn_off", "0", "false"):
        svc = "turn_off"
    elif act in ("toggle", "prepni", "přepni"):
        svc = "toggle"
    else:
        raise ValueError(f"Neplatná akce {action!r}, použij: on | off | toggle")
    ha_request(env, "POST", f"/api/services/{domain}/{svc}", {"entity_id": eid})
    target = {"turn_on": "on", "turn_off": "off"}.get(svc)
    new_st = None
    for _ in range(8):
        st_obj = ha_request(env, "GET", f"/api/states/{eid}")
        new_st = st_obj.get("state", "unknown") if isinstance(st_obj, dict) else "unknown"
        if target is None or new_st == target:
            break
        time.sleep(0.4)
    return f"OK: {fname} ({eid}) -> {new_st}"


def cmd_blinds_action(env: dict, room: str, action: str) -> str:
    eid = "cover.automaticke_stineni"
    act = action.strip().lower()
    if act in ("open", "otevri", "otevři", "nahoru", "up"):
        svc = "open_cover"
    elif act in ("close", "zavri", "zavři", "dolu", "dolů", "down"):
        svc = "close_cover"
    elif act in ("stop", "zastav"):
        svc = "stop_cover"
    else:
        raise ValueError(f"Neplatná akce pro žaluzie: {action!r}, použij: open | close | stop")
    ha_request(env, "POST", f"/api/services/cover/{svc}", {"entity_id": eid})
    return f"OK: Obývací pokoj – Žaluzie ({eid}) -> {svc}"


def cmd_env_audit(env: dict) -> dict:
    mode = oct(stat.S_IMODE(ENV_PATH.stat().st_mode)) if ENV_PATH.exists() else "missing"
    return {
        "ok": True,
        "env_file": str(ENV_PATH),
        "permissions": mode,
        "permissions_ok": mode == "0o600",
        "present_keys": [k for k in sorted(SECRET_KEYS) if bool(env.get(k))],
        "secrets_exposed": False,
    }


def main():
    env = load_secure_env()
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print("Usage: smarthome_ctl.py <status [--compact]|weather|lights|light <room> <on|off|toggle>|blinds <room> <open|close|stop>|entity|list|call|assist|env-audit>")
        return 0
    cmd = args[0]
    try:
        if cmd in ("cat-env", "dump-secrets", "print-secrets"):
            print(json.dumps({"ok": False, "error": "SECURITY_POLICY_VIOLATION: Direct secret dumping is strictly forbidden."}, ensure_ascii=False))
            return 2
        if cmd == "status":
            compact = "--compact" in args or "-c" in args
            res = cmd_status(env, compact=compact)
            if isinstance(res, str):
                print(scrub_secrets(res, env))
                return 0
        elif cmd == "weather":
            print(scrub_secrets(cmd_weather(env), env))
            return 0
        elif cmd == "lights":
            print(scrub_secrets(cmd_lights(env), env))
            return 0
        elif cmd == "light":
            if len(args) < 3:
                raise ValueError("Usage: smarthome_ctl.py light <místnost> <on|off|toggle>")
            print(scrub_secrets(cmd_light_action(env, args[1], args[2]), env))
            return 0
        elif cmd == "blinds":
            if len(args) < 3:
                raise ValueError("Usage: smarthome_ctl.py blinds <místnost> <open|close|stop>")
            print(scrub_secrets(cmd_blinds_action(env, args[1], args[2]), env))
            return 0
        elif cmd == "env-audit":
            res = cmd_env_audit(env)
        elif cmd == "entity":
            if len(args) < 2:
                raise ValueError("Usage: smarthome_ctl.py entity <entity_id>")
            res = ha_request(env, "GET", f"/api/states/{args[1]}")
        elif cmd == "list":
            domain = args[1] if len(args) > 1 else ""
            states = ha_request(env, "GET", "/api/states")
            items = []
            for s in states:
                eid = s.get("entity_id", "")
                if not domain or eid.startswith(domain + "."):
                    items.append({
                        "entity_id": eid,
                        "state": s.get("state"),
                        "friendly_name": s.get("attributes", {}).get("friendly_name", ""),
                    })
            res = {"ok": True, "domain": domain or "all", "count": len(items), "entities": items[:100]}
        elif cmd == "call":
            if len(args) < 3:
                raise ValueError("Usage: smarthome_ctl.py call <domain> <service> [json_payload]")
            domain, service = args[1], args[2]
            payload = json.loads(args[3]) if len(args) > 3 else {}
            res = {"ok": True, "result": ha_request(env, "POST", f"/api/services/{domain}/{service}", payload)}
        elif cmd == "assist":
            if len(args) < 2:
                raise ValueError("Usage: smarthome_ctl.py assist <text>")
            text = " ".join(args[1:])
            res = ha_request(env, "POST", "/api/conversation/process", {"text": text, "language": "cs"})
        else:
            raise ValueError(f"Unknown command: {cmd}")
        output = json.dumps(res, ensure_ascii=False, indent=2)
        print(scrub_secrets(output, env))
        return 0
    except Exception as e:
        err_out = json.dumps({"ok": False, "error": scrub_secrets(str(e), env)}, ensure_ascii=False)
        print(err_out)
        return 1


if __name__ == "__main__":
    sys.exit(main())
