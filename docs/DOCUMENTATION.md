# Documentation

Installing, configuring and running Flatout.

## Installing

Flatout runs as one Docker container with all its data in one volume: the
database, uploaded images and fonts, the Flatpak repository, its signing key
and the uploaded bundles.

```sh
mkdir flatout && cd flatout
curl -O https://raw.githubusercontent.com/hyprlab/flatout/main/docker-compose.yml
docker compose up -d
```

Open `http://<host>:8102`. The first visit opens the setup wizard, which
creates your account and names your app. There is no default account or
password. The admin is at `/admin`; the homepage is at `/`.

From a clone of the repository, `docker compose up -d --build` builds the
image from source instead: `docker-compose.override.yml` is picked up
automatically.

## Getting started

The admin's Overview lists the steps. Each ticks itself when Flatout sees it
done (the list says what it looks for), and any step can be ticked or unticked
by hand, which wins. **Hide this list** puts it away for anyone who would
rather set up their own way; a link under the tiles brings it back.

1. **Name the app and give it an icon**, under Content > Name and links, and
   Images.
2. **Choose the colors and fonts**, under Design.
3. **Write the homepage**, under Content: every section's text, which
   sections show, and their order.
4. **Create the repository's signing key**, under Signing and addresses.
5. **Upload the first release**, under Releases.
6. **Publish the site.** Until then visitors see the placeholder site.

## The site

Everything on the homepage is set in the admin; there is no template to edit.

- **Content** holds the parts every page shares (the app's name and links,
  images, the header, the footer, the install dialog, the page title, the
  page for an address with nothing at it) and the homepage's sections. A
  section can be shown or hidden, moved, duplicated or deleted, and new ones
  added: hero, feature cards, screenshots, highlight band, text, install
  guide, beta channel, what's new, questions, people, and a closing call to
  action.
- **Design** sets every color, for light and dark separately, the icon
  palette, the fonts (Cantarell, Inter, system fonts, or an uploaded font
  file), the base text size, the heading weight, the corner rounding and the
  content width. A site can follow the visitor's light or dark setting, or
  stay in one mode. **Custom CSS** is added after the theme, for a touch the
  settings don't reach; it can use and change the page's variables, such as
  `--accent`.
- **Pages** are extra pages at their own address, such as `/privacy`,
  written in Markdown and linked from the header or the footer.
- **Media** holds uploaded images and fonts. A file the site uses can't be
  deleted until the site stops using it.

Every change saves to a draft at once, and the preview beside the form shows
the draft as visitors will see it, at desktop, tablet or phone width and in
either color mode. **Publish** makes it live. **History** lists every
published version; restoring one puts it in the draft for review.

Text can use placeholders, filled in when the page is shown: `{app_name}`,
`{version}`, `{flatpakref_url}` and others, listed in the editor. Commands on
the install guide stay right after each release without being edited.

Sections have finer controls where a layout calls for them:

- **Hero**: the screenshot can lean back and straighten as the page scrolls,
  and the hero's color can fade into the page instead of ending at a straight
  edge.
- **Screenshots**: in one row, each picture's width share sets its column;
  the row can join the section above it.
- **Text**: a width, the buttons under the introduction or after the text,
  and whether it fades in as one, heading by heading, or not at all.
- **Beta channel**: an icon, the version shown or not, the button after the
  warning or after the text, a note under it, and a command in a copy box.
- **Install guide**: a distro's command can come right after its text, its
  steps can have a heading of their own, and a note can close it; closing
  blocks put text and its buttons in turn after the guide.
- **Install dialog**: the wording of each way to install, a folded tip under
  the install file, and an address for the full download elsewhere, such as
  a GitHub release asset. A package built for some machines only is greyed
  out, and says so, on the others.

In Markdown, `{: .center}`, `{: .left}` or `{: .note}` on the line after a
paragraph or list aligns it or sets it in small print, and `{#name}` after a
heading makes it a link target, `#name`.

Some sections follow the repository on their own: the beta section appears
only while a beta release is live, and What's new once a release exists.

### Who sees the site

Publishing puts the draft's changes live; the site status, under Site status
in the sidebar, decides whether visitors get the site at all:

- **Live**: everyone sees the site.
- **Maintenance**: visitors see a "back soon" page in the site's design, with
  an optional time it is expected back. It answers 503 with `Retry-After`, so
  search engines keep the site's pages instead of indexing the notice.
- **Unpublished**: visitors see a "coming soon" page, and search engines are
  asked not to list the site.
- **Off**: no website, for an install that only wants the repository.
  Visitors to the site's address see a short page about the repository, with
  how to install the app once a stable release is live and a "Powered by
  Flatout" link to Flatout's source, or are sent to
  another address you give (the project's GitHub page, say). Search engines
  are asked not to list it. The site's editors fold away in the sidebar, and
  the getting-started list drops its website steps.

A new install is unpublished until its first publish, and then goes live on
its own; once a status has been chosen, publishing doesn't change it. The text
of each page can be edited in the same dialog. Anyone signed in still sees the
site, with a note on what visitors get. The repository, the install files and
the downloads keep working whatever the status, so installed copies go on
updating during maintenance. Scripts and agents switch it with
`PATCH /api/v1/site/status` or the MCP tool `set_site_status`.

## Releases

Flatout keeps a signed Flatpak repository at `/repo/` and the files that
install from it:

| Address | What it is |
| --- | --- |
| `/flatpak/<app-id>.flatpakref` | The install file for the stable channel |
| `/flatpak/<app-id>-beta.flatpakref` | The install file for the beta channel |
| `/flatpak/<remote>.flatpakrepo` | Adds the repository without installing anything |
| `/flatpak/<remote>.gpg` | The public signing key |
| `/download/<app-id>-<arch>.flatpak` | The newest uploaded bundle (`-beta-<arch>` for the beta) |

A release is a bundle made by `flatpak build-bundle`, one per architecture:

```sh
flatpak-builder --repo=build-repo build-dir org.example.App.yml
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  build-repo org.example.App-x86_64.flatpak org.example.App
```

Upload it under Releases, on the stable or the beta channel. Several bundles
can be chosen or dropped at once, say one per architecture: they share the
version and notes typed with them and upload one after another, each its own
release. Flatout reads the app ID, the architecture and the version (from the
app's AppStream metainfo) from the bundle, signs it into the repository with a
fresh timestamp, regenerates the signed summary and the static deltas, and the
update reaches installed copies the next time they check. The job's log is
under the release's Details.

- **Promote to stable** copies the live beta to the stable channel without
  uploading it again.
- **Bring back** makes an earlier build of a channel live again. It goes out
  as a new commit, since Flatpak won't update to an older one.
- **End the beta** tells installed betas, with your message, that no more
  updates are coming.

An app built for more than one architecture, say x86_64 and aarch64, is one
upload per architecture. Each is its own branch in the repository, and each
user's Flatpak picks the one for their machine; a new upload replaces only
its own architecture. The channel's version is the newest any architecture
has. Until every architecture has it, the Overview and Releases say which
one is behind, the site shows its version next to the others ("1.1 (ARM:
1.0)"), and the release history marks a version built for some machines
only. Release notes need to be on only one of the uploads.

Past builds kept for rollback default to ten per channel (Repository >
App); the newest three bundles of each channel and architecture stay
available to download.

### The app

Repository > App holds what the repository needs to know about the app,
and takes effect at once, without publishing the site:

- **Flatpak app ID**: the app the site installs (its install files, its
  download button, its commands). Left empty, the newest upload's app ID.
  Releases for another app ID stay in the repository, but the site leaves
  them out; Releases and the Overview say so.
- **Beta app ID**: for a beta published as a separate app, such as
  `org.example.App.Beta`, which installs beside the stable one. Its beta
  channel builds then appear on the site's beta section and in the beta
  install file. Left empty, the beta is the same app on the beta branch.
- **Remote name**: what `flatpak remote-add` calls the repository on the
  computers that install from it. Left empty, the app's name in lower case.
- **Runtime repository**: where installs get the app's runtime. Flathub
  unless the app's runtime lives elsewhere.
- **Builds kept for rollback**, per channel.

### The signing key

Create the key under Signing and addresses, or import one you already have
(exported without a passphrase: the server signs every update on its own).
**Download a backup of the secret key and keep it off the server.** If the key
is lost, every install has to add the repository again with a new one.

### Addresses

Install files and commands use the site's public address. Set it under
Signing and addresses (or in the setup wizard) once the site has its real
domain; until then Flatout uses the address each request arrives on.

## Packages

Beside the Flatpak repository, Flatout keeps a dnf repository for RPMs, an apt
repository for Debian packages, and a place for any other file people
download, such as an AppImage or a tarball. Each has a stable and a beta
channel, like releases. Installs that add the dnf or apt repository get every
later version through their system's own updates: `dnf upgrade`, `apt
upgrade`, GNOME Software or KDE Discover.

Upload under Packages, or from CI (below). What a file is comes from its
first bytes:

- **An RPM** is read with `rpm`, signed with the repository's key
  (`rpmsign`, replacing any signature it came with) and indexed with
  `createrepo_c`. The index is signed too, so the `.repo` file turns on
  both `gpgcheck` and `repo_gpgcheck`. Source RPMs are refused.
- **A Debian package** is read with `dpkg-deb`. Flatout writes the archive's
  `Packages` and `Release` itself and signs them (`InRelease` and
  `Release.gpg`). Each channel is a suite: `stable` and `beta`, with one
  component, `main`.
- **Anything else** is offered as a download, under its own name or a
  download name you give it. Its version comes from that name (or type one).
  A later upload under the same name replaces it at the same address.

| Address | What it is |
| --- | --- |
| `/rpm/<remote>.repo` | Adds the stable dnf repository (`/rpm/<remote>-beta.repo` for the beta) |
| `/rpm/<channel>/` | The dnf repository itself, the `.repo` file's `baseurl` |
| `/rpm/<remote>.asc` | The signing key, armored, which dnf imports |
| `/deb/<remote>.sources` | Adds the stable apt repository, key included (`-beta.sources` for the beta) |
| `/deb/` | The apt archive: `dists/<channel>/` and `pool/<channel>/` |
| `/deb/<remote>.gpg` | The signing key, for a `Signed-By` of your own |
| `/download/<name>-<arch>.rpm`, `.deb` | The newest package (`<name>-beta-<arch>` for the beta) |
| `/download/<file>`, `/download/beta/<file>` | The newest of another file |

`<remote>` is the remote name under Repository > App. Adding the repository is
one command, then the package installs by name:

```sh
sudo curl -fsSLo /etc/yum.repos.d/myapp.repo https://app.example.org/rpm/myapp.repo
sudo dnf install myapp
```

```sh
sudo curl -fsSLo /etc/apt/sources.list.d/myapp.sources https://app.example.org/deb/myapp.sources
sudo apt update && sudo apt install myapp
```

The `.sources` file carries the key inside it, which apt reads from version
2.4 on (Debian 12, Ubuntu 22.04). The site's install dialog shows these
commands once a package is published, and links each package for those who
want a single file; switch any of them off under Content > Install dialog.
The placeholders `{package_name}`, `{rpm_repo_file_url}` and
`{deb_sources_url}` put them in the site's own text.

**Versions.** The live version of a package is the newest by the rules dnf
and apt use (`1.10` after `1.9`, `1.0~rc1` before `1.0`, epochs first), not
the newest upload, because that is what they install. An older version
uploaded later stays behind it, available to anyone who asks for it by
version. Each channel keeps the three newest versions of every package and
architecture, for a downgrade (Packages > Versions kept); older ones are
deleted. A version already in the channel is refused: build it again with a
new version or release number.

**Withdraw** takes a version out of its repository and deletes it. New
installs then get the version before it, but dnf and apt never move an
installed copy to an older version on their own, so installs that already
have the withdrawn one keep it until a newer one is published (or someone
runs `dnf downgrade`). There is no rollback like a Flatpak's: publish a
fixed version instead.

**Promote to stable** copies the newest beta of every package and file to
stable, the same file, without uploading it again.

Changing the signing key signs every RPM and every index again with the new
one. Installs that added the repository have to fetch the new key, as with
Flatpak.

## Publishing from CI or an agent

Make a token under API and agents with the scopes it needs: **site** to change
and publish the site, **releases** to publish releases. A token is shown once.

From a release workflow:

```sh
curl -fsS -H "Authorization: Bearer $FLATOUT_TOKEN" \
  -F file=@org.example.App-x86_64.flatpak -F channel=beta -F notes="$(cat NOTES.md)" \
  https://app.example.org/api/v1/releases
```

or have Flatout fetch the bundle itself, which suits release assets that are
already published somewhere:

```sh
curl -fsS -H "Authorization: Bearer $FLATOUT_TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/me/app/releases/download/v1.2.0/app-x86_64.flatpak", "channel": "stable"}' \
  https://app.example.org/api/v1/releases
```

Behind a proxy that caps request bodies, send a large bundle in pieces:
`POST /api/v1/uploads` with its `size` answers with an `id` and the
`chunk_size` to use; `PUT /api/v1/uploads/<id>?offset=N` takes each piece as
the raw body (a piece sent twice is taken once; one out of place answers 409
with `received`, where to carry on); then the release is made from it:

```sh
api=https://app.example.org/api/v1; auth="Authorization: Bearer $FLATOUT_TOKEN"
file=org.example.App-x86_64.flatpak; size=$(stat -c %s "$file")
up=$(curl -fsS -H "$auth" -H "Content-Type: application/json" -d "{\"size\": $size}" $api/uploads)
id=$(echo "$up" | jq -r .id); chunk=$(echo "$up" | jq -r .chunk_size)
for ((offset = 0; offset < size; offset += chunk)); do
  tail -c +$((offset + 1)) "$file" | head -c $chunk |
    curl -fsS --retry 5 -H "$auth" -H "Content-Type: application/octet-stream" \
      -X PUT --data-binary @- "$api/uploads/$id?offset=$offset" >/dev/null
done
curl -fsS -H "$auth" -H "Content-Type: application/json" \
  -d "{\"upload\": \"$id\", \"channel\": \"stable\"}" $api/releases
```

Unfinished uploads are removed after a day.

Each way answers at once with the release and its job;
`GET /api/v1/releases/<id>` shows when it is live.

Packages and other files go to `/api/v1/packages` the same three ways, with
`channel` and `notes`, and for a file that isn't a package an optional
`name`, `version` and `arch`. A finished upload in pieces also sends its
`filename`.

```sh
curl -fsS -H "Authorization: Bearer $FLATOUT_TOKEN" \
  -F file=@myapp-1.2.0-1.fc44.x86_64.rpm -F channel=stable \
  https://app.example.org/api/v1/packages
```

`GET /api/v1/packages/<id>` shows when it is live and its job's log.

The whole API is described in OpenAPI at `/api/v1/openapi.json`, and listed
in the admin under API and agents > Reference. Errors are
`{"error": "a sentence"}`; a site change that doesn't validate also lists
each problem with its path, such as `$.sections[2].title`.

### MCP

AI agents that speak the Model Context Protocol can connect to `/mcp` with a
token in the `Authorization` header. The server's tools cover the same ground
as the API: reading and changing the site, sections one at a time, media,
previewing the draft as text, publishing, releases, promotion, rollback,
packages, and install numbers. With Claude Code:

```sh
claude mcp add --transport http flatout https://app.example.org/mcp \
  --header "Authorization: Bearer YOUR_TOKEN"
```

An agent's changes go to the draft like anyone's; it publishes only with a
token that has the site scope and when it calls `publish_site`.

## Install numbers

The Installs page counts, per day, the distinct installs that checked the
repository for updates, and per release, the distinct installs that
downloaded it. Both come from ordinary repository requests; the app itself
reports nothing. No address is stored: each is hashed with a secret and the
day, so the hash changes daily, and the daily hashes are deleted after 60
days. Install files, bundle downloads and page visits aren't counted.

From those two signals the page shows:

- **Estimated installs**: the busiest day of update checks in the last 7
  days; until checks build up, the most-downloaded release of the last 14
- **Latest stable** and **Latest beta**: the installs that downloaded the
  current version since it came out, by architecture
- **Machines by architecture**: each architecture's share of release
  downloads in the last 30 days
- **Active today**, the **seven-day average**, and how many installs are **on
  an older release** than the latest stable or beta
- **Releases in the last 30 days**, stable and beta
- Update checks per day over 30 days, 90 days or a year, as a chart or a
  table, with the busiest day
- Below the chart, one view at a time: installs and downloads **by
  release**, **by architecture** (the last 30 days and all time), the
  **packages**, and **each build**: every signed commit, uploaded, promoted
  or brought back, with when it was first and last downloaded. Long tables
  show their first 10 rows until you ask for all of them

Packages are counted the same way. An install with the dnf or apt
repository added fetches its index (`repomd.xml`, `InRelease`) when it
checks for updates, which dnf does daily with the `.repo` file Flatout
writes and apt does daily on most systems, so the Packages part of the page
shows the installs checking each repository today and on the busiest day of
the week. Each package and file also counts its downloads, by dnf and apt or
from the site. They are under Packages below the chart, which appears
once there is something to show.

`GET /api/v1/stats` returns the same, for scripts and agents.

## Configuration

Everything is optional. Put values in a `.env` file next to
`docker-compose.yml` (`.env.example` lists them all), then
`docker compose up -d` to apply.

| Variable | Default | What it does |
| --- | --- | --- |
| `IMAGE_TAG` | `latest` | The image to run: `latest` for stable, `beta`, or a version to pin |
| `SECRET_KEY` | generated | Signs sessions. If unset, one is generated and kept in the volume |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when the site is served over HTTPS |
| `TRUST_PROXY` | `0` | How many reverse proxies are in front; see below |
| `MAX_UPLOAD_MB` | `2048` | The largest bundle accepted |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | empty | Cloudflare Turnstile keys for the sign-in page; usually set in the admin instead |
| `ALLOW_REGISTRATION` | `0` | Whether anyone can create their own account |
| `WORKER_MINUTES` | `60` | How often housekeeping runs (pruning old stats); `0` turns it off |
| `APP_NAME` | `Flatout` | What the admin calls itself |
| `DATA_DIR` | `/data` | Where everything is kept inside the container |

Every account can edit the site and publish releases; admins also manage
accounts and security, in Settings.

## Turnstile

[Cloudflare Turnstile](https://www.cloudflare.com/products/turnstile/) puts a
challenge on the sign-in page, which stops most automated password guessing.
It is off until an admin turns it on.

1. In the [Cloudflare dashboard](https://dash.cloudflare.com/?to=/:account/turnstile),
   add a widget and list the hostname the site is reached on.
2. In the admin, open Settings > Security, paste the site key and the secret
   key, and press **Verify and turn on**.
3. Complete the challenge that appears. The keys are saved only if Cloudflare
   accepts the answer, so a wrong key can't lock anyone out.

If sign-in becomes impossible anyway, turn it off from the server:

```sh
docker exec flatout flask turnstile off
```

## Channels

`IMAGE_TAG=beta` follows Flatout's own beta channel: previews of the next
minor release. Betas can break things; back up first. `IMAGE_TAG=1.4.0` pins
a version.

## Behind a reverse proxy

Behind Cloudflare Tunnel, Caddy, Traefik or nginx, set `TRUST_PROXY` to the
number of proxies in front, usually `1`. Flatout then takes the client's
address, the scheme and the host from the `X-Forwarded-*` headers, which the
install files, the sign-in throttle and the install numbers need. Setting it
higher than the real number lets a client forge its address. With HTTPS at
the proxy, also set `SESSION_COOKIE_SECURE=1`.

Uploads from the admin, bundles and backups alike, go in pieces of 90 MB, so
they get through proxies that cap request bodies, Cloudflare's 100 MB among
them. Only a bundle sent in one request from a script (`-F file=@…`, below)
needs the proxy to allow bodies as large as `MAX_UPLOAD_MB` (nginx's
`client_max_body_size`).

A CDN in front works: the repository's summary and signatures are sent with
`Cache-Control: no-store`, and content objects, which never change, may be
cached for good.

## Updating

```sh
docker compose pull && docker compose up -d
```

Migrations run by themselves at startup. A MAJOR version (`2.0.0`) means an
existing install needs something done by hand; the changelog says what.

## Backups

Settings > Backup (admins only) makes one file with everything: the
accounts, the site and its media, the repository with every build, the
signing key and the settings. It is encrypted with a passphrase you choose,
at least 12 characters, which the server forgets once the backup is made;
without it nobody can open the file. Making one runs in the background and
can take a while for a large repository; the newest backup stays on the
server until you download it, delete it or make another, and needs about as
much free disk space as the data itself.

To restore, start a fresh install and choose **Restore from a backup** in its
setup. The file uploads in pieces of 90 MB, each retried on its own, so
proxies that cap request bodies (Cloudflare's limit is 100 MB) let it through.
The passphrase is checked once the first piece is in. The fresh install's
data is replaced only after the whole file has arrived and proved intact;
then sign in with an account from the backup. The restoring install needs
free space for about twice the backup.

A large backup restores more easily from the server: copy it into the
volume, then on the fresh install

```sh
docker exec -it flatout flask restore-backup /data/flatout-backup-….tar.gpg
docker compose restart
```

A backup is a tar archive encrypted with standard OpenPGP, so it opens
without Flatout as well: `gpg --decrypt flatout-backup-….tar.gpg | tar -x`.
A backup made by a newer Flatout than the restoring one is refused; update
first.

Copying the volume by hand works too, with the container stopped:

```sh
docker compose stop
docker run --rm -v flatout_flatout-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/flatout-$(date +%F).tar.gz -C /data .
docker compose start
```

## Commands

Run inside the container:

| Command | What it does |
| --- | --- |
| `flask create-user EMAIL [--admin] [--name NAME]` | Create an account; asks for the password |
| `flask reset-password EMAIL` | Set a new password; the way back in for a locked-out admin |
| `flask backup PATH` | Write a consistent copy of the database |
| `flask restore-backup FILE` | Restore a backup from Settings > Backup onto a fresh install; restart afterwards |
| `flask turnstile status`, `flask turnstile off` | Show whether Turnstile is on; turn it off when nobody can sign in |

## Health

`GET /healthz` answers `{"ok": true, "version": "..."}` once Flatout can
reach its database. The image's `HEALTHCHECK` uses it.

## Troubleshooting

**Install files say the wrong address.** Set the public address under
Signing and addresses, or pass the Host header through the proxy and set
`TRUST_PROXY`. Then press **Re-sign the repository**.

**The site says there is no release, but Releases lists one as live.** The
release is for another app ID than the one set under Repository > App.
Releases and the Overview name both; set the app ID to the bundle's, or
upload a bundle for the site's app. A beta built as a separate app needs the
beta app ID instead.

**A release failed.** Its Details show the job's log; the last lines are
`flatpak`'s own message. A file that isn't a bundle, an app ID that doesn't
match, or a runtime bundle instead of an app are the usual causes.

**Clients say the signature is invalid after a key change.** Installs trust
the key they were installed with. After replacing it, every install has to
add the repository again; keep the old key's backup if you may need it.

**"Your session expired. Reload the page and try again."** The secret key
changed or the session cookie was cleared. Reload.

**The container stays unhealthy.** `docker compose logs` shows why. The usual
cause is a volume the container user (uid 1000) can't write to.
