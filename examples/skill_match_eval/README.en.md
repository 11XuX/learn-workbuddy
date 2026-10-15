# Skill Match Regression Gates

[中文](./README.md)

```bash
python3 examples/skill_match_eval/code.py
```

A keyless gate that locks the current s16 `match_skill` contract (lowercase substring match on `read_when` triggers); it is a regression lock, not a quality score. Two gates must pass on the chapter while a deliberately wrong naive matcher fails: `chapter_matches_gold` (a matcher without `.lower()` fails `Please COMMIT this`) and `token_split_fix_breaks_cjk` (whole-word matching fails `帮我做代码审查` and `ready to git push`). Each gate loads a fresh s16 module, so `skill_index` is always the seed index. Two known false matches (`preview the deploy`, `uncommitted changes`) are recorded with their current result and do not affect `passed`. The report is written to `.tmp/skill-match-eval/skill-match-report.json`; any failed gate exits 1.
