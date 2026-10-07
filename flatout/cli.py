"""Command-line tools for running an instance.

Inside the container:

    docker exec -it flatout flask create-user you@example.com --admin
    docker exec -it flatout flask reset-password you@example.com
    docker exec flatout flask backup /data/backup-$(date +%F).db
    docker exec -it flatout flask restore-backup /data/flatout-backup-….tar.gpg
    docker exec flatout flask turnstile off

(The image sets FLASK_APP, so no --app is needed inside the container.)

``reset-password`` is the way back in for an admin locked out of the only admin
account; there is no email-based reset, because the app sends no email.
``restore-backup`` restores a backup made under Settings > Backup onto a fresh
install, for a file too large to upload comfortably through the setup wizard.
``turnstile off`` is the way back in when a Turnstile widget stops working
(its hostname list changed, Cloudflare is unreachable) and nobody can sign in.
"""
import sqlite3
from pathlib import Path

import click
from flask import Flask, current_app
from flask.cli import with_appcontext
from sqlalchemy import func

from .auth import EMAIL_RE, MIN_PASSWORD
from .models import User, db, set_setting


def register(app: Flask) -> None:
    app.cli.add_command(create_user)
    app.cli.add_command(reset_password)
    app.cli.add_command(backup)
    app.cli.add_command(restore_backup)
    app.cli.add_command(turnstile)


def _find(username: str) -> User | None:
    return User.query.filter(func.lower(User.username) == username.strip().lower()).first()


def _ask_password() -> str:
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < MIN_PASSWORD:
        raise click.ClickException(f"Passwords need at least {MIN_PASSWORD} characters.")
    return password


@click.command("create-user")
@with_appcontext
@click.argument("username")
@click.option("--name", default=None, help="Display name.")
@click.option("--admin", is_flag=True, help="Make the account an admin.")
def create_user(username, name, admin):
    """Create an account."""
    username = username.strip().lower()
    if not EMAIL_RE.match(username):
        raise click.ClickException("The username must be an email address.")
    if _find(username):
        raise click.ClickException(f"{username} already exists.")
    user = User(username=username, name=name, is_admin=admin)
    user.set_password(_ask_password())
    db.session.add(user)
    db.session.commit()
    click.echo(f"Created {username}{' (admin)' if admin else ''}.")


@click.command("reset-password")
@with_appcontext
@click.argument("username")
def reset_password(username):
    """Set a new password for an account."""
    user = _find(username)
    if not user:
        raise click.ClickException(f"No account called {username}.")
    user.set_password(_ask_password())
    db.session.commit()
    click.echo(f"Password updated for {user.username}.")


@click.command("backup")
@with_appcontext
@click.argument("destination", type=click.Path(dir_okay=False, path_type=Path))
def backup(destination: Path):
    """Write a consistent copy of the SQLite database, safe while the app runs.

    Uses SQLite's online backup API, so a write in progress can't leave a torn
    copy the way copying the file (and its -wal) by hand can.
    """
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        raise click.ClickException("backup only knows how to copy a SQLite database.")
    source = uri.removeprefix("sqlite:///")
    if destination.exists():
        raise click.ClickException(f"{destination} already exists.")
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    click.echo(f"Backed up to {destination} ({destination.stat().st_size // 1024} KB).")


@click.command("restore-backup")
@with_appcontext
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.password_option("--passphrase", prompt="Backup passphrase", confirmation_prompt=False,
                       help="The passphrase the backup was made with (asked for if left out).")
def restore_backup(source: Path, passphrase: str):
    """Restore a backup (Settings > Backup) onto this fresh install.

    Only before the first account exists. Restart the container afterwards:
    the running server still has the old data open.
    """
    from . import backup as backups
    from .setup import needs_setup
    if not needs_setup():
        raise click.ClickException("This instance is already set up. Restore onto a fresh install.")
    try:
        manifest = backups.restore_file(current_app._get_current_object(), source, passphrase)
    except backups.BackupError as err:
        raise click.ClickException(str(err))
    finally:
        import shutil
        for leftover in backups.restore_dir().glob("cli-*"):
            shutil.rmtree(leftover, ignore_errors=True)
    click.echo(f"Restored the backup made {manifest.get('created_at', '')[:10]} by Flatout "
               f"{manifest.get('version')}. Restart the container, then sign in with an account from it.")


@click.group("turnstile")
def turnstile():
    """Cloudflare Turnstile on the sign-in and sign-up pages."""


@turnstile.command("status")
@with_appcontext
def turnstile_status():
    """Say whether Turnstile is on, and where its keys come from."""
    from .auth import turnstile_config
    config = turnstile_config()
    if config is None:
        click.echo("Turnstile is off.")
    else:
        click.echo(f"Turnstile is on, site key {config['site_key']}, from the {config['source']}.")


@turnstile.command("off")
@with_appcontext
def turnstile_off():
    """Turn Turnstile off. The saved keys are kept; Settings > Security turns it
    back on after a successful challenge."""
    set_setting("turnstile_enabled", "0")
    click.echo("Turnstile is off. Sign-in and sign-up no longer show a challenge.")
