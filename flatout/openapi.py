"""The API's OpenAPI 3.1 description, built from the code.

Paths come from the api blueprint's routes, their summaries from the view
functions' docstrings, their required scope from ``api.needs``. The site
document's JSON Schema is generated from site_schema, the same declaration
that validates it, so the description can't drift from what the API accepts.
"""
from __future__ import annotations

import inspect
import re

from flask import current_app

from . import __version__, site_schema
from .api import SCOPES


def _field_schema(f: dict) -> dict:
    t = f["type"]
    out: dict = {"description": f["label"] + (f". {f['help']}" if f.get("help") else "")}
    if t == "group":
        out.update(type="object", additionalProperties=False,
                   properties={k: _field_schema(v) for k, v in f["fields"].items()})
    elif t == "list":
        out.update(type="array", items=_field_schema(f["item"]))
        if f.get("max") is not None:
            out["maxItems"] = f["max"]
    elif t == "bool":
        out["type"] = "boolean"
    elif t == "int":
        out.update(type="integer", minimum=f["min"], maximum=f["max"])
    elif t == "select":
        out.update(type="string", enum=[c["value"] for c in f["choices"]])
    elif t == "color":
        out.update(type="string", pattern="^#[0-9a-fA-F]{6}$")
    else:
        out["type"] = "string"
        if f.get("max") is not None:
            out["maxLength"] = f["max"]
        if t == "icon":
            out["description"] += " Built-in names: " + ", ".join(site_schema.schema_json()["icons"]) + "."
        if t == "font":
            out["description"] += " Built-in: " + ", ".join(c["value"] for c in f["choices"]) + "."
        if t == "markdown":
            out["format"] = "markdown"
    if "default" in f and t not in ("group", "list"):
        out["default"] = f["default"]
    return out


def document_schema() -> dict:
    s = site_schema.schema_json()
    doc = _field_schema(s["document"])
    common = {k: _field_schema(v) for k, v in s["section_common"].items()}
    variants = []
    for name, t in s["section_types"].items():
        props = dict(common)
        props["type"] = {"const": name, "description": t["label"]}
        props.update({k: _field_schema(v) for k, v in t["fields"].items()})
        variants.append({"title": t["label"], "description": t["description"], "type": "object",
                         "properties": props, "required": ["id", "type"], "additionalProperties": False})
    doc["properties"]["sections"] = {"type": "array", "description": "The homepage's sections, in order.",
                                     "items": {"oneOf": variants}}
    doc["description"] = ("The whole public site. Text fields may use these placeholders: "
                          + ", ".join(f"{k} ({v})" for k, v in s["placeholders"].items()) + ".")
    return doc


def spec() -> dict:
    from .site import base_url
    paths: dict = {}
    for rule in current_app.url_map.iter_rules():
        if not rule.endpoint.startswith("api.") or rule.rule.endswith("/"):
            continue
        view = current_app.view_functions[rule.endpoint]
        doc = inspect.getdoc(view) or ""
        summary, _, description = doc.partition("\n\n")
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule)
        scope = getattr(view, "api_scope", None)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            op = {
                "operationId": rule.endpoint.removeprefix("api.") + ("" if method in ("GET",) else f"_{method.lower()}"),
                "summary": " ".join(summary.split()) or rule.endpoint.removeprefix("api.").replace("_", " "),
                "responses": {"200": {"description": "OK"}, "401": {"description": "No valid token or session"},
                              "4XX": {"description": 'An error: {"error": "…"}, and "errors" for a document that doesn\'t validate'}},
            }
            if description:
                op["description"] = " ".join(description.split())
            if scope:
                op["x-scope"] = scope
                op["security"] = [{"token": []}]
            else:
                op["description"] = (op.get("description", "") + " Session only: not available to API tokens.").strip()
            params = [{"name": n, "in": "path", "required": True,
                       "schema": {"type": "integer" if "int:" + n in rule.rule else "string"}}
                      for n in rule.arguments]
            if params:
                op["parameters"] = params
            if rule.endpoint in ("api.site_put",) and method == "PUT":
                op["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SiteDocument"}}}}
            if rule.endpoint == "api.releases_create" and method == "POST":
                op["requestBody"] = {"content": {
                    "multipart/form-data": {"schema": {"type": "object", "properties": {
                        "file": {"type": "string", "format": "binary", "description": "A bundle made by flatpak build-bundle"},
                        "channel": {"type": "string", "enum": ["stable", "beta"]},
                        "version": {"type": "string"}, "notes": {"type": "string", "format": "markdown"}}, "required": ["file"]}},
                    "application/json": {"schema": {"type": "object", "properties": {
                        "url": {"type": "string", "description": "An https address to download the bundle from"},
                        "channel": {"type": "string", "enum": ["stable", "beta"]},
                        "version": {"type": "string"}, "notes": {"type": "string"}}, "required": ["url"]}}}}
            paths.setdefault(path, {})[method.lower()] = op
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Flatout API", "version": __version__,
            "description": ("Edit and publish the site, manage media, upload and promote Flatpak releases, "
                            "and read install numbers. Send `Authorization: Bearer <token>`; tokens are made "
                            "in the admin under API and agents. Changes to the site go to a draft; "
                            "POST /site/publish makes them live. Scopes: "
                            + "; ".join(f"{k}: {v}" for k, v in SCOPES.items()) + "."),
        },
        "servers": [{"url": base_url() + "/api/v1"}],
        "paths": {p.removeprefix("/api/v1") or "/": ops for p, ops in sorted(paths.items())},
        "components": {
            "securitySchemes": {"token": {"type": "http", "scheme": "bearer"}},
            "schemas": {"SiteDocument": document_schema()},
        },
    }
