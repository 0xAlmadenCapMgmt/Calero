# Calero suite run — 2026-07-16T05-22-51Z

- Python: `/Users/jason/Calero/alice-bob/.venv/bin/python`
- anthropic package: **present**; live model (key): **unavailable**
- Overall: **PASS**

| # | Stage | Status | Duration | Log | Proves |
|---|-------|--------|----------|-----|--------|
| 1 | core | ✅ PASS | 0.19s | [01-core.log](01-core.log) | Generic engine: platform controls, approval-token lifecycle, NullAdapter. |
| 2 | adapters | ✅ PASS | 0.27s | [02-adapters.log](02-adapters.log) | Both adapters + shared derivation; two demos run. |
| 3 | judgment | ✅ PASS | 0.42s | [03-judgment.log](03-judgment.log) | alice-bob: intents judged live by the engine; nothing executes. |
| 4 | enforcement | ✅ PASS | 0.3s | [04-enforcement.log](04-enforcement.log) | treasury-desk: governed agents move real funds; invariants hold. |
| 5 | adversarial | ⏭️ SKIP | 0.0s | [05-adversarial.log](05-adversarial.log) | Subverted LLM cannot exfiltrate; invariants are the oracle. |
