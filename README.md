# Repo Radar

Repo Radar is a private, local-first GitHub discovery tool. It learns from repositories you star, projects you save, your public GitHub portfolio, manual interests, and ongoing feedback. It then searches GitHub and ranks repositories with transparent relevance, activity, quality, and novelty signals.

[![CI](https://github.com/quangshuynh/repo-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/quangshuynh/repo-radar/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
[![License: MIT](https://img.shields.io/badge/license-MIT-c56a3d.svg)](LICENSE)

![Repo Radar local interface](docs/screenshot.png)

## Features

- Discover repositories from focused GitHub searches
- Build one preference profile from several transparent sources
- Save interesting repositories for later
- Star one saved repository or a confirmed batch on GitHub
- Review the repositories in your synchronized GitHub star library
- Block or dismiss recommendations and undo that feedback later
- Exclude owned, archived, duplicate, saved, starred, blocked, and dismissed repositories
- Import public portfolio signals through GitProfileLens
- Use the local web interface or the complete command-line workflow
- Keep tokens, preferences, and repository history on your machine

Repo Radar never stars a repository without an explicit single-repository action or confirmed bulk action.

## Quick start

Repo Radar requires Python 3.11 or newer.

```bash
git clone https://github.com/quangshuynh/repo-radar.git
cd repo-radar
python -m venv .venv
```

Activate the virtual environment:

```powershell
# PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# Git Bash, macOS, or Linux
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add a GitHub token:

```text
GITHUB_TOKEN=your_token_here
```

Start the local website:

```bash
python -m repo_radar web
```

Open `http://127.0.0.1:8000`.

## GitHub token setup

A classic personal access token with only the `public_repo` scope is recommended for public repository sync and starring.

Create one under GitHub Settings, Developer settings, Personal access tokens, Tokens (classic). Select:

- `public_repo` for public repositories
- `repo` instead only when private starred repository access is intentional

Fine-grained tokens may read starred repositories successfully while GitHub still rejects star mutations with a `403` response. Repo Radar supports either token format, but the classic `public_repo` token is the reliable option for the complete workflow.

Restart Repo Radar after changing `.env`. A running Python process keeps the token it loaded at startup.

Never commit `.env`, paste a token into logs or issues, or expose it through screenshots. Repo Radar never returns the configured token from its API.

## Preference sources

All sources feed the same normalized language, topic, and description-keyword profile.

| Source | Weight |
| --- | ---: |
| Starred repository | `1.00` |
| Pinned GitProfileLens repository | `0.80` |
| Saved interested repository | `0.70` |
| Manual seed preference | `0.60` |
| Other active GitProfileLens repository | `0.35` |
| Archived or forked imported repository | `0.00` |

Saved cards show their preference weight and signal count. Repositories with more usable language, topic, and description signals appear first in the Saved section.

## Web workflow

1. Add manual preferences or import a public GitProfileLens profile
2. Sync GitHub stars
3. Select **Find something good**
4. Save, dismiss, block, or star recommendations
5. Review saved repositories and the synchronized starred library
6. Open Feedback history to undo a previous classification

Sync reconciles the local Saved list with GitHub. If you star a saved repository outside Repo Radar, the next sync removes it from Saved and records it as starred locally.

Clearing blocked or dismissed feedback makes that repository eligible for discovery again. Clearing interested feedback also removes the repository from Saved. Clearing a starred feedback record does not unstar it on GitHub or remove it from the synchronized starred cache.

## Command-line usage

```bash
python -m repo_radar init
python -m repo_radar import-profile your_username
python -m repo_radar sync
python -m repo_radar profile
python -m repo_radar recommend
python -m repo_radar recommend --limit 5
python -m repo_radar feedback owner/repository not-interested
python -m repo_radar web
```

- `init` replaces the current comma-separated language, topic, and keyword seeds
- `import-profile` imports the optional GitProfileLens public repository report
- `sync` refreshes the authenticated user's starred repository cache
- `profile` prints the merged preference profile and active source counts
- `recommend` discovers and ranks a fresh set of eligible repositories
- `feedback` records `interested`, `not-interested`, `starred`, or `blocked`
- `web` starts the local FastAPI interface on `127.0.0.1:8000`

## How recommendations work

Repo Radar builds targeted searches from the strongest profile languages and topics. It deduplicates the results, applies ownership and feedback exclusions, then ranks eligible repositories using:

- Preference relevance
- Repository activity
- Modest quality signals
- Result novelty

The ranking system uses weighted counts and readable heuristics rather than embeddings, language models, or opaque machine-learning models.

## Recommendation evaluation

Ranking quality is measured offline against a frozen repository corpus with explicit graded relevance labels:

```bash
python -m repo_radar.evaluation
```

The evaluation reports NDCG@10, Precision@10, Recall@10, MRR, a redundancy diagnostic, and popularity-bias diagnostics for several preference scenarios. It contacts no network service and reads no local `data/` state.

See [evaluation/README.md](evaluation/README.md) for the corpus methodology, label definitions, and limitations.

## Local data and privacy

Private application state is stored as JSON under `data/`:

- Starred repository cache
- Saved repositories
- GitProfileLens import data
- Manual seed preferences
- Derived profile scores
- Feedback history
- Sync status

The `data/` directory and `.env` are ignored by Git. The web server binds only to `127.0.0.1`. Data is sent only to GitHub and, when requested, GitProfileLens. Repo Radar has no telemetry, analytics, cloud database, background worker, or third-party recommendation service.

## Development

Run the complete validation suite:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
node --check repo_radar/static/app.js
```

Tests mock GitHub and GitProfileLens requests. They do not call either live service.

To inspect local coverage without enforcing an arbitrary threshold:

```bash
python -m pytest --cov=repo_radar --cov-report=term-missing
```

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull request guidance. Report security concerns according to [SECURITY.md](SECURITY.md), and never include a GitHub token in an issue or screenshot.

## Current boundaries

- Search coverage depends on the strongest profile signals and GitHub search limits
- Recommendations are generated on demand rather than cached
- GitProfileLens remains optional and the last valid import survives refresh failures
- GitHub starring depends on token capabilities and GitHub API availability
- Repo Radar does not automatically unstar repositories

## Roadmap

- Improve recommendation explanations with per-signal score details
- Add sorting and filtering to the starred library
- Add pagination for large saved and starred collections
- Add profile-source controls without creating a second profile format
- Package the application for simpler installation

## License

Repo Radar is available under the [MIT License](LICENSE).
