# web: design notes

The per-screen detail behind [README.md](README.md): what each screen shows and why, the rules it follows
(most also in the docstrings named here), and the measurements. Long by design. When a screen changes,
change its section here in the same commit.

Contents: [Look (Seal)](#look-seal), [Shell and layout](#shell-and-layout), [Screens (hash
routes)](#screens-hash-routes), [Sign-in and access](#sign-in-and-access), [Company names](#company-names),
[Freshness and times](#freshness-and-times), [Home](#home), [Company page](#company-page), [Filing
reader](#filing-reader), [Search](#search), [Assistant](#assistant), [Saved work and
reports](#saved-work-and-reports), [Customizable views](#customizable-views), [Theses and
models](#theses-and-models), [Analysis](#analysis), [Filing alerts](#filing-alerts), [Finding a
company](#finding-a-company), [Chart builder](#chart-builder), [Compare](#compare), [API keys and Using the
API](#api-keys-and-using-the-api), [End-to-end flows](#end-to-end-flows), [Performance](#performance), [Deploy
window](#deploy-window).

## Look (Seal)

**Look ("Seal").** The design system of record is `.interface-design/system.md` at the repo root: an
annual report kept as a ledger. A white page (a warm near-black in dark), black serif figures, red
rubrics in the margin, ledger rules, red-letter days, a double-ruled bottom line, and a red wax seal on
every figure taken from a filing; gold appears only on the seal. Tokens (both palettes, type scale,
spacing, square corners, motion) are in `src/tokens.css`, with the older names (`--panel`, `--text`,
`--accent`...) read from Seal's so screens not yet redrawn keep working; `src/seal.css` holds Seal's
own components and `src/styles.css` the rest. Red is never "a loss": a fall keeps its minus sign in
the ink (`.neg`), a rise is green. Since Riley's audit (2026-11-15) red belongs to the seal, the margin
rubrics, red-letter days and a "may have changed" mark only: the one primary button is the ink filled
(`--action`, inverted in dark), a link is the ink with a rule under it (`--link`; links that are
controls, nav or a table's figures carry no rule), an error has its own `--error`, checkboxes and focus
are the ink, hovers are the recessed sheet rather than a red wash. Pointing `--action` at the red in
`tokens.css` gives actions the red back in one place. Forms are marked by `components/FormMark` (10-K
filled ink, 10-Q outlined, the rest quieter, expected dashed; the hover names the form). Fonts are Source Serif 4 (names, sentences, figures; lining, tabular numerals) and Archivo
(controls, navigation, meta), both OFL and self-hosted: pinned `@fontsource-variable` packages
imported in `main.tsx`, bundled into `/assets` by the build, because the CSP allows fonts only from
this origin. No monospace for data, no all-caps labels.

Seal's pieces: `components/Seal.tsx` (the seal drawn once, `SealSprite` in `main.tsx`, and used by
reference; `SealLink` is a seal on a figure: it opens the filing at its statements, and its source is
a Radix tooltip on hover or focus and the link's description for a screen reader, replacing the
hover-only source), `components/Folio.tsx` (a section with its rubric in the margin: a 190px column
of red labels beside each section, above it when the page has under 1100px), `components/Choices.tsx`
(one of a few views: a Radix toggle group, arrow keys between options). Controls with behaviour use
Radix Primitives (pinned exact versions), styled only with the tokens: the company page's More menu
(DropdownMenu, `modal={false}`: a modal one locks scrolling with an injected style), the toggle groups, the seal's
tooltip, and the confirm dialog (`workspaces/Actions.tsx` `ConfirmDelete`, a Radix alert dialog). Radix
places popovers through the CSSOM, which the CSP allows; Radix's dialog Overlay isn't used, because
its scroll lock injects a `<style>` element the CSP refuses (the dialog draws its own backdrop).
Money negatives read in parentheses on every table (statements, ledgers, the recap, the screener, and since
Riley's audit the watchlist, Peers, Compare and the model through `components/FigureText`; `format.ts`
`paren`: display only; the CSV and the hover keep the sign, and F5's `outflow` stays the one place a
sign is decided); percentages and multiples keep their minus sign. An amount has four significant figures at
most: two decimals below 100 in its scale, one from 100 ("$46.74B", "$227.0M", never "$227.00M"); a row
takes its largest value's decimals, and a value that would round to nothing keeps its own scale. Compare's
comps say the quarter's end date ("Jul 26, 2026": two companies a quarter apart read apart), the calendar
and fiscal labels in the hover; a target with no peers starts with the five suggested ones; Make target
and Remove are buttons, a removal a toast with Undo; the side-by-side charts open indexed when the
companies are over 100 times apart in size.

Tags: colour says the form (10-K, 10-Q) or, on saved work, the kind; an expected filing has a dashed outline,
not a colour, so a colour never means "not filed yet". Bold marks new rows on Home only when some rows aren't,
and an 8-K's period of report reads as the event date it is (`format.ts` `periodPhrase`).

### The human pass

The human pass (Riley's audit, test 6, branch `riley-fixes`): every select and date field is one styled
control, buttons one scale, a page's bar one row (secondary actions in a `components/MenuButton` menu),
confirmations toasts (`components/Toasts`, at the foot of the window with the delete undo notes), a new page
opens at its top. The screener fades the last screen's rows with a line saying a new one is running, moves
the industry under the company's name below 1240px of table (the SIC code is a hover-only filter link), gives
a sector's industries their own filter cell, and saves a screen under a short name into a folder of screens
(else a new "Screens"); arriving from another page with a different screen than the one open closes the open
one. Home's feed starts with the watchlist's filings (All in your plan a toggle), its watchlist has one Last
filing column with the period under the date, sortable headers, cards with labelled figures on a phone, and a
toast to undo a removal; the status strip's figures never wrap. The top bar's Theme is a menu of the three
(Light, Dark, System), a demo sandbox has End demo (top bar and the phone's menu), and signing out lands on
the site's own address; with the assistant open the bar drops who is signed in before the breadcrumb's
words. Settings shows only what the server has (no Connections or Email section to say they're off), its
alerts' rules behind a disclosure. A workspace's Duplicate opens the copy, Write a report asks first and says
what it costs, renaming hides the heading it replaces, and an analysis project is tagged "Analysis".
The company page names the extra listings ("and 8 other share classes or notes", tickers on hover) and keeps
the SIC code in the industry's hover; each ratio's label says how it is worked out (ROE: twelve months' net
income over the average of equity now and a year earlier); a gross margin blank says why (no gross profit or
cost of revenue, or a warehouse club, variety store or telecom, 039's rule in words); the Fiscal / Calendar
switch says it only renames the columns for one company; on a phone the statement's switches are one
scrolling row. The Filings tab's Insider filter shows the transactions table in place of the index (not two
lists of the same Form 4s), dates have Clear, and insiders read given name first ("Jen Hsun Huang",
`insiders.ts` `personName`; the filing's form in the hover). The front door has one left edge, a 1px rule,
an odd last figure across the row, and figures in a reader's words; Data status says what a finding means
for what you see before how the checks work, and its cards keep their own height; What Tributary covers
stacks its three lists with their items side by side; the Ask page is wider with prose at a readable
measure, a cited filing a quiet mark rather than red monospace, and no pill repeating the title. The reader's
outline shows the item's number under its Part ("1A", not "II-1A") and no character counts (the size is
the hover), and its sticky section heading has a rule under it; Search says first when only recent filings
were searched, names sections ("Risk Factors") instead of their keys, offers Open the filing instead of the
accession number, and never doubles a phrase's quotes; Peers' shared terms appear once each, three at most;
Business paragraphs rejoin a sentence a line break cut.

### The restraint pass (Riley's audit, test 10, findings 37-107)

Red is only the seal, the margin rubrics, red-letter days and the "may have changed" `*`: the Home badge,
the bell's count, watchlist new-filing dots, where-you-are rules (nav, tabs, steps, sort, saved screens),
disclosure triangles, red line states, crossed tags and severity tags are the ink or the ink's greys; a dark
table's column rules are a warm grey (`--col-rule`; the paper's stay the red hairline). Buttons are one
system: primary (ink filled), secondary (outline), quiet (text, underlined on hover, never a grey pill),
destructive (the error ink, after a rule in a menu). An empty state is one line and one action, how the
screen works behind `details.how-to-read` (Portfolio, Your data, Analysis, Alerts, a workspace). What just
happened is a toast: a save (and the workspace it made), CSV exports, alert ticks, a red line, End demo (at
the front door's foot too). The company tabs' own cards read as folios (rubric in the margin, no box; a CSS
rule on `.company-page > section.card`, the markup unchanged). Confidence and a computed figure's how open
on a tap or focus (`components/Why`, the ledger's focusable `dd`), not only a hover. The palette finds the
pages and saved screens by what they do; the sidebar links Using the API; g p is Portfolio, g n Analysis.
A phone's company header keeps only the listing, industry, year end, currency and confidence; its tabs are
one scrolling row; the user's own tables read as labelled cards under 560px.

## Shell and layout

Every screen sets the browser tab's title (`src/title.ts`: "Nvidia (NVDA) · Statements · Tributary").
A page that fails to render shows "This page didn't load" with Reload and Home, keeping the shell
(`components/ErrorBoundary.tsx`, around the page and around the whole app; the error goes to the
console, never on screen). The "demo" badge shows only on a sandbox; the app names an account the way a
person knows it (`access.ts` `accountName`: "morgan", not the API's "pw-morgan").

The shell keeps its sidebar: a sidebar (screens with `g`-then-letter shortcuts, watchlist
with revenue sparklines, next expected filings, freshness), a top bar (breadcrumb, Ctrl/Cmd-K
command palette, the filing alerts bell, theme, who is signed in) and the assistant drawer on every page (Ctrl/Cmd-J toggles,
`/` focuses; the top bar's Assistant is the way in, closed until asked for and then remembered per
browser). The theme follows the visitor's system (light or dark, `prefers-color-scheme`, read by
`tokens.css` itself so nothing waits for a script). The top bar's Theme button says which is in force
and cycles Light, Dark, System: Light and Dark are kept per user in localStorage, System forgets the
choice (`theme.ts`).

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
periods. A long table's header row stays under the top bar while the page scrolls past it: the
table scrolls sideways, so a `position: sticky` header could only stick inside that scroller, and a
bounded-height scroller would trap the wheel on pages with several tables; instead the real header
row moves down (`headerShift`, a `--tw-head-y` variable set through the CSSOM, which the CSP allows),
so its sort buttons, the frozen first column and the sideways scroll work unchanged and there is no
copy to keep in step. Numeric headers stack their label over their unit, so a column is as wide as
its numbers, and as a table narrows the wordy columns give way first (the screener's industry and
Peers' "Why" move under the name; the watchlist's period end and Compare's fiscal label go to the
hover). Dollars in a statement, segment or revenue row share one unit (`format.ts` `moneyRow`): the
largest value's, or the next smaller one when a figure would read under one of the larger and the
largest still fits four digits ($119M ... $3,955M rather than $0.12B ... $3.96B; $55K, $964K, $2,430K),
thousands grouped; a figure that would round to nothing keeps its own, and the hover has the exact value. A company header shows its main ticker and "+8 more", with the rest in the hover.
Charts are drawn for the width they have (`chart/layout.ts` `chartSize`), so their text is its real
size at any width and titles and footnotes wrap instead of being cut; `svgRef` always gets the
standard-size drawing, so Export SVG / PNG give the same deck-ready image from a phone or a monitor.
The chart builder puts the chart first when there's no room for it beside the layers.

### States and accessibility

States and accessibility: every data panel loads with a skeleton (`components/States.tsx`),
errors say what happened in plain words (`api.ts` `failureReason`: a 4xx detail is written for the caller, a 5xx's is for developers and never shown) and offer a retry; only a failure that clears by itself (a 5xx or no answer) adds "It usually clears in a moment", never a 4xx (a 422 is a request to change). Empty states say what to do next. The screener, watchlist and comps rows
are a roving tab stop (arrows, Page Up/Down, Home/End, Ctrl/Cmd + Up/Down the first and last
row, Right or Ctrl/Cmd + Left/Right into the cells, then the first and last column; Enter opens:
`components/tableNav.ts`). `?` opens the keyboard shortcut sheet (`components/ShortcutSheet.tsx`,
also from the sidebar footer).
`src/tokens.test.ts` checks every text, state and series token against WCAG AA in both themes, and that
following the system to dark gives the same palette as choosing Dark. Focus is a 2px ring in the ink
(`--focus`) for keyboard users; motion is short (a press sinks to 97% in 120 ms, a tooltip fades in) and is dropped
under `prefers-reduced-motion`.
Under 900px the sidebar becomes a drawer behind Menu; on a phone Sign out moves into it, the
screener's filters start folded, and an analysis's steps show their numbers with the current step
named.

## Screens (hash routes)

Views (hash routes): `#/` Home (Start an analysis first, then the watchlist table, new filings since the
last visit and data notes; the status strip, Continue and expected filings can be turned on in
Customize); `#/alerts` filing alerts (below); `#/companies?<filters>` the screener (`/v1/screener`: filters kept in
the URL, sortable, ungranted companies as metadata only, CSV export through the API);
`#/company/<company_id>[/<tab>]` the company tearsheet, with tabs Overview, Earnings, Statements,
Revenue, Segments, Ratios, Filings, Data notes, Peers and Price (only when Connect your data is on,
below; `route.ts` `COMPANY_TABS`), every statement or segment value linked to its
filing (it opens the reader at the financial statements, a 10-K's Item 8 or a 10-Q's Part I Item 1;
the EDGAR link sits beside it on hover);
`#/filing/<company_id>/<accession>[/<section>]` parsed filing reader; `#/search` full-text search
(below); `#/settings` filing alerts, Connect your data and API keys; `#/api` Using the API
(public, below); `#/workspace/<id>`, `#/report/<id>`,
`#/thesis/<id>`, `#/model/<id>` saved work (below); `#/chart` the chart
builder (each layer a `/v1/formulas/evaluate` request; plain SVG; CSV, SVG and PNG export);
`#/ask` the assistant full width, with streamed answers, the lookups it made and verified citation links
(the badge says "N figures found in the data it was given": each figure was found in what the tools
returned and each cited filing is one a tool returned, its hover says it doesn't mean the passage a
citation points to was read: Riley 4; an answer's figures not found in the data are named under it, each a button that scrolls to and
flashes its mark in the answer: `components/FigureFlags.tsx`, the same in the check panel; a
question that got no answer says it didn't count, after agent-api's `refunded` event);
`#/compare` the comps table; `#/analysis` the Analysis tab (guided research projects, below; `#/analysis?start=<company_id>` opens New analysis on that company, from the company page's "Start an analysis", which reads "Open analysis" when there is one: beside New thesis and New model, whose forms say a thesis or model there is a single item, without the steps); `#/about` says what Tributary covers and doesn't (no prices, consensus or
transcripts; estimated dates; banks; EDGAR timing), linked from the sidebar footer and the
assistant's empty state; `#/status` the data status page (`pages/Status.tsx`, `GET
/v1/status/data`), in words an analyst or a reviewer reads, with every time in its zone: feed
freshness, how long a filing takes from EDGAR to here (typical, and 95% within), live-feed share,
completeness against EDGAR's daily indexes (business days only, which is why fewer days than the window
are checked), coverage counts, data-quality check totals (reconciled: every company with XBRL figures is
checked, and those without a quarterly series are why fewer show as having financials), grouped by what a
finding means for a reader (migration 072's tiers, `Status.tsx` `TIERS`: errors readers could see, to
double-check, kept out, coverage, information; each row worded by check and severity, `meaning`, with
companies affected and findings per 1,000 figures; an older API's rows group by severity), the
filing-section checks (documents, per 1,000 filings; absent from an older API) and the last
recorded results of the adversarial, citation and golden-figure suites (linked to their reports on GitHub at
the commit they were recorded in, which shows only on the link's hover). It is public: signed out, `#/status` renders it without the
app shell (and without calling `/v1/me`), linked from the front door; signed in, from the
sidebar footer. Every figure comes from the API; the page hard-codes none. A signed-in reader can
follow a check's company count to the screener (`#/companies?issue=<check>`: the companies they can
read with such a finding; the counts are coloured as links and a line above the table says so); the
status route itself stays aggregate, and signed out there are no such links, only a line saying
that signing in lists the companies (#55). The agent's page context reports it as `about` (agent-api has no `status` page) and
leaves the data-check filter out of the screen it sends (its filters are strict).

## Sign-in and access

Sign-in (`src/session.ts`): the front door (`pages/Welcome.tsx`) says what Tributary is beside the
way in, with figures from the public `GET /v1/status/data` (none hard-coded; if the route fails the
page reads the same without them). Its first action is "Try the demo". It calls
`POST /v1/demo/sandbox`, which creates a temporary real user (plan pro, a seeded watchlist and
workspace; see the Query API README) and returns its JWT, then lands on Home. With the public
catalog open the user reads every company at once (`/v1/me` says so). While it is closed the
sandbox gets grants on 20 companies instead, and the page polls `/v1/me` until all 20 are
readable, showing how many have arrived (access travels app DB → Debezium → Kafka → authz-sync →
OpenFGA; the BRD target is under 30 s; after that it offers "Continue anyway"). The session is
kept in localStorage until the sandbox expires; a banner shows "Demo sandbox: every company" and
"Resets in Xh" ("20 companies" while the catalog is closed), and an expired or rejected token
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
"Demo sandbox: every company", the front door says "access to every company"
(`catalog_public` on `GET /v1/demo/sandbox`), the Welcome page waits for no grant (none is
written), Search covers every company's filings (ranked within the newest window that fills the
page, with a line saying how far back, unless a company or dates narrow it), Home's feed heading
counts `readableTotal`, and Home's expected filings are the watchlist's (`scope=readable` would be
thousands of rows). Display only: the API decides.

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
(`access=financials`, `readableFor`). Named demo users and regular accounts (an owner-issued link or
a password: session kind `account`) get no such scope and keep the full public index, with
ungranted companies as metadata only.

## Company names

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

## Freshness and times

Freshness has two clocks, labelled, explained on hover (`home/useFeedStatus.ts`): a filing is *listed*
(`GET /v1/status`, the same time as Data status's "Newest filing listed here") minutes after EDGAR accepts
it, and its text *parsed* (the newest in `/v1/filings/latest`) later: within minutes for a tracked
company's filings and, since 2026-11-09, an operating company's 10-K, 10-Q and material 8-K from the
live feed (about a minute after EDGAR: the hover says so); within the document crawl's next plan (about
6 hours) for the rest (doc-fetcher README). Neither clock stands in for the other.

Times (analyst test 4, #23): a moment is written one way everywhere, the viewer's local date and clock
with their zone's abbreviation, `format.ts` `localTime` ("Nov 10, 2026, 10:14 PM MST"; `localClock` for
the clock alone): Data status, thesis and model versions, the check history, alerts, deliveries, the
watchlist's "as of", and the assistant's daily reset (midnight UTC, the service's day, said as the
viewer's own time: "resets at 5:00 PM MST"). No UTC clock beside a local one and no numeric
`toLocaleString` dates; a moment's date alone is `localDay`.

## Home

Home: the sidebar badge and the status tile count the same filings (`Workspace.fresh`: the
watchlist's with the catalog open, since the feed is then everyone's latest 50) and say the same
thing (`home/data.ts` `sinceLabel`: since your last visit, or the last 7 days on a first visit in
this browser). Removing a watchlist company is a toast with Undo (10 seconds, `components/Toasts`). Deleting a workspace or a
saved item (#158, `useSaved` `deleteWithUndo`) hides it at once and offers Undo for 10 seconds at
the foot of the window (`UndoDeletes`); the DELETE goes out when that runs out, or at once (as a
`keepalive` request) when the page closes or another user signs in, so closing the tab never keeps
something the user confirmed. It's an undo window in the browser, not a soft delete: nothing
changes in the API or the app DB, and once sent a delete is final. The assistant's own Undo of a
save still deletes immediately. While a delete waits out its window, a page that has the item (or an
item in the workspace) open stops editing it (#20, `useSaved`'s `open` leaves it out): no "workspace /
name" header and no Save over something about to go; what's on screen stays as unsaved work, and Undo
brings the open item back. "New chart" starts empty
and detached from any saved chart (the nav's Chart builder still reopens the draft). Anything that
replaces a chart with work in it (New chart, a template, a Chart button, opening a saved chart) keeps
that draft: the builder says what happened and can restore it until the next replacement.

Home's first step (F6, #127, `home/StartCard.tsx`): a "Start an analysis" card leads Home, a
company picker (companies you can read) that opens New analysis on the one you pick, the analyses
under way to continue (with their steps done), and the new filing alerts. Screen companies, New
comps and New chart stay in the page bar as quiet buttons. With nothing saved, the cards under it
are the watchlist, new filings and data notes, the three the testers said work; the status strip,
Continue where you left off and Expected filings are off (`views/model.ts` `DEFAULT_HOME`) and come
back in Customize.

## Company page

### As of a past day

Point in time (query-api `point_in_time.py`): an "As of" date field under the head's actions, in the
address as `?asof=YYYY-MM-DD` on every tab (`route.ts` `asOfDay`: a real day from 2009; today or
later is today). Set, the key figures in the head (the screener row), Statements and Ratios read the
Query API with `as_of`: the figures as they stood at the end of that day, from the filings filed by
then, each citing the first filing that stated it, readings against that day. Set, the control is a
band (sheet-2, an ink rule) and its note says what follows the day (the Overview's year and key
figures, Statements, Ratios) and that everything else is today's; every other tab opens with "This tab
shows today's ..." and each of the Overview's other sections with "Today's, not as it stood on ..."
(`Folio`'s `TodaysView`), the head's dates with "Today:" and the holding "Today you hold" (Riley, test
10, findings 18-19, 41). As of a day the key figures read gold's history (086), which its catch-up
builds company by company: where `/restatements` says `history: false` the Overview says the day's
figures aren't kept for the company yet and links Statements and Ratios for that day (they replay at
read time) instead of "not reported" everywhere (finding 7). Statements as of a day skips the
today-based "Not in these figures yet" notice (finding 3), and says EPS is split-adjusted to that day's
share basis (finding 5). "Back to today" clears it.

Restated figures: Statements asks for `originals=true`, today or as of a day, and a figure a later
filing changed carries a small hollow square (ink-3, before the figure, like a callout's dot) and its
hover the first figure, the filing that stated it and the day, and what changed it: a stock split,
another line of the filings, or the company restating the same line (`data.ts` `restatedText`). The
cell's text stays the figure. While the API says `history: false` (no history kept yet) a note under
the bar says no figure can be marked yet and points to Data notes (finding 8).

### Head and Overview

Seal pages so far (the first batch): the front door (the seal and name, a headline, the way in under
it; on the right, ruled off in ink, the live figures from `/v1/status/data` and what's in it), Data
status, the shell, the company page and Statements, and the screener; the others read through the
tokens until their own pass. The company page's head has the sector in the margin, the name, then
the facts as separate items (listing, SIC industry, fiscal year end, filer category, where it's
incorporated in words: `companies/places.ts` maps EDGAR's state and country codes, "Y9" is Jersey,
and a code EDGAR doesn't list isn't shown; currency; confidence, its reasons a tap away; the CIK, its
hover the CIK's; "Your plan: ..." only for a plan short of a tier), the next expected filing only when
the API has one ("expected around Nov 25, 2026 (an estimated date)", or due about now, or later than
estimated), two actions in the open (Start an
analysis, the one primary, and Watch) with Chart, Compare, Ask, New thesis and New model under More,
and the tabs. The Overview: **The year**, the twelve months as a sentence
(revenue, its growth, net income and net cash or net debt, `data.ts` `yearStory`: API values only,
and a figure from another period is left out rather than mixed in), sealed to its filing only when its
twelve months are the 10-K's year (from a 10-Q they are four quarters added up, and the margin says
"Four quarters to ..."); **Figures**, the key figures as a ledger (amounts, then rates), with Cash and
short-term investments (what net debt subtracts) beside Cash and a negative net debt read as "Net cash"
(the exact figure in its hover). A seal says "taken from the filing", so only a figure the filing
states carries one (`data.ts` `howFiled`, `statFiled`: a balance, a quarter from its 10-Q, the year from the 10-K);
a ratio, a difference (net debt, free cash flow), four quarters added up or a fourth quarter worked out
from the year has no seal, and its hover says what it is worked out from (`COMPUTED_FROM`, the API's
definitions in words; ROE is twelve months' net income over the average of equity now and a year
earlier). EPS (diluted) is the twelve months' like the block's other flows: `/per-share`'s TTM (the
four quarters added on today's share basis, derived, no seal), a once-a-year filer's latest year (sealed
unless a split was applied), the screener's quarter only from an older API, labelled "quarter"
(`data.ts` `epsStat`; Riley, test 10, finding 1). A balance a later 8-K may have changed keeps its figure in the ink with a red "*". On results day (an 8-K
with item 2.02 filed after the latest 10-Q or 10-K, `events.ts` `resultsOut`) a **Results out** section
opens the Overview with the press release. Without it, when the earnings answer's newest quarter has a
release and no 10-Q or 10-K (`events.ts` `resultsPending`, `/earnings` with one quarter and no tables,
asked only of a filings reader), one quiet line under the figures says the quarter's results were
announced on that day (the 8-K linked) and that detailed figures arrive with the 10-Q (the 10-K for
the fiscal year's last quarter); Statements says the same above its table (NVIDIA, Nov 2026: its Oct 25
quarter announced on Nov 17, the statements ended in July with no word why). A company with figures but
no revenue (one before revenue) reads "No revenue reported" where the revenue chart would be, never
as having no data. **Since the last report** (red-letter days, below); **Latest quarter**, the recap
set as a statement (a change from a zero or negative base reads n/m with the API's
`pct_not_meaningful` in its hover; changes carry no colour: debt and costs are in it); **Business**; **Revenue and operating margin**; **Backlog and orders**. A
foreign private issuer (`company/data.ts` `foreignReport`: its figures come from 20-F, 40-F or 6-K
filings and none from a 10-K or 10-Q, by the API's sources) files no 10-Q, 10-K or 8-K: Since the last
report says it files current reports on 6-K (no 8-K list is asked for), Latest quarter that it files
annual reports on 20-F (or 40-F) and interim ones on 6-K, so there is no quarterly recap, and Business
that its description is in that report, whose text Tributary doesn't read (analyst test 4: Birkenstock
read "no 10-Q or 10-K values" and an empty 8-K list). A
statement is a ledger table: red column rules, subtotals heavier, the bottom line (net income, total
assets, cash at the end of the period) double-ruled, the newest period washed, derived figures italic,
and its choices (statement, period, range, basis) are toggle groups. Under a statement and under
the screener one sentence stays in view; the rest of the notes (definitions, blanks, signs) are behind
"How to read these figures", closed by default, the blanks as a legend. The screener's column rules
run between its figures only.

### Statements

Company page (`pages/Company.tsx`): Statements keeps statement, period, basis and range in the
address (`#/company/{id}/statements?kind=bs&period=ttm&range=5y`, `company/statementView.ts`, defaults
left out, so a view can be linked), with a range control for Quarterly and TTM (8 quarters, 5y, 10y,
Max; wide tables open at their newest end); Annual stays ten years. Filings opens on Reports (10-K,
10-Q, 8-K; All is one click away), headed by fiscal year with gold's rule (`data.ts` `fiscalYear`,
migration 014; `filingHeadings`: on a newest-first page a heading only moves back in time, since a
10-K for FY2026 is filed in FY2027 beside that quarter's 8-Ks), and says "not parsed yet" only on
forms that get parsed. What changed sits under the table, so the table has the page's width; its
actions stay in view on narrow pages (sticky) and long 8-K item lists wrap.

Statements since F5 (migration 049; `company/data.ts` `STATEMENTS`, `cashBridges`, `company/callouts.ts`,
`company/components.ts`, `company/Components.tsx`):

- **Cash flow** reads as a statement: depreciation and stock pay (added back), then net cash from operating
  activities; capex, then net cash from investing; dividends and buybacks, then net cash from financing;
  "Exchange rates and other" (the change in cash less the three subtotals, shown only when it is more than
  rounding: JPMorgan's -$14.6B in Q4 2024); the change in cash; the cash at the start of the period (the
  end of the one before, so the request asks for four more quarters); the cash at its end (with restricted
  cash, where the statement ends); and **Beginning + change = end?**, "✓ ties" or "off $X" with the gap
  in the hover (within 0.5% or $100K: 15,620 of 16,062 quarters since 2019 tie on 613 companies rebuilt
  with 049). **One sign convention**: outflows (capex, dividends, buybacks) are negative, as filings print
  them, in the table, its CSV and the Numbers step's charts; the hover gives the amount the tag carries.
  The chart builder and screener keep the API's metric, the amount paid.
- **Balance sheet**: cash and short-term investments (049's derived line, net debt's own reading); under
  current liabilities, accounts payable, contract liabilities (customer advances: Vertiv's $1.13B to
  $4.12B beside current liabilities of $3.58B to $8.71B) and debt due within a year; contract liabilities
  current and noncurrent; and remaining performance obligations (revenue under contract not yet
  recognised, the nearest tagged figure to a backlog; Caterpillar $49.4B, NVIDIA $3.2B; Vertiv tags none).
  A dotted label has its definition in the hover.
- **Callouts** (Quarterly): a quarter the API calls out (`/financials?callouts=true`, query-api README
  "Statement callouts") carries a dot before its figure, its sentence in the hover, and a line under the
  table in **Worth a look** with a link to its filing: "Dividends paid, FY2027 Q2: -$6.05B, 25 times the two
  quarters before it (-$242.00M and -$243.00M). The filing says why." A row whose sign changes in three or
  more of the shown quarters is said once ("Net cash from operating activities changes sign in 6 of these
  8 quarters, from -$251.84B to $147.76B"). The page never decides one; it never calls a figure wrong.
- **Reported vs standardised**: a standardised line (a derived figure such as net debt, cash and
  investments, free cash flow or a ratio; gross profit computed as revenue less cost; a line tagged as
  parts) has ▸. Opened, its lines show under it across the periods (from each column's `inputs`, signed:
  "+ Long-term debt", "− Cash"), and a panel below the table shows what one period is made of
  (`/v1/companies/{id}/components`): each line's value, the tag the filing used, its filing (reader and
  EDGAR), and the other tags with a value for that date and why gold didn't use them ("not used:
  LongTermDebtNoncurrent comes first"). Ratios open the same way.

### Blanks, readings, currencies and per-share figures

What a table shows where there's no usable figure is one vocabulary, in `src/blanks.ts`: **—** not
reported, **·** no data in this period, **n/a** not applicable, **n/m** not meaningful, **$0** a real
zero. Whether a value is n/m, n/a or old is the database's call (migration 035), sent as `readings` so
the screener, Peers, Compare, Key stats and Home can't disagree with the sort behind them; `readFigure`
only words it. An n/m keeps its exact figure and reason in the hover. Ratios on negative equity (gold has
no debt/equity or ROE row there) read n/m only where `/financials?readings=true` lists the period in
`not_meaningful_periods`; the page never works out equity itself. Charts read the same rule (F7a): a
point `/v1/formulas/evaluate` marks `not_meaningful` is a gap, never the raw ratio (Triton's 1,047%
operating margin), and the chart says under it which periods read n/m and why (`series.ts`
`notMeaningfulNote`); the CSV keeps the exact figure. An old figure (035's `period_status`: over 300
days behind the company's latest period, so the company stopped tagging it) is never shown as the
company's current figure: it reads **— since Sep '14** in a table and "not reported since Sep 30, 2014"
in the Overview's ledger (no seal), with the last figure on file, its period and its filing in the hover
(analyst test 4: Deckers' 2014 net debt, Eaton's 2019 and Johnson Controls' 2016 operating margins
read as current). One merely behind shows its date and still counts. Migration 069 adds two readings:
**n/a** for a figure Tributary computes none of for the company's industry, the API's reason in the hover
(other warehouse clubs' and variety stores' gross margin: revenue includes membership fees; Walmart and
Costco read net sales apart and have one, on net sales, since 084; AT&T's and Verizon's: network
costs outside cost of revenue; also the Ratios table's blank row, from `/financials`' `not_computed`), and
*stale*, a company whose latest period is long past (a quarterly filer's 270 days, an annual filer's 500):
its figure shows with its date but leaves sorts, filters and medians (Aphoenity's Sep 2024 margin topped
a Nov 2026 sort). A figure from a 20-F or 40-F says **annual, period ended Dec '25** under it, the fiscal
year's full date in the hover (owner decision 4: an annual filer is current until its next annual report
is due). The screener's foot says when the page holds an amount in another currency. Neither n/m, n/a nor old enters a
median, a sort or a size band. A company reporting in another currency (a
20-F/40-F filer, migration 056) shows its amounts in it, never converted: `money()` writes the symbol or
the ISO code (€2.10B, CAD 1.20B, €1.87 a share); a screener, Peers or Compare cell the API reads
`other_currency` shows the figure in its currency and leaves sorts, filters and medians ("1 in another
currency" in the median's hover); the company header says "figures in EUR", the statements' unit and
note say it too (`MetricTable.currency`, from the rows' units), and a filer whose figures are fiscal
years (`cadence: "annual"`) is told so beside the statements with a button to Annual. Charts (057): a
formula series whose money isn't dollars says so (`currency`), and `toSeries` draws it in that unit
("CNY", "EUR/shares": axis "CNY", ticks CN¥996B, "EUR per share"), never "$"; a formula mixing two
currencies is refused by the API (code `currency`) and its layer shows why; an older API reads as
dollars. Alert red lines on an amount show its `unit` (CN¥1.00T). A bank reads "n/a (bank)" for gross and
operating margin, FCF, FCF margin, net debt, debt to equity and the current ratio whether or not it
tags them, by the API's `not_applicable` list. Statements: a balance sheet has no TTM (the button is off,
and a saved one reads as quarters). EPS and weighted shares come from the Query API's one per-share
pipeline (`/v1/companies/{id}/per-share`, owner decisions 1 and 2): split-adjusted to today's share basis
by default, with **Per share: Split-adjusted / As filed** beside Basis (`shares=filed` in the address)
and the figure as filed, its filing and the splits applied in every adjusted cell's hover; a fiscal Q4
derived from its net income over the year's shares less the first three quarters', TTM EPS the four
quarters' EPS added, both italic and their inputs in the hover (`perShareTitle`). A cell the pipeline
leaves blank says why. An older API (404) leaves the statement's own figures, the 10-K's in Annual.
Column pickers don't offer a balance's `_ttm` column.
What a statement's lines can't say is a computed row (`withBridges`, data.ts), italic and never in the
CSV: "Other items, net" between operating and net income where the filing tags no pretax income
(Guerrilla RF's warrant revaluation), "Not itemised" where assets aren't liabilities plus equity
(redeemable or mezzanine equity, noncontrolling interests), and a bank's interest expense says it's
already taken out of revenue. Gross profit computed as revenue less cost (migration 039) says so in its
hover. A fiscal year's month (`fyeMonth`) gives a date in a month's first week to the month before, as
`silver.fiscal_year_end_month` does for the screener's filter.

### Earnings and events

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
  offset); **management's adjustments** (its reconciliation tables, folded, and its notes that state
  a dollar amount). A block that quotes one of the release's own tables shows it as a table
  (`company/ReleaseTable.tsx`: the API's `parts`, asked with `tables=true`, parser v7; a Seal
  statement table, head rows over the columns, the label first, figures right-aligned as the release
  prints them, a total or adjusted line in 650; analyst test 4, finding 31); a release parsed before
  v7 shows its lines as before (the reconciliation in monospace). Each says when the release has none ("no section headed Outlook
  or Guidance ... read the release to be sure"; for adjustments, "none in the forms Tributary reads"
  and that a release may reconcile in another exhibit, such as JPMorgan's financial supplement). No beat or miss: Tributary has no estimates, and the
  page says so. Pins: an outlook block or a note as `quoteLines` (its lines kept), a table as
  `quoteTable` (a code block in the quote), each citing "8-K for the quarter ended …".
- **Since the last report** (company Overview, below its Figures; the What changed step's first card):
  the 8-Ks filed after the latest 10-Q/10-K period end, newest first, as red-letter days (the day
  filed, large; red when the API says the 8-K may change a balance): form, the items in the
  reader's short labels (`EIGHT_K_ITEMS`; exhibits left out), "may change cash, debt, ..." on an
  event the API says affects a balance, the 8-K's own opening words quoted (pinnable in a project),
  Read the 8-K at that passage, Read the press release, EDGAR; one not stored here says so and links
  EDGAR. Routine filings (votes, nominations, exhibits alone) fold under one line. Above them, the
  figures as of the period end a later event may have changed: on the What changed step a list, each
  with its event, and "no pro forma" (Tributary doesn't compute a figure after the event); on the
  Overview, whose ledger already marks them, one line ("Debt, net debt, debt / equity and cash are as
  of Jul 26, 2026; the Aug 17, 2026 8-K (Material agreement) may have changed them.", `events.ts`
  `staleLine`) with the no-pro-forma explanation in its hover. The same flags sit on the Overview's
  ledger (Cash, Net debt, Debt / equity; every figure for non-reliance or bankruptcy) when the
  figure is as of that period end or earlier: the figure in the ink with a red "*", and a note under the
  ledger "* At Sep 30, 2026, before the Oct 27, 2026 8-K (Acquisition or disposition) and 1 more 8-K,
  which may have changed it" (the seal's source names each event), the figure itself unchanged. The Overview asks `/events` once for both. An event the API flags from its 7.01 / 8.01
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

### Backlog and orders

Backlog and orders from the filings' text (F8b, H11; `company/TextKpis.tsx`, wording in
`company/textKpis.ts`; the Query API's `/text-kpis`, rules in its README): a card on the company
page's Overview (last) and in the Numbers step (after the backlog proxy chart), headed "Backlog and
orders", every figure labelled "from the filing's text". A chart (backlog and orders as bars, order
growth as a line on the right, one basis: organic when the filings say so more often; points open the
reader at their passage) and per figure a table, newest first (six, then Show all): as of (a date for
backlog, "quarter to ..." for orders), the figure as the API states it, and where it's written ("10-Q
filed Oct 22, 2025 · MD&A", linking the reader at the passage's offset; "also in N more filings")
with the passage quoted around its number. When a figure's newest value is older than the newest
filing read (the API's `read.latest`), a plain line under its heading says so (`notStatedSince`: "Backlog
was last stated as $15.00B at Dec 31, 2025, in the 10-K filed Feb 13, 2026. None of Vertiv's filings
since, up to the 10-Q for the period ended Sep 30, 2026, states it in the ways Tributary reads."). With
nothing stated it says what it looked for and where,
and that a company can state these otherwise or only by segment. The figures, their dates and which
one wins a disagreement are the API's; nothing is read from text in the browser. An older API (404)
reads "not available here yet"; without the filings tier it asks nothing. e2e's mock answers the
route (empty). Fixtures `company/fixtures/{vrt,nvda}-text-kpis.json`: the branch's route over
production's sections (2026-11-09).

### Filings tab and What changed

The Filings tab opens on Reports: 10-K, 10-Q and 8-K, and a foreign filer's 20-F, 40-F and 6-K, each with
its amendments (`company/data.ts` `REPORT_FORMS`); All lists every form. What Tributary covers
(`pages/About.tsx`) says it keeps no share prices, so screens, tables and charts have no valuation
multiples, and that a model gives market cap, P/E and EV multiples from a price typed into it.

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

### Insider filings

Insider filings (#117; `company/Insiders.tsx`, wording in `company/insiders.ts`'s docstring; the
Query API's `/v1/companies/{id}/insiders`, read from the SEC's XML, rules in its README "Insider
filings"): on the Filings tab's Insider filter, an "Insider transactions" table lists every trade of
the company's Forms 3, 4 and 5, newest first: the insider and their role, the date, the transaction
(the SEC's code in words, a derivative's security, an indirect holding's nature, a 10b5-1 plan), the
shares signed by direction, price, value and shares owned after, each linking its filing on EDGAR.
A name the filing writes in capitals reads in name case, in the filing's order ("STEVENS MARK A" is
"Stevens Mark A": Mc/Mac, O', hyphens, Jr. and II/III handled; `insiders.ts` `nameCase`), the name as
filed in the hover. In the filing index (Insider and All), an insider filing's row says who and what ("Kress Colette, EVP
& Chief Financial Officer · sold 34,918 shares ($7.65M) · shares withheld for tax or exercise price:
40,747 shares"). Needs filings access; an older API (404) says the details aren't available yet and
the index stays as before. Every figure is the API's; nothing is summed or valued here. The table
starts with the 100 most recent filings; "Show older filings" asks for the next 100 with the API's
`next_cursor` (an API without it shows no button: the 100 most recent, as before).

### Revenue

Revenue view (the company page's Revenue tab, `src/company/Revenue.tsx`; rules in
`company/revenue.ts`'s docstring): three sections, each chart an ordinary chart state, so "Save
to workspace" (`workspaces/SaveChart.tsx`: name and workspace, through the same item route and
state cleaner as the chart builder's Save as, without becoming the chart builder's open item)
makes it a chart item a thesis there can embed, and "Open in Chart builder" opens it there.
Values need financials access, like Statements.
Revenue and Segments each do one job (#115) and say so in a line at the top with a link to the other:
Revenue is three years of trends (trailing twelve months), what drove growth and revenue quality;
Segments is the quarters as filed, with gross profit and operating income by segment, share and YoY.
Revenue had a Quarterly switch until F7b; its tables were Segments' revenue tables again, so it went.

- **Revenue trends by segment, geography and product** (TTM, fiscal periods; `/v1/companies/{id}/
  segments?metric=revenue`): per axis (business segment, geography, product/service) a chart and
  a members × periods table (last 8 periods), each value's hover naming its filing. A member
  missing a period shows "·", never zero; one reported in part of the window says "from FY2024
  Q1" / "until FY2023 Q4". The 6 largest members (by the latest period) are named, the rest folded
  into "Other (n members)" (the sum of those reporting each period). Members that overlap the rest
  (a parent beside its children, a second cut of the same revenue: `chart/segments.ts`
  `separateMembers`, rules in its docstring) are left out of every sum and every chart, lines too, and
  read through one parent/child model per axis (`memberTree`, Riley 29-31): a child is listed indented
  under the member it is part of ("part of Data Center": NVIDIA's Hyperscale and AI Clouds), another cut
  after the rest ("another breakdown of the same revenue": Microsoft's product lines beside Product and
  Service), on the Revenue and Segments tabs alike; a parent is a set of children's sum to 0.1% in at
  least half the periods both report (at 0.5% Microsoft's lines made its Service line by chance). The
  data notes' check (migration 070) asks the same question and no longer calls overlap a deviation.
  Finding them is a subset sum over 10,000 buckets of revenue, kept in one
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

### Segments

The Segments tab (`company/data.ts` `segmentTables`): a table per axis and metric (revenue, gross
profit, operating income), the newest eight quarters. YoY is the newest quarter against the one ending
a year earlier, found by date. A member with no value in those quarters isn't listed; one that stopped
before the newest reads "until FY2025 Q3", dimmed, after the current members (a reorganisation:
Deckers' UGG Wholesale became UGG Brand). A table whose newest quarter is more than 100 days behind the
company's newest segment figure says it isn't current (Deckers reports gross profit by segment since
2025, so its operating income ends December 2024). A business-segment table ends with the filer's
"All other" (#171: its reconciling members, the API's `reconciliation` axis, summed) only for quarters
where the segments plus it come to the consolidated revenue, gross profit or operating income within
0.5% (037's tolerance; the tab now also asks `/financials` for those two). NVIDIA's reconciling member
is All Other in FY2025 (−$1.71B in Q3: $22.08B + $1.50B − $1.71B = the statement's $21.87B) but carries
the segments' total from FY2026 ($66.6B beside segments of $66.6B), which never ties, so only the
first is shown. It has no share or YoY and is in no sum.

Segment layers (`chart/segments.ts`, `chart/fetch.ts`): the Revenue view's segment charts are
chart builder layers of kind `segment` (company, segment metric, axis, 1-20 members), read from
`/segments` instead of `/v1/formulas/evaluate` (one request per company, axis, metric and view,
shared by the chart's layers), with transforms Quarterly, TTM, YoY and Contribution to growth.
They render, save, embed and restore like any layer (`model.ts` `layerKey`/`segmentRequest`,
`restoreChart`); the chart builder shows them as "Segments" cards (members fixed: they come from
the Revenue view). The assistant's page context leaves them out and reports the Revenue tab as
`segments` (agent-api's context knows neither).

### Data notes

Data notes (`src/company/notes.ts`, Query API `/v1/companies/{id}/data-notes`, `/v1/data-notes`):
the tearsheet's Data notes tab groups what the automated checks found (freshness, restated
figures, gaps and overlapping periods, values to double-check, derived quarters) in plain
language, after a Coverage group (076, `company/coverage.ts` words it): what the nightly coverage sweep
found missing from the company's figures or documents (no figures, figures starting at a re-registration
or late, a core figure missing lately, figures behind its latest report, statements in an Exhibit 13,
documents not stored) and whose gap it is in words (Tributary's to fix, not in the SEC's structured
data, outside what Tributary reads), never a colour; a history cut short by a re-registration is said
once. The status page counts the gaps per kind and reason, never a company, each with links to its filings (the reader for 10-K/10-Q text the user can read,
else EDGAR); groups run by their most important note, then their newest, with derived quarters last.
The API ranks and groups the findings (M2: `rank` attention / note / minor, a series' repeats as one
note with `repeats` and its period range, a `summary`): the tab opens on a line like "1 needs attention
· 3 worth a look · 40 minor or old, from 312 check findings", each group shows what needs attention or
is worth a look and keeps its minor and old notes behind "Show all" (`shownNotes`), and a note that
needs attention is outlined. Against an older API (no rank, no summary) every note reads as a plain
note and no summary shows. Home lists what needs attention first, then the newest notes of the user's
own companies, by name: with the catalog open, the ones they watch or
analyse (`/v1/data-notes?company_id=`, at most 50), since every company is readable; while it's closed,
the ones they can read. They need financials access, like the values they describe. A
company is checked when its figures change, so its tab says when *its* figures were last checked while
Home and Data status say when the checks last ran, from the same source (#24: Home reads Data
status's `quality.last_checked_at`, `home/useFeedStatus.ts` `useChecksRan`; it once showed its
companies' newest check under that name, "18 h ago" beside "3 min ago"). Company counts say what
they count: the sidebar's Companies count is the companies you can read (financials of any kind or a
stored filing's text; its hover says so, `Sidebar.tsx` `COMPANIES_COUNTED`), Data status's are the
companies with quarterly financials and the companies checked, and signed in it names the reader's
larger count beside the first (`Status.tsx` `companiesNote`). A note about filings the SEC's XBRL data doesn't have
yet dates them newest to oldest and promises "a day or two" only while that's true (over 3 days, it
says how long the oldest has waited). The Statements tab says the same where the figures are read
(H9, `company/data.ts` `figuresBehind`): when the newest 10-K/10-Q in the public filing index reports a
period more than a week after the figures' newest and was filed after everything they cite, a notice
names it, its period, when it was filed and how many days ago, how many quarters the figures are behind,
and links its financial statements in the reader and on EDGAR. It promises no date (Citigroup was three
quarters behind on 2026-11-08). What a note says follows the API's reading of a finding: a stock
split is one "10-for-1 stock split" note dated by when it took effect as far as the filings show
("took effect between Apr 28, 2024 and Jul 28, 2024", `notes.ts` `splitWhen`/`noteWhen`), its restating
filings listed under it and never given as its date, a restatement by a clean factor without the other side says it
may be a split, a factor of 1,000 says the unit changed, a spike the same quarter has in other years
is a seasonal peak.

### Peers

Peers (F4, H6; the company page's Peers tab and Compare's suggestions): the Query API suggests peers by
what companies do (their 10-K Business sections compared word by word, competitors either one names,
then SIC division and size: Query API README "Peers"), and the page only words its answer
(`compare/peers.ts`): each peer's "Why" says how alike the two Business sections read ("very similar",
"similar", "somewhat similar"), the shared terms ("business: footwear, wholesale channel, dtc"), "named
as a competitor in Vertiv's 10-K" or "its 10-K names Vertiv as a competitor" (a link that opens that
sentence in the reader), the same industry and a similar size; a large company of the same SIC listed after them for its size has no Business section to compare, and says so (`size_peer`). Above the table, the company's own most
distinctive terms. The columns are the answer's: a bank is compared on a bank's lines. Each median says
how many of the peers it uses ("3 of 4"), and its hover why the others are left out, in the blanks
vocabulary (not reported, not applicable, not meaningful or not reported since an earlier period,
metadata only). A peer with TTM revenue under 1% of the target's stays in the table but leaves every
median, high and low, its cells and the median's hover saying so (`MEDIAN_MIN_SIZE`, Riley 26: GSI
Technology's -100.8% moved NVIDIA's median); the table reads revenue for it even when the column isn't
shown. Compare's medians do the same. Adding a peer on Compare asks `/peers/check` first: a company with no figures to compare isn't
added, and the page says why (Birkenstock files 20-F annual reports as a foreign private issuer, under
IFRS; a company whose XBRL hasn't arrived yet; a fund with no annual report). A bank target's comps open
on a bank's columns (net interest income, efficiency ratio, provision, loans, deposits, ROE, net margin)
unless columns were chosen; the column picker offers a bank's lines only for a bank.
When there is no business text to compare, peers are by SIC code and size, and every row's "Why" starts
by saying so and why, from the API's `basis_reason` (`compare/peers.ts` `fallbackPart`, the full
sentence in its hover and above the table: `fallbackSentence`): "by SIC and size (Intel's 10-K
Business section is a cross-reference)", "no Business section found in Citigroup's 10-K", "no 10-K
from the last three years", "filings text isn't in your plan", "isn't compared yet", "no close business
match". An older API without `basis_reason` reads "by SIC and size", its `method` in the hover.

### Banks, finance arms, co-registrants and sectors

A bank (#103; the API's `not_applicable` list): the Overview's figures show net revenue, net interest income,
efficiency ratio, provision for credit losses, net income, EPS, loans, deposits, CET1 (where the bank
tags it), ROE, revenue growth and net margin; Statements show a bank's income statement (net interest
income, noninterest income, net revenue, noninterest expense, provision, pretax and net income) and
balance sheet (cash and deposits with banks: what the cash tags hold, JPMorgan's $343.3B being cash and
due from banks plus deposits with banks, Morgan 2; loans net of the allowance, total assets, deposits, total
liabilities, equity, CET1); Ratios show `BANK_RATIOS` (efficiency ratio, CET1, net margin, ROE, revenue
and net income growth, payout ratio; Riley 58: six of ten rows read n/a (bank)), the standard ratios
again from an older API that refuses the bank lines (422). JPMorgan, Bank of America and Wells Fargo tag
CET1 only under their own XBRL tags, with dimensions (the firm and the Basel III method), so their CET1
row stays blank until a dimensional pick reads it. All say what isn't shown and why: net interest margin, tangible book value
per share and ROTCE (migration 048's header). Which companies are banks is the API's alone (migration
058 added Schwab, American Express, Synchrony and Ally); nothing here lists one.

Debt with a finance arm (`company/data.ts` `FINANCE_ARMS`, `financeArmNote`): Ford's and GM's debt and
net debt are their consolidated balance sheets' (migration 055 sums each debt line's segment members),
so they include Ford Credit's and GM Financial's borrowings. The Overview (under its figures, and in
the net debt seal's source) and the Balance sheet say so. Keyed by company id; `data.test.ts` checks the ids
against 055's company tags.

Co-registrants (#135): a company that files one combined 10-K with another (PG&E Corp and Pacific Gas
& Electric) has a "combined 10-K" tag in the screener and a note on its page saying it isn't a
duplicate: two SEC registrants, each with its own statements, a parent's including its subsidiaries'.

Sectors (#136): the screener's filters have a Sector and an Industry within it (`/v1/sectors`,
Tributary's own grouping of SIC codes, not GICS), and "Group by sector" sorts by sector and industry
with a heading row for each. Several of each (uranium plus renewables): each box adds one, the
chosen ones are removable chips under it (removing a sector takes its industries), and the Industry
box offers the chosen sectors' industries grouped by sector (every sector's when the assistant set
industries alone). The API reads them as a tree: an industry narrows its own sector, a sector with
none of its industries chosen counts whole. The URL repeats `sector` and `sector_industry`; a saved
screen keeps one as the string screens always saved, several as a list (an old address or saved
screen reads as a list of one). The SIC label stays: the Industry column shows it with the sector's
industry under it, and the company header reads "Technology · Semiconductors · Semiconductors & Related
Devices (SIC 3674)".

### Price (Connect your data)

Connect your data (Query API README, "Connect your data"; shown only when `/v1/connections`
answers, i.e. the server has `DATA_CONNECTIONS_KEY`: `market/useMarketData.ts`, one request per
token shared by every component; a 404 or an error hides everything below):

- **Settings → Connections** (`#/settings`, `pages/Settings.tsx`, linked from the sidebar footer
  whether or not a provider is offered, since Settings also holds filing alerts, #60):
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

## Filing reader

Filing reader (`components/explore/FilingReader.tsx`, pure parts in `reader/text.ts`): the section
is read in full (`max_chars`, the API's cap is 1,000,000: the old 50,000 hid later matches and
cited passages; an API still at 50,000 answers 422, and the page asks again at 50,000, see
[Deploy window](#deploy-window)). A search lists its matches above the outline, opens the section at the first
highlight (or the first at a cited passage), and steps with the ↑ ↓ buttons or Enter / Shift+Enter
in the box. Two counts, labelled, because they count different things: the outline and heading show
*passages* (the search index's chunks of about 2,000 characters holding all the words, from the
API), the heading also *highlights* (each word marked, what next and previous step through). The
outline's legend says what its numbers are; only one kind shows at a time. The previous and next
filing of the same form (10-K, 10-Q, 8-K) open at the same section with the same search; one that
isn't parsed links to EDGAR. A 10-K's Item 8 under 2,000 characters that only points to the
statements (parser rule 7's measure) says which section holds them and links it: the longest later
section whose title names financial statements, NVIDIA's Item 15 (`reader/sections.ts`
`statementsElsewhere`; Riley, test 10, finding 10).
The section shows as paragraphs and tables (`reader/blocks.ts`, rules in its docstring; #178, #179),
read back from the stored text without a reparse: the parser writes one table row per line with its
cells joined by a space, so a run of lines ending in figures ("Cash and cash equivalents $ 10,605
$ 8,589") becomes a table with a label column, its date heads ("Jan 25, 2026 Jan 26, 2025") and
sub-headings, and a label whose figures wrapped onto the next line is joined back to them. Page
furniture is left out (`reader/furniture.ts`, the rule search shares, migration 068: page numbers by
a page break, "Table of Contents" links, and running headers and footers repeated by page breaks, such
as "Notes to Consolidated Financial Statements (Continued)" or a statement's "See accompanying
notes"), and a sentence a page break cut rejoins. Display only: offsets are the stored text's, so
citations, cited passages and highlights land as before (highlights are counted per table cell, the
same pieces next/previous step through). What it can't do: a table whose cells hold words (a list of
directors) stays text; cells merged across columns in the HTML can't be told apart here (the parser
records them since v7, `silver.filing_tables`; the Earnings tab reads them). Search finds a phrase
across a page break once its passages are rebuilt (services/parser README "Search without page
furniture"). The same furniture rule keeps the Analysis Business step's paragraphs and every pinned
quote clean (`analysis/project.ts` `quoteBlock`, `quoteLines`, `quoteTable`: analyst test 4,
finding 19's "6 Table of contents Backlog —").

## Search

Search (`pages/Search.tsx`): words run on Enter or the button (each search ranks every match);
form, company, dates, order and Exact phrase apply as soon as they change. Inverted dates aren't searched: the page says
so and offers to swap them. No result under filters offers to clear them instead of blaming the words.
The results open with one sentence saying what was searched (`search/state.ts` `searchScope`, from
the API's answer, not from what was asked): "647 sections in every company's filings filed since
Oct 10, 2026 match, best match first." (#81, #145). The dates a reader picks are exactly the dates
searched (`searchWindow`: `window=exact`), across every company too; only an all-company search with
no dates lets the API rank the newest window that fills a page, and then the sentence says from when,
that older filings aren't in the results, and offers "The past year" and "All dates" (`dates=all`). All
dates refused as too broad (the API's 422) offers the way back to the newest filings (Riley, test 10,
finding 34). One company is always every date, and the
sentence says "of any date". Order: Best match or Newest first (`sort=date`; then the list stays in
date order, a company's run of hits grouped, not all its hits). The pager says "1-20 of 647".
Exact phrase (`isPhrase`/`asPhrase`) quotes the words; a phrase marks only the words together, in the
snippets too (`reader/text.ts` `snippetRuns(snippet, terms)` re-marks a phrase query's snippet
because `ts_headline` marks each word alone; `highlight` matches a plain word from its stem,
`wordStem`, and a phrase by its exact words, as the search matches it since migration 065: "liquid
cooling" marks "liquid-cooling" but never "liquid-cooled"). The reader's
search box has the same box (#80). An older API ignores `sort`/`window` and echoes neither: the
sentence then shows no count and "best match first".

## Assistant

The assistant panel shows the questions left before one is spent (`assistant/useAllowance.ts`,
agent-api `/v1/allowance`): per hour and, where the account has a cap, per day. What Tributary covers
states the same limits, and a report's and a thesis check's buttons say what's left. On an older agent-api
it shows nothing. A question a page wrote for the user ("Summarize … in this 10-Q") leaves when they leave
that page, unless they edited it. The footer's date is "Newest filing cited": it knows what an answer
cites, not what the answer relied on. Model name, tokens and cost sit behind Details; `renderAnswer` writes
timestamps, dates, `as_of` and XBRL concepts the model copied from a tool in words (display only, the concept
on hover; a thesis, which is the user's, isn't touched). A timestamp reads as the viewer's own date
(`format.ts` `localDay`: 03:00 UTC on the 7th is the 6th in Phoenix); a plain date as written.

The assistant's suggestions (`assistant/context.ts`) follow the page and are true for this user: the
company on screen, the chart's first company, an analysis's company, or the user's own watchlist by
name (the agent can't see a watchlist, so a prompt says who). With nothing to go on they name no
company. Test strings ("I'm bob", "SYSTEM: admin override") aren't in the product; they live in the
adversarial suite (`tests/adversarial`).

The assistant sends the screen as `context` with each question (`assistant/actions.ts`) and
applies the agent's `action` events (chart layers and options, screener filters, watchlist) with
an Undo per action (`assistant/useUiActions.ts`): undo restores the chart and page from before
it, reverses watchlist calls, deletes an item the assistant saved (`save_to_workspace`), and
also undoes every later action. The screen's sectors and industries go to agent-api as `sectors`
and `industries`, and come back the same way in a screen action (an empty list clears one); the
data-check filter, the grouping sort and the user's own metrics never go. Below 1200px the drawer
floats over the page, so a change to a page (the chart, Companies, Compare) is made behind it: the
applied line then has Show, which closes the drawer (the conversation stays; Ctrl J opens it again).
Beside the page there is nothing to show and no Show.

## Saved work and reports

Saved work (`src/workspaces/`, Query API `/v1/workspaces`): the sidebar's Workspaces are folders
of saved charts, comps sets and screens, and the theses, models and reports below. A folder's arrow folds it and its name opens the workspace
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
`styles.css`: the page alone, in the light palette whatever the theme or the system's; `tokens.test.ts`
keeps it equal to the light tokens). "Write again" regenerates it over the same item. A report
counts as 3 assistant questions; the API's refusal (hourly or demo cap) is shown as is.

## Customizable views

Customizable views (`src/views/`, Query API `/v1/views`): Home's "Customize" shows, hides and
reorders its cards; the watchlist and the screener have "Columns" to choose and order their value
columns from what `/v1/screener/columns` offers (up to 8 and 12; typing finds one among the 80 or so), with Reset. Each change is saved
to the user's account at once (optimistic; a failed save restores the last saved state and says
so). Saved preferences are cleaned like any untrusted input (`views/model.ts`: unknown cards and
columns dropped, missing ones default) and grant nothing: values are still read through the
permission-checked routes, masked for ungranted companies. Without the app database the defaults
show and the controls are hidden.

## Theses and models

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
  Markdown, Preview or Side by side (remembered per browser); Save or Ctrl/Cmd+S, with an "unsaved
  changes" mark and the browser's leave-page warning. **Write** (F6, `thesis/BlockEditor.tsx`, rules
  in `thesis/blocks.ts`'s docstring) shows the text in boxes and every embedded chart, comps set,
  model or screen and every pinned quote or earnings-release passage as a card, drawn live: never a
  `[[embed:…]]` line. A card moves up or down past the paragraph or card beside it (↑ ↓, or Alt+↑ /
  Alt+↓ on the focused card) and goes with ✕ or Delete, with Undo in the notice; moves are announced
  and focus follows the card. A quote card says what it is and the period it reports on ("Quote ·
  10-Q · quarter ended 2026-10-26", "Earnings release · 8-K"). A chart card is a small preview (the
  comps' small-multiple drawing, at most 360 px wide) with Expand to draw it at full width and Show
  smaller to fold it back (per card, not remembered); Preview and reports draw charts in full. The
  Markdown underneath is still the
  only thing saved: the cards are read from it and every edit writes it back line for line, so
  export, import, versions, drafts and pins are unchanged (a test exports and imports a pinned
  thesis and gets the same bytes). **Markdown** shows the text as saved, embed lines and all.
  "Embed a saved item…" inserts a `[[embed:<item id>]]` line at the cursor (at the end until the
  user has placed one, never above the title) for any chart, comps set, screen or model in the same
  workspace; the preview draws each one in place as reports do (`reports/Embeds.tsx`, the data
  read now as the viewer). A comps set is drawn as its table (`compare/CompsTable.tsx`: the saved
  columns, each company's latest quarter, the peer median with how many peers each column uses ("3 of 4", beside "of 4 peers"), metadata only where the
  viewer can't read the financials), in theses and reports alike, never as charts. A marker for anything else (another workspace's or user's item, a
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
  while there are unsaved changes) through agent-api `/v1/thesis-checks`. What it costs and how
  long it takes are said before it starts (analyst test 4, finding 22), in agent-api's numbers
  from `/v1/allowance` (`check_questions`, `check_seconds`; `check.ts` `checkCost`, never a
  copy here): beside the button while no panel shows ("Uses 4 of your 28 questions · about 2
  min", the tighter of the hour's and the day's allowance), at the top of the panel ("A check
  counts as 4 questions (you have 28 left this hour, 998 today) and usually takes about 2
  minutes."), in the button's hover, and while it runs as the time so far against the usual
  ("· 0:42 of about 2 min", not read out). Without the allowance no number is said. The panel
  beside the thesis lists each factual claim with a Supported / Contradicted / Can't verify /
  **Your model** badge (a claim made of the user's own model's figures, a forecast: its model
  linked and each figure as the model shows it, "FY2026E P/E at your price 29.2x (you wrote
  29x)"; analyst test 4, finding 9), the assistant's explanation and its citations (form, date,
  "Read MD&A" opening the filing reader at the cited section, "EDGAR ↗" the filing on sec.gov
  with its accession on hover, the figure; `components/FilingLinks.tsx`, also on answers and
  reports); a filing no tool returned and a figure not found in the data are marked, never hidden,
  and the warning names each figure (a button to its mark); a figure the user's model gives (status
  `model`) isn't one of them. A check that couldn't finish says why
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
  check — re-check?" with a Re-check button (the same check and cost). The workspace page
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
  rates, empty meaning Tributary's default (shown as a grey italic placeholder, "Tributary's default"
  under it; a typed rate shows "default x%" instead): interest and
  other (% of revenue: operating income less pretax income), tax rate, share change (buybacks less
  issuance, % a year) and FCF margin (FCF as one rate on revenue rather than cash flow less capex:
  both move with revenue, and the table shows operating cash flow, capex and FCF behind it); a
  note. Each field shows its default ("default 35.3%"), and one line per default says the rule
  with the company's numbers (`defaultRules`). **The current year from actuals** (`ytd`, every new
  model): year 1 is the current fiscal year's reported quarters plus the remainder, its growth and
  margin the remainder's ("FY2026E · Q4 (Q1–Q3 reported)"); the remainder grows the same quarters
  a year earlier at the trailing twelve months' growth, at their operating margin (the TTM run
  rate, like any first year; its FCF margin is those quarters' a year earlier). Vertiv's FY2026E
  is Q1–Q3 as reported ($10.24B, +39.3%) plus a Q4 starting at +35.3% and 20.1%; Deckers' FY2027E
  is H1 plus H2; NVIDIA's FY2027E is H1 plus an H2 at +83.4% and 65.2% (last year's H2 margin moved
  by this year's change read 75.0%). Later years close half the gap each year from the TTM run
  rate to the long run (B7, unchanged). A model saved before F2 opens unchanged (year 1 a full-year guess, labelled so) and
  offers "Build FY2026E from Q1–Q3 reported", which sets each case's remainder to what its full
  year implied (`implied_remainder`), so no year changes until the user edits it.
  **The table** (`modelColumns`, `modelLines`, `ModelView`): one table with every line (revenue,
  growth, organic growth with acquired revenue, operating income and margin, reported beside
  adjusted with one-offs, interest and other, tax rate, net income, diluted EPS and its growth,
  diluted shares and their change, FCF and its margin, buybacks, and with a price the multiples)
  and bear, base and bull side by side: history years, the TTM (when it isn't a fiscal year end)
  and the current year's reported quarters once per line, in its own "Reported" row (Morgan 29:
  spanning the cases' rows from the first, the history read as the bear case's), then a row per case
  for the forecast years, the case being edited marked. A split or merger year (the API's `share_break`) shows no
  share change or EPS growth, and per-share figures before it are marked * with why. Two charts side
  by side, revenue and operating income, each on its own scale (a 4% margin's operating income read as
  zero on a revenue axis, Riley 36), history solid as "Reported", each case dashed (bear and bull lighter,
  `faint`, and each in its own colour, `CASE_COLORS`: umber and deep teal, the table's case labels
  in the same two). The table opens scrolled to the forecast, the line's name and its case frozen while the
  years scroll. The page's longer explanations (how the model works, one-offs, how the multiples are worked
  out) are one sentence each with the rest behind a closed "How this works". **Bear / base / bull**: a switch picks the case being edited; "Add bear and bull cases"
  copies the run's preview of them (the base case moved by half its growth, at least 5 points, and
  a quarter of its margin, x0.75 and x1.25 of a positive one; the rates the user set; a case's default FCF margin follows its
  own operating margin, the API's rule worded in the case's rules line), and waits for the run of the latest
  numbers so a case never starts from stale ones. Before they're added, a bear or bull tab shows
  that case's preview beside base, its switch outlined "Bear: preview" with a note that nothing is added
  yet (Riley, test 10, finding 35); the years the frozen Case column covers are blanked, never cut
  (`TableScroll` treats `data-frozen` as frozen, finding 6); a case whose numbers don't parse keeps its last valid ones
  (marked ⚠), never another case's. Check my thesis checks the base case. **Adjustments** (yours,
  labelled so): a named one-off in a fiscal quarter's operating income (Deckers' "IEEPA tariff
  refunds", $46M, Q2 FY2027: its H1 margin reads 19.9% adjusted beside 21.8% reported, and the
  defaults read the clean year), or acquired revenue, which only organic growth leaves out.
  **Guidance** (`guidanceYear`): the latest earnings release's outlook (F1's `/earnings`) sits in a
  collapsed row beside the forecast year it covers (the fiscal year of the quarter after the
  release's), quoted with its 8-K citation, never read into a field. **Valuation at your price**:
  a price per share and a day you type, saved with the model, give market cap (× the latest shares
  outstanding), EV (+ net debt, only when its reading is current; Deckers' latest is from 2014, so
  EV is blank and says why) and P/E, EV/Revenue, EV/EBIT and FCF yield for the trailing twelve
  months and each case's forecast years only: a past year reads n/a with why (`PAST_YEAR`: its
  multiple needs that year's price, which Tributary doesn't have; Vertiv's "P/E FY2021 560.6x" at
  today's price was the analyst test's finding 5), and the group's heading carries the price and
  its day. The page says it's your price: nothing is fetched or kept. A company reporting in another
  currency (057: the run's `currency`) has its table, chart and notes in it ("€2.10B"), and the price
  gets a currency picker (the company's or USD; a price saved before is dollars): a price in another
  currency than the figures gives market cap in the price's currency but no EV or multiple, and the
  card says why and to type the price in the figures' currency. **History**: every save over a
  model keeps the version it replaced (Query API, app DB 014/015); Restore loads one into the
  editor unsaved, with Undo. Only what was typed is saved, never the history or a result.
  Embedded in a thesis or report (`ModelEmbed`), a model is run from its saved state and drawn the
  same way. Tests: `model.test.ts` on real run answers (`models/fixtures/*-run.json`: the branch's
  route over production inputs of 2026-11-09; `synthetic-runs.json` for the UI tests' history),
  regenerated with `services/query-api/tests/fixtures/model/make_web_fixtures.py`, not hand-edited.

## Analysis

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
  one checks-list request per thesis, first 20) and Revisit's "new 10-Q" flag. A check that no
  longer covers the saved text reads "Stale" with its counts faded (#16), by the Monitor step's own
  rule: the check's `text_hash` against `textHash` of the saved Markdown (the listing nulls a
  thesis's state, so each checked thesis is read once). **New
  analysis**: a company search that lists only companies you can read (`readableFor`;
  `access=financials` on the API too), suggested peers from `/peers` (readable ones, the first
  3 ticked), a name; it creates the workspace with its project and opens step 1.
- **A project** (`#/analysis/{id}[/{step}[/{item}]]`, `ProjectPage.tsx`): a header with the
  company (public identity) and "n of 8 steps marked done"; the rail of the 8 steps is the one
  navigation (the header's tabs repeated it, Riley 51), with a done marker each and the thesis's
  unsaved dot. A pin's confirmation is a toast ("Open the thesis" its action), cleared when the step changes. Done means the
  user ticked it (`project.done`), never automatic and never locked: work in a step (notes, a
  saved chart, peers, a model, the Core thesis written in the user's own words, a check: `hasWork`;
  a pinned passage or an embed alone isn't writing, #30, `project.ts` `ownLines`) shows as a hint under
  the box ("1 chart saved here"). The rail's Monitor says "re-check" when the last check no longer
  covers the thesis (its text changed) or a newer 10-K/10-Q is filed. Without a step in the URL
  it opens at the first step not ticked. Each step is a pane
  that stays mounted once visited (hidden, not unmounted), so switching is instant and keeps
  everything typed; editors take `active` so only the visible one answers Ctrl+S.
- **Steps**, each reusing a screen scoped to the company: 1 **Business**: "How it makes money"
  notes (saved in the project after a pause), the latest 10-K's Item 1 in paragraphs, and the
  company page's Segments tables; 2 **Numbers**: nine chart builder states (`charts.ts`: revenue
  and YoY growth, gross and operating margin, free cash flow = operating cash flow − capex,
  buybacks and dividends against free cash flow, shares outstanding, inventory with days of
  inventory, remaining performance obligations and customer advances with the backlog in years of
  revenue on its own axis, ten fiscal years annually then the year in progress; the Revenue tab's
  receivables vs revenue; long-term debt with debt to equity), each drawn by `ChartView`, with Open in
  Chart builder, Save to project and Add to thesis; a contents row first and two charts a row where
  there's room. Outflows are drawn below zero (`-capex` formula layers). After the backlog proxy, the
  backlog and orders the filings state in words (F8b, below); 3 **Earnings**: the company page's Earnings view (below), every outlook block, note and
  reconciliation pinnable; 4 **What changed**: "Since the last report" (the 8-Ks filed after the
  latest 10-Q/10-K period end, each excerpt pinnable, the balances they may have changed flagged),
  then the filing page's `Changes` for the latest 10-Q and 10-K (a 10-Q against a year earlier,
  ranked, filters; each pin names the passage's period; a change over 520 characters opens at five
  lines with "Show the whole paragraph");
  5 **Peers**: Compare with the target fixed and the peers saved in the project (`embedded`:
  changes come back instead of going to the URL), Save to project / Add to thesis for the comps;
  6 **Model**: the project's models (ModelPage embedded, bear/base/bull side by side), "Create
  the model" from the company's history (this year from its reported quarters); 7 **Thesis**: ThesisPage embedded (no back link, no company
  picker); 8 **Monitor** (F6, #154): the company's filing alerts (`alerts/Notifications.tsx`, newest
  filing first, with the alerts job's last check), its red lines (`alerts/RedLines.tsx`: each
  "Clear" or "Crossed" on the latest figures, a form with rules to start from or a formula of your
  own; a percent is typed as 55 and saved as 0.55), then Check my thesis (disabled while the
  thesis has unsaved changes or only template text; checks run when the user asks), where the
  filings stand (Revisit's new-filing notice with a re-check, or which 10-K/10-Q the last check
  read), and the check panel (a quote opens the Thesis step and marks it).
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

## Filing alerts

Filing alerts (F6, `src/alerts/`, Query API README "Filing alerts"): a bell in the top bar with
the unread count (`useAlerts.ts`: one request shared by every hook, again every 5 minutes, on
focus and after marking read; an older API's 404 hides the bell); `#/alerts` lists them newest
filing first: form, company, filed date and period, an 8-K's items, a 10-K/10-Q's key figures with
their change on a year earlier, your red lines on it (Clear / Crossed), and links to What changed
(the company's Filings tab opened on that filing's comparison: `#/company/<id>/filings?changes=<accession>`),
Earnings, the reader and EDGAR; opening the list marks what it shows read. Settings → Filing alerts
(`AlertSettings.tsx`): 10-K / 10-Q / 8-K per followed company, saved at once, and a 7.01 / 8.01
column (8-Ks filed only under Regulation FD or Other events, off by default; disabled while 8-Ks
are off) shown only when the API's settings list `other_8k_items`: an older API is sent the forms
alone, so a save during a deploy never meets a strict refusal; webhooks (the URL
typed once and cleared, shown masked; the API's refusal sentence for anything but https to a
public address; the signing secret shown once with Copy, kept only in the component's state;
Send a test, New secret, Remove, on/off; a webhook the alerts job is leaving out after repeated
stalls says until when, `stallNote`; a demo sandbox is told webhooks need a permanent account)
and the delivery log in words ("Retrying: it answered 500; tried 2 times"); an Email section only when the
server has email. `alerts/alerts.ts` only words what the API
decided.

## Watched screens

Saved screens that alert (`src/watches/`, Query API README "Saved screens that alert"). A saved
screen is the workspace item Companies' Save… makes; under the page bar Companies lists the user's
saved screens (`SavedScreens`, "watched" beside the watched ones, the open one marked) and, while a
saved screen is open, its watch (`WatchControl`): whether it is watched, what its last check found
(`watches.ts` `lastCheck`, the API's outcome: the first check only records), and Watch… / Change…,
a form for companies entering, leaving and up to the API's `max_lines` lines (a column of the table
as shown, own metrics included; Rises above / Falls below; a value typed as the table reads it: a
percent for a ratio, $ millions for an amount; any company in it, or one picked with
`CompanySearch`). It saves the columns the page shows with the watch; with unsaved changes it says the
watch checks the screen as saved. The sidebar's workspaces mark a watched screen with a bell. The
Alerts page lists screen alerts above filings (`ScreenAlerts.tsx`): the screen (a link to
`#/screen/<id>`), what crossed a line with the value as the API read it, entered and left, each
company a link, "and N more", and how many it no longer lists because the user can't read them;
opening the list marks what it shows read. Above them, and in Settings, the screens the user watches
(`WatchedScreens`: name, what it watches, the last check), so a watch shows before its first alert
(Riley, test 10, findings 14, 72); the Alerts page says how many alerts were new on opening (it marks
them read, so the bell then counts none). `#/screen/<id>` opens the saved item and replaces itself
with Companies' address (`location.replace`). Nothing here decides a reading or a crossing: the API's.

## Portfolio

`src/portfolio/` (Query API README "Portfolio" decides every figure; `portfolio.ts` only words
them). `#/portfolio`: the portfolio's value first (on your own prices, a holding without one at
what it cost, said so: Morgan 33), a sentence (what you hold, what it cost, what it's worth on your
own prices and how far up or down), "Not investment advice", a review-due note, then the holdings table
(shares, cost, your price with its day and source in the hover, value, gain, weight by value or by
cost as the API says, next report, the linked analysis or thesis), closed holdings, the latest
journal entries; empty, it explains itself. `#/portfolio/<id>`: the position as a ledger, your
price form, the research link (a select of your projects and theses for the company), buys, sales,
the journal. `#/portfolio/import`: the browser reads Fidelity's CSV and posts its text; the preview
lists every row (keep it or not, a cost to type where the export had none, the account label as a
name) and what was left out; Add saves; the export's last prices are kept only if ticked. The
company page's head says "You hold N shares, since <day>" for an open holding. A review falls due after
a 10-K, a 10-Q or results in an 8-K ("Review due: results released <day> (8-K)"); with no red lines the
journal's form links the analysis's Monitor step, where they're set (Riley, test 10, findings 12-13).
Recording a sale opens the journal's form as sold (trimmed while shares remain) on the sale's day,
asking why, and says when its price became yours; a holding's weight reads "Weight (cost)" when the
portfolio is weighted by cost (Morgan 31).

Rules: your money is shown to the cent (`cash`, parentheses for a negative), never with a seal
(nothing here is a filed figure); umber (`--warn`) marks what asks for your writing (a review due,
a correction); journal text renders as plain text (`pre-wrap`), never Markdown; an entry past its
24 hours offers only Correct, which writes a new entry shown under it. The assistant gets nothing
of it: the page is "about" to agent-api (`uiContext`) and suggests nothing.

## Your data

`#/data` (`data/DataPage.tsx`, `data/dataset.ts`; query-api README "Your data"), in the left nav
(g d). Four folios: **Datasets** (each with its formula name `mine.<name>`, fields with their units
in the hover, counts, days, how its rows are dated; More: upload a newer file, delete after a
confirm, which can't be undone), **Upload**, **Metrics**, **How it reads**.

Upload: the browser reads only the file's first 64 KB, to list the header's columns (`headerOf`,
`guessColumns`: the company and date columns by their usual names, a publication date only
suggested, never chosen for the user). The user names the formula name, the three columns, each
value column's unit and the money's currency. Check it posts the file as a preview (`dry_run`):
what's shown is the API's answer (rows, companies, days, the rows left out by number, how rows are
dated) or each problem by row and column, never a cell (the server quotes none either). Store
sends it again for real. The file never goes to storage, a URL or a log. A demo sandbox sees why
it can't upload. `simplify` and the column names twin query-api `datasets_csv.py` (pre-fill only).

Metrics: a name, a formula (checked by `/v1/formulas/validate` before saving, with a placeholder
company: it reads no data) and the view it reads in; saved as a workspace item of kind `metric`
into "Your metrics" (made on first use). Screen with it opens Companies with `?metric=<id>`;
Companies' "Your metrics" menu adds or removes up to five, and their columns come from the API's
column info (`own`: name, formula, display; `ownColumn` words the kind, `num` for a plain number).
A metric opened from the sidebar goes to this page.

The company page's Overview ends with **Your data** (`company/YourData.tsx`) when the user has rows
for the company: each dataset's latest days, values in their field's unit and currency
(`fieldValue`), the day each was known in its hover, never a seal. Charts draw `mine.` inputs like
any (`chart/series.ts` skips their sources: no filing). Nothing of it reaches the assistant (the
page is "about" to agent-api).

Metrics: the formula box is the chart builder's (`chart/FormulaCombo.tsx`, `useMetricCatalog`: the
same metric names, tickers and functions) and also suggests the user's own fields (`mine.<dataset>.<field>`);
a problem shown clears on any edit (Riley, test 10, findings 9, 44).

## Finding a company

Finding a company (`components/CompanySearch.tsx`): one server-backed combobox everywhere a
company is picked (Chart builder, Compare, Search's filter, Analysis, thesis and model pickers;
`CompanyPicker` shows a choice and opens the search to change it). It asks `/v1/companies/search`
per keystroke (debounced, tickers, CIKs and any part of a name, best first), shows ticker and name,
works with arrows and Enter, and says when nothing matches. No page loads a list of companies: a
screener capped at 500 stopped near "ARTC" and hid NVDA, MSFT and TSLA without saying so.

## Chart builder

Chart builder (`src/chart/`): a "Chart a company" box next to the title searches the companies the
chart can read (by name or ticker) and adds the picked one's revenue (TTM) as a new line layer.
Each layer card starts with a labelled Company row (full width) and its header shows the
ticker. Names and tickers of companies on the chart come from the pick, or are asked once by id.
Tickers in a formula's suggestions come from the same search. Layers can be renamed (legend), recoloured (the colour
control is the eight series colours themselves, a radio group drawn with the classes the lines use, so it can't say
"Blue" and draw grey as the old named select did; colours stay with a layer when it moves), moved up or down, hidden; types line, bar, area, stacked bar and stacked
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
one, and while its text doesn't check the layer is off the chart, its title and its legend (the old formula's
line used to stay under the new one's error). A formula reads in words in legends and titles ("inventory ÷
cost of revenue × 365", "COST revenue ÷ WMT revenue", `formulaWords`); the card and the CSV keep what was
typed. The page's bar stays on one row: New chart, Export (CSV, SVG, PNG: one menu), Save; Filing markers and
Deck output are under Show (a count on the button says how many are on), the templates one menu (Start from /
Replace with a template). What happened (a new chart, a template over your layers, a PNG that failed) is a
toast at the foot of the window (`components/Toasts`), with Restore where it applies; Restore also stays in the
bar while a replaced draft can come back. Deck output draws the chart in the light palette (Seal's light tokens: the ink first, never the red;
`chart/export.ts` mirrors them) with a subtitle (periods and units,
editable), axis unit captions and a source footnote ("Source: SEC filings via Tributary; forms
for companies; latest filing; as of"), which SVG and PNG exports keep. Evaluate answers are kept
in memory per (token, request) for the session (`chart/cache.ts`), so coming back to a chart
asks the API nothing. Clicking a point
(or Enter on it; arrows move between quarters and layers) opens its newest source filing in the
reader. Filing markers put each company's 10-Ks and 10-Qs (public index) and 8-K results (item
2.02, from parsed filings) in a band of their own under the plot, their letters only where they fit
(10-Ks first, at least 13 units apart; the rest stay as stems, their hover names them), each company's in its
line's colour (the footnote's key says so when there are several), and on a chart of years only the 10-Ks.
The companies marked are the ones the layers read: a formula's are those its answer read, so
`MSFT.capex / MSFT.revenue` marks Microsoft's filings whatever its default company (analyst test 4: it
marked nothing).
"View as table" shows the plotted values with their sources as a data table.

### How charts tell the truth

How charts tell the truth, in every chart that draws through `chart/LayerChart.tsx` (the builder,
Overview, Revenue, Analysis Numbers, Compare, thesis and report embeds), SVG, PNG and CSV included:

- **Titles** come from the layers (`chart/model.ts` `autoTitle`: "AMZN: revenue (TTM) and net income ÷
  revenue (TTM)", "Operating margin (TTM): KO and PEP", ", annual" when it is; a quarter's figure beside a
  TTM one is "(quarterly)", or "revenue and operating margin (TTM)" read as both TTM), naming what is drawn,
  until the user types one; then the builder says "Custom title" and offers the automatic one back.
  `ChartState.title` holds only what was typed, so a chart saved before this keeps its stored title as
  a custom one. Templates and the Chart buttons leave it empty.
- **An axis holds one unit, and a unit has one axis** (`chart/axes.ts`): a layer asks for a side; if
  its unit already has a side it joins it (two percents never get a scale each: an operating margin on
  0-25% beside an FCF margin on 0-30% crossed where the values didn't, analyst test 4), if the side reads
  in another unit it moves to the other, and with both taken it isn't drawn (the card and the chart's
  footnote say so, and its values stay in the tooltip, the table and the CSV). Dollars, dollars per share,
  an index and a multiple never share one, so the smaller doesn't flatten at zero. Percents of every kind
  do. Each layer's Axis control (two buttons, Left and Right) shows the side it is drawn on, with that side's
  unit in its name and hover ("Right axis (USD)"), never only what it asked for; choosing a side moves the layer's whole unit there and the
  unit drawn there to the other side (`askSides`).
- **A balance is never "TTM"**: a layer that reads only balances (`chart/metrics.ts` POINT_IN_TIME, the
  mirror of query-api `formula/check.py`; loans and deposits included) has no "(TTM)" in its legend or
  the title, and its Transform control shows Quarterly (the quarter-end value the API answers with).
- **A ratio isn't necessarily a percent**: the API says how a dimensionless result reads from its
  formula's shape (query-api `formula/check.py`), percent, multiple or plain number, and a layer's
  "Reads as" overrides it (`display`, saved with the chart). Debt to equity is 1.91x here as in the tables,
  NVDA / MSFT a multiple, `receivables / revenue * 365` a number of days. A contribution to growth is
  in points on axis and tooltip alike.
- **The year in progress on annual charts** (F5, `chart/series.ts` `annualPoints`): after the last
  fiscal year end, the newest quarter stays as a last point labelled as that quarter and marked
  ("FY2027 Q2*"): the trailing twelve months to it for a flow, its quarter end for a balance (every
  input a balance). Its bar is lighter with a dashed edge, a line reaches it dashed to a hollow point,
  the footnote says "* FY2027 Q2: the latest quarter, not a full fiscal year (...)", and it never makes
  another layer "stop". A chart with no full year on file draws that point rather than nothing.
- **Gaps are gaps** (`chart/series.ts` `xAxis`): the axis has every period between the first and the
  last, so a missing year is a shaded hole, lines and areas break there, a point alone gets a dot, the
  table and the CSV have its row. In the builder and saved-chart embeds the axis also runs on to the
  newest period due on file by the chart's end (`chartEnd`: a custom range's last day, else the day the
  answers were read; a quarter is due 90 days after it ends, a non-accelerated filer's 10-K deadline),
  so Goldman's loans, tagged to CY2022 Q2, end in a shaded gap to CY2026 Q2 with "stops at CY2022 Q2"
  under the chart, not an axis that ends with the series (analyst test 4). Compare, the Overview and a
  model end with what is plotted. The legend lists only what is drawn. `chart/notes.ts` says the rest in
  words, under the chart and in its exports: a period with no value, a series that starts late or
  stops early (one quarter behind others isn't a gap: companies file at different times), a layer with
  no values, a chart with nothing as far back as it asked, a negative index. A layer with nothing to
  draw is asked once over everything on file, so "try Max" appears only when Max has something, and
  when nothing is on file the chart's footnote says "No values on file", as the layer does, never "in
  this range" beside it (`chartNotes` `emptyOnFile`).
- **Basis**: the Calendar / Fiscal control highlights what is applied. Fiscal quarters only line up
  within one company, so with several (counted from what the formulas really read) the chart draws calendar
  ones and says why; the choice is kept for when it's one company again.
- **Axes**: margins are as wide as the tick labels (no left gutter when only the right axis is drawn), a
  caption too wide for its margin runs from the chart's edge and sits clear of the top tick's label, period
  labels never touch and step by 1, 2, 4, 8... quarters (whole years from 4) or 1, 2, 5, 10 years, counted
  back from the last (every 7 quarters read FY2010 Q1, FY2011 Q4), a line is dashed only for a forecast (a
  right-axis line is solid, its legend says "(right axis)"), a sliver past zero (-$8K beside $30M) doesn't earn a step of
  empty axis, ticks have the decimals to tell them apart, and a lone bar doesn't fill the chart.

## Compare

Compare's dollar charts can also read on a log scale or indexed to the first value in range (not
saved; it says so, naming the chart, when the largest company's latest value in that chart's own
metric is over 100 times the smallest's: `SmallMultiples.tsx` `spreadNote`, measured per chart, never
across charts, since revenue 14 times apart and free cash flow 130 times apart is a note on free cash
flow only). "Chart these" and Peers'
"Chart margins" draw at most 8 companies, the colours a chart tells apart, and say so. The Peers table
sorts by any column like Compare (`compare/state.ts` `nextSort`/`sortRows`: high first, again to reverse,
a third time for the suggested order; empty cells last). Revenue growth is green or red; below 1,040px
"Why" moves under the name and wraps there instead of being cut (#173).

Compare's "Side by side" draws the selected columns as small multiples (`compare/SmallMultiples.tsx`):
one compact LayerChart per column, every readable company a line in the same colour on each
(at most 8), calendar quarters over 3 years, each line one `/v1/formulas/evaluate` request built
by the chart builder's rules and shared with its session cache; nothing is asked until the
charts are shown. One, two or three charts a row by the room they have (`@container multiples`); a
short last row is centred, its charts the size of the rest, so eight columns leave no empty cell. A
company with nothing to draw on a chart isn't in its legend; the footnote names it, and a line that
stops says nothing is on file after it. The target's line is heavier (`Series.strong`), so it reads
apart by weight as well as colour; "One scale for charts in the same unit" puts the charts of one unit
on one left axis (`LayerChart` `span`, not on a log scale). In the table a latest quarter more than 45
days before the newest says "a quarter older" (`olderThan`, dates only), and High and Low say what
they're of like the median (Riley, test 10, findings 32-33).

## API keys and Using the API

API keys (Query API README, "API keys"; `src/apikeys/`):

- **Settings → API keys** (`apikeys/ApiKeysSection.tsx`, after Connections): make a key (a name, Read
  only or Read and write, 30 / 90 / 365 days, default from the API), see it once (`.api-key-once`:
  the key with Copy and "I've saved it"), list the keys (name, `trb_xxxx…`, access, created, last
  used, expires or when revoked/expired, today's requests and refusals) and revoke one after the
  Radix alert dialog (`ConfirmDelete` with `yes="Revoke"` and its own line: a revoke can't be
  undone). The key lives only in the section's state until dismissed: never storage, a URL, a log or
  the assistant's context. The 5-key cap and the limits are the API's (`max_active`, `limits`);
  `apikeys/apiKeys.ts` only words them. Hidden when `/v1/api-keys` answers 404 (an older API) or
  `allowed: false` (a demo sandbox: the API's rule).
- **Using the API** (`#/api`, `pages/ApiDocs.tsx`, public: signed out it shows without the shell,
  like Data status; linked from the front door, About and the Settings section): how to make a key,
  curl and Python examples with the site's own base URL (`window.location.origin` + `/api/query`), what
  each scope reaches, the routes table and the limits, both from the Query API's generated
  `GET /v1/openapi-keys.json` (`keyRoutes`, read without the display-name pass; each route's summary
  and description as the API writes them for its users, its `code` spans as code; 404: "doesn't take
  API keys yet"), and the error codes. "Connect your AI model" says how to add the site's MCP server
  (`apiKeys.ts` `mcpUrl`: origin + `/mcp`, [mcp-server](../mcp-server)) to a client that sends a header
  (the Claude Code command), what its tools read, and that claude.ai and ChatGPT connect it by its
  address (the next section). The assistant reads it as `about` (agent-api has no "api" page).
- **In your spreadsheet** (`pages/Spreadsheets.tsx`, on Using the API): the Excel and Google Sheets
  formulas with examples, install steps (the manifest and script as same-origin downloads from
  `/addins/`), the sharing caveat (Google runs formulas with the owner's key), limits and how
  citations and readings reach a cell. The script's text is fetched only when "The script" is opened
  (nothing new on load, so #68's clean console holds), with Copy. The add-ins themselves are static
  files: README "Spreadsheet add-ins".

## Connected apps

An AI app (claude.ai, ChatGPT, Claude Code) added by the MCP server's address signs the user in
here (Query API README, "Connected apps (OAuth)"; `src/connect/`). The API decides everything; the
web shows it and passes the user's decision on.

- **The consent screen** (`#/connect?request=<sealed>`, `connect/Connect.tsx`, lazy, without the
  shell like the front door): the API's `/oauth/authorize` sends the browser here with a sealed
  request and a nonce cookie. `POST /v1/oauth/consent` (the cookie goes with it: same origin) says
  the app's name, its publisher (the host of its metadata document, or "Registered itself with
  Tributary: Tributary can't confirm who made it"), the host it sends the user back to (shown large:
  the MCP spec asks for it), a warning for a loopback address, what it can and can't do (read as
  you; no changes, no portfolio or journal, no assistant, keys, alerts or settings), who is signed
  in, and Approve (the one primary) or Deny. Both answer `{"redirect"}`; the browser goes there only
  if `connect.ts` `safeRedirect` agrees (https, or http on localhost, 127.0.0.1, [::1]; no user info
  or fragment): never a javascript: or data: URL whatever an answer says. A demo sandbox sees why it
  can't connect and may only go back (Deny). An expired request, or one started in another browser,
  says to start again from the app. `#/connect?error=<code>` words `/oauth/authorize`'s refusals
  (`connectError`) with no request and no sign-in.
- **Signing in on the way**: signed out, `#/connect` shows the front door with a notice and keeps
  the sealed request in this tab's sessionStorage (`savePending`: only a sealed request, matched by
  `route.ts` `SEALED_REQUEST`, never a URL, so the way back can't be pointed elsewhere); after any
  sign-in (GitHub's round trip included) the app opens `#/connect` with it, and the page forgets it.
- **Settings → Connected apps** (`connect/ConnectedApps.tsx`, before API keys): the address to add,
  each connected app (name, publisher, redirect hosts, connected, last used), Disconnect after the
  Radix alert dialog (every token of that app refused from its next request). Hidden when
  `/v1/oauth/connections` answers 404 (an older API, or OAuth off) or `allowed: false` (a sandbox).
- Nothing of it reaches the assistant (`uiContext` sends `#/connect` as about). Codes and tokens
  never touch the web: the code passes once through the approve answer to the browser's
  navigation; nothing is stored.

## End-to-end flows

The 23 end-to-end flows (`e2e/app.spec.ts`: try the demo → Home, screener filter/sort/watch, a
two-layer chart with a formula exported as CSV, formula autocomplete with annual frequency and deck
output, comps with the peer median as CSV and side-by-side charts, saving a chart to a workspace and reopening it, the
assistant drawer with a streamed answer (closed on a first visit, nothing floating over the page),
the narrow-screen menu, the shortcut sheet, Ctrl+arrow table jumps and the limits page, data notes on
Home and the tearsheet, writing a report from a workspace with a mocked SSE stream (embedded charts,
the flagged figure, Markdown download, print styles), customizing Home and the watchlist and screener
columns, workspace actions, a clean console on Home, Companies, a company's Overview and Statements,
Compare, the chart builder, Analysis, Alerts, Portfolio and its import, Settings, Data status and Using the API (#68), no screen
scrolling sideways on a phone, at 1024 or at 1440 with the panel open, the screener's numbers under a
frozen name with the panel open, a company reporting in euros, labelled everywhere and never ranked
or averaged with dollars, a thesis check that says what it costs before it runs and links a claim of the
user's own model, an earnings release's guidance table read as a table (parser v7), API keys: a GitHub account makes one, sees it once and revokes it, a
sandbox's Settings offers none, and the public Using the API page from the front door, and the portfolio: a
Fidelity export previewed then added, a holding by hand with why, a journal entry, the company page's line) run against `vite preview` with every `/api/*` call answered by `e2e/mockApi.ts` (route
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
| + Thesis cards, alerts, Home's first step (2026-11-09, F6) | ThesisPage 40.0 kB (14.3 kB) with the Write view; AlertsPage 1.5 kB + Notifications chunk; Settings 11.4 kB (4.0 kB); shell 471.9 kB (154.4 kB), +9.3 kB (+3.1 kB) over F5's 462.6 kB (151.3 kB) for the bell, the start card and the alerts' wording | 72.8 kB (15.0 kB) |

Before splitting, everything was one 493.6 kB (156.3 kB gzip) script; the first screen now
loads a third less. Most of what remains is React DOM. Chart data is cached in memory per
(token, request) for the session (`src/chart/cache.ts`). Lighthouse CI isn't run: `@lhci/cli`
plus a Chrome download didn't fit the CI and laptop budget; these sizes are the tracked number.

## Deploy window

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
companies keep only those; data notes without `coverage` (076) show no Coverage group, and a status
answer without `gaps` no gaps table; a data-check screen the API didn't echo (`issue`) is a failure with Try
again, never every company; `/annual-per-share`, `readings` and agent-api's `/v1/allowance` were
already optional. UI audit part 1 (Riley, test 10): the Overview's EPS from `/per-share` (404: the
screener's quarter, labelled "quarter"), `/restatements`' `history` as of a day (404: no note),
`/financials`' `history` (absent: no note), the status page's `catalog_companies` (absent: the
quarterly count) and an own metric's `not_reported` reading (an older API sends none: a plain blank).
F4: `/peers/check` and `/v1/sectors` answering 404 mean no check (the peer is added
as before) and no sector filter; a bank's statement or header columns refused (422) are asked again
without the bank lines; a save refused for a comps table's bank columns or a screen's sector filter
(an older strict model: "Input should be ..." or "Extra inputs") is saved again without them. F5: a
statement refused for 049's metrics (422) is asked again without them (no subtotals or reconciliation,
as before); an older API ignores `callouts` (no marks); `/components` answering 404 says the figure
can't be opened yet; a Numbers chart line of a metric it doesn't know says it couldn't be drawn (the rest draw).
F6: every `/v1/alerts` route answering 404 means no alerts yet: no bell, and the Alerts page, the
Monitor step's lists and Settings' section say alerts aren't available on this server yet; an older
API's notification without `filing_url` links EDGAR from the company id. The thesis's Write view and
the comps table change no request, and Home's new default arrangement asks nothing new. F1, F7b, F8b:
`/earnings`, `/events`, `/insiders` and `/text-kpis` answering 404 read "not available here yet" (the
Overview's and Statements' results-announced line just doesn't show)
(`api.ts` `orNullOn404`); a model's price currency is the user's work, so like F2's fields it is never
stripped from a save an older API refuses. API keys: `/v1/api-keys` answering 404 hides the Settings
section, and `/v1/openapi-keys.json` answering 404 makes Using the API say the server doesn't take
keys yet (the rest of the page is static). Data trust, part 2: an older API sends no `confidence`
(no mark in the header or the screener) and no tag disagreement `figures` (the note keeps its
tags-only wording); nothing new is requested. Portfolio: `/v1/portfolio` and the journal answering
404 say the portfolio isn't available yet, the company page's holding line asks
`/v1/portfolio/position` and shows nothing on a 404, and the bell counts `reviews_due` only when
the unread answer has it. Your data: `/v1/datasets` answering 404 says it isn't available on this
server yet; the company page's Your data panel asks `/v1/companies/{id}/datasets` and shows nothing
on a 404; a saved screen refused for its own metrics (`metric`, or a sort on an `m_` column) is
saved again without them; the screener sends `metric` only when the user chose one. Several
sectors and industries: an agent-api older than them refuses the question's context (422, before
anything is counted), and `ask` asks once more without the screen's `sectors` and `industries`;
a saved screen with one of each saves the strings an older Query API took (`stateOf`). Watched
screens: `/v1/watches` and `/v1/alerts/screens` answering 404 (or a 503 without the app database)
show no watch and no screen alerts (`orNullOn404`); nothing else is asked differently. Connected
apps: `/v1/oauth/connections` answering 404 hides Settings' section (`orNullOn404`); the consent
screen is reached only from the newer API's `/oauth/authorize`, so it never meets an older one.
