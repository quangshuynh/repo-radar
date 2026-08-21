# Repo Radar

Repo Radar is a private Python CLI that learns from repositories you have starred on GitHub, discovers new candidates through targeted GitHub searches, and ranks them using transparent relevance and novelty signals.

## Current MVP capabilities

- Caches all repositories starred by the authenticated user
- Builds normalized language, topic, and description keyword preferences
- Runs several focused searches instead of one broad query
- Filters archived, owned, already starred, duplicate, and rejected repositories
- Ranks by relevance, activity, modest quality signals, and result novelty
- Stores interested, not interested, starred, and blocked feedback locally
- Supports manual seed interests when no starred repositories are available

Repo Radar does not automatically star repositories.

## Requirements and setup

Repo Radar requires Python 3.11 or newer and a GitHub personal access token that can read public repository data. Private starred repositories require suitable token access.

```bash
python -m venv .venv
# Activate the environment for your shell
python -m pip install -r requirements.txt
```

Set the token in your environment or in a `.env` file at the project root. Repo Radar loads `.env` automatically without replacing an already exported environment value.

```text
GITHUB_TOKEN=your_token_here
```

PowerShell example:

```powershell
$env:GITHUB_TOKEN = "your_token_here"
```

## Commands

```bash
python -m repo_radar init
python -m repo_radar sync
python -m repo_radar profile
python -m repo_radar recommend
python -m repo_radar recommend --limit 5
python -m repo_radar feedback owner/repository not-interested
```

Run `init` to enter comma-separated languages, topics, and optional keywords. Running it again replaces the previous seed preferences. Each manual preference contributes two count units when combined with signals inferred from starred repositories.

Run `sync` again whenever you want to refresh the local starred repository cache.

Example recommendation:

```text
1. owner/repository
   Score: 82%
   Python | 842 stars
   developer tool for analyzing API performance

   Why: strong match for Python, developer-tools, API

   https://github.com/owner/repository
```

## Privacy

Starred repositories, seed preferences, derived preferences, and feedback are JSON files under `data/`. That directory, `.env`, and common local environment files are ignored by Git. Data is sent only to GitHub through authenticated REST API requests needed for synchronization and discovery. There is no telemetry, cloud storage, or third-party recommendation service.

## Current limitations

The MVP uses weighted counts and heuristics, not machine learning. Search coverage depends on the strongest profile signals and GitHub search limits. Feedback is entered manually, positive feedback does not yet change profile weights, and recommendations are generated fresh rather than cached.
