# Repo Radar recommendation evaluation

Repo Radar's central claim is that it can surface repositories a particular user is unusually likely to care about. This directory turns that claim into something measurable: a frozen corpus, explicit human relevance judgments, and a small set of interpretable metrics that can be re-run on demand.

The point is not to produce a flattering number. It is to make ranking changes arguable with evidence instead of intuition.

## Two evaluations, two questions

They are deliberately separate and must not be averaged together.

| | **Synthetic graded evaluation** | **Held-out-star evaluation** |
| --- | --- | --- |
| Question | Given a controlled profile, does the ranker order candidates the way a careful human would? | If some real stars had never been seen, would the ranker surface them again from the remaining evidence? |
| Ground truth | Hand-authored graded labels (`0`–`3`) | None. Real historical stars used as an implicit **behavioral proxy** |
| Repositories | Synthetic, constructed to force specific comparisons | Real public GitHub metadata, frozen |
| Primary metrics | NDCG@10, Precision@10 | Hit Rate@5/@10, Recall@10, MRR, median rank |
| Good at | Isolating one ranking behavior at a time | Sanity-checking whether synthetic findings survive real data |
| Bad at | Estimating real-world performance | Attributing a failure to a specific ranking rule |
| Files | `corpus.json`, `scenarios.json`, `baseline.json` | `heldout/snapshot.json`, `heldout/baseline.json` |
| Code | `repo_radar/evaluation.py` | `repo_radar/heldout_evaluation.py` |

The rest of this section covers the synthetic evaluation. The held-out experiment is documented [further down](#held-out-star-evaluation).

## Running the evaluation

```bash
python -m repo_radar.evaluation
```

Machine-readable output:

```bash
python -m repo_radar.evaluation --json
```

Regenerate the checked-in baseline after a deliberate ranking change:

```bash
python -m repo_radar.evaluation --write-baseline
```

The evaluation touches no network and reads no local `data/` state. It uses only the files in this directory.

## Files

| File | Purpose |
| --- | --- |
| `corpus.json` | Frozen repository snapshots and the snapshot date |
| `scenarios.json` | Preference profiles and graded relevance labels |
| `baseline.json` | Recorded results for the current ranking behavior |

## Corpus and freeze methodology

`corpus.json` holds 48 repository snapshots and an explicit `snapshot_date`. Evaluation never contacts GitHub, so results do not drift when upstream repositories gain stars or go quiet.

The snapshot date does double duty: it is also the frozen "now" passed to `rank_candidates`, so activity decay is computed against a fixed reference point rather than the wall clock. Without this, every metric would move slightly each day for reasons that have nothing to do with the ranking code.

Each snapshot stores only fields the ranking system actually consumes — identity, owner, description, language, topics, stars, forks, pushed timestamp, and archived state. The `case` field is documentation for reviewers and is ignored when constructing `Repository` objects.

Repository identities are **synthetic**. This is deliberate. Attaching invented star counts and timestamps to real project names would misrepresent those projects, and copying real metrics would make the fixture wrong the moment it was written. Synthetic entries let the corpus be a controlled instrument: descriptions, topics, and popularity are chosen to produce specific hard comparisons.

The corpus deliberately includes:

- small but highly relevant repositories
- huge but irrelevant repositories
- huge and relevant repositories
- small and moderately relevant repositories
- matching language but wrong subject
- matching topic but weaker overall fit
- interesting but stale repositories
- active but irrelevant repositories
- near-duplicate pairs
- relevant repositories from different project categories

## Relevance labels

Labels are graded and explicit. They are recorded by hand in `scenarios.json` and are never derived from Repo Radar's own score — that separation is the whole point of the exercise.

| Label | Meaning |
| --- | --- |
| `3` | Strongly relevant. This scenario's user would very likely want it. |
| `2` | Probably relevant. A plausible, useful recommendation. |
| `1` | Weak or marginal relevance. Adjacent, but not what the scenario is looking for. |
| `0` | Not relevant. |

These are controlled judgments **for these specific scenarios**. They are not claims about what users in general prefer.

Every candidate that survives filtering must carry a label. A missing label is an error, not an implicit zero — otherwise adding a repository to the corpus would quietly depress precision.

## Scenarios

Profiles are built through the production abstractions (`build_profile`, `SeedPreferences`) and filtered through the production `filter_candidates`, so the evaluation exercises the real pipeline rather than a parallel one. Repositories listed as `starred` become preference signal and are excluded from the candidate pool, exactly as they would be in the application.

| Scenario | Shape |
| --- | --- |
| `narrow_python_tooling` | Concentrated: Python plus automation plus CLI and developer tooling |
| `broad_infra_systems` | Broader: Rust and Go systems work, infrastructure, observability, performance |
| `mixed_web_and_data` | Two distinct interests at once: TypeScript frontend and Python data engineering |

The mixed scenario matters most. A ranker can look excellent on a single narrow interest and still fail a user who cares about two unrelated things.

## Metrics

**NDCG@10** is the primary metric. It uses the graded labels with exponential gain (`2^label - 1`) and logarithmic discount, normalized against the best ordering achievable from the candidate pool. It answers: are the strongly relevant repositories near the top?

Secondary metrics:

- **Precision@10** — share of the returned ten with label `>= 2`.
- **Recall@10** — share of *all* candidates labelled `>= 2` that appear in the top ten. Bounded by pool size; with more relevant candidates than slots, perfect recall is impossible by construction.
- **MRR** — reciprocal rank of the first result labelled `>= 3`. The threshold is strong relevance, not merely relevance, so this measures how quickly the user sees something excellent.

Diagnostics, reported separately and deliberately **not** folded into a score:

- **Mean and maximum pairwise similarity in the top ten**, using `candidate_similarity` — the same function that drives the novelty penalty. High mean similarity means the ten results say roughly the same thing.
- **Popularity**: median stars across candidates versus median stars in the top ten, plus counts of relevant low-star repositories that made it in and irrelevant high-star repositories that made it in.

There is no single combined "quality score". Combining these would hide exactly the trade-offs the evaluation exists to expose.

## Interpreting results

Read them together, not individually:

- High NDCG with high mean pairwise similarity means the ranker found the right *neighborhood* and then filled all ten slots with variations of one idea.
- High precision with low MRR means the results are reasonable but the strongest one is buried.
- A top-ten median star count far above the candidate median, combined with irrelevant high-star entries, is the signature of popularity crowding out personalization.
- The popularity diagnostics are diagnostics. A repository is not worse for being popular; the question is whether popularity is *substituting* for relevance.

Metric changes of one or two hundredths on a 48-repository corpus are noise. Treat a movement as meaningful only when you can point at the ranking entries that moved and explain why.

## Baseline

`baseline.json` records the current ranking behavior: metrics, diagnostics, and the full top-ten ordering with scores and labels for each scenario. The orderings are the useful part — when a metric shifts, the diff shows which repositories changed places.

The baseline was captured after the determinism fixes and the near-duplicate suppression rule in `repo_radar/ranking.py`, and before any weight tuning. It exists to answer "how does the current system behave", not "how high can this number go". Weights were not adjusted to improve it.

### NDCG cannot see redundancy

Worth knowing when reading a duplicate-suppression change: NDCG, precision, and recall all score each result against its own standalone label. None of them can express that the second of two near-identical repositories adds little on top of the first — a duplicate labelled `3` contributes a full gain at its rank whether or not its twin already appeared above it.

So correctly demoting an independently relevant duplicate *lowers* NDCG and precision by construction. That is a limitation of the metric, not evidence the ranking got worse. Read the redundancy diagnostic alongside them: a change that lowers NDCG a little while substantially lowering mean and maximum pairwise similarity has traded per-item relevance for non-redundancy, which is exactly the tradeoff the diagnostics were separated out to expose.

## Limitations

These are real and should be stated whenever the numbers are cited:

- **The corpus is deliberately small.** 48 repositories across three scenarios is enough to catch ordering defects and gross popularity bias. It is not enough to estimate real-world performance.
- **Relevance judgments are scenario-specific and hand-authored.** They encode one author's reading of what each fictional profile wants. A second labeller would disagree in places, and no inter-annotator agreement has been measured.
- **Offline evaluation does not prove real-world satisfaction.** It measures agreement with stated labels, not whether anyone would actually star the results.
- **Real repository metadata changes over time even though this snapshot does not.** A ranking tuned against a frozen corpus can drift away from live GitHub behavior without any metric moving.
- **Synthetic repositories are cleaner than real ones.** Real descriptions are noisier, topics are sparser and less consistent, and real corpora contain far more irrelevant material per relevant item. Precision here is optimistic.
- **Metrics are decision aids, not verdicts.** They are useful for comparing two versions of the ranker against the same fixture. They do not establish that the ranker is good.

---

# Held-out-star evaluation

The synthetic corpus is a controlled instrument, and controlled instruments can be wrong in ways that are invisible from inside the control. This experiment asks the same system a question with no hand-authored labels anywhere in it:

> If Repo Radar had never seen some of the repositories the user actually starred, would it rank those repositories highly using only the remaining preference evidence?

```text
real starred repositories
            ↓
  deterministic trial split
     ↙               ↘
training stars     held-out stars
     ↓                  ↓
production          positives the
build_profile()     profile never sees
     ↓
rank a real candidate pool
     ↓
measure where the held-out stars landed
```

Every scoring decision runs through the production `build_profile`, `filter_candidates`, and `rank_candidates`. There is no evaluation-only recommender.

## What a star actually means

**A star is not ground truth, and this experiment does not treat it as such.**

A star may mean sustained interest. It may equally mean a bookmark, a transitive dependency, a favour to a friend, a conference talk that was interesting once, or a moment of curiosity in 2021. And a repository the user has *not* starred is not evidence of disinterest — it is usually evidence that they never saw it.

What the experiment measures is narrower: **preference recovery**. Can the ranker reconstruct a known historical preference from adjacent evidence? That is a real signal about whether the profile and scoring machinery generalize. It is not a measurement of user satisfaction, and a held-out miss is not proof of a bad recommendation.

## Running it

```bash
python -m repo_radar.heldout_evaluation
```

```bash
python -m repo_radar.heldout_evaluation --json
```

Regenerate the checked-in baseline after a deliberate ranking change:

```bash
python -m repo_radar.heldout_evaluation --write-baseline
```

A full run takes a few minutes: production ranking is greedy and re-scores every remaining candidate on every selection, and the experiment runs four full ranking configurations across every trial. Use `--max-trials` for a fast smoke run.

The evaluation itself **touches no network**. It reads `heldout/snapshot.json` and nothing else — not even `data/`.

## Regenerating the snapshot

The snapshot is the only part that contacts GitHub, and it is a separate command on purpose: *fetch once, evaluate offline forever.*

```bash
python -m repo_radar.heldout_snapshot
```

This requires `GITHUB_TOKEN` and performs read-only requests: one paginated fetch of the authenticated user's stars, plus the searches that `build_search_queries` derives from the resulting profile. It writes real repository metadata, so regenerating it changes the experiment's inputs — treat that as a deliberate act, not routine maintenance, and re-record the baseline in the same change.

### Privacy

Only public repositories are written. `Repository.private` is captured from the GitHub API purely so the generator can *prove* a repository is public before committing its identity; ranking and discovery ignore the field entirely. Private stars and private search results are dropped with a recorded exclusion reason rather than anonymized, and `load_snapshot` refuses to load any snapshot containing a repository flagged private — so a mistake fails the test suite instead of shipping.

Note what committing this snapshot does disclose: it publishes which public repositories this user starred, as of the snapshot date. That was an explicit decision, not a default.

## Eligibility

Not every stored star can serve as a held-out positive. A star is excluded, with a reason recorded in the report, when it is:

| Exclusion | Why |
| --- | --- |
| private | must not enter a public snapshot |
| missing an `owner/name` identity | cannot be matched in a ranking |
| archived | `filter_candidates` drops archived repositories, so recovery is impossible by construction |
| owned by the profile user | same reason: production excludes the user's own repositories |
| no language, topics, *or* description | the ranker has no personalized signal to match on; only popularity and activity remain |
| no `pushed_at` or `updated_at` | activity scoring would fall back to a constant |

Missing metadata is never substituted from a live fetch. The star is excluded and the exclusion is reported.

## Trials

Splits are enumerated, not sampled, whenever the eligible set is small enough — with 10 eligible stars and a holdout of 2 there are exactly `C(10,2) = 45` distinct splits, and the experiment runs all of them. This is strictly stronger than a seeded sample: it removes the "lucky split" question rather than mitigating it, and every star serves as a positive an equal number of times. Larger star sets fall back to a seeded sample from the same deterministic enumeration order, with the seed recorded in the baseline.

Each trial hides `holdout_per_trial` stars and trains on all remaining stars.

### Leakage control

A held-out repository must be genuinely unseen, which is harder than removing it from one list. The allowed profile sources are declared explicitly and each is filtered by identity:

- **Starred (training only)** — the holdout is removed by full name.
- **Owned public repositories** — filtered by `username/name`, so a held-out repository cannot re-enter through the GitProfileLens import.
- **Saved/interested repositories and manual seeds** — not carried in the snapshot at all.

`build_trial_profile` asserts the invariant rather than assuming it: if any source contributes a held-out identity it raises. That check is covered by tests, including a deliberately malformed trial.

Candidate filtering uses only the *training* star names, so a held-out repository stays eligible — otherwise the production filter would remove the very thing being measured.

## Candidate pool

Held-out positives are mixed into a frozen pool of real repositories discovered through the production search path. Nothing in the pool is invented.

Two properties matter for reading the numbers:

- **The pool is large and topically adjacent.** The distractors came from the same searches the product would run, so they are hard negatives, not noise. A pool of 200 candidates with 2 positives is roughly a 1% base rate.
- **The pool was generated from the *full* star history**, including repositories later held out. This makes the experiment a test of **ranking**, not of candidate generation. It cannot tell you whether discovery would have found the repository in the first place.

Held-out positives are injected into every trial's pool, then everything passes through production `filter_candidates`. If a positive is lost to filtering the trial fails loudly rather than silently scoring zero.

## Metrics

Positives here are binary — held-out-starred or not — so the graded NDCG from the synthetic evaluation does not apply and is deliberately not reused.

- **Hit Rate@5 / @10 / @20** — share of *trials* where at least one held-out positive appears that high. This is the closest thing to "would the user have noticed".
- **Recall@5 / @10 / @20** — share of *all* held-out positives recovered that high. Differs from hit rate when a trial recovers one positive but not the other.
- **MRR** — reciprocal rank of the first recovered positive, averaged over trials.
- **Median / mean / p25 / p75 / best / worst held-out rank** — where positives actually land, which is far more informative than a cutoff metric when most positives fall outside the top ten.

There is deliberately **no combined "held-out quality score"**. Combining these would hide the trade-offs the metrics exist to expose.

### The ranking window

Production ranking is greedy and roughly cubic in the number of ranked positions, so every ranker is truncated to the same window (`RANKING_WINDOW`, currently the top 100 of a ~200 candidate pool). A positive outside the window is **unranked**, which is treated explicitly:

- counted as a **miss** for every hit rate and recall;
- **excluded** from median/mean/percentile statistics, which would otherwise be distorted by an invented rank;
- **counted and reported** as `unranked_positives`.

If `unranked_positives` is non-zero for the production ranker, the window is too shallow and the rank statistics below it must not be read as complete. The baseline currently records zero.

## Baselines

These exist to answer "compared to *what*", and are labelled evaluation-only. None of them is a candidate production ranker.

- **Popularity** — orders by stars and forks alone, ignoring the profile entirely. This is the important one: it asks whether personalization is doing anything a "recommend famous repositories" heuristic would not.
- **Activity** — orders by most recent push. Mostly a control for the fact that the candidate pool was fetched sorted by recency.
- **Random** — deterministic seeded shuffle. A sanity floor.

**Read the popularity baseline's MRR next to its `unranked_positives`, never alone.** Popularity ranking is bimodal against a star history that contains a few famous repositories: it places those at rank 1–2 and buries everything else past the window entirely. That produces a flattering MRR — MRR only ever sees the *best* positive in each trial — beside a recall that collapses. A ranker that finds the obvious repository instantly and never finds anything else is not a better ranker, and MRR is the one metric here that cannot tell you so.

## Language ablations

The synthetic evaluation raised a structural concern: `_normalize` divides every language count by the strongest one, so a user with a 2:1 split between two languages gives the second language a permanent `0.5` handicap in the `0.25`-weighted language term.

This experiment tests that concern **without changing production**. Three evaluation-only variants of the same training profile are compared:

| Mode | Transformation |
| --- | --- |
| `current` | production weights, unchanged (control) |
| `uniform` | every language present in the profile weighted `1.0` |
| `compressed` | `sqrt` of the normalized weight — shrinks the gap without erasing it |

`apply_language_mode` returns a new `PreferenceProfile`; the production profile is never mutated, and a test asserts it. Nothing here selects a winner or ships one. A result is evidence for a future scoped experiment, not a change.

## Redundancy diagnostic

The ranker suppresses near-duplicates hard: at similarity `>= 0.9` the novelty weight jumps to `1.0`. A held-out star sitting just behind a near-identical higher-ranked candidate is arguably not a recommendation failure — the user would have seen something equivalent.

This is reported as a **diagnostic** (`positives_behind_a_near_duplicate`), never folded into the metrics. Metrics stay honest; the explanation sits beside them. Duplicate suppression must not be weakened to improve held-out recovery.

## Score term discrimination

A rank tells you a repository placed 24th. It does not tell you *which* of the five scoring terms put it there. `score_components` decomposes each held-out positive's production score into its five weighted contributions — reading production scoring directly, with a test asserting the parts sum to `score_repository`'s output so the diagnostic cannot drift from the thing it describes.

The aggregate view reports, per term, its nominal weight against its **observed range and interquartile spread** across the held-out positives. Both are needed:

- A term with a large nominal weight and a narrow observed spread is not deciding anything, whatever the weight table says.
- A term with a wide range but a near-zero IQR is acting as a **presence flag** — separating the few candidates missing that metadata entirely from everyone else, while grading the rest identically.

This is the diagnostic that distinguishes "the ranker weighed topics heavily and still got it wrong" from "the topic term never varied enough to matter". Those call for completely different responses, and no rank-based metric can tell them apart.

## Segments

Raw metrics say whether recovery worked. Segments start to say *when*:

- held-out language is the dominant training language vs. a secondary/unseen one;
- held-out topics appear in the training profile vs. no topic overlap;
- above vs. at-or-below median popularity among the positives.

Per-positive diagnostics also record the nearest-training similarity, matched topics and keywords, and the training weight of the held-out repository's language — enough to explain individual failures without building a statistics package.

## Limitations

State these whenever the numbers are cited. Several are severe.

- **A star does not mean strong or current interest.** Bookmarks, dependencies, and old curiosity all look identical here.
- **Unstarred does not mean irrelevant.** Every distractor is treated as a negative; some of them are probably repositories the user would like.
- **Interests change.** The profile is built from stars spanning years and evaluated as if they were simultaneous.
- **The dataset is small.** A handful of eligible stars produces trials that share most of their training data, so the trials are heavily correlated and the effective sample size is far below the trial count. Confidence intervals would be embarrassing; none are reported because none would be meaningful.
- **Language coverage may be degenerate.** If every star shares one language, the language ablation cannot test the two-language handicap it was designed for — under-weighting a secondary language costs nothing when there are no positives in one. Check `dominant_language` and `active_languages` in the baseline before reading the ablation as evidence either way.
- **Candidate-set construction drives the metrics.** A larger or more adjacent pool lowers every number without the ranker changing. Comparisons are only valid against the same snapshot.
- **This tests ranking, not discovery.** The pool was built knowing the full star history.
- **Public GitHub metadata is sparse and noisy.** Topics are inconsistently applied and descriptions are frequently marketing copy.
- **Held-out recovery is not user satisfaction.** Recovering historical preferences says nothing about surfacing genuinely new interests, which is arguably the more valuable behavior and is not measured here at all.
- **Stars are themselves popularity-biased.** Users star what they encounter, and they encounter popular repositories. The popularity baseline is not a neutral comparison for this reason.

## Held-out baseline

`heldout/baseline.json` records the snapshot date, trial configuration and seeds, eligibility coverage with exclusion reasons, candidate pool sizing, metrics for the production ranker and every baseline, the language ablations, the segments, and per-trial held-out ranks.

As with the synthetic baseline, the per-trial orderings are the useful part: when a metric moves, the diff shows which repositories moved and where.

**No metric thresholds are enforced in CI.** With trials this correlated, any threshold would be arbitrary. CI verifies that the snapshot parses, the experiment executes, trials are deterministic, leakage guards hold, and the metric implementations are correct — not that any number clears a bar.
