"""The site's finer controls: how sections lay out and move, the install
dialog's wording, the not-found page, Markdown's alignment and anchors, and
the owner's own CSS. All of it is in the document, so the editor, the API
and the MCP tools reach it like any other field."""
import re

import pytest

from flatout import site_schema


def h(csrf):
    return {"X-CSRF": csrf}


def section(client, csrf, sid, **values):
    resp = client.patch(f"/api/v1/site/sections/{sid}", json=values, headers=h(csrf))
    assert resp.status_code == 200, resp.get_json()


def preview(client):
    return client.get("/admin/preview").data.decode()


@pytest.fixture()
def repo_state(monkeypatch):
    """A stable release on two machines, a beta, and an RPM built for
    Intel/AMD only, without a repository on disk."""
    from flatout import releases
    real = releases.public_info

    def build(version):
        return {"version": version, "published_at": "2026-01-01T00:00:00Z", "notes": "",
                "arches": ["x86_64", "aarch64"], "bundles": {"x86_64": False, "aarch64": False},
                "builds": {a: {"version": version, "published_at": "2026-01-01T00:00:00Z", "bundle": False}
                           for a in ("x86_64", "aarch64")},
                "behind": []}

    def info(doc, base):
        out = real(doc, base)
        out["stable"], out["beta"] = build("1.0.0"), build("1.1.0-beta.1")
        out["packages"]["rpm"]["stable"] = {"name": "gnomish", "version": "1.0.0",
                                            "downloads": {"x86_64": base + "/download/gnomish-x86_64.rpm"}}
        return out
    monkeypatch.setattr(releases, "public_info", info)


def test_new_fields_leave_a_site_as_it_was(client, csrf, admin):
    """Every control added here defaults to the look a site already had."""
    doc = site_schema.default_document()
    assert site_schema.validate(doc) == doc
    hero = doc["sections"][0]
    assert (hero["tilt"], hero["fade"]) == (False, False)
    page = preview(client)
    assert "data-tilt" not in page and "hero--fade" not in page and "prose__part" not in page


def test_the_hero_screenshot_can_tilt(client, csrf, admin):
    section(client, csrf, "top", tilt=True, screenshot="https://example.com/shot.png",
            announcement={"text": "**New name**, same app.", "link_label": "", "link_url": ""})
    page = preview(client)
    assert '<div class="shot__stage" data-tilt>' in page
    assert 'loading="eager"' in page                     # the first screen's picture isn't deferred
    assert "<strong>New name</strong>, same app." in page


def test_screenshots_in_one_row_by_width_share(client, csrf, admin):
    section(client, csrf, "screenshots", enabled=True, layout="row", joined=True, items=[
        {"image": "https://example.com/a.png", "image_dark": "", "alt": "A", "title": "", "caption": "One",
         "share": 1000},
        {"image": "https://example.com/b.png", "image_dark": "", "alt": "B", "title": "", "caption": "Two",
         "share": 1325},
    ])
    page = preview(client)
    assert 'class="section section--joined"' in page
    assert "grid-template-columns: minmax(0, 1.000fr) minmax(0, 1.325fr)" in page
    assert 'style="--col: 2"' in page
    # Without shares, and without known picture shapes, it falls back to the grid.
    section(client, csrf, "screenshots", items=[
        {"image": "https://example.com/a.png", "image_dark": "", "alt": "A", "title": "", "caption": "", "share": 0},
        {"image": "https://example.com/b.png", "image_dark": "", "alt": "B", "title": "", "caption": "", "share": 0},
    ])
    assert "gallery gallery--2" in preview(client)


def test_a_text_section_s_width_buttons_and_fade(client, csrf, admin):
    section(client, csrf, "about", enabled=True, title="About", lead="Read the [license](/license).",
            body="### First {: .center }\n\nWords.\n\n### Second\n\nMore.", width=640, buttons_at="lead",
            buttons=[{"label": "Source", "url": "https://example.com", "icon": "", "style": "dark"}],
            reveal="headings")
    page = preview(client)
    block = page[page.index('id="about"'):]
    block = block[:block.index("</section>")]
    assert 'style="max-width: 688px"' in block                   # the text's width, plus the padding
    assert '<a href="/license">license</a>' in block               # links in the introduction
    assert block.index('class="actions reveal-me"') < block.index('class="prose"')   # buttons under it
    assert block.count('class="prose__part reveal-me"') == 2      # each heading fades in with its text
    assert '<h3 class="center">First</h3>' in block
    section(client, csrf, "about", reveal="none")
    block = preview(client).split('id="about"')[1].split("</section>")[0]
    assert "reveal-me" not in block


def test_the_beta_section_s_own_layout(client, csrf, admin, repo_state):
    section(client, csrf, "beta", icon="https://example.com/beta.png", show_version=False, tinted=False,
            button_at="text", button_note="Opens in your app store.", command_intro="Or run:",
            command="flatpak install --from {beta_flatpakref_url}", after="Updates arrive by themselves.",
            width=640, reveal="whole")
    page = preview(client)
    block = page[page.index('id="beta"'):]
    block = block[:block.index("</section>")]
    assert 'class="section"' in block and "section--tint" not in block
    assert 'class="beta__icon" src="https://example.com/beta.png" alt="Gnomish beta app icon"' in block
    assert "Beta 1.1.0-beta.1" not in block
    assert block.index('class="prose beta__text') < block.index("Download the beta")   # text, then the button
    assert "Opens in your app store." in block and "Or run:" in block
    assert re.search(r'<code id="beta-cmd">flatpak install --from http://localhost/flatpak/\S+-beta\.flatpakref</code>', block)
    assert 'style="max-width: 640px"' in block
    assert block.count("reveal-me") == 2                  # the heading, then everything under it


def test_an_install_guide_tab_laid_out_like_a_handset_page(client, csrf, admin):
    distro = {"name": "Mint", "status": "setup", "status_text": "One step", "body": "Installs as is, or run:",
              "command": "flatpak install x", "command_at": "body", "steps_title": "Remember passwords",
              "steps_intro": "Once:", "note": "Done then.",
              "steps": [{"text": "**Install the keyring**", "command": "sudo apt install seahorse"}]}
    section(client, csrf, "install", distros=[distro], after="", buttons=[], closing=[
        {"text": "Found a bug?", "buttons": [{"label": "Report it", "url": "https://example.com/issues",
                                              "icon": "", "style": "dark"}]},
        {"text": "Like it?", "buttons": [{"label": "Buy a coffee", "url": "https://example.com/coffee",
                                          "icon": "", "style": "coffee"}]},
    ])
    page = preview(client)
    panel = page[page.index('id="install-panel-1"'):]
    order = [panel.index(s) for s in ("Installs as is", "flatpak install x", "Remember passwords", "Once:",
                                      "Install the keyring", "Done then.")]
    assert order == sorted(order)
    order = [page.index(s) for s in ("Found a bug?", "Report it", "Like it?", "Buy a coffee")]
    assert order == sorted(order)


def test_the_footer_can_have_a_wordmark_of_its_own(client, csrf, admin):
    client.patch("/api/v1/site", json={"footer": {"wordmark": "https://example.com/wm.svg",
                                                  "wordmark_dark": "https://example.com/wm-dark.svg"}},
                 headers=h(csrf))
    page = preview(client)
    footer = page[page.index('<footer'):]
    assert 'class="brand__wordmark brand__wordmark--footer" src="https://example.com/wm.svg"' in footer
    assert 'srcset="https://example.com/wm-dark.svg"' in footer
    header = page[:page.index('<footer')]
    assert "wm.svg" not in header                       # the header keeps the name as text


def test_the_install_dialog_s_wording(client, csrf, admin, repo_state):
    page = preview(client)
    assert "Install file" in page and "Version 1.0.0" in page        # the built-in text to start with
    resp = client.patch("/api/v1/site", json={"install": {
        "show_version": False, "ref_title": "Install via Flatpakref", "ref_tip_title": "Using Bazaar?",
        "ref_tip_text": "Add the repository once:", "ref_tip_command": "flatpak remote-add x {site_url}/x",
        "bundle_url": "https://github.com/x/y/releases/latest/download/App-{arch}.flatpak",
        "bundle_button": "Download Gnomish for", "bundle_after": "Built for **{arch_name}**.",
        "rpm_files_title": "Prefer a download?", "rpm_files_text": "The same package as a file.",
        "rpm_files_button": "Download the RPM", "bundle_desc": ""}}, headers=h(csrf))
    assert resp.status_code == 200, resp.get_json()
    page = preview(client)
    assert "Version 1.0.0" not in page and "Install via Flatpakref" in page
    assert "<summary>Using Bazaar?</summary>" in page and "flatpak remote-add x http://localhost/x" in page
    # The full download from the owner's address, though nothing was uploaded here.
    assert 'data-href="https://github.com/x/y/releases/latest/download/App-{arch}.flatpak"' in page
    assert "flatpak install ./App-x86_64.flatpak" in page
    assert '<strong><span class="dl-arch-name">Intel/AMD</span></strong>' in page
    assert "The whole app in one .flatpak file." not in page        # emptied, so left out
    # The RPM: greyed out on ARM, and its own download button.
    assert 'data-arches="x86_64"' in page and "Intel/AMD only</span>" in page
    assert 'class="btn btn--ghost" href="http://localhost/download/gnomish-x86_64.rpm"' in page
    assert ">rpm</span>" in page


def test_a_site_saved_before_the_wording_fields_gets_the_built_in_text(app, client, csrf, admin, repo_state):
    from flatout import site
    from flatout.models import SiteDocument, db
    with app.app_context():
        doc = site.get("draft")
        wording = [k for k in doc["install"] if k.startswith(("ref_", "bundle_", "repo_", "rpm_"))
                   and not k.endswith("_method")] + ["show_version"]
        for key in wording:
            del doc["install"][key]
        del doc["not_found"]
        row = db.session.get(SiteDocument, "draft")
        import json
        row.data = json.dumps(doc)
        db.session.commit()
    page = preview(client)
    assert "Install file" in page and "Download the install file" in page and "Version 1.0.0" in page


def test_the_not_found_page_s_wording(app, client, csrf, admin):
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    client.patch("/api/v1/site/status", json={"status": "published"}, headers=h(csrf))
    visitor = app.test_client()
    page = visitor.get("/no-such-page")
    assert page.status_code == 404 and b"There is nothing at this address" in page.data
    client.patch("/api/v1/site", json={"not_found": {"title": "Lost in the mail", "message": "Not here.",
                                                     "button": "Back to home"}}, headers=h(csrf))
    client.post("/api/v1/site/publish", json={}, headers=h(csrf))
    page = visitor.get("/no-such-page").data.decode()
    assert "Lost in the mail" in page and "Not here." in page and "Back to home" in page


def test_markdown_may_align_a_block_and_name_a_heading_only():
    from flatout.sanitize import sanitize_html
    import markdown
    html = markdown.markdown("## Counts {#install-counts}\n\nA date.\n{: .note }\n\nOdd.\n{: .x }\n\n"
                             "### Bad {#Bad\" onmouseover=x}", extensions=["extra"])
    clean = sanitize_html(html, site=True)
    assert '<h2 id="install-counts">Counts</h2>' in clean and '<p class="note">A date.</p>' in clean
    assert 'class="x"' not in clean and "onmouseover" not in clean and 'id="Bad' not in clean
    # Foreign HTML (release notes) keeps none of it.
    assert "id=" not in sanitize_html(html) and "class=" not in sanitize_html(html)


def test_the_owner_s_css_comes_last_and_cannot_close_its_element(client, csrf, admin):
    resp = client.patch("/api/v1/site", json={"theme": {
        "custom_css": ".hero { color: red; }</style><script>alert(1)</script>"}}, headers=h(csrf))
    assert resp.status_code == 200
    page = preview(client)
    style = page[page.index('<style id="site-theme">'):page.index("</style>", page.index('<style id="site-theme">'))]
    assert style.rstrip().endswith(".hero { color: red; }<\\/style><script>alert(1)<\\/script>")
    assert "<script>alert(1)</script>" not in page
