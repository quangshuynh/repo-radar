# Repo Radar

Repo Radar is a private Python CLI that learns from repositories you have starred on GitHub, discovers new candidates through targeted GitHub searches, and ranks them using transparent relevance and novelty signals.

## Current MVP capabilities

- Caches all repositories starred by the authenticated user
- Builds normalized language, topic, and description keyword preferences
- Runs several focused searches instead of one broad query
- Filters archived, owned, already starred, duplicate, and rejected repositories
- Ranks by relevance, activity, modest quality signals, and result novelty
- Saves interested repositories for later and uses them as preference signals
- Stars repositories from the local website through the authenticated GitHub API
- Reviews cached GitHub stars and grouped feedback history in the local website
- Supports undoing local feedback so dismissed repositories can become eligible again
- Supports manual seed interests when no starred repositories are available
- Imports public portfolio signals from the optional GitProfileLens JSON report API
- Provides a local FastAPI web interface while preserving every CLI command

Repo Radar only stars repositories after an explicit single-repository or confirmed bulk action.

## Requirements and setup

Repo Radar requires Python 3.11 or newer and a GitHub personal access token that can read public repository data. A classic personal access token with only the `public_repo` scope is recommended for reliable public repository sync and starring. Private starred repositories require the broader `repo` scope.

```bash
python -m venv .venv
# Activate the environment for your shell
python -m pip install -r requirements.txt
```

Set the token in your environment or in a `.env` file at the project root. Repo Radar loads `.env` automatically without replacing an already exported environment value.

```text
GITHUB_TOKEN=your_token_here
```

Create a classic token under GitHub Settings, Developer settings, Personal access tokens, Tokens (classic). Select only `public_repo` unless private repository access is intentionally required. Never commit the token or paste it into logs, issues, or documentation. Restart Repo Radar after changing `.env` because a running Python process keeps its current environment.

PowerShell example:

```powershell
$env:GITHUB_TOKEN = "your_token_here"
```

## Commands

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

Run `init` to enter comma-separated languages, topics, and optional keywords. Running it again replaces the previous seed preferences.

Run `import-profile` with a username, or omit it to be prompted, to import a structured GitProfileLens JSON report. A starred repository contributes `1.00`, a pinned imported repository contributes `0.80`, another active imported repository contributes `0.35`, and each manual seed contributes `0.60`. Archived and forked imported repositories contribute nothing.

Run `sync` again whenever you want to refresh the local starred repository cache. Sync also removes externally starred repositories from the local Saved list.

Run `web`, then open `http://127.0.0.1:8000`. The web UI supports discovery, saved interests, GitHub starring, a cached starred library, profile review, feedback undo, preferences, GitProfileLens import, and starred repository sync. The CLI remains fully supported.

## Local interface

![Repo Radar local interface](docs/screenshot.png)

The Saved section shows each repository's `0.70` preference weight and signal count. Saved repositories with more language, topic, and description signals appear first. Feedback history groups blocked, dismissed, interested, and starred records. Clearing a blocked or dismissed record makes that repository eligible for future discovery again. Clearing an interested record also removes it from Saved. Clearing a starred feedback record does not unstar it on GitHub or remove it from the synchronized starred cache.

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

Starred repositories, imported public profile data, seed preferences, derived preferences, and feedback are JSON files under `data/`. That directory, `.env`, and common local environment files are ignored by Git. The web server binds only to `127.0.0.1` and never returns the GitHub token. Data is sent only to GitHub and the optional GitProfileLens import URL for requested operations. There is no telemetry, cloud storage, or third-party recommendation service.

## Current limitations

The MVP uses weighted counts and heuristics, not machine learning. Search coverage depends on the strongest profile signals and GitHub search limits. Interested feedback contributes a transparent `0.70` preference weight, and recommendations are generated fresh rather than cached. GitProfileLens import remains optional and preserves the last valid import when its JSON report is unavailable or malformed.
