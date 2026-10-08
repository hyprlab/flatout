"""The admin pages.

Pages only: each one renders the shell (templates/admin/shell.html) and its
own content. What they do, they do through the JSON API (api.py) with the
session's CSRF token, the same endpoints an API token or an agent uses, so
anything the admin can do can be automated too.
"""
import json

from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user, login_required

from . import checklist, public, site, site_schema
from . import stats as install_stats
from . import releases as releases_info
from .models import User

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


@bp.route("")
def home():
    live = site.get("live")
    revisions = site.revisions(5)
    return _page("admin/home.html", "home", "Overview",
                 draft_meta=site.meta("draft"), revisions=revisions,
                 checklist=checklist.state(),
                 repo=releases_info.public_info(live, site.base_url()),
                 installs_today=install_stats.installs_today())


def _editor(scope, section, title):
    # Inside a <script> element: "</" would end it early.
    schema = json.dumps(site_schema.schema_json()).replace("</", "<\\/")
    return _page("admin/editor.html", section, title, scope=scope, schema_json=schema)


@bp.route("/site", endpoint="site")
def site_editor():
    return _editor("site", "site", "Content")


@bp.route("/design")
def design():
    # Colors, fonts and sizes: the document's "theme" part, which keeps its
    # name in the API. Called Design here, leaving "theme" free for
    # whole ready-made looks later.
    return _editor("theme", "design", "Design")


@bp.route("/theme")
def theme_redirect():
    return redirect(url_for("admin.design"), 301)


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


@bp.route("/packages")
def packages():
    return _page("admin/packages.html", "packages", "Packages")


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
