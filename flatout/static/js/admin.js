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
    failed: "Failed", ended: "Ended"
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

  /* ————— Uploads, for Releases and Packages ————— */

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
  var releasesPage = document.getElementById("releases-page");
  if (releasesPage) {
    var rows = document.getElementById("release-rows");
    var tiles = document.getElementById("channel-tiles");
    var pollTimer = null;
    var queue = fileQueue({
      input: document.getElementById("bundle-file"), drop: document.getElementById("bundle-drop"),
      list: document.getElementById("bundle-list"), label: document.getElementById("bundle-name"),
      empty: "Choose .flatpak bundles", unit: "bundle", error: "upload-error",
      refuse: function (file) {
        if (/\.(rpm|deb)$/i.test(file.name)) return "packages go under Packages";
        return /\.flatpak$/i.test(file.name) ? "" : "not a .flatpak bundle";
      }
    });

    document.getElementById("upload-form").addEventListener("submit", function (e) {
      e.preventDefault();
      if (queue.busy) return;
      showError("upload-error", null);
      var btn = document.getElementById("upload-btn");
      var fields = {
        channel: document.querySelector('input[name="channel"]:checked').value,
        version: document.getElementById("upload-version").value.trim(),
        notes: document.getElementById("upload-notes").value
      };
      var url = document.getElementById("upload-url").value.trim();
      var clearFields = function () {
        ["upload-version", "upload-notes", "upload-url"].forEach(function (id) { document.getElementById(id).value = ""; });
      };
      if (!queue.items.length && !url) { showError("upload-error", new Error("Choose a bundle, or give the address to fetch one from.")); return; }
      if (queue.items.length && url) { showError("upload-error", new Error("Upload the chosen bundles or fetch one from the address, not both at once.")); return; }
      F.setBusy(btn, true);
      if (!queue.items.length) {
        fields.url = url;
        call("POST", "/api/v1/releases", fields)
          .then(function () { clearFields(); F.toast("Fetching it. It's published once it's in."); refresh(); })
          .catch(function (err) { showError("upload-error", err); })
          .finally(function () { F.setBusy(btn, false); });
        return;
      }
      sendQueue(queue, document.getElementById("upload-progress"), function (item, uploadId) {
        return call("POST", "/api/v1/releases", { upload: uploadId, channel: fields.channel, version: fields.version, notes: fields.notes });
      }).then(function (r) {
        F.setBusy(btn, false);
        if (r.sent) refresh();
        if (!r.failed) {
          clearFields();
          F.toast(r.sent === 1 ? "Uploaded. Publishing it now." : "Uploaded " + r.sent + " bundles. Publishing them now.");
        } else {
          showError("upload-error", new Error((r.failed === 1 ? "One bundle" : r.failed + " bundles") + " didn't upload" +
            (r.sent ? " (" + r.sent + " did)" : "") + ". The list says why; upload again to retry them."));
        }
      });
    });

    var tile = function (label, info, actions) {
      var behind = info && info.behind && info.behind.length ? info.behind : [];
      return el("section", { class: "panel tile" }, [
        el("p", { class: "tile-label", text: label }),
        el("p", { class: "tile-big", text: info ? info.version : "None" }),
        el("p", { class: "hint", text: info ? info.arches.join(", ") + " · " + when(info.published_at) : (label === "Beta" ? "Upload to the beta channel to start one." : "Upload a bundle to publish the first version.") }),
        behind.length ? el("p", { class: "hint tile-warn", text: behind.map(function (a) { return a + " is still on " + info.builds[a].version; }).join("; ") + ". Upload its " + info.version + " build to match." }) : null,
        actions && actions.length ? el("div", { class: "tile-actions" }, actions) : null
      ]);
    };

    var releaseRow = function (r) {
      var actions = [];
      if (r.status === "superseded" && r.commit) {
        actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Bring back", title: "Make this build the live one again", onclick: function () {
          call("POST", "/api/v1/releases/" + r.id + "/rollback", {}).then(function () {
            F.toast("Rolling " + r.channel + " back to " + r.version);
            refresh();
          }).catch(F.toastError);
        } }));
      }
      actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Details", onclick: function () { openRelease(r.id); } }));
      var what = r.origin === "promote" ? "promoted" : r.origin === "rollback" ? "brought back" : "uploaded";
      return el("li", { class: "release-row is-" + r.status }, [
        el("div", { class: "release-main" }, [
          el("span", { class: "release-version", text: r.version || "Reading the bundle…" }),
          el("span", { class: "chip chip--muted", text: r.channel }),
          r.arch ? el("span", { class: "chip chip--muted", text: r.arch }) : null,
          el("span", { class: "status status--" + r.status, text: STATUS[r.status] || r.status })
        ]),
        el("div", { class: "release-sub" }, [
          (r.created_by ? what.charAt(0).toUpperCase() + what.slice(1) + " by " + r.created_by + " · " : "") + when(r.published_at || r.created_at) +
          (r.bundle.size ? " · " + size(r.bundle.size) : "")
        ]),
        r.error ? el("p", { class: "form-error", text: r.error }) : null,
        el("div", { class: "release-actions" }, actions)
      ]);
    };

    var openRelease = function (id) {
      var body = document.getElementById("release-modal-body");
      body.innerHTML = "";
      body.appendChild(el("p", { class: "hint", text: "Loading…" }));
      document.getElementById("release-modal").showModal();
      call("GET", "/api/v1/releases/" + id).then(function (data) {
        var r = data.release;
        document.getElementById("release-modal-title").textContent = (r.version || "Release") + " · " + r.channel + (r.arch ? " · " + r.arch : "");
        body.innerHTML = "";
        var facts = el("dl", { class: "addresses" });
        [["App", r.app_id], ["Status", STATUS[r.status] || r.status], ["Commit", r.commit], ["Runtime", r.runtime],
         ["Bundle SHA-256", r.bundle.sha256], ["Published", when(r.published_at)]].forEach(function (p) {
          if (!p[1]) return;
          facts.appendChild(el("dt", { text: p[0] }));
          facts.appendChild(el("dd", {}, [el("code", { text: p[1] })]));
        });
        body.appendChild(facts);
        var notes = el("textarea", { rows: 6, class: "field-input" });
        notes.value = r.notes || "";
        var version = el("input", { value: r.version || "", maxlength: 60 });
        body.appendChild(el("label", { class: "field" }, [el("span", { class: "field-label", text: "Version shown on the site" }), version]));
        body.appendChild(el("label", { class: "field" }, [el("span", { class: "field-label", text: "Release notes (Markdown)" }), notes]));
        body.appendChild(el("div", {}, [el("button", { type: "button", class: "btn btn--primary", text: "Save", onclick: function () {
          call("PATCH", "/api/v1/releases/" + r.id, { notes: notes.value, version: version.value }).then(function () {
            F.toast("Saved");
            refresh();
          }).catch(F.toastError);
        } })]));
        if (r.job && r.job.log) {
          body.appendChild(el("details", { class: "term-like" }, [
            el("summary", { text: "What the job did" }),
            el("pre", { class: "joblog", text: r.job.log })
          ]));
        }
      }).catch(function (err) { body.innerHTML = ""; body.appendChild(el("p", { class: "form-error", text: err.message })); });
    };

    var refresh = function () {
      clearTimeout(pollTimer);
      Promise.all([call("GET", "/api/v1/repo"), call("GET", "/api/v1/releases?limit=100")]).then(function (res) {
        var repoInfo = res[0], list = res[1].releases;
        var blocker = document.getElementById("releases-blocker");
        var missing = Object.keys(repoInfo.tools).filter(function (k) { return !repoInfo.tools[k]; });
        blocker.innerHTML = "";
        if (missing.length) {
          blocker.appendChild(el("p", { text: missing.join(", ") + " isn't installed where Flatout runs, so releases can't be published. Run Flatout from its Docker image." }));
        } else if (!repoInfo.signing_key) {
          blocker.appendChild(el("p", {}, ["The repository needs a signing key before the first release. ",
            el("a", { class: "link", href: "/admin/repository", text: "Create one under Signing and addresses." })]));
        }
        blocker.hidden = !blocker.childNodes.length;
        showMismatch(repoInfo);

        tiles.innerHTML = "";
        var betaActions = [];
        if (repoInfo.beta) {
          betaActions.push(el("button", { type: "button", class: "btn btn--primary btn--xs", text: "Promote to stable", onclick: function () {
            call("POST", "/api/v1/releases/promote", { from: "beta", to: "stable" }).then(function () {
              F.toast("Promoting " + repoInfo.beta.version + " to stable");
              refresh();
            }).catch(F.toastError);
          } }));
          betaActions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "End the beta", onclick: function () {
            var msg = prompt("Installed betas will be told no more updates are coming. The message they see:",
                             "This beta has ended. Install the stable version instead.");
            if (msg === null) return;
            call("POST", "/api/v1/channels/beta/end", { message: msg }).then(function () {
              F.toast("Ending the beta");
              refresh();
            }).catch(F.toastError);
          } }));
        }
        tiles.appendChild(tile("Stable", repoInfo.stable));
        tiles.appendChild(tile("Beta", repoInfo.beta, betaActions));

        rows.innerHTML = "";
        if (!list.length) rows.appendChild(el("li", { class: "hint", text: "Nothing uploaded yet." }));
        list.forEach(function (r) { rows.appendChild(releaseRow(r)); });
        var busy = list.some(function (r) { return r.status === "queued" || r.status === "processing"; });
        document.getElementById("jobs-state").textContent = busy ? "Publishing…" : "";
        if (busy) pollTimer = setTimeout(refresh, 2000);
      }).catch(F.toastError);
    };
    refresh();
  }

  /* ————— Packages ————— */
  var packagesPage = document.getElementById("packages-page");
  if (packagesPage) {
    var FORMAT = { rpm: "RPM", deb: "Debian", file: "File" };
    var PKG_STATUS = {
      queued: "Waiting", processing: "Publishing", live: "Live", superseded: "Earlier",
      withdrawn: "Withdrawn", pruned: "Pruned", failed: "Failed"
    };
    var pkgTimer = null;
    var pkgQueue = fileQueue({
      input: document.getElementById("pkg-file"), drop: document.getElementById("pkg-drop"),
      list: document.getElementById("pkg-list"), label: document.getElementById("pkg-name"),
      empty: "Choose packages or files", unit: "file", error: "pkg-error",
      refuse: function (file) { return /\.flatpak$/i.test(file.name) ? "Flatpak bundles go under Releases" : ""; }
    });

    document.getElementById("pkg-form").addEventListener("submit", function (e) {
      e.preventDefault();
      if (pkgQueue.busy) return;
      showError("pkg-error", null);
      var btn = document.getElementById("pkg-btn");
      var fields = {
        channel: document.querySelector('input[name="pkg-channel"]:checked').value,
        notes: document.getElementById("pkg-notes").value,
        version: document.getElementById("pkg-version").value.trim(),
        name: document.getElementById("pkg-filename").value.trim()
      };
      var url = document.getElementById("pkg-url").value.trim();
      var clearFields = function () {
        ["pkg-notes", "pkg-version", "pkg-filename", "pkg-url"].forEach(function (id) { document.getElementById(id).value = ""; });
      };
      if (!pkgQueue.items.length && !url) { showError("pkg-error", new Error("Choose a package or a file, or give the address to fetch one from.")); return; }
      if (pkgQueue.items.length && url) { showError("pkg-error", new Error("Upload the chosen files or fetch one from the address, not both at once.")); return; }
      if (fields.name && pkgQueue.items.length > 1) { showError("pkg-error", new Error("A download name is for one file at a time.")); return; }
      F.setBusy(btn, true);
      if (!pkgQueue.items.length) {
        fields.url = url;
        call("POST", "/api/v1/packages", fields)
          .then(function () { clearFields(); F.toast("Fetching it. It's published once it's in."); refresh(); })
          .catch(function (err) { showError("pkg-error", err); })
          .finally(function () { F.setBusy(btn, false); });
        return;
      }
      sendQueue(pkgQueue, document.getElementById("pkg-progress"), function (item, uploadId) {
        return call("POST", "/api/v1/packages", {
          upload: uploadId, filename: item.file.name, channel: fields.channel, notes: fields.notes,
          version: fields.version, name: fields.name
        });
      }).then(function (r) {
        F.setBusy(btn, false);
        if (r.sent) refresh();
        if (!r.failed) {
          clearFields();
          F.toast(r.sent === 1 ? "Uploaded. Publishing it now." : "Uploaded " + r.sent + " files. Publishing them now.");
        } else {
          showError("pkg-error", new Error((r.failed === 1 ? "One file" : r.failed + " files") + " didn't upload" +
            (r.sent ? " (" + r.sent + " did)" : "") + ". The list says why; upload again to retry them."));
        }
      });
    });

    var pkgTile = function (label, info, note) {
      return el("section", { class: "panel tile" }, [
        el("p", { class: "tile-label", text: label }),
        el("p", { class: "tile-big", text: info ? info.version : "None" }),
        el("p", { class: "hint", text: info ? info.name + " · " + info.arches.join(", ") + " · " + when(info.published_at) : note })
      ]);
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
      pairs.forEach(function (pair) {
        dl.appendChild(el("dt", { text: pair[0] }));
        dl.appendChild(el("dd", {}, [el("code", { text: pair[1] }), " ",
          el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Copy", onclick: function () { copy(pair[1]); } })]));
      });
      document.getElementById("pkg-repos").hidden = !box.childNodes.length;
    };

    var pkgRow = function (r) {
      var actions = [];
      if (r.status === "live" || r.status === "superseded") {
        actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Withdraw", title: "Take it out and delete it", onclick: function () {
          var what = (r.name || "This file") + " " + r.version;
          var after = r.format === "file" ? " It stops being offered for download." :
            " New installs get the version before it; installed copies keep this one until a newer version is published.";
          if (!confirm("Withdraw " + what + " from " + r.channel + "?" + after + " The file is deleted.")) return;
          call("POST", "/api/v1/packages/" + r.id + "/withdraw", {}).then(function () {
            F.toast("Withdrawing " + what);
            refresh();
          }).catch(F.toastError);
        } }));
      }
      actions.push(el("button", { type: "button", class: "btn btn--ghost btn--xs", text: "Details", onclick: function () { openPackage(r.id); } }));
      return el("li", { class: "release-row is-" + r.status }, [
        el("div", { class: "release-main" }, [
          el("span", { class: "release-version", text: r.name ? r.name + " " + r.version : "Reading the file…" }),
          r.format ? el("span", { class: "chip chip--muted", text: FORMAT[r.format] }) : null,
          el("span", { class: "chip chip--muted", text: r.channel }),
          r.arch ? el("span", { class: "chip chip--muted", text: r.arch }) : null,
          el("span", { class: "status status--" + r.status, text: PKG_STATUS[r.status] || r.status })
        ]),
        el("div", { class: "release-sub" }, [
          (r.created_by ? (r.origin === "promote" ? "Promoted" : "Uploaded") + " by " + r.created_by + " · " : "") +
          when(r.published_at || r.created_at) + (r.file.size ? " · " + size(r.file.size) : "") +
          (r.evr && r.evr !== r.version ? " · " + r.evr : "")
        ]),
        r.error ? el("p", { class: "form-error", text: r.error }) : null,
        el("div", { class: "release-actions" }, actions)
      ]);
    };

    var openPackage = function (id) {
      var body = document.getElementById("pkg-modal-body");
      body.innerHTML = "";
      body.appendChild(el("p", { class: "hint", text: "Loading…" }));
      document.getElementById("pkg-modal").showModal();
      call("GET", "/api/v1/packages/" + id).then(function (data) {
        var r = data.package;
        document.getElementById("pkg-modal-title").textContent = (r.name || "Package") + " " + r.version + " · " + r.channel;
        body.innerHTML = "";
        var facts = el("dl", { class: "addresses" });
        [["Type", FORMAT[r.format]], ["Status", PKG_STATUS[r.status] || r.status], ["Version", r.evr],
         ["Architecture", r.arch], ["Summary", r.summary], ["SHA-256", r.file.sha256],
         ["Download", r.file.url], ["Published", when(r.published_at)]].forEach(function (p) {
          if (!p[1]) return;
          facts.appendChild(el("dt", { text: p[0] }));
          facts.appendChild(el("dd", {}, [el("code", { text: p[1] })]));
        });
        body.appendChild(facts);
        var notes = el("textarea", { rows: 6, class: "field-input" });
        notes.value = r.notes || "";
        var version = el("input", { value: r.version || "", maxlength: 120 });
        body.appendChild(el("label", { class: "field" }, [el("span", { class: "field-label", text: "Version shown on the site" }), version]));
        body.appendChild(el("label", { class: "field" }, [el("span", { class: "field-label", text: "Release notes (Markdown)" }), notes]));
        body.appendChild(el("div", {}, [el("button", { type: "button", class: "btn btn--primary", text: "Save", onclick: function () {
          call("PATCH", "/api/v1/packages/" + r.id, { notes: notes.value, version: version.value }).then(function () {
            F.toast("Saved");
            refresh();
          }).catch(F.toastError);
        } })]));
        if (r.job && r.job.log) {
          body.appendChild(el("details", { class: "term-like" }, [
            el("summary", { text: "What the job did" }),
            el("pre", { class: "joblog", text: r.job.log })
          ]));
        }
      }).catch(function (err) { body.innerHTML = ""; body.appendChild(el("p", { class: "form-error", text: err.message })); });
    };

    var refresh = function () {
      clearTimeout(pkgTimer);
      Promise.all([call("GET", "/api/v1/repo"), call("GET", "/api/v1/packages?limit=100")]).then(function (res) {
        var repoInfo = res[0], list = res[1].packages, p = repoInfo.packages;
        var blocker = document.getElementById("packages-blocker");
        blocker.innerHTML = "";
        var cant = [];
        if (!p.tools.rpm) cant.push("RPMs (rpm, rpmsign and createrepo_c)");
        if (!p.tools.deb) cant.push("Debian packages (dpkg-deb)");
        if (cant.length) {
          blocker.appendChild(el("p", { text: "This server can't publish " + cant.join(" or ") + ": the tools aren't installed where Flatout runs. Run Flatout from its Docker image. Other files work." }));
        } else if (!repoInfo.signing_key) {
          blocker.appendChild(el("p", {}, ["RPM and Debian packages are signed with the repository's key, which doesn't exist yet. ",
            el("a", { class: "link", href: "/admin/repository", text: "Create one under Signing and addresses." })]));
        }
        blocker.hidden = !blocker.childNodes.length;
        document.getElementById("pkg-kept").value = repoInfo.settings.packages_kept;

        pkgTiles.innerHTML = "";
        var any = list.some(function (r) { return r.format === "rpm" || r.format === "deb"; });
        [["rpm", "RPM"], ["deb", "Debian"]].forEach(function (f) {
          var has = list.some(function (r) { return r.format === f[0]; });
          if (!has && any) return;   // show only the kinds in use, or both while there are none
          ["stable", "beta"].forEach(function (c) {
            if (c === "beta" && !p[f[0]].beta) return;
            pkgTiles.appendChild(pkgTile(f[1] + (c === "beta" ? " · beta" : ""), p[f[0]][c],
              "Upload " + (f[0] === "rpm" ? "an .rpm" : "a .deb") + " to start the " + (f[0] === "rpm" ? "dnf" : "apt") + " repository."));
          });
        });
        var betaLive = list.some(function (r) { return r.channel === "beta" && r.status === "live"; });
        if (betaLive) {
          pkgTiles.appendChild(el("section", { class: "panel tile" }, [
            el("p", { class: "tile-label", text: "Beta to stable" }),
            el("p", { class: "hint", text: "Copy the newest beta of every package and file to stable, as they are." }),
            el("div", { class: "tile-actions" }, [el("button", { type: "button", class: "btn btn--primary btn--xs", text: "Promote to stable", onclick: function () {
              call("POST", "/api/v1/packages/promote", { from: "beta", to: "stable" }).then(function (d) {
                F.toast("Promoting " + d.promoted.length + (d.promoted.length === 1 ? " package" : " packages") + " to stable");
                refresh();
              }).catch(F.toastError);
            } })])
          ]));
        }
        drawRepos(repoInfo);

        pkgRows.innerHTML = "";
        if (!list.length) pkgRows.appendChild(el("li", { class: "hint", text: "Nothing uploaded yet." }));
        list.forEach(function (r) { pkgRows.appendChild(pkgRow(r)); });
        var busy = list.some(function (r) { return r.status === "queued" || r.status === "processing"; }) ||
          list.some(function (r) { return r.job && (r.job.status === "queued" || r.job.status === "running"); });
        document.getElementById("pkg-state").textContent = busy ? "Publishing…" : "";
        if (busy) pkgTimer = setTimeout(refresh, 2000);
      }).catch(F.toastError);
    };
    var pkgRows = document.getElementById("pkg-rows");
    var pkgTiles = document.getElementById("pkg-tiles");

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
    var table = function (id, heads, rowsOf, empty) {
      var t = document.getElementById(id);
      t.innerHTML = "";
      if (!rowsOf.length) {
        t.appendChild(el("tbody", {}, [el("tr", {}, [el("td", { class: "hint", text: empty })])]));
        return;
      }
      t.appendChild(el("thead", {}, [el("tr", {}, heads.map(function (h) { return el("th", { text: h }); }))]));
      t.appendChild(el("tbody", {}, rowsOf.map(function (cells) {
        return el("tr", {}, cells.map(function (c) { return el("td", typeof c === "object" && c ? c : { text: c }); }));
      })));
    };
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
        summary.appendChild(tileOf("Estimated installs", fmt(s.install_base), BASIS[s.install_base_basis]));
        [["Latest stable", s.latest_stable, "No stable release yet."], ["Latest beta", s.latest_beta, "No beta running."]].forEach(function (p) {
          var r = p[1];
          summary.appendChild(r
            ? tileOf(p[0] + " · " + r.version, fmt(r.installs), "Installs since " + longDay(r.published_at.slice(0, 10)) + ", " + fmt(r.downloads) + " downloads.", archChips(r.arches))
            : tileOf(p[0], "None", p[2]));
        });

        var t = document.getElementById("stats-tiles");
        t.innerHTML = "";
        t.appendChild(tileOf("Active today", fmt(s.today), "Installs that checked for updates today, so far."));
        t.appendChild(tileOf("Seven-day average", fmt(s.average_7_days), "Installs checking for updates per day."));
        t.appendChild(tileOf("On an older release", fmt(s.older_installs), "Estimated installs minus those on the latest stable or beta."));
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

        table("release-stats", ["Version", "Channel", "Installs", "Downloads", "By architecture", "Seen"],
          s.releases.map(function (r) {
            return [r.version, r.channel, fmt(r.installs), fmt(r.downloads), archChips(r.arches).join(", "),
                    r.first_seen + " to " + r.last_seen];
          }), "No downloads counted yet.");

        table("arch-stats", ["Architecture", "Installs, last 30 days", "Installs, all time", "Downloads, all time"],
          s.arches.map(function (a) { return [a.arch, fmt(a.installs_30_days), fmt(a.installs), fmt(a.downloads)]; }),
          "No downloads counted yet.");

        var pk = s.packages;
        document.getElementById("pkg-stats").hidden = !pk.repositories.length && !pk.downloads.length;
        var REPO = { rpm: "dnf", deb: "apt" };
        table("pkg-repo-stats", ["Repository", "Installs checking today", "Busiest day, last 7 days"],
          pk.repositories.map(function (r) { return [REPO[r.format] + " · " + r.channel, fmt(r.today), fmt(r.peak_7_days)]; }),
          "No installs have checked the repositories in the last 7 days.");
        var KIND = { rpm: "RPM", deb: "Debian", file: "File" };
        table("pkg-download-stats", ["Package", "Version", "Type", "Channel", "Architecture", "Installs", "Downloads", "Seen"],
          pk.downloads.map(function (d) {
            return [d.name, d.version, KIND[d.format] || d.format, d.channel, d.arch || "", fmt(d.installs), fmt(d.downloads),
                    d.first_seen + " to " + d.last_seen];
          }), "No downloads counted yet.");

        var ORIGIN = { upload: "uploaded", promote: "promoted", rollback: "brought back" };
        table("build-stats", ["Version", "Channel", "Architecture", "Commit", "Installs", "Downloads", "First seen", "Last seen"],
          s.builds.map(function (b) {
            return [b.version ? b.version + (b.origin && b.origin !== "upload" ? " (" + ORIGIN[b.origin] + ")" : "") : "Unknown build",
                    b.channel || "", b.arch || "", { text: b.commit.slice(0, 12), class: "mono", title: b.commit },
                    fmt(b.installs), fmt(b.downloads), b.first_seen, b.last_seen];
          }), "No downloads counted yet.");
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
