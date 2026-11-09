# web

Chat and explore UI (React 19, TypeScript, Vite), served by nginx. It talks to the
[Query API](../query-api) and [agent-api](../agent) through same-origin paths, so it needs no
CORS:

| Path | Proxied to |
|---|---|
| `/api/query/*` | Query API (`query-api:8000`) |
| `/api/agent/*` | agent-api (`agent-api:8001`), unbuffered for server-sent events |

Every screen sets the browser tab's title (`src/title.ts`: "Nvidia (NVDA) · Statements · Tributary").
A page that fails to render shows "This page didn't load" with Reload and Home, keeping the shell
(`components/ErrorBoundary.tsx`, around the page and around the whole app; the error goes to the
console, never on screen). The "demo" badge shows only on a sandbox; the app names an account the way a
person knows it (`access.ts` `accountName`: "morgan", not the API's "pw-morgan").

The shell follows prototype v3: a sidebar (screens with `g`-then-letter shortcuts, watchlist
with revenue sparklines, next expected filings, freshness), a top bar (breadcrumb, Ctrl/Cmd-K
command palette, theme, demo user) and the assistant drawer on every page (Ctrl/Cmd-J toggles,
`/` focuses; the top bar's Assistant is the way in, closed until asked for and then remembered per
browser). Dark is the default theme; the choice is kept per user in localStorage. Design
tokens (colours for both themes, type scale, spacing) are in `src/tokens.css`.

Layout: pages lay out by the room they have, not the window's width, because the assistant takes
372px at any window size: `.main` is a CSS container and `@container main` rules reflow it (the
same rules with the panel open at 1440 as at 1024). Above 1200px the panel sits beside the page;
at or below that it floats over it; under 600px it is full screen. The sidebar has three parts: the
logo and screens stay put, the lists scroll between them (on a phone they open at the top), and the
footer is always whole, with the freshness detail behind its summary line. Nothing floats over a
page. Every data table is a `components/TableScroll`: a table that doesn't fit says how many columns
are off screen on each side and moves to them, shows a shadow on an edge with more beyond it and on a
frozen name column once something is under it, blanks a column showing only in part (a number is
never read with digits missing), and takes arrow keys once focused; a statement opens at its newest
periods. Numeric headers stack their label over their unit, so a column is as wide as
its numbers, and as a table narrows the wordy columns give way first (the screener's industry and
Peers' "Why" move under the name; the watchlist's period end and Compare's fiscal label go to the
hover). Dollars in a statement, segment or revenue row share the largest value's unit (`format.ts`
`moneyRow`: $0.05M, $0.96M, $2.43M; a figure that would round to nothing keeps its own, and the hover
has the exact value). A company header shows its main ticker and "+8 more", with the rest in the hover.
Charts are drawn for the width they have (`chart/layout.ts` `chartSize`), so their text is its real
size at any width and titles and footnotes wrap instead of being cut; `svgRef` always gets the
standard-size drawing, so Export SVG / PNG give the same deck-ready image from a phone or a monitor.
The chart builder puts the chart first when there's no room for it beside the layers.

Views (hash routes): `#/` Home (status strip, watchlist table, new filings since the last
visit, expected filings); `#/companies?<filters>` the screener (`/v1/screener`: filters kept in
the URL, sortable, ungranted companies as metadata only, CSV export through the API);
`#/company/<company_id>` 12-quarter chart and table, every value linked to its filing (a statement or segment value opens the filing in the reader at its financial statements, a 10-K's Item 8 or a 10-Q's Part I Item 1; the EDGAR link sits beside it on hover);
`#/filing/<company_id>/<accession>[/<section>]` parsed filing reader; `#/chart` the chart
builder (each layer a `/v1/formulas/evaluate` request; plain SVG; CSV, SVG and PNG export);
`#/ask` the assistant full width, with streamed answers, the lookups it made and verified citation links
(an answer's figures not found in the data are named under it, each a button that scrolls to and
flashes its mark in the answer: `components/FigureFlags.tsx`, the same in the check panel; a
question that got no answer says it didn't count, after agent-api's `refunded` event);
`#/compare` the comps table; `#/analysis` the Analysis tab (guided research projects, below; `#/analysis?start=<company_id>` opens New analysis on that company, from the company page's "Start an analysis", which reads "Open analysis" when there is one: beside New thesis and New model, whose forms say a thesis or model there is a single item, without the steps); `#/about` says what Tributary covers and doesn't (no prices, consensus or
transcripts; estimated dates; banks; EDGAR timing), linked from the sidebar footer and the
assistant's empty state; `#/status` the data status page (`pages/Status.tsx`, `GET
/v1/status/data`), in words an analyst or a reviewer reads, with every time in its zone: feed
freshness, how long a filing takes from EDGAR to here (typical, and 95% within), live-feed share,
completeness against EDGAR's daily indexes (business days only, which is why fewer days than the window
are checked), coverage counts, data-quality check totals (reconciled: every company with XBRL figures is
checked, and those without a quarterly series are why fewer show as having financials) and the last
recorded results of the adversarial, citation and golden-figure suites (linked to their reports on GitHub at
the commit they were recorded in, which shows only on the link's hover). It is public: signed out, `#/status` renders it without the
app shell (and without calling `/v1/me`), linked from the front door; signed in, from the
sidebar footer. Every figure comes from the API; the page hard-codes none. A signed-in reader can
follow a check's company count to the screener (`#/companies?issue=<check>`: the companies they can
read with such a finding); the status route itself stays aggregate, and signed out there are no
such links. The agent's page context reports it as `about` (agent-api has no `status` page) and
leaves the data-check filter out of the screen it sends (its filters are strict).

Company names (`src/displayNames.ts`, `src/companies/directory.ts`): SEC conformed names are made
readable for display only, wherever API JSON arrives (`api.ts` `displayNames`): state and listing tags
and legal suffixes come off, then a table of brands, the SEC's surname-first forms ("HUNT J B
TRANSPORT" is J.B. Hunt Transport), then casing, which leaves a name the filer wrote properly alone and
keeps initialisms in capitals (three letters or fewer unless it's a word). The stored name is untouched
and CSV exports keep it. `displayNames.test.ts` is a table of real names. Segment labels get the same
treatment (`segmentLabel`, ported from facts-fetcher's `labels.py`). Anything that holds only a company
id asks one directory per session (`CompanyDirectory`): it answers from what the app already holds,
otherwise gathers the ids and asks the screener's `company_id` list (identity is public metadata, and
that route needs a token; no public route gained company data). Legends and source lines use the ticker
(or name), headings, breadcrumbs, Home's data notes and thesis exports use "Nvidia (NVDA)". While a name
is on its way a company reads "Company"; it never reads as a CIK.

Sign-in (`src/session.ts`): the front door (`pages/Welcome.tsx`) says what Tributary is beside the
way in, with figures from the public `GET /v1/status/data` (none hard-coded; if the route fails the
page reads the same without them). Its first action is "Try the demo". It calls
`POST /v1/demo/sandbox`, which creates a temporary real user (plan pro, a seeded watchlist and
workspace; see the Query API README) and returns its JWT, then lands on Home. With the public
catalog open the user reads every company at once (`/v1/me` says so). While it is closed the
sandbox gets grants on 20 companies instead, and the page polls `/v1/me` until all 20 are
readable, showing how many have arrived (access travels app DB → Debezium → Kafka → authz-sync →
OpenFGA; the BRD target is under 30 s; after that it offers "Continue anyway"). The session is
kept in localStorage until the sandbox expires; a banner shows "Demo sandbox: every company ·
resets in Xh" ("20 companies" while the catalog is closed), and an expired or rejected token
returns to the front door. A regular account (not a sandbox or GitHub user, e.g. a tester the owner invites) signs in with
an owner-issued link: the owner mints a token on the server (`query-api-token <user_id> --hours
N` inside the query-api pod) and sends `https://<site>/#/signed-in?token=<token>&expires_in=<s>`;
the app keeps it as an `account` session, with no demo scope (it reads what the API says).
An owner-created username/password account (Query API README, "Password accounts"; there is
no signup) uses "Sign in" on the front door, shown when `GET /v1/auth/providers` says
`"password": true`: a labelled form (username, and a `type="password"`,
`autocomplete="current-password"` field) that posts once to `/api/query/v1/auth/password`, then
reads `/v1/me` and stores the same `account` session (login = the username, from `/v1/me`'s
`account`). The password stays only in the form's state until it is sent and is cleared right
away; it is never stored. Every refusal shows "Wrong username or password, or the account is
locked."; a 429 shows the API's message. An expired or revoked session (the owner disabled the
account or changed its password) returns to the front door with "Your sign-in expired. Sign in
again." `src/PasswordSignIn.test.tsx` covers the form, the error and the session.
The named demo users (alice, bob, carol, dave: `/v1/demo/users`,
`/v1/demo/token`) are test personas: production turns them off (`demo.enabled: false` in the Flux
release), and locally they are behind a dev flag: build with `VITE_DEMO_PICKER=1`, or run
`localStorage.setItem("tributary.picker", "1")` in the browser and reload.

"Sign in with GitHub" sits next to "Try the demo" when `GET /v1/auth/providers` says
`{"github": true}`. It is a plain link to `/api/query/v1/auth/github/start` (a full-page
navigation, not a fetch; the Query API does the OAuth flow, see its README). GitHub's callback
lands on `#/signed-in?token=…&expires_in=…` (or `?error=<code>`, shown as a plain sentence:
`session.ts` `signInError`). The app reads the token from the fragment (`readSignedIn`; the
token still comes only from the API), drops the fragment from the address bar and history
(`history.replaceState`), reads `/v1/me` (its `github` block: login and the demo's company
ids), stores a `kind: "github"` `Session` the same way as a sandbox's, shows the same
propagation progress for a new account, and lands on Home. The top bar shows "Signed in as
<login>" with "Sign out", which forgets the token (the JWT is stateless and lasts 7 days; an
expired one returns to the front door with "Your sign-in expired"). A GitHub account is not a
sandbox: no "resets in" banner, but it has the same access, so the scope below applies while
the catalog is closed.

**Open access.** `/v1/me` says whether the public catalog is open (`catalog.public`, with its
tiers; then `companies` lists only private grants). `src/access.ts` turns that into what the
pages show: `readableFor(me, tier)` (every company while the catalog is open with that tier),
`readableTotal(me)` (the sidebar's count) and `catalogOpen`. With the catalog open nothing below
is scoped: a sandbox or GitHub session sees every company like anyone else, the banner says
"Demo sandbox: every company · resets in Xh", the front door says "access to every company"
(`catalog_public` on `GET /v1/demo/sandbox`), the Welcome page waits for no grant (none is
written), Search covers every company's filings (ranked within the newest window that fills the
page, with a line saying how far back, unless a company or dates narrow it), Home's feed heading
counts `readableTotal`, and Home's expected filings are the watchlist's (`scope=readable` would be
thousands of rows). Display only: the API decides.

Filing reader (`components/explore/FilingReader.tsx`, pure parts in `reader/text.ts`): the section
is read in full (`max_chars`, the API's cap is 1,000,000: the old 50,000 hid later matches and
cited passages; an API still at 50,000 answers 422, and the page asks again at 50,000, see
Production). A search lists its matches above the outline, opens the section at the first
highlight (or the first at a cited passage), and steps with the ↑ ↓ buttons or Enter / Shift+Enter
in the box. Two counts, labelled, because they count different things: the outline and heading show
*passages* (the search index's chunks of about 2,000 characters holding all the words, from the
API), the heading also *highlights* (each word marked, what next and previous step through). The
outline's legend says what its numbers are; only one kind shows at a time. The previous and next
filing of the same form (10-K, 10-Q, 8-K) open at the same section with the same search; one that
isn't parsed links to EDGAR.

Earnings and events (F1; `company/Earnings.tsx`, `company/Events.tsx`, wording in `company/events.ts`'s
docstring; the Query API's `/earnings` and `/events`, rules in its README, "Earnings and events"):
- **Earnings tab** (company page, after Overview; also the Analysis flow's Earnings step): a quarter
  picker (quarters by their end date, the newest with a release first; "expected" until the 10-Q/10-K
  covering it is filed), then the **earnings release** (8-K Item 2.02: its headline quoted, the 8-K's
  items, Read the release in the reader, EDGAR), beside the quarter's **reported figures** from its
  10-Q/10-K (the recap route: revenue, operating and net income, EPS, free cash flow, the three
  margins, against the same quarter a year ago, n/m readings from the API; "not filed yet" or "not
  in the figures yet" when that's the case); the **outlook stated in the release** (the blocks under
  its own Outlook or Guidance headings, lines as written, each with Read it in the release at its
  offset); **management's adjustments** (its reconciliation tables, folded, in monospace, and its
  notes that state a dollar amount). Each says when the release has none ("no section headed Outlook
  or Guidance ... read the release to be sure"). No beat or miss: Tributary has no estimates, and the
  page says so. Pins: an outlook block or a note as `quoteLines` (its lines kept), a table as
  `quoteTable` (a code block in the quote), each citing "8-K for the quarter ended …".
- **Since the last report** (company Overview, below Key stats; the What changed step's first card):
  the 8-Ks filed after the latest 10-Q/10-K period end, newest first: date, form, the items in the
  reader's short labels (`EIGHT_K_ITEMS`; exhibits left out), "may change cash, debt, ..." on an
  event the API says affects a balance, the 8-K's own opening words quoted (pinnable in a project),
  Read the 8-K at that passage, Read the press release, EDGAR; one not stored here says so and links
  EDGAR. Routine filings (votes, nominations, exhibits alone) fold under one line. Above them, the
  figures as of the period end a later event may have changed, each with its event, and "no pro
  forma": Tributary doesn't compute a figure after the event. The same flags sit on the Overview's
  key stats (Cash, Net debt, Debt / equity; every stat for non-reliance or bankruptcy) when the
  figure is as of that period end or earlier: "⚑ before the Oct 27, 2026 8-K (Acquisition or
  disposition)" ("... and 1 more 8-K" when several; the hover names each), the figure itself
  unchanged. The Overview asks `/events` once for both. An event the API flags from its 7.01 / 8.01
  announcement (F1b, `announced`) says so under its head: "⚑ A completed acquisition or
  disposition, read from its Item 7.01 announcement (it has no Item 2.01): the words “Completes
  Acquisition” in Press release", linking the reader at that sentence, then the sentence quoted;
  the stale list names it the same way. An older API without `announced` words it by its items.
- An older Query API without the routes (404) reads as "not available here yet", never an error
  (`api.ts` `orNullOn404`); e2e's mock answers both routes so the clean-console check holds.
- Verified (2026-11-08) against production's data through a harness: the branch's routes over rows
  read from hermes, every other read from production: Vertiv's Sep 30 cash, debt, net debt and total
  assets flagged before the Oct 27 UIG acquisition (tester 1's missed 8-K) with its 8-K quoted;
  Deckers' FY27 outlook ($7.80 to $7.95 EPS) and the $46.0M IEEPA refund note (tester 2's facts);
  NVIDIA's Q3 FY27 outlook and its debt 8-K after the Jul 26 quarter end; a pinned guidance table
  in a thesis citing "8-K for the quarter ended 2026-09-30".

Company page (`pages/Company.tsx`): Statements keeps statement, period, basis and range in the
address (`#/company/{id}/statements?kind=bs&period=ttm&range=5y`, `company/statementView.ts`, defaults
left out, so a view can be linked), with a range control for Quarterly and TTM (8 quarters, 5y, 10y,
Max; wide tables open at their newest end); Annual stays ten years. Filings opens on Reports (10-K,
10-Q, 8-K; All is one click away), headed by fiscal year with gold's rule (`data.ts` `fiscalYear`,
migration 014; `filingHeadings`: on a newest-first page a heading only moves back in time, since a
10-K for FY2026 is filed in FY2027 beside that quarter's 8-Ks), and says "not parsed yet" only on
forms that get parsed. What changed sits under the table, so the table has the page's width; its
actions stay in view on narrow pages (sticky) and long 8-K item lists wrap.

What changed (F3; `company/Filings.tsx` `Changes`, wording in `company/changes.ts`'s docstring; the
Query API's `/changes` with `view`, rules in its README, "Read for materiality"): a 10-Q opens
against the **same quarter a year earlier** (Previous 10-Q one click away; a 10-K against the
previous 10-K), **most material first** (Reading order beside it), with a **topic** box ("tariffs":
matches marked), **Only passages with new figures** and a section choice. Above the list, in words:
what it was compared with ("Compared with the 10-Q for the quarter ended Sep 30, 2025, filed Oct 31,
2025."), the ranking line, and what the pass left out ("Not shown: 9 calendar rollovers ..., 2
paragraphs only split, rejoined or page furniture, ..."), then "10 of 91 changes about “tariffs”".
Each change says New / Removed / Rewritten, its section, its period ("3 months", "6 months, year to
date", "3 and 6 months": a 10-Q's quarter text is never read as its half-year text), the passage
with its word diff (a "second" → "third" swap inside it unmarked), "Why it ranks here: new passage ·
5 new figures · new risk wording: tariff, refund · Results of operations" and Read it in the filing;
the first 30, then Show all. In Analysis the same list pins each passage with its period ("MD&A (6
months, year to date)"). The filing reader's side by side asks the same view, a 10-Q a year earlier
with the comparison switchable, and puts each passage's period beside it. When the year-ago 10-Q
isn't indexed the page falls back to the previous one once and says so. An older Query API (no
`view` in its answer) gets the plain list it always had, the comparison still choosable; the
"unchanged or minor" count no longer adds minor edits twice. Verified (2026-11-09) through a harness
serving the built app: the branch's `/changes` over production's section texts (its stored rows
reproduced exactly from them), every other read from production: Deckers' Q2 FY27 10-Q against Q2
FY26 (91 changes, "tariffs" 10, with new figures 4), against Q1 (its six-month text labelled, not
paired with Q1's three-month text), Vertiv's Q3 10-Q (against Q2: 9 calendar rollovers hidden,
"third quarter" unmarked inside rewritten passages; against Q3 2025: its new covenant risk factors
ranked), NVIDIA's 10-K against the previous 10-K, at desktop and phone widths.

Freshness has two clocks, labelled, explained on hover (`home/useFeedStatus.ts`): a filing is *listed*
(`GET /v1/status`, the same time as Data status's "Newest filing listed here") minutes after EDGAR accepts
it, and its text *parsed* (the newest in `/v1/filings/latest`) at once for tracked companies and
within about 6 hours for the rest. They differ by hours, and neither stands in for the other.

The assistant panel shows the questions left before one is spent (`assistant/useAllowance.ts`,
agent-api `/v1/allowance`): per hour and, where the account has a cap, per day. What Tributary covers
states the same limits, and a report's and a thesis check's buttons say what's left. On an older agent-api
it shows nothing. A question a page wrote for the user ("Summarize … in this 10-Q") leaves when they leave
that page, unless they edited it. The footer's date is "Newest filing cited": it knows what an answer
cites, not what the answer relied on. Model name, tokens and cost sit behind Details; `renderAnswer` writes
timestamps, dates, `as_of` and XBRL concepts the model copied from a tool in words (display only, the concept
on hover; a thesis, which is the user's, isn't touched). A timestamp reads as the viewer's own date
(`format.ts` `localDay`: 03:00 UTC on the 7th is the 6th in Phoenix); a plain date as written.

Tags: colour says the form (10-K, 10-Q) or, on saved work, the kind; an expected filing has a dashed outline,
not a colour, so a colour never means "not filed yet". Bold marks new rows on Home only when some rows aren't,
and an 8-K's period of report reads as the event date it is (`format.ts` `periodPhrase`).

Home: the sidebar badge and the status tile count the same filings (`Workspace.fresh`: the
watchlist's with the catalog open, since the feed is then everyone's latest 50) and say the same
thing (`home/data.ts` `sinceLabel`: since your last visit, or the last 7 days on a first visit in
this browser). Removing a watchlist company offers Undo for 15 seconds. "New chart" starts empty
and detached from any saved chart (the nav's Chart builder still reopens the draft). Anything that
replaces a chart with work in it (New chart, a template, a Chart button, opening a saved chart) keeps
that draft: the builder says what happened and can restore it until the next replacement.

Search (`pages/Search.tsx`): words run on Enter or the button (each search ranks every match);
form, company and dates apply as soon as they change. Inverted dates aren't searched: the page says
so and offers to swap them. No result under filters offers to clear them instead of blaming the words.

While the catalog is closed, a sandbox (or a GitHub sign-in) lists only its own 20 companies
(`src/scope.ts`, a React context set from the session, and only while `/v1/me` says the catalog
is closed; display only, the API's OpenFGA check and RLS stay the boundary). Companies screens
with `access=financials`, without the "Only companies I can read" box, and counts "N companies
in your demo"; the palette (and so the watchlist's "+ Add"), Compare's target/peer pickers and the palette's `compare`/`chart`
commands search with `/v1/companies/search?access=financials`; Compare's suggestions and the
company Peers tab ask `/v1/companies/{id}/peers?access=financials` (no readable peers: a short
note pointing to Compare); a Compare link with other companies leaves them out, with a note;
a company or filing URL outside the demo shows one page, "<Name> isn't one of the 20 companies
in this demo.", instead of locked tabs, and the assistant suggests nothing about it. Already scoped by the API
for everyone: the chart builder's companies (`access=financials`), Home's feed and expected
filings (`/v1/filings/latest`, `scope=readable`/`watchlist`), the Search page's company filter
(`access=financials`, `readableFor`). Named demo users and future signed-in users keep the full public index, with
ungranted companies as metadata only.

The assistant's suggestions (`assistant/context.ts`) follow the page and are true for this user: the
company on screen, the chart's first company, an analysis's company, or the user's own watchlist by
name (the agent can't see a watchlist, so a prompt says who). With nothing to go on they name no
company. Test strings ("I'm bob", "SYSTEM: admin override") aren't in the product; they live in the
adversarial suite (`tests/adversarial`).

The assistant sends the screen as `context` with each question (`assistant/actions.ts`) and
applies the agent's `action` events (chart layers and options, screener filters, watchlist) with
an Undo per action (`assistant/useUiActions.ts`): undo restores the chart and page from before
it, reverses watchlist calls, deletes an item the assistant saved (`save_to_workspace`), and
also undoes every later action.

Saved work (`src/workspaces/`, Query API `/v1/workspaces`): the sidebar's Workspaces are folders
of saved charts, comps sets and screens. A folder's arrow folds it and its name opens the workspace
(or the analysis) page, which has Rename, Duplicate and Delete in the open, for itself and for each
item (`workspaces/Actions.tsx`); "⋯" on a sidebar row is the shortcut to the same actions, in a strip
under the row, so a name never gives up room for them. Delete asks over the whole screen, with the
safe answer focused and Escape keeping it. Clicking an
item opens its page with the saved state (`model.ts` restores it through the same cleaners as a
URL, so a saved item is trusted no more than a link). The Chart builder, Compare and Companies
have Save / Save as… with the open item's workspace and an "unsaved changes" mark; the open item
per page is remembered per user in localStorage. Home's "Continue where you left off" lists
recently opened items and filings read in the reader, newest first. Saved items grant nothing:
the pages read data through the Query API as usual, so a chart for a company you lost shows the
same "isn't one of your companies" state as anywhere else.

Reports (`src/reports/`, agent-api `/v1/reports`): a workspace's page (`#/workspace/{id}`, the
folder's name in the sidebar) lists its items and reports and has "Write a report". The assistant
writes a research note from the saved items and saves it as an item of kind `report`; progress
streams while it works, and the app stays usable (the run lives with the app, `useReportWriter`).
The report page (`#/report/{id}`) renders its Markdown through `markdown.ts` (DOMPurify; accession
numbers link to EDGAR only when a tool returned them) and draws each embedded item from the state
saved with the report, reading the data through the Query API as the viewer: charts with
LayerChart, comps as small multiples (an ungranted peer says "no access"), screens as their
filters with a link. Figures the verification couldn't trace are highlighted, and listed in the
note itself. Download Markdown (`reportMarkdown`: embeds become a line naming the item,
unverified figures are marked `[unverified]`, sources listed) and Print / PDF (`@media print` in
`styles.css`: the page alone, in the light deck palette whatever the theme; `tokens.test.ts`
keeps it equal to the light tokens). "Write again" regenerates it over the same item. A report
counts as 3 assistant questions; the API's refusal (hourly or demo cap) is shown as is.

Theses and models (`src/thesis/`, `src/models/`; Query API README, "Theses and models"): what
you write yourself, saved as workspace items through the generic item routes and owner-only
like everything saved. "New thesis" and "New model" sit on a workspace's page (next to "Write a
report") and on a company's page (next to Chart / Compare / Ask: about that company, in a
workspace you pick or a new one); the workspace page lists them under "Theses and models", the
sidebar with the other items (rename, duplicate, delete as usual). Every Save and New form starts
on a workspace about the item (`workspaces/model.ts` `defaultWorkspace`): the company's analysis
project, else a folder whose name names it, else a new workspace named after it; a screen starts
on the folder changed last. Never just the first workspace.

- **Thesis** (`#/thesis/{id}`, `ThesisPage.tsx`, pure parts in `thesis/model.ts`): a title (the
  item's name), an optional company and a Markdown body (up to 100,000 characters). Write,
  Preview or Side by side (remembered per browser); Save or Ctrl/Cmd+S, with an "unsaved
  changes" mark and the browser's leave-page warning. "Embed a saved item…" inserts a
  `[[embed:<item id>]]` line at the cursor (at the end until the user has placed one, never above
  the title) for any chart, comps set, screen or model in the same
  workspace; the preview draws each one in place as reports do (`reports/Embeds.tsx`, the data
  read now as the viewer). A marker for anything else (another workspace's or user's item, a
  report, a malformed line) is dropped and the editor says how many don't resolve. The text is
  rendered only through `markdown.ts` `renderMarkdown` (marked + DOMPurify, no inline styles or
  form controls; images become their alt text, since the CSP loads nothing from elsewhere;
  accession numbers stay plain text). Import .md reads a `.md`/`.txt` file in the browser (up to
  400 KB, refused if it isn't plain text), drops a front-matter header (its title becomes the
  thesis's if it is still "Untitled"), and replaces the text until you Save (Undo import).
  Export .md downloads the thesis with a front-matter header (title, company, CIK, date), each
  embed as a readable block saying what it is and shows (`thesis/export.ts` `embedSummary`: a
  chart's layers, comps' companies and columns, a screen's filters, a model's assumptions per
  case) between `<!-- tributary:embed <id> -->` comments, and the last check (date, stale or not,
  counts, each claim's status, explanation and filings: `checkSection`) between
  `<!-- tributary:check -->` comments; it holds only your own items. Importing such a file turns
  each embed block back into its `[[embed:<id>]]` line (live again in the same workspace) and
  drops the check. **History** lists the versions earlier saves replaced (Query API, app
  migration 014; the newest 30) and Restore puts one in the editor as unsaved changes, with Undo
  restore; saving it keeps the one it replaces in turn.
- **Check my thesis** (`thesis/CheckPanel.tsx`, `useThesisCheck.ts`, pure parts in
  `thesis/check.ts`; agent README, "Thesis checks"): the button checks the saved text (disabled
  while there are unsaved changes) through agent-api `/v1/thesis-checks` and counts as 4
  assistant questions. The panel beside the thesis lists each factual claim with a Supported /
  Contradicted / Can't verify badge, the assistant's explanation and its citations (form, date,
  "Read MD&A" opening the filing reader at the cited section, "EDGAR ↗" the filing on sec.gov
  with its accession on hover, the figure; `components/FilingLinks.tsx`, also on answers and
  reports); a filing no tool returned and a figure not found in the data are marked, never hidden,
  and the warning names each figure (a button to its mark). A check that couldn't finish says why
  and, when agent-api gave its questions back (`refunded`), that it didn't count; the last saved
  check stays shown.
  Clicking a claim's quote marks it in the preview (only text nodes are wrapped in
  `<mark class="claim-mark">`; nothing is parsed) or selects it in the editor in Write mode. Then
  each embedded model's assumptions per forecast year against its 5-year revenue CAGR and margin
  range (the Query API's model run, read by agent-api; flagged years marked), what
  the latest 10-K/10-Q says that the thesis doesn't mention, the summary, notes (e.g. "only the
  first 30,000 characters were checked") and as-of, with model and cost behind Details. A
  quote shows without the Markdown it was written in (`plainQuote`; finding it still uses the
  quote as given). A history dropdown shows earlier
  checks (`GET /v1/workspace-items/{id}/checks`). A check whose `text_hash` differs from the
  current text (SHA-256 of the company id, a newline and the Markdown; `textHash`, the same as
  agent-api's `text_hash`, with a shared test vector) is labelled stale. All of the assistant's
  text renders through `markdown.ts` `renderAnswer`, like chat answers.
- **Revisit** (`thesis/revisit.ts`, pure; `useRevisit.ts`): on page load the thesis page asks the
  company's filing index for its newest 10-K/10-Q (`/v1/companies/{id}/filing-index?form=10-K&
  form=10-Q&limit=1`, public metadata, one request per company per page load) and compares it
  with the last check: newer when the check recorded the index's newest 10-K/10-Q (`filings[].
  latest`) and this one differs and was filed on or after it, or, for checks stored before that
  record, when it was filed after the check's day. Then "New 10-Q filed <date> since your last
  check — re-check?" with a Re-check button (the same check, 4 questions). The workspace page
  shows the same line beside each such thesis (its checks list gives the thesis's company and
  record: one request per thesis, first 20). No background job. After a re-check, the panel
  opens with **Changes since <date>** against the check before the one shown (`diffChecks`):
  claims matched by quote (normalised like the highlighter, trailing punctuation ignored;
  repeats in order) with changed statuses (Supported → Contradicted, ...), new and no-longer
  checked claims, model assumption flags that changed (by model and fiscal year), models added or
  removed, and the count of points not mentioned. Quotes there are plain text (React-escaped),
  statuses are labels; the assistant's prose still renders only in the claims below.
- **Model** (`#/model/{id}`, `ModelPage.tsx`; what was typed in `models/drafts.ts`, the saved state,
  the run's answer, the chart and the table in `models/model.ts`, the one-line rules in
  `models/rules.ts`): your assumptions for a company, run by the Query API. **The maths aren't
  here**: every change (300 ms after the last keystroke, `useModelRun`) posts the state to `POST
  /v1/companies/{id}/model`, the one copy of a model's maths (Query API README, "Models: one copy
  of the maths", whose `model_maths.py` docstring is the spec); the thesis check and later MCP call
  the same route. Before F2 the web app and agent-api each kept a copy.
  Per forecast year (1-10, default 3) and case: revenue growth %, operating margin %, and four
  rates, blank meaning Tributary's default from history (shown as the placeholder): interest and
  other (% of revenue: operating income less pretax income), tax rate, share change (buybacks less
  issuance, % a year) and FCF margin (FCF as one rate on revenue rather than cash flow less capex:
  both move with revenue, and the table shows operating cash flow, capex and FCF behind it); a
  note. Each field shows its default ("default 39.3%"), and one line per default says the rule
  with the company's numbers (`defaultRules`). **The current year from actuals** (`ytd`, every new
  model): year 1 is the current fiscal year's reported quarters plus the remainder, its growth and
  margin the remainder's ("FY2026E · Q4 (Q1–Q3 reported)"); the remainder starts at the year to
  date's growth over the same quarters a year earlier and those quarters' margin a year earlier
  moved by this year's change so far. Vertiv's FY2026E is Q1–Q3 as reported ($10.24B, +39.3%) plus
  a Q4 starting at +39.3% and 23.3%; Deckers' FY2027E is H1 plus H2; NVIDIA's FY2027E is H1 plus H2.
  Later years start at the TTM run rate and close half the gap each year to the long run (B7,
  unchanged). A model saved before F2 opens unchanged (year 1 a full-year guess, labelled so) and
  offers "Build FY2026E from Q1–Q3 reported", which sets each case's remainder to what its full
  year implied (`implied_remainder`), so no year changes until the user edits it.
  **The table** (`modelColumns`, `modelLines`, `ModelView`): one table with every line (revenue,
  growth, organic growth with acquired revenue, operating income and margin, reported beside
  adjusted with one-offs, interest and other, tax rate, net income, diluted EPS and its growth,
  diluted shares and their change, FCF and its margin, buybacks, and with a price the multiples)
  and bear, base and bull side by side: history years, the TTM (when it isn't a fiscal year end)
  and the current year's reported quarters once per line, then a row per case for the forecast
  years, the case being edited marked. A split or merger year (the API's `share_break`) shows no
  share change or EPS growth, and per-share figures before it are marked * with why. The chart
  draws revenue and operating income, history solid, each case dashed (bear and bull lighter,
  `faint`). **Bear / base / bull**: a switch picks the case being edited; "Add bear and bull cases"
  copies the run's preview of them (the base case moved by half its growth, at least 5 points, and
  a fifth of its margin, at least 3; the rates the user set), and waits for the run of the latest
  numbers so a case never starts from stale ones. Before they're added, a bear or bull tab shows
  that case's preview beside base; a case whose numbers don't parse keeps its last valid ones
  (marked ⚠), never another case's. Check my thesis checks the base case. **Adjustments** (yours,
  labelled so): a named one-off in a fiscal quarter's operating income (Deckers' "IEEPA tariff
  refunds", $46M, Q2 FY2027: its H1 margin reads 19.9% adjusted beside 21.8% reported, and the
  defaults read the clean year), or acquired revenue, which only organic growth leaves out.
  **Guidance** (`guidanceYear`): the latest earnings release's outlook (F1's `/earnings`) sits in a
  collapsed row beside the forecast year it covers (the fiscal year of the quarter after the
  release's), quoted with its 8-K citation, never read into a field. **Valuation at your price**:
  a price per share and a day you type, saved with the model, give market cap (× the latest shares
  outstanding), EV (+ net debt, only when its reading is current; Deckers' latest is from 2014, so
  EV is blank and says why) and P/E, EV/Revenue, EV/EBIT and FCF yield on history, the TTM and each
  case. The page says it's your price: nothing is fetched or kept. **History**: every save over a
  model keeps the version it replaced (Query API, app DB 014/015); Restore loads one into the
  editor unsaved, with Undo. Only what was typed is saved, never the history or a result.
  Embedded in a thesis or report (`ModelEmbed`), a model is run from its saved state and drawn the
  same way. Tests: `model.test.ts` on real run answers (`models/fixtures/*-run.json`: the branch's
  route over production inputs of 2026-11-09; `synthetic-runs.json` for the UI tests' history),
  regenerated with `services/query-api/tests/fixtures/model/make_web_fixtures.py`, not hand-edited.

Analysis (`src/analysis/`, `#/analysis`, sidebar "Analysis", `g p`): a guided research project
per company, from its filings to a thesis that's checked after every filing. Rules are in
`analysis/project.ts`'s docstring.

- **A project is a workspace** with `project = {companyId, peers, notes, done}` (app DB 012,
  Query API README "Workspaces"); its charts, comps, models and thesis are ordinary items in it,
  so the item routes, embeds, checks and Revisit work unchanged, and it shows in the sidebar's
  Workspaces with an `A` tag (◆ opens the project). A plain workspace has `project: null` and
  is untouched. `#/thesis/{id}` or `#/model/{id}` of a project's item is replaced by
  `#/analysis/{project}/thesis|model/{id}` (App.tsx), so it opens inside the project.
- **Home** (`#/analysis`, `AnalysisHome.tsx`): your projects, newest edit first: company,
  project name, last edited, the thesis's last check (supported / contradicted / can't verify,
  one checks-list request per thesis, first 20) and Revisit's "new 10-Q" flag. **New
  analysis**: a company search that lists only companies you can read (`readableFor`;
  `access=financials` on the API too), suggested peers from `/peers` (readable ones, the first
  3 ticked), a name; it creates the workspace with its project and opens step 1.
- **A project** (`#/analysis/{id}[/{step}[/{item}]]`, `ProjectPage.tsx`): a header with the
  company (public identity), "n of 8 steps done" and tabs for the 8 steps under the rail's names
  (each tab opens its own step); a rail of the 8 steps with a done marker each. Done means the
  user ticked it (`project.done`), never automatic and never locked: work in a step (notes, a
  saved chart, peers, a model, the Core thesis written, a check: `hasWork`) shows as a hint under
  the box ("1 chart saved here"). The rail's Monitor says "re-check" when the last check no longer
  covers the thesis (its text changed) or a newer 10-K/10-Q is filed. Without a step in the URL
  it opens at the first step not ticked. Each step is a pane
  that stays mounted once visited (hidden, not unmounted), so switching is instant and keeps
  everything typed; editors take `active` so only the visible one answers Ctrl+S.
- **Steps**, each reusing a screen scoped to the company: 1 **Business**: "How it makes money"
  notes (saved in the project after a pause), the latest 10-K's Item 1 in paragraphs, and the
  company page's Segments tables; 2 **Numbers**: five chart builder states (`charts.ts`: revenue
  and YoY growth, gross and operating margin, free cash flow = operating cash flow − capex, ten
  fiscal years annually; the Revenue tab's receivables vs revenue; long-term debt with debt to
  equity), each drawn by `ChartView`, with Open in Chart builder, Save to project and Add to
  thesis; 3 **Earnings**: the company page's Earnings view (below), every outlook block, note and
  reconciliation pinnable; 4 **What changed**: "Since the last report" (the 8-Ks filed after the
  latest 10-Q/10-K period end, each excerpt pinnable, the balances they may have changed flagged),
  then the filing page's `Changes` for the latest 10-Q and 10-K (a 10-Q against a year earlier,
  ranked, filters; each pin names the passage's period);
  5 **Peers**: Compare with the target fixed and the peers saved in the project (`embedded`:
  changes come back instead of going to the URL), Save to project / Add to thesis for the comps;
  6 **Model**: the project's models (ModelPage embedded, bear/base/bull side by side), "Create
  the model" from the company's history (this year from its reported quarters); 7 **Thesis**: ThesisPage embedded (no back link, no company
  picker); 8 **Monitor**: Check my thesis (disabled while the thesis has unsaved changes or only
  template text; checks run when the user asks), where the filings stand (Revisit's new-filing
  notice with a re-check, or which 10-K/10-Q the last check read), and the check panel (a quote
  opens the Thesis step and marks it).
- **Why Earnings is a step and the 8-Ks sit in What changed** (F1, from the analyst tests: the
  release and the acquisition after the quarter end were "the most decision-relevant filing
  content" and came only from a lucky search and the check): the release is management's account
  of the figures the Numbers step just showed (its outlook, its own adjustments), so it comes right
  after them as a step of its own, with its own tick, impossible to miss; the 8-Ks filed since the
  report are what changed after its period end, read beside the report's rewritten paragraphs.
  Projects saved before F1 keep their ticks: Earnings starts unticked, so such a project opens at
  it until it's ticked. An older Query API refuses `earnings` in `done`: `updateProject` saves again
  without that tick (deploy window).
- **Thesis template**: a project's thesis starts from `thesisTemplate`: `# Company: thesis`, then
  Core thesis, Variant perception, Pillars (with `### Pillar 1`), Catalysts (prefilled with the
  next expected 10-Q/10-K dates, `/expected-filings?periods=4`, labelled estimated), Risks and
  mitigants, What would prove me wrong, KPIs to watch, each with its guidance as an HTML comment
  (invisible in the preview; DOMPurify drops comments). Theses outside projects are unchanged.
- **Add to thesis** (`Pin.tsx`, a pushpin button with a menu of where it goes: under Pillars each
  pillar the thesis has, by its `###` sub-heading as the user named it, and "New pillar", the first
  pillar focused as the default; then the other sections; arrows, Escape): a chart or comps set is
  saved as an item of the project, then its `[[embed:<id>]]` line goes at the end of the chosen
  pillar or section (`placePin`: a new pillar is `### Pillar N`, the next free number, after the
  others; a missing section is added); a filing passage (Item 1, a what-changed paragraph; removed ones
  quote the earlier filing) goes in as a blockquote, escaped so it renders as written, with a
  citation line: form, the period it reports on ("10-Q for the quarter ended 2026-09-30", so
  six-month wording reads in its filing; a release's passage "8-K for the quarter ended 2026-09-30",
  the quarter the release reports), filed date, section, and the accession linked to EDGAR
  (`edgarFilingUrl`, built only from the company id and accession number the API returned). The
  project's thesis editor stays mounted and registers a handle (`bridge.ts`), so a pin lands in
  the text being edited: saved at once when there were no unsaved changes, else left as an
  unsaved change. With no thesis yet, a pin creates it from the template with the pin in it.
  Everything goes through the owner-only item routes.
- **Drafts** (`bridge.ts`, ThesisPage, every thesis, not only in projects): unsaved changes are
  kept in localStorage per user and thesis (`tributary.thesis-draft.<user>.<item>`) and dropped
  on save. Opening the thesis again restores them when they were made on the version saved now
  ("Restored your unsaved changes … Discard them"), else offers them ("Restore them" /
  "Discard").
- Grants nothing: every step reads through the permission-checked routes as the viewer. The
  assistant's page context reports a project as `workspace` (its thesis and model steps as
  `thesis` / `model`): agent-api's pages have no `analysis`.

Customizable views (`src/views/`, Query API `/v1/views`): Home's "Customize" shows, hides and
reorders its cards; the watchlist and the screener have "Columns" to choose and order their value
columns from what `/v1/screener/columns` offers (up to 8 and 12; typing finds one among the 80 or so), with Reset. Each change is saved
to the user's account at once (optimistic; a failed save restores the last saved state and says
so). Saved preferences are cleaned like any untrusted input (`views/model.ts`: unknown cards and
columns dropped, missing ones default) and grant nothing: values are still read through the
permission-checked routes, masked for ungranted companies. Without the app database the defaults
show and the controls are hidden.

Finding a company (`components/CompanySearch.tsx`): one server-backed combobox everywhere a
company is picked (Chart builder, Compare, Search's filter, Analysis, thesis and model pickers;
`CompanyPicker` shows a choice and opens the search to change it). It asks `/v1/companies/search`
per keystroke (debounced, tickers, CIKs and any part of a name, best first), shows ticker and name,
works with arrows and Enter, and says when nothing matches. No page loads a list of companies: a
screener capped at 500 stopped near "ARTC" and hid NVDA, MSFT and TSLA without saying so.

Chart builder (`src/chart/`): a "Chart a company" box next to the title searches the companies the
chart can read (by name or ticker) and adds the picked one's revenue (TTM) as a new line layer.
Each layer card starts with a labelled Company row (full width) and its header shows the
ticker. Names and tickers of companies on the chart come from the pick, or are asked once by id.
Tickers in a formula's suggestions come from the same search. Layers can be renamed (legend), recoloured (colours stay with a
layer when it moves), moved up or down, hidden; types line, bar, area, stacked bar and stacked
area (layers of the same stacked type and axis stack, negatives downward); transforms quarterly,
TTM, YoY, QoQ, indexed and per share (`ttm(f) / weighted_shares_diluted` in the quarter view,
offered only when the catalog has the share count); ranges 1y/3y/5y/10y/max or a custom first and last period end; quarterly or annual frequency
(annual = each year's fourth quarter: flows as TTM there, i.e. the fiscal- or calendar-year total,
balances as the year-end value, ratios full-year; QoQ becomes YoY; the Transform control then reads
"Annual"). Formulas autocomplete metric names (the catalog), your companies' tickers (`TICKER.`) and
`ttm()` / `abs()` while a name is unfinished, and stop once it's complete, since the list sits over the
controls below (`chart/autocomplete.ts`; Tab completes, arrows then Enter pick). The API checks them as
you type: the chart takes a formula as soon as it's valid (the check's 350 ms is the debounce), or on
Enter or blur whatever it is; every bad name shows at once, with "did you mean" when there is a close
one. Deck output draws the chart in the light palette with a subtitle (periods and units,
editable), axis unit captions and a source footnote ("Source: SEC filings via Tributary; forms
for companies; latest filing; as of"), which SVG and PNG exports keep. Evaluate answers are kept
in memory per (token, request) for the session (`chart/cache.ts`), so coming back to a chart
asks the API nothing. Clicking a point
(or Enter on it; arrows move between quarters and layers) opens its newest source filing in the
reader. Filing markers put each company's 10-Ks and 10-Qs (public index) and 8-K results (item
2.02, from parsed filings) in a band of their own under the plot, their letters only where they fit
(10-Ks first; the rest stay as stems, their hover names them), and on a chart of years only the 10-Ks.
"View as table" shows the plotted values with their sources as a data table.

How charts tell the truth, in every chart that draws through `chart/LayerChart.tsx` (the builder,
Overview, Revenue, Analysis Numbers, Compare, thesis and report embeds), SVG, PNG and CSV included:

- **Titles** come from the layers (`chart/model.ts` `autoTitle`: "AMZN: revenue (TTM) and net_income /
  revenue (TTM)", "Operating margin (TTM): KO and PEP", ", annual" when it is), naming what is drawn,
  until the user types one; then the builder says "Custom title" and offers the automatic one back.
  `ChartState.title` holds only what was typed, so a chart saved before this keeps its stored title as
  a custom one. Templates and the Chart buttons leave it empty.
- **An axis holds one unit** (`chart/axes.ts`): a layer asks for a side; if that side reads in another
  unit it moves to the other, and with both taken it isn't drawn (the card and the chart's footnote say
  so, and its values stay in the tooltip, the table and the CSV). Dollars, dollars per share, an index
  and a multiple never share one, so the smaller doesn't flatten at zero. Percents of every kind do.
- **A ratio isn't necessarily a percent**: the API says how a dimensionless result reads from its
  formula's shape (query-api `formula/check.py`), percent, multiple or plain number, and a layer's
  "Reads as" overrides it (`display`, saved with the chart). Debt to equity is 1.91x here as in the tables,
  NVDA / MSFT a multiple, `receivables / revenue * 365` a number of days. A contribution to growth is
  in points on axis and tooltip alike.
- **Gaps are gaps** (`chart/series.ts` `xAxis`): the axis has every period between the first and the
  last, so a missing year is a shaded hole, lines and areas break there, a point alone gets a dot, the
  table and the CSV have its row. The legend lists only what is drawn. `chart/notes.ts` says the rest in
  words, under the chart and in its exports: a period with no value, a series that starts late or
  stops early (one quarter behind others isn't a gap: companies file at different times), a layer with
  no values, a chart with nothing as far back as it asked, a negative index. A layer with nothing to
  draw is asked once over everything on file, so "try Max" appears only when Max has something, and
  when nothing is on file the chart's footnote says "No values on file", as the layer does, never "in
  this range" beside it (`chartNotes` `emptyOnFile`).
- **Basis**: the Calendar / Fiscal control highlights what is applied. Fiscal quarters only line up
  within one company, so with several (counted from what the formulas really read) the chart draws calendar
  ones and says why; the choice is kept for when it's one company again.
- **Axes**: margins are as wide as the tick labels, a caption too wide for its margin runs from the
  chart's edge, period labels never touch, a sliver past zero (-$8K beside $30M) doesn't earn a step of
  empty axis, ticks have the decimals to tell them apart, and a lone bar doesn't fill the chart.

Compare's dollar charts can also read on a log scale or indexed to the first value in range (not
saved; it says so when the largest company is over 100 times the smallest). "Chart these" and Peers'
"Chart margins" draw at most 8 companies, the colours a chart tells apart, and say so. The Peers table
sorts by any column like Compare (`compare/state.ts` `nextSort`/`sortRows`: high first, again to reverse,
a third time for the suggested order; empty cells last).

Revenue view (the company page's Revenue tab, `src/company/Revenue.tsx`; rules in
`company/revenue.ts`'s docstring): three sections, each chart an ordinary chart state, so "Save
to workspace" (`workspaces/SaveChart.tsx`: name and workspace, through the same item route and
state cleaner as the chart builder's Save as, without becoming the chart builder's open item)
makes it a chart item a thesis there can embed, and "Open in Chart builder" opens it there.
Values need financials access, like Statements.

- **Revenue by segment and geography** (TTM or Quarterly, fiscal periods; `/v1/companies/{id}/
  segments?metric=revenue`): per axis (business segment, geography, product/service) a chart and
  a members × periods table (last 8 periods), each value's hover naming its filing. A member
  missing a period shows "·", never zero; one reported in part of the window says "from FY2024
  Q1" / "until FY2023 Q4". The 6 largest members (by the latest period) are named, the rest folded
  into "Other (n members)" (the sum of those reporting each period). Members that overlap the rest
  (a parent beside its children, a second cut of the same revenue: `chart/segments.ts`
  `separateMembers`, rules in its docstring) are listed as reported, marked "overlaps the others",
  and left out of every sum. Finding them is a subset sum over 10,000 buckets of revenue, kept in one
  typed array of bitmask picks: at most 24 members x 10,074 steps a period, about 25 ms for 40 periods
  of 20 countries and a region overlapping three (5.9 s before), and remembered by input, so a
  re-render, the decomposition and saved charts don't search again (`overlap.test.ts` checks the
  answer equals the unbounded search's and the time). A "Sum vs reported revenue" row compares the rest with `/financials`
  revenue: bars stack only when every period is within 2% of 100%, otherwise each member is a line
  and the table says the breakdown is partial or can't be separated.
- **Growth decomposition** (TTM, year over year): each member's contribution in percentage points
  = its TTM change over four quarters / company TTM revenue four quarters earlier (members
  reporting both periods, without those that overlap), "Not attributed" = revenue growth minus
  their sum, and revenue growth; chart: contributions as stacked bars (negatives down) with revenue
  growth (TTM YoY) as a line. A period whose members change by more than revenue did (they overlap,
  or the company changed the basis between the two years, as NVIDIA did with its geography) shows no
  contributions, in the table or the chart, and says which: a stack never overshoots the total.
  This is in `contrib` itself, so saved charts get it too.
- **Revenue quality**, like with like: per quarter, the quarter's revenue (`/financials`
  view=quarter, never a TTM), quarter-end receivables, DSO (receivables / the quarter's revenue ×
  the quarter's days), both YoY rates (each against the same quarter a year earlier) and their gap.
  A quarter is **flagged when receivables grew more than 10 percentage points faster than the same
  quarter's revenue** (`QUALITY_FLAG_POINTS`). Receivables at a quarter end are what that quarter's
  sales left unpaid; a TTM lags a fast grower's latest quarter, which flagged Vertiv's receivables
  (+55.8%) against TTM revenue (+35.3%) when the quarter's revenue grew +61.2% (H3). Deckers' September
  2026 quarter still flags (+25.3% against +3.5%). The chart is the two sides as formula-API lines,
  quarter view (`revenue / revenue[-4q] - 1`, `receivables / receivables[-4q] - 1`); the Analysis
  Numbers step draws the same chart.

The Segments tab (`company/data.ts` `segmentTables`): a table per axis and metric (revenue, gross
profit, operating income), the newest eight quarters. YoY is the newest quarter against the one ending
a year earlier, found by date. A member with no value in those quarters isn't listed; one that stopped
before the newest reads "until FY2025 Q3", dimmed, after the current members (a reorganisation:
Deckers' UGG Wholesale became UGG Brand). A table whose newest quarter is more than 100 days behind the
company's newest segment figure says it isn't current (Deckers reports gross profit by segment since
2025, so its operating income ends December 2024).

Segment layers (`chart/segments.ts`, `chart/fetch.ts`): the Revenue view's segment charts are
chart builder layers of kind `segment` (company, segment metric, axis, 1-20 members), read from
`/segments` instead of `/v1/formulas/evaluate` (one request per company, axis, metric and view,
shared by the chart's layers), with transforms Quarterly, TTM, YoY and Contribution to growth.
They render, save, embed and restore like any layer (`model.ts` `layerKey`/`segmentRequest`,
`restoreChart`); the chart builder shows them as "Segments" cards (members fixed: they come from
the Revenue view). The assistant's page context leaves them out and reports the Revenue tab as
`segments` (agent-api's context knows neither).

Connect your data (Query API README, "Connect your data"; shown only when `/v1/connections`
answers, i.e. the server has `DATA_CONNECTIONS_KEY`: `market/useMarketData.ts`, one request per
token shared by every component; a 404 or an error hides everything below):

- **Settings → Connections** (`#/settings`, `pages/Settings.tsx`, linked from the sidebar footer):
  per provider (Tiingo today) a link to get a key, a password field (`autocomplete=off`) and
  Connect. The key goes out once, in the `PUT /v1/connections/{provider}` body; the field is
  cleared on success and nothing keeps it (no state, no storage). A saved key shows as provider,
  `…last4` and the date it was added, with Replace key and Disconnect (deletes the key and every
  price fetched with it). Errors are the API's own messages (a refused key, rate limit).
- **Company Price tab** (`company/Price.tsx`): without a connection, only a "Connect your market
  data" prompt (no price request is made); with one, the daily close (1y/5y/Max; as traded, not
  adjusted) and a Valuation panel: market cap, P/E, P/S, EV/EBIT (formulas in the Query API
  README, shown on each tile) and a table of every input with its date: the close's day, the
  shares' and TTM figures' period ends and filings. Each view names the provider (attribution).
- **Price layers** (`chart/prices.ts`): "+ Price" in the chart builder (only with a connection)
  adds a layer of kind `price` (company only) on calendar quarters: Quarter-end close (as
  traded), YoY % or Indexed (both over adjusted closes). A visible price layer puts the chart on
  calendar quarters (`model.ts` `basis`; prices have no fiscal calendar). Saved charts store the
  company and transform only, never prices: whoever opens a saved or embedded chart sees their
  own prices, or the layer's error ("connect your market data first") without a connection. The
  footnote names the provider. The assistant's page context leaves price layers out, reports the
  Price tab as `overview` and Settings as `about` (the agent can't read prices).

Data notes (`src/company/notes.ts`, Query API `/v1/companies/{id}/data-notes`, `/v1/data-notes`):
the tearsheet's Data notes tab groups what the automated checks found (freshness, restated
figures, gaps and overlapping periods, values to double-check, derived quarters) in plain
language, each with links to its filings (the reader for 10-K/10-Q text the user can read,
else EDGAR); groups run newest first, by their newest note, with derived quarters last. Home lists
the newest notes of the user's own companies, by name: with the catalog open, the ones they watch or
analyse (`/v1/data-notes?company_id=`, at most 50), since every company is readable; while it's closed,
the ones they can read. They need financials access, like the values they describe. A
company is checked when its figures change, so its tab says when *its* figures were last checked while
Home and Data status say when the checks last ran. A note about filings the SEC's XBRL data doesn't have
yet dates them newest to oldest and promises "a day or two" only while that's true (over 3 days, it
says how long the oldest has waited). The Statements tab says the same where the figures are read
(H9, `company/data.ts` `figuresBehind`): when the newest 10-K/10-Q in the public filing index reports a
period more than a week after the figures' newest and was filed after everything they cite, a notice
names it, its period, when it was filed and how many days ago, how many quarters the figures are behind,
and links its financial statements in the reader and on EDGAR. It promises no date (Citigroup was three
quarters behind on 2026-11-08). What a note says follows the API's reading of a finding: a stock
split is one "10-for-1 stock split" note, a restatement by a clean factor without the other side says it
may be a split, a factor of 1,000 says the unit changed, a spike the same quarter has in other years
is a seasonal peak.

Compare's "Side by side" draws the selected columns as small multiples (`compare/SmallMultiples.tsx`):
one compact LayerChart per column, every readable company a line in the same colour on each
(at most 8), calendar quarters over 3 years, each line one `/v1/formulas/evaluate` request built
by the chart builder's rules and shared with its session cache; nothing is asked until the
charts are shown. One, two or three charts a row by the room they have (`@container multiples`); a
short last row is centred, its charts the size of the rest, so eight columns leave no empty cell. A
company with nothing to draw on a chart isn't in its legend; the footnote names it.

States and accessibility: every data panel loads with a skeleton (`components/States.tsx`),
errors say what happened in plain words (`api.ts` `failureReason`: a 4xx detail is written for the caller, a 5xx's is for developers and never shown) and offer a retry; only a failure that clears by itself (a 5xx or no answer) adds "It usually clears in a moment", never a 4xx (a 422 is a request to change). Empty states say what to do next. The screener, watchlist and comps rows
are a roving tab stop (arrows, Page Up/Down, Home/End, Ctrl/Cmd + Up/Down the first and last
row, Right or Ctrl/Cmd + Left/Right into the cells, then the first and last column; Enter opens:
`components/tableNav.ts`). `?` opens the keyboard shortcut sheet (`components/ShortcutSheet.tsx`,
also from the sidebar footer).
`src/tokens.test.ts` checks every text, state and series token against WCAG AA in both themes.
Under 900px the sidebar becomes a drawer behind Menu; on a phone Sign out moves into it, the
screener's filters start folded, and an analysis's steps show their numbers with the current step
named.

What a table shows where there's no usable figure is one vocabulary, in `src/blanks.ts`: **—** not
reported, **·** no data in this period, **n/a** not applicable, **n/m** not meaningful, **$0** a real
zero. Whether a value is n/m, n/a or old is the database's call (migration 035), sent as `readings` so
the screener, Peers, Compare, Key stats and Home can't disagree with the sort behind them; `readFigure`
only words it. An n/m keeps its exact figure and reason in the hover. Ratios on negative equity (gold has
no debt/equity or ROE row there) read n/m only where `/financials?readings=true` lists the period in
`not_meaningful_periods`; the page never works out equity itself. An old figure (about a year or
more behind the company's latest period) shows dimmed with its date; one merely behind shows its date
and still counts. Neither n/m, n/a nor old enters a median. A bank reads "n/a (bank)" for gross and
operating margin, FCF, FCF margin, net debt, debt to equity and the current ratio whether or not it
tags them, by the API's `not_applicable` list. Statements: a balance sheet has no TTM (the button is off,
and a saved one reads as quarters); EPS and diluted shares read n/a in TTM, — in a fiscal Q4 (the 10-K
states the year, and they aren't derived by subtraction) and come from the 10-K itself in Annual
(`/v1/companies/{id}/annual-per-share`). Column pickers don't offer a balance's `_ttm` column.
What a statement's lines can't say is a computed row (`withBridges`, data.ts), italic and never in the
CSV: "Other items, net" between operating and net income where the filing tags no pretax income
(Guerrilla RF's warrant revaluation), "Not itemised" where assets aren't liabilities plus equity
(redeemable or mezzanine equity, noncontrolling interests), and a bank's interest expense says it's
already taken out of revenue. Gross profit computed as revenue less cost (migration 039) says so in its
hover. A fiscal year's month (`fyeMonth`) gives a date in a month's first week to the month before, as
`silver.fiscal_year_end_month` does for the screener's filter.

## Run

```bash
make web        # npm install + Vite dev server on http://localhost:5173 (needs query-api, agent-api)
make up-all     # nginx build on http://localhost:3000
```

The dev server proxies to `QUERY_API_URL` (default `http://localhost:8000`) and `AGENT_API_URL`
(default `http://localhost:8001`).

```bash
cd services/web
npm run typecheck
npm test          # vitest
npm run build     # tsc + vite build into dist/
npm run e2e       # Playwright flows in e2e/ against dist/ (build first)
```

The end-to-end flows (`e2e/app.spec.ts`: try the demo → Home, screener filter/sort/watch, a
two-layer chart with a formula exported as CSV, comps, saving a chart to a workspace and
reopening it, the assistant drawer with a streamed answer, the narrow-screen menu, no screen
scrolling sideways on a phone, at 1024 or at 1440 with the panel open, workspace actions, formula
autocomplete with annual frequency and deck output, Compare's side-by-side charts, the shortcut
sheet and Ctrl+arrow table jumps, the limits page, data notes on Home and the tearsheet, writing
a report from a workspace with a mocked SSE stream (embedded charts, the flagged figure, Markdown
download, print styles), and customizing Home and the watchlist and screener columns) run
against `vite preview` with every `/api/*` call answered by `e2e/mockApi.ts` (route
interception), so no backend runs. One worker and one headless chromium
(`npx playwright install chromium` once). CI runs them in the `e2e` job.

## Performance

Production build (`npm run build`, Vite 6, 2026-10-21), per route: what a first visit to that
screen downloads, minified (gzip). Every screen but Home is its own chunk (`src/pages/lazy.ts`,
React.lazy); once signed in and idle the app prefetches them, so switching screens doesn't wait.

| Route | JS | CSS |
|---|---|---|
| Shell + Home (`#/`), first load | 342.3 kB (109.0 kB) | 37.2 kB (8.1 kB) |
| + Chart builder (`#/chart`) | + ChartBuilder 23.7 kB (8.9 kB), LayerChart 11.4 kB (4.8 kB), cache 0.2 kB | |
| + Company tearsheet (`#/company/…`) | + Company 12.5 kB (4.0 kB), Filings 6.5 kB (2.3 kB), LayerChart | |
| + Compare (`#/compare`) | + Compare 13.6 kB (4.9 kB), LayerChart, cache | |
| + Companies (`#/companies`) | + 11.1 kB (4.1 kB) | |
| + Filing reader (`#/filing/…`) | + FilingReader 10.8 kB (3.7 kB), text 2.1 kB (1.1 kB) | |
| + Search, About | + 5.2 kB (1.9 kB), 3.6 kB (1.4 kB) | |
| + Data status (`#/status`) | + Status 10.2 kB (3.4 kB) | |
| + first assistant answer (marked, DOMPurify) | + AssistantMessage 68.1 kB (23.2 kB) | |
| + Workspace (`#/workspace/…`), Report (`#/report/…`) | + 8.3 kB (3.4 kB) each, Report + markdown, LayerChart, SmallMultiples 2.4 kB | |
| + Thesis (`#/thesis/…`), Model (`#/model/…`) (2026-11-02) | + ThesisPage 9.6 kB (4.0 kB) + markdown, Embeds 5.0 kB, ModelView 3.1 kB; ModelPage 7.3 kB (2.8 kB) + LayerChart; NewWork 3.5 kB (1.6 kB) with Company or Workspace | |
| + Check my thesis (2026-11-02) | ThesisPage 21.7 kB (8.1 kB) with the check panel | |
| + Revenue tab, Revisit (2026-11-02) | Company 30.0 kB (9.5 kB) with the Revenue view and its chart renderer; ThesisPage 22.8 kB (8.1 kB) | |
| + Price tab, Settings (`#/settings`) (2026-11-02, Connect your data) | Company 37.1 kB (11.7 kB) with the Price tab; Settings 3.1 kB (1.4 kB); shell 358.7 kB (114.3 kB) | |
| + Analysis (`#/analysis…`) (2026-11-02) | AnalysisPage 35.7 kB (12.0 kB) + ThesisPage 32.3 kB (11.8 kB), ModelPage 9.9 kB (3.8 kB), Compare 10.8 kB (4.0 kB), Company chunks; shell 361.0 kB (114.9 kB) | 48.6 kB (10.3 kB) |
| + Model depth (2026-11-09, F2) | ModelPage 27.7 kB (9.6 kB), useModelRun 14.1 kB (5.0 kB), ModelView 6.2 kB (2.7 kB), model 3.3 kB (1.7 kB); shell 433.6 kB (142.4 kB) | 65.4 kB (13.6 kB) |

Before splitting, everything was one 493.6 kB (156.3 kB gzip) script; the first screen now
loads a third less. Most of what remains is React DOM. Chart data is cached in memory per
(token, request) for the session (`src/chart/cache.ts`). Lighthouse CI isn't run: `@lhci/cli`
plus a Chrome download didn't fit the CI and laptop budget; these sizes are the tracked number.

## Production

One Helm upgrade rolls every service at once (same image tag, rolling updates, nothing orders web
after query-api), so for a minute this page can talk to a Query API one release older. What it asks
of the newer API degrades rather than breaks (`api.ts`, tested in `api.test.ts`): a refused 1,000,000
`max_chars` or 120 `quarters` is asked again at the old caps (50,000, 40); a save refused for a field
the older strict models don't know (a chart layer's `display`, a screen's `issue`) is saved again
without it, so no work is lost (a model's F2 fields are the user's work, so a save the older API
refuses for them says to try again in a minute instead of dropping them); a model run (`POST
/v1/companies/{id}/model`) answering 404 shows "being updated, draws again in a minute" with Try
again, and a new model then starts at zeros in the older shape; versions answer an empty list on
404; data notes asked for some
companies keep only those; a data-check screen the API didn't echo (`issue`) is a failure with Try
again, never every company; `/annual-per-share`, `readings` and agent-api's `/v1/allowance` were
already optional.

`Dockerfile` builds the bundle and serves it from `nginx-unprivileged` on port 8080
(`deploy/nginx.conf`). nginx returns 404 for the APIs' `/docs`, `/redoc`, `/openapi.json` and
`/metrics`, forwards a request ID, and passes Cloudflare's `CF-Connecting-IP` to agent-api as
`X-Client-IP` for its per-IP limit. `deploy/headers.conf` sets CSP, HSTS, nosniff,
Referrer-Policy and Permissions-Policy on every response. On k3s only the `cloudflared`
namespace can reach it. The page itself goes out with `Cache-Control: no-transform`, so Cloudflare
serves it as built: its Web Analytics "automatic setup" injected a beacon script from
`static.cloudflareinsights.com`, which the CSP blocked on every load (a console error, and nothing
measured). The CSP stays as it is; if the beacon still shows up, turn the zone's Web Analytics
automatic setup off in the Cloudflare dashboard.
