# Contribution ranking evaluation

Repo Radar's second claim is that it can find open issues worth a particular person's time to
investigate — including issues in repositories that person has never seen. This directory
turns that claim into something measurable: a frozen corpus of **real GitHub issues**,
explicit human relevance judgments, and interpretable ranking metrics.

It is the contribution counterpart of the repository evaluations documented in
[`../README.md`](../README.md), and it is deliberately separate from both of them. Repository
ranking and issue ranking are different concerns with different weights, and their numbers
must never be averaged together.

## Status: awaiting human judgments

The corpus is captured and frozen. **No quality baseline exists yet**, because the issues
have not been labelled. `python -m repo_radar.contribution_evaluation` currently reports the
ranking behavior and explicitly reports no metrics, and `--write-baseline` refuses to run.

That refusal is the point. Inventing 0–3 judgments on the user's behalf would produce a
number that measures the labeller's model of the ranker rather than the ranker.

## The question

> Would I actually open this issue and investigate it as a possible contribution?

Not *is this issue important*, not *did a fix get merged*, not *was it easy*. The unit being
evaluated is the **recommendation**, and a recommendation can only be judged on what was
visible at the moment it was made.

## Running it

```bash
python -m repo_radar.contribution_evaluation
```

```bash
python -m repo_radar.contribution_evaluation --json
```

Print the unjudged candidates in a labelling-friendly form:

```bash
python -m repo_radar.contribution_evaluation --labeling-sheet
```

Freeze the baseline (refused until every candidate is judged):

```bash
python -m repo_radar.contribution_evaluation --write-baseline
```

Diff current behavior against the frozen baseline (exit code `1` when anything moved):

```bash
python -m repo_radar.contribution_evaluation --compare
```

The evaluation **touches no network**. It reads `fixtures.json` and `judgments.json` and
nothing else — not `data/`, not GitHub. A test asserts it by making `urlopen` raise.

## Files

| File | Purpose |
| --- | --- |
| `fixtures.json` | Frozen real issues, their repository metadata, the profile that ranked them, and the exact searches that produced each scope |
| `judgments.json` | Graded human relevance judgments, `null` where unjudged |
| `baseline.json` | Frozen metrics and rankings — **created only once judgments exist** |

## Capturing the corpus

The fixture is the only part that contacts GitHub, and it is a separate command on purpose:
*fetch once, evaluate offline forever.*

```bash
python -m repo_radar.contribution_snapshot
```

This runs both production scopes end to end — the same query generation, the same bounded
searches, the same repository hydration, the same normalization — and freezes what came back.
The corpus therefore represents the product as shipped, not a hand-built approximation of it.

Refreshing is a deliberate act, not maintenance. It changes the inputs, so the baseline must
be re-recorded in the same change. **Existing judgments survive a refresh**: candidates still
present keep their labels, candidates that disappeared are dropped, and new candidates arrive
explicitly `null`. A refresh never invents a label.

### Both scopes, on purpose

| Scope | Candidates come from |
| --- | --- |
| `discover` | GitHub-wide profile-derived issue searches plus bounded repository hydration — **repositories the user has never saved or starred** |
| `saved_starred` | Grouped `repo:` searches over the user's saved and starred repositories |

Evaluating only the narrower scope would measure a feature the product no longer defaults to.
Evaluating only discovery would lose the regression protection on the older behavior.

### What is frozen

Only fields the ranker actually consumes.

- **Issues** — repository, number, title, url, body, labels, assignee count, comment count,
  created/updated timestamps, state, and pull-request flag.
- **Repositories** — identity, owner, description, language, topics, stars, forks, archived
  state, and timestamps.
- **Profile** — the derived normalized language/topic/keyword weights and median stars.
- **Queries** — the exact search strings each scope issued, so the corpus can be audited.

The `snapshot_date` does double duty: it is also the frozen "now" passed to `rank_issues`, so
freshness decay and repository activity decay are computed against a fixed reference point
rather than the wall clock. Without this every metric would drift daily for reasons unrelated
to the ranking code.

### Two fidelity notes, stated rather than hidden

1. **Issue bodies are truncated** at `FROZEN_BODY_CHARACTERS` (2000). Issue relevance reads
   only the first 600 characters, contribution friendliness only asks whether the body reaches
   120 characters, and readiness markers live in an issue template's opening section — so the
   scoring impact is small but it is not provably zero for an unusually long issue.
2. **The profile is frozen as derived, not rebuilt from sources.** `build_profile` is exercised
   by the repository evaluations; freezing its output here keeps this corpus focused on issue
   ranking and avoids republishing the user's entire star history a second time.

### Privacy

Only public repositories are written. Private starred repositories are excluded before the
profile is built and before anything is serialized, and a test asserts the checked-in corpus
contains none. Note what committing this file does disclose: which public issues Repo Radar
surfaced for this user on the snapshot date. That was a deliberate decision.

## Relevance judgments

Graded, recorded by hand in `judgments.json`, and never derived from Repo Radar's own score.

| Label | Meaning |
| --- | --- |
| `3` | Strong contribution candidate — clearly worth opening and working on |
| `2` | Likely inspect — plausible and interesting enough to open |
| `1` | Maybe inspect — adjacent, probably not |
| `0` | Would skip |
| `null` | **Not yet judged.** Explicitly not a zero. |

`null` is load-bearing. Treating an unlabelled issue as irrelevant would quietly reward a
ranker for burying everything the labeller never got around to reading. Any unjudged candidate
in a scope suppresses that scope's metrics entirely.

### Judge without hindsight

Labels must reflect only what was knowable when the recommendation was made. Do **not** label
on whether the fix turned out to be easy, whether a maintainer was responsive, whether a pull
request was accepted, or how long the work actually took. Those are task outcomes. This
evaluation measures recommendation usefulness.

### The labelling sheet is deliberately not ranked

`--labeling-sheet` lists candidates by repository and issue number, never in Repo Radar's
ranked order. Showing the ranking would anchor the labels to the exact thing being measured.

## Metrics

**NDCG@5** is primary. A contribution session is a short list, and the question is whether the
first handful are worth opening. It uses exponential gain (`2^label - 1`) with logarithmic
discount, normalized against the best ordering achievable from that scope's candidate pool.

Secondary:

- **NDCG@10** — the broader ordering.
- **Precision@5** — share of the top five with judgment `>= 2` (*actionable*).
- **MRR** — reciprocal rank of the first result with judgment `>= 2`. Note this threshold
  differs from the repository evaluation's MRR, which uses `>= 3`. Here the honest question is
  how quickly the user sees something worth opening, not something perfect.

Diagnostic, reported separately and **not** optimized against:

- **Unique repositories in the top 5 and top 10.** The per-repository cap of three already
  provides diversity behavior; this metric makes that behavior visible rather than assumed.
  A busy project with dozens of open issues can otherwise dominate a result list without
  anything in the metrics noticing.

There is no combined quality score. Metric means across the two scopes are reported as
`mean_*`; that is a mean over evaluation queries, the standard way to report a ranking metric
over more than one query, not a composite.

## Baseline discipline

The baseline records metrics **and the full ranking** for each scope: issue identity, rank,
score, judgment, labels, assignment, comment count, scope signal, and the generated reasons.

The rankings are the useful part. When a metric moves, the diff shows *which* issues moved,
which repositories disappeared, and whether good-first-issue labels started dominating — the
questions a headline number cannot answer.

### Guarding against a vacuous comparison

This project has already been burned once: a baseline check compared stored values that could
not express the behavior being checked, so it could never fail. Three things guard against a
repeat here.

1. `compare_reports` diffs **both** halves that can move — every metric *and* the produced
   ordering — so a reshuffle that leaves every metric unchanged is still reported.
2. `COMPARED_METRICS` is an explicit set, and a test asserts it equals the metric keys a scope
   actually reports. Adding a metric without teaching the comparison about it fails the suite.
3. A test constructs a report whose ranking is reordered with identical metrics and identical
   candidate counts, and asserts the comparison reports it.

**Do not tune the issue ranking weights before this corpus is judged.** The current
`0.30 / 0.35 / 0.15 / 0.10 / 0.10` split has never been measured against real judged issues.
Changing it first would make every later comparison unfalsifiable, which is exactly the trap
the repository-ranking evaluation was built to avoid.

## How to label this corpus

1. `python -m repo_radar.contribution_evaluation --labeling-sheet`
2. For each entry, replace `null` in `judgments.json` with `0`, `1`, `2`, or `3`.
3. Optionally set `judged_by` and `judged_at`.
4. `python -m repo_radar.contribution_evaluation` — metrics appear once nothing is `null`.
5. `python -m repo_radar.contribution_evaluation --write-baseline`

Labelling is not required to be done in one sitting; a partially labelled file is valid and
simply keeps metrics suppressed.

## Limitations

State these whenever the numbers are cited.

- **The corpus reflects one user, one profile, one day.** It cannot estimate general
  recommendation quality.
- **It is a snapshot of a moving target.** Issues get closed, assigned, and re-labelled. The
  frozen metadata will drift away from live GitHub, which is the price of reproducibility.
- **Discovery candidates are shaped by the hydration bound.** Only the strongest
  `MAX_REPOSITORY_HYDRATIONS` repositories enter the discover pool, so the corpus measures the
  ranking of a bounded pool, not the ranking of all of GitHub.
- **The pools are unbalanced.** `saved_starred` carries far more candidates than `discover`,
  and two busy repositories supply most of them. Per-scope metrics are not comparable to each
  other in absolute terms.
- **One labeller, no inter-annotator agreement.** The judgments encode one person's reading.
- **"Would investigate" is not "did contribute".** The evaluation measures the usefulness of a
  recommendation, which is the right unit, but it is not a measure of contribution outcomes.
- **No metric threshold is enforced in CI.** With a corpus this size any threshold would be
  arbitrary. CI verifies the fixture parses, the evaluation runs offline, the metric
  implementations are correct, and the baseline comparison is not vacuous — not that any
  number clears a bar.
