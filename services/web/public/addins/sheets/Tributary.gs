/**
 * Tributary for Google Sheets: figures from SEC filings in your cells, each with the filing it
 * came from. https://tributary.lucasgroves.dev/#/api ("In your spreadsheet").
 *
 * Install: Extensions > Apps Script, replace everything in Code.gs with this file, Save, reload the
 * spreadsheet, then Tributary > Set API key (a read-only key is enough; Google asks you once to
 * let the script reach an external service and show a prompt).
 *
 *   =TRIBUTARY("NVDA", "revenue")                    the newest quarter's revenue
 *   =TRIBUTARY("NVDA", "revenue", "FY2024")          a fiscal year (or TTM, FY, FY2024 Q3, CY2024 Q3)
 *   =TRIBUTARY(A2:A30, "gross_margin", "TTM")        a whole column of companies in one request
 *   =TRIBUTARY_SERIES("NVDA", "revenue", 8)          eight quarters, oldest first: period, figure
 *   =HYPERLINK(TRIBUTARY_SOURCE("NVDA", "revenue"))  the filing on EDGAR
 *
 * A figure Tributary won't show as a number (stale, n/m, n/a, another currency, nothing on file,
 * not readable with your key) is an error with the reason (#ERROR!: hover the cell), or that
 * reason as text in a range's cell. Tributary never converts currencies: give the currency
 * argument ("EUR") to read a figure in it.
 *
 * Your key stays in your Apps Script user properties and goes only to Tributary, in the
 * Authorization header. Google runs custom functions with the spreadsheet OWNER's properties:
 * anyone who can edit this spreadsheet can edit this script and so use the owner's key. Share
 * a copy without the script, or with view access only. Answers are cached for six hours
 * (Tributary > Refresh figures asks again). Each request (up to 200 figures, 25 companies) counts
 * once against the key's limits (60 a minute, 10,000 a day): prefer one range formula to many
 * single-cell ones.
 */

/** @OnlyCurrentDoc */

var TRIBUTARY_SITE = "https://tributary.lucasgroves.dev";
var TRIBUTARY_KEY = "TRIBUTARY_API_KEY";
var TRIBUTARY_GENERATION = "TRIBUTARY_GENERATION";
var TRIBUTARY_KEY_SHAPE = /^trb_[A-Za-z0-9_-]{43}$/;
var TRIBUTARY_MAX_REQUESTS = 200;
var TRIBUTARY_MAX_COMPANIES = 25;
var TRIBUTARY_MAX_COUNT = 40;
var TRIBUTARY_CACHE_SECONDS = 6 * 60 * 60;
var TRIBUTARY_NUMBERS = { current: true, behind: true, ok: true };
var TRIBUTARY_WORDS = {
  stale: "stale", old: "old", not_meaningful: "n/m", not_applicable: "n/a", not_computed: "n/a",
  other_currency: "other currency", no_figure: "no figure", not_permitted: "not permitted",
  invalid: "invalid"
};
var TRIBUTARY_SPANS = {
  "": "latest", quarter: "latest", q: "latest", latest: "latest", ttm: "TTM", fy: "FY", year: "FY",
  annual: "FY"
};
var TRIBUTARY_NO_COMPANY = "company: a ticker (NVDA) or a CIK (1045810)";
var TRIBUTARY_FIELDS = ["url", "period", "period_end", "filed", "form", "accession", "unit",
  "status", "reason"];

/** The menu. */
function onOpen() {
  SpreadsheetApp.getUi().createMenu("Tributary")
    .addItem("Set API key", "tributarySetKey")
    .addItem("Check the key", "tributaryCheckKey")
    .addItem("Refresh figures", "tributaryRefresh")
    .addItem("Remove API key", "tributaryRemoveKey")
    .addToUi();
}

/**
 * A company's figure from its SEC filings, as Tributary reads it.
 *
 * @param {string|Array<Array<string>>} company A ticker (NVDA) or a CIK, or a range of them.
 * @param {string|Array<Array<string>>} metric A metric name: revenue, net_income, gross_margin, eps_diluted...
 * @param {string|Array<Array<string>>} period latest (default), TTM, FY, FY2024, FY2024 Q3, CY2024 Q3 or a quarter's end day.
 * @param {string|Date} as_of Optional: a past day; the figure as it stood at its end.
 * @param {string} currency Optional: the currency you accept (default USD). Tributary never converts.
 * @return The figure, or the reason there is none.
 * @customfunction
 */
function TRIBUTARY(company, metric, period, as_of, currency) {
  return tributaryCells_([company, metric, period, as_of, currency], function (a) {
    return tributaryAsk_(a[0], a[1], a[2], a[3], a[4], null);
  }, tributaryNumber_);
}

/**
 * A company's figures for its newest periods, oldest first: the period and the figure per row.
 *
 * @param {string} company A ticker (NVDA) or a CIK.
 * @param {string} metric A metric name: revenue, net_income, gross_margin, eps_diluted...
 * @param {number} count How many periods, 1 to 40 (default 8).
 * @param {string} span quarter (default), TTM or FY.
 * @param {string} currency Optional: the currency you accept (default USD).
 * @return Rows of period and figure.
 * @customfunction
 */
function TRIBUTARY_SERIES(company, metric, count, span, currency) {
  var n = count === "" || count === null || count === undefined ? 8 : Number(count);
  if (!(n >= 1 && n <= TRIBUTARY_MAX_COUNT) || Math.floor(n) !== n) {
    throw new Error("count: 1 to " + TRIBUTARY_MAX_COUNT + " periods");
  }
  var s = TRIBUTARY_SPANS[tributaryText_(span).toLowerCase()];
  if (!s) throw new Error("span: quarter, TTM or FY");
  var ask = tributaryAsk_(company, metric, s, null, currency, n);
  if (ask === null) throw new Error(TRIBUTARY_NO_COMPANY);
  var answer = tributaryAnswers_([ask])[0];
  if (!answer.points) throw new Error(tributaryWhy_(answer));
  if (!answer.points.length) throw new Error("no figure: nothing on file");
  return answer.points.slice().reverse().map(function (p) {
    var v = tributaryNumber_(p);
    return [p.period || p.period_end || "", v instanceof Error ? v.message : v];
  });
}

/**
 * Where a figure comes from: its filing on EDGAR, or another field.
 *
 * @param {string|Array<Array<string>>} company A ticker (NVDA) or a CIK, or a range of them.
 * @param {string|Array<Array<string>>} metric A metric name.
 * @param {string|Array<Array<string>>} period As in TRIBUTARY (default latest).
 * @param {string|Date} as_of Optional: as in TRIBUTARY.
 * @param {string} field url (default), period, period_end, filed, form, accession, unit, status or reason.
 * @return The field.
 * @customfunction
 */
function TRIBUTARY_SOURCE(company, metric, period, as_of, field) {
  var f = tributaryText_(field).toLowerCase() || "url";
  if (TRIBUTARY_FIELDS.indexOf(f) === -1) throw new Error("field: one of " + TRIBUTARY_FIELDS.join(", "));
  return tributaryCells_([company, metric, period, as_of], function (a) {
    return tributaryAsk_(a[0], a[1], a[2], a[3], null, null);
  }, function (answer) {
    return tributaryField_(answer, f);
  });
}

// --- the menu's actions ------------------------------------------------------------------------

function tributarySetKey() {
  var ui = SpreadsheetApp.getUi();
  var got = ui.prompt("Tributary API key",
    "Paste a key from Tributary's Settings, API keys (it starts trb_). It is kept in your Apps " +
    "Script user properties and sent only to Tributary.", ui.ButtonSet.OK_CANCEL);
  if (got.getSelectedButton() !== ui.Button.OK) return;
  var key = got.getResponseText().trim();
  if (!TRIBUTARY_KEY_SHAPE.test(key)) {
    ui.alert("That isn't a Tributary API key: one starts trb_ and has 47 characters.");
    return;
  }
  var props = PropertiesService.getUserProperties();
  props.setProperty(TRIBUTARY_KEY, key);
  tributaryNextGeneration_(props);
  SpreadsheetApp.getActive().toast("Key saved (" + key.slice(0, 8) + "…). Recalculating…", "Tributary");
  tributaryRecalculate_();
}

function tributaryRemoveKey() {
  var props = PropertiesService.getUserProperties();
  props.deleteProperty(TRIBUTARY_KEY);
  tributaryNextGeneration_(props);
  SpreadsheetApp.getActive().toast("Key removed.", "Tributary");
}

function tributaryRefresh() {
  tributaryNextGeneration_(PropertiesService.getUserProperties());
  var n = tributaryRecalculate_();
  SpreadsheetApp.getActive().toast(n + " cells asked again.", "Tributary");
}

function tributaryCheckKey() {
  var ui = SpreadsheetApp.getUi();
  var key = PropertiesService.getUserProperties().getProperty(TRIBUTARY_KEY);
  if (!key) {
    ui.alert("No key saved yet: Tributary > Set API key.");
    return;
  }
  var res = UrlFetchApp.fetch(TRIBUTARY_SITE + "/api/query/v1/me", {
    headers: { Authorization: "Bearer " + key }, muteHttpExceptions: true, followRedirects: false
  });
  var code = res.getResponseCode();
  var body = tributaryJson_(res);
  if (code === 200 && body) {
    var k = body.api_key || {};
    ui.alert("The key works: “" + (k.name || "key") + "” (" + (k.prefix || "") + "), " +
      (k.scopes || []).join(" and ") + ", expires " + String(k.expires_at || "").slice(0, 10) + ".");
    return;
  }
  ui.alert(tributaryRefusal_(code, body, null).message);
}

// --- private: asks, requests, answers ----------------------------------------------------------

function tributaryText_(v) {
  return v === null || v === undefined ? "" : String(v).trim();
}

function tributaryDay_(v) {
  if (v instanceof Date) {
    return Utilities.formatDate(v, SpreadsheetApp.getActive().getSpreadsheetTimeZone(), "yyyy-MM-dd");
  }
  return tributaryText_(v);
}

/** One cell's ask as the batch route takes it (throws for an argument to fix). */
function tributaryAsk_(company, metric, period, asOf, currency, count) {
  var c = tributaryText_(company);
  if (!c) return null;
  var m = tributaryText_(metric).toLowerCase();
  if (!m) throw new Error("metric: a name such as revenue, net_income or gross_margin");
  var p;
  if (typeof period === "number" && period >= 1900 && period <= 2200 && Math.floor(period) === period) {
    p = "FY" + period;
  } else {
    p = tributaryDay_(period) || "latest";
  }
  var ask = { company: c, metric: m, period: p };
  var day = tributaryDay_(asOf);
  if (day) ask.as_of = day;
  var cur = tributaryText_(currency);
  if (cur) ask.currency = cur.toUpperCase();
  if (count) ask.count = count;
  return ask;
}

/**
 * Each cell of the arguments' shape (a range broadcasts against single values), asked in as few
 * requests as possible. One cell: its value, or an error with the reason. A range: each cell's
 * value or reason (a range can't hold errors).
 */
function tributaryCells_(args, makeAsk, show) {
  var rows = 1;
  var cols = 1;
  var ranged = false;
  args.forEach(function (a) {
    if (Array.isArray(a)) {
      ranged = true;
      if ((rows > 1 && a.length > 1 && a.length !== rows) ||
          (cols > 1 && a[0].length > 1 && a[0].length !== cols)) {
        throw new Error("ranges of different sizes: give ranges of one size, or single values");
      }
      rows = Math.max(rows, a.length);
      cols = Math.max(cols, a[0].length);
    }
  });
  function at(a, r, c) {
    if (!Array.isArray(a)) return a;
    var row = a[a.length === 1 ? 0 : r];
    return row[row.length === 1 ? 0 : c];
  }
  var asks = [];
  var errors = [];
  for (var r = 0; r < rows; r++) {
    for (var c = 0; c < cols; c++) {
      try {
        asks.push(makeAsk(args.map(function (a) { return at(a, r, c); })));
        errors.push(null);
      } catch (e) {
        if (!ranged) throw e;
        asks.push(null);
        errors.push(e.message);
      }
    }
  }
  var answers = tributaryAnswers_(asks.filter(function (a) { return a !== null; }));
  var i = 0;
  var cells = asks.map(function (a, n) {
    if (errors[n] !== null) return errors[n];
    if (a === null) {
      if (!ranged) throw new Error(TRIBUTARY_NO_COMPANY);
      return "";  // a blank row of a range stays blank
    }
    var v = show(answers[i++]);
    if (v instanceof Error) {
      if (!ranged) throw v;
      return v.message;
    }
    return v;
  });
  if (!ranged) return cells[0];
  var out = [];
  for (var k = 0; k < rows; k++) out.push(cells.slice(k * cols, (k + 1) * cols));
  return out;
}

function tributaryWhy_(answer) {
  var word = TRIBUTARY_WORDS[answer.status] || answer.status || "no figure";
  return word + (answer.reason ? ": " + answer.reason : "");
}

function tributaryNumber_(answer) {
  if (answer.value !== null && answer.value !== undefined && TRIBUTARY_NUMBERS[answer.status]) {
    var n = Number(answer.value);
    if (isFinite(n)) return n;
  }
  return new Error(tributaryWhy_(answer));
}

function tributaryField_(answer, field) {
  if (answer.status === "invalid" || answer.status === "not_permitted") return new Error(tributaryWhy_(answer));
  var c = answer.citation || {};
  var v = { url: c.filing_url, accession: c.accession_no, form: c.form, filed: c.filed,
    period: answer.period, period_end: answer.period_end, unit: answer.unit, status: answer.status,
    reason: answer.reason }[field];
  if (v === null || v === undefined || v === "") {
    return field === "reason" || field === "status" ? "" : new Error(tributaryWhy_(answer));
  }
  return String(v);
}

function tributaryCacheKey_(prefix, ask) {
  var text = JSON.stringify([ask.company.toUpperCase(), ask.metric, ask.period.toUpperCase(),
    ask.as_of || "", ask.currency || "", ask.count || 0]);
  return prefix + Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text));
}

/** Requests of at most 200 asks and 25 (company, as-of day) pairs, counted on the names. */
function tributaryChunks_(asks) {
  var out = [];
  var cur = [];
  var seen = {};
  var companies = 0;
  asks.forEach(function (a) {
    var pair = a.company.toUpperCase().replace(/\s+/g, " ") + "|" + (a.as_of || "");
    var isNew = !Object.prototype.hasOwnProperty.call(seen, pair);
    if (cur.length >= TRIBUTARY_MAX_REQUESTS || (isNew && companies >= TRIBUTARY_MAX_COMPANIES)) {
      out.push(cur);
      cur = [];
      seen = {};
      companies = 0;
      isNew = true;
    }
    if (isNew) {
      seen[pair] = true;
      companies += 1;
    }
    cur.push(a);
  });
  if (cur.length) out.push(cur);
  return out;
}

function tributaryJson_(res) {
  var headers = res.getAllHeaders ? res.getAllHeaders() : res.getHeaders();
  var type = "";
  Object.keys(headers || {}).forEach(function (h) {
    if (h.toLowerCase() === "content-type") type = String(headers[h]);
  });
  if (type.indexOf("application/json") === -1) return null;
  try {
    return JSON.parse(res.getContentText());
  } catch (e) {
    return null;
  }
}

function tributaryHeader_(res, name) {
  var headers = res.getAllHeaders ? res.getAllHeaders() : res.getHeaders();
  var out = null;
  Object.keys(headers || {}).forEach(function (h) {
    if (h.toLowerCase() === name) out = String(headers[h]);
  });
  return out;
}

function tributaryRefusal_(code, body, retryAfter) {
  var detail = body && typeof body.detail === "string" ? body.detail : "";
  if (code === 401) {
    return new Error("Tributary refused the API key (unknown, expired or revoked): make a new one " +
      "in Tributary's Settings, then Tributary > Set API key");
  }
  if (code === 403 && body) return new Error(detail || "this API key can't read figures");
  if (code === 422 && body) return new Error(detail || "Tributary didn't take the request");
  if (code === 429) {
    return new Error("over the API key's limit" + (retryAfter ? ": try again in " + retryAfter +
      " seconds" : "") + " (use one range formula instead of many single cells)");
  }
  if (!body) {
    return new Error("the site answered " + code + " without the API's answer (a proxy or " +
      "firewall page, such as Cloudflare's): tell Tributary's owner if it lasts");
  }
  return new Error("Tributary couldn't answer (" + code + "): try again in a moment");
}

function tributaryPost_(key, asks) {
  return {
    url: TRIBUTARY_SITE + "/api/query/v1/sheets/values",
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ requests: asks }),
    headers: { Authorization: "Bearer " + key },
    muteHttpExceptions: true,
    followRedirects: false
  };
}

/** The API's answers for these asks, in order: from the cache, else in as few requests as can
 * carry them (sent together). Throws for a refusal of the whole request. */
function tributaryAnswers_(asks) {
  if (!asks.length) return [];
  var props = PropertiesService.getUserProperties();
  var key = props.getProperty(TRIBUTARY_KEY);
  if (!key) throw new Error("no API key: Tributary > Set API key (in the menu)");
  var prefix = "trb1|" + key.slice(0, 8) + "|" + (props.getProperty(TRIBUTARY_GENERATION) || "0") + "|";
  var cache = CacheService.getScriptCache();
  var keys = asks.map(function (a) { return tributaryCacheKey_(prefix, a); });
  var hits = cache.getAll(keys) || {};
  var answers = asks.map(function (a, i) {
    return hits[keys[i]] ? JSON.parse(hits[keys[i]]) : null;
  });
  var todo = [];
  asks.forEach(function (a, i) { if (answers[i] === null) todo.push(i); });
  if (!todo.length) return answers;
  var parts = tributaryChunks_(todo.map(function (i) { return asks[i]; }));
  var responses = UrlFetchApp.fetchAll(parts.map(function (p) { return tributaryPost_(key, p); }));
  var fresh = {};
  var at = 0;
  parts.forEach(function (part, n) {
    var res = responses[n];
    var code = res.getResponseCode();
    var retry = parseInt(tributaryHeader_(res, "retry-after") || "", 10);
    if (code === 429 && retry > 0 && retry <= 10) {  // a custom function has 30 seconds in all
      Utilities.sleep(retry * 1000);
      res = UrlFetchApp.fetch(tributaryPost_(key, part).url, tributaryPost_(key, part));
      code = res.getResponseCode();
      retry = parseInt(tributaryHeader_(res, "retry-after") || "", 10);
    }
    var body = tributaryJson_(res);
    if (code !== 200 || !body || !Array.isArray(body.values) || body.values.length !== part.length) {
      throw tributaryRefusal_(code === 200 ? 502 : code, body, retry > 0 ? retry : null);
    }
    body.values.forEach(function (answer, j) {
      var i = todo[at + j];
      answers[i] = answer;
      fresh[keys[i]] = JSON.stringify(answer);
    });
    at += part.length;
  });
  try {
    cache.putAll(fresh, TRIBUTARY_CACHE_SECONDS);
  } catch (e) {
    // a full cache only costs a request next time
  }
  return answers;
}

function tributaryNextGeneration_(props) {
  var n = parseInt(props.getProperty(TRIBUTARY_GENERATION) || "0", 10) + 1;
  props.setProperty(TRIBUTARY_GENERATION, String(n));
}

/** Sheets keeps a custom function's result until its arguments change: set every TRIBUTARY
 * formula again so each asks Tributary again. Returns how many cells. */
function tributaryRecalculate_() {
  var n = 0;
  SpreadsheetApp.getActive().getSheets().forEach(function (sheet) {
    var range = sheet.getDataRange();
    var formulas = range.getFormulas();
    var found = [];
    formulas.forEach(function (row, r) {
      row.forEach(function (f, c) {
        if (f && /TRIBUTARY/i.test(f)) found.push([r + 1, c + 1, f]);
      });
    });
    found.forEach(function (x) { sheet.getRange(x[0], x[1]).setValue(""); });
    SpreadsheetApp.flush();
    found.forEach(function (x) { sheet.getRange(x[0], x[1]).setFormula(x[2]); });
    n += found.length;
  });
  return n;
}
