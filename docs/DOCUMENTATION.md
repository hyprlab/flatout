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

The admin's Overview lists the steps and ticks them off:

1. **Name the app and give it an icon**, under Content > App and Images.
2. **Choose the colors and fonts**, under Theme.
3. **Write the homepage**, under Content: every section's text, which
   sections show, and their order.
4. **Create the repository's signing key**, under Signing and addresses.
5. **Upload the first release**, under Releases.
6. **Publish the site.** Until then visitors see the placeholder site.

## The site

Everything on the homepage is set in the admin; there is no template to edit.

- **Content** holds the parts every page shares (the app's name and links,
  images, the header, the footer, the install dialog, the page title) and the
  homepage's sections. A section can be shown or hidden, moved, duplicated or
  deleted, and new ones added: hero, feature cards, screenshots, highlight
  band, text, install guide, beta channel, what's new, questions, people, and
  a closing call to action.
- **Theme** sets every color, for light and dark separately, the icon
  palette, the fonts (Cantarell, Inter, system fonts, or an uploaded font
  file), the base text size, the heading weight, the corner rounding and the
  content width. A site can follow the visitor's light or dark setting, or
  stay in one mode.
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

Some sections follow the repository on their own: the beta section appears
only while a beta release is live, and What's new once a release exists.

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

Upload it under Releases, on the stable or the beta channel. Flatout reads the
app ID, the architecture and the version (from the app's AppStream metainfo)
from the bundle, signs it into the repository with a fresh timestamp,
regenerates the signed summary and the static deltas, and the update reaches
installed copies the next time they check. The job's log is under the
release's Details.

- **Promote to stable** copies the live beta to the stable channel without
  uploading it again.
- **Bring back** makes an earlier build of a channel live again. It goes out
  as a new commit, since Flatpak won't update to an older one.
- **End the beta** tells installed betas, with your message, that no more
  updates are coming.

Past builds kept for rollback default to ten per channel (Signing and
addresses > Advanced); the newest three bundles of each channel and
architecture stay available to download.

### The signing key

Create the key under Signing and addresses, or import one you already have
(exported without a passphrase: the server signs every update on its own).
**Download a backup of the secret key and keep it off the server.** If the key
is lost, every install has to add the repository again with a new one.

### Addresses

Install files and commands use the site's public address. Set it under
Signing and addresses (or in the setup wizard) once the site has its real
domain; until then Flatout uses the address each request arrives on.

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

Both answer at once with the release and its job; `GET /api/v1/releases/<id>`
shows when it is live.

The whole API is described in OpenAPI at `/api/v1/openapi.json`, and listed
in the admin under API and agents > Reference. Errors are
`{"error": "a sentence"}`; a site change that doesn't validate also lists
each problem with its path, such as `$.sections[2].title`.

### MCP

AI agents that speak the Model Context Protocol can connect to `/mcp` with a
token in the `Authorization` header. The server's tools cover the same ground
as the API: reading and changing the site, sections one at a time, media,
previewing the draft as text, publishing, releases, promotion, rollback, and
install numbers. With Claude Code:

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
day, so the hash changes daily.

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
the proxy, also set `SESSION_COOKIE_SECURE=1`, and allow request bodies as
large as `MAX_UPLOAD_MB` (nginx's `client_max_body_size`).

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

Back up the whole volume: it holds the database, the repository, the signing
key, uploads and bundles. With the container stopped:

```sh
docker compose stop
docker run --rm -v flatout_flatout-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/flatout-$(date +%F).tar.gz -C /data .
docker compose start
```

For the database alone while the site runs, `docker exec flatout flask backup
/data/backup.db` writes a consistent copy. Keep a separate copy of the secret
signing key (Signing and addresses > Download a backup).

## Commands

Run inside the container:

| Command | What it does |
| --- | --- |
| `flask create-user EMAIL [--admin] [--name NAME]` | Create an account; asks for the password |
| `flask reset-password EMAIL` | Set a new password; the way back in for a locked-out admin |
| `flask backup PATH` | Write a consistent copy of the database |
| `flask turnstile status`, `flask turnstile off` | Show whether Turnstile is on; turn it off when nobody can sign in |

## Health

`GET /healthz` answers `{"ok": true, "version": "..."}` once Flatout can
reach its database. The image's `HEALTHCHECK` uses it.

## Troubleshooting

**Install files say the wrong address.** Set the public address under
Signing and addresses, or pass the Host header through the proxy and set
`TRUST_PROXY`. Then press **Re-sign the repository**.

**The site says there is no release, but Releases lists one as live.** The
release is for another app ID than the one set under Content > App. Releases
and the Overview name both; set the app ID to the bundle's and publish, or
upload a bundle for the site's app.

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
