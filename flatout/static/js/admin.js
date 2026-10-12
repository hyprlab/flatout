/* The repository and automation pages: Releases, Signing and addresses,
 * Installs, API and agents. No dependencies, no build step. Each section
 * guards on its page's root element. Everything goes through the JSON API,
 * the same endpoints a token or an agent uses.
 */
(function () {
  "use strict";

  var F = window.Flatout;
  if (!F) return;

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    });
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }
  function call(method, url, body) {
    var opts = { method: method, headers: { "X-CSRF": F.csrf, "Accept": "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) { var e = new Error(data.error || "Something went wrong."); e.status = resp.status; throw e; }
        return data;
      });
    }, function () { throw new Error("Can't reach the server."); });
  }
  function when(iso) {
    if (!iso) return "";
    try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); }
    catch (_) { return iso; }
  }
  function day(iso) {
    try { return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" }); }
    catch (_) { return iso; }
  }
  function size(n) {
    if (n == null) return "";
    if (n < 1024) return n + " bytes";
    if (n < 1024 * 1024) return Math.round(n / 1024) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }
  function fmt(n) { return (n || 0).toLocaleString(); }
  function showError(id, err) {
    var box = document.getElementById(id);
    box.textContent = err ? err.message : "";
    box.hidden = !err;
  }
  function copy(text) {
    if (navigator.clipboard) return navigator.clipboard.writeText(text).then(function () { F.toast("Copied"); });
    prompt("Copy this:", text);
    return Promise.resolve();
  }
  var STATUS = {
    queued: "Waiting", processing: "Publishing", live: "Live", superseded: "Earlier",
    failed: "Failed", ended: "Ended", withdrawn: "Withdrawn", pruned: "Pruned"
  };

  // Live builds of another app than the site's: the site, its install files
  // and the channel tiles leave them out, so say so and say how to fix it.
  // On the App page itself the fix is the form underneath.
  function showMismatch(repoInfo, onAppPage) {
    var box = document.getElementById("app-id-mismatch");
    if (!box) return;
    box.innerHTML = "";
    var ids = repoInfo.unmatched_app_ids || [];
    box.hidden = !ids.length;
    if (!ids.length) return;
    // Builds only the beta channel has are a separate beta app: the beta
    // app ID is the setting that fixes them, not the app ID.
    var betaIds = repoInfo.unmatched_beta_app_ids || [];
    var betaOnly = ids.every(function (id) { return betaIds.indexOf(id) !== -1; });
    var field = betaOnly ? "beta app ID" : "app ID";
    var current = betaOnly ? repoInfo.beta_app_id : repoInfo.app_id;
    box.appendChild(el("p", {}, [
      el("strong", { text: "The site doesn't show these releases. " }),
      "They are for " + ids.join(", ") + ", but the site's " + field + " is " + current + ". " +
      "The homepage, the install files and the channel tiles only show releases of the site's app."
    ]));
    box.appendChild(el("p", {}, onAppPage
      ? ["Set the " + field + " below to " + ids[0] + ", or upload a bundle for " + current + "."]
      : [el("a", { class: "link", href: "/admin/app", text: "Set the " + field + " under Repository > App" }),
         ", or upload a bundle for " + current + "."]));
  }

  /* ————— Getting started (the Overview) ————— */
  var checklistEl = document.getElementById("checklist");
  if (checklistEl) {
    var showLink = document.getElementById("checklist-show");
    var paint = function (data) {
      data.steps.forEach(function (step) {
        var li = checklistEl.querySelector('[data-step="' + step.id + '"]');
        if (!li) return;
        li.classList.toggle("is-done", step.done);
        li.querySelector("input").checked = step.done;
        var rule = step.detects.charAt(0).toLowerCase() + step.detects.slice(1);
        li.querySelector(".step-how").textContent = step.override === null ? "Ticks itself when " + rule : "Marked by hand.";
      });
      checklistEl.querySelector(".checklist-done").hidden = !data.all_done;
      checklistEl.hidden = data.hidden;
      showLink.hidden = !data.hidden;
    };
    var save = function (body) {
      return call("PATCH", "/api/v1/setup-checklist", body).then(function (data) {
        paint(data);
        return data;
      });
    };
    checklistEl.querySelectorAll("[data-step] input").forEach(function (box) {
      box.addEventListener("change", function () {
        var id = box.closest("[data-step]").getAttribute("data-step");
        var steps = {};
        steps[id] = box.checked;
        save({ steps: steps }).catch(function (err) {
          box.checked = !box.checked;   // as it was: the server refused
          F.toastError(err);
        });
      });
    });
    checklistEl.querySelector("[data-checklist-hide]").addEventListener("click", function () {
      save({ hidden: true }).then(function () {
        F.toast("Getting started is hidden", "Undo", function () { save({ hidden: false }).catch(F.toastError); });
      }).catch(F.toastError);
    });
    showLink.querySelector("[data-checklist-show]").addEventListener("click", function () {
      save({ hidden: false }).catch(F.toastError);
    });
  }

  /* ————— Uploads ————— */

  // A dropzone and the files waiting under it. They upload one after
  // another, and each becomes its own release or package, as if uploaded
  // alone. opts: input, drop, list, label (the dropzone's text), empty (that
  // text with nothing chosen), unit ("bundle"), error (the form-error id) and
  // refuse(file), which gives the reason to leave a file out, or "".
  function fileQueue(opts) {
    var q = { items: [], busy: false };
    q.draw = function () {
      opts.label.innerHTML = "";
      opts.label.appendChild(q.items.length
        ? el("span", {}, [el("strong", { text: q.items.length === 1 ? "1 " + opts.unit : q.items.length + " " + opts.unit + "s" }), " · choose or drop more"])
        : el("span", {}, [el("strong", { text: opts.empty }), " or drop them here"]));
      opts.drop.classList.toggle("has-file", q.items.length > 0);
      opts.list.innerHTML = "";
      opts.list.hidden = !q.items.length;
      q.items.forEach(function (item) {
        item.stateEl = el("span", { class: "upload-file-state", text: item.note || size(item.file.size) });
        var remove = null;
        if (!q.busy) {
          remove = el("button", { type: "button", class: "iconbtn iconbtn--sm", "aria-label": "Remove " + item.file.name, title: "Remove",
            onclick: function () { q.items.splice(q.items.indexOf(item), 1); q.draw(); } });
          remove.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>';
        }
        opts.list.appendChild(el("li", { class: "upload-file" + (item.failed ? " is-failed" : "") }, [
          el("span", { class: "upload-file-name", text: item.file.name, title: item.file.name }), item.stateEl, remove]));
      });
    };
    q.add = function (files) {
      if (q.busy) return;
      showError(opts.error, null);
      var skipped = [];
      Array.prototype.forEach.call(files, function (file) {
        var why = opts.refuse(file);
        if (why) { skipped.push(file.name + " (" + why + ")"); return; }
        var known = q.items.some(function (i) { return i.file.name === file.name && i.file.size === file.size; });
        if (!known) q.items.push({ file: file, note: "", failed: false });
      });
      if (skipped.length) showError(opts.error, new Error("Left out: " + skipped.join(", ") + "."));
      q.draw();
    };
    opts.input.addEventListener("change", function () { q.add(opts.input.files); opts.input.value = ""; });
    ["dragenter", "dragover"].forEach(function (t) {
      opts.drop.addEventListener(t, function (e) { e.preventDefault(); opts.drop.classList.add("is-dropping"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      opts.drop.addEventListener(t, function () { opts.drop.classList.remove("is-dropping"); });
    });
    opts.drop.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer.files.length) q.add(e.dataTransfer.files);
    });
    return q;
  }

  // Every file goes up in pieces of the size the server asks for (under
  // 100 MB), so a proxy that caps request bodies, Cloudflare's among them,
  // lets it through. Each piece is retried on its own. Resolves with the
  // finished upload's id, for the release or package to be made from.
  // XMLHttpRequest, for the progress bar.
  function sendPiece(up, file, offset, progress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.upload.addEventListener("progress", function (ev) { if (ev.lengthComputable) progress(offset + ev.loaded); });
      xhr.addEventListener("load", function () {
        var data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) {}
        if (xhr.status === 200 || (xhr.status === 409 && typeof data.received === "number")) { resolve(data.received); return; }
        var err = new Error(data.error || (xhr.status === 413 ? "The piece is larger than a proxy in front of the server accepts." : "The upload failed."));
        err.retry = xhr.status >= 500;   // the server or a proxy faltered; a client error won't fix itself
        reject(err);
      });
      xhr.addEventListener("error", function () { var e = new Error("The upload was cut off."); e.retry = true; reject(e); });
      xhr.open("PUT", "/api/v1/uploads/" + up.id + "?offset=" + offset);
      xhr.setRequestHeader("X-CSRF", F.csrf);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.send(file.slice(offset, Math.min(offset + up.chunk_size, file.size)));
    });
  }
  function sendFile(file, progress) {
    if (!file.size) return Promise.reject(new Error("The file is empty."));
    return call("POST", "/api/v1/uploads", { size: file.size }).then(function (up) {
      var from = function (offset) {
        if (offset >= file.size) return Promise.resolve();
        var tries = 0;
        var attempt = function () {
          return sendPiece(up, file, offset, progress).catch(function (err) {
            tries += 1;
            if (!err.retry || tries >= 5) throw err;
            return new Promise(function (wait) { setTimeout(wait, 1000 * Math.pow(2, tries - 1)); }).then(attempt);
          });
        };
        return attempt().then(from);
      };
      return from(0).then(function () { return up.id; }, function (err) {
        // A failed upload is started afresh next time; free its disk now.
        call("DELETE", "/api/v1/uploads/" + up.id).catch(function () {});
        throw err;
      });
    });
  }

  // Send everything in a queue, one file after another, with one progress
  // bar for the lot. make(item, uploadId) turns a finished upload into a
  // release or a package. Resolves with how many went and how many didn't;
  // those that didn't stay in the queue, saying why.
  function sendQueue(q, bar, make) {
    var items = q.items.slice();
    var total = items.reduce(function (n, i) { return n + i.file.size; }, 0) || 1;
    var before = 0, sent = 0, failed = 0;
    q.busy = true;
    items.forEach(function (item) { item.failed = false; item.note = "Waiting"; });
    q.draw();
    bar.hidden = false;
    bar.value = 0;
    var next = function (i) {
      if (i >= items.length) return Promise.resolve();
      var item = items[i];
      item.stateEl.textContent = "0%";
      return sendFile(item.file, function (loaded) {
        item.stateEl.textContent = Math.round(loaded / item.file.size * 100) + "%";
        bar.value = Math.round((before + loaded) / total * 100);
      }).then(function (uploadId) { return make(item, uploadId); }).then(function () {
        sent++;
        q.items.splice(q.items.indexOf(item), 1);
      }, function (err) {
        failed++;
        item.failed = true;
        item.note = err.message;
      }).then(function () {
        before += item.file.size;
        q.draw();
        return next(i + 1);
      });
    };
    return next(0).then(function () {
      q.busy = false;
      q.items.forEach(function (i) { if (!i.failed) i.note = ""; });
      q.draw();
      bar.hidden = true;
      return { sent: sent, failed: failed };
    });
  }

  /* ————— Releases ————— */

  // One page for every format. A release is a version on a channel: every
  // Flatpak build, package and file uploaded for it, withdrawn together or
  // one by one. The API's /versions groups them; bundles still go to
  // /releases and everything else to /packages.
  var releasesPage = document.getElementById("releases-page");
  if (releasesPage) {
    var FORMAT = { flatpak: "Flatpak", rpm: "RPM", deb: "Debian", appimage: "AppImage", file: "File" };
    var rows = document.getElementById("release-rows");
    var tiles = document.getElementById("channel-tiles");
    var pollTimer = null;
    var isBundle = function (name) { return /\.flatpak$/i.test(name); };
    // Versions as people read them: 1.4.0~beta.1 and 1.4.0-beta.1 are one
    // (releases.version_key on the server).
    var sameVersion = function (a, b) {
      var key = function (v) { return (String(v || "").toLowerCase().match(/\d+|[a-z]+/g) || []).map(function (t) { return /^\d/.test(t) ? String(+t) : t; }).join("."); };
      return key(a) === key(b);
    };
    var queue = fileQueue({
      input: document.getElementById("upload-file"), drop: document.getElementById("upload-drop"),
      list: document.getElementById("upload-list"), label: document.getElementById("upload-name"),
      empty: "Choose files", unit: "file", error: "upload-error",
      refuse: function () { return ""; }
    });

    document.getElementById("upload-form").addEventListener("submit", function (e) {
      e.preventDefault();
      if (queue.busy) return;
      showError("upload-error", null);
      var btn = document.getElementById("upload-btn");
      var fields = {
        channel: document.querySelector('input[name="channel"]:checked').value,
        version: document.getElementById("upload-version").value.trim(),
        notes: document.getElementById("upload-notes").value,
        name: document.getElementById("upload-filename").value.trim()
      };
      var url = document.getElementById("upload-url").value.trim();
      var clearFields = function () {
        ["upload-version", "upload-notes", "upload-filename", "upload-url"].forEach(function (id) { document.getElementById(id).value = ""; });
      };
      var named = queue.items.filter(function (i) { return !isBundle(i.file.name); }).length;
      if (!queue.items.length && !url) { showError("upload-error", new Error("Choose files, or give the address to fetch one from.")); return; }
      if (queue.items.length && url) { showError("upload-error", new Error("Upload the chosen files or fetch one from the address, not both at once.")); return; }
      if (fields.name && named > 1) { showError("upload-error", new Error("A download name is for one file at a time.")); return; }
      // What a file is decides where it goes: a bundle is a Flatpak build,
      // anything else a package or a download.
      var create = function (file, extra) {
        var body = { channel: fields.channel, version: fields.version, notes: fields.notes };
        Object.keys(extra).forEach(function (k) { body[k] = extra[k]; });
        if (isBundle(file)) return call("POST", "/api/v1/releases", body);
        body.name = fields.name;
        return call("POST", "/api/v1/packages", body);
      };
      F.setBusy(btn, true);
      if (!queue.items.length) {
        create(url.split(/[?#]/)[0], { url: url })
          .then(function () { clearFields(); F.toast("Fetching it. It's published once it's in."); refresh(); })
          .catch(function (err) { showError("upload-error", err); })
          .finally(function () { F.setBusy(btn, false); });
        return;
      }
      sendQueue(queue, document.getElementById("upload-progress"), function (item, uploadId) {
        return create(item.file.name, isBundle(item.file.name) ? { upload: uploadId } : { upload: uploadId, filename: item.file.name });
      }).then(function (r) {
        F.setBusy(btn, false);
        if (r.sent) refresh();
        if (!r.failed) {
          clearFields();
          F.toast(r.sent === 1 ? "Uploaded. Publishing it now." : "Uploaded " + r.sent + " files. Publishing them now.");
        } else {
          showError("upload-error", new Error((r.failed === 1 ? "One file" : r.failed + " files") + " didn't upload" +
            (r.sent ? " (" + r.sent + " did)" : "") + ". The list says why; upload again to retry them."));
        }
      });
    });

    // A Flatpak build or a package, in one shape: fmt, what to call it,
    // its architecture, and where its API lives.
    var asItem = function (r, kind) {
      var fmt = kind === "release" ? "flatpak" : r.format;
      var name = kind === "release" ? "Flatpak" :
        r.format === "rpm" || r.format === "deb" ? (r.name ? r.name + " (" + FORMAT[r.format] + ")" : FORMAT[r.format]) :
        r.name || FORMAT[r.format] || "File";
      return { kind: kind, fmt: fmt, name: name, arch: r.arch, r: r,
               url: "/api/v1/" + (kind === "release" ? "releases/" : "packages/") + r.id,
               size: kind === "release" ? r.bundle.size : r.file.size };
    };
    var itemsOf = function (v) {
      return v.releases.map(function (r) { return asItem(r, "release"); })
        .concat(v.packages.map(function (p) { return asItem(p, "package"); }));
    };
    var published = function (it) { return it.r.status === "live" || it.r.status === "superseded"; };

    // What withdrawing does, said for the formats it touches.
    var consequences = function (items) {
      var live = items.filter(function (it) { return it.r.status === "live"; });
      var has = function (f) { return live.some(function (it) { return it.fmt === f; }); };
      var out = [];
      if (has("flatpak")) out.push("Flatpak installs go back to the build before it when they next update; a channel with no build before it loses that architecture.");
      if (has("rpm") || has("deb")) out.push("Installed packages keep this version until a newer one is published, since dnf and apt don't go back on their own; new installs get the one before.");
      if (has("appimage")) out.push("AppImages go back to the version before when they next check for updates.");
      if (has("file")) out.push("Its downloads stop being offered.");
      if (!live.length) out.push("It isn't live, so nothing installed changes.");
      return out.join(" ");
    };

    var withdrawItem = function (it, done) {
      var what = it.name + " " + (it.r.version || "") + (it.arch ? " (" + it.arch + ")" : "");
      if (!confirm("Withdraw " + what + " from " + it.r.channel + "? " + consequences([it]) + " The file is deleted, and it can't be brought back.")) return;
      call("POST", it.url + "/withdraw", {}).then(function () {
        F.toast("Withdrawing " + what);
        refresh();
        if (done) done();
      }).catch(F.toastError);
    };
    var withdrawVersion = function (v, done) {
      var items = itemsOf(v).filter(published);
      if (!confirm("Withdraw " + v.version + " from " + v.channel + ", in every format (" + items.length +
                   (items.length === 1 ? " file" : " files") + ")? " + consequences(items) +
                   " The files are deleted, and they can't be brought back.")) return;
      call("POST", "/api/v1/versions/withdraw", { channel: v.channel, version: v.version }).then(function () {
        F.toast("Withdrawing " + v.version + " from " + v.channel);
        refresh();
        if (done) done();
      }).catch(F.toastError);
    };
    // The newest earlier build of each architecture, for a version whose
    // Flatpak builds are no longer live.
    var bringBackable = function (v) {
      if (v.releases.some(function (r) { return r.status === "live"; })) return [];
      var seen = {};
      return v.releases.filter(function (r) {
        if (r.status !== "superseded" || !r.commit || seen[r.arch]) return false;
        seen[r.arch] = true;
        return true;
      });
    };
    var bringBack = function (builds, label, channel) {
      Promise.all(builds.map(function (r) { return call("POST", "/api/v1/releases/" + r.id + "/rollback", {}); }))
        .then(function () { F.toast("Bringing back " + label + " on " + channel); refresh(); })
        .catch(function (err) { F.toastError(err); refresh(); });
    };

    var tile = function (label, channel, repoInfo, actions) {
      var flat = repoInfo[channel], p = repoInfo.packages;
      var lines = [];
      if (flat) lines.push({ name: "Flatpak", version: flat.version, arches: flat.arches, at: flat.published_at });
      [["rpm", "RPM"], ["deb", "Debian"]].forEach(function (f) {
        var s = p[f[0]][channel];
        if (s) lines.push({ name: f[1], version: s.version, arches: s.arches, at: s.published_at });
      });
      [["appimage", "AppImage"], ["files", "Files"]].forEach(function (f) {
        var list = p[f[0]][channel];
        if (!list.length) return;
        var newest = list.reduce(function (a, b) { return a.published_at > b.published_at ? a : b; });
        lines.push({ name: f[1], version: newest.version, at: newest.published_at,
          arches: list.map(function (x) { return x.arch || "any"; }).filter(function (a, i, all) { return all.indexOf(a) === i; }) });
      });
      // The site's version is the Flatpak one when there is one.
      var top = flat ? lines[0] : lines.reduce(function (a, b) { return !a || b.at > a.at ? b : a; }, null);
      var behind = flat && flat.behind && flat.behind.length ? flat.behind : [];
      return el("section", { class: "panel tile" }, [
        el("p", { class: "tile-label", text: label }),
        el("p", { class: "tile-big", text: top ? top.version : "None" }),
        top ? null : el("p", { class: "hint", text: channel === "beta" ? "Upload to the beta channel to start one." : "Upload a release to publish the first version." }),
        lines.length ? el("ul", { class: "tile-formats" }, lines.map(function (l) {
          return el("li", {}, [el("span", { class: "tile-format", text: l.name }),
            " " + l.arches.join(", ") + (sameVersion(l.version, top.version) ? "" : " · still " + l.version)]);
        })) : null,
        top ? el("p", { class: "hint", text: "Published " + when(top.at) }) : null,
        behind.length ? el("p", { class: "hint tile-warn", text: behind.map(function (a) { return a + " is still on " + flat.builds[a].version; }).join("; ") + ". Upload its " + flat.version + " build to match." }) : null,
        actions && actions.length ? el("div", { class: "tile-actions" }, actions) : null
      ]);
    };

    var promote = function (repoInfo) {
      var p = repoInfo.packages;
      var urls = [];
      if (repoInfo.beta) urls.push("/api/v1/releases/promote");
      if (p.rpm.beta || p.deb.beta || p.appimage.beta.length || p.files.beta.length) urls.push("/api/v1/packages/promote");
      // One after the other. Each kind promotes on its own; one with
      // nothing new for stable says so, which only matters when none had.
      var errors = [];
      urls.reduce(function (chain, url) {
        return chain.then(function () {
          return call("POST", url, { from: "beta", to: "stable" }).catch(function (err) { errors.push(err); });
        });
      }, Promise.resolve()).then(function () {
        if (errors.length === urls.length) F.toastError(errors[0]);
        else F.toast("Promoting the beta to stable");
        refresh();
      });
    };

    var statusChip = function (status) {
      return el("span", { class: "status status--" + status, text: STATUS[status] || status });
    };

    var versionRow = function (v) {
      var items = itemsOf(v);
      var newest = items.reduce(function (a, b) { return (b.r.published_at || b.r.created_at) > (a.r.published_at || a.r.created_at) ? b : a; });
      // Flatpak x86_64, aarch64 · RPM x86_64 · Debian amd64
      var formats = [];
      items.forEach(function (it) {
        var f = formats.filter(function (x) { return x.fmt === it.fmt; })[0];
        if (!f) formats.push(f = { fmt: it.fmt, arches: [] });
        if (it.arch && f.arches.indexOf(it.arch) === -1) f.arches.push(it.arch);
      });
      var order = Object.keys(FORMAT);
      formats.sort(function (a, b) { return order.indexOf(a.fmt) - order.indexOf(b.fmt); });
      var what = newest.r.origin === "promote" ? "Promoted" : newest.r.origin === "rollback" ? "Brought back" : "Uploaded";
      var actions = [];
      var back = bringBackable(v);
      if (back.length) {
        actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Bring back", title: "Make this version's Flatpak builds the live ones again",
          onclick: function () { bringBack(back, v.version, v.channel); } }));
      }
      if (v.withdrawable) {
        actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Withdraw", title: "Take this version out in every format and delete its files",
          onclick: function () { withdrawVersion(v); } }));
      }
      actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: items.length === 1 ? "Details" : "Files",
        onclick: function () { if (items.length === 1) openItem(items[0]); else openVersion(v); } }));
      var errors = items.filter(function (it) { return it.r.error; });
      return el("li", { class: "release-row is-" + v.status }, [
        el("div", { class: "release-main" }, [
          el("span", { class: "release-version", text: v.version || (items[0].kind === "release" ? "Reading the bundle…" : "Reading the file…") }),
          el("span", { class: "chip chip--muted", text: v.channel }),
          statusChip(v.status)
        ]),
        el("div", { class: "release-sub" }, [
          formats.map(function (f) { return FORMAT[f.fmt] + (f.arches.length ? " " + f.arches.join(", ") : ""); }).join(" · ") + " · " +
          (newest.r.created_by ? what + " by " + newest.r.created_by + " · " : "") + when(v.published_at)
        ]),
        errors.length ? el("p", { class: "form-error", text: errors.map(function (it) { return (items.length > 1 ? it.name + (it.arch ? " " + it.arch : "") + ": " : "") + it.r.error; }).join(" ") }) : null,
        el("div", { class: "release-actions" }, actions)
      ]);
    };

    // The dialog shows a version's files, or one file; from a version, a
    // file's details lead back to the list.
    var modal = document.getElementById("release-modal");
    var modalBody = document.getElementById("release-modal-body");
    var modalTitle = document.getElementById("release-modal-title");
    var openVersion = function (v) {
      modalTitle.textContent = (v.version || "Release") + " · " + v.channel;
      modalBody.innerHTML = "";
      var items = itemsOf(v);
      var list = el("ul", { class: "release-rows" });
      items.forEach(function (it) {
        var actions = [];
        if (it.kind === "release" && it.r.status === "superseded" && it.r.commit) {
          actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Bring back", title: "Make this build the live one again",
            onclick: function () { bringBack([it.r], it.r.version, it.r.channel); modal.close(); } }));
        }
        if (published(it)) {
          actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Withdraw", title: "Take it out and delete it",
            onclick: function () { withdrawItem(it, function () { modal.close(); }); } }));
        }
        actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Details", onclick: function () { openItem(it, v); } }));
        list.appendChild(el("li", { class: "release-row is-" + it.r.status }, [
          el("div", { class: "release-main" }, [
            el("span", { class: "release-file", text: it.name }),
            it.arch ? el("span", { class: "chip chip--muted", text: it.arch }) : null,
            statusChip(it.r.status)
          ]),
          el("div", { class: "release-sub" }, [
            when(it.r.published_at || it.r.created_at) + (it.size ? " · " + size(it.size) : "") +
            (it.r.evr && it.r.evr !== it.r.version ? " · " + it.r.evr : "")
          ]),
          it.r.error ? el("p", { class: "form-error", text: it.r.error }) : null,
          el("div", { class: "release-actions" }, actions)
        ]));
      });
      modalBody.appendChild(list);
      if (v.withdrawable) {
        modalBody.appendChild(el("div", {}, [el("button", { type: "button", class: "btn btn--ghost", text: "Withdraw " + (v.version || "this version") + " in every format",
          onclick: function () { withdrawVersion(v, function () { modal.close(); }); } })]));
      }
      if (!modal.open) modal.showModal();
    };

    var openItem = function (it, fromVersion) {
      modalBody.innerHTML = "";
      modalBody.appendChild(el("p", { class: "hint", text: "Loading…" }));
      if (!modal.open) modal.showModal();
      call("GET", it.url).then(function (data) {
        var r = data.release || data.package;
        modalTitle.textContent = it.name + " " + (r.version || "") + " · " + r.channel + (r.arch ? " · " + r.arch : "");
        modalBody.innerHTML = "";
        if (fromVersion) {
          modalBody.appendChild(el("div", {}, [el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "All files of " + (fromVersion.version || "this version"),
            onclick: function () { openVersion(fromVersion); } })]));
        }
        var facts = el("dl", { class: "addresses" });
        var pairs = it.kind === "release"
          ? [["App", r.app_id], ["Status", STATUS[r.status] || r.status], ["Commit", r.commit], ["Runtime", r.runtime],
             ["Bundle SHA-256", r.bundle.sha256], ["Published", when(r.published_at)]]
          : [["Type", FORMAT[r.format]], ["Status", STATUS[r.status] || r.status], ["Version", r.evr],
             ["Architecture", r.arch], ["Summary", r.summary], ["SHA-256", r.file.sha256],
             ["Download", r.file.url], ["Updates from", r.file.zsync_url], ["Published", when(r.published_at)]];
        pairs.forEach(function (p) {
          if (!p[1]) return;
          facts.appendChild(el("dt", { text: p[0] }));
          facts.appendChild(el("dd", {}, [el("code", { text: p[1] })]));
        });
        modalBody.appendChild(facts);
        var notes = el("textarea", { rows: 6, class: "field-input" });
        notes.value = r.notes || "";
        var version = el("input", { value: r.version || "", maxlength: it.kind === "release" ? 60 : 120 });
        modalBody.appendChild(el("label", { class: "field" }, [el("span", { class: "field-label", text: "Version shown on the site" }), version]));
        modalBody.appendChild(el("label", { class: "field" }, [el("span", { class: "field-label", text: "Release notes (Markdown)" }), notes]));
        modalBody.appendChild(el("div", {}, [el("button", { type: "button", class: "btn btn--primary", text: "Save", onclick: function () {
          call("PATCH", it.url, { notes: notes.value, version: version.value }).then(function () {
            F.toast("Saved");
            refresh();
          }).catch(F.toastError);
        } })]));
        if (r.job && r.job.log) {
          modalBody.appendChild(el("details", { class: "term-like" }, [
            el("summary", { text: "What the job did" }),
            el("pre", { class: "joblog", text: r.job.log })
          ]));
        }
      }).catch(function (err) { modalBody.innerHTML = ""; modalBody.appendChild(el("p", { class: "form-error", text: err.message })); });
    };

    // Each command with its own Copy button: what people run once to add a
    // repository, after which their system's updates bring each version.
    var commandBlock = function (title, text) {
      return el("div", { class: "pkg-command" }, [
        el("p", { class: "setting-label", text: title }),
        el("div", { class: "pkg-command-box" }, [
          el("pre", { text: text }),
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Copy", onclick: function () { copy(text); } })
        ])
      ]);
    };

    var drawRepos = function (repoInfo) {
      var p = repoInfo.packages;
      var box = document.getElementById("pkg-commands");
      var dl = document.getElementById("pkg-addresses");
      box.innerHTML = "";
      dl.innerHTML = "";
      var remote = repoInfo.remote_name;
      [["stable", ""], ["beta", " (beta)"]].forEach(function (c) {
        var rpm = p.rpm[c[0]], deb = p.deb[c[0]];
        var beta = c[0] === "beta";
        if (rpm) {
          box.appendChild(commandBlock("Fedora and other dnf systems" + c[1],
            "sudo curl -fsSLo /etc/yum.repos.d/" + remote + (beta ? "-beta" : "") + ".repo " + (beta ? p.rpm.beta_repo_file_url : p.rpm.repo_file_url) +
            "\nsudo dnf install " + rpm.name));
        }
        if (deb) {
          box.appendChild(commandBlock("Debian, Ubuntu and other apt systems" + c[1],
            "sudo curl -fsSLo /etc/apt/sources.list.d/" + remote + (beta ? "-beta" : "") + ".sources " + (beta ? p.deb.beta_sources_url : p.deb.sources_url) +
            "\nsudo apt update && sudo apt install " + deb.name));
        }
      });
      var pairs = [];
      if (p.rpm.stable || p.rpm.beta) pairs.push(["dnf repository", p.rpm.stable ? p.rpm.repo_url : p.rpm.beta_repo_url], ["Its key", p.rpm.key_url]);
      if (p.deb.stable || p.deb.beta) pairs.push(["apt archive", p.deb.repo_url], ["Its key", p.deb.key_url]);
      // What each AppImage carries as its update information.
      [["stable", ""], ["beta", ", beta"]].forEach(function (c) {
        p.appimage[c[0]].forEach(function (f) {
          if (f.zsync_url) pairs.push(["Updates for " + f.name + c[1], f.zsync_url]);
        });
      });
      pairs.forEach(function (pair) {
        dl.appendChild(el("dt", { text: pair[0] }));
        dl.appendChild(el("dd", {}, [el("code", { text: pair[1] }), " ",
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Copy", onclick: function () { copy(pair[1]); } })]));
      });
      document.getElementById("pkg-repos").hidden = !box.childNodes.length && !pairs.length;
    };

    // The history's filters: channel, status and format, and a version
    // search. Options nothing has are left out; the address keeps the choice
    // over a reload. How many releases show is beside the title.
    var FILTERS = {
      channel: [["", "All"], ["stable", "Stable"], ["beta", "Beta"]],
      status: [["", "All"], ["publishing", "Publishing"], ["live", "Live"], ["earlier", "Earlier"],
               ["withdrawn", "Withdrawn"], ["failed", "Failed"]],
      format: [["", "All"], ["flatpak", "Flatpak"], ["rpm", "RPM"], ["deb", "Debian"], ["appimage", "AppImage"], ["file", "File"]]
    };
    var STATUS_GROUP = { queued: "publishing", processing: "publishing", live: "live", superseded: "earlier",
                         ended: "earlier", pruned: "earlier", withdrawn: "withdrawn", failed: "failed" };
    var query = new URLSearchParams(location.search);
    var filters = { channel: query.get("channel") || "", status: query.get("status") || "", format: query.get("format") || "",
                    q: query.get("q") || "" };
    var versions = [];
    var HISTORY_LIMIT = 100;
    var matches = function (v, f) {
      if (f.channel && v.channel !== f.channel) return false;
      if (f.status && STATUS_GROUP[v.status] !== f.status) return false;
      if (f.format && !itemsOf(v).some(function (it) { return it.fmt === f.format; })) return false;
      if (f.q && (v.version || "").toLowerCase().indexOf(f.q.toLowerCase()) === -1) return false;
      return true;
    };
    var keepFilters = function () {
      var q = new URLSearchParams();
      Object.keys(filters).forEach(function (k) { if (filters[k]) q.set(k, filters[k]); });
      history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
    };
    var searchBox = document.getElementById("release-search");
    searchBox.value = filters.q;
    searchBox.addEventListener("input", function () { filters.q = searchBox.value.trim(); keepFilters(); drawHistory(); });

    var drawFilters = function () {
      document.getElementById("release-filters").hidden = !versions.length;
      Object.keys(FILTERS).forEach(function (name) {
        var seg = document.querySelector('#release-filters [data-filter="' + name + '"]');
        var has = function (value) {
          var only = { channel: "", status: "", format: "", q: "" };
          only[name] = value;
          return versions.some(function (v) { return matches(v, only); });
        };
        var options = FILTERS[name].filter(function (o) { return !o[0] || o[0] === filters[name] || has(o[0]); });
        seg.innerHTML = "";
        seg.hidden = options.length < 3 && !filters[name];   // "All" and one option filter nothing
        options.forEach(function (o) {
          seg.appendChild(el("label", {}, [
            el("input", { type: "radio", name: "filter-" + name, value: o[0], checked: filters[name] === o[0], onchange: function () {
              filters[name] = o[0];
              keepFilters();
              drawHistory();
            } }),
            el("span", { text: o[1] })
          ]));
        });
      });
    };

    var drawHistory = function () {
      drawFilters();
      rows.innerHTML = "";
      var count = document.getElementById("history-count");
      count.textContent = "";
      if (!versions.length) { rows.appendChild(el("li", { class: "hint", text: "Nothing uploaded yet." })); return; }
      var shown = versions.filter(function (v) { return matches(v, filters); });
      // The page loads the newest HISTORY_LIMIT; at the limit there may be more.
      var all = versions.length >= HISTORY_LIMIT ? "the newest " + fmt(versions.length) : fmt(versions.length);
      var filtered = filters.channel || filters.status || filters.format || filters.q;
      count.textContent = filtered ? fmt(shown.length) + " of " + all + " releases"
        : versions.length >= HISTORY_LIMIT ? "The newest " + fmt(versions.length) + " releases"
        : fmt(versions.length) + (versions.length === 1 ? " release" : " releases");
      shown.forEach(function (v) { rows.appendChild(versionRow(v)); });
      if (!shown.length) {
        rows.appendChild(el("li", { class: "hint release-empty" }, ["No releases match these filters. ",
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Show every release", onclick: function () {
            filters = { channel: "", status: "", format: "", q: "" };
            searchBox.value = "";
            keepFilters();
            drawHistory();
          } })]));
      }
    };

    var drawBlocker = function (repoInfo) {
      var blocker = document.getElementById("releases-blocker");
      var p = repoInfo.packages;
      blocker.innerHTML = "";
      var missing = Object.keys(repoInfo.tools).filter(function (k) { return !repoInfo.tools[k]; });
      var cant = [];
      if (missing.length) cant.push("Flatpak bundles (" + missing.join(", ") + ")");
      if (!p.tools.rpm) cant.push("RPMs (rpm, rpmsign and createrepo_c)");
      if (!p.tools.deb) cant.push("Debian packages (dpkg-deb)");
      if (!p.tools.appimage) cant.push("AppImages (zsyncmake)");
      if (cant.length) {
        blocker.appendChild(el("p", { text: "This server can't publish " + cant.join(" or ") + ": the tools aren't installed where Flatout runs. Run Flatout from its Docker image." }));
      } else if (!repoInfo.signing_key) {
        blocker.appendChild(el("p", {}, ["The repository needs a signing key before the first release: bundles and packages are signed with it. ",
          el("a", { class: "link", href: "/admin/repository", text: "Create one under Signing and addresses." })]));
      }
      blocker.hidden = !blocker.childNodes.length;
    };

    var refresh = function () {
      clearTimeout(pollTimer);
      Promise.all([call("GET", "/api/v1/repo"), call("GET", "/api/v1/versions?limit=" + HISTORY_LIMIT)]).then(function (res) {
        var repoInfo = res[0], list = res[1].versions, p = repoInfo.packages;
        drawBlocker(repoInfo);
        showMismatch(repoInfo);
        document.getElementById("pkg-kept").value = repoInfo.settings.packages_kept;

        tiles.innerHTML = "";
        var betaActions = [];
        var betaLive = repoInfo.beta || p.rpm.beta || p.deb.beta || p.appimage.beta.length || p.files.beta.length;
        if (betaLive) {
          betaActions.push(el("button", { type: "button", class: "btn btn--primary btn--xs", text: "Promote to stable",
            title: "Copy the beta's newest build of every format to stable, as it is", onclick: function () { promote(repoInfo); } }));
        }
        if (repoInfo.beta) {
          betaActions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "End the beta", onclick: function () {
            var msg = prompt("Installed Flatpak betas will be told no more updates are coming. The message they see:",
                             "This beta has ended. Install the stable version instead.");
            if (msg === null) return;
            call("POST", "/api/v1/channels/beta/end", { message: msg }).then(function () {
              F.toast("Ending the beta");
              refresh();
            }).catch(F.toastError);
          } }));
        }
        tiles.appendChild(tile("Stable", "stable", repoInfo));
        tiles.appendChild(tile("Beta", "beta", repoInfo, betaActions));
        drawRepos(repoInfo);

        versions = list;
        drawHistory();
        var busy = res[1].busy || list.some(function (v) { return v.status === "queued" || v.status === "processing"; });
        document.getElementById("jobs-state").textContent = busy ? "Publishing…" : "";
        if (busy) pollTimer = setTimeout(refresh, 2000);
      }).catch(F.toastError);
    };

    document.getElementById("pkg-kept-form").addEventListener("submit", function (e) {
      e.preventDefault();
      showError("pkg-kept-error", null);
      call("PATCH", "/api/v1/repo/settings", { packages_kept: parseInt(document.getElementById("pkg-kept").value, 10) })
        .then(function () { F.toast("Saved"); })
        .catch(function (err) { showError("pkg-kept-error", err); });
    });
    refresh();
  }

  /* ————— App ————— */
  var appPage = document.getElementById("app-page");
  if (appPage) {
    var fill = function (info) {
      document.getElementById("app-id").value = info.settings.app_id;
      document.getElementById("app-id").placeholder = info.settings.app_id ? "" : (info.app_id || "org.example.App");
      document.getElementById("beta-app-id").value = info.settings.beta_app_id;
      document.getElementById("beta-app-id").placeholder = info.settings.beta_app_id ? "" : (info.app_id || "org.example.App");
      document.getElementById("remote-name").value = info.settings.remote_name;
      document.getElementById("remote-name").placeholder = info.settings.remote_name ? "" : info.remote_name;
      document.getElementById("runtime-repo").value = info.settings.runtime_repo;
      document.getElementById("prune-depth").value = info.settings.prune_depth;
      showMismatch(info, true);
      // The app IDs uploaded so far, one click from being the site's.
      var box = document.getElementById("app-ids");
      box.innerHTML = "";
      var others = info.release_app_ids.filter(function (id) {
        return id !== info.settings.app_id && id !== info.settings.beta_app_id;
      });
      box.hidden = !others.length;
      if (!others.length) return;
      box.appendChild(el("span", { class: "hint", text: "Uploaded:" }));
      others.forEach(function (id) {
        box.appendChild(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Use " + id, onclick: function () {
          document.getElementById("app-id").value = id;
          document.getElementById("app-form").requestSubmit();
        } }));
      });
    };
    call("GET", "/api/v1/repo").then(fill).catch(F.toastError);
    document.getElementById("app-form").addEventListener("submit", function (e) {
      e.preventDefault();
      showError("app-error", null);
      call("PATCH", "/api/v1/repo/settings", {
        app_id: document.getElementById("app-id").value.trim(),
        beta_app_id: document.getElementById("beta-app-id").value.trim(),
        remote_name: document.getElementById("remote-name").value.trim(),
        runtime_repo: document.getElementById("runtime-repo").value.trim(),
        prune_depth: parseInt(document.getElementById("prune-depth").value, 10)
      }).then(function (info) {
        fill(info);
        F.toast("Saved");
      }).catch(function (err) { showError("app-error", err); });
    });
  }

  /* ————— Signing and addresses ————— */
  var repoPage = document.getElementById("repository-page");
  if (repoPage) {
    var keyPanel = document.getElementById("key-panel");
    var repoPoll = null;

    var renderKey = function (info) {
      keyPanel.innerHTML = "";
      if (info.signing_key) {
        var k = info.signing_key;
        var dl = el("dl", { class: "addresses" });
        [["Fingerprint", k.fingerprint], ["Name", k.user_id], ["Type", k.algorithm]].forEach(function (p) {
          if (!p[1]) return;
          dl.appendChild(el("dt", { text: p[0] }));
          dl.appendChild(el("dd", {}, [el("code", { text: p[1] })]));
        });
        keyPanel.appendChild(dl);
        keyPanel.appendChild(el("div", { class: "row-actions" }, [
          el("a", { class: "btn btn--ghost btn--xs", href: info.urls.flatpakrepo_url.replace(/\.flatpakrepo$/, ".gpg"), text: "Download the public key" }),
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Download a backup of the secret key", onclick: function () {
            call("GET", "/api/v1/repo/key/secret").then(function (d) {
              var blob = new Blob([d.armored], { type: "application/pgp-keys" });
              var a = el("a", { href: URL.createObjectURL(blob), download: "flatout-signing-key-" + d.fingerprint.slice(-8) + ".asc" });
              document.body.appendChild(a);
              a.click();
              a.remove();
              F.toast("Keep it somewhere safe, away from this server");
            }).catch(F.toastError);
          } })
        ]));
        return;
      }
      var name = el("input", { maxlength: 120, placeholder: "Your app's name" });
      var email = el("input", { type: "email", maxlength: 200, placeholder: "you@example.org (optional)" });
      var armored = el("textarea", { rows: 5, placeholder: "-----BEGIN PGP PRIVATE KEY BLOCK-----" });
      keyPanel.appendChild(el("div", { class: "key-choices" }, [
        el("div", { class: "key-choice" }, [
          el("p", { class: "setting-label", text: "Create a new key" }),
          el("label", { class: "field" }, [el("span", { class: "field-label", text: "Name on the key" }), name]),
          el("label", { class: "field" }, [el("span", { class: "field-label", text: "Email on the key" }), email]),
          el("button", { type: "button", class: "btn btn--primary", text: "Create the key", onclick: function () {
            call("POST", "/api/v1/repo/key", { action: "generate", name: name.value, email: email.value }).then(function () {
              F.toast("Creating the key");
              keyPanel.innerHTML = "";
              keyPanel.appendChild(el("p", { class: "hint", text: "Creating the key, which takes a few seconds…" }));
              repoPoll = setTimeout(loadRepo, 2500);
            }).catch(F.toastError);
          } })
        ]),
        el("div", { class: "key-choice" }, [
          el("p", { class: "setting-label", text: "Or use a key you have" }),
          el("label", { class: "field" }, [el("span", { class: "field-label", text: "Secret key, without a passphrase" }), armored]),
          el("p", { class: "hint" }, ["Export it with ", el("code", { text: "gpg --armor --export-secret-keys <id>" }), "."]),
          el("button", { type: "button", class: "btn btn--ghost", text: "Import the key", onclick: function () {
            call("POST", "/api/v1/repo/key", { action: "import", armored: armored.value }).then(function () {
              F.toast("Key imported");
              loadRepo();
            }).catch(F.toastError);
          } })
        ])
      ]));
    };

    var renderAddresses = function (info) {
      document.getElementById("public-url").value = info.settings.public_url;
      if (info.detected_url) document.getElementById("public-url").placeholder = info.detected_url;
      var dl = document.getElementById("addresses");
      dl.innerHTML = "";
      [["Repository", info.urls.repo_url], ["Install file", info.app_id ? info.urls.flatpakref_url : ""],
       ["Beta install file", info.app_id ? info.urls.beta_flatpakref_url : ""], ["Repository file", info.urls.flatpakrepo_url],
       ["Remote name", info.remote_name]].forEach(function (p) {
        if (!p[1]) return;
        dl.appendChild(el("dt", { text: p[0] }));
        dl.appendChild(el("dd", {}, [el("code", { text: p[1] }), " ",
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Copy", onclick: function () { copy(p[1]); } })]));
      });
      if (!info.app_id) dl.appendChild(el("dd", { class: "hint addresses-note", text: "The install files appear once a release is uploaded, or an app ID is set under Repository > App." }));
    };

    var loadRepo = function () {
      clearTimeout(repoPoll);
      call("GET", "/api/v1/repo").then(function (info) {
        var missing = Object.keys(info.tools).filter(function (k) { return !info.tools[k]; });
        var blocker = document.getElementById("tools-blocker");
        blocker.textContent = missing.length ? missing.join(", ") + " isn't installed where Flatout runs. Run Flatout from its Docker image to sign and publish." : "";
        blocker.hidden = !missing.length;
        renderKey(info);
        renderAddresses(info);
      }).catch(F.toastError);
    };

    document.getElementById("address-form").addEventListener("submit", function (e) {
      e.preventDefault();
      showError("address-error", null);
      call("PATCH", "/api/v1/repo/settings", {
        public_url: document.getElementById("public-url").value.trim()
      }).then(function (info) {
        renderAddresses(info);
        F.toast("Saved");
      }).catch(function (err) { showError("address-error", err); });
    });
    document.getElementById("rebuild-btn").addEventListener("click", function () {
      call("POST", "/api/v1/repo/rebuild", {}).then(function () { F.toast("Re-signing the repository"); }).catch(F.toastError);
    });
    loadRepo();
  }

  /* ————— Installs ————— */
  var statsPage = document.getElementById("stats-page");
  if (statsPage) {
    var chartBox = document.getElementById("stats-chart");
    var tip = el("div", { class: "chart-tip", role: "tooltip", hidden: true });

    // Clean value-axis ticks (steps of 1, 2 or 5 x 10^k) covering the top value.
    var ticks = function (top) {
      if (top <= 0) return [0, 1];
      var raw = top / 4;
      var mag = Math.pow(10, Math.floor(Math.log10(raw)));
      var step = [1, 2, 5, 10].map(function (m) { return m * mag; }).filter(function (s) { return s >= raw; })[0];
      step = Math.max(1, step);
      var out = [];
      for (var v = 0; v <= top + step - 1 && out.length < 8; v += step) out.push(v);
      if (out[out.length - 1] < top) out.push(out[out.length - 1] + step);
      return out;
    };

    var drawChart = function (days) {
      chartBox.innerHTML = "";
      var NS = "http://www.w3.org/2000/svg";
      var W = Math.max(chartBox.clientWidth, 320), H = 240, L = 44, R = 10, T = 22, B = 30;
      var plotW = W - L - R, plotH = H - T - B;
      var top = Math.max.apply(null, days.map(function (d) { return d.installs; }).concat([0]));
      var ys = ticks(top), ymax = ys[ys.length - 1];
      var slot = plotW / days.length;
      var barW = Math.min(24, Math.max(2, slot - 2));   // the 2px surface gap between columns
      var svgEl = document.createElementNS(NS, "svg");
      svgEl.setAttribute("viewBox", "0 0 " + W + " " + H);
      svgEl.setAttribute("class", "chart-svg");
      svgEl.setAttribute("role", "img");
      svgEl.setAttribute("aria-label", "Installs checking for updates per day; the table below has every value.");
      var add = function (tag, attrs, text) {
        var n = document.createElementNS(NS, tag);
        Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
        if (text !== undefined) n.textContent = text;
        svgEl.appendChild(n);
        return n;
      };
      var y = function (v) { return T + plotH - (v / ymax) * plotH; };
      ys.forEach(function (v) {
        add("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "chart-grid" });
        add("text", { x: L - 8, y: y(v) + 4, class: "chart-axis", "text-anchor": "end" }, fmt(v));
      });
      var every = Math.max(1, Math.round(days.length / 6));
      var peak = 0;
      days.forEach(function (d, i) { if (d.installs > days[peak].installs) peak = i; });
      days.forEach(function (d, i) {
        var x = L + i * slot + (slot - barW) / 2;
        var h = (d.installs / ymax) * plotH;
        if (h > 0) {
          var r = Math.min(4, h / 2, barW / 2);
          // 4px rounded data end, square at the baseline.
          var x0 = x, x1 = x + barW, y0 = T + plotH, y1 = y0 - h;
          add("path", { class: "chart-bar", d: "M" + x0 + "," + y0 + "V" + (y1 + r) + "Q" + x0 + "," + y1 + " " + (x0 + r) + "," + y1 +
            "H" + (x1 - r) + "Q" + x1 + "," + y1 + " " + x1 + "," + (y1 + r) + "V" + y0 + "Z" });
        }
        if (i % every === 0 || i === days.length - 1) {
          add("text", { x: L + i * slot + slot / 2, y: H - 10, class: "chart-axis", "text-anchor": "middle" }, day(d.day));
        }
        // Direct labels on the peak and the latest day only.
        if ((i === peak || i === days.length - 1) && d.installs > 0) {
          add("text", { x: L + i * slot + slot / 2, y: y(d.installs) - 6, class: "chart-label", "text-anchor": "middle" }, fmt(d.installs));
        }
        // The hit target is the whole slot, taller than the bar.
        var hit = add("rect", { x: L + i * slot, y: T, width: slot, height: plotH, class: "chart-hit", tabindex: "0",
                                "aria-label": day(d.day) + ": " + fmt(d.installs) + " installs" });
        var show = function () {
          tip.innerHTML = "";
          tip.appendChild(el("strong", { text: fmt(d.installs) + " installs" }));
          tip.appendChild(el("span", { text: day(d.day) + " · " + fmt(d.checks) + " checks" }));
          tip.hidden = false;
          var box = chartBox.getBoundingClientRect(), cx = (L + i * slot + slot / 2) / W * box.width;
          tip.style.left = Math.min(Math.max(cx, 70), box.width - 70) + "px";
          tip.style.top = (y(d.installs) / H * box.height - 8) + "px";
          hit.classList.add("is-hover");
        };
        var hide = function () { tip.hidden = true; hit.classList.remove("is-hover"); };
        hit.addEventListener("pointerenter", show);
        hit.addEventListener("pointerleave", hide);
        hit.addEventListener("focus", show);
        hit.addEventListener("blur", hide);
      });
      add("line", { x1: L, x2: W - R, y1: T + plotH, y2: T + plotH, class: "chart-baseline" });
      chartBox.appendChild(svgEl);
      chartBox.appendChild(tip);
    };

    var longDay = function (iso) {
      try { return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" }); }
      catch (_) { return iso; }
    };
    var tileOf = function (label, value, note, chips) {
      return el("section", { class: "panel tile" }, [
        el("p", { class: "tile-label", text: label }),
        el("p", { class: "tile-big", text: value }),
        note ? el("p", { class: "hint", text: note }) : null,
        chips && chips.length ? el("p", { class: "tile-chips" }, chips.map(function (c) { return el("span", { class: "chip chip--muted", text: c }); })) : null
      ]);
    };
    var archChips = function (arches) {
      return Object.keys(arches || {}).map(function (a) { return a + " " + fmt(arches[a]); });
    };
    // Long tables show their first rows and a button for the rest; what was
    // opened stays open when the range changes.
    var ROWS = 10, expanded = {};
    var table = function (id, heads, rowsOf, empty, limit) {
      var t = document.getElementById(id);
      var wrap = t.closest(".table-scroll") || t;
      var more = document.getElementById(id + "-more");
      if (more) more.remove();
      t.innerHTML = "";
      if (!rowsOf.length) {
        t.appendChild(el("tbody", {}, [el("tr", {}, [el("td", { class: "hint", text: empty })])]));
        return;
      }
      var cut = limit && rowsOf.length > limit + 2;   // never hide just a row or two
      var shown = cut && !expanded[id] ? rowsOf.slice(0, limit) : rowsOf;
      t.appendChild(el("thead", {}, [el("tr", {}, heads.map(function (h) { return el("th", { text: h }); }))]));
      t.appendChild(el("tbody", {}, shown.map(function (cells) {
        return el("tr", {}, cells.map(function (c) { return el("td", typeof c === "object" && c ? c : { text: c }); }));
      })));
      if (!cut) return;
      wrap.after(el("p", { class: "table-more", id: id + "-more" }, [
        el("button", { type: "button", class: "btn btn--ghost btn--xs",
                       text: expanded[id] ? "Show the first " + limit : "Show all " + fmt(rowsOf.length),
                       onclick: function () {
                         expanded[id] = !expanded[id];
                         table(id, heads, rowsOf, empty, limit);
                         if (!expanded[id]) wrap.scrollIntoView({ block: "nearest" });
                       } })
      ]));
    };

    // Machines by architecture, as shares: the counts add up every release an
    // install took, so only the proportions say something about machines.
    var archTile = function (arches) {
      var live = arches.filter(function (a) { return a.installs_30_days > 0; });
      if (!live.length) return tileOf("Machines by architecture", "None", "No downloads in the last 30 days.");
      var total = live.reduce(function (n, a) { return n + a.installs_30_days; }, 0);
      return el("section", { class: "panel tile" }, [
        el("p", { class: "tile-label", text: "Machines by architecture" }),
        el("ul", { class: "arch-list" }, live.map(function (a) {
          var share = a.installs_30_days / total;
          var pct = share >= 0.01 ? Math.round(share * 100) + "%" : "Under 1%";
          return el("li", { class: "arch-row" }, [
            el("span", { class: "arch-name", text: a.arch }),
            el("span", { class: "arch-num", text: pct }),
            el("span", { class: "arch-bar", "aria-hidden": "true" }, [
              el("span", { style: "width:" + Math.max(share * 100, 1) + "%" })
            ])
          ]);
        })),
        el("p", { class: "hint", text: "Share of release downloads in the last 30 days." })
      ]);
    };

    // Installs by format: each format's busiest day of update checks this
    // week, as a share of all of them. One hue: the label names each bar.
    var FORMAT_NAMES = { flatpak: "Flatpak", rpm: "RPM", deb: "Debian", appimage: "AppImage", file: "File" };
    var CHECKS_BY = { flatpak: "the Flatpak repository", rpm: "the dnf repository", deb: "the apt repository", appimage: "their zsync file" };
    var drawFormats = function (s) {
      var panel = document.getElementById("stats-formats");
      var list = document.getElementById("format-list");
      var formats = s.formats;
      panel.hidden = !formats.length;
      list.innerHTML = "";
      var total = formats.reduce(function (n, f) { return n + f.installs; }, 0);
      formats.forEach(function (f) {
        var share = total ? f.installs / total : 0;
        var pct = !total ? "" : share >= 0.01 || !f.installs ? Math.round(share * 100) + "%" : "Under 1%";
        var split = s.packages.repositories.filter(function (r) { return r.format === f.format; });
        list.appendChild(el("li", { class: "format-row", title: fmt(f.installs) + " installs" + (pct ? ", " + pct + " of all" : "") }, [
          el("span", { class: "format-name", text: FORMAT_NAMES[f.format] }),
          el("span", { class: "arch-bar format-bar", "aria-hidden": "true" }, [
            el("span", { style: "width:" + (f.installs ? Math.max(share * 100, 1) : 0) + "%" })
          ]),
          el("span", { class: "format-num" }, [el("strong", { text: fmt(f.installs) }), pct ? " " + pct : ""]),
          el("span", { class: "format-sub", text: fmt(f.today) + " today" +
            (split.length > 1 ? " · " + split.map(function (r) { return r.channel + " " + fmt(r.peak_7_days); }).join(", ") : "") })
        ]));
      });
    };

    // Releases, in every format or one: every format together is a row per
    // version; one format is that format's own numbers, a row per file for
    // packages. The address keeps the choice over a reload.
    var statsFormat = new URLSearchParams(location.search).get("format") || "";
    var lastStats = null;
    var VKEY = function (v) { return (String(v || "").toLowerCase().match(/\d+|[a-z]+/g) || []).map(function (t) { return /^\d/.test(t) ? String(+t) : t; }).join("."); };
    var seenRange = function (a, b) { return a === b ? a : a + " to " + b; };
    var RELEASE_HINTS = {
      "": "Distinct installs that downloaded each version, in every format and architecture, and the downloads in all. " +
          "Flatpak installs count once for each day they pulled the release, which in practice is once; packages and files " +
          "count downloads by dnf, apt and AppImage updaters and from the site.",
      flatpak: "Distinct installs that downloaded each version, every architecture together, and the downloads in all. An " +
               "install counts once for each day it pulled the release, which in practice is once.",
      pkg: "Downloads of each file: by dnf, apt and AppImage updaters, and from the site. Each requester counts once a day."
    };
    var drawReleaseStats = function (s) {
      var pk = s.packages;
      var present = [];
      if (s.releases.length || s.formats.some(function (f) { return f.format === "flatpak"; })) present.push("flatpak");
      ["rpm", "deb", "appimage", "file"].forEach(function (f) {
        if (pk.downloads.some(function (d) { return d.format === f; }) || s.formats.some(function (x) { return x.format === f; })) present.push(f);
      });
      if (statsFormat && present.indexOf(statsFormat) === -1) statsFormat = "";
      var bar = document.getElementById("release-format-bar");
      var seg = document.getElementById("release-format");
      bar.hidden = present.length < 2;
      seg.innerHTML = "";
      [""].concat(present).forEach(function (f) {
        var input = el("input", { type: "radio", name: "release-format", value: f, checked: f === statsFormat, onchange: function () {
          statsFormat = f;
          var q = new URLSearchParams(location.search);
          if (f) q.set("format", f); else q.delete("format");
          history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
          drawReleaseStats(lastStats);
        } });
        seg.appendChild(el("label", {}, [input, el("span", { text: f ? FORMAT_NAMES[f] : "All formats" })]));
      });

      var pkgFormat = statsFormat && statsFormat !== "flatpak";
      document.getElementById("release-stats-hint").textContent = RELEASE_HINTS[pkgFormat ? "pkg" : statsFormat];
      // One format: what is checking it for updates, beside its downloads.
      var checks = document.getElementById("release-format-checks");
      var picked = s.formats.filter(function (f) { return f.format === statsFormat; })[0];
      checks.hidden = !picked;
      if (picked) {
        var split = pk.repositories.filter(function (r) { return r.format === statsFormat; });
        checks.textContent = fmt(picked.today) + " installs checked " + CHECKS_BY[statsFormat] + " today, " + fmt(picked.installs) +
          " on the busiest day of the last 7" + (split.length > 1 ? " (" + split.map(function (r) { return r.channel + " " + fmt(r.peak_7_days); }).join(", ") + ")" : "") + ".";
      }

      if (pkgFormat) {
        table("release-stats", ["File", "Version", "Channel", "Architecture", "Installs", "Downloads", "Seen"],
          pk.downloads.filter(function (d) { return d.format === statsFormat; }).map(function (d) {
            return [d.name, d.version, d.channel, d.arch || "", fmt(d.installs), fmt(d.downloads), seenRange(d.first_seen, d.last_seen)];
          }), "No downloads counted yet.", ROWS);
        return;
      }
      if (statsFormat === "flatpak") {
        table("release-stats", ["Version", "Channel", "Installs", "Downloads", "By architecture", "Seen"],
          s.releases.map(function (r) {
            return [r.version, r.channel, fmt(r.installs), fmt(r.downloads), archChips(r.arches).join(", "), seenRange(r.first_seen, r.last_seen)];
          }), "No downloads counted yet.", ROWS);
        return;
      }
      // Every format: one row per version and channel.
      var rows = {}, order = [];
      var add = function (fmtName, version, channel, installs, downloads, first, last, published) {
        var key = channel + " " + VKEY(version);
        var r = rows[key];
        if (!r) {
          r = rows[key] = { version: version, channel: channel, installs: 0, downloads: 0, by: {}, first: first, last: last, published: published };
          order.push(r);
        }
        if (fmtName === "flatpak") r.version = version;   // the spelling the site shows
        r.installs += installs;
        r.downloads += downloads;
        r.by[fmtName] = (r.by[fmtName] || 0) + installs;
        if (first < r.first) r.first = first;
        if (last > r.last) r.last = last;
        if (published > r.published) r.published = published;
      };
      s.releases.forEach(function (r) { add("flatpak", r.version, r.channel, r.installs, r.downloads, r.first_seen, r.last_seen, r.published_at); });
      pk.downloads.forEach(function (d) { add(d.format, d.version, d.channel, d.installs, d.downloads, d.first_seen, d.last_seen, d.published_at); });
      order.sort(function (a, b) { return a.published < b.published ? 1 : a.published > b.published ? -1 : 0; });
      table("release-stats", ["Version", "Channel", "Installs", "Downloads", "By format", "Seen"],
        order.map(function (r) {
          return [r.version, r.channel, fmt(r.installs), fmt(r.downloads),
                  present.filter(function (f) { return f in r.by; }).map(function (f) { return FORMAT_NAMES[f] + " " + fmt(r.by[f]); }).join(", "),
                  seenRange(r.first, r.last)];
        }), "No downloads counted yet.", ROWS);
    };

    // The detail panel shows one view at a time; the address keeps it over a reload.
    var views = document.querySelectorAll('input[name="stats-view"]');
    var showView = function (name) {
      if (name === "packages") name = "releases";   // a view of its own before 1.9
      var input = document.querySelector('input[name="stats-view"][value="' + name + '"]');
      if (!input || input.closest("label").hidden) input = views[0];
      input.checked = true;
      document.querySelectorAll(".stats-view").forEach(function (v) { v.hidden = v.dataset.view !== input.value; });
      history.replaceState(null, "", location.pathname + location.search + (input === views[0] ? "" : "#" + input.value));
    };
    views.forEach(function (r) { r.addEventListener("change", function () { showView(r.value); }); });
    var BASIS = {
      checks: "The busiest day of update checks in the last 7 days.",
      release: "The most-downloaded release of the last 14 days, until update checks build up.",
      none: "No installs counted yet."
    };

    var loadStats = function () {
      var range = document.querySelector('input[name="stats-days"]:checked').value;
      chartBox.classList.add("is-loading");
      call("GET", "/api/v1/stats?days=" + range).then(function (s) {
        chartBox.classList.remove("is-loading");
        document.getElementById("stats-meta").textContent =
          "Updated " + new Date(s.generated).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) +
          (s.counting_since ? ". Counting since " + longDay(s.counting_since) + "." : ". Nothing counted yet.");

        var summary = document.getElementById("stats-summary");
        summary.innerHTML = "";
        // With packages beside the Flatpak, the estimate is every format's together.
        var several = s.formats.length > 1;
        var everyFormat = s.formats.reduce(function (n, f) { return n + f.installs; }, 0);
        summary.appendChild(several
          ? tileOf("Estimated installs", fmt(everyFormat), "Every format together: the busiest day of update checks in the last 7 days for each.")
          : tileOf("Estimated installs", fmt(s.install_base), BASIS[s.install_base_basis]));
        [["Latest stable", s.latest_stable, "No stable release yet."], ["Latest beta", s.latest_beta, "No beta running."]].forEach(function (p) {
          var r = p[1];
          summary.appendChild(r
            ? tileOf(p[0] + " · " + r.version, fmt(r.installs), "Installs since " + longDay(r.published_at.slice(0, 10)) + ", " + fmt(r.downloads) + " downloads.", archChips(r.arches))
            : tileOf(p[0], "None", p[2]));
        });
        summary.appendChild(archTile(s.arches));

        var t = document.getElementById("stats-tiles");
        t.innerHTML = "";
        t.appendChild(tileOf("Active today", fmt(s.today), "Installs that checked for updates today, so far."));
        t.appendChild(tileOf("Seven-day average", fmt(s.average_7_days), "Installs checking for updates per day."));
        t.appendChild(several
          ? tileOf("On an older Flatpak release", fmt(s.older_installs), "Estimated Flatpak installs minus those on the latest stable or beta.")
          : tileOf("On an older release", fmt(s.older_installs), "Estimated installs minus those on the latest stable or beta."));
        var shipped = s.releases_30_days;
        t.appendChild(tileOf("Releases, last 30 days", fmt(shipped.total), fmt(shipped.stable) + " stable, " + fmt(shipped.beta) + " beta."));

        drawChart(s.days);
        window.onresize = function () { drawChart(s.days); };
        document.getElementById("stats-caption").textContent = s.peak_day
          ? "Each column is one day (UTC). " + longDay(s.days[0].day) + " to " + longDay(s.days[s.days.length - 1].day) +
            "; busiest day " + fmt(s.peak) + " installs on " + longDay(s.peak_day) + "."
          : "No update checks counted in this range.";
        table("stats-table", ["Day", "Installs", "Checks"],
          s.days.slice().reverse().map(function (d) { return [d.day, fmt(d.installs), fmt(d.checks)]; }), "");

        lastStats = s;
        drawFormats(s);
        drawReleaseStats(s);

        table("arch-stats", ["Architecture", "Installs, last 30 days", "Installs, all time", "Downloads, all time"],
          s.arches.map(function (a) { return [a.arch, fmt(a.installs_30_days), fmt(a.installs), fmt(a.downloads)]; }),
          "No downloads counted yet.");

        var ORIGIN = { upload: "uploaded", promote: "promoted", rollback: "brought back" };
        table("build-stats", ["Version", "Channel", "Architecture", "Commit", "Installs", "Downloads", "First seen", "Last seen"],
          s.builds.map(function (b) {
            return [b.version ? b.version + (b.origin && b.origin !== "upload" ? " (" + ORIGIN[b.origin] + ")" : "") : "Unknown build",
                    b.channel || "", b.arch || "", { text: b.commit.slice(0, 12), class: "mono", title: b.commit },
                    fmt(b.installs), fmt(b.downloads), b.first_seen, b.last_seen];
          }), "No downloads counted yet.", ROWS);
        showView(location.hash.slice(1));
      }).catch(function (err) { chartBox.classList.remove("is-loading"); F.toastError(err); });
    };
    document.querySelectorAll('input[name="stats-days"]').forEach(function (r) { r.addEventListener("change", loadStats); });
    loadStats();
  }

  /* ————— API and agents ————— */
  var apiPage = document.getElementById("api-page");
  if (apiPage) {
    var list = document.getElementById("token-list");
    var loadTokens = function () {
      call("GET", "/api/v1/tokens").then(function (data) {
        list.innerHTML = "";
        if (!data.tokens.length) list.appendChild(el("li", { class: "hint", text: "No tokens yet." }));
        data.tokens.forEach(function (t) {
          var meta = [t.prefix + "…", "can " + t.scopes.join(", "), "by " + t.owner,
                      t.last_used_at ? "used " + when(t.last_used_at) : "never used",
                      t.expires_at ? "expires " + when(t.expires_at) : ""].filter(Boolean).join(" · ");
          list.appendChild(el("li", { class: "manage-item" }, [
            el("div", { class: "manage-meta" }, [el("span", { class: "manage-title", text: t.name }), el("span", { class: "manage-sub", text: meta })]),
            el("button", { type: "button", class: "btn btn--danger btn--xs", text: "Revoke", onclick: function () {
              if (!confirm('Revoke "' + t.name + '"? Anything using it stops working at once.')) return;
              call("DELETE", "/api/v1/tokens/" + t.id).then(function () { F.toast("Revoked " + t.name); loadTokens(); }).catch(F.toastError);
            } })
          ]));
        });
      }).catch(F.toastError);
    };
    document.getElementById("token-form").addEventListener("submit", function (e) {
      e.preventDefault();
      showError("token-error", null);
      var scopes = Array.prototype.map.call(document.querySelectorAll('input[name="scope"]:checked'), function (c) { return c.value; });
      var days = document.getElementById("token-days").value;
      call("POST", "/api/v1/tokens", { name: document.getElementById("token-name").value, scopes: scopes, expires_in_days: days ? parseInt(days, 10) : null })
        .then(function (data) {
          document.getElementById("token-value").textContent = data.token;
          document.getElementById("token-new").hidden = false;
          document.getElementById("token-form").reset();
          loadTokens();
        }).catch(function (err) { showError("token-error", err); });
    });
    document.getElementById("token-copy").addEventListener("click", function () {
      copy(document.getElementById("token-value").textContent);
    });
    loadTokens();
  }
})();
