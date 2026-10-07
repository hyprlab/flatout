"""The admin pages.

Pages only: each one renders the shell (templates/admin/shell.html) and its
own content. What they do, they do through the JSON API (api.py) with the
session's CSRF token, the same endpoints an API token or an agent uses, so
anything the admin can do can be automated too.
"""
import json

from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user, login_required

from . import public, repo, site, site_schema
from . import stats as install_stats
from . import releases as releases_info
from .models import Release, User

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
def _signed_in():
    """Every admin page needs an account."""
    return None


@bp.context_processor
def _admin_context():
    ctx = {"site_has_changes": site.has_changes(), "site_status": site.status_json()}
    if current_user.is_authenticated and current_user.is_admin:
        from .main import _turnstile_status
        ctx["admin_users"] = User.query.order_by(User.created_at).all()
        ctx["turnstile"] = _turnstile_status()
    return ctx


def _page(template, section, title, **kw):
    return render_template(template, section=section, page_title=title, **kw)


def _checklist(draft: dict, live: dict, published: bool) -> list[dict]:
    defaults = site_schema.default_document()
    return [
        {"title": "Name the app and give it an icon", "url": url_for("admin.site") + "#group/app",
         "hint": "Content > Name and links, and Images.",
         "done": draft["app"]["name"] != defaults["app"]["name"] and bool(draft["images"]["icon"])},
        {"title": "Choose the colors and fonts", "url": url_for("admin.theme"),
         "hint": "Theme. Every color has a light and a dark version.",
         "done": draft["theme"] != defaults["theme"]},
        {"title": "Write the homepage", "url": url_for("admin.site"),
         "hint": "Content. Turn sections on or off, reorder them, add new ones.",
         "done": draft["sections"] != defaults["sections"]},
        {"title": "Create the repository's signing key", "url": url_for("admin.repository"),
         "hint": "Flatpak checks every update against it.",
         "done": repo.key_fingerprint() is not None},
        {"title": "Upload the first release", "url": url_for("admin.releases"),
         "hint": "A .flatpak bundle, made with flatpak build-bundle.",
         "done": Release.query.filter_by(status="live").first() is not None},
        {"title": "Publish the site", "url": url_for("admin.site"),
         "hint": "Until then visitors see a coming-soon page.",
         "done": published and site.status() == "published"},
    ]


@bp.route("")
def home():
    draft, live = site.get("draft"), site.get("live")
    revisions = site.revisions(5)
    return _page("admin/home.html", "home", "Overview",
                 draft_meta=site.meta("draft"), revisions=revisions,
                 checklist=_checklist(draft, live, bool(revisions)),
                 repo=releases_info.public_info(live, site.base_url()),
                 installs_today=install_stats.installs_today())


def _editor(scope, section, title):
    # Inside a <script> element: "</" would end it early.
    schema = json.dumps(site_schema.schema_json()).replace("</", "<\\/")
    return _page("admin/editor.html", section, title, scope=scope, schema_json=schema)


@bp.route("/site", endpoint="site")
def site_editor():
    return _editor("site", "site", "Content")


@bp.route("/theme")
def theme():
    return _editor("theme", "theme", "Theme")


@bp.route("/pages")
def pages():
    return _editor("pages", "pages", "Pages")


@bp.route("/media", endpoint="media")
def media_library():
    return _page("admin/media.html", "media", "Media")


@bp.route("/preview")
def preview():
    """The draft homepage, for the editor's preview pane."""
    return public.render_home(site.get("draft"), preview=True)


@bp.route("/preview/status/<state>")
def preview_status(state):
    """The maintenance or coming-soon page as visitors see it, with the text
    saved now."""
    if state not in ("maintenance", "unpublished"):
        abort(404)
    resp = public.render_status(state)
    resp.status_code = 200
    return resp


@bp.route("/preview/<slug>")
def preview_page(slug):
    resp = public.render_page(site.get("draft"), slug, preview=True)
    if resp is None:
        abort(404)
    return resp


@bp.route("/app", endpoint="app")
def app_settings():
    return _page("admin/app.html", "app", "App")


@bp.route("/releases")
def releases():
    return _page("admin/releases.html", "releases", "Releases")


@bp.route("/repository")
def repository():
    return _page("admin/repository.html", "repository", "Signing and addresses")


@bp.route("/stats")
def stats():
    return _page("admin/stats.html", "stats", "Installs")


@bp.route("/api")
def api():
    return _page("admin/api.html", "api", "API and agents", base_url=site.base_url())


@bp.route("/api/reference")
def api_reference():
    from . import openapi
    spec = openapi.spec()
    return _page("admin/api_reference.html", "api", "API reference", spec=spec,
                 base_url=site.base_url())


@bp.route("/settings")
def settings_redirect():
    return redirect(url_for("admin.home"))
