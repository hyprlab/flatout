"""Health, the signed-in account, and instance administration.

Every route here answers JSON to ``fetch`` calls from ``static/js/app.js``,
behind the session CSRF check in the app factory. Failures return
``{"error": "..."}`` with a 4xx status, written for the person reading it; the
client shows it as a toast or an inline form error. The admin pages
themselves are in admin.py, the public site in public.py, the API in api.py.
"""
from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user, login_required, login_user
from sqlalchemy import func

from . import __version__
from .auth import EMAIL_RE, MIN_PASSWORD, clear_failures, record_failure, siteverify, too_many, turnstile_config
from .models import User, db, get_setting, set_setting

bp = Blueprint("main", __name__)


# ———— Health ————

@bp.route("/healthz")
def healthz():
    """Liveness probe for Docker and any proxy in front of it.

    Outside @login_required and outside the setup gate, and it touches the
    database, so a healthy answer means the app can serve, not just that the
    port is open.
    """
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:
        return jsonify(ok=False), 503
    return jsonify(ok=True, version=__version__)


# ———— Account ————

@bp.route("/settings", methods=["POST"])
@login_required
def settings():
    data = request.get_json(silent=True) or {}
    if "name" in data:
        current_user.name = (data.get("name") or "").strip()[:120] or None
    if data.get("theme") in ("system", "light", "dark"):
        current_user.theme = data["theme"]
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/account/password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    # The current-password check gets the sign-in throttle too, or this form
    # would be an unthrottled offline-guessing oracle for a session that was
    # left signed in.
    if too_many(current_user.username):
        return jsonify(error="Too many attempts. Wait a few minutes and try again."), 429
    if not current_user.check_password(data.get("current", "")):
        record_failure(current_user.username)
        return jsonify(error="Current password is wrong."), 403
    clear_failures(current_user.username)
    new = data.get("new", "")
    if len(new) < MIN_PASSWORD:
        return jsonify(error=f"New passwords need at least {MIN_PASSWORD} characters."), 400
    user = current_user._get_current_object()
    user.set_password(new)
    db.session.commit()
    # Every other session of this account ends with the old password; this
    # one carries on under the new one.
    csrf = session.get("_csrf")
    login_user(user, remember=bool(request.cookies.get("remember_token")))
    if csrf:
        session["_csrf"] = csrf
    return jsonify(ok=True)


# ———— Admin ————

def _require_admin() -> None:
    if not current_user.is_admin:
        abort(403)


@bp.route("/settings/registration", methods=["POST"])
@login_required
def admin_registration():
    _require_admin()
    open_ = bool((request.get_json(silent=True) or {}).get("open"))
    set_setting("registration_open", "1" if open_ else "0")
    return jsonify(ok=True, open=open_)


def _turnstile_status() -> dict:
    """What the Security section shows. The secret never leaves the server;
    only its last four characters do, so an admin can tell which one is saved."""
    config = turnstile_config()
    stored_site = get_setting("turnstile_site_key") or ""
    stored_secret = get_setting("turnstile_secret_key") or ""
    return {
        "on": config is not None,
        "source": config["source"] if config else None,
        "site_key": config["site_key"] if config else stored_site,
        "secret_hint": (config["secret_key"] if config else stored_secret)[-4:],
    }


@bp.route("/settings/turnstile", methods=["POST"])
@login_required
def admin_turnstile():
    """Turn Turnstile on, or change its keys.

    Only after the admin has passed a challenge rendered with the new site key
    and Cloudflare has accepted the answer with the new secret. That proves the
    two keys belong together and that this address is allowed for the widget;
    a wrong pair saved blindly would lock everyone, the admin included, out of
    sign-in.
    """
    _require_admin()
    data = request.get_json(silent=True) or {}
    site_key = (data.get("site_key") or "").strip()
    secret_key = (data.get("secret_key") or "").strip()
    if not site_key:
        return jsonify(error="Enter the site key."), 400
    if not secret_key:
        # Blank keeps the secret already in force, so changing only the site
        # key doesn't mean pasting the secret again.
        current = turnstile_config() or {}
        secret_key = current.get("secret_key") or get_setting("turnstile_secret_key") or ""
        if not secret_key:
            return jsonify(error="Enter the secret key."), 400
    if len(site_key) > 200 or len(secret_key) > 200:
        return jsonify(error="That doesn't look like a Turnstile key."), 400
    ok, why = siteverify(secret_key, data.get("token") or "")
    if not ok:
        return jsonify(error=why), 400
    set_setting("turnstile_site_key", site_key)
    set_setting("turnstile_secret_key", secret_key)
    set_setting("turnstile_enabled", "1")
    return jsonify(ok=True, status=_turnstile_status())


@bp.route("/settings/turnstile/disable", methods=["POST"])
@login_required
def admin_turnstile_disable():
    """Turn Turnstile off. The keys stay saved, so turning it back on is one
    challenge away. The stored "off" also overrides TURNSTILE_* variables."""
    _require_admin()
    set_setting("turnstile_enabled", "0")
    return jsonify(ok=True, status=_turnstile_status())


@bp.route("/settings/users", methods=["POST"])
@login_required
def admin_create_user():
    _require_admin()
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    if len(username) > 80 or not EMAIL_RE.match(username):
        return jsonify(error="Enter a valid email address."), 400
    if len(password) < MIN_PASSWORD:
        return jsonify(error=f"Passwords need at least {MIN_PASSWORD} characters."), 400
    if User.query.filter(func.lower(User.username) == username).first():
        return jsonify(error="An account with that email already exists."), 409
    user = User(username=username, is_admin=bool(data.get("is_admin")),
                name=(data.get("name") or "").strip()[:120] or None)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify(ok=True, id=user.id)


@bp.route("/settings/users/<int:user_id>/password", methods=["POST"])
@login_required
def admin_reset_password(user_id):
    _require_admin()
    user = db.get_or_404(User, user_id)
    new = (request.get_json(silent=True) or {}).get("new", "")
    if len(new) < MIN_PASSWORD:
        return jsonify(error=f"Passwords need at least {MIN_PASSWORD} characters."), 400
    user.set_password(new)
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/settings/users/<int:user_id>/toggle-admin", methods=["POST"])
@login_required
def admin_toggle_admin(user_id):
    _require_admin()
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        return jsonify(error="You can't change your own admin status."), 400
    user.is_admin = not user.is_admin
    db.session.commit()
    return jsonify(ok=True, is_admin=user.is_admin)


@bp.route("/settings/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    _require_admin()
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        return jsonify(error="You can't delete your own account from here."), 400
    db.session.delete(user)
    db.session.commit()
    return jsonify(ok=True)
