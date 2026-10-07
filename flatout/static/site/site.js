/* The public site. No dependencies, no build step.
 *
 * Sections, in order: the theme switch, the header that trims as the page
 * scrolls, scroll reveals, the screenshot lightbox, the install dialog, copy
 * buttons, and the distro tabs. Each guards on the elements it needs.
 */
(function () {
  "use strict";

  /* ————— Theme switch ————— */
  // The head script already set data-theme. The button flips it and
  // remembers the choice; every <picture data-theme-swap> follows the mode in
  // use rather than the system's. Without a saved choice, a system change
  // still flows through. A site fixed to one mode has no switch.
  (function () {
    var html = document.documentElement;
    if (html.hasAttribute("data-theme-fixed")) {
      swapPictures(html.getAttribute("data-theme"));
      return;
    }
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    function saved() {
      try { var t = localStorage.getItem("site-theme"); return (t === "dark" || t === "light") ? t : null; }
      catch (e) { return null; }
    }
    function apply(t) {
      html.setAttribute("data-theme", t);
      swapPictures(t);
      document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
        var label = t === "dark" ? "Switch to light mode" : "Switch to dark mode";
        b.setAttribute("aria-label", label);
        b.title = label;
      });
    }
    apply(saved() || (mq.matches ? "dark" : "light"));
    document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
      b.addEventListener("click", function () {
        var next = html.getAttribute("data-theme") === "dark" ? "light" : "dark";
        try { localStorage.setItem("site-theme", next); } catch (e) {}
        apply(next);
      });
    });
    mq.addEventListener("change", function (e) { if (!saved()) apply(e.matches ? "dark" : "light"); });
  })();

  function swapPictures(theme) {
    document.querySelectorAll("picture[data-theme-swap] source").forEach(function (src) {
      src.media = theme === "dark" ? "all" : "not all";
    });
  }

  /* ————— The header over the hero ————— */
  // Trims from its full height to a slim bar over the first 140px of scroll.
  // The stylesheet reads --nav-p (0 at the top, 1 once trimmed).
  (function () {
    var nav = document.querySelector(".nav--hero");
    if (!nav) return;
    var RANGE = 140, ticking = false;
    function update() {
      ticking = false;
      var p = Math.min(1, Math.max(0, window.scrollY / RANGE));
      nav.style.setProperty("--nav-p", p.toFixed(3));
    }
    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(update);
    }, { passive: true });
    update();
  })();

  /* ————— Scroll reveals ————— */
  // Blocks fade in and rise as they enter the screen, staggered left to right
  // and then row by row. Scrolling back up, a block fades away once it drops
  // into the bottom tenth of the screen and is re-armed. Nothing is hidden
  // until this runs, so without scripts everything is visible.
  (function () {
    if (!("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (document.body.classList.contains("is-preview")) return;   // the editor reloads it constantly
    var targets = Array.prototype.slice.call(document.querySelectorAll(".reveal-me"));
    if (!targets.length) return;
    var COL_STEP = 90, ROW_STEP = 110;

    var show = new IntersectionObserver(function (entries) {
      var batch = entries.filter(function (e) { return e.isIntersecting && !e.target.classList.contains("is-in"); })
        .map(function (e) {
          var r = e.target.getBoundingClientRect();
          return { el: e.target, top: Math.round(r.top), left: Math.round(r.left) };
        })
        .sort(function (a, b) { return a.top - b.top || a.left - b.left; });
      var row = -1, col = 0, lastTop = null;
      batch.forEach(function (item) {
        if (lastTop === null || Math.abs(item.top - lastTop) > 8) { row += 1; col = 0; lastTop = item.top; }
        else { col += 1; }
        item.el.style.setProperty("--reveal-delay", (col * COL_STEP + row * ROW_STEP) + "ms");
        item.el.classList.add("is-in");
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });

    var hide = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting || e.boundingClientRect.top <= 0) return;
        e.target.classList.remove("is-in");
      });
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0 });

    targets.forEach(function (el) {
      el.classList.add("reveal");
      show.observe(el);
      hide.observe(el);
    });
  })();

  /* ————— Screenshot lightbox ————— */
  // Any [data-zoom] image opens full size; a click on it (or the magnifier)
  // toggles 1:1, where the stage scrolls so small print is readable.
  (function () {
    var box = document.getElementById("lightbox");
    var targets = document.querySelectorAll("[data-zoom]");
    if (!box || !targets.length) return;
    var stage = box.querySelector(".lightbox__stage");
    var img = box.querySelector(".lightbox__img");
    var caption = box.querySelector(".lightbox__caption");
    var zoomBtn = box.querySelector("[data-lightbox-zoom]");
    var lastFocus = null, drag = null, dragged = false;

    function zoomed() { return box.classList.contains("is-zoomed"); }
    function measure() {
      var fits = img.naturalWidth && img.naturalWidth > img.clientWidth + 8;
      box.classList.toggle("is-zoomable", !!fits);
    }
    function setZoom(on, originX, originY) {
      if (on && !box.classList.contains("is-zoomable")) return;
      if (on) box.style.setProperty("--lightbox-zoom-w", img.naturalWidth + "px");
      box.classList.toggle("is-zoomed", on);
      zoomBtn.setAttribute("aria-label", on ? "Zoom out" : "Zoom in");
      if (!on) { stage.scrollTop = 0; stage.scrollLeft = 0; return; }
      var rx = typeof originX === "number" ? originX : .5;
      var ry = typeof originY === "number" ? originY : .5;
      requestAnimationFrame(function () {
        stage.scrollLeft = img.offsetLeft + rx * img.offsetWidth - stage.clientWidth / 2;
        stage.scrollTop = img.offsetTop + ry * img.offsetHeight - stage.clientHeight / 2;
      });
    }
    function open(el) {
      lastFocus = document.activeElement;
      var fig = el.closest("figure");
      var cap = fig && fig.querySelector("figcaption");
      var text = cap ? cap.textContent.replace(/\s+/g, " ").trim() : "";
      box.classList.remove("is-zoomed", "is-zoomable");
      img.src = el.currentSrc || el.src;
      img.alt = el.alt || "";
      caption.firstElementChild.textContent = text;
      caption.hidden = !text;
      box.classList.toggle("has-caption", !!text);
      box.hidden = false;
      document.body.style.overflow = "hidden";
      stage.scrollTop = 0; stage.scrollLeft = 0;
      if (img.complete) measure();
      box.focus();
    }
    function close() {
      box.hidden = true;
      box.classList.remove("is-zoomed", "is-zoomable", "is-panning", "has-caption");
      document.body.style.overflow = "";
      img.removeAttribute("src");
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    targets.forEach(function (el) {
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      if (!el.title) el.title = "Click to view full size";
      el.addEventListener("click", function () { open(el); });
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(el); }
      });
    });
    img.addEventListener("load", measure);
    window.addEventListener("resize", function () { if (!box.hidden && !zoomed()) measure(); });
    img.addEventListener("click", function (e) {
      if (dragged) { dragged = false; return; }
      var r = img.getBoundingClientRect();
      setZoom(!zoomed(), (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
    });
    zoomBtn.addEventListener("click", function () { setZoom(!zoomed()); });
    img.addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse" || !zoomed()) return;
      drag = { x: e.clientX, y: e.clientY, left: stage.scrollLeft, top: stage.scrollTop };
      dragged = false;
      img.setPointerCapture(e.pointerId);
      box.classList.add("is-panning");
      e.preventDefault();
    });
    img.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragged = true;
      stage.scrollLeft = drag.left - dx;
      stage.scrollTop = drag.top - dy;
    });
    ["pointerup", "pointercancel"].forEach(function (type) {
      img.addEventListener(type, function () {
        if (!drag) return;
        drag = null;
        box.classList.remove("is-panning");
      });
    });
    stage.addEventListener("click", function (e) { if (e.target === stage) close(); });
    box.querySelectorAll("[data-lightbox-close]").forEach(function (el) { el.addEventListener("click", close); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !box.hidden) close(); });
  })();

  /* ————— Install dialog ————— */
  (function () {
    var modal = document.getElementById("install-modal");
    if (!modal) return;
    var dialog = modal.querySelector(".modal__scroll");
    var lastFocus = null;

    function open(e) {
      if (e) e.preventDefault();
      lastFocus = document.activeElement;
      modal.hidden = false;
      document.body.style.overflow = "hidden";
      dialog.focus();
    }
    function close() {
      modal.hidden = true;
      document.body.style.overflow = "";
      if (lastFocus && lastFocus.focus) lastFocus.focus();
      if (location.hash === "#install") history.replaceState(null, "", location.pathname + location.search);
    }
    document.querySelectorAll("[data-install-open]").forEach(function (el) { el.addEventListener("click", open); });
    modal.querySelectorAll("[data-install-close]").forEach(function (el) { el.addEventListener("click", close); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !modal.hidden) close(); });
    // A link to /#install opens it straight away.
    if (location.hash === "#install") open();

    // The picker: the machine and the method drive one state. The install
    // file needs neither (flatpak resolves the architecture), the bundle
    // download needs both.
    var NAMES = { x86_64: "Intel/AMD", aarch64: "ARM" };
    var archInputs = modal.querySelectorAll('input[name="install-arch"]');
    var methodInputs = modal.querySelectorAll('input[name="install-method"]');
    function value(list) {
      for (var i = 0; i < list.length; i++) if (list[i].checked || list[i].type === "hidden") return list[i].value;
      return list[0] ? list[0].value : "";
    }
    function render() {
      var arch = value(archInputs);
      var method = value(methodInputs);
      if (method) {
        modal.querySelectorAll(".how").forEach(function (sec) {
          sec.hidden = sec.getAttribute("data-method") !== method;
        });
      }
      var link = modal.querySelector("[data-bundle-link]");
      if (link && arch) link.href = link.getAttribute("data-href").replace("{arch}", arch);
      var cmd = document.getElementById("install-cmd-bundle");
      if (cmd && arch) cmd.textContent = cmd.getAttribute("data-template").split("{arch}").join(arch);
      modal.querySelectorAll(".dl-arch-name").forEach(function (el) { el.textContent = NAMES[arch] || arch; });
      // Architectures can be on different versions; say the chosen one's.
      var picked = modal.querySelector('input[name="install-arch"]:checked');
      var sub = modal.querySelector("[data-install-version]");
      if (picked && sub && picked.getAttribute("data-version")) sub.textContent = "Version " + picked.getAttribute("data-version");
    }
    archInputs.forEach(function (el) { el.addEventListener("change", render); });
    methodInputs.forEach(function (el) { el.addEventListener("change", render); });

    // Guess the machine. Chromium only tells the CPU through client hints;
    // elsewhere the user-agent string carries it on Linux ("Linux aarch64").
    function useArm() {
      var arm = modal.querySelector('input[name="install-arch"][value="aarch64"]');
      if (arm && arm.type === "radio") { arm.checked = true; render(); }
    }
    var ua = navigator.userAgent || "";
    if (/aarch64|arm64|armv8/i.test(ua)) {
      useArm();
    } else if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
      navigator.userAgentData.getHighEntropyValues(["architecture", "bitness"]).then(function (hints) {
        if (hints && hints.architecture === "arm" && hints.bitness === "64") useArm();
      }).catch(function () {});
    }
    render();
  })();

  /* ————— Copy buttons ————— */
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = document.querySelector(btn.getAttribute("data-copy"));
      if (!target) return;
      var text = target.textContent.trim();
      var label = btn.querySelector(".code-copy__label");
      function done() {
        btn.classList.add("is-copied");
        if (label) label.textContent = "Copied!";
        setTimeout(function () {
          btn.classList.remove("is-copied");
          if (label) label.textContent = "Copy";
        }, 2000);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () { fallback(text, done); });
      } else {
        fallback(text, done);
      }
    });
  });
  function fallback(text, done) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (err) {}
    document.body.removeChild(ta);
  }

  /* ————— Distro tabs ————— */
  // Click, or the left and right arrow keys, per group of tabs.
  document.querySelectorAll("[data-tabs]").forEach(function (group) {
    var tabs = Array.prototype.slice.call(group.querySelectorAll(".ostab"));
    function select(tab) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) { panel.classList.toggle("is-active", on); panel.hidden = !on; }
      });
    }
    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { select(tab); });
      tab.addEventListener("keydown", function (e) {
        var dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!dir) return;
        e.preventDefault();
        var next = tabs[(i + dir + tabs.length) % tabs.length];
        next.focus();
        select(next);
      });
    });
  });
})();
