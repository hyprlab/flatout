"""Sign in, sign up, sign out.

Usernames are email addresses: validated and lowercased at registration. The
sign-in field is deliberately ``type=text`` so an account created before that
rule (an app that started with plain usernames) can still sign in.
"""
import re
import threading
import time

import requests
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .models import User, db, get_setting

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Failed sign-ins per (address, account) in a sliding window, plus a per-address
# aggregate so an attacker can't multiply attempts by spraying many accounts.
# Past the aggregate an account still gets a few tries of its own: behind a
# proxy that isn't declared (TRUST_PROXY) every visitor shares one address, and
# made-up emails must not lock real people out.
# In memory is enough: there is one process (see the Dockerfile), and a restart
# forgetting the count costs an attacker a restart they cannot trigger.
MAX_FAILURES = 8
MAX_FAILURES_PER_ADDRESS = 40
MAX_FAILURES_PAST_ADDRESS_LIMIT = 3
FAILURE_WINDOW = 15 * 60
_MAX_TRACKED = 10000    # keys kept before a sweep; a flood of fresh account names can't grow it forever
_failures: dict[tuple[str, str], list[float]] = {}
_per_address: dict[str, list[float]] = {}
_failures_lock = threading.Lock()

# Creating the first (admin) account, from the wizard or from registration, is
# a check-then-insert across the worker's threads; the lock makes it one act.
first_account_lock = threading.Lock()

# Compared against when the account doesn't exist, so a missing account takes
# the same time as a wrong password on a real one and login timing says nothing.
_DUMMY_HASH = generate_password_hash("flatout-timing-pad")


def _fresh(stamps: list[float], now: float) -> list[float]:
    return [t for t in stamps if now - t < FAILURE_WINDOW]


def too_many(username: str) -> bool:
    """At or past the limit, by account or by address?"""
    now = time.monotonic()
    key = (request.remote_addr or "?", username.lower())
    with _failures_lock:
        stamps = _fresh(_failures.get(key, []), now)
        per_addr = _fresh(_per_address.get(key[0], []), now)
        _failures[key], _per_address[key[0]] = stamps, per_addr
        if len(per_addr) >= MAX_FAILURES_PER_ADDRESS:
            return len(stamps) >= MAX_FAILURES_PAST_ADDRESS_LIMIT
        return len(stamps) >= MAX_FAILURES


def record_failure(username: str) -> None:
    now = time.monotonic()
    key = (request.remote_addr or "?", username.lower())
    with _failures_lock:
        if len(_failures) + len(_per_address) > _MAX_TRACKED:
            for table in (_failures, _per_address):
                for stale in [k for k, v in table.items() if not v or now - v[-1] >= FAILURE_WINDOW]:
                    del table[stale]
        _failures.setdefault(key, []).append(now)
        _per_address.setdefault(key[0], []).append(now)


def clear_failures(username: str) -> None:
    with _failures_lock:
        _failures.pop((request.remote_addr or "?", username.lower()), None)


def registration_open() -> bool:
    """The admin-panel toggle wins; ALLOW_REGISTRATION is only the default."""
    stored = get_setting("registration_open")
    if stored is not None:
        return stored == "1"
    return current_app.config["ALLOW_REGISTRATION"]


# ———— Cloudflare Turnstile ————
# Configured in Settings > Security. The TURNSTILE_* environment variables are
# the fresh-install default, as for every admin setting: once an admin turns
# Turnstile on or off in the app, the stored choice wins.

TURNSTILE_ERRORS = {
    "invalid-input-secret": "Cloudflare doesn't recognize that secret key.",
    "missing-input-secret": "Enter the secret key.",
    "invalid-input-response": "The challenge answer was not accepted. Try again.",
    "missing-input-response": "Complete the challenge first.",
    "timeout-or-duplicate": "The challenge expired. Complete it again.",
    "internal-error": "Cloudflare had an internal error. Try again.",
}


def turnstile_config() -> dict | None:
    """The keys in force, or None when Turnstile is off.

    Returns {"site_key", "secret_key", "source"}, source being "settings" or
    "environment".
    """
    enabled = get_setting("turnstile_enabled")
    if enabled == "0":
        return None
    if enabled == "1":
        site, secret = get_setting("turnstile_site_key"), get_setting("turnstile_secret_key")
        if site and secret:
            return {"site_key": site, "secret_key": secret, "source": "settings"}
    site = current_app.config["TURNSTILE_SITE_KEY"]
    secret = current_app.config["TURNSTILE_SECRET_KEY"]
    if site and secret:
        return {"site_key": site, "secret_key": secret, "source": "environment"}
    return None


def siteverify(secret: str, token: str) -> tuple[bool, str | None]:
    """Ask Cloudflare whether a challenge token is good. Returns (ok, why not),
    the reason written for a person."""
    if not token:
        return False, TURNSTILE_ERRORS["missing-input-response"]
    try:
        resp = requests.post(
            TURNSTILE_VERIFY_URL,
            # remote_addr, not a CF-Connecting-IP header a client could set:
            # behind a proxy, TRUST_PROXY makes remote_addr the real address.
            data={"secret": secret, "response": token, "remoteip": request.remote_addr},
            timeout=10,
        )
        data = resp.json()
    except (requests.RequestException, ValueError):
        return False, "Couldn't reach Cloudflare to check the challenge."
    if data.get("success"):
        return True, None
    codes = data.get("error-codes") or []
    for code in codes:
        if code in TURNSTILE_ERRORS:
            return False, TURNSTILE_ERRORS[code]
    return False, "Cloudflare refused the challenge" + (f" ({', '.join(codes)})." if codes else ".")


def verify_turnstile() -> bool:
    """Check the sign-in or sign-up form's challenge, if Turnstile is on."""
    config = turnstile_config()
    if config is None:
        return True
    ok, _ = siteverify(config["secret_key"], request.form.get("cf-turnstile-response", ""))
    return ok


def safe_next(dest: str | None) -> str:
    """Only same-site paths: an absolute or protocol-relative "next" would turn
    the sign-in form into an open redirect."""
    if dest and dest.startswith("/") and not dest.startswith("//") and "\\" not in dest:
        return dest
    return url_for("admin.home")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if too_many(username):
            flash("Too many attempts. Wait a few minutes and try again.", "error")
            return render_template("auth/login.html"), 429
        if not verify_turnstile():
            flash("Verification failed. Please try again.", "error")
            return render_template("auth/login.html"), 400
        password = request.form.get("password", "")
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if user is None:
            # A missing account must cost what a wrong password on a real one
            # does, or the difference in answer time enumerates accounts.
            check_password_hash(_DUMMY_HASH, password)
            ok = False
        else:
            ok = user.check_password(password)
        if ok:
            clear_failures(username)
            login_user(user, remember=request.form.get("remember") == "on")
            return redirect(safe_next(request.args.get("next")))
        record_failure(username)
        flash("Wrong email or password.", "error")
        return render_template("auth/login.html"), 401
    return render_template("auth/login.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("admin.home"))
    if not registration_open():
        flash("Registration is closed on this server.", "error")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if not verify_turnstile():
            flash("Verification failed. Please try again.", "error")
            return render_template("auth/register.html"), 400
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(username) > 80 or not EMAIL_RE.match(username):
            flash("Enter a valid email address.", "error")
        elif len(password) < MIN_PASSWORD:
            flash(f"Passwords need at least {MIN_PASSWORD} characters.", "error")
        elif password != confirm:
            flash("Passwords don't match.", "error")
        elif User.query.filter(func.lower(User.username) == username).first():
            flash("An account with that email already exists.", "error")
        else:
            # The first account on a fresh instance becomes the admin. The
            # lock keeps two concurrent registrations from both seeing zero.
            with first_account_lock:
                user = User(username=username, is_admin=db.session.query(User.id).first() is None,
                            name=request.form.get("name", "").strip()[:120] or None)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
            login_user(user)
            return redirect(url_for("admin.home"))
        return render_template("auth/register.html"), 400
    return render_template("auth/register.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
