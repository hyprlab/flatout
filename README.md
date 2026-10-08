<p align="center">
  <img src="flatout/static/img/icon.png" width="96" alt="Flatout icon">
</p>

<h1 align="center">Flatout</h1>

<p align="center"><strong>A self-hosted Flatpak repository with a homepage for your app.</strong></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0 license"></a>
  <img src="https://img.shields.io/badge/docker-one%20container%2C%20one%20volume-blue" alt="Docker, one container, one volume">
</p>

Flatout gives a Linux app its own website and its own signed Flatpak
repository, from one Docker container. Upload a `.flatpak` bundle and the
update reaches every install; edit the homepage in the browser and publish it
when it looks right. Scripts and AI agents can do all of it through an API
and an MCP server.

## Features

- **A homepage you edit in the browser.** Every section's text, which
  sections show and in what order, every color in light and dark, the fonts,
  the type size and the images, with a live preview and a draft that goes
  live only when you publish
- **A signed Flatpak repository.** Bundles are imported, signed and published
  with static deltas, by Flatpak's own tools; installs update through GNOME
  Software, KDE Discover or `flatpak update`
- **dnf and apt repositories too.** Upload an `.rpm` or a `.deb` and it is
  signed into this site's dnf or apt repository, so installs update with the
  rest of the system; any other file, an AppImage or a tarball, becomes a
  download
- **Stable and beta channels.** Promote a beta to stable without uploading it
  again, bring back an earlier build, end a channel with a message to its users
- **Install files that write themselves.** The `.flatpakref`, the
  `.flatpakrepo`, the download button and every command on the install guide
  follow the current release, key and address
- **Install numbers without telemetry,** read from ordinary repository traffic
- **An API and an MCP server,** with scoped tokens, for CI and AI agents
- **Encrypted backups** of everything, restored by a fresh install's setup
- Accounts with a first-run setup wizard, Cloudflare Turnstile for sign-in,
  light and dark themes

## Install with Docker Compose

```sh
mkdir flatout && cd flatout
curl -O https://raw.githubusercontent.com/hyprlab/flatout/main/docker-compose.yml
docker compose up -d
```

Then open http://localhost:8102. The first visit opens the setup wizard; the
admin then walks you through the rest. Put a reverse proxy with HTTPS in
front for a public site ([how](docs/DOCUMENTATION.md#behind-a-reverse-proxy)).

## Publishing a release

Build a bundle, one per architecture, and upload it in the admin under
Releases, or from CI with a token:

```sh
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  build-repo org.example.App-x86_64.flatpak org.example.App
curl -fsS -H "Authorization: Bearer $FLATOUT_TOKEN" \
  -F file=@org.example.App-x86_64.flatpak -F channel=stable \
  https://app.example.org/api/v1/releases
```

Agents connect to the MCP server at `/mcp`:

```sh
claude mcp add --transport http flatout https://app.example.org/mcp \
  --header "Authorization: Bearer YOUR_TOKEN"
```

## Documentation

| | |
| --- | --- |
| [Documentation](docs/DOCUMENTATION.md) | Setup, the site, releases, the API and MCP, configuration, backups |
| [Architecture](docs/ARCHITECTURE.md) | How the pieces fit, and why |
| [Design system](docs/DESIGN.md) | The admin's components and the public site's theme tokens |
| [Contributing](docs/CONTRIBUTING.md) | Commits, prose style, tests |
| [Releasing](docs/RELEASING.md) | Versions, the beta and stable channels |
| [Security](docs/SECURITY.md) | What it defends against, and reporting |
| [Changelog](CHANGELOG.md) | What changed in each release |

## AI notice

Flatout is built by a human maintainer who uses generative AI as a development
tool. The maintainer decides what gets built, reviews the results, tests every
release and signs off on everything that ships. Commits are made under the
maintainer's name; the tool is declared here once, for the whole repository,
instead of in a trailer on every commit. The app itself contains no AI and
makes no requests to AI services; its MCP server is there for agents you
choose to connect.

## License

Flatout is free software, licensed under the **GNU Affero General Public License
v3.0 or later** ([AGPL-3.0-or-later](LICENSE)). Cantarell and Inter are under
the SIL Open Font License ([credits](docs/CREDITS.md)).

© 2026 Hyprlab
