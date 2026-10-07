# Changelog

All notable changes to Flatout are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Changed
- Flatout has its own icon, in the admin, on the sign-in and setup pages, in
  the browser tab and on home-screen shortcuts

## [0.1.0] — 2026-10-07

### Added
- A homepage for your app, edited in the admin: the app's name, links and
  images; a header and footer; a hero, feature cards, screenshots, a
  highlight band, text, an install guide with steps per distro, a beta
  section, the newest release notes, questions, credits and a call to action.
  Sections can be shown, hidden, reordered, duplicated and added
- A theme with every color for light and dark, an icon palette, built-in or
  uploaded fonts, the text size, heading weight, corner rounding and width
- Extra pages at their own address, written in Markdown
- A media library for images and fonts
- A site status: live, maintenance (a "back soon" page, with an optional
  time) or unpublished (a "coming soon" page). A new install is unpublished
  until its first publish. Updates keep reaching installed copies whatever
  the status
- Changes save to a draft with a live preview at desktop, tablet and phone
  widths; publishing makes them live, and every published version can be
  restored
- Repository > App sets the Flatpak app ID the site installs, the remote
  name, the runtime repository and how many builds are kept for rollback;
  they take effect without publishing the site
- A signed Flatpak repository: upload a bundle to the stable or beta channel,
  or give its address; promote the beta to stable; bring back an earlier
  build; end a channel with a message to its installs
- Install files, the repository file, the public key and the newest bundle
  at fixed addresses, written from the current release, key and address
- Install numbers per day and per release, from ordinary repository traffic,
  without storing any address
- An API under /api/v1 with scoped, revocable tokens, described in OpenAPI
- An MCP server at /mcp, so AI agents can edit the site and publish releases
- A setup wizard that names the app, and a getting-started list in the
  admin whose steps tick themselves, can be ticked or unticked by hand, and
  can be hidden
- Releases and the Overview warn when live releases are for another app ID
  than the site's, which the site and its install files leave out, and say
  how to fix it
