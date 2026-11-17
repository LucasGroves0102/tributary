/* Tributary's Excel custom functions: =TRIBUTARY.VALUE, =TRIBUTARY.SERIES and =TRIBUTARY.SOURCE.
 *
 * Every cell's ask joins a queue; the queue goes to the Query API's batch route
 * (POST /api/query/v1/sheets/values, same origin as this page) a moment later, at most 200 asks
 * and 25 companies (and as-of days) per request, one request at a time. Each request counts once
 * against the API key's limits. Answers are kept for ten minutes (the task pane's Refresh clears
 * them). The API decides every reading: a figure it won't show as a number (stale, n/m, n/a, old,
 * another currency, no figure, not permitted) becomes #N/A with its reason, a bad argument #VALUE!.
 *
 * The API key lives only in OfficeRuntime.storage (the task pane saves it) and goes only in the
 * Authorization header of requests to this site's own API: never in a URL, a log or a cell.
 *
 * A classic script on purpose (the custom functions runtime loads it as one); tests run this exact
 * file (services/web/src/addins/functions.test.ts). */
(function (root) {
  "use strict";

  var ENDPOINT = "/api/query/v1/sheets/values";
  var KEY_ITEM = "tributary.apiKey";
  var MAX_REQUESTS = 200;
  var MAX_COMPANIES = 25;
  var MAX_COUNT = 40;
  var WAIT_MS = 50;           // how long a cell waits for the cells beside it
  var KEEP_MS = 10 * 60 * 1000;
  var MAX_RETRY_WAIT = 60;    // seconds; a longer Retry-After is an error, not a wait
  var RETRIES = 2;
  var NUMBERS = { current: true, behind: true, ok: true };
  var WORDS = {
    stale: "stale", old: "old", not_meaningful: "n/m", not_applicable: "n/a",
    not_computed: "n/a", other_currency: "other currency", no_figure: "no figure",
    not_permitted: "not permitted", invalid: "invalid"
  };

  /** An error a cell shows: kind "value" (#VALUE!, the arguments) or "na" (#N/A, no number). */
  function Problem(kind, message) {
    this.kind = kind;
    this.message = String(message || "").slice(0, 250);
  }

  function excelSerialToDay(n) {
    var ms = Date.UTC(1899, 11, 30) + Math.round(n) * 86400000;
    return new Date(ms).toISOString().slice(0, 10);
  }

  function text(v) {
    return v === null || v === undefined ? "" : String(v).trim();
  }

  /** A period argument: blank is latest; 2024 is FY2024; a date (Excel's serial) its day. */
  function periodArg(v) {
    if (typeof v === "number") {
      if (v >= 1900 && v <= 2200 && Math.floor(v) === v) return "FY" + v;
      if (v > 2200) return excelSerialToDay(v);
      throw new Problem("value", "period: latest, TTM, FY, FY2024, FY2024 Q3, CY2024 Q3 or a day");
    }
    return text(v) || "latest";
  }

  function dayArg(v) {
    if (typeof v === "number") return excelSerialToDay(v);
    return text(v) || null;
  }

  /** One cell's ask, as the batch route takes it. */
  function makeAsk(company, metric, period, asOf, currency, count) {
    var c = text(company);
    if (!c) throw new Problem("value", "company: a ticker (NVDA) or a CIK (1045810)");
    var m = text(metric).toLowerCase();
    if (!m) throw new Problem("value", "metric: a name such as revenue, net_income or gross_margin");
    var ask = { company: c, metric: m, period: periodArg(period) };
    var day = dayArg(asOf);
    if (day) ask.as_of = day;
    var cur = text(currency);
    if (cur) ask.currency = cur.toUpperCase();
    if (count !== undefined && count !== null) ask.count = count;
    return ask;
  }

  function cacheKey(ask) {
    return JSON.stringify([ask.company.toUpperCase(), ask.metric, ask.period.toUpperCase(),
      ask.as_of || "", ask.currency || "", ask.count || 0]);
  }

  /** Splits asks into requests of at most MAX_REQUESTS asks and MAX_COMPANIES (company, as_of)
   * pairs, counted on the names as typed (as the API counts them). */
  function chunk(asks) {
    var out = [];
    var cur = [];
    var seen = {};
    var companies = 0;
    asks.forEach(function (a) {
      var pair = a.company.toUpperCase().replace(/\s+/g, " ") + "|" + (a.as_of || "");
      var isNew = !Object.prototype.hasOwnProperty.call(seen, pair);
      if (cur.length >= MAX_REQUESTS || (isNew && companies >= MAX_COMPANIES)) {
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

  function detailOf(body) {
    if (body && typeof body.detail === "string") return body.detail;
    return "";
  }

  /** A refusal of the whole request, in words for a cell. */
  function refusal(status, body, retryAfter) {
    var detail = detailOf(body);
    if (status === 0) return new Problem("na", "Tributary didn't answer: check the connection and recalculate");
    if (status === 429) {
      return new Problem("na", "over the API key's limit" + (retryAfter ? ": recalculate in " +
        retryAfter + " seconds" : ""));
    }
    if (body === null) {  // not the API's JSON: a proxy's or a firewall's page (Cloudflare's 1010)
      return new Problem("na", "the site answered " + status + " without the API's answer (a proxy " +
        "or firewall page): recalculate, and tell Tributary's owner if it lasts");
    }
    if (status === 401) {
      return new Problem("value", "Tributary refused the API key (unknown, expired or revoked): make a new " +
        "one in Tributary's Settings and paste it in the task pane");
    }
    if (status === 403) return new Problem("value", detail || "this API key can't read figures");
    if (status === 422) return new Problem("value", detail || "Tributary didn't take the request");
    return new Problem("na", "Tributary couldn't answer (" + status + "): recalculate in a moment");
  }

  /**
   * The queue and the requests. deps: fetch (the browser's), getKey (a promise of the saved key
   * or null), sleep(ms), now(), schedule(fn, ms). Returns {ask(ask) -> promise of the API's
   * answer for it, clear()}.
   */
  function createClient(deps) {
    var queue = [];
    var timer = null;
    var cache = {};
    var sending = Promise.resolve();

    function readJson(res) {
      var type = (res.headers && res.headers.get && res.headers.get("content-type")) || "";
      if (type.indexOf("application/json") === -1) return Promise.resolve(null);
      return res.json().catch(function () { return null; });
    }

    function post(key, asks, tries) {
      return deps.fetch(ENDPOINT, {
        method: "POST",
        headers: { "Authorization": "Bearer " + key, "Content-Type": "application/json" },
        body: JSON.stringify({ requests: asks }),
        credentials: "omit",
        redirect: "error"
      }).then(function (res) {
        return readJson(res).then(function (body) {
          if (res.status === 200 && body && Array.isArray(body.values) && body.values.length === asks.length) {
            return body.values;
          }
          var retry = parseInt((res.headers && res.headers.get && res.headers.get("retry-after")) || "", 10);
          if (res.status === 429 && tries < RETRIES && retry > 0 && retry <= MAX_RETRY_WAIT) {
            return deps.sleep(retry * 1000).then(function () { return post(key, asks, tries + 1); });
          }
          throw refusal(res.status === 200 ? 502 : res.status, body, retry > 0 ? retry : null);
        });
      }, function (e) {
        if (e instanceof Problem) throw e;
        throw refusal(0, null, null);
      });
    }

    function sendAll(items) {
      return deps.getKey().then(function (key) {
        if (!key) {
          var none = new Problem("value", "no API key: open Tributary on the Home tab and paste your key");
          items.forEach(function (it) { it.reject(none); });
          return;
        }
        var parts = chunk(items.map(function (it) { return it.ask; }));
        var at = 0;
        var chain = Promise.resolve();
        parts.forEach(function (part) {
          var mine = items.slice(at, at + part.length);
          at += part.length;
          chain = chain.then(function () {
            return post(key, part, 0).then(function (answers) {
              mine.forEach(function (it, i) {
                cache[cacheKey(it.ask)] = { at: deps.now(), answer: answers[i] };
                it.resolve(answers[i]);
              });
            }, function (e) {
              mine.forEach(function (it) { it.reject(e instanceof Problem ? e : refusal(0, null, null)); });
            });
          });
        });
        return chain;
      }, function () {
        var none = new Problem("value", "couldn't read the saved API key: paste it again in the task pane");
        items.forEach(function (it) { it.reject(none); });
      });
    }

    function flush() {
      timer = null;
      var items = queue;
      queue = [];
      // one batch at a time: never two requests of this workbook racing the key's limits
      sending = sending.then(function () { return sendAll(items); });
    }

    function ask(a) {
      var hit = cache[cacheKey(a)];
      if (hit && deps.now() - hit.at < KEEP_MS) return Promise.resolve(hit.answer);
      return new Promise(function (resolve, reject) {
        queue.push({ ask: a, resolve: resolve, reject: reject });
        if (timer === null) timer = deps.schedule(flush, WAIT_MS);
      });
    }

    function clear() {
      cache = {};
    }

    return { ask: ask, clear: clear };
  }

  /** What a cell shows for one answer: a number, or a Problem with the API's reason. */
  function cellValue(answer) {
    if (answer.value !== null && answer.value !== undefined && NUMBERS[answer.status]) {
      var n = Number(answer.value);
      if (isFinite(n)) return n;
    }
    var word = WORDS[answer.status] || answer.status || "no figure";
    var kind = answer.status === "invalid" ? "value" : "na";
    return new Problem(kind, word + (answer.reason ? ": " + answer.reason : ""));
  }

  var SOURCE_FIELDS = {
    url: function (a) { return a.citation && a.citation.filing_url; },
    filing: function (a) { return a.citation && a.citation.filing_url; },
    accession: function (a) { return a.citation && a.citation.accession_no; },
    form: function (a) { return a.citation && a.citation.form; },
    filed: function (a) { return a.citation && a.citation.filed; },
    period: function (a) { return a.period; },
    period_end: function (a) { return a.period_end; },
    unit: function (a) { return a.unit; },
    status: function (a) { return a.status; },
    reason: function (a) { return a.reason; }
  };

  function sourceField(field) {
    var f = text(field).toLowerCase() || "url";
    if (!SOURCE_FIELDS[f]) {
      throw new Problem("value", "field: one of " + Object.keys(SOURCE_FIELDS).join(", "));
    }
    return f;
  }

  function sourceValue(answer, field) {
    var f = sourceField(field);
    var pick = SOURCE_FIELDS[f];
    if (answer.status === "invalid" || answer.status === "not_permitted") return cellValue(answer);
    var v = pick(answer);
    if (v === null || v === undefined || v === "") {
      if (f === "reason" || f === "status") return "";
      return new Problem("na", (WORDS[answer.status] || "no figure") + (answer.reason ? ": " + answer.reason : ""));
    }
    return String(v);
  }

  var SPANS = { "": "latest", quarter: "latest", q: "latest", latest: "latest", ttm: "TTM", fy: "FY", year: "FY", annual: "FY" };

  /** A series' rows, oldest first: [period, value] (a value it won't show is that point's #N/A). */
  function seriesRows(answer) {
    if (!answer.points) return [[cellValue(answer), ""]];
    if (!answer.points.length) return [[new Problem("na", "no figure: nothing on file"), ""]];
    return answer.points.slice().reverse().map(function (p) {
      return [p.period || p.period_end || "", cellValue(p)];
    });
  }

  /** The three functions, over a client. toError(problem) turns a Problem into what the host
   * shows (Excel: CustomFunctions.Error). */
  function makeFunctions(client, toError) {
    function fail(e) {
      if (e instanceof Problem) throw toError(e);
      throw toError(new Problem("na", "something went wrong: recalculate"));
    }
    function shown(v) {
      if (v instanceof Problem) throw toError(v);
      return v;
    }
    return {
      value: function (company, metric, period, asOf, currency) {
        var a;
        try { a = makeAsk(company, metric, period, asOf, currency); } catch (e) { return Promise.reject(toError(e)); }
        return client.ask(a).then(function (ans) { return shown(cellValue(ans)); }, fail);
      },
      series: function (company, metric, count, span, currency) {
        var a;
        try {
          var n = count === null || count === undefined || count === "" ? 8 : Number(count);
          if (!(n >= 1 && n <= MAX_COUNT) || Math.floor(n) !== n) {
            throw new Problem("value", "count: 1 to " + MAX_COUNT + " periods");
          }
          var s = SPANS[text(span).toLowerCase()];
          if (!s) throw new Problem("value", "span: quarter, TTM or FY");
          a = makeAsk(company, metric, s, null, currency, n);
        } catch (e) { return Promise.reject(toError(e)); }
        return client.ask(a).then(function (ans) {
          return seriesRows(ans).map(function (row) {
            return row.map(function (v) { return v instanceof Problem ? toError(v) : v; });
          });
        }, fail);
      },
      source: function (company, metric, period, asOf, field) {
        var a;
        try {
          a = makeAsk(company, metric, period, asOf, null);
          sourceField(field);
        } catch (e) { return Promise.reject(toError(e)); }
        return client.ask(a).then(function (ans) { return shown(sourceValue(ans, field)); }, fail);
      }
    };
  }

  var api = {
    ENDPOINT: ENDPOINT, KEY_ITEM: KEY_ITEM, MAX_REQUESTS: MAX_REQUESTS, MAX_COMPANIES: MAX_COMPANIES,
    Problem: Problem, chunk: chunk, makeAsk: makeAsk, cellValue: cellValue, sourceValue: sourceValue,
    seriesRows: seriesRows, createClient: createClient, makeFunctions: makeFunctions,
    excelSerialToDay: excelSerialToDay
  };
  root.TributaryAddin = api;

  // In Excel: register the functions on the shared runtime's client.
  if (typeof root.CustomFunctions !== "undefined" && typeof root.OfficeRuntime !== "undefined") {
    var CF = root.CustomFunctions;
    var client = createClient({
      fetch: function (url, init) { return root.fetch(url, init); },
      getKey: function () { return root.OfficeRuntime.storage.getItem(KEY_ITEM); },
      sleep: function (ms) { return new Promise(function (r) { root.setTimeout(r, ms); }); },
      now: function () { return Date.now(); },
      schedule: function (fn, ms) { return root.setTimeout(fn, ms); }
    });
    api.client = client;
    var fns = makeFunctions(client, function (p) {
      var code = p.kind === "value" ? CF.ErrorCode.invalidValue : CF.ErrorCode.notAvailable;
      return new CF.Error(code, p.message);
    });
    CF.associate("VALUE", fns.value);
    CF.associate("SERIES", fns.series);
    CF.associate("SOURCE", fns.source);
  }
})(typeof self !== "undefined" ? self : this);
