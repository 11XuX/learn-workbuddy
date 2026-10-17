# Skill Promotion Evaluation

[中文](./README.md)

```bash
python3 examples/skill_promotion_eval/code.py
```

A keyless counterfactual gate for held-out evaluation in `examples/self_evolving_skills`. Each case seeds a fresh temporary `EvolutionStore`, distills the candidate, and evaluates it with the leaked training trajectory `train-run-1`. The example's real `evaluate()` fails, including `held_out_trace` and `held_out_digest` (gate `chapter_rejects_leaked_validation`); a deliberately lazy evaluator that only checks `validation.successful` passes the same trajectory through the public `EvaluationReport` constructor (gate `naive_evaluator_accepts_leak`, where `passed` means the leak was demonstrated). Promotion only trusts the report, so the evaluator must be able to fail; the example stops at `evaluate()` and never calls `promote()`. The report goes to `.tmp/skill-promotion-eval/skill-promotion-report.json`; a failed gate exits 1.
