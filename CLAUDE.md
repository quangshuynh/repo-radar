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

## Where things live

| Concern | File |
| --- | --- |
| GitHub REST access, including issue search | `repo_radar/github_client.py` |
| Repository and issue domain models | `repo_radar/models.py` |
| Repository candidate discovery and exclusions | `repo_radar/discovery.py` |
| Repository scoring, novelty, duplicate suppression | `repo_radar/ranking.py` |
| Issue candidate selection and API bounding | `repo_radar/contribution.py` |
| Issue scoring **and** explanation generation | `repo_radar/issue_ranking.py` |
| Local JSON persistence | `repo_radar/storage.py` |
| HTTP surface | `repo_radar/web.py` |
| Terminal surface | `repo_radar/cli.py` |
| Frontend | `repo_radar/static/{index.html,app.js,styles.css}` |

Architecture boundaries to respect:

- `web.py` and `cli.py` are thin. Both call `generate_contribution_recommendations` and format
  the result; neither may reimplement scoring, filtering, or explanation text.
- Explanations are produced in `issue_ranking.score_issue` alongside the score that justifies
  them, so the two cannot drift. Do not compose reason strings in the web or CLI layer.
- All GitHub traffic goes through `GitHubClient`. Do not add a second HTTP path.
- `contribution.py` owns every API bound (`MAX_SOURCE_REPOSITORIES`, `REPOSITORY_BATCH_SIZE`,
  `ISSUES_PER_QUERY`, `MAX_ISSUE_CANDIDATES`). Change them there, not at a call site.
- `issue_ranking.py` imports `_parse_date` and `_strongest_matches` from `ranking.py`. That
  cross-module private reuse is the existing convention (`heldout_evaluation.py` does the
  same) and exists so deterministic evidence ordering is defined in exactly one place.

## GitHub API caveats learned here

- `/search/issues` is on the Search API's 30 requests per minute limit, not the 5000 per hour
  core limit. Treat every issue search request as expensive.
- Issue searches pass `advanced_search=true` and use grouped `(repo:a/b OR repo:c/d)` scopes.
  Advanced syntax is required for the parenthesized OR grouping.
- Query strings must stay under 256 characters, and GitHub caps boolean operators per query.
  That is why batches are five repositories, not twenty.
- Issue search items carry `repository_url`, not a nested repository object; the repository
  full name is derived from it, with `html_url` as a fallback.
- Pull requests appear in issue search results and are identified by a `pull_request` key.
  `is:issue` is not sufficient on its own — filter defensively.
- Malformed *shape* raises `GitHubError`; individual unusable rows are dropped so one bad
  row cannot discard a whole batch.

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

Issue ranking additionally requires:
- keep repository weights untouched; issue weights live only in `issue_ranking.py`;
- choose weights against *reachable* ranges, not nominal size — a term whose realistic spread
  is near zero contributes nothing regardless of its declared weight (see `CONTEXT.md` on how
  relative normalization flattened the topic signal);
- keep contribution friendliness a tie-breaker, never a shortcut past relevance;
- apply diversity rules (the per-repository cap) after scoring so reported scores stay raw.

## Validation

Run all five before finishing:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
node --check repo_radar/static/app.js
python -m repo_radar.evaluation
```

When a change is not meant to affect repository ranking, prove it: capture
`python -m repo_radar.evaluation` output, `git stash` the change, capture it again, and diff.
Note that `evaluation/baseline.json` stores per-scenario detail, not the headline metrics, so
comparing top-level numbers against it proves nothing — diff the actual report output.

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
