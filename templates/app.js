(function () {
  "use strict";

  var root = document.documentElement;
  var concerts = Array.prototype.slice.call(document.querySelectorAll("[data-concert]"));
  var upcomingItems = Array.prototype.slice.call(document.querySelectorAll("[data-upcoming]"));
  var upcoming = document.getElementById("upcoming");
  var empty = document.getElementById("empty");
  var loadedAt = Date.now();
  var STALE_AFTER = 6 * 60 * 60 * 1000; // reload a page left open for hours

  function time(el, name) {
    return Date.parse(el.getAttribute(name));
  }

  var londonDay = (function () {
    try {
      var fmt = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit"
      });
      return function (ms) { return fmt.format(new Date(ms)); };
    } catch (e) {
      return function (ms) { return new Date(ms).toDateString(); };
    }
  })();

  // Same rules as status_label() in build.py.
  function statusLabel(concert, now) {
    var start = time(concert, "data-start");
    if (now >= start && now < time(concert, "data-finish")) return "On now";
    var day = londonDay(start);
    if (day === londonDay(now)) return "Today";
    if (day === londonDay(now + 24 * 60 * 60 * 1000)) return "Tomorrow";
    return "Next concert";
  }

  // Show the first concert that hasn't yet handed over to the next one.
  function update() {
    var now = Date.now();
    var current = null;
    concerts.forEach(function (concert) {
      if (!current && time(concert, "data-until") > now) current = concert;
    });
    concerts.forEach(function (concert) {
      concert.hidden = concert !== current;
    });
    if (empty) empty.hidden = !!current;
    if (current) {
      var status = current.querySelector("[data-status]");
      if (status) status.textContent = statusLabel(current, now);
    }

    if (!upcoming) return;
    var limit = parseInt(upcoming.getAttribute("data-show"), 10) || 4;
    var currentId = current ? current.getAttribute("data-concert") : null;
    var currentStart = current ? time(current, "data-start") : -Infinity;
    var shown = 0;
    upcomingItems.forEach(function (item) {
      var show = shown < limit &&
        item.getAttribute("data-upcoming") !== currentId &&
        time(item, "data-until") > now &&
        time(item, "data-start") >= currentStart;
      item.hidden = !show;
      if (show) shown += 1;
    });
    upcoming.hidden = shown === 0;
  }

  function refresh() {
    if (Date.now() - loadedAt > STALE_AFTER) {
      window.location.reload();
    } else {
      update();
    }
  }

  update();
  window.setInterval(update, 60 * 1000);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refresh();
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) refresh();
  });

  // Reader preferences, remembered on this device only.
  function save(key, value) {
    try {
      if (value) localStorage.setItem(key, value);
      else localStorage.removeItem(key);
    } catch (e) { /* private browsing: settings just aren't remembered */ }
  }

  var themeButton = document.getElementById("theme-toggle");
  var darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function isDark() {
    var chosen = root.getAttribute("data-theme");
    if (chosen) return chosen === "dark";
    return !!(darkQuery && darkQuery.matches);
  }

  function syncThemeButton() {
    if (themeButton) {
      themeButton.setAttribute("aria-label", isDark() ? "Switch to light mode" : "Switch to dark mode");
    }
  }

  if (themeButton) {
    themeButton.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      root.setAttribute("data-theme", next);
      save("theme", next);
      syncThemeButton();
    });
  }
  if (darkQuery && darkQuery.addEventListener) {
    darkQuery.addEventListener("change", syncThemeButton);
  }
  syncThemeButton();

  var sizes = ["", "large", "larger"];
  var sizeNames = { "": "standard", large: "large", larger: "largest" };
  var sizeButton = document.getElementById("size-toggle");

  function syncSizeButton() {
    if (!sizeButton) return;
    var size = root.getAttribute("data-size") || "";
    var hint = size === "larger" ? "Tap to return to standard." : "Tap to enlarge.";
    sizeButton.setAttribute("aria-label", "Text size: " + (sizeNames[size] || "standard") + ". " + hint);
  }

  if (sizeButton) {
    sizeButton.addEventListener("click", function () {
      var current = sizes.indexOf(root.getAttribute("data-size") || "");
      var next = sizes[(current + 1) % sizes.length];
      if (next) root.setAttribute("data-size", next);
      else root.removeAttribute("data-size");
      save("size", next);
      syncSizeButton();
    });
  }
  syncSizeButton();
})();
