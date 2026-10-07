"""An MCP server, so AI agents can run the site and the repository.

It speaks the Model Context Protocol's Streamable HTTP transport at /mcp:
each POST carries one JSON-RPC message and gets one JSON answer (the
transport allows a plain JSON response instead of an event stream, and none
of these tools needs to stream). It is stateless, so it needs no session id.

Every tool is a thin wrapper over one REST endpoint (api.py), called inside
this process with the caller's own token. Scopes, validation and error
messages are therefore exactly the API's, and anything an agent can do, a
script can do too.
"""
from __future__ import annotations

import json
import re

from flask import Blueprint, Response, current_app, jsonify, request

from . import __version__
from .api import resolve_token, token_from_header
from .sanitize import strip_tags

bp = Blueprint("mcp", __name__)

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """Flatout runs a Flatpak app's homepage and its Flatpak repository.

The site is one JSON document with a draft and a live copy. Every change goes to
the draft; nothing is public until publish_site. Before editing, call
get_site_schema to learn the fields and section types, then get_site or
list_sections. Prefer update_section and update_site (JSON merge patches) over
replace_site. Text may use placeholders such as {app_name} and {version}.
Images must be uploaded first (upload_media), then referenced by the /media/...
address it returns. preview_site shows the draft's text before publishing.

Releases: upload_release_from_url imports a .flatpak bundle into a channel
(stable or beta) as a background job; poll get_release or get_job until its
status is live or failed. promote_release copies the live beta to stable.
rollback_release makes an earlier build live again."""


# ———————————————————————————— Tools ————————————————————————————

def _obj(props: dict, required: list | None = None) -> dict:
    out = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


S = {"type": "string"}
I = {"type": "integer"}
CHANNEL = {"type": "string", "enum": ["stable", "beta"]}

# name: (description, input schema, method, path template, how to build the body, read-only?, destructive?)
TOOLS = {
    "get_site": ("Read the site document: the draft (default) or the live version.",
                 _obj({"version": {"type": "string", "enum": ["draft", "live"]}}),
                 "GET", "/site?version={version}", None, True, False),
    "get_site_schema": ("The site document's fields and section types, with labels, limits and defaults. Read this before editing.",
                        _obj({}), "GET", "/site/schema", None, True, False),
    "update_site": ("Change the draft with a JSON merge patch (RFC 7396): objects merge, null removes a key, lists are replaced whole. "
                    "For one section, update_section is simpler.",
                    _obj({"patch": {"type": "object", "description": "For example {\"app\": {\"name\": \"Gnomish\"}}"}}, ["patch"]),
                    "PATCH", "/site", "patch", False, False),
    "replace_site": ("Replace the whole draft document. Fields left out take their defaults.",
                     _obj({"document": {"type": "object"}}, ["document"]), "PUT", "/site", "document", False, True),
    "list_sections": ("The draft homepage's sections, in order, with their ids and types.",
                      _obj({}), "GET", "/site/sections", None, True, False),
    "get_section": ("One section of the draft, every field.", _obj({"id": S}, ["id"]),
                    "GET", "/site/sections/{id}", None, True, False),
    "update_section": ("Change fields of one section in the draft (a merge patch). \"enabled\": false hides it.",
                       _obj({"id": S, "values": {"type": "object"}}, ["id", "values"]),
                       "PATCH", "/site/sections/{id}", "values", False, False),
    "add_section": ("Add a section to the draft. type is one of the section types in get_site_schema.",
                    _obj({"type": S, "after": {"type": "string", "description": "The id of the section to insert after"},
                          "position": {"type": "integer", "description": "0 puts it first"},
                          "id": S, "values": {"type": "object"}}, ["type"]),
                    "POST", "/site/sections", "*", False, False),
    "delete_section": ("Remove a section from the draft.", _obj({"id": S}, ["id"]),
                       "DELETE", "/site/sections/{id}", None, False, True),
    "reorder_sections": ("Set the order of the draft's sections. ids must list every section once.",
                         _obj({"ids": {"type": "array", "items": S}}, ["ids"]), "POST", "/site/sections/order", "*", False, False),
    "preview_site": ("The draft homepage as visitors would read it, as plain text.",
                     _obj({}), "GET", "/site/preview", None, True, False),
    "publish_site": ("Make the draft live.", _obj({"note": {"type": "string", "description": "What changed, for the history"}}),
                     "POST", "/site/publish", "*", False, False),
    "discard_draft": ("Throw away the draft's changes and go back to the live version.", _obj({}),
                      "POST", "/site/discard", "*", False, True),
    "list_revisions": ("Published versions of the site, newest first.", _obj({}), "GET", "/site/revisions", None, True, False),
    "restore_revision": ("Put a published version back in the draft (publish it afterwards).", _obj({"id": I}, ["id"]),
                         "POST", "/site/revisions/{id}/restore", "*", False, False),
    "list_media": ("Uploaded images and fonts, with the /media/... address to use in the site.",
                   _obj({"kind": {"type": "string", "enum": ["image", "font"]}}), "GET", "/media?kind={kind}", None, True, False),
    "upload_media": ("Upload an image or a font, base64-encoded. Returns its /media/... address.",
                     _obj({"filename": S, "data_base64": S, "alt": S}, ["filename", "data_base64"]),
                     "POST", "/media", "*", False, False),
    "update_media": ("Change an upload's description (alt text) or name.", _obj({"id": I, "alt": S, "name": S}, ["id"]),
                     "PATCH", "/media/{id}", "*", False, False),
    "delete_media": ("Delete an upload the site no longer uses.", _obj({"id": I}, ["id"]),
                     "DELETE", "/media/{id}", None, False, True),
    "list_releases": ("Releases, newest first.", _obj({"channel": CHANNEL, "limit": I}),
                      "GET", "/releases?channel={channel}&limit={limit}", None, True, False),
    "get_release": ("One release, with the log of the job that published it.", _obj({"id": I}, ["id"]),
                    "GET", "/releases/{id}", None, True, False),
    "upload_release_from_url": ("Have Flatout download a .flatpak bundle and publish it on a channel. Answers at once; "
                                "the import runs as a job (see get_release).",
                                _obj({"url": S, "channel": CHANNEL, "version": S, "notes": {"type": "string", "description": "Markdown"}}, ["url"]),
                                "POST", "/releases", "*", False, False),
    "update_release": ("Change a release's notes or the version label shown on the site.",
                       _obj({"id": I, "notes": S, "version": S}, ["id"]), "PATCH", "/releases/{id}", "*", False, False),
    "promote_release": ("Copy the live builds of one channel to another, usually beta to stable.",
                        _obj({"from": CHANNEL, "to": CHANNEL, "arch": S, "notes": S}), "POST", "/releases/promote", "*", False, False),
    "rollback_release": ("Make an earlier build of a channel live again (as a new commit).", _obj({"id": I}, ["id"]),
                         "POST", "/releases/{id}/rollback", "*", False, True),
    "end_channel": ("Retire a channel: installed copies are told no more updates will come.",
                    _obj({"channel": CHANNEL, "message": S}, ["channel"]), "POST", "/channels/{channel}/end", "*", False, True),
    "get_job": ("A repository job's status and log.", _obj({"id": I}, ["id"]), "GET", "/jobs/{id}", None, True, False),
    "get_repository": ("The repository: the app ID, install addresses, signing key and settings, and live releases "
                       "for another app ID than the site's (unmatched_app_ids).",
                       _obj({}), "GET", "/repo", None, True, False),
    "update_repository_settings": ("Set the Flatpak app ID the site installs, the remote name, the public address, "
                                   "the runtime repository or how many builds to keep. An empty string restores a default.",
                                   _obj({"app_id": S, "remote_name": S, "public_url": S, "runtime_repo": S, "prune_depth": I}),
                                   "PATCH", "/repo/settings", "*", False, False),
    "create_signing_key": ("Create the repository's signing key, if it has none yet.", _obj({"name": S, "email": S}),
                           "POST", "/repo/key", "generate", False, False),
    "get_install_stats": ("Installs checking for updates per day, and downloads per release.", _obj({"days": I}),
                          "GET", "/stats?days={days}", None, True, False),
}


def tool_list() -> list[dict]:
    out = []
    for name, (desc, schema, method, _path, _body, read_only, destructive) in TOOLS.items():
        out.append({
            "name": name, "description": desc, "inputSchema": schema,
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive, "openWorldHint": False},
        })
    return out


def _path(template: str, args: dict) -> str:
    path, _, query = template.partition("?")

    def fill(m):
        value = args.get(m.group(1))
        if value is None:
            raise ValueError(f'"{m.group(1)}" is required.')
        return str(value)

    path = re.sub(r"\{(\w+)\}", fill, path)
    if query:
        pairs = []
        for part in query.split("&"):
            key, _, ref = part.partition("=")
            value = args.get(ref.strip("{}"))
            if value is not None and value != "":
                pairs.append(f"{key}={value}")
        if pairs:
            path += "?" + "&".join(pairs)
    return "/api/v1" + path


def call_tool(name: str, args: dict, auth: str) -> tuple[str, bool]:
    if name not in TOOLS:
        raise KeyError(name)
    _desc, _schema, method, template, body_spec, _ro, _d = TOOLS[name]
    try:
        path = _path(template, args)
    except ValueError as err:
        return str(err), True
    if body_spec == "*":
        body = {k: v for k, v in args.items() if f"{{{k}}}" not in template}
    elif body_spec == "generate":
        body = {"action": "generate", **args}
    elif body_spec:
        body = args.get(body_spec)
    else:
        body = None
    client = current_app.test_client()
    kwargs = {"headers": {"Authorization": auth, "Accept": "application/json"}}
    if body is not None:
        kwargs["json"] = body
    resp = client.open(path, method=method, **kwargs)
    if name == "preview_site" and resp.status_code == 200:
        text = strip_tags(resp.get_data(as_text=True))
        return re.sub(r"\n{3,}", "\n\n", text).strip()[:30000], False
    try:
        data = resp.get_json()
    except Exception:
        data = None
    if name == "get_site_schema" and isinstance(data, dict):
        data["icons"] = sorted(data.get("icons", {}))   # names are enough; the SVG is noise here
    text = json.dumps(data if data is not None else {"status": resp.status_code}, indent=1, ensure_ascii=False)
    return text, resp.status_code >= 400


# ———————————————————————————— The transport ————————————————————————————

def _rpc_error(msg_id, code: int, message: str, status: int = 200):
    return jsonify(jsonrpc="2.0", id=msg_id, error={"code": code, "message": message}), status


def _unauthorized(message: str):
    resp = jsonify(jsonrpc="2.0", id=None, error={"code": -32001, "message": message})
    resp.status_code = 401
    resp.headers["WWW-Authenticate"] = 'Bearer realm="Flatout", error="invalid_token"'
    return resp


@bp.route("/mcp", methods=["POST"])
def endpoint():
    # Browsers send Origin; a page on another site must not reach this
    # endpoint through a visitor's browser (DNS rebinding).
    origin = request.headers.get("Origin")
    if origin and re.sub(r"^https?://", "", origin).rstrip("/") != request.host:
        return Response("Origin not allowed.", status=403)
    token = token_from_header()
    if token is None:
        return _unauthorized("Send an API token: Authorization: Bearer <token>. Tokens are made in Flatout's admin under API and agents.")
    if resolve_token(token) is None:
        return _unauthorized("This API token is unknown, revoked or expired.")
    msg = request.get_json(silent=True)
    if isinstance(msg, list):
        return _rpc_error(None, -32600, "Batches aren't supported; send one message per request.", 400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Not a JSON-RPC 2.0 message.", 400)
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if msg_id is None:
        # A notification (initialized, cancelled): acknowledged, no answer.
        return Response(status=202)

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "flatout", "title": "Flatout", "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _rpc_error(msg_id, -32602, "arguments must be an object.")
        try:
            text, is_error = call_tool(name, args, request.headers["Authorization"])
        except KeyError:
            return _rpc_error(msg_id, -32602, f"There is no tool called {name}.")
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    else:
        return _rpc_error(msg_id, -32601, f"{method} isn't supported.")
    return jsonify(jsonrpc="2.0", id=msg_id, result=result)


@bp.route("/mcp", methods=["GET", "DELETE"])
def no_stream():
    """No server-initiated stream and no sessions to end."""
    return Response("This MCP server answers POST requests only.", status=405, headers={"Allow": "POST"})
