"""The public site: the homepage, the owner's pages, and uploaded media.

Everything here renders the live site document (site.py). The admin's preview
uses the same templates with the draft (admin.preview).
"""
from flask import Blueprint, Response, abort, current_app, render_template, request, send_from_directory

from . import media, site

bp = Blueprint("public", __name__)


def render_home(doc: dict, preview: bool = False):
    s = site.Renderer(doc, preview=preview)
    return render_template("site/home.html", s=s, hero_page=True)


def render_page(doc: dict, slug: str, preview: bool = False):
    page = next((p for p in doc["pages"] if p["slug"] == slug and (p["published"] or preview)), None)
    if page is None:
        return None
    return render_template("site/page.html", s=site.Renderer(doc, preview=preview), page=page)


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
        return render_template("site/not_found.html", s=site.Renderer(site.get("live")))
    except Exception:
        current_app.logger.exception("the site's 404 page failed to render")
        return None


def is_public_path() -> bool:
    return not request.path.startswith(("/admin", "/api/", "/mcp", "/setup", "/login", "/register", "/logout"))
