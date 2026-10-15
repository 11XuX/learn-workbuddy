# Compaction Survival Evaluation

[中文](./README.md)

```bash
python3 examples/compaction_survival_eval/code.py
```

A keyless counterfactual gate for s14. The same fact and pending item go through the chapter's real `compact_context` twice, with thresholds forced to 1 so L1/L3/L4 all run and a fixed adversarial summarizer: through the durable bypass they both survive; inlined as a user message and compacted with `EMPTY_DURABLE_STATE`, the chapter's own pruning and summary drop both. The gate `durable_bypass_survives_lossy_compaction` passes when the bypass keeps both and the inline naive loses both. The report goes to `.tmp/compaction-survival-eval/compaction-survival-report.json`; a failed gate exits 1.
