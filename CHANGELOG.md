# Changelog

All notable changes to Flatout are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Changed
- The current page in the sidebar and in Settings is marked by a short bar
  beside its highlight instead of along its edge

## [1.3.0] — 2026-10-08

### Fixed
- Only an admin can export or replace the repository's signing key; before,
  any signed-in account could take it or swap it
- A token owned by a plain account can no longer replace the signing key, and
  only an admin can mint a token with the site or releases scopes; a plain
  account sees and revokes only its own tokens
- Importing a release or a file from a URL refuses addresses on the internal
  network, so an account or a token cannot read it through Flatout
- An import now connects to the very address the check approved, so a DNS
  answer switched between the check and the download cannot target the
  internal network

### Security
- Markdown 3.8.1: a malformed document could abort a request with an
  unhandled exception (CVE-2025-69534)
- Flask 3.1.3 and requests 2.34.2, which fix the advisories published
  against the versions before them
- Sign-in answers in the same time whether the account exists or not, so the
  response time no longer says which emails have accounts
- The sign-in throttle now also caps attempts per address across accounts
  (past forty failures, each account gets three), the remembered failures are
  swept instead of growing forever, and the change-password form shares the
  throttle
- Two browsers finishing the setup wizard (or registration) at the same
  moment can no longer both become the first admin
- Pages now carry a Content-Security-Policy whose script nonce changes with
  every response, so injected markup cannot run even if a sanitizer miss ever
  let it in; `Strict-Transport-Security` is sent when `SESSION_COOKIE_SECURE=1`
- The development server listens on localhost again; `DEV_BIND=0.0.0.0`
  opens it to the local network as before
- `CF-Connecting-IP` is only believed when a trusted proxy is configured
  (`TRUST_PROXY`), so a direct visitor can no longer invent the address
  install counts are keyed by
- A media upload larger than the limit is refused before its body is read,
  and bundle uploads arriving at the same time now count what each still has
  to receive against the free disk; a failed upload frees its space at once
- Importing from a URL checks the announced size and the free disk before
  downloading, refuses a download that stops short of what was announced,
  and never leaves a partial file behind
- A failed job's log shows the failing line instead of a traceback with the
  server's paths, and a chatty job keeps its last 400 lines instead of
  growing the database without bound
- A restore now checks the backup as it unpacks: an entry that isn't a
  file, a folder or a hard link, an implausible file count, or a total that
  would overfill the disk all refuse the restore before it replaces anything
- The database file and its journal companions are now readable only by
  the app's own user, including right after a restore

### Changed
- A new Flatout icon in the admin, the sign-in and setup pages, and the
  browser tab
- New passwords hash with scrypt at OWASP's current work factor (2^17), and
  "Keep me signed in" lasts 30 days instead of a year and is off unless
  ticked
- Markdown-filled links whose placeholders fill to a `javascript:` address
  are refused, the sanitizer closes tags the input left open, and the
  sitemap escapes its URLs

### Added
- The site can be turned off, for an install that only wants the
  repository: Site status > Off shows a short page about the repository at
  the site's address, or sends visitors to another address, while installs
  and updates go on as usual
- CI checks dependencies against the published vulnerability databases
  (pip-audit)

## [1.2.0] — 2026-10-08

### Added
- Packages: RPMs, Debian packages and other files, on the stable and beta
  channels. An RPM is signed and published in a dnf repository at `/rpm/`,
  and a Debian package in an apt repository at `/deb/`, so installs that
  add the repository update with the rest of the system. Anything else, an
  AppImage or a tarball, is offered as a download. The newest version of
  each package is live, older ones stay for a downgrade, and a version can
  be withdrawn or promoted from beta to stable
- `/rpm/<remote>.repo` and `/deb/<remote>.sources`, which add the
  repository in one command, the `.sources` file with its key inside
- The install dialog offers the dnf repository, the apt repository and
  other downloads once something is published there, each with a switch
  under Content > Install dialog; the placeholders `{package_name}`,
  `{rpm_repo_file_url}` and `{deb_sources_url}`
- The Installs page counts installs checking the dnf and apt repositories,
  and each package's downloads
- The API's `/packages` endpoints and the MCP tools `list_packages`,
  `get_package`, `upload_package_from_url`, `update_package`,
  `withdraw_package` and `promote_packages`

### Changed
- The Docker image carries `rpm` and `createrepo-c`, for the dnf repository
- Changing the signing key signs every RPM and package index again

## [1.1.0] — 2026-10-07

### Added
- A beta app ID under Repository > App, for a beta published as a separate
  app (`org.example.App.Beta`) that installs beside the stable one. Its builds
  show as the site's beta and in the beta install file, instead of being
  flagged as another app's

## [1.0.0] — 2026-10-07

### Added
- A homepage for your app, edited in the admin: the app's name, links and
  images; a header and footer; a hero, feature cards, screenshots, a
  highlight band, text, an install guide with steps per distro, a beta
  section, the newest release notes, questions, credits and a call to action.
  Sections can be shown, hidden, reordered, duplicated and added
- Design: every color for light and dark, an icon palette, built-in or
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
- A signed Flatpak repository: upload bundles to the stable or beta channel,
  several at once, or give a bundle's address; promote the beta to stable;
  bring back an earlier build; end a channel with a message to its installs.
  Uploads go in 90 MB pieces, so large bundles get through Cloudflare and
  similar proxies
- Apps built for several architectures, one bundle each: every user's
  Flatpak picks their machine's build, and the site and the admin show each
  architecture's version and say when one is behind
- Install files, the repository file, the public key and the newest bundle
  at fixed addresses, written from the current release, key and address
- Install numbers from ordinary repository traffic, without storing any
  address: installs checking for updates per day, an estimate of the
  installs in use, the installs of the latest stable and beta, those on an
  older release, and installs by release, by architecture and for each build
- Encrypted backups of everything under Settings > Backup, restored by a
  fresh install's setup or with `flask restore-backup` on the server
- An API under /api/v1 with scoped, revocable tokens, described in OpenAPI,
  including uploads in pieces for CI behind a proxy
- An MCP server at /mcp, so AI agents can edit the site and publish releases
- A setup wizard that names the app, and a getting-started list in the
  admin whose steps tick themselves, can be ticked or unticked by hand, and
  can be hidden
- Releases and the Overview warn when live releases are for another app ID
  than the site's, which the site and its install files leave out, and say
  how to fix it

