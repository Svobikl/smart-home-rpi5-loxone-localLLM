---
name: long-horizon
description: Multi-wave task decomposition, on-disk checkpointing, and proactive context-budget protection for MiniMax 3.1 Flash long-horizon tasks.
version: 1.0.0
metadata:
  hermes:
    tags: [long-horizon, planning, checkpointing, compaction]
---

# Long-Horizon Execution Skill (`long-horizon`)

## Purpose
Prevents context bloat and tool-calling degradation on `MiniMax 3.1 Flash` during multi-step tasks by:
1. Breaking any 3+ step goal into an on-disk wave plan (`~/.hermes/plans/<slug>.json`).
2. Checkpointing completed steps to disk after every wave so state survives session compaction.
3. Monitoring estimated prompt size and recommending/triggering session compaction before `threshold_tokens: 24000`.

## Commands
```bash
python3 ~/.hermes/skills/long-horizon/scripts/task_checkpoint.py init --slug "task-name" --goal "Goal description" --steps "Step 1|Step 2|Step 3"
python3 ~/.hermes/skills/long-horizon/scripts/task_checkpoint.py complete --slug "task-name" --step 1 --note "Verified"
python3 ~/.hermes/skills/long-horizon/scripts/task_checkpoint.py status --slug "task-name"
```
