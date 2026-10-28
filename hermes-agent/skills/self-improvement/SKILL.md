---
name: self-improvement
description: Autonomous self-reflection, mistake capture, tool-error recovery, and skill refinement loop for Hermes Agent on RPi5.
version: 1.0.0
metadata:
  hermes:
    tags: [self-improvement, learning, reflection, lessons]
---

# Self-Improvement Skill (`self-improvement`)

## Workflow
1. **Before complex tasks**: Query past lessons via `self_reflect.py recall "<topic>"`.
2. **After any tool error, retry, or user correction**: Record a structured lesson immediately via:
   ```bash
   python3 ~/.hermes/skills/self-improvement/scripts/self_reflect.py record --cue "<when this happens>" --mistake "<what went wrong>" --rule "<mandatory future rule>"
   ```
3. **Autonomous Skill Synthesis**: When a multi-step workflow succeeds, persist or update the corresponding skill in `~/.hermes/skills/` and index the lesson in `rpi5-fast-memory`.
