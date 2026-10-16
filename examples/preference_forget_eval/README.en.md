# Preference Forget Evaluation

[中文](./README.md)

```bash
python3 examples/preference_forget_eval/code.py
```

A keyless counterfactual gate for s11. The same fixture preference goes through the chapter's real `delete_preference`, which writes a tombstone, and through a deliberately wrong `naive_delete` that only pops the key and writes `preferences.json` back. Replaying the same evidence raises `StalePreferenceUpdateError` on the chapter path and stays forgotten after a restart; on the naive path the preference revives. Each case runs both paths: `passed` means the chapter blocks revival and the naive pop is caught reviving, i.e. the bug was demonstrated, not that the naive forget is correct. The report goes to `.tmp/preference-forget-eval/preference-forget-report.json`; a failed gate exits 1. The tombstone contract itself stays covered by `tests/test_s11_preference_forget.py`.
