<p align="center">
  <img src="flatout/static/img/logo.svg" width="72" alt="Flatout logo">
</p>

<h1 align="center">Flatout</h1>

<p align="center"><strong>A self-hosted Flatpak repository with a homepage for your app.</strong></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0 license"></a>
</p>

Flatout is a self-hosted web app that runs in one Docker container with its data
in a single SQLite volume.

## Features

- Accounts with a first-run setup wizard; the first account is the admin
- Light and dark themes that follow the system

## Install with Docker Compose

```sh
curl -O https://raw.githubusercontent.com/hyprlab/flatout/main/docker-compose.yml
docker compose up -d
```

Then open http://localhost:8102. The first visit opens the setup wizard.
Configuration is covered in [docs/DOCUMENTATION.md](docs/DOCUMENTATION.md).

## Documentation

| | |
| --- | --- |
| [Documentation](docs/DOCUMENTATION.md) | Configuration, deployment, backups |
| [Architecture](docs/ARCHITECTURE.md) | How the pieces fit, and why |
| [Contributing](docs/CONTRIBUTING.md) | Commits, prose style, tests |
| [Releasing](docs/RELEASING.md) | Versions, the beta and stable channels |
| [Changelog](CHANGELOG.md) | What changed in each release |

## AI notice

Flatout is built by a human maintainer who uses generative AI as a development
tool. The maintainer decides what gets built, reviews the results, tests every
release and signs off on everything that ships. Commits are made under the
maintainer's name; the tool is declared here once, for the whole repository,
instead of in a trailer on every commit. The app itself contains no AI and
makes no requests to AI services.

## License

Flatout is free software, licensed under the **GNU Affero General Public License
v3.0 or later** ([AGPL-3.0-or-later](LICENSE)).

© 2026 Hyprlab
