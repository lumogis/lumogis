# Injection regression fixtures (LUM-127)

| File | Role |
| --- | --- |
| `benign_product_note.md` | Benign product copy (false positive budget) |
| `ignore_instruction_tail.md` | Classic “ignore prior instructions” pivot (negative harness) |
| `tool_call_spoof.xmlish.txt` | Pseudo tool-call XML scaffolding (negative harness) |
| `bidi_control_u202e.txt` | Hidden BiDi control character sample (negative harness) |
| `markdown_fence_pivot.md` | Suspicious fenced block pivot (negative harness) |

False-positive target: **0** on `benign_product_note.md` for the bundled default rules.
False-negative budget: **≥1** hit each on the malicious samples under `INJECTION_ACTION=wrap`.
