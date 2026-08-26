# AGENTS.md

## Purpose

This repository is **Repo Radar**, a local-first personalized GitHub discovery tool. It recommends repositories the user is likely to care about, and open issues inside those repositories that are worth investigating as contribution opportunities.

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
- `discovery` generates/searches repository candidate pools and applies exclusions.
- `ranking` scores and orders eligible repository candidates.
- `contribution` selects a bounded issue candidate pool from repositories the user already follows.
- `issue_ranking` scores and orders issue candidates and produces their explanations.
- feedback/history/exclusions remain explicit.
- local state remains local-first.
- evaluation fixtures/labels must not leak into production behavior.

Repository ranking and issue ranking are **separate concerns**. Issue ranking may consume the
repository score as one input signal, but it must never modify repository weights, and a
repository ranking change must not be justified by an issue ranking outcome.

## Contribution discovery invariants

Contribution discovery is a first-class Repo Radar workflow, not an experiment.

1. Issue recommendations stay deterministic and explainable. Tie-breaks are explicit.
2. Recommendation explanations may only claim evidence that actually contributed to the score.
3. Issue results must exclude pull requests and non-open issues at the client boundary.
4. GitHub API usage stays bounded and rate-limit aware: bounded source repositories, grouped
   queries, single page results, no per-repository request fan-out. The Search API allows only
   30 authenticated requests per minute.
5. Issue search failures degrade to partial results plus a warning; they do not crash a run.
6. Candidate repositories come from local evidence. Do not introduce unrestricted GitHub-wide
   issue crawling.
7. Do not silently discard candidates that merely lack beginner labels; prefer transparent
   ranking signals over hidden filters.
8. Never claim difficulty, effort, time estimates, or maintainer responsiveness that the
   system does not actually measure.

## Testing expectations

New ranking or normalization behavior needs explicit fixtures, not incidental coverage:
- GitHub normalization: valid, pull request, closed, partial, and malformed payloads.
- Candidate selection: bounding, exclusion, deduplication, and degradation behavior.
- Ranking: ordering invariants and deterministic tie-breaks rather than frozen float internals.
- Explanations: evidence in the text must correspond to signals that scored.

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
- LLM issue summaries or AI-generated difficulty/time estimates
- issue commenting, assignment, or pull request submission
- unrestricted GitHub-wide issue crawling
- GraphQL, unless inspection proves a material advantage over the current REST client

Do not use GitHub communication/community activity as a growth tactic.

## Recommendation philosophy

Repo Radar should answer two questions:

> Can this system find repositories the user is unusually likely to care about?

> Within those repositories, which open issues are worth the user's time to investigate?

Prefer measurable improvements to recommendation quality over new surface area.

Keep relevance distinct from popularity. Treat novelty/diversity as a tradeoff, not a goal that should overwhelm relevance.

## Release discipline

Do not create or bump a release/tag unless the task explicitly asks for it.

Keep package/version metadata internally consistent with the latest established release when correcting version drift.
