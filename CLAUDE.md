# CLAUDE.md

Read `AGENTS.md` and `CONTEXT.md` before implementation.

## How to work in this repo

- Inspect first; do not assume the prompt perfectly matches the current branch.
- Summarize the relevant architecture and exact files you expect to touch before making broad changes.
- Proceed without asking for confirmation when the task is clear.
- Keep edits tightly scoped.
- Prefer explicit behavior and pure functions over abstraction layers.
- Use existing project conventions and dependencies.
- Do not add dependencies for functionality that is simple with the standard library.
- Add a focused failing/regression test for a confirmed ranking bug when practical.
- Run the complete validation suite after changes.
- Do not silently modify ranking weights or evaluation labels.
- Do not optimize metrics against the synthetic evaluation corpus.
- Never expose configured GitHub tokens in output, logs, fixtures, screenshots, or tests.

## Ranking-specific guidance

Current scoring philosophy is intentionally inspectable. Do not replace it with opaque models.

When modifying ranking:
- preserve determinism;
- use deterministic tie-breaks;
- distinguish raw relevance score from novelty/diversity adjustments;
- test ordering invariants rather than freezing arbitrary float internals;
- explain mathematically why a ranking change is justified;
- compare evaluation before/after when evaluation infrastructure exists.

A metric improvement is not sufficient by itself. Explain the behavior that improved and the tradeoff introduced.

## Evaluation-specific guidance

Evaluation is for measurement, not automatic optimization.

- Keep snapshots frozen and reproducible.
- Keep human relevance labels separate from production scores.
- Missing labels should fail loudly rather than silently becoming irrelevant.
- Do not fetch live metadata during frozen offline evaluation.
- Prefer separate interpretable metrics over a synthetic overall quality score.
- CI should not enforce arbitrary metric thresholds unless a later task establishes justified tolerances.

## Final implementation report

Include:
1. inspection findings;
2. files changed;
3. behavior changed;
4. tests/regressions added;
5. before/after evaluation results when relevant;
6. validation commands/results;
7. remaining limitations;
8. recommended next step based on evidence, not feature count.
