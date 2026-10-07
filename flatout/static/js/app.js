/* Flatout's admin client. No dependencies, no build step.
 *
 * Sections, in order: API, toasts, theme, mobile sidebar, dialogs, settings,
 * admin, Turnstile, menus, dialogs that survive a reload, the About hero.
 * Each section guards on the elements it needs. The site editor
 * (editor.js) and the other admin pages reuse the helpers through
 * window.Flatout.
 */
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  var root = document.documentElement;
  var sheet = null;   // the template's detail sheet; Flatout has none

  /* ————— API ————— */
  // Every call goes through here, so the CSRF header and the {"error": "..."}
  // convention live in exactly one place.
  function api(url, body) {
    return request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF": CSRF },
      body: JSON.stringify(body || {})
    });
  }
  function get(url) {
    return request(url, { headers: { "X-CSRF": CSRF, "Accept": "application/json" } });
  }
  function request(url, opts) {
    return fetch(url, opts).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) throw new Error(data.error || "Something went wrong.");
        return data;
      });
    }, function () {
      throw new Error("Can't reach the server.");
    });
  }

  function setBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = busy;
    var label = btn.querySelector(".btn-label"), busyEl = btn.querySelector(".btn-busy");
    if (label) label.hidden = busy;
    if (busyEl) busyEl.hidden = !busy;
  }

  /* ————— Toast ————— */
  var toastTimer, toastLeaveTimer;
  function dismissToast(el) {
    if (el.hidden) return;
    el.classList.add("is-leaving");
    clearTimeout(toastLeaveTimer);
    toastLeaveTimer = setTimeout(function () {
      el.hidden = true;
      el.classList.remove("is-leaving");
    }, 260);
  }
  // toast("Saved") · toast("Deleted", "Undo", fn) · toast(err.message, null, null, true)
  function toast(message, actionLabel, actionFn, isError) {
    var el = document.getElementById("toast");
    if (!el) return;
    // A modal <dialog> paints above everything outside it, so a toast raised
    // while a dialog is open has to live inside it to be seen at all.
    var open = document.querySelector("dialog[open]");
    var host = open || document.body;
    if (el.parentNode !== host) host.appendChild(el);
    clearTimeout(toastTimer);
    clearTimeout(toastLeaveTimer);
    el.classList.remove("is-leaving");
    el.classList.toggle("toast--error", !!isError);
    el.textContent = message;
    if (actionLabel) {
      var action = document.createElement("button");
      action.className = "toast-action";
      action.textContent = actionLabel;
      action.addEventListener("click", function () {
        clearTimeout(toastTimer);
        dismissToast(el);
        actionFn();
      });
      el.appendChild(action);
    }
    el.hidden = false;
    toastTimer = setTimeout(function () { dismissToast(el); }, actionLabel ? 3500 : 2600);
  }
  function toastError(err) { toast(err.message || String(err), null, null, true); }

  // Carries a toast across a reload, for changes the page has to be rebuilt for.
  function queueToast(message) {
    try { sessionStorage.setItem("app-toast", message); } catch (_) {}
  }
  function reloadWith(message) { queueToast(message); location.reload(); }
  try {
    var pending = sessionStorage.getItem("app-toast");
    if (pending) {
      sessionStorage.removeItem("app-toast");
      setTimeout(function () { toast(pending); }, 250);
    }
  } catch (_) {}

  /* ————— Theme ————— */
  var media = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(pref) {
    root.setAttribute("data-theme-pref", pref);
    root.setAttribute("data-theme", pref === "system" ? (media.matches ? "dark" : "light") : pref);
  }
  media.addEventListener("change", function () {
    if (root.getAttribute("data-theme-pref") === "system") applyTheme("system");
  });

  var themeBtn = document.getElementById("theme-btn");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      applyTheme(next);
      var radio = document.querySelector('input[name="theme"][value="' + next + '"]');
      if (radio) radio.checked = true;
      api("/settings", { theme: next }).catch(function () {});
    });
  }
  document.querySelectorAll('input[name="theme"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      applyTheme(radio.value);
      api("/settings", { theme: radio.value }).catch(function () {});
    });
  });

  /* ————— Sidebar (mobile) ————— */
  var sidebar = document.getElementById("sidebar");
  var scrim = document.querySelector(".scrim");
  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("is-open", open);
    if (scrim) scrim.hidden = !open;
  }
  document.querySelectorAll("[data-open-sidebar]").forEach(function (el) {
    el.addEventListener("click", function () { setSidebar(true); });
  });
  document.querySelectorAll("[data-close-sidebar]").forEach(function (el) {
    el.addEventListener("click", function () { setSidebar(false); });
  });

  /* ————— Dialogs ————— */
  /* The settings window: a rail of sections and one pane at a time. On a
     phone only one of the two shows, and .is-showing-pane says which. */
  var settingsModal = document.getElementById("settings-modal");
  var narrow = window.matchMedia("(max-width: 700px)");
  function settingsItems() {
    return settingsModal ? Array.prototype.slice.call(settingsModal.querySelectorAll(".settings-navitem")) : [];
  }
  function showSettingsSection(name, focusItem) {
    if (!settingsModal) return;
    var chosen = null;
    settingsItems().forEach(function (item) {
      var on = item.getAttribute("data-section") === name;
      item.classList.toggle("is-active", on);
      item.setAttribute("aria-selected", on ? "true" : "false");
      item.tabIndex = on ? 0 : -1;
      if (on) chosen = item;
    });
    if (!chosen) return;
    settingsModal.querySelectorAll(".settings-pane").forEach(function (pane) {
      var on = pane.getAttribute("data-pane") === name;
      if (on && !pane.classList.contains("is-active")) pane.scrollTop = 0;
      pane.classList.toggle("is-active", on);
    });
    document.getElementById("settings-section-title").textContent =
      chosen.querySelector("span").textContent;
    settingsModal.classList.add("is-showing-pane");
    if (focusItem) chosen.focus();
  }
  function showSettingsList() {
    if (!settingsModal) return;
    settingsModal.classList.remove("is-showing-pane");
    var active = settingsModal.querySelector(".settings-navitem.is-active");
    if (active) active.focus();
  }

  function openDialog(id, section) {
    var dialog = document.getElementById(id);
    if (!dialog || dialog.open) return;
    if (id === "settings-modal") {
      // A named section opens straight to it. Otherwise the window reopens
      // where it was left, except on a phone, where it starts at the list.
      if (section) showSettingsSection(section);
      else if (narrow.matches) dialog.classList.remove("is-showing-pane");
    }
    setSidebar(false);
    dialog.showModal();
  }
  // Delegated, so buttons that arrive later (an empty state re-rendered by
  // paging) open their dialog too.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-open]");
    if (btn) openDialog(btn.getAttribute("data-open"), btn.getAttribute("data-settings-section"));
  });

  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // The sheet slides back out through the top before it actually closes, so
  // every way of dismissing it routes through here (button, backdrop, Escape).
  var SHEET_EXIT_MS = 300;   // keep in step with the sheet-out animation
  var sheetExitTimer = null;
  function closeDialog(dialog, then) {
    if (dialog.id !== "sheet" || prefersReducedMotion()) {
      dialog.close();
      if (then) then();
      return;
    }
    if (dialog.classList.contains("is-closing")) return;
    dialog.classList.add("is-closing");
    clearTimeout(sheetExitTimer);
    sheetExitTimer = setTimeout(function () {
      dialog.classList.remove("is-closing");
      dialog.close();
      if (then) then();
    }, SHEET_EXIT_MS);
  }

  // A click on a modal's ::backdrop is dispatched to the <dialog> itself, so
  // target identity alone can't tell it from a click on the dialog's own
  // chrome: the sheet's 840px shell is wider than its 660px article column, so
  // its side gutters are the dialog element too. Compare the pointer with the
  // dialog's box (which follows the open/close transform) instead, and require
  // the press to have started outside as well, so a text selection dragged off
  // the article doesn't dismiss it.
  function hitOutside(dialog, e) {
    var r = dialog.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    return e.clientX < r.left || e.clientX > r.right ||
           e.clientY < r.top || e.clientY > r.bottom;
  }
  function onBackdropClick(dialog, close) {
    var pressedOutside = false;
    dialog.addEventListener("pointerdown", function (e) {
      pressedOutside = e.target === dialog && hitOutside(dialog, e);
    });
    dialog.addEventListener("click", function (e) {
      var fromBackdrop = pressedOutside;
      pressedOutside = false;   // don't let a stale press arm a later click
      if (fromBackdrop && e.target === dialog && hitOutside(dialog, e)) close();
    });
  }
  document.querySelectorAll("dialog").forEach(function (dialog) {
    onBackdropClick(dialog, function () { closeDialog(dialog); });
    dialog.querySelectorAll("[data-close]").forEach(function (btn) {
      btn.addEventListener("click", function () { closeDialog(dialog); });
    });
    if (dialog.id === "sheet") {
      dialog.addEventListener("cancel", function (e) {   // Escape
        e.preventDefault();
        closeDialog(dialog);
      });
    }
  });

  /* ————— Settings ————— */
  if (settingsModal) {
    settingsItems().forEach(function (item) {
      item.addEventListener("click", function () {
        showSettingsSection(item.getAttribute("data-section"));
      });
    });
    // Arrow keys move through the rail and select as they go (the WAI-ARIA
    // vertical tab pattern); Home and End jump to the ends.
    settingsModal.querySelector(".settings-nav").addEventListener("keydown", function (e) {
      var items = settingsItems();
      var at = items.indexOf(document.activeElement);
      if (at === -1) return;
      var next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[e.key];
      if (next === undefined) return;
      e.preventDefault();
      next = (next + items.length) % items.length;
      showSettingsSection(items[next].getAttribute("data-section"), true);
      if (narrow.matches) settingsModal.classList.remove("is-showing-pane");
    });
    settingsModal.querySelector(".settings-back").addEventListener("click", showSettingsList);
    // On a phone, Escape inside a section goes back to the list first.
    settingsModal.addEventListener("cancel", function (e) {
      if (narrow.matches && settingsModal.classList.contains("is-showing-pane")) {
        e.preventDefault();
        showSettingsList();
      }
    });
  }
  var acctName = document.getElementById("acct-name");
  if (acctName) {
    acctName.addEventListener("change", function () {
      var value = acctName.value.trim();
      api("/settings", { name: value }).then(function () {
        toast("Name saved");
        var nameEl = document.getElementById("user-name");
        var emailEl = document.getElementById("user-email");
        var avatar = document.getElementById("user-avatar");
        var email = (emailEl && emailEl.textContent) || "";
        if (nameEl) nameEl.textContent = value || email;
        if (avatar) avatar.textContent = (value || email).charAt(0).toUpperCase();
        if (emailEl) emailEl.hidden = !value;
      }).catch(toastError);
    });
  }

  var passwordForm = document.getElementById("password-form");
  if (passwordForm) {
    passwordForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var errEl = document.getElementById("pw-error");
      errEl.hidden = true;
      api("/account/password", {
        current: document.getElementById("pw-current").value,
        new: document.getElementById("pw-new").value
      }).then(function () {
        passwordForm.reset();
        toast("Password updated");
      }).catch(function (err) {
        errEl.textContent = err.message;
        errEl.hidden = false;
      });
    });
  }

  /* ————— Admin ————— */
  var regOpen = document.getElementById("reg-open");
  if (regOpen) {
    regOpen.addEventListener("change", function () {
      api("/settings/registration", { open: regOpen.checked }).then(function (data) {
        toast(data.open ? "Registration is open" : "Registration is closed");
      }).catch(toastError);
    });
  }
  var adduserForm = document.getElementById("admin-adduser");
  if (adduserForm) {
    adduserForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var errEl = document.getElementById("au-error");
      errEl.hidden = true;
      api("/settings/users", {
        name: document.getElementById("au-name").value.trim(),
        username: document.getElementById("au-email").value.trim(),
        password: document.getElementById("au-password").value,
        is_admin: document.getElementById("au-admin").checked
      }).then(function () {
        reloadWith("User created");
      }).catch(function (err) {
        errEl.textContent = err.message;
        errEl.hidden = false;
      });
    });
  }
  document.querySelectorAll(".manage-item[data-user]").forEach(function (item) {
    var userId = item.getAttribute("data-user");
    var username = item.getAttribute("data-username");
    var pwBtn = item.querySelector("[data-admin-password]");
    var toggleBtn = item.querySelector("[data-admin-toggle]");
    var deleteBtn = item.querySelector("[data-admin-delete]");
    if (pwBtn) {
      pwBtn.addEventListener("click", function () {
        var pw = prompt('New password for "' + username + '" (at least 8 characters):');
        if (pw === null) return;
        api("/settings/users/" + userId + "/password", { new: pw })
          .then(function () { toast("Password reset for " + username); })
          .catch(toastError);
      });
    }
    if (toggleBtn) {
      toggleBtn.addEventListener("click", function () {
        api("/settings/users/" + userId + "/toggle-admin").then(function (data) {
          reloadWith(username + (data.is_admin ? " is now an admin" : " is no longer an admin"));
        }).catch(toastError);
      });
    }
    if (deleteBtn) {
      deleteBtn.addEventListener("click", function () {
        if (!confirm('Delete "' + username + '" and everything they own? This cannot be undone.')) return;
        api("/settings/users/" + userId + "/delete")
          .then(function () { reloadWith("Deleted " + username); })
          .catch(toastError);
      });
    }
  });

  /* ————— Security: Cloudflare Turnstile ————— */
  // Turning Turnstile on (or changing its keys) goes through a real challenge
  // rendered with the new site key; the server saves the keys only if
  // Cloudflare accepts the answer with the new secret. A wrong pair saved
  // blindly would lock everyone out of sign-in.
  var tsForm = document.getElementById("ts-form");
  var tsScript = null;
  var tsWidget = null;

  function loadTurnstile() {
    if (window.turnstile) return Promise.resolve(window.turnstile);
    if (!tsScript) {
      tsScript = new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
        s.async = true;
        s.onload = function () { resolve(window.turnstile); };
        s.onerror = function () {
          tsScript = null;
          reject(new Error("Couldn't load Cloudflare's challenge. Check the server's connection."));
        };
        document.head.appendChild(s);
      });
    }
    return tsScript;
  }

  // The widget's own failure codes, for the ones an admin can act on.
  function turnstileWidgetError(code) {
    code = String(code || "");
    if (code.indexOf("1101") === 0) return "Cloudflare doesn't recognize that site key.";
    if (code === "110200") return "This address isn't in the widget's hostname list. Add " + location.hostname + " to it in the Cloudflare dashboard.";
    if (code.indexOf("300") === 0 || code.indexOf("600") === 0) return "The challenge failed in this browser (error " + code + "). Try again.";
    return "The challenge failed to load (error " + code + ").";
  }

  function setTurnstileStatus(status) {
    var chip = document.getElementById("ts-chip");
    chip.textContent = status.on ? "On" : "Off";
    chip.classList.toggle("chip--muted", !status.on);
    document.getElementById("ts-status-text").textContent = status.on
      ? (status.source === "environment"
          ? "From the TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY environment variables. Keys saved here take over from them."
          : "Sign-in and sign-up show a challenge.")
      : "Sign-in and sign-up have no challenge.";
    document.getElementById("ts-off").hidden = !status.on;
    tsForm.querySelector("#ts-verify .btn-label").textContent =
      status.on ? "Verify and save the keys" : "Verify and turn on";
    var secret = document.getElementById("ts-secret");
    secret.value = "";
    secret.placeholder = status.secret_hint
      ? "Saved, ends in " + status.secret_hint + ". Leave empty to keep it."
      : "From the same widget";
    tsForm.setAttribute("data-has-secret", status.secret_hint ? "1" : "0");
  }

  function clearTurnstileWidget() {
    if (tsWidget !== null && window.turnstile) window.turnstile.remove(tsWidget);
    tsWidget = null;
    document.getElementById("ts-challenge").hidden = true;
  }

  if (tsForm) {
    var tsError = document.getElementById("ts-error");
    var tsVerify = document.getElementById("ts-verify");
    function showTsError(message) {
      tsError.textContent = message;
      tsError.hidden = !message;
    }

    tsForm.addEventListener("submit", function (e) {
      e.preventDefault();
      showTsError("");
      var siteKey = document.getElementById("ts-site").value.trim();
      var secretKey = document.getElementById("ts-secret").value.trim();
      if (!siteKey) { showTsError("Enter the site key."); return; }
      if (!secretKey && tsForm.getAttribute("data-has-secret") !== "1") {
        showTsError("Enter the secret key."); return;
      }
      setBusy(tsVerify, true);
      loadTurnstile().then(function (turnstile) {
        clearTurnstileWidget();
        document.getElementById("ts-challenge").hidden = false;
        tsWidget = turnstile.render("#ts-widget", {
          sitekey: siteKey,
          theme: root.getAttribute("data-theme") === "dark" ? "dark" : "light",
          callback: function (token) {
            api("/settings/turnstile", { site_key: siteKey, secret_key: secretKey, token: token })
              .then(function (data) {
                clearTurnstileWidget();
                setTurnstileStatus(data.status);
                setBusy(tsVerify, false);
                toast("Turnstile is on");
              })
              .catch(function (err) {
                // Remove, not reset: a reset widget passes again by itself
                // and would resubmit the same wrong keys in a loop.
                showTsError(err.message);
                clearTurnstileWidget();
                setBusy(tsVerify, false);
              });
          },
          "error-callback": function (code) {
            showTsError(turnstileWidgetError(code));
            clearTurnstileWidget();
            setBusy(tsVerify, false);
            return true;   // handled: don't let the widget retry on its own
          },
          "expired-callback": function () {
            showTsError("The challenge expired. Press the button again.");
            clearTurnstileWidget();
            setBusy(tsVerify, false);
          }
        });
      }).catch(function (err) {
        showTsError(err.message);
        setBusy(tsVerify, false);
      });
    });

    document.getElementById("ts-off").addEventListener("click", function () {
      showTsError("");
      clearTurnstileWidget();
      api("/settings/turnstile/disable").then(function (data) {
        setTurnstileStatus(data.status);
        toast("Turnstile is off");
      }).catch(toastError);
    });
  }

  /* ————— Menus (the topbar sort, and any .menu) ————— */
  document.querySelectorAll(".menu").forEach(function (menu) {
    var btn = menu.querySelector(".menubtn");
    var pop = menu.querySelector(".menupop");
    if (!btn || !pop) return;
    function set(open) {
      menu.classList.toggle("is-open", open);
      pop.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      set(pop.hidden);
    });
    document.addEventListener("click", function (e) {
      if (!menu.contains(e.target)) set(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") set(false);
    });
  });

  /* ————— Dialogs survive a reload ————— */
  // Refreshing the page brings back the dialog that was open, as it was: the
  // settings section and its scroll, a half-typed record, the search query.
  // It is the default for every <dialog>, including ones added later: one
  // with nothing to remember simply reopens. Add data-restore="off" to a
  // dialog that must not come back. Only a reload restores; arriving at the
  // page any other way starts clean. sessionStorage keeps it to this tab.
  //
  // Dialogs with state register a save/restore pair in dialogMemory.
  var RESTORE_KEY = "app-dialog";
  var navEntry = (performance.getEntriesByType && performance.getEntriesByType("navigation")[0]) || {};
  var reloaded = navEntry.type === "reload";
  var remembered = null;
  try { remembered = JSON.parse(sessionStorage.getItem(RESTORE_KEY) || "null"); } catch (_) {}
  try { sessionStorage.removeItem(RESTORE_KEY); } catch (_) {}
  if (!reloaded || !remembered || remembered.path !== location.pathname) remembered = null;

  var dialogMemory = {
    "settings-modal": {
      save: function (d) {
        var active = d.querySelector(".settings-navitem.is-active");
        var pane = d.querySelector(".settings-pane.is-active");
        return {
          section: active && active.getAttribute("data-section"),
          pane: d.classList.contains("is-showing-pane"),
          scroll: pane ? pane.scrollTop : 0
        };
      },
      restore: function (d, s) {
        openDialog("settings-modal", s.section);
        if (narrow.matches && !s.pane) d.classList.remove("is-showing-pane");
        var pane = d.querySelector(".settings-pane.is-active");
        if (pane) requestAnimationFrame(function () { pane.scrollTop = s.scroll || 0; });
      }
    }
  };

  function markRestoring(d) {
    // Reappear in place: no entrance animation for a dialog that never left.
    d.classList.add("is-restoring");
    setTimeout(function () { d.classList.remove("is-restoring"); }, 60);
  }

  var topDialog = null;
  function rememberDialog() {
    try {
      if (!topDialog || !topDialog.open || topDialog.getAttribute("data-restore") === "off") {
        sessionStorage.removeItem(RESTORE_KEY);
        return;
      }
      var memory = dialogMemory[topDialog.id];
      sessionStorage.setItem(RESTORE_KEY, JSON.stringify({
        id: topDialog.id,
        path: location.pathname,
        state: memory ? memory.save(topDialog) : null
      }));
    } catch (_) {}
  }
  var dialogWatcher = new MutationObserver(function (records) {
    records.forEach(function (r) {
      if (r.target.open) topDialog = r.target;
      else if (topDialog === r.target) topDialog = document.querySelector("dialog[open]");
    });
    rememberDialog();
  });
  document.querySelectorAll("dialog[id]").forEach(function (d) {
    dialogWatcher.observe(d, { attributes: true, attributeFilter: ["open"] });
  });
  // The latest draft, section and scroll are read as the page goes away.
  window.addEventListener("pagehide", rememberDialog);

  if (remembered) {
    var toRestore = document.getElementById(remembered.id);
    if (toRestore && toRestore.tagName === "DIALOG" && toRestore.getAttribute("data-restore") !== "off") {
      markRestoring(toRestore);
      var memory = dialogMemory[remembered.id];
      if (memory) memory.restore(toRestore, remembered.state || {});
      else toRestore.showModal();
    }
  }

  /* ————— Site status ————— */
  // Live, maintenance or unpublished, and the text of the two pages visitors
  // get meanwhile. The dialog is on every admin page.
  var statusForm = document.getElementById("site-status-form");
  var STATUS_LABELS = { published: "Live", maintenance: "Maintenance", unpublished: "Unpublished" };
  var STATUS_TOASTS = {
    published: "The site is live",
    maintenance: "Maintenance is on: visitors see the back-soon page",
    unpublished: "The site is unpublished: visitors see the coming-soon page"
  };
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function chosenStatus() {
    var r = document.querySelector('input[name="site-status"]:checked');
    return r ? r.value : "published";
  }
  function showStatusFields() {
    var chosen = chosenStatus();
    document.querySelectorAll(".status-page-fields").forEach(function (f) {
      f.hidden = f.getAttribute("data-for") !== chosen;
    });
  }
  function setStatusChip(status) {
    var chip = document.getElementById("site-status-chip");
    if (!chip) return;
    chip.textContent = STATUS_LABELS[status];
    chip.className = "status-chip status-chip--" + status;
    chip.parentNode.setAttribute("aria-label", "Site status: " + status);
  }
  window.flatoutSetStatusChip = setStatusChip;
  if (statusForm) {
    // The stored time is UTC; the field shows and takes local time.
    var until = document.getElementById("mt-until");
    var iso = until.getAttribute("data-iso");
    if (iso) {
      var d = new Date(iso);
      if (!isNaN(d)) until.value = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
                                   "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
    }
    document.querySelectorAll('input[name="site-status"]').forEach(function (r) {
      r.addEventListener("change", showStatusFields);
    });
    showStatusFields();
    statusForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var errEl = document.getElementById("site-status-error");
      var btn = statusForm.querySelector('button[type="submit"]');
      errEl.hidden = true;
      setBusy(btn, true);
      var status = chosenStatus();
      request("/api/v1/site/status", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRF": CSRF, "Accept": "application/json" },
        body: JSON.stringify({
          status: status,
          pages: {
            maintenance: {
              title: document.getElementById("mt-title").value,
              message: document.getElementById("mt-message").value,
              until: until.value ? new Date(until.value).toISOString().replace(/\.\d+Z$/, "Z") : "",
              updates_note: document.getElementById("mt-note").checked
            },
            unpublished: {
              title: document.getElementById("un-title").value,
              message: document.getElementById("un-message").value,
              updates_note: document.getElementById("un-note").checked
            }
          }
        })
      }).then(function (data) {
        setStatusChip(data.status);
        closeDialog(document.getElementById("site-status-modal"));
        // The Overview says the status in its own words; show it fresh.
        if (location.pathname === "/admin") reloadWith(STATUS_TOASTS[data.status]);
        else toast(STATUS_TOASTS[data.status]);
      }).catch(function (err) {
        errEl.textContent = err.message;
        errEl.hidden = false;
      }).finally(function () { setBusy(btn, false); });
    });
    if (location.hash === "#site-status") {
      history.replaceState(null, "", location.pathname + location.search);
      openDialog("site-status-modal");
    }
  }

  window.Flatout = {
    api: api, get: get, request: request, setBusy: setBusy, toast: toast,
    toastError: toastError, reloadWith: reloadWith, openDialog: openDialog,
    closeDialog: closeDialog, csrf: CSRF
  };

  /* ————— About hero: animated sine-wave gradient ————— */
  (function initAboutHeroBg() {
    var canvas = document.querySelector(".about-hero-bg");
    if (!canvas || prefersReducedMotion() || !("IntersectionObserver" in window)) return;
    var W = 200, H = 120;
    canvas.width = W; canvas.height = H;
    var cctx = canvas.getContext("2d");

    function hslToRgb(h, s, l) {
      h /= 360;
      var a = s * Math.min(l, 1 - l);
      var f = function (n) {
        var k = (n + h * 12) % 12;
        return Math.round((l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))) * 255);
      };
      return [f(0), f(8), f(4)];
    }
    var rand = function (a, b) { return a + Math.random() * (b - a); };

    // A triadic palette from a random base hue: every visit looks different
    // and none of them clash.
    var hueBase = Math.random() * 360;
    var triad = [0, 120, 240].map(function (d) { return (hueBase + d + rand(-8, 8) + 360) % 360; });
    for (var i = triad.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = triad[i]; triad[i] = triad[j]; triad[j] = tmp;
    }
    var rgb = triad.map(function (h) { return hslToRgb(h, 1, 0.55); });
    var n = rgb.length;

    var freq1 = rand(2.2, 4.0), freq2 = rand(5.0, 8.0);
    var amp1 = rand(0.16, 0.28), amp2 = rand(0.06, 0.12);
    var speed1 = rand(0.05, 0.12) * (Math.random() < 0.5 ? 1 : -1);
    var speed2 = rand(0.06, 0.14) * (Math.random() < 0.5 ? 1 : -1);
    var phase = Math.random() * Math.PI * 2;
    var rot = Math.random() * Math.PI * 2;
    var cosR = Math.cos(rot), sinR = Math.sin(rot);
    var maxDim = Math.max(W, H);

    var img = cctx.createImageData(W, H);
    var d = img.data;
    var running = false, start = 0;

    function frame(now) {
      if (!running) return;
      var t = (now - start) / 1000;
      var p1 = t * speed1, p2 = t * speed2 + phase;
      for (var y = 0; y < H; y++) {
        for (var x = 0; x < W; x++) {
          var cx = x - W / 2, cy = y - H / 2;
          var rx = cx * cosR - cy * sinR, ry = cx * sinR + cy * cosR;
          var nx = (rx + maxDim / 2) / maxDim, ny = (ry + maxDim / 2) / maxDim;
          var wave = Math.sin(nx * freq1 + p1) * amp1 + Math.sin(nx * freq2 + p2) * amp2;
          var g = Math.min(0.9999, Math.max(0, ny + wave));
          var seg = g * (n - 1), lo = Math.floor(seg), f = seg - lo;
          var c0 = rgb[lo], c1 = rgb[Math.min(lo + 1, n - 1)];
          var o = (y * W + x) * 4;
          d[o] = c0[0] + (c1[0] - c0[0]) * f;
          d[o + 1] = c0[1] + (c1[1] - c0[1]) * f;
          d[o + 2] = c0[2] + (c1[2] - c0[2]) * f;
          d[o + 3] = 255;
        }
      }
      cctx.putImageData(img, 0, 0);
      requestAnimationFrame(frame);
    }
    // Animate only while the canvas is visible (the About section is open).
    new IntersectionObserver(function (entries) {
      var visible = entries[0].isIntersecting;
      if (visible && !running) {
        running = true;
        start = performance.now();
        requestAnimationFrame(frame);
      } else if (!visible) {
        running = false;
      }
    }).observe(canvas);
  })();
})();
