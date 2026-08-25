# Contributing to Repo Radar

Thanks for helping improve Repo Radar. Narrow changes with clear tests are easiest to review.

## Setup

Use Python 3.11 or newer. Follow the [README setup instructions](README.md#quick-start), then start the local interface with:

```bash
python -m repo_radar web
```

The server should remain local at `http://127.0.0.1:8000`.

## Validation

Run these checks before opening a pull request:

```bash
python -m pytest
python -m repo_radar.evaluation
python -m ruff check .
python -m ruff format --check .
node --check repo_radar/static/app.js
```

Changes to ranking behavior should report the before and after evaluation output and, when the change is intended, refresh the baseline with `python -m repo_radar.evaluation --write-baseline`. See [evaluation/README.md](evaluation/README.md).

A ranking change should also be checked against the held-out-star evaluation, which measures the same ranker against real GitHub metadata instead of hand-authored labels:

```bash
python -m repo_radar.heldout_evaluation
python -m repo_radar.heldout_evaluation --write-baseline
```

This takes a few minutes and reads a frozen snapshot; it contacts no network service. Regenerating that snapshot (`python -m repo_radar.heldout_snapshot`) changes the experiment's inputs and is a deliberate act — re-record the held-out baseline in the same change. Read both evaluations together: they answer different questions and neither settles a ranking decision alone.

Optional local coverage is available with:

```bash
python -m pytest --cov=repo_radar --cov-report=term-missing
```

Tests must mock GitHub, GitProfileLens, and other external services. Never add credentials to make tests pass. GitHub star mutations must remain limited to an explicit single-repository action or an explicitly confirmed batch action.

## Pull requests

- Explain what changed and why
- Add focused tests for changed behavior
- Call out recommendation or scoring changes
- Call out any change to GitHub mutation behavior
- Keep refactors and feature changes separate where practical
- Never commit `.env`, tokens, private repository data, local JSON state, or unredacted logs
