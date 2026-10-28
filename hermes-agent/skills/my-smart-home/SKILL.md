---
name: my-smart-home
description: Ovládání chytrého domu MySmartHome (Home Assistant, FVE Deye 15kW, Loxone světla a žaluzie, počasí a předpověď). Optimalizováno pro rychlé 1-krokové volání z modelu Gemma 4 E2B a MiniMax-M3.1-Flash.
version: 2.1.0
metadata:
  hermes:
    tags: [smart-home, home-assistant, deye, loxone, weather, e2b-optimized]
---

# MySmartHome — Rychlé příkazy (E2B & Specialist Optimized)

Pro zjištění stavů, počasí nebo ovládání domu volej **rovnou v 1 kroku** nástroj `terminal` s těmito příkazy:

## 1. Kompletní stručný přehled domu (spotřeba, fáze L1/L2/L3, FVE, baterie, teplota, rozsvícená světla)
```bash
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py status --compact
```

## 2. Aktuální počasí a 5denní předpověď (Open-Meteo + Met.no, teploty, srážky, vítr, predikce FVE)
```bash
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py weather
```

## 3. Výpis stavu všech světel v domě (16 místností)
```bash
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py lights
```

## 4. Rozsvícení / zhasnutí / přepnutí světla podle názvu místnosti
```bash
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py light "zádveří" on
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py light "zádveří" off
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py light "obývák" toggle
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py light "kuchyně" on
```
Podporované místnosti: `zádveří`, `obývák`, `kuchyně`, `chodba`, `garáž`, `pracovna`, `malá koupelna`, `velká koupelna`, `ložnice 1`, `ložnice 2`, `ložnice 3`, `technická`, `wc 1`, `wc 2`, `šatna`, `sauna`.

## 5. Ovládání žaluzií v obývacím pokoji
```bash
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py blinds obývák open
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py blinds obývák close
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py blinds obývák stop
```

## 6. Otevření vstupních dveří (zádveří — elektrický zámek / Loxone impulz)
```bash
python3 ~/.hermes/skills/my-smart-home/scripts/smarthome_ctl.py door open
```
