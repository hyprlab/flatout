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
    box.appendChild(el("p", {}, [
      el("strong", { text: "The site doesn't show these releases. " }),
      "They are for " + ids.join(", ") + ", but the site's app ID is " + repoInfo.app_id + ". " +
      "The homepage, the install files and the channel tiles only show releases of the site's app."
    ]));
    box.appendChild(el("p", {}, onAppPage
      ? ["Set the app ID below to " + ids[0] + ", or upload a bundle for " + repoInfo.app_id + "."]
      : [el("a", { class: "link", href: "/admin/app", text: "Set the app ID under Repository > App" }),
         ", or upload a bundle for " + repoInfo.app_id + "."]));
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

  /* ————— Releases ————— */
  var releasesPage = document.getElementById("releases-page");
  if (releasesPage) {
    var rows = document.getElementById("release-rows");
    var tiles = document.getElementById("channel-tiles");
    var fileInput = document.getElementById("bundle-file");
    var drop = document.getElementById("bundle-drop");
    var chosen = null;
    var pollTimer = null;

    var choose = function (file) {
      chosen = file || null;
      document.getElementById("bundle-name").innerHTML = "";
      document.getElementById("bundle-name").appendChild(chosen
        ? el("strong", { text: chosen.name + " · " + size(chosen.size) })
        : el("span", {}, [el("strong", { text: "Choose a .flatpak bundle" }), " or drop it here"]));
      drop.classList.toggle("has-file", !!chosen);
    };
    fileInput.addEventListener("change", function () { choose(fileInput.files[0]); });
    ["dragenter", "dragover"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("is-dropping"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      drop.addEventListener(t, function () { drop.classList.remove("is-dropping"); });
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer.files.length) choose(e.dataTransfer.files[0]);
    });

    document.getElementById("upload-form").addEventListener("submit", function (e) {
      e.preventDefault();
      showError("upload-error", null);
      var btn = document.getElementById("upload-btn");
      var channel = document.querySelector('input[name="channel"]:checked').value;
      var version = document.getElementById("upload-version").value.trim();
      var notes = document.getElementById("upload-notes").value;
      var url = document.getElementById("upload-url").value.trim();
      var done = function () {
        F.setBusy(btn, false);
        document.getElementById("upload-progress").hidden = true;
      };
      var success = function () {
        choose(null);
        fileInput.value = "";
        document.getElementById("upload-version").value = "";
        document.getElementById("upload-notes").value = "";
        document.getElementById("upload-url").value = "";
        F.toast("Uploaded. Publishing it now.");
        refresh();
      };
      if (!chosen && !url) { showError("upload-error", new Error("Choose a bundle, or give the address to fetch one from.")); return; }
      F.setBusy(btn, true);
      if (!chosen) {
        call("POST", "/api/v1/releases", { url: url, channel: channel, version: version, notes: notes })
          .then(success).catch(function (err) { showError("upload-error", err); }).finally(done);
        return;
      }
      // XMLHttpRequest, for the progress bar: bundles can be large.
      var form = new FormData();
      form.append("file", chosen);
      form.append("channel", channel);
      form.append("version", version);
      form.append("notes", notes);
      var xhr = new XMLHttpRequest();
      var bar = document.getElementById("upload-progress");
      bar.hidden = false;
      bar.value = 0;
      xhr.upload.addEventListener("progress", function (ev) {
        if (ev.lengthComputable) bar.value = Math.round(ev.loaded / ev.total * 100);
      });
      xhr.addEventListener("load", function () {
        var data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) {}
        done();
        if (xhr.status >= 200 && xhr.status < 300) success();
        else showError("upload-error", new Error(data.error || (xhr.status === 413 ? "The file is larger than this server accepts." : "The upload failed.")));
      });
      xhr.addEventListener("error", function () { done(); showError("upload-error", new Error("The upload was cut off.")); });
      xhr.open("POST", "/api/v1/releases");
      xhr.setRequestHeader("X-CSRF", F.csrf);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.send(form);
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

  /* ————— App ————— */
  var appPage = document.getElementById("app-page");
  if (appPage) {
    var fill = function (info) {
      document.getElementById("app-id").value = info.settings.app_id;
      document.getElementById("app-id").placeholder = info.settings.app_id ? "" : (info.app_id || "org.example.App");
      document.getElementById("remote-name").value = info.settings.remote_name;
      document.getElementById("remote-name").placeholder = info.settings.remote_name ? "" : info.remote_name;
      document.getElementById("runtime-repo").value = info.settings.runtime_repo;
      document.getElementById("prune-depth").value = info.settings.prune_depth;
      showMismatch(info, true);
      // The app IDs uploaded so far, one click from being the site's.
      var box = document.getElementById("app-ids");
      box.innerHTML = "";
      var others = info.release_app_ids.filter(function (id) { return id !== info.settings.app_id; });
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

    var loadStats = function () {
      var range = document.querySelector('input[name="stats-days"]:checked').value;
      chartBox.classList.add("is-loading");
      call("GET", "/api/v1/stats?days=" + range).then(function (s) {
        chartBox.classList.remove("is-loading");
        var t = document.getElementById("stats-tiles");
        t.innerHTML = "";
        [["Today", s.today], ["Seven-day average", s.average_7_days], ["Busiest day", s.peak]].forEach(function (p) {
          t.appendChild(el("section", { class: "panel tile" }, [el("p", { class: "tile-label", text: p[0] }), el("p", { class: "tile-big", text: fmt(p[1]) })]));
        });
        drawChart(s.days);
        window.onresize = function () { drawChart(s.days); };
        var table = document.getElementById("stats-table");
        table.innerHTML = "";
        table.appendChild(el("thead", {}, [el("tr", {}, [el("th", { text: "Day" }), el("th", { text: "Installs" }), el("th", { text: "Checks" })])]));
        var tb = el("tbody");
        s.days.slice().reverse().forEach(function (d) {
          tb.appendChild(el("tr", {}, [el("td", { text: d.day }), el("td", { text: fmt(d.installs) }), el("td", { text: fmt(d.checks) })]));
        });
        table.appendChild(tb);
        var rel = document.getElementById("release-stats");
        rel.innerHTML = "";
        if (!s.releases.length) {
          rel.appendChild(el("tbody", {}, [el("tr", {}, [el("td", { class: "hint", text: "No downloads counted yet." })])]));
          return;
        }
        rel.appendChild(el("thead", {}, [el("tr", {}, ["Version", "Channel", "Installs", "Downloads", "By architecture", "Seen"].map(function (h) { return el("th", { text: h }); }))]));
        var rb = el("tbody");
        s.releases.forEach(function (r) {
          var arches = Object.keys(r.arches).map(function (a) { return a + " " + fmt(r.arches[a]); }).join(", ");
          rb.appendChild(el("tr", {}, [el("td", { text: r.version }), el("td", { text: r.channel }), el("td", { text: fmt(r.installs) }),
            el("td", { text: fmt(r.downloads) }), el("td", { text: arches }), el("td", { text: r.first_seen + " to " + r.last_seen })]));
        });
        rel.appendChild(rb);
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
