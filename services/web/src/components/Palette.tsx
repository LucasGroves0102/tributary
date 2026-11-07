import { useEffect, useMemo, useRef, useState } from "react";

import { searchCompanies } from "../api";
import { metricLabel } from "../chart/metrics";
import { parseCommand } from "../palette/commands";
import { NAV, type NavView } from "../route";
import { useScope } from "../scope";
import { resultTicker } from "./CompanySearch";
import type { SearchResult } from "../types";

export const WATCH_PREFIX = "watch:";
const MAX_ITEMS = 9;
const SEARCH_DELAY_MS = 200;
/** Companies asked of the API: enough to fill the list, it ranks them. */
const SEARCH_LIMIT = 8;
/** With other matches (a page, an action), they keep this many of the list's rows. */
const KEEP_FOR_OTHERS = 2;
/** Items that match any text: full-text search and the assistant. */
const FIXED = new Set(["cmd", "search", "ask"]);

export interface PaletteCompany {
  companyId: string;
  name: string;
  sub: string;
}

export interface PaletteActions {
  go: (view: NavView) => void;
  company: (companyId: string) => void;
  watch: (companyId: string) => void;
  ask: (question: string) => void;
  /** the full-text search page for these words */
  searchFilings: (q: string) => void;
  /** "compare KO PEP": the first ticker is the target */
  compareTickers: (tickers: string[]) => void;
  /** "chart NVDA revenue" */
  chartTicker: (ticker: string, metric: string) => void;
  toggleTheme: () => void;
  exportWatchlist: () => void;
  openAssistant: () => void;
}

interface Item {
  key: string;
  label: string;
  sub: string;
  /** "watch:" on a company already on the watchlist: shown, marked, does nothing */
  already?: boolean;
  run: () => void;
}

interface Props {
  token: string | null;
  /** starts with "watch:" to add a company to the watchlist */
  initial: string;
  companies: PaletteCompany[];
  /** company ids on the watchlist: "watch:" marks them instead of offering them again */
  watched?: ReadonlySet<string> | null;
  actions: PaletteActions;
  onClose: () => void;
  /** every company in the catalog is readable: one that isn't has no financials or stored
   * documents here, which isn't a matter of access */
  catalogOpen?: boolean;
}

/** Ctrl/Cmd-K: jump to a company or screen, run an action, or ask the assistant. Prefix the
 * query with "watch:" to add a company to the watchlist instead of opening it. */
export function Palette({ token, initial, companies, watched, actions, onClose, catalogOpen = false }: Props) {
  const [q, setQ] = useState(initial);
  const [active, setActive] = useState(0);
  const [remote, setRemote] = useState<SearchResult[]>([]);
  const input = useRef<HTMLInputElement>(null);
  // a demo sandbox finds only its own companies (scope.ts)
  const scoped = useScope() !== null;

  const watchMode = q.startsWith(WATCH_PREFIX);
  const text = (watchMode ? q.slice(WATCH_PREFIX.length) : q).trim();

  useEffect(() => input.current?.focus(), []);

  // companies beyond the ones already known: identity is public, access is shown as a tag
  useEffect(() => {
    if (!token || text.length < 1) {
      setRemote([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      searchCompanies(token, text, scoped, SEARCH_LIMIT)
        .then((r) => live && setRemote(r))
        .catch(() => live && setRemote([]));
    }, SEARCH_DELAY_MS);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [token, text, scoped]);

  // the server's answer, in its order: it matched on a ticker or part of a name that the row's
  // label may not contain ("TSLA" finds "Tesla, Inc."), so it never goes through the text filter
  const found = useMemo(() => new Set(remote.map((r) => `c-${r.company_id}`)), [remote]);

  const items = useMemo(() => {
    const noAccess = catalogOpen ? "company · filing index only" : "company · no access";
    const known = new Map(companies.map((c) => [c.companyId, c]));
    const ranked = new Set(remote.map((r) => r.company_id));
    const all: PaletteCompany[] = [
      ...remote.map((r) => {
        const mine = known.get(r.company_id);
        const ticker = resultTicker(r);
        return {
          companyId: r.company_id,
          name: r.name,
          sub: mine?.sub ?? [ticker, r.access.length ? `company · ${r.access.join(", ")}` : noAccess]
            .filter(Boolean).join(" · "),
        };
      }),
      ...companies.filter((c) => !ranked.has(c.companyId)),
    ];
    const companyItems: Item[] = all.map((c) => {
      const already = watchMode && !!watched?.has(c.companyId);
      return {
        key: `c-${c.companyId}`,
        label: c.name,
        sub: already ? `on your watchlist · ${c.sub}` : watchMode ? `add to watchlist · ${c.sub}` : c.sub,
        already,
        run: () => {
          if (already) return;
          if (watchMode) actions.watch(c.companyId);
          else actions.company(c.companyId);
        },
      };
    });
    if (watchMode) return [...companyItems.filter((i) => !i.already), ...companyItems.filter((i) => i.already)];
    const pages: Item[] = NAV.map((n) => ({
      key: `p-${n.view}`,
      label: n.label,
      sub: `page · g${n.key}`,
      run: () => actions.go(n.view),
    }));
    const commands: Item[] = [
      { key: "a-assistant", label: "Open the assistant", sub: "action · Ctrl J", run: actions.openAssistant },
      { key: "a-watch", label: "Add a company to the watchlist", sub: "action · watch:", run: () => setQ(WATCH_PREFIX) },
      { key: "a-export", label: "Export watchlist CSV", sub: "action", run: actions.exportWatchlist },
      { key: "a-theme", label: "Switch light / dark theme", sub: "action", run: actions.toggleTheme },
    ];
    const askItem: Item[] =
      text.length > 3
        ? [{ key: "ask", label: `Ask the assistant: “${text}”`, sub: "assistant", run: () => actions.ask(text) }]
        : [];
    const searchItem: Item[] =
      text.length >= 2
        ? [{ key: "search", label: `Search filings for “${text}”`, sub: "full text · your companies", run: () => actions.searchFilings(text) }]
        : [];
    const command = parseCommand(text);
    const commandItem: Item[] = !command
      ? []
      : command.kind === "compare"
        ? [{ key: "cmd", label: `Compare ${command.tickers.join(", ")}`, sub: `action · ${command.tickers[0]} vs peers`, run: () => actions.compareTickers(command.tickers) }]
        : [{ key: "cmd", label: `Chart ${command.ticker} ${metricLabel(command.metric).toLowerCase()}`, sub: "action · chart builder", run: () => actions.chartTicker(command.ticker, command.metric) }];
    return [...commandItem, ...companyItems, ...pages, ...commands, ...searchItem, ...askItem];
  }, [companies, remote, watchMode, watched, text, actions, catalogOpen]);

  const shown = useMemo(() => {
    const needle = text.toLowerCase();
    const match = items.filter(
      (i) => FIXED.has(i.key) || found.has(i.key) || `${i.label} ${i.sub}`.toLowerCase().includes(needle),
    );
    // keep "Search filings" and "Ask the assistant" reachable even when other matches fill the
    // list, and a page or action that matches beside a full page of companies; with nothing else
    // matching, Enter searches the filings
    const cmd = match.filter((i) => i.key === "cmd");
    const fixed = match.filter((i) => FIXED.has(i.key) && i.key !== "cmd");
    const room = MAX_ITEMS - fixed.length - cmd.length;
    const firms = match.filter((i) => i.key.startsWith("c-"));
    const others = match.filter((i) => !FIXED.has(i.key) && !i.key.startsWith("c-"));
    const kept = others.slice(0, Math.max(KEEP_FOR_OTHERS, room - firms.length));
    return [...cmd, ...firms.slice(0, room - kept.length), ...kept, ...fixed];
  }, [items, text, found]);

  useEffect(() => setActive(0), [q]);

  const run = (item: Item | undefined) => {
    if (!item || item.already) return;
    if (item.key !== "a-watch") onClose();
    item.run();
  };

  return (
    <div
      className="pal"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="box" role="dialog" aria-label="Command palette">
        <input
          ref={input}
          value={q}
          placeholder="Search companies, filings, pages, actions… (watch: to add to your watchlist)"
          aria-label="Command"
          autoComplete="off"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setActive((a) => Math.min(shown.length - 1, a + 1));
            else if (e.key === "ArrowUp") setActive((a) => Math.max(0, a - 1));
            else if (e.key === "Enter") run(shown[active]);
            else if (e.key === "Escape") onClose();
            else return;
            e.preventDefault();
          }}
        />
        <ul className="pal-list" role="listbox">
          {shown.length === 0 && (
            <li className="empty">
              {watchMode
                ? "Type a company name, ticker or CIK to add it."
                : "No matches. Try a ticker, a company name or a former name."}
            </li>
          )}
          {shown.map((item, i) => (
            <li
              key={item.key}
              role="option"
              aria-selected={i === active}
              className={[i === active ? "it on" : "it", item.already ? "already" : ""].join(" ").trim()}
              aria-disabled={item.already || undefined}
              onMouseEnter={() => setActive(i)}
              onClick={() => run(item)}
            >
              <span className="grow">{item.label}</span>
              <span className="faint">{item.sub}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
