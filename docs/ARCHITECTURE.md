# Architecture

One Flask process, SQLite and a Flatpak repository in one volume,
server-rendered HTML, and plain JavaScript with no build step. This page is
how the pieces fit and why.

## Layout

```
flatout/
  __init__.py      the app factory: database, sessions, CSRF, setup gate,
                   headers, error pages, template globals, migrations, threads
  config.py        every environment variable, each with a working default
  models.py        the models, and the runtime settings helpers
  site_schema.py   the public site as data: every field, section type, default
  site.py          the site document: draft, live, publishing, rendering helpers
  public.py        the homepage, pages, uploaded media
  media.py         storing uploaded images and fonts
  icons.py         the icon set the editor offers
  releases.py      what the site reads about releases
  repo.py          the repository on disk and the flatpak/ostree/gpg commands
  jobs.py          the thread that runs repository work, one job at a time
  serve.py         what Flatpak clients fetch: /repo, install files, bundles
  stats.py         install numbers from repository traffic
  api.py           the JSON API under /api/v1, and its tokens
  openapi.py       the API's OpenAPI description, built from the code
  mcp.py           the MCP server at /mcp, a wrapper over the API
  admin.py         the admin pages
  auth.py          sign in, sign up, sign out, Turnstile, the sign-in throttle
  setup.py         the first-run wizard
  main.py          health, the account, and account administration
  cli.py           flask commands: create-user, reset-password, backup
  worker.py        periodic housekeeping
  about_docs.py    parses CHANGELOG.md for the About section
  sanitize.py      allowlist HTML sanitizer, stdlib only
  static/          css, js (app.js, editor.js, admin.js), fonts, the site's assets
  templates/       the admin shell and pages, the public site, auth, setup
tools/             release and repository tooling
tests/             pytest
```

## Decisions

**The site is one JSON document, declared once.** `site_schema.py` declares
every field (its type, label, limit and default) and every section type. The
same declaration fills in a new install, validates every change, builds the
editor's forms (the editor reads it as JSON), and becomes the API's JSON
Schema. A new field is one line there and one use in a template; nothing else
needs to learn about it.

**A draft and a live copy.** Editing never touches what visitors see. The
editor saves the whole draft a moment after each change; publishing validates
it again (an image it uses may have been deleted since), copies it over the
live one, and keeps the result as a revision. Restoring a revision goes
through the draft too, so every change is reviewed the same way.

**Optimistic locking, optional.** A person, a script and an agent may all
edit the draft. A client that sends `X-Draft-Base` with the draft's
`updated_at` gets a 409 instead of overwriting someone else's save; the
editor always sends it. Scripts that don't are last-write-wins.

**One API for three callers.** The admin pages call `/api/v1` with the
session and its CSRF token; scripts call it with a bearer token; the MCP
server calls it in-process with the agent's token. A bearer request is judged
by its token alone, never by a session cookie riding along, so a token's
scopes always hold. Anything the admin can do can be automated, through the
same validation and with the same error messages.

**The theme is CSS variables.** The site's stylesheet only uses tokens; the
page head carries a small `<style>` with the theme's values for light and
dark. Changing a color is a change to the document, not to a file, and the
preview shows it on the next save.

**Repository work runs in its own thread.** Importing a bundle and writing
static deltas take seconds to minutes, and an OSTree repository takes one
writer at a time. A request only queues a `Job` and answers; `jobs.py` runs
them in order and records each one's log. A job interrupted by a restart is
marked failed, not rerun: its release may be half imported.

**Flatpak's own tools do the repository work.** `flatpak build-import-bundle`
unpacks a bundle into a staging repository, `flatpak build-commit-from` signs
it into the served one under its channel's ref, and `flatpak build-update-repo`
signs the summary, writes static deltas and prunes. Promotion and rollback are
`build-commit-from` again from a different source. Every commit is stamped
with the current time, because Flatpak refuses an update older than what is
installed, which a rollback would otherwise be.

**One gunicorn worker, eight threads.** The job thread and the housekeeping
thread must each exist exactly once, and SQLite is happiest with one writing
process. Threads carry the concurrency.

**SQLite in WAL mode.** Readers don't wait for the writer, and
`busy_timeout` makes a writer wait for the lock instead of failing. The
sqlite3 driver's own transaction handling is turned off on connect and
SQLAlchemy emits `BEGIN` itself, so savepoints roll back as they should.

**No migration framework.** Schema changes are steps in `_migrate()`: an
`ALTER TABLE` guarded by a column check, run at every boot, safe to run twice.
New tables come from `create_all`.

**Server-rendered HTML, plain JavaScript.** Jinja renders every page. `app.js`
is the admin shell (dialogs, settings, toasts); `editor.js` builds the site
editor and the media library from the schema; `admin.js` runs the release,
signing, installs and API pages. The public site has its own small `site.js`.
Nothing is compiled.

**Uploads are named by their content.** A media file is stored under a hash
of its bytes and served with a year-long cache: a new file is a new address.
Its type comes from its first bytes, not its name; an SVG with script in it
is refused, and SVGs are served with a sandboxing policy in case one is
opened directly.

**Install numbers without telemetry.** Every installed copy fetches the
repository summary on each update check, and a commit's `.commitmeta` once
per download. Counting distinct requesters per day, by a salted hash that
changes daily, gives the installs in use and each release's uptake.

**JSON errors for the API, pages for people.** Requests under `/api/` and
`/mcp`, or that sent `X-CSRF` or JSON, get `{"error": "..."}`; anything else
gets a page: the site's own 404 in its theme for public addresses, the admin's
error page elsewhere.

**Two kinds of setting.** Environment variables are fresh-install defaults.
Anything changed while Flatout runs is a row in `settings` (the public
address, the runtime repository, the prune depth, the signing key's
fingerprint), and the stored value wins.

**The first account is the admin.** No seeded account, no default password.
While there are no accounts every page goes to `/setup`, and the API and MCP
answer 503.

## A request

1. `ProxyFix` rewrites the client address, scheme and host, if `TRUST_PROXY`
   is set.
2. `steer_to_setup` sends everything to `/setup` while there are no users.
   Static files, `/healthz` and the repository are exempt.
3. `check_csrf` rejects a mutating request authenticated by the session
   without its token. Bearer-token and MCP requests are exempt: no browser
   sends those on its own.
4. The route runs. Admin pages need a session; API routes take a session or a
   token (`api.needs`); the public site and the repository are open.
5. `headers` adds `no-store` to HTML and the security headers. Frames are
   refused, except the editor's own preview of the draft.

## Where state lives

| | |
| --- | --- |
| `DATA_DIR/flatout.db` | Accounts, settings, the site document and its revisions, media and release records, jobs, tokens, install counts |
| `DATA_DIR/media/` | Uploaded images and fonts |
| `DATA_DIR/repo/` | The OSTree repository clients pull from |
| `DATA_DIR/staging/` | Scratch space for unpacking bundles |
| `DATA_DIR/gnupg/` | The signing key |
| `DATA_DIR/bundles/` | Uploaded bundles, for the download button |
| `DATA_DIR/.secret_key`, `.stats_salt` | Generated secrets |

`DATA_DIR` is `/data` in Docker and `./var` locally. Backing up Flatout is
backing up that directory (see [DOCUMENTATION.md](DOCUMENTATION.md#backups)).
