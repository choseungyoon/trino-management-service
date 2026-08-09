/* TMS UI behaviour.
 *
 * Progressive enhancement only. Every action already works as a form post to a
 * real URL — this file makes those actions feel immediate. If it fails to load,
 * the console still works, which matters because it is used during incidents.
 *
 * No framework, no build step: this ships from the same FastAPI process.
 */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── Mobile navigation ─────────────────────────────────────────── */

  var menu = document.getElementById("menu-toggle");
  var sidebar = document.getElementById("sidebar");
  if (menu && sidebar) {
    menu.addEventListener("click", function () {
      var open = sidebar.dataset.open === "true";
      sidebar.dataset.open = open ? "false" : "true";
      menu.setAttribute("aria-expanded", open ? "false" : "true");
    });
  }

  /* ── Query detail drawer ───────────────────────────────────────── */
  /* The row link is a real href; this intercepts it into a <dialog> so the
     operator keeps their filters, scroll position, and place in the table. */

  var drawer = document.getElementById("drawer");

  function closeDrawer() {
    if (drawer && drawer.open) {
      drawer.close();
      drawer.innerHTML = "";
    }
  }

  if (drawer) {
    document.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-drawer]");
      if (trigger) {
        event.preventDefault();
        openDrawer(trigger.getAttribute("data-drawer"));
        return;
      }
      if (event.target.closest("[data-drawer-close]")) {
        event.preventDefault();
        closeDrawer();
      }
    });

    /* Clicking the backdrop closes; clicking inside the panel must not. */
    drawer.addEventListener("click", function (event) {
      if (event.target === drawer) closeDrawer();
    });
    drawer.addEventListener("close", function () { drawer.innerHTML = ""; });
  }

  function openDrawer(url) {
    fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then(function (html) {
        drawer.innerHTML = html;
        drawer.showModal();
        var close = drawer.querySelector("[data-drawer-close]");
        if (close) close.focus();
      })
      .catch(function () {
        /* Fall back to the full page rather than leaving a dead click. */
        window.location.href = url.replace(/[?&]fragment=1/, "");
      });
  }

  /* ── Auto-refresh ──────────────────────────────────────────────── */
  /* Reload the page on the collector's own cadence so the freshness label
     stays honest. Paused while a dialog is open, while the tab is hidden,
     and while the operator is typing — a page that reloads mid-sentence
     during an incident is worse than a slightly stale one. */

  var body = document.body;
  var interval = parseInt(body.getAttribute("data-refresh") || "0", 10);

  if (interval > 0) {
    var timer = null;

    function busy() {
      if (document.hidden) return true;
      if (drawer && drawer.open) return true;
      var active = document.activeElement;
      if (!active) return false;
      var tag = active.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    }

    function schedule() {
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        if (busy()) { schedule(); return; }
        window.location.reload();
      }, interval * 1000);
    }

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) schedule();
    });
    schedule();
  }

  /* ── Restart sequence: live progress ───────────────────────────── */
  /* Swaps the checklist and log in place instead of reloading the page, so
     the operator keeps their scroll position in a log that is still being
     written to. Falls back to the ordinary page refresh above if a poll
     fails — the whole screen still works with this file absent. */

  var seq = document.getElementById("seq");

  function atBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  function pollSequence() {
    var node = document.getElementById("seq");
    if (!node || node.dataset.live !== "true") return;

    var console_ = document.getElementById("console");
    /* Only follow the tail if they are already at it. Someone who scrolled up
       to read an error is reading it; yanking them back down is worse than
       showing nothing. */
    var follow = !console_ || atBottom(console_);
    var scrolled = console_ ? console_.scrollTop : 0;

    fetch("/restarts/" + node.dataset.sequence + "?fragment=1",
          { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then(function (html) {
        var holder = document.createElement("div");
        holder.innerHTML = html;
        var fresh = holder.querySelector("#seq");
        if (!fresh) throw new Error("unexpected fragment");

        /* Never replace the panel a form is being typed into. */
        var active = document.activeElement;
        if (active && node.contains(active) &&
            /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) {
          scheduleSequence();
          return;
        }

        node.replaceWith(fresh);
        var pane = document.getElementById("console");
        if (pane) pane.scrollTop = follow ? pane.scrollHeight : scrolled;
        scheduleSequence();
      })
      .catch(function () {
        /* Leave the page as it is and try again; the full-page refresh is
           still running underneath as a backstop. */
        scheduleSequence();
      });
  }

  var sequenceTimer = null;

  function scheduleSequence() {
    window.clearTimeout(sequenceTimer);
    var node = document.getElementById("seq");
    if (!node || node.dataset.live !== "true") return;
    sequenceTimer = window.setTimeout(function () {
      if (document.hidden) { scheduleSequence(); return; }
      pollSequence();
    }, 2000);
  }

  if (seq) {
    var initial = document.getElementById("console");
    if (initial) initial.scrollTop = initial.scrollHeight;
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) scheduleSequence();
    });
    scheduleSequence();
  }

  /* ── Write-action guards ───────────────────────────────────────── */

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var reason = form.querySelector('[name="reason"]');

    /* A blank reason is a 400 by contract. Say so before the round trip. */
    if (reason && !reason.value.trim()) {
      event.preventDefault();
      reason.focus();
      reason.setCustomValidity("A reason is required.");
      reason.reportValidity();
      return;
    }
    if (reason) reason.setCustomValidity("");

    /* Destructive submits are one-shot: a double click must not send two kills. */
    var submit = form.querySelector('button[type="submit"]');
    if (submit && !submit.disabled) {
      window.setTimeout(function () {
        submit.disabled = true;
        submit.textContent = submit.dataset.busy || "Working…";
      }, 0);
    }
  });

  document.addEventListener("input", function (event) {
    if (event.target.name === "reason") event.target.setCustomValidity("");
  });

  /* ── Freshness ticker ──────────────────────────────────────────── */
  /* "updated 3s ago" that stays true between reloads. */

  var freshness = document.querySelector(".freshness [data-collected-at]");
  if (freshness && !reduceMotion) {
    var collected = Date.parse(freshness.getAttribute("data-collected-at"));
    var suffix = freshness.getAttribute("data-suffix") || "";
    if (!isNaN(collected)) {
      window.setInterval(function () {
        var seconds = Math.max(0, Math.round((Date.now() - collected) / 1000));
        var text = seconds < 60 ? seconds + "s ago"
                 : seconds < 3600 ? Math.floor(seconds / 60) + "m ago"
                 : Math.floor(seconds / 3600) + "h ago";
        freshness.textContent = "updated " + text + suffix;
      }, 1000);
    }
  }
})();
