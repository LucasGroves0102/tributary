/* The task pane: save or remove the API key (OfficeRuntime.storage only), refresh every figure,
 * check the key against /v1/me. The key is never shown again after saving: at most its first 8
 * characters, the prefix Tributary itself shows. Runs in the shared runtime beside functions.js. */
(function () {
  "use strict";

  var KEY = /^trb_[A-Za-z0-9_-]{43}$/;
  var SHOWN = 8;
  var addin = window.TributaryAddin;

  function el(id) {
    return document.getElementById(id);
  }

  function say(id, text) {
    el(id).textContent = text;
  }

  function storage() {
    return window.OfficeRuntime && window.OfficeRuntime.storage;
  }

  function showError(text) {
    var e = el("key-error");
    e.textContent = text;
    e.hidden = !text;
  }

  function showKeyState() {
    var s = storage();
    if (!s) {
      say("key-state", "Open this pane from Excel (Home, Tributary) to save a key.");
      return Promise.resolve();
    }
    return s.getItem(addin.KEY_ITEM).then(function (k) {
      say("key-state", k ? "A key is saved (" + k.slice(0, SHOWN) + "…)." : "No key saved yet.");
    }, function () {
      say("key-state", "Couldn't read the saved key: paste it again.");
    });
  }

  /** Every TRIBUTARY cell asks again (Excel recalculates the whole workbook). */
  function recalculate() {
    if (addin.client) addin.client.clear();
    if (!window.Excel || !window.Excel.run) return Promise.resolve(false);
    return window.Excel.run(function (ctx) {
      ctx.workbook.application.calculate(window.Excel.CalculationType.full);
      return ctx.sync();
    }).then(function () { return true; }, function () { return false; });
  }

  function save(event) {
    event.preventDefault();
    var input = el("key");
    var key = input.value.trim();
    input.value = "";  // never kept on screen
    showError("");
    if (!KEY.test(key)) {
      showError("That isn't a Tributary API key: one starts trb_ and has 47 characters.");
      return;
    }
    var s = storage();
    if (!s) {
      showError("Open this pane from Excel to save a key.");
      return;
    }
    s.setItem(addin.KEY_ITEM, key).then(function () {
      return showKeyState();
    }).then(recalculate).then(function () {
      say("figures-state", "Key saved: your TRIBUTARY cells are asking again.");
    }, function () {
      showError("Couldn't save the key: try again.");
    });
  }

  function forget() {
    var s = storage();
    if (!s) return;
    s.removeItem(addin.KEY_ITEM).then(function () {
      if (addin.client) addin.client.clear();
      say("figures-state", "Key removed: the TRIBUTARY cells need a key to recalculate.");
      return showKeyState();
    });
  }

  function refresh() {
    say("figures-state", "Asking again…");
    recalculate().then(function (ok) {
      say("figures-state", ok ? "Every TRIBUTARY cell asked again." : "Couldn't recalculate the workbook.");
    });
  }

  function check() {
    var s = storage();
    if (!s) return;
    say("figures-state", "Checking…");
    s.getItem(addin.KEY_ITEM).then(function (key) {
      if (!key) {
        say("figures-state", "No key saved yet.");
        return;
      }
      return fetch("/api/query/v1/me", {
        headers: { "Authorization": "Bearer " + key }, credentials: "omit", redirect: "error"
      }).then(function (res) {
        if (res.status === 401) {
          say("figures-state", "Tributary refused this key (unknown, expired or revoked): make a new one.");
          return;
        }
        if (!res.ok) {
          say("figures-state", "Tributary couldn't check the key (" + res.status + "): try again in a moment.");
          return;
        }
        return res.json().then(function (me) {
          var k = me.api_key || {};
          var left = res.headers.get("x-ratelimit-remaining-day");
          say("figures-state", "The key works: “" + (k.name || "key") + "” (" + (k.prefix || "") +
            "), " + (k.scopes || []).join(" and ") + ", expires " + String(k.expires_at || "").slice(0, 10) +
            (left ? "; " + Number(left).toLocaleString() + " requests left today" : "") + ".");
        });
      });
    }).catch(function () {
      say("figures-state", "Tributary didn't answer: check the connection.");
    });
  }

  function start() {
    el("key-form").addEventListener("submit", save);
    el("forget").addEventListener("click", forget);
    el("refresh").addEventListener("click", refresh);
    el("check").addEventListener("click", check);
    showKeyState();
  }

  if (window.Office && window.Office.onReady) {
    window.Office.onReady(start);
  } else {
    start();
  }
})();
