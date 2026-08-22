# Security Policy

## Supported versions

Security fixes are applied to the latest version on the `main` branch. Earlier releases may not receive separate fixes while Repo Radar is a small project.

## Reporting a vulnerability

Never post a GitHub token, authorization header, `.env` contents, or an unredacted screenshot in a public issue.

If this repository offers GitHub private vulnerability reporting under its Security tab, use that channel. If it does not, contact the maintainer through the GitHub profile before sharing sensitive details. The project does not currently promise a separate private email channel.

Include a concise description, reproduction steps, affected version, and impact. Remove credentials and personal repository data from every attachment.

If a token may have been exposed, revoke it immediately, create a replacement with the minimum required permissions, and restart Repo Radar.

## Local credentials and data

Repo Radar reads `GITHUB_TOKEN` from the local environment or `.env`. It stores repository preferences and history as local JSON under `data/`. These paths are ignored by Git, but users and contributors remain responsible for checking staged files, logs, screenshots, and bug reports before sharing them.
