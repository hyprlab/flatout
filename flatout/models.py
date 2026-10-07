"""Database models.

Schema changes go through ``_migrate()`` in ``__init__.py``: ``ALTER TABLE``
guarded by a column check, no migration framework. SQLite in one volume is the
whole storage story (see docs/ARCHITECTURE.md).
"""
import hashlib
from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow() -> datetime:
    """Naive UTC. SQLite has no timezone type, so everything stored is UTC and
    every comparison goes through this."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)  # an email address
    name = db.Column(db.String(120))
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    # Preferences live on the account, not in localStorage, so they follow the
    # user to another browser.
    theme = db.Column(db.String(10), default="system", nullable=False)     # system|light|dark
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def display_name(self) -> str:
        return self.name or self.username

    def get_id(self) -> str:
        """What the session and the remember-me cookie hold: the id and a
        stamp of the password. A new password changes the stamp, so every
        other session and remember-me cookie of the account ends."""
        return f"{self.id}.{self.password_stamp}"

    @property
    def password_stamp(self) -> str:
        return hashlib.sha256(self.password_hash.encode()).hexdigest()[:12]

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Setting(db.Model):
    """Instance-wide key/value settings an admin edits at runtime.

    A stored value always wins over the matching environment variable, which
    is only the fresh-install default.
    """
    __tablename__ = "settings"

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(500), nullable=False)


def get_setting(key: str) -> str | None:
    row = db.session.get(Setting, key)
    return row.value if row else None


def int_setting(key: str, fallback: int) -> int:
    raw = get_setting(key)
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return fallback


def set_setting(key: str, value: str) -> None:
    row = db.session.get(Setting, key)
    if row:
        row.value = value
    else:
        db.session.add(Setting(key=key, value=value))
    db.session.commit()


class SiteDocument(db.Model):
    """The public site as one JSON document (site_schema.py).

    Two rows: ``draft``, which the editor and the API change, and ``live``,
    which visitors see. Publishing copies the draft over the live one and keeps
    the old live document as a revision.
    """
    __tablename__ = "site_documents"

    name = db.Column(db.String(10), primary_key=True)   # draft | live
    data = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    updated_by = db.Column(db.String(120))


class SiteRevision(db.Model):
    """A published version of the site, newest last. Restoring one copies it
    into the draft, so it is reviewed and published like any other change."""
    __tablename__ = "site_revisions"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Text, nullable=False)
    published_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    published_by = db.Column(db.String(120))
    note = db.Column(db.String(200), default="", nullable=False)


class Media(db.Model):
    """An uploaded file: an image or a font the site uses.

    Stored once under DATA_DIR/media, named by a hash of its content, and
    served at /media/<filename> with a long cache lifetime: a new file is a new
    address, so a cached copy is never stale.
    """
    __tablename__ = "media"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(80), unique=True, nullable=False)   # <sha256[:20]>.<ext>
    original_name = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(10), nullable=False)                     # image | font
    mime = db.Column(db.String(80), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    alt = db.Column(db.String(300), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def url(self) -> str:
        return f"/media/{self.filename}"


class Release(db.Model):
    """One uploaded bundle, for one architecture and one channel.

    A release is ``queued`` when its bundle is stored, ``processing`` while the
    job imports and signs it, then ``live`` as the newest build of its
    channel and architecture. The build it replaced becomes ``superseded``; it
    stays in the repository's history, so a rollback can bring it back.
    """
    __tablename__ = "releases"
    __table_args__ = (db.Index("ix_releases_head", "channel", "arch", "status"),)

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.String(255), nullable=False, index=True)
    arch = db.Column(db.String(20), nullable=False)          # x86_64 | aarch64 | ...
    channel = db.Column(db.String(20), nullable=False)       # stable | beta
    version = db.Column(db.String(60), default="", nullable=False)
    notes = db.Column(db.Text, default="", nullable=False)   # Markdown
    status = db.Column(db.String(12), default="queued", nullable=False, index=True)
    commit = db.Column(db.String(64))                        # the OSTree commit it became
    source_ref = db.Column(db.String(255))                   # the ref inside the bundle
    runtime = db.Column(db.String(255))
    bundle_file = db.Column(db.String(120))                  # under DATA_DIR/bundles
    bundle_size = db.Column(db.BigInteger)
    bundle_sha256 = db.Column(db.String(64))
    origin = db.Column(db.String(20), default="upload", nullable=False)   # upload | promote | rollback
    origin_release_id = db.Column(db.Integer)
    error = db.Column(db.Text, default="", nullable=False)
    created_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    published_at = db.Column(db.DateTime)


class Job(db.Model):
    """Work on the repository, done one at a time by the job thread (jobs.py):
    the repository can only take one writer."""
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)          # import | promote | rollback | ...
    release_id = db.Column(db.Integer, db.ForeignKey("releases.id", ondelete="SET NULL"))
    payload = db.Column(db.Text, default="{}", nullable=False)
    status = db.Column(db.String(10), default="queued", nullable=False, index=True)
    log = db.Column(db.Text, default="", nullable=False)
    created_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)


class ApiToken(db.Model):
    """A key for the API and the MCP server, made in the admin.

    Only a hash is kept; the token itself is shown once, when it is made. It
    acts with its owner's account and only within its scopes (api.SCOPES).
    """
    __tablename__ = "api_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    prefix = db.Column(db.String(12), nullable=False)          # shown to tell tokens apart
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    scopes = db.Column(db.String(200), nullable=False)          # space-separated
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_used_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime)

    user = db.relationship("User", backref=db.backref("api_tokens", cascade="all, delete-orphan",
                                                      passive_deletes=True))

    @property
    def scope_set(self) -> set[str]:
        return set(self.scopes.split())


class StatDay(db.Model):
    """Counts per day: ``check`` for update checks (the repository summary),
    ``pull`` for a commit's download (its .commitmeta), keyed by commit."""
    __tablename__ = "stat_days"

    day = db.Column(db.String(10), primary_key=True)        # YYYY-MM-DD, UTC
    metric = db.Column(db.String(10), primary_key=True)     # check | pull
    target = db.Column(db.String(64), primary_key=True)     # "summary" | <commit>
    uniques = db.Column(db.Integer, default=0, nullable=False)
    hits = db.Column(db.Integer, default=0, nullable=False)


class StatSeen(db.Model):
    """Who was already counted today, as a salted hash that changes every
    day, so nobody can be followed from one day to the next. Pruned after
    stats.SEEN_DAYS."""
    __tablename__ = "stat_seen"

    day = db.Column(db.String(10), primary_key=True)
    metric = db.Column(db.String(10), primary_key=True)
    target = db.Column(db.String(64), primary_key=True)
    visitor = db.Column(db.String(16), primary_key=True)
