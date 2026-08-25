# Repo Radar recommendation evaluation

Repo Radar's central claim is that it can surface repositories a particular user is unusually likely to care about. This directory turns that claim into something measurable: a frozen corpus, explicit human relevance judgments, and a small set of interpretable metrics that can be re-run on demand.

The point is not to produce a flattering number. It is to make ranking changes arguable with evidence instead of intuition.

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

The baseline was captured after the determinism fixes in `repo_radar/ranking.py` and before any weight tuning. It exists to answer "how does the current system behave", not "how high can this number go". Weights were not adjusted to improve it.

## Limitations

These are real and should be stated whenever the numbers are cited:

- **The corpus is deliberately small.** 48 repositories across three scenarios is enough to catch ordering defects and gross popularity bias. It is not enough to estimate real-world performance.
- **Relevance judgments are scenario-specific and hand-authored.** They encode one author's reading of what each fictional profile wants. A second labeller would disagree in places, and no inter-annotator agreement has been measured.
- **Offline evaluation does not prove real-world satisfaction.** It measures agreement with stated labels, not whether anyone would actually star the results.
- **Real repository metadata changes over time even though this snapshot does not.** A ranking tuned against a frozen corpus can drift away from live GitHub behavior without any metric moving.
- **Synthetic repositories are cleaner than real ones.** Real descriptions are noisier, topics are sparser and less consistent, and real corpora contain far more irrelevant material per relevant item. Precision here is optimistic.
- **Metrics are decision aids, not verdicts.** They are useful for comparing two versions of the ranker against the same fixture. They do not establish that the ranker is good.
