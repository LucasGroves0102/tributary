/** The spreadsheet add-ins (public/addins): Excel's functions.js and Google Sheets' Tributary.gs run
 * here as served (each file evaluated as it is, with fakes for the host's services), the Excel
 * manifest checked against the files it names, and the add-in pages' headers against the app's.
 * No call leaves this process. */
import { readFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import vm from "node:vm";
import { describe, expect, it, vi } from "vitest";

const ROOT = `${process.cwd()}/public/addins`;
const FUNCTIONS_JS = readFileSync(`${ROOT}/excel/functions.js`, "utf8");
const SHEETS_GS = readFileSync(`${ROOT}/sheets/Tributary.gs`, "utf8");
const KEY = "trb_" + "k".repeat(43); // a fake key: right shape, not a key

type Ask = { company: string; metric: string; period: string; as_of?: string; currency?: string; count?: number };
type Answer = Record<string, unknown>;

/** What the batch route answers for an ask (only what the clients read). */
function answerFor(a: Ask): Answer {
  if (a.company === "NOPE") return { status: "not_permitted", reason: "not permitted to read financials for this company", value: null };
  if (a.metric === "net_margin") return { status: "stale", reason: "latest figures are for Jun 30, 2024", value: null, citation: { filing_url: "https://www.sec.gov/x/" } };
  if (a.count) {
    return {
      status: "ok", value: null, reason: null,
      points: [
        { period: "FY2027 Q2", status: "ok", value: "46743000000" },
        { period: "FY2027 Q1", status: "not_meaningful", value: null, reason: "negative equity" },
        { period: "FY2026 Q4", status: "ok", value: "39331000000" },
      ],
    };
  }
  return {
    status: "current", reason: null, value: a.company === "TSLA" ? "25500000000" : "46743000000", unit: "USD",
    period: "FY2027 Q2", period_end: "2026-07-26",
    citation: { accession_no: "0001045810-26-000075", form: "10-Q", filed: "2026-08-26", filing_url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/" },
  };
}

// --- Excel: functions.js ------------------------------------------------------------------------

type Problem = { kind: "value" | "na"; message: string };
type Client = { ask: (a: Ask) => Promise<Answer>; clear: () => void };
interface Addin {
  Problem: new (kind: string, message: string) => Problem;
  chunk: (asks: Ask[]) => Ask[][];
  makeAsk: (...args: unknown[]) => Ask;
  cellValue: (a: Answer) => number | Problem;
  createClient: (deps: object) => Client;
  makeFunctions: (client: Client, toError: (p: Problem) => unknown) => {
    value: (...args: unknown[]) => Promise<unknown>;
    series: (...args: unknown[]) => Promise<unknown[][]>;
    source: (...args: unknown[]) => Promise<unknown>;
  };
  excelSerialToDay: (n: number) => string;
  client?: Client;
}

function loadAddin(extra: Record<string, unknown> = {}): Addin {
  const ctx = vm.createContext({ ...extra });
  vm.runInContext(FUNCTIONS_JS, ctx, { filename: "functions.js" });
  return (ctx as unknown as { TributaryAddin: Addin }).TributaryAddin;
}

function response(status: number, body: unknown, headers: Record<string, string> = {}) {
  const h = new Map(Object.entries({ "content-type": "application/json", ...headers }).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    status,
    headers: { get: (k: string) => h.get(k.toLowerCase()) ?? null },
    json: () => Promise.resolve(body),
  };
}

/** A client whose fetch answers like the batch route; schedule runs on demand (flushNow). */
function harness(opts: { key?: string | null; reply?: (asks: Ask[], n: number) => ReturnType<typeof response> } = {}) {
  const addin = loadAddin();
  const sent: { url: string; init: RequestInit; asks: Ask[] }[] = [];
  const sleeps: number[] = [];
  let pending: (() => void) | null = null;
  let clock = 0;
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const asks = JSON.parse(String(init.body)).requests as Ask[];
    sent.push({ url, init, asks });
    const r = opts.reply ? opts.reply(asks, sent.length) : response(200, { values: asks.map(answerFor) });
    return Promise.resolve(r);
  });
  const client = addin.createClient({
    fetch,
    getKey: () => Promise.resolve(opts.key === undefined ? KEY : opts.key),
    sleep: (ms: number) => { sleeps.push(ms); return Promise.resolve(); },
    now: () => clock,
    schedule: (fn: () => void) => { pending = fn; return 1; },
  });
  const flushNow = async () => {
    const fn = pending;
    pending = null;
    fn?.();
    for (let i = 0; i < 20; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  };
  return { addin, client, fetch, sent, sleeps, flushNow, tick: (ms: number) => { clock += ms; } };
}

const NVDA_REV: Ask = { company: "NVDA", metric: "revenue", period: "latest" };

/** A promise's outcome, handled at once (a rejection waiting for the flush isn't "unhandled"). */
function settle<T>(p: Promise<T>): Promise<{ ok: T } | { error: unknown }> {
  return p.then((ok) => ({ ok }), (error: unknown) => ({ error }));
}

describe("Excel functions: batching", () => {
  it("sends every cell asked together in one request, the key only in the Authorization header", async () => {
    const h = harness();
    const asks = ["NVDA", "TSLA", "NVDA"].map((c, i) => h.client.ask({ ...NVDA_REV, company: c, period: i === 2 ? "FY2024" : "latest" }));
    await h.flushNow();
    const got = await Promise.all(asks);
    expect(h.fetch).toHaveBeenCalledTimes(1);
    expect(h.sent[0].url).toBe("/api/query/v1/sheets/values");
    expect(h.sent[0].asks.map((a) => a.company)).toEqual(["NVDA", "TSLA", "NVDA"]);
    expect((h.sent[0].init.headers as Record<string, string>).Authorization).toBe(`Bearer ${KEY}`);
    expect(String(h.sent[0].init.body)).not.toContain(KEY);
    expect(h.sent[0].init.credentials).toBe("omit");
    expect(h.sent[0].init.redirect).toBe("error");
    expect(got.map((a) => a.value)).toEqual(["46743000000", "25500000000", "46743000000"]);
  });

  it("splits a workbook into requests of 200 asks and 25 companies, one request at a time", () => {
    const { chunk } = loadAddin();
    const one = Array.from({ length: 450 }, () => NVDA_REV);
    expect(chunk(one).map((c) => c.length)).toEqual([200, 200, 50]);
    const many = Array.from({ length: 30 }, (_, i) => ({ ...NVDA_REV, company: `T${i}` }));
    expect(chunk(many).map((c) => c.length)).toEqual([25, 5]);
    // a company as of another day counts as another; a ticker typed in another case doesn't
    const days = Array.from({ length: 26 }, (_, i) => ({ ...NVDA_REV, company: i % 2 ? "nvda" : "NVDA", as_of: `2024-01-${String(i + 1).padStart(2, "0")}` }));
    expect(chunk(days).map((c) => c.length)).toEqual([25, 1]);
    expect(chunk([NVDA_REV, { ...NVDA_REV, company: "nvda" }])).toHaveLength(1);
  });

  it("keeps answers ten minutes, then asks again; clear() asks again at once", async () => {
    const h = harness();
    const first = h.client.ask(NVDA_REV);
    await h.flushNow();
    await first;
    await h.client.ask(NVDA_REV);
    expect(h.fetch).toHaveBeenCalledTimes(1);
    h.tick(10 * 60 * 1000);
    const later = h.client.ask(NVDA_REV);
    await h.flushNow();
    await later;
    expect(h.fetch).toHaveBeenCalledTimes(2);
    h.client.clear();
    const cleared = h.client.ask(NVDA_REV);
    await h.flushNow();
    await cleared;
    expect(h.fetch).toHaveBeenCalledTimes(3);
  });

  it("without a key asks nothing and says where to paste one", async () => {
    const h = harness({ key: null });
    const p = settle(h.client.ask(NVDA_REV));
    await h.flushNow();
    expect(await p).toMatchObject({ error: { kind: "value", message: expect.stringContaining("no API key") } });
    expect(h.fetch).not.toHaveBeenCalled();
  });

  it("waits out a short Retry-After, and words a long one", async () => {
    const h = harness({ reply: (asks, n) => (n === 1 ? response(429, { detail: "limit" }, { "retry-after": "3" }) : response(200, { values: asks.map(answerFor) })) });
    const p = h.client.ask(NVDA_REV);
    await h.flushNow();
    await expect(p).resolves.toMatchObject({ status: "current" });
    expect(h.sleeps).toEqual([3000]);
    const slow = harness({ reply: () => response(429, { detail: "limit" }, { "retry-after": "120" }) });
    const q = settle(slow.client.ask(NVDA_REV));
    await slow.flushNow();
    expect(await q).toEqual({ error: expect.objectContaining({ kind: "na", message: "over the API key's limit: recalculate in 120 seconds" }) });
    expect(slow.sleeps).toEqual([]);
  });

  it.each([
    [response(401, { detail: "invalid token" }), "value", "refused the API key"],
    [response(403, { detail: "API keys can't use this route: sign in to Tributary" }), "value", "sign in to Tributary"],
    [response(503, { detail: "authorization unavailable" }), "na", "couldn't answer (503)"],
    [{ ...response(403, null, { "content-type": "text/html" }), json: () => Promise.reject(new Error("html")) }, "na", "without the API's answer"],
    [response(200, { values: [] }), "na", "couldn't answer"],
  ])("a refused request is every cell's error, worded (%#)", async (res, kind, words) => {
    const h = harness({ reply: () => res as ReturnType<typeof response> });
    const p = settle(h.client.ask(NVDA_REV));
    await h.flushNow();
    expect(await p).toMatchObject({ error: { kind, message: expect.stringContaining(words) } });
  });

  it("a network failure is #N/A, never a crash", async () => {
    const addin = loadAddin();
    let run: () => void = () => {};
    const client = addin.createClient({
      fetch: () => Promise.reject(new TypeError("Failed to fetch")),
      getKey: () => Promise.resolve(KEY), sleep: () => Promise.resolve(), now: () => 0,
      schedule: (fn: () => void) => { run = fn; return 1; },
    });
    const p = settle(client.ask(NVDA_REV));
    run();
    expect(await p).toMatchObject({ error: { kind: "na", message: expect.stringContaining("didn't answer") } });
  });
});

describe("Excel functions: cells", () => {
  const toError = (p: Problem) => ({ error: p.kind, message: p.message });

  function functions() {
    const h = harness();
    return { h, fns: h.addin.makeFunctions(h.client, toError) };
  }

  it("a number for a figure the API shows; the API's reason otherwise", async () => {
    const { h, fns } = functions();
    const v = settle(fns.value("NVDA", "Revenue"));
    const stale = settle(fns.value("NVDA", "net_margin"));
    const denied = settle(fns.value("NOPE", "revenue"));
    await h.flushNow();
    expect(await v).toEqual({ ok: 46743000000 });
    expect(await stale).toEqual({ error: { error: "na", message: "stale: latest figures are for Jun 30, 2024" } });
    expect(await denied).toEqual({ error: { error: "na", message: "not permitted: not permitted to read financials for this company" } });
    expect(h.sent[0].asks[0].metric).toBe("revenue");
  });

  it("an invalid answer and a blank argument are #VALUE!", async () => {
    const { h, fns } = functions();
    expect(h.addin.cellValue({ status: "invalid", reason: "unknown metric 'revnue'", value: null })).toMatchObject({ kind: "value" });
    await expect(fns.value("", "revenue")).rejects.toMatchObject({ error: "value" });
    await expect(fns.value("NVDA", "")).rejects.toMatchObject({ error: "value" });
    await expect(fns.source("NVDA", "revenue", "", "", "colour")).rejects.toMatchObject({ error: "value" });
    await expect(fns.series("NVDA", "revenue", 41)).rejects.toMatchObject({ error: "value" });
    await expect(fns.series("NVDA", "revenue", 8, "monthly")).rejects.toMatchObject({ error: "value" });
    expect(h.fetch).not.toHaveBeenCalled();
  });

  it("reads Excel's numbers: a year is a fiscal year, a date serial a day", () => {
    const { makeAsk, excelSerialToDay } = loadAddin();
    expect(excelSerialToDay(45473)).toBe("2024-06-30");
    expect(makeAsk("nvda", "revenue", 2024, 45473, "eur")).toEqual({ company: "nvda", metric: "revenue", period: "FY2024", as_of: "2024-06-30", currency: "EUR" });
    expect(makeAsk(1045810, "revenue", 45565)).toEqual({ company: "1045810", metric: "revenue", period: "2024-09-30" });
    expect(makeAsk("NVDA", "revenue", null, "")).toEqual({ company: "NVDA", metric: "revenue", period: "latest" });
  });

  it("a series spills oldest first, a point it won't show as that cell's #N/A", async () => {
    const { h, fns } = functions();
    const p = fns.series("NVDA", "revenue", 3, "TTM");
    await h.flushNow();
    expect(await p).toEqual([
      ["FY2026 Q4", 39331000000],
      ["FY2027 Q1", { error: "na", message: "n/m: negative equity" }],
      ["FY2027 Q2", 46743000000],
    ]);
    expect(h.sent[0].asks[0]).toMatchObject({ period: "TTM", count: 3 });
  });

  it("the source is the filing's link, or the field asked for, even where the figure isn't shown", async () => {
    const { h, fns } = functions();
    const url = fns.source("NVDA", "revenue");
    const filed = fns.source("NVDA", "revenue", "latest", "", "filed");
    const stale = fns.source("NVDA", "net_margin");
    const why = fns.source("NVDA", "net_margin", "", "", "reason");
    await h.flushNow();
    await expect(url).resolves.toBe("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/");
    await expect(filed).resolves.toBe("2026-08-26");
    await expect(stale).resolves.toBe("https://www.sec.gov/x/");
    await expect(why).resolves.toBe("latest figures are for Jun 30, 2024");
  });

  it("in Excel, registers the three functions with Excel's own errors", async () => {
    const associate = vi.fn();
    class CFError { constructor(public code: string, public message: string) {} }
    const addin = loadAddin({
      CustomFunctions: { associate, Error: CFError, ErrorCode: { invalidValue: "#VALUE!", notAvailable: "#N/A" } },
      OfficeRuntime: { storage: { getItem: () => Promise.resolve(null) } },
      setTimeout, fetch: vi.fn(),
    });
    expect(associate.mock.calls.map((c) => c[0])).toEqual(["VALUE", "SERIES", "SOURCE"]);
    const value = associate.mock.calls[0][1] as (...a: unknown[]) => Promise<unknown>;
    await expect(value("", "revenue")).rejects.toMatchObject({ code: "#VALUE!" });
    expect(addin.client).toBeDefined();
    const noKey = settle(value("NVDA", "revenue"));
    await new Promise((r) => setTimeout(r, 120));  // the queue's wait, then the storage read
    expect(await noKey).toMatchObject({ error: { code: "#VALUE!", message: expect.stringContaining("no API key") } });
  });
});

// --- Google Sheets: Tributary.gs ----------------------------------------------------------------

interface Sheets {
  TRIBUTARY: (...a: unknown[]) => unknown;
  TRIBUTARY_SERIES: (...a: unknown[]) => unknown;
  TRIBUTARY_SOURCE: (...a: unknown[]) => unknown;
  tributaryRefresh: () => void;
  tributarySetKey: () => void;
}

function gsResponse(code: number, body: unknown, headers: Record<string, string> = { "Content-Type": "application/json; charset=utf-8" }) {
  return {
    getResponseCode: () => code,
    getContentText: () => (typeof body === "string" ? body : JSON.stringify(body)),
    getAllHeaders: () => headers,
  };
}

type FetchParams = { url?: string; payload: string; headers: Record<string, string>; method: string; followRedirects: boolean; muteHttpExceptions: boolean };

function loadSheets(opts: { key?: string | null; reply?: (asks: Ask[]) => ReturnType<typeof gsResponse>; prompt?: string } = {}) {
  const props = new Map<string, string>();
  if (opts.key !== null) props.set("TRIBUTARY_API_KEY", opts.key ?? KEY);
  const cache = new Map<string, string>();
  const batches: { url: string; params: FetchParams; asks: Ask[] }[] = [];
  const reply = (p: FetchParams) => {
    const asks = JSON.parse(p.payload).requests as Ask[];
    batches.push({ url: p.url ?? "", params: p, asks });
    return opts.reply ? opts.reply(asks) : gsResponse(200, { values: asks.map(answerFor) });
  };
  const toasts: string[] = [];
  const alerts: string[] = [];
  const formulas: Record<string, string> = { "1,1": "=TRIBUTARY(\"NVDA\",\"revenue\")", "2,1": "", "2,2": "=SUM(A1:A2)" };
  const writes: string[] = [];
  const ctx = vm.createContext({
    PropertiesService: {
      getUserProperties: () => ({
        getProperty: (k: string) => props.get(k) ?? null,
        setProperty: (k: string, v: string) => { props.set(k, v); },
        deleteProperty: (k: string) => { props.delete(k); },
      }),
    },
    CacheService: {
      getScriptCache: () => ({
        getAll: (keys: string[]) => Object.fromEntries(keys.filter((k) => cache.has(k)).map((k) => [k, cache.get(k)])),
        putAll: (values: Record<string, string>) => { for (const [k, v] of Object.entries(values)) cache.set(k, v); },
      }),
    },
    UrlFetchApp: {
      fetchAll: (requests: FetchParams[]) => requests.map(reply),
      fetch: (url: string, params: FetchParams) => reply({ ...params, url }),
    },
    Utilities: {
      DigestAlgorithm: { SHA_256: "sha256" },
      computeDigest: (_: string, text: string) => [...createHash("sha256").update(text).digest()],
      base64Encode: (bytes: number[]) => Buffer.from(bytes).toString("base64"),
      formatDate: (d: Date) => d.toISOString().slice(0, 10),
      sleep: vi.fn(),
    },
    SpreadsheetApp: {
      getActive: () => ({
        getSpreadsheetTimeZone: () => "UTC",
        toast: (t: string) => toasts.push(t),
        getSheets: () => [{
          getDataRange: () => ({ getFormulas: () => [[formulas["1,1"], ""], [formulas["2,1"], formulas["2,2"]]] }),
          getRange: (r: number, c: number) => ({
            setValue: (v: string) => writes.push(`${r},${c}=${v}`),
            setFormula: (f: string) => writes.push(`${r},${c}:${f}`),
          }),
        }],
      }),
      getUi: () => ({
        prompt: () => ({ getSelectedButton: () => "OK", getResponseText: () => opts.prompt ?? "" }),
        alert: (t: string) => alerts.push(t),
        Button: { OK: "OK" },
        ButtonSet: { OK_CANCEL: 1 },
      }),
      flush: () => writes.push("flush"),
    },
  });
  vm.runInContext(SHEETS_GS, ctx, { filename: "Tributary.gs" });
  /** A date made in the script's own realm, as Sheets passes a date cell. */
  const day = (iso: string) => vm.runInContext(`new Date("${iso}T00:00:00Z")`, ctx) as Date;
  return { gs: ctx as unknown as Sheets, props, cache, batches, toasts, alerts, writes, day };
}

describe("Google Sheets script", () => {
  it("one cell: the figure, or an error with the API's reason", () => {
    const s = loadSheets();
    expect(s.gs.TRIBUTARY("NVDA", "revenue")).toBe(46743000000);
    expect(() => s.gs.TRIBUTARY("NVDA", "net_margin")).toThrow("stale: latest figures are for Jun 30, 2024");
    expect(() => s.gs.TRIBUTARY("NOPE", "revenue")).toThrow("not permitted");
    expect(() => s.gs.TRIBUTARY("", "revenue")).toThrow("company: a ticker");
  });

  it("a range is one request; its cells hold figures or reasons, blank rows stay blank", () => {
    const s = loadSheets();
    const got = s.gs.TRIBUTARY([["NVDA"], ["TSLA"], [""], ["NOPE"]], "revenue", [["latest"], ["FY2024"], [""], ["latest"]]);
    expect(got).toEqual([[46743000000], [25500000000], [""], ["not permitted: not permitted to read financials for this company"]]);
    expect(s.batches).toHaveLength(1);
    expect(s.batches[0].asks.map((a) => [a.company, a.period])).toEqual([["NVDA", "latest"], ["TSLA", "FY2024"], ["NOPE", "latest"]]);
    expect(() => s.gs.TRIBUTARY([["NVDA"], ["TSLA"]], "revenue", [["a"], ["b"], ["c"]])).toThrow("ranges of different sizes");
  });

  it("the key goes only in the Authorization header, to Tributary, never followed elsewhere", () => {
    const s = loadSheets();
    s.gs.TRIBUTARY("NVDA", "revenue", 2024, s.day("2024-06-30"), "eur");
    const { url, params, asks } = s.batches[0];
    expect(url).toBe("https://tributary.lucasgroves.dev/api/query/v1/sheets/values");
    expect(params.headers.Authorization).toBe(`Bearer ${KEY}`);
    expect(params.payload).not.toContain(KEY);
    expect(url).not.toContain(KEY);
    expect(params.followRedirects).toBe(false);
    expect(asks[0]).toEqual({ company: "NVDA", metric: "revenue", period: "FY2024", as_of: "2024-06-30", currency: "EUR" });
    // the cache's keys carry the key's first 8 characters at most
    expect([...s.cache.keys()].every((k) => !k.includes(KEY.slice(0, 9)))).toBe(true);
  });

  it("230 companies go in requests of 25 companies, sent together", () => {
    const s = loadSheets();
    const col = Array.from({ length: 230 }, (_, i) => [`T${i}`]);
    s.gs.TRIBUTARY(col, "revenue");
    expect(s.batches.map((b) => b.asks.length)).toEqual([25, 25, 25, 25, 25, 25, 25, 25, 25, 5]);
  });

  it("answers come from the cache until Refresh, which sets every TRIBUTARY formula again", () => {
    const s = loadSheets();
    s.gs.TRIBUTARY("NVDA", "revenue");
    s.gs.TRIBUTARY("NVDA", "revenue");
    expect(s.batches).toHaveLength(1);
    s.gs.tributaryRefresh();
    expect(s.writes).toEqual(["1,1=", "flush", "1,1:=TRIBUTARY(\"NVDA\",\"revenue\")"]);
    s.gs.TRIBUTARY("NVDA", "revenue");
    expect(s.batches).toHaveLength(2);
  });

  it("a series is oldest first; the source is the filing's link", () => {
    const s = loadSheets();
    expect(s.gs.TRIBUTARY_SERIES("NVDA", "revenue", 3)).toEqual([
      ["FY2026 Q4", 39331000000], ["FY2027 Q1", "n/m: negative equity"], ["FY2027 Q2", 46743000000]]);
    expect(s.gs.TRIBUTARY_SOURCE("NVDA", "revenue")).toBe("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/");
    expect(s.gs.TRIBUTARY_SOURCE("NVDA", "revenue", "", "", "form")).toBe("10-Q");
    expect(() => s.gs.TRIBUTARY_SOURCE("NVDA", "revenue", "", "", "colour")).toThrow("field: one of");
    expect(() => s.gs.TRIBUTARY_SERIES("NVDA", "revenue", 0)).toThrow("count: 1 to 40");
  });

  it.each([
    [gsResponse(401, { detail: "invalid token" }), "refused the API key"],
    [gsResponse(403, "error code: 1010", { "Content-Type": "text/plain" }), "firewall page, such as Cloudflare's"],
    [gsResponse(429, { detail: "limit" }, { "Content-Type": "application/json", "Retry-After": "40" }), "over the API key's limit: try again in 40 seconds"],
  ])("a refused request is the cell's error, worded (%#)", (res, words) => {
    const s = loadSheets({ reply: () => res });
    expect(() => s.gs.TRIBUTARY("NVDA", "revenue")).toThrow(words);
  });

  it("without a key, says where to set one; the menu keeps a key only of the right shape", () => {
    const s = loadSheets({ key: null, prompt: "not-a-key" });
    expect(() => s.gs.TRIBUTARY("NVDA", "revenue")).toThrow("no API key");
    s.gs.tributarySetKey();
    expect(s.props.has("TRIBUTARY_API_KEY")).toBe(false);
    expect(s.alerts[0]).toContain("isn't a Tributary API key");
    const ok = loadSheets({ key: null, prompt: `  ${KEY} ` });
    ok.gs.tributarySetKey();
    expect(ok.props.get("TRIBUTARY_API_KEY")).toBe(KEY);
    expect(ok.toasts[0]).toContain(KEY.slice(0, 8));
    expect(ok.toasts[0]).not.toContain(KEY.slice(0, 9));
  });
});

// --- the manifest and the headers ---------------------------------------------------------------

describe("Excel manifest", () => {
  const xml = readFileSync(`${ROOT}/excel/manifest.xml`, "utf8");
  const doc = new DOMParser().parseFromString(xml, "application/xml");

  it("is well-formed XML with a fixed id, and names only this site's add-in files, which exist", () => {
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
    expect(doc.documentElement.localName).toBe("OfficeApp");
    expect(doc.getElementsByTagName("Id")[0].textContent).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const urls = [...xml.matchAll(/(?:DefaultValue|SourceLocation)="(https:[^"]+)"/g)].map((m) => m[1]);
    expect(urls.length).toBeGreaterThan(8);
    for (const url of urls) {
      expect(url.startsWith("https://tributary.lucasgroves.dev/")).toBe(true);
      const path = new URL(url).pathname;
      if (path.startsWith("/addins/")) expect(existsSync(`${process.cwd()}/public${path}`), path).toBe(true);
    }
  });

  it("every resource it refers to is defined, and the short strings fit", () => {
    const defined = new Set([...doc.getElementsByTagName("*")].filter((e) => e.parentElement?.localName.match(/^(Images|Urls|ShortStrings|LongStrings)$/)).map((e) => e.getAttribute("id")));
    const used = [...doc.getElementsByTagName("*")].map((e) => e.getAttribute("resid")).filter(Boolean);
    expect(used.length).toBeGreaterThan(10);
    for (const id of used) expect(defined.has(id), id ?? "").toBe(true);
    for (const s of doc.getElementsByTagNameNS("http://schemas.microsoft.com/office/officeappbasictypes/1.0", "String")) {
      const long = s.parentElement?.localName === "LongStrings";
      expect((s.getAttribute("DefaultValue") ?? "").length).toBeLessThanOrEqual(long ? 250 : 125);
    }
    const ns = [...doc.getElementsByTagName("*")].find((e) => e.getAttribute("id") === "Functions.Namespace");
    expect(ns?.getAttribute("DefaultValue")).toBe("TRIBUTARY");
  });

  it("functions.json describes exactly the functions functions.js registers", () => {
    const meta = JSON.parse(readFileSync(`${ROOT}/excel/functions.json`, "utf8")) as { functions: { id: string; parameters: unknown[] }[] };
    expect(meta.functions.map((f) => f.id)).toEqual(["VALUE", "SERIES", "SOURCE"]);
    expect([...FUNCTIONS_JS.matchAll(/CF\.associate\("(\w+)"/g)].map((m) => m[1])).toEqual(["VALUE", "SERIES", "SOURCE"]);
    expect(meta.functions.map((f) => f.parameters.length)).toEqual([5, 5, 5]);
  });
});

describe("add-in headers", () => {
  const deploy = `${process.cwd()}/deploy`;
  const app = readFileSync(`${deploy}/headers.conf`, "utf8").split("\n").filter((l) => l.startsWith("add_header"));
  const addin = readFileSync(`${deploy}/addin-headers.conf`, "utf8").split("\n").filter((l) => l.startsWith("add_header"));

  it("are the app's but for the CSP, which lets Office frame the pages and load Office.js", () => {
    const notCsp = (l: string) => !l.includes("Content-Security-Policy");
    expect(addin.filter(notCsp)).toEqual(app.filter(notCsp));
    const csp = addin.find((l) => !notCsp(l)) ?? "";
    expect(csp).toContain("script-src 'self' https://appsforoffice.microsoft.com;");
    expect(csp).toContain("connect-src 'self';");
    expect(csp).not.toMatch(/unsafe-inline|unsafe-eval|frame-ancestors 'none'/);
    expect(app.find((l) => !notCsp(l))).toContain("frame-ancestors 'none'");
  });

  it("apply to /addins/ only, and the image ships them", () => {
    const nginx = readFileSync(`${deploy}/nginx.conf`, "utf8");
    expect(nginx.match(/include \/etc\/nginx\/addin-headers\.conf/g)).toHaveLength(1);
    expect(nginx).toMatch(/location \/addins\/ \{\s+include \/etc\/nginx\/addin-headers\.conf;/);
    expect(readFileSync(`${process.cwd()}/Dockerfile`, "utf8")).toContain("COPY services/web/deploy/addin-headers.conf /etc/nginx/addin-headers.conf");
  });
});
