"""First-run setup wizard.

Shown exactly once: while the instance has zero users, every request is steered
to /setup. The wizard creates the admin account, names the app, and seeds the
placeholder site in one POST, signs the admin in, and hands over to the app. There is
no seeded account and no default password.
"""
import json
import re

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import login_user

from .auth import EMAIL_RE, MIN_PASSWORD
from . import site_schema
from .models import SiteDocument, User, db, set_setting

bp = Blueprint("setup", __name__)

# Once a user exists the answer can never go back to True, so cache it for the
# life of the process and skip the query on every request.
_completed = {"done": False}


def needs_setup() -> bool:
    if _completed["done"]:
        return False
    if db.session.query(User.id).first() is not None:
        _completed["done"] = True
        return False
    return True


@bp.route("/setup")
def wizard():
    if not needs_setup():
        return redirect(url_for("auth.login"))
    return render_template("setup.html")


@bp.route("/setup", methods=["POST"])
def submit():
    if not needs_setup():
        return jsonify(error="This instance is already set up."), 409
    data = request.get_json(silent=True) or {}

    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    if len(username) > 80 or not EMAIL_RE.match(username):
        return jsonify(error="Enter a valid email address."), 400
    if len(password) < MIN_PASSWORD:
        return jsonify(error=f"Passwords need at least {MIN_PASSWORD} characters."), 400

    app_name = (data.get("app_name") or "").strip()[:80]
    tagline = (data.get("app_tagline") or "").strip()[:160]
    public_url = (data.get("public_url") or "").strip().rstrip("/")
    if public_url and not re.match(r"^https?://[^\s/]+(/\S*)?$", public_url):
        return jsonify(error="The public address must start with https:// (or http://)."), 400

    admin = User(username=username, is_admin=True,
                 name=(data.get("name") or "").strip()[:120] or None)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()

    set_setting("registration_open", "1" if data.get("registration_open", False) else "0")
    if public_url:
        set_setting("public_url", public_url)
    _seed_site(app_name, tagline, admin.display_name)

    _completed["done"] = True
    login_user(admin, remember=True)
    return jsonify(ok=True)


def _seed_site(name: str, tagline: str, who: str) -> None:
    """The placeholder site with the app's own name in it, draft and live
    alike, so the homepage says the right name from the first visit."""
    doc = site_schema.default_document()
    if name:
        doc["app"]["name"] = name
    if tagline:
        doc["app"]["tagline"] = tagline
    data = json.dumps(doc)
    for row_name in ("draft", "live"):
        row = db.session.get(SiteDocument, row_name)
        if row is None:
            db.session.add(SiteDocument(name=row_name, data=data, updated_by=who))
        else:
            row.data, row.updated_by = data, who
    db.session.commit()
