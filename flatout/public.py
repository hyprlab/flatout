"""The public site: the homepage, the owner's pages, and uploaded media.

Everything here renders the live site document (site.py). The admin's preview
uses the same templates with the draft (admin.preview).
"""
from datetime import datetime, timezone

from flask import (Blueprint, Response, abort, current_app, make_response, render_template, request,
                   send_from_directory)
from flask_login import current_user

from . import media, site

bp = Blueprint("public", __name__)


def _renderer(doc: dict, preview: bool = False) -> "site.Renderer":
    s = site.Renderer(doc, preview=preview)
    # Signed-in people see the site whatever its status, with a bar saying
    # what visitors get instead.
    state = site.status()
    s.visitor_status = state if state != "published" and not preview else None
    return s


def render_home(doc: dict, preview: bool = False):
    return render_template("site/home.html", s=_renderer(doc, preview), hero_page=True)


def render_page(doc: dict, slug: str, preview: bool = False):
    page = next((p for p in doc["pages"] if p["slug"] == slug and (p["published"] or preview)), None)
    if page is None:
        return None
    return render_template("site/page.html", s=_renderer(doc, preview), page=page)


def visitor_status() -> str | None:
    """maintenance or unpublished when this request should get the status
    page instead of the site; None when it gets the site."""
    state = site.status()
    if state == "published" or current_user.is_authenticated:
        return None
    return state


def render_status(state: str):
    """The maintenance or coming-soon page, in the site's own theme."""
    s = site.Renderer(site.get("live"))
    page = site.status_pages()[state]
    resp = make_response(render_template("site/status.html", s=s, state=state, page=page))
    if state == "maintenance":
        # 503 with Retry-After: search engines keep the site's pages rather
        # than indexing "back soon" or dropping them.
        resp.status_code = 503
        resp.headers["Retry-After"] = str(_retry_after(page.get("until", "")))
    else:
        resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _retry_after(until: str) -> int:
    try:
        when = datetime.fromisoformat(until.replace("Z", "+00:00"))
        seconds = int((when - datetime.now(timezone.utc)).total_seconds())
        return max(60, min(seconds, 86400))
    except ValueError:
        return 3600


@bp.before_request
def _gate():
    """The homepage and the pages honor the status; media stays served (the
    status page shows the icon), and robots and the sitemap follow it on
    their own."""
    if request.endpoint in ("public.home", "public.page"):
        state = visitor_status()
        if state:
            return render_status(state)
    return None


@bp.route("/")
def home():
    return render_home(site.get("live"))


@bp.route("/media/<path:filename>")
def media_file(filename):
    """Uploaded images and fonts. Their names are hashes of their content, so
    they never change and can be cached for good."""
    if "/" in filename or filename.startswith("."):
        abort(404)
    resp = send_from_directory(media.media_dir(), filename, max_age=31536000)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    resp.headers["Access-Control-Allow-Origin"] = "*"   # fonts load cross-origin from a CDN
    if filename.endswith(".svg"):
        # An SVG opened on its own could run script on this origin; this
        # policy stops that while <img> keeps working.
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return resp


@bp.route("/robots.txt")
def robots():
    if site.status() != "published":
        return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")
    return Response(
        "User-agent: *\nDisallow: /admin\nDisallow: /api/\n"
        f"Sitemap: {site.base_url()}/sitemap.xml\n",
        mimetype="text/plain",
    )


@bp.route("/sitemap.xml")
def sitemap():
    base = site.base_url()
    doc = site.get("live")
    urls = [base + "/"] + [f"{base}/{p['slug']}" for p in doc["pages"] if p["published"]]
    if site.status() != "published":
        urls = []
    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
        mimetype="application/xml",
    )


# Werkzeug ranks fixed routes (/admin, /healthz) above this one, and page
# slugs can't take a name Flatout uses (site_schema.RESERVED_SLUGS).
@bp.route("/<slug>")
def page(slug):
    resp = render_page(site.get("live"), slug)
    if resp is None:
        abort(404)
    return resp


def not_found_page():
    """The site's own 404, in its theme. Falls back to None (the plain error
    page) if the site itself can't render."""
    try:
        state = visitor_status()
        if state:
            return render_status(state)
        return render_template("site/not_found.html", s=_renderer(site.get("live")))
    except Exception:
        current_app.logger.exception("the site's 404 page failed to render")
        return None


# Addresses that aren't the website: the admin and the API, and everything
# Flatpak clients fetch. A missing repository file must stay a plain 404,
# never the site's page or a maintenance 503: clients probe for optional
# files, and a 503 would fail their update.
NOT_THE_SITE = ("/admin", "/api/", "/mcp", "/setup", "/login", "/register", "/logout",
                "/repo/", "/flatpak/", "/rpm/", "/deb/", "/download/", "/media/", "/static/")


def is_public_path() -> bool:
    return not request.path.startswith(NOT_THE_SITE)
