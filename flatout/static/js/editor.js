/* The site editor and the media library. No dependencies, no build step.
 *
 * The editor builds its forms from the site schema (site_schema.py, embedded
 * in the page as JSON), edits the draft document in memory, and saves the
 * whole draft through the API a moment after each change. The preview pane
 * reloads after every save, so it always shows what publishing would put
 * live. The same file runs the media library page and the media picker.
 *
 * Sections, in order: helpers, the API, the media library, the media page,
 * the media picker, then the editor: state, saving, the preview, the views
 * (the list of parts, a part's form), the field renderers, publishing.
 */
(function () {
  "use strict";

  var F = window.Flatout;
  if (!F) return;

  /* ————— Helpers ————— */
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    });
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }
  function svg(path) {
    return el("span", { class: "svgwrap", html: '<svg viewBox="0 0 24 24">' + path + "</svg>" }).firstChild;
  }
  var ICON = {
    back: '<path d="M15 5l-7 7 7 7"/>',
    chev: '<path d="m9 5 7 7-7 7"/>',
    up: '<path d="m6 15 6-6 6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    trash: '<path d="M5.5 7.5h13M9.5 7V5h5v2M7 7.5l.8 12a1 1 0 0 0 1 .9h6.4a1 1 0 0 0 1-.9l.8-12M10.2 11v6M13.8 11v6"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    grip: '<path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m20.5 16-5-5-8.5 8.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>'
  };
  function iconBtn(name, label, onclick, extra) {
    return el("button", { type: "button", class: "iconbtn iconbtn--sm " + (extra || ""), "aria-label": label, title: label, onclick: onclick }, [svg(ICON[name])]);
  }
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function getAt(obj, path) {
    return path.reduce(function (o, k) { return o == null ? undefined : o[k]; }, obj);
  }
  function setAt(obj, path, value) {
    var parent = getAt(obj, path.slice(0, -1));
    parent[path[path.length - 1]] = value;
  }
  function pathKey(path) { return JSON.stringify(path); }
  function parsePath(p) {
    // "$.sections[2].cards[0].title" -> ["sections", 2, "cards", 0, "title"]
    var out = [];
    p.replace(/^\$/, "").replace(/\.([A-Za-z0-9_]+)|\[(\d+)\]/g, function (_, key, idx) {
      out.push(key !== undefined ? key : parseInt(idx, 10));
      return "";
    });
    return out;
  }
  function humanSize(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }
  function when(iso) {
    try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); }
    catch (_) { return iso; }
  }

  /* ————— The API ————— */
  // Like app.js's request(), but keeps the status and the per-field errors a
  // 422 carries, which the forms show next to the fields.
  function call(method, url, body, headers) {
    var opts = { method: method, headers: Object.assign({ "X-CSRF": F.csrf, "Accept": "application/json" }, headers || {}) };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) {
          var err = new Error(data.error || "Something went wrong.");
          err.status = resp.status;
          err.errors = data.errors || null;
          throw err;
        }
        return data;
      });
    }, function () { throw new Error("Can't reach the server."); });
  }

  /* ————— The media library ————— */
  var Library = {
    items: null,
    load: function () {
      return call("GET", "/api/v1/media").then(function (d) { Library.items = d.media; return d.media; });
    },
    upload: function (files) {
      var list = Array.prototype.slice.call(files || []);
      var added = [];
      return list.reduce(function (p, file) {
        return p.then(function () {
          var form = new FormData();
          form.append("file", file);
          return call("POST", "/api/v1/media", form).then(function (d) {
            added.push(d.media);
            Library.items = [d.media].concat((Library.items || []).filter(function (m) { return m.id !== d.media.id; }));
          }).catch(function (err) { F.toastError(new Error(file.name + ": " + err.message)); });
        });
      }, Promise.resolve()).then(function () { return added; });
    }
  };

  function mediaCard(item, opts) {
    var thumb = item.kind === "image"
      ? el("img", { src: item.url, alt: item.alt || "", loading: "lazy" })
      : el("span", { class: "media-font", text: "Aa" });
    if (item.kind === "font") {
      var face = "MediaFont" + item.id;
      if (!document.getElementById("ff-" + item.id)) {
        document.head.appendChild(el("style", { id: "ff-" + item.id, text: '@font-face{font-family:"' + face + '";src:url("' + item.url + '")}' }));
      }
      thumb.style.fontFamily = '"' + face + '", sans-serif';
    }
    var meta = [item.width && item.height ? item.width + " × " + item.height : (item.kind === "font" ? "Font" : ""), humanSize(item.size)]
      .filter(Boolean).join(" · ");
    var card = el("div", { class: "media-card" + (opts.pick ? " media-card--pick" : ""), "data-id": item.id }, [
      el("div", { class: "media-thumb" }, [thumb]),
      el("div", { class: "media-meta" }, [
        el("span", { class: "media-name", text: item.name, title: item.name }),
        el("span", { class: "media-sub", text: meta + (item.in_use ? " · in use" : "") })
      ])
    ]);
    if (opts.pick) {
      card.setAttribute("role", "button");
      card.tabIndex = 0;
      var pick = function () { opts.pick(item); };
      card.addEventListener("click", pick);
      card.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    }
    if (opts.manage) {
      var alt = el("input", { class: "media-alt", value: item.alt || "", placeholder: item.kind === "image" ? "Description, for screen readers" : "Description", "aria-label": "Description of " + item.name, maxlength: 300 });
      alt.addEventListener("change", function () {
        call("PATCH", "/api/v1/media/" + item.id, { alt: alt.value }).then(function () { F.toast("Description saved"); }).catch(F.toastError);
      });
      card.appendChild(el("div", { class: "media-actions" }, [
        alt,
        iconBtn("copy", "Copy the address", function () {
          var url = location.origin + item.url;
          (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () {
            F.toast("Address copied");
          }, function () { prompt("The file's address:", url); });
        }),
        iconBtn("trash", item.in_use ? "In use by the site" : "Delete", function () {
          if (item.in_use) { F.toastError(new Error("The site still uses this file. Take it out of the site and publish first.")); return; }
          call("DELETE", "/api/v1/media/" + item.id).then(function () {
            Library.items = Library.items.filter(function (m) { return m.id !== item.id; });
            card.remove();
            F.toast("Deleted " + item.name);
          }).catch(F.toastError);
        }, "iconbtn--danger")
      ]));
    }
    return card;
  }

  function renderGrid(container, items, opts) {
    container.innerHTML = "";
    if (!items.length) {
      container.appendChild(el("p", { class: "hint media-empty", text: opts.empty || "Nothing uploaded yet." }));
      return;
    }
    items.forEach(function (item) { container.appendChild(mediaCard(item, opts)); });
  }

  // Drop files on a [data-dropzone] (or anywhere on the media page).
  function dropzone(target, onFiles) {
    ["dragenter", "dragover"].forEach(function (type) {
      target.addEventListener(type, function (e) {
        if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types, "Files") === -1) return;
        e.preventDefault();
        target.classList.add("is-dropping");
      });
    });
    ["dragleave", "drop"].forEach(function (type) {
      target.addEventListener(type, function () { target.classList.remove("is-dropping"); });
    });
    target.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      onFiles(e.dataTransfer.files);
    });
  }

  /* ————— The media page ————— */
  var mediaPage = document.getElementById("media-grid");
  if (mediaPage) {
    var showPage = function () { renderGrid(mediaPage, Library.items, { manage: true }); };
    Library.load().then(showPage).catch(F.toastError);
    var pageUpload = function (files) {
      Library.upload(files).then(function (added) {
        if (added.length) F.toast(added.length === 1 ? "Uploaded " + added[0].name : "Uploaded " + added.length + " files");
        showPage();
      });
    };
    document.getElementById("media-file").addEventListener("change", function (e) {
      pageUpload(e.target.files);
      e.target.value = "";
    });
    dropzone(document.querySelector(".content"), pageUpload);
  }

  /* ————— The media picker ————— */
  var picker = document.getElementById("media-modal");
  var pickerState = null;   // { kind, done }
  function openPicker(kind, done) {
    pickerState = { kind: kind, done: done };
    document.getElementById("media-title").textContent = kind === "font" ? "Choose a font" : "Choose an image";
    document.getElementById("media-modal-file").accept = kind === "font" ? ".woff2,.woff,.ttf,.otf" : "image/*,.svg,.ico";
    var grid = document.getElementById("media-modal-grid");
    var show = function () {
      renderGrid(grid, (Library.items || []).filter(function (m) { return m.kind === kind; }), {
        empty: kind === "font" ? "No fonts uploaded yet. Upload a WOFF2, WOFF, TTF or OTF file." : "No images uploaded yet.",
        pick: function (item) { picker.close(); pickerState.done(item); }
      });
    };
    grid.innerHTML = "";
    grid.appendChild(el("p", { class: "hint", text: "Loading…" }));
    Library.load().then(show).catch(F.toastError);
    pickerState.show = show;
    picker.showModal();
  }
  if (picker) {
    var pickUpload = function (files) {
      Library.upload(files).then(function (added) {
        var fitting = added.filter(function (m) { return m.kind === pickerState.kind; });
        if (fitting.length === 1 && added.length === 1) {
          picker.close();
          pickerState.done(fitting[0]);
          return;
        }
        pickerState.show();
      });
    };
    document.getElementById("media-modal-file").addEventListener("change", function (e) {
      pickUpload(e.target.files);
      e.target.value = "";
    });
    dropzone(picker, pickUpload);
  }

  /* ————— The editor ————— */
  var root = document.getElementById("editor");
  if (!root) return;

  var schemaEl = document.getElementById("site-schema");
  var S = JSON.parse(schemaEl.textContent);
  var panel = document.getElementById("editor-panel");
  var frame = document.getElementById("preview");
  var stateEl = document.getElementById("save-state");
  var publishBtn = document.getElementById("publish-btn");
  var discardBtn = document.getElementById("discard-btn");

  var E = {
    scope: root.getAttribute("data-scope"),
    doc: null,
    base: null,           // the draft's updated_at when last loaded or saved
    hasChanges: false,
    view: null,           // { kind: "list" } | { kind: "group", key } | { kind: "section", obj } | { kind: "page", obj } | { kind: "theme" }
    timer: null, saving: false, again: false, dirty: false, failed: false,
    restoreY: null, scrollTarget: null
  };

  var GROUPS = [
    ["app", "App", "Name, tagline, app ID, links"],
    ["images", "Images", "Icon, wordmark, social preview"],
    ["nav", "Header", "Links and the install button"],
    ["footer", "Footer", "Links, credit, buttons, small print"],
    ["install", "Install dialog", "The ways to install it offered"],
    ["seo", "Search and sharing", "Page title and description"]
  ];

  /* ——— Saving ——— */
  function status(text, isError) {
    stateEl.textContent = text;
    stateEl.classList.toggle("is-error", !!isError);
  }
  function updateButtons() {
    var busy = E.saving || E.dirty;
    publishBtn.disabled = !E.hasChanges && !busy;
    discardBtn.hidden = !E.hasChanges;
    var dot = document.querySelector('.navitem[href$="/admin/site"] .count');
    if (dot) dot.hidden = !E.hasChanges;
  }
  function changed() {
    E.dirty = true;
    E.hasChanges = true;
    status("Unsaved");
    updateButtons();
    clearTimeout(E.timer);
    E.timer = setTimeout(save, 650);
  }
  function save() {
    clearTimeout(E.timer);
    if (E.saving) { E.again = true; return; }
    E.saving = true;
    E.dirty = false;
    status("Saving…");
    var headers = E.base ? { "X-Draft-Base": E.base } : {};
    call("PUT", "/api/v1/site", E.doc, headers).then(function (data) {
      E.base = data.updated.updated_at;
      E.hasChanges = data.has_unpublished_changes;
      E.failed = false;
      clearErrors();
      status("Draft saved");
      refreshPreview();
    }).catch(function (err) {
      E.failed = true;
      if (err.status === 409) { conflict(err.message); status("Not saved", true); return; }
      if (err.errors) showErrors(err.errors);
      status("Not saved: " + err.message, true);
    }).finally(function () {
      E.saving = false;
      updateButtons();
      if (E.again || E.dirty) { E.again = false; save(); }
    });
  }
  // Resolves once every change is saved, or rejects if the last save failed:
  // publishing must never put out an older draft than the one on screen.
  function flush() {
    return new Promise(function (resolve, reject) {
      (function check() {
        if (E.dirty && !E.saving) save();
        if (!E.dirty && !E.saving) {
          if (E.failed) reject(new Error("The draft has a problem to fix before it can be published."));
          else resolve();
          return;
        }
        setTimeout(check, 80);
      })();
    });
  }
  window.addEventListener("beforeunload", function (e) {
    if (E.dirty || E.saving) { e.preventDefault(); e.returnValue = ""; }
  });

  function conflict(message) {
    var old = panel.querySelector(".ed-conflict");
    if (old) old.remove();
    var bar = el("div", { class: "ed-conflict", role: "alert" }, [
      el("p", { text: message }),
      el("div", { class: "ed-conflict-actions" }, [
        el("button", { type: "button", class: "btn btn--primary btn--xs", text: "Reload theirs", onclick: function () { load(); } }),
        el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Keep mine", onclick: function () {
          E.base = null;
          bar.remove();
          save();
        } })
      ])
    ]);
    panel.prepend(bar);
  }

  function clearErrors() {
    panel.querySelectorAll(".has-error").forEach(function (n) { n.classList.remove("has-error"); });
    panel.querySelectorAll(".ed-error").forEach(function (n) { n.remove(); });
  }
  function showErrors(errors) {
    clearErrors();
    var unseen = [];
    errors.forEach(function (e) {
      var target = panel.querySelector('[data-path="' + CSS.escape(pathKey(parsePath(e.path))) + '"]');
      if (!target) { unseen.push(e); return; }
      target.classList.add("has-error");
      target.appendChild(el("p", { class: "ed-error form-error", text: e.message }));
    });
    if (unseen.length) F.toastError(new Error(unseen[0].path.replace(/^\$\./, "") + ": " + unseen[0].message));
  }

  /* ——— The preview ——— */
  function previewUrl() {
    if (E.view && E.view.kind === "page") return "/admin/preview/" + encodeURIComponent(E.view.obj.slug);
    return "/admin/preview";
  }
  function refreshPreview(scrollTarget) {
    if (scrollTarget !== undefined) E.scrollTarget = scrollTarget;
    var url = previewUrl();
    document.getElementById("preview-open").href = url;
    var win = frame.contentWindow;
    var samePage = false;
    try {
      samePage = win.location.pathname === url;
      if (samePage && E.scrollTarget === null) E.restoreY = win.scrollY;
    } catch (_) {}
    if (samePage) win.location.reload();
    else frame.src = url;
  }
  frame.addEventListener("load", function () {
    var win = frame.contentWindow, doc;
    try { doc = frame.contentDocument; } catch (_) { return; }
    var mode = document.querySelector('input[name="preview-mode"]:checked').value;
    if (mode !== "auto") doc.documentElement.setAttribute("data-theme", mode);
    // Links inside the preview open in a new tab, so the editor stays put.
    doc.addEventListener("click", function (e) {
      var a = e.target.closest("a[href]");
      if (!a || a.getAttribute("href").charAt(0) === "#") return;
      e.preventDefault();
      window.open(a.href, "_blank", "noopener");
    });
    if (E.scrollTarget) {
      var target = E.scrollTarget === "footer" ? doc.querySelector(".footer") : doc.getElementById(E.scrollTarget);
      if (target) target.scrollIntoView({ block: "start" });
    } else if (E.restoreY !== null) {
      win.scrollTo(0, E.restoreY);
    }
    if (E.view && E.view.kind === "group" && E.view.key === "install") {
      var opener = doc.querySelector("[data-install-open]");
      if (opener) opener.click();
    }
    E.restoreY = null;
    E.scrollTarget = null;
  });
  document.querySelectorAll('input[name="preview-width"]').forEach(function (r) {
    r.addEventListener("change", function () {
      var stage = document.getElementById("preview-stage");
      stage.style.setProperty("--preview-w", r.value === "full" ? "100%" : r.value + "px");
      stage.classList.toggle("is-device", r.value !== "full");
    });
  });
  document.querySelectorAll('input[name="preview-mode"]').forEach(function (r) {
    r.addEventListener("change", function () { E.restoreY = null; refreshPreview(); });
  });
  function scrollPreviewTo(id) {
    try {
      var doc = frame.contentDocument;
      var target = id === "footer" ? doc.querySelector(".footer") : (id === "top-of-page" ? doc.body : doc.getElementById(id));
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (_) {}
  }

  /* ——— Loading and routing ——— */
  function load() {
    status("");
    return call("GET", "/api/v1/site?version=draft").then(function (data) {
      E.doc = data.document;
      E.base = data.updated.updated_at;
      E.hasChanges = data.has_unpublished_changes;
      updateButtons();
      route();
    }).catch(function (err) {
      panel.innerHTML = "";
      panel.appendChild(el("p", { class: "form-error", text: err.message }));
    });
  }
  // The open part lives in the URL's hash, so a reload or a link comes back to it.
  function route() {
    var hash = decodeURIComponent(location.hash.slice(1));
    var parts = hash.split("/");
    if (E.scope === "theme") return show({ kind: "theme" }, true);
    if (E.scope === "pages") {
      var page = E.doc.pages.filter(function (p) { return parts[0] === "page" && p.slug === parts[1]; })[0];
      return show(page ? { kind: "page", obj: page } : { kind: "list" }, true);
    }
    if (parts[0] === "group" && E.doc[parts[1]]) return show({ kind: "group", key: parts[1] }, true);
    var sec = E.doc.sections.filter(function (s) { return parts[0] === "section" && s.id === parts[1]; })[0];
    show(sec ? { kind: "section", obj: sec } : { kind: "list" }, true);
  }
  function hashFor(view) {
    if (view.kind === "group") return "#group/" + view.key;
    if (view.kind === "section") return "#section/" + encodeURIComponent(view.obj.id);
    if (view.kind === "page") return "#page/" + encodeURIComponent(view.obj.slug);
    return "";
  }
  function show(view, initial) {
    var previous = E.view;
    E.view = view;
    history.replaceState(null, "", location.pathname + hashFor(view));
    panel.innerHTML = "";
    panel.scrollTop = 0;
    if (view.kind === "list") renderList();
    else renderDetail();
    var pageChanged = (previous && previous.kind === "page") !== (view.kind === "page") ||
                      (previous && view.kind === "page" && previous.obj !== view.obj);
    var target = view.kind === "section" ? view.obj.id : view.kind === "group" ? ({ footer: "footer", nav: "top-of-page" }[view.key] || null) : null;
    if (initial || pageChanged || (view.kind === "group" && view.key === "install") ||
        (previous && previous.kind === "group" && previous.key === "install")) {
      refreshPreview(target);
    } else if (target) {
      scrollPreviewTo(target);
    }
  }
  window.addEventListener("hashchange", function () { if (E.doc) route(); });

  /* ——— The list of parts ——— */
  function listRow(label, sub, onOpen, extras) {
    var row = el("div", { class: "ed-row" }, (extras && extras.before) || []);
    var open = el("button", { type: "button", class: "ed-row-open", onclick: onOpen }, [
      el("span", { class: "ed-row-text" }, [
        el("span", { class: "ed-row-title", text: label }),
        sub ? el("span", { class: "ed-row-sub", text: sub }) : null
      ]),
      svg(ICON.chev)
    ]);
    row.appendChild(open);
    ((extras && extras.after) || []).forEach(function (n) { row.appendChild(n); });
    return row;
  }

  // The list shows titles as visitors read them, with the app's name filled in.
  function filled(text) {
    return String(text || "").replace(/\{app_name\}/g, E.doc.app.name || "the app");
  }
  function sectionTitle(sec) {
    var type = S.section_types[sec.type];
    var title = filled(sec.title || sec.eyebrow || "");
    return { title: title || type.label, sub: title ? type.label + " · #" + sec.id : "#" + sec.id };
  }

  function renderList() {
    if (E.scope === "pages") return renderPagesList();
    var wrap = el("div", { class: "ed-list" });
    wrap.appendChild(el("p", { class: "ed-label", text: "Every page" }));
    GROUPS.forEach(function (g) {
      wrap.appendChild(listRow(g[1], g[2], function () { show({ kind: "group", key: g[0] }); }));
    });

    wrap.appendChild(el("div", { class: "ed-label ed-label--row" }, [
      el("span", { text: "Homepage sections" }),
      el("button", { type: "button", class: "btn btn--ghost btn--xs", onclick: openAddSection }, [svg(ICON.plus), "Add"])
    ]));
    var list = el("ol", { class: "ed-sections", "aria-label": "Sections, in page order" });
    E.doc.sections.forEach(function (sec, i) {
      var t = sectionTitle(sec);
      var toggle = el("input", { type: "checkbox", class: "switch", checked: sec.enabled, "aria-label": "Show " + t.title });
      toggle.addEventListener("change", function () {
        sec.enabled = toggle.checked;
        li.classList.toggle("is-off", !sec.enabled);
        changed();
      });
      var li = el("li", { class: "ed-section" + (sec.enabled ? "" : " is-off"), draggable: "true", "data-index": i });
      li.appendChild(listRow(t.title, t.sub, function () { show({ kind: "section", obj: sec }); }, {
        before: [el("span", { class: "ed-grip", title: "Drag to reorder", "aria-hidden": "true" }, [svg(ICON.grip)])],
        after: [
          iconBtn("up", "Move up", function () { moveSection(i, -1); }),
          iconBtn("down", "Move down", function () { moveSection(i, 1); }),
          el("label", { class: "ed-switch", title: sec.enabled ? "Shown" : "Hidden" }, [toggle])
        ]
      }));
      li.addEventListener("dragstart", function (e) {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", String(i));
        li.classList.add("is-dragging");
      });
      li.addEventListener("dragend", function () { li.classList.remove("is-dragging"); });
      li.addEventListener("dragover", function (e) { e.preventDefault(); li.classList.add("is-over"); });
      li.addEventListener("dragleave", function () { li.classList.remove("is-over"); });
      li.addEventListener("drop", function (e) {
        e.preventDefault();
        li.classList.remove("is-over");
        var from = parseInt(e.dataTransfer.getData("text/plain"), 10);
        if (isNaN(from) || from === i) return;
        var moved = E.doc.sections.splice(from, 1)[0];
        E.doc.sections.splice(i, 0, moved);
        changed();
        show({ kind: "list" });
      });
      list.appendChild(li);
    });
    wrap.appendChild(list);
    wrap.appendChild(el("p", { class: "hint ed-foot", text: "Drag a section, or use its arrows, to change the order. The switch hides a section without deleting it." }));
    panel.appendChild(wrap);
  }
  function moveSection(i, step) {
    var j = i + step;
    if (j < 0 || j >= E.doc.sections.length) return;
    var s = E.doc.sections;
    var tmp = s[i]; s[i] = s[j]; s[j] = tmp;
    changed();
    show({ kind: "list" });
    var btn = panel.querySelectorAll(".ed-section")[j].querySelector(step < 0 ? '[aria-label="Move up"]' : '[aria-label="Move down"]');
    if (btn) btn.focus();
  }

  function openAddSection() {
    var grid = document.getElementById("type-grid");
    grid.innerHTML = "";
    Object.keys(S.section_types).forEach(function (type) {
      var t = S.section_types[type];
      grid.appendChild(el("button", { type: "button", class: "type-card", onclick: function () {
        document.getElementById("add-section-modal").close();
        addSection(type);
      } }, [el("strong", { text: t.label }), el("span", { text: t.description })]));
    });
    document.getElementById("add-section-modal").showModal();
  }
  function addSection(type) {
    var ids = E.doc.sections.map(function (s) { return s.id; });
    var base = type === "cta" ? "call-to-action" : type, id = base, n = 2;
    while (ids.indexOf(id) !== -1) { id = base + "-" + n; n++; }
    var sec = { id: id, type: type, enabled: true };
    var fields = S.section_types[type].fields;
    Object.keys(fields).forEach(function (k) { sec[k] = defaultFor(fields[k]); });
    E.doc.sections.push(sec);
    changed();
    show({ kind: "section", obj: sec });
  }

  function renderPagesList() {
    var wrap = el("div", { class: "ed-list" });
    wrap.appendChild(el("div", { class: "ed-label ed-label--row" }, [
      el("span", { text: "Pages" }),
      el("button", { type: "button", class: "btn btn--ghost btn--xs", onclick: addPage }, [svg(ICON.plus), "Add"])
    ]));
    if (!E.doc.pages.length) wrap.appendChild(el("p", { class: "hint", text: "No pages yet. A page has its own address, such as /privacy, and can be linked from the header or the footer." }));
    E.doc.pages.forEach(function (page) {
      wrap.appendChild(listRow(page.title || "Untitled", "/" + page.slug + (page.published ? "" : " · not published"), function () {
        show({ kind: "page", obj: page });
      }));
    });
    panel.appendChild(wrap);
  }
  function addPage() {
    var slugs = E.doc.pages.map(function (p) { return p.slug; });
    var slug = "new-page", n = 2;
    while (slugs.indexOf(slug) !== -1) { slug = "new-page-" + n; n++; }
    var page = { slug: slug, title: "New page", body: "", published: false, in_footer: true, in_nav: false };
    E.doc.pages.push(page);
    changed();
    show({ kind: "page", obj: page });
  }

  /* ——— A part's form ——— */
  function renderDetail() {
    var v = E.view, title, fields, path, actions = [];
    if (v.kind === "theme") {
      title = null;
      fields = S.document.fields.theme.fields;
      path = ["theme"];
    } else if (v.kind === "group") {
      var g = GROUPS.filter(function (x) { return x[0] === v.key; })[0];
      title = g ? g[1] : v.key;
      fields = S.document.fields[v.key].fields;
      path = [v.key];
    } else if (v.kind === "section") {
      var idx = E.doc.sections.indexOf(v.obj);
      title = S.section_types[v.obj.type].label;
      fields = Object.assign({ id: S.section_common.id }, S.section_types[v.obj.type].fields);
      path = ["sections", idx];
      actions.push(iconBtn("copy", "Duplicate this section", function () {
        var copy = clone(v.obj);
        var ids = E.doc.sections.map(function (s) { return s.id; }), n = 2;
        while (ids.indexOf(v.obj.id + "-" + n) !== -1) n++;
        copy.id = v.obj.id + "-" + n;
        E.doc.sections.splice(idx + 1, 0, copy);
        changed();
        show({ kind: "section", obj: copy });
        F.toast("Section duplicated");
      }));
      actions.push(iconBtn("trash", "Delete this section", function () {
        var at = E.doc.sections.indexOf(v.obj);
        var removed = E.doc.sections.splice(at, 1)[0];
        changed();
        show({ kind: "list" });
        F.toast("Section deleted", "Undo", function () {
          E.doc.sections.splice(at, 0, removed);
          changed();
          show({ kind: "section", obj: removed });
        });
      }, "iconbtn--danger"));
    } else if (v.kind === "page") {
      var pidx = E.doc.pages.indexOf(v.obj);
      title = "Page";
      fields = S.document.fields.pages.item.fields;
      path = ["pages", pidx];
      actions.push(iconBtn("trash", "Delete this page", function () {
        var at = E.doc.pages.indexOf(v.obj);
        var removed = E.doc.pages.splice(at, 1)[0];
        changed();
        show({ kind: "list" });
        F.toast("Page deleted", "Undo", function () {
          E.doc.pages.splice(at, 0, removed);
          changed();
          show({ kind: "page", obj: removed });
        });
      }, "iconbtn--danger"));
    }

    var wrap = el("div", { class: "ed-detail" });
    if (title !== null) {
      wrap.appendChild(el("div", { class: "ed-detail-head" }, [
        iconBtn("back", "Back to the list", function () { show({ kind: "list" }); }),
        el("h3", { class: "ed-detail-title", text: title })
      ].concat(actions.length ? [el("span", { class: "ed-detail-actions" }, actions)] : [])));
    }
    if (v.kind === "section") {
      var type = S.section_types[v.obj.type];
      wrap.appendChild(el("p", { class: "hint ed-intro", text: type.description }));
      var on = el("input", { type: "checkbox", class: "switch", checked: v.obj.enabled });
      on.addEventListener("change", function () { v.obj.enabled = on.checked; changed(); });
      wrap.appendChild(el("label", { class: "check ed-shown" }, [on, el("span", { text: "Shown on the homepage" })]));
      if (v.obj.type === "beta") wrap.appendChild(el("p", { class: "hint", text: "Visitors see this section only while a beta release is live." }));
      if (v.obj.type === "releases") wrap.appendChild(el("p", { class: "hint", text: "Visitors see this section once a release has been published." }));
    }
    wrap.appendChild(placeholderHelp());
    var form = el("div", { class: "ed-form" });
    Object.keys(fields).forEach(function (name) {
      form.appendChild(field(fields[name], path.concat([name]), 0));
    });
    wrap.appendChild(form);
    panel.appendChild(wrap);
    linkSuggestions();
  }

  function placeholderHelp() {
    var list = el("dl", { class: "ed-placeholders" });
    Object.keys(S.placeholders).forEach(function (k) {
      list.appendChild(el("dt", {}, [el("code", { text: k })]));
      list.appendChild(el("dd", { text: S.placeholders[k] }));
    });
    return el("details", { class: "ed-help" }, [
      el("summary", { text: "Text can use placeholders" }),
      el("p", { class: "hint", text: "They are filled in when the page is shown, so they stay right after a release or a change of name." }),
      list
    ]);
  }

  function linkSuggestions() {
    var dl = document.getElementById("ed-links");
    if (!dl) { dl = el("datalist", { id: "ed-links" }); document.body.appendChild(dl); }
    dl.innerHTML = "";
    var options = ["#install", "{source_url}", "{issues_url}", "{flatpakref_url}", "{beta_flatpakref_url}"]
      .concat(E.doc.sections.map(function (s) { return "/#" + s.id; }))
      .concat(E.doc.pages.map(function (p) { return "/" + p.slug; }));
    options.forEach(function (o) { dl.appendChild(el("option", { value: o })); });
  }

  /* ——— Fields ——— */
  function defaultFor(f) {
    if (f.type === "group") {
      var out = {};
      Object.keys(f.fields).forEach(function (k) { out[k] = defaultFor(f.fields[k]); });
      return out;
    }
    if (f.type === "list") return [];
    return f.default !== undefined ? clone(f.default) : "";
  }

  function wrapField(f, path, control, opts) {
    var wrap = el("div", { class: "field ed-field" + (opts && opts.inline ? " ed-field--inline" : ""), "data-path": pathKey(path) });
    var id = "f" + Math.random().toString(36).slice(2, 9);
    if (control.tagName === "INPUT" || control.tagName === "TEXTAREA" || control.tagName === "SELECT") control.id = id;
    else {
      var first = control.querySelector("input, textarea, select, button");
      if (first && !first.id) first.id = id;
    }
    var label = el("label", { class: "field-label", for: id, text: f.label });
    if (opts && opts.inline) {
      wrap.appendChild(control);
      wrap.appendChild(label);
    } else {
      wrap.appendChild(label);
      wrap.appendChild(control);
    }
    if (f.help) wrap.appendChild(el("p", { class: "hint ed-hint", text: f.help }));
    return wrap;
  }

  function field(f, path, depth) {
    var value = getAt(E.doc, path);
    var set = function (v) { setAt(E.doc, path, v); changed(); };
    switch (f.type) {
      case "text":
      case "url": {
        var input = el("input", { type: "text", value: value || "", maxlength: f.max || null, spellcheck: f.type === "url" ? "false" : null,
                                  list: f.type === "url" ? "ed-links" : null, placeholder: f.type === "url" ? "https://…" : null });
        input.addEventListener("input", function () { set(input.value); });
        return wrapField(f, path, input);
      }
      case "textarea":
      case "markdown": {
        var ta = el("textarea", { rows: f.type === "markdown" ? 5 : 3, maxlength: f.max || null, class: f.type === "markdown" ? "ed-md" : null });
        ta.value = value || "";
        var grow = function () { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 520) + "px"; };
        ta.addEventListener("input", function () { set(ta.value); grow(); });
        setTimeout(grow, 0);
        return wrapField(f, path, ta);
      }
      case "bool": {
        var cb = el("input", { type: "checkbox", class: "switch", checked: !!value });
        cb.addEventListener("change", function () { set(cb.checked); });
        return wrapField(f, path, cb, { inline: true });
      }
      case "int": {
        var num = el("input", { type: "number", value: value, min: f.min, max: f.max, step: 1 });
        num.addEventListener("input", function () {
          var n = parseInt(num.value, 10);
          if (!isNaN(n)) set(n);
        });
        return wrapField(f, path, num);
      }
      case "select": {
        var sel = el("select", {}, f.choices.map(function (c) {
          return el("option", { value: c.value, selected: c.value === value, text: c.label });
        }));
        sel.addEventListener("change", function () { set(sel.value); });
        return wrapField(f, path, sel);
      }
      case "color": return wrapField(f, path, colorControl(value, set));
      case "image": return wrapField(f, path, imageControl(value, set));
      case "icon": return wrapField(f, path, iconControl(value, set));
      case "font": return wrapField(f, path, fontControl(f, value, set));
      case "group": return groupControl(f, path, depth);
      case "list": return listControl(f, path, depth);
    }
    return el("p", { class: "hint", text: f.label + ": " + f.type });
  }

  function colorControl(value, set) {
    var picker = el("input", { type: "color", value: value || "#000000", "aria-label": "Pick a color" });
    var hex = el("input", { type: "text", value: value || "", maxlength: 7, spellcheck: "false", class: "ed-hex" });
    picker.addEventListener("input", function () { hex.value = picker.value; set(picker.value); });
    hex.addEventListener("input", function () {
      if (/^#[0-9a-fA-F]{6}$/.test(hex.value)) { picker.value = hex.value; set(hex.value.toLowerCase()); }
    });
    return el("div", { class: "ed-color" }, [hex, picker]);
  }

  function imageControl(value, set) {
    var box = el("div", { class: "ed-image" });
    function draw(v) {
      box.innerHTML = "";
      var thumb = el("div", { class: "ed-image-thumb" }, [v ? el("img", { src: v, alt: "" }) : svg(ICON.image)]);
      var url = el("input", { type: "text", value: v || "", placeholder: "An uploaded file, or https://…", spellcheck: "false", "aria-label": "Image address" });
      url.addEventListener("change", function () { set(url.value.trim()); draw(url.value.trim()); });
      box.appendChild(thumb);
      box.appendChild(el("div", { class: "ed-image-side" }, [
        el("div", { class: "ed-image-actions" }, [
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: v ? "Replace…" : "Choose…", onclick: function () {
            openPicker("image", function (item) { set(item.url); draw(item.url); });
          } }),
          v ? el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Remove", onclick: function () { set(""); draw(""); } }) : null
        ]),
        url
      ]));
    }
    draw(value);
    return box;
  }

  function iconMarkup(name) {
    var icon = S.icons[name];
    if (!icon) return "";
    return '<svg class="ico ico--' + icon.kind + '" viewBox="0 0 24 24">' + icon.svg + "</svg>";
  }
  function iconControl(value, set) {
    var box = el("div", { class: "ed-icon" });
    var current = el("button", { type: "button", class: "ed-icon-current", "aria-haspopup": "true" });
    var pop = el("div", { class: "ed-icon-pop", hidden: true, role: "listbox" });
    function label(v) {
      current.innerHTML = "";
      if (!v) current.appendChild(el("span", { class: "ed-icon-none", text: "None" }));
      else if (S.icons[v]) {
        current.appendChild(el("span", { class: "ed-icon-glyph", html: iconMarkup(v) }));
        current.appendChild(el("span", { text: S.icons[v].label }));
      } else {
        current.appendChild(el("img", { class: "ed-icon-img", src: v, alt: "" }));
        current.appendChild(el("span", { text: "Uploaded image" }));
      }
    }
    function close() { pop.hidden = true; document.removeEventListener("click", outside, true); }
    function outside(e) { if (!box.contains(e.target)) close(); }
    function choose(v) { set(v); label(v); close(); current.focus(); }
    current.addEventListener("click", function () {
      if (!pop.hidden) { close(); return; }
      pop.innerHTML = "";
      pop.appendChild(el("button", { type: "button", class: "ed-icon-opt ed-icon-opt--wide", text: "None", onclick: function () { choose(""); } }));
      Object.keys(S.icons).forEach(function (k) {
        pop.appendChild(el("button", { type: "button", class: "ed-icon-opt", title: S.icons[k].label, "aria-label": S.icons[k].label,
                                       html: iconMarkup(k), onclick: function () { choose(k); } }));
      });
      pop.appendChild(el("button", { type: "button", class: "ed-icon-opt ed-icon-opt--wide", text: "Use an image…", onclick: function () {
        close();
        openPicker("image", function (item) { choose(item.url); });
      } }));
      pop.hidden = false;
      document.addEventListener("click", outside, true);
    });
    box.appendChild(current);
    box.appendChild(pop);
    label(value);
    return box;
  }

  function fontControl(f, value, set) {
    var sel = el("select");
    function fill(fonts) {
      sel.innerHTML = "";
      sel.appendChild(el("optgroup", { label: "Built in" }, f.choices.map(function (c) {
        return el("option", { value: c.value, text: c.label, selected: c.value === value });
      })));
      if (fonts.length) {
        sel.appendChild(el("optgroup", { label: "Uploaded" }, fonts.map(function (m) {
          return el("option", { value: m.url, text: m.name, selected: m.url === value });
        })));
      }
      sel.appendChild(el("option", { value: "__upload", text: "Upload a font…" }));
    }
    fill([]);
    Library.load().then(function (items) { fill(items.filter(function (m) { return m.kind === "font"; })); }).catch(function () {});
    sel.addEventListener("change", function () {
      if (sel.value === "__upload") {
        sel.value = value;
        openPicker("font", function (item) {
          value = item.url;
          set(item.url);
          fill((Library.items || []).filter(function (m) { return m.kind === "font"; }));
        });
        return;
      }
      value = sel.value;
      set(sel.value);
    });
    return sel;
  }

  function groupControl(f, path, depth) {
    var body = el("div", { class: "ed-group-body" });
    Object.keys(f.fields).forEach(function (name) { body.appendChild(field(f.fields[name], path.concat([name]), depth + 1)); });
    var details = el("details", { class: "ed-group", "data-path": pathKey(path), open: depth === 0 && path[path.length - 1] !== "dark" ? true : null }, [
      el("summary", { class: "ed-group-title" }, [el("span", { text: f.label })]),
      f.help ? el("p", { class: "hint ed-hint", text: f.help }) : null,
      body
    ]);
    return details;
  }

  function listControl(f, path, depth) {
    var box = el("div", { class: "ed-listfield", "data-path": pathKey(path) });
    function itemTitle(item, i) {
      var t = f.title_field && item && item[f.title_field];
      return (t && String(t).replace(/[*_`#]/g, "").slice(0, 80)) || (f.item_label.charAt(0).toUpperCase() + f.item_label.slice(1) + " " + (i + 1));
    }
    function draw(openIndex) {
      var items = getAt(E.doc, path) || [];
      box.innerHTML = "";
      box.appendChild(el("p", { class: "field-label", text: f.label }));
      if (f.help) box.appendChild(el("p", { class: "hint ed-hint", text: f.help }));
      items.forEach(function (item, i) {
        var titleEl = el("span", { class: "ed-item-title", text: itemTitle(item, i) });
        // A group's fields go straight into the card, without a second fold.
        var inner = f.item.type === "group"
          ? Object.keys(f.item.fields).map(function (name) { return field(f.item.fields[name], path.concat([i, name]), depth + 1); })
          : [field(f.item, path.concat([i]), depth + 1)];
        var bodyEl = el("div", { class: "ed-item-body", "data-path": pathKey(path.concat([i])) }, inner);
        // A single field (not a group) has no inner title, so the item shows open.
        var card = el("details", { class: "ed-item", open: i === openIndex || f.item.type !== "group" ? true : null }, [
          el("summary", { class: "ed-item-head" }, [
            titleEl,
            el("span", { class: "ed-item-actions" }, [
              iconBtn("up", "Move up", function (e) { e.preventDefault(); move(i, -1); }),
              iconBtn("down", "Move down", function (e) { e.preventDefault(); move(i, 1); }),
              iconBtn("trash", "Remove this " + f.item_label, function (e) {
                e.preventDefault();
                var list = getAt(E.doc, path);
                var removed = list.splice(i, 1)[0];
                changed();
                draw(-1);
                F.toast("Removed the " + f.item_label, "Undo", function () {
                  getAt(E.doc, path).splice(i, 0, removed);
                  changed();
                  draw(i);
                });
              }, "iconbtn--danger")
            ])
          ]),
          bodyEl
        ]);
        bodyEl.addEventListener("input", function () { titleEl.textContent = itemTitle(getAt(E.doc, path)[i], i); });
        bodyEl.addEventListener("change", function () { titleEl.textContent = itemTitle(getAt(E.doc, path)[i], i); });
        box.appendChild(card);
      });
      var full = f.max && items.length >= f.max;
      box.appendChild(el("button", { type: "button", class: "btn btn--ghost btn--xs ed-add", disabled: full, onclick: function () {
        getAt(E.doc, path).push(defaultFor(f.item));
        changed();
        draw(items.length);
        var last = box.querySelectorAll(".ed-item");
        last = last[last.length - 1];
        if (last) { var input = last.querySelector("input, textarea, select"); if (input) input.focus(); }
      } }, [svg(ICON.plus), "Add " + (/^[aeiou]/.test(f.item_label) ? "an " : "a ") + f.item_label]));
    }
    function move(i, step) {
      var list = getAt(E.doc, path), j = i + step;
      if (j < 0 || j >= list.length) return;
      var tmp = list[i]; list[i] = list[j]; list[j] = tmp;
      changed();
      draw(j);
    }
    draw(-1);
    return box;
  }

  /* ——— Publishing ——— */
  publishBtn.addEventListener("click", function () {
    F.setBusy(publishBtn, true);
    flush().then(function () {
      return call("POST", "/api/v1/site/publish", {});
    }).then(function () {
      E.hasChanges = false;
      status("Published");
      F.toast("Published. Visitors see the new version now.");
    }).catch(function (err) {
      if (err.errors) showErrors(err.errors);
      F.toastError(err);
    }).finally(function () {
      F.setBusy(publishBtn, false);
      updateButtons();
    });
  });

  discardBtn.addEventListener("click", function () {
    var mine = clone(E.doc);
    clearTimeout(E.timer);
    E.dirty = false;
    call("POST", "/api/v1/site/discard", {}).then(function (data) {
      E.doc = data.document;
      E.base = data.updated.updated_at;
      E.hasChanges = false;
      updateButtons();
      status("Changes discarded");
      show({ kind: E.scope === "theme" ? "theme" : "list" }, true);
      F.toast("Back to the published version", "Undo", function () {
        E.doc = mine;
        changed();
        route();
      });
    }).catch(F.toastError);
  });

  // History: the published versions, any of which can go back into the draft.
  document.querySelector('[data-open="history-modal"]').addEventListener("click", function () {
    var list = document.getElementById("history-list");
    list.innerHTML = "";
    list.appendChild(el("li", { class: "hint", text: "Loading…" }));
    call("GET", "/api/v1/site/revisions").then(function (data) {
      list.innerHTML = "";
      if (!data.revisions.length) {
        list.appendChild(el("li", { class: "hint", text: "Nothing has been published yet." }));
        return;
      }
      data.revisions.forEach(function (r, i) {
        list.appendChild(el("li", { class: "manage-item" }, [
          el("div", { class: "manage-meta" }, [
            el("span", { class: "manage-title", text: when(r.published_at) + (i === 0 ? " · live now" : "") }),
            el("span", { class: "manage-sub", text: "By " + (r.published_by || "someone") + (r.note ? " · " + r.note : "") })
          ]),
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Restore", onclick: function () {
            call("POST", "/api/v1/site/revisions/" + r.id + "/restore", {}).then(function (d) {
              E.doc = d.document;
              E.base = d.updated.updated_at;
              E.hasChanges = d.has_unpublished_changes;
              updateButtons();
              document.getElementById("history-modal").close();
              show({ kind: E.scope === "theme" ? "theme" : "list" }, true);
              F.toast("That version is in the draft. Publish it to make it live.");
            }).catch(F.toastError);
          } })
        ]));
      });
    }).catch(function (err) {
      list.innerHTML = "";
      list.appendChild(el("li", { class: "form-error", text: err.message }));
    });
  });

  load();
})();
