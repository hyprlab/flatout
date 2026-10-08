# Security

## Reporting a problem

Report security problems privately, through GitHub's
[private vulnerability reporting](https://github.com/hyprlab/flatout/security/advisories/new)
(Security, then "Report a vulnerability"), or by email to
hyprlab@proton.me. Please don't open a public issue.

Expect a reply within a week. A fix ships as an urgent patch release
([RELEASING.md](RELEASING.md#urgent-patches)), the reporter is credited in the
changelog unless they ask not to be, and an embargo the reporter proposes is
respected, ending when the fixed release ships. Serious issues get a GitHub
Security Advisory, and a CVE where one is warranted.

Only the latest stable release receives security fixes.

## What the app defends against

| Threat | Defense |
| --- | --- |
| Cross-site request forgery | A per-session token on every POST, PUT, PATCH and DELETE a session authenticates, compared in constant time. API tokens are only ever sent deliberately, so requests that carry one need no CSRF token |
| Cross-site scripting | Jinja autoescaping; user text rendered with `textContent` in JavaScript; the site's Markdown through an allowlist sanitizer; uploaded SVGs with script refused, and every SVG served with a sandboxing policy; and a Content-Security-Policy with a per-request nonce, so a missed injection still cannot run |
| Clickjacking | `X-Frame-Options: DENY`, except the admin's preview of the draft, which only the admin itself may frame (`SAMEORIGIN`) |
| Tampered updates | Every commit and the repository summary are signed with the repository's GPG key; Flatpak refuses anything else, and anything older than what is installed |
| API tokens | Stored as SHA-256 hashes, shown once; each has scopes, optionally an expiry, and can be revoked. A token can't make tokens, and only an admin can mint one that can change the site or the repository. A token acts within its owner's account, so one owned by a plain account can't create or replace the signing key |
| Server-side fetches | A release or package imported from a URL must be http(s) and must resolve to public addresses only, checked again on every redirect hop. The fetch connects to the address the check approved, so a DNS answer switched after the check can't redirect the download to the internal network. A download that declares too much, or stops short of its announced size, is refused, and no partial file is kept |
| Resource exhaustion | A media body bigger than the limit is refused before it is read. Chunked uploads and URL imports check the free disk first, counting uploads that are still arriving, and a restore is checked as it unpacks, so a backup that would fill the disk never does |
| A crafted backup | Entries that aren't files or folders, and a file count beyond what a Flatout backup ever holds, refuse the restore |
| Job logs | A failed job's log shows the failing line, not a traceback; a chatty job's log is trimmed to its last 400 lines |
| The database on disk | The SQLite file and its WAL companions are `0600`, readable only by the app's own user in the container |
| DNS rebinding against `/mcp` | Requests whose `Origin` isn't this site are refused |
| Password guessing | Salted hashes (scrypt at OWASP's work factor); a throttle of eight failures per account per fifteen minutes, three once an address has forty, shared with the change-password form; a wrong answer takes the same time whether the account exists; optional Cloudflare Turnstile, turned on in Settings > Security only after a challenge passes with the new keys |
| Open redirects | The post-sign-in `next` must be a same-site path |
| Session theft | `HttpOnly` and `SameSite=Lax` cookies; `Secure` with `SESSION_COOKIE_SECURE=1`, which also adds `Strict-Transport-Security`; remember-me lasts 30 days; sign-in ids carry a stamp of the password, so changing or resetting it ends every other session and remember-me cookie of the account |
| A default password | There is none: the first account is created in the setup wizard |
| A stolen backup | Encrypted with OpenPGP (AES-256) under a passphrase of at least 12 characters that the server never stores; only an admin, signed in, can make or download one, never an API token |
| Stale pages | HTML is served `no-store` |
| Running as root | The container runs as an unprivileged user |

## Out of scope

- The app sends no email, so there is no self-service password reset; an
  admin resets passwords in Settings, or with `flask reset-password`.
- There is no two-factor authentication yet.
- TLS is the reverse proxy's job.
- The in-memory sign-in throttle resets when the container restarts.
- The Turnstile secret is stored in the database unencrypted, like every other
  setting; whoever can read the volume can read it. It is never sent to a
  browser.
- The repository's signing key has no passphrase, because the server signs
  every update unattended. Whoever can read the volume can sign updates;
  protect the volume and keep the key's backup elsewhere.
- Until the first account exists, whoever reaches a fresh install can set
  it up or restore a backup onto it, as with any first-run wizard. Set it up
  before exposing it.
- A backup holds every account's password hash and the signing key, so its
  passphrase is what protects them once the file leaves the server. Keep
  the passphrase apart from the file.
- Every account can change the site and publish releases. Accounts are for
  the people who run the site; sign-up is closed by default.
