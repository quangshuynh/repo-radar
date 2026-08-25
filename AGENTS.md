# AGENTS.md

## Purpose

This repository is **Repo Radar**, a local-first personalized GitHub repository discovery tool.

Optimize for:
- recommendation quality
- deterministic and explainable ranking
- correctness
- privacy
- small, reviewable changes
- evidence-driven decisions

Do not optimize for feature count or stack size.

## Working rules

1. Inspect the current repository before changing code.
2. Treat the code and tests as the source of truth when docs/context disagree.
3. Preserve existing behavior unless the task explicitly changes it or a correctness defect is demonstrated.
4. Prefer the smallest change that proves the intended behavior.
5. Add regression protection for confirmed bugs before or with the fix.
6. Keep ranking behavior deterministic.
7. Keep evaluation logic separate from production preference/ranking logic.
8. Do not tune ranking weights against a small synthetic fixture just to improve metrics.
9. Do not weaken tests, CI, typing, linting, privacy boundaries, or exclusions to make a change pass.
10. Report what changed, what was intentionally not changed, and all checks run.

## Validation

Run the repository's full current validation suite. At minimum, when applicable:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
node --check repo_radar/static/app.js
```

If evaluation infrastructure is present, also run its documented evaluation command and verify deterministic output where relevant.

## Architecture boundaries

Preserve these concepts:
- `profile` builds preference signals from supported sources.
- `discovery` generates/searches candidate pools and applies exclusions.
- `ranking` scores and orders eligible candidates.
- feedback/history/exclusions remain explicit.
- local state remains local-first.
- evaluation fixtures/labels must not leak into production behavior.

## Non-goals unless explicitly requested

Do not introduce:
- LLMs
- embeddings
- vector databases
- ML frameworks
- microservices
- Redis
- Kafka
- cloud infrastructure
- accounts/social features
- dashboards for evaluation
- unrelated frontend redesigns
- broad refactors
- new ranking algorithms merely for variety
- arbitrary test-count inflation

Do not use GitHub communication/community activity as a growth tactic.

## Recommendation philosophy

Repo Radar should answer:

> Can this system find repositories the user is unusually likely to care about?

Prefer measurable improvements to recommendation quality over new surface area.

Keep relevance distinct from popularity. Treat novelty/diversity as a tradeoff, not a goal that should overwhelm relevance.

## Release discipline

Do not create or bump a release/tag unless the task explicitly asks for it.

Keep package/version metadata internally consistent with the latest established release when correcting version drift.
