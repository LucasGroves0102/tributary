# Tributary build spec

Noor, 2026-11-24. **This is the single source.** It replaces Jeff's `overhaul.md` and Sana's `audit.md`; where
it differs from either, `review.md` says why. The working prototype is `prototype/transitions.html`: open the
file in Chrome; the "Prototype controls" at the foot of the rail switch motion, speed, the fallback, network
delay and theme. Its key frames are in `prototype/frames/`. Every figure in the prototype is copied from the
live site (Nov 24, 2026) and every quote is pasted, not retyped.

## 0. Where each part comes from

- **From Jeff:**
  - The frame (48px top bar, 232px rail), the 12-column grid and three page patterns.
  - Ledger tables with one unit per table, per-column sources and italic derived quarters.
  - Readings shown as a dash and a letter.
  - What changed moved into the reader, with notes beside each change.
  - The front-door specimen.
  - One 32px control height and quiet segmented controls.
  - No cards, the peer dot plot, and the assistant as a panel.
  - The page-by-page structure, and his press and popover motion.
- **From Sana:**
  - Archivo + Source Serif 4, with serif reserved for the filing's words.
  - The seal as an auditor's tick, plus Σ and *d*.
  - The footing of the headline figure, and blackline changes.
  - One yellow, and her token fixes.
  - The 11px floor; no tiles and no uppercase eyebrows.
  - The outlook quoted, not retyped, and the plain-English sentence back.
  - Responsive layouts at every width, drawn states, and her fidelity and consistency rules.
- **Mine:**
  - Red separated from loss.
  - The footing as one line.
  - Chart bars with a 3:1 edge, and deletions in pencil.
  - The figure as the hit target, and one tab stop per table.
  - When source cards open.
  - Container-query collapse, and the skeleton rule.
  - The freshness popover.
  - The motion system (§7).

## 1. Five rules every screen obeys

1. **One left edge, one focal figure.** Everything in `main` starts at the same x. Each page names the thing
   that leads, and it wins on size and weight.
2. **Serif means the filing wrote it.** Source Serif 4 is for the filing's own words only. Everything
   Tributary computes, labels or says is set in Archivo.
3. **Every figure opens its source.** A mark says how the figure relates to a filing (§5).
4. **Each colour has one job.** Seal red is the tick and the logo, green and rust are a change's direction,
   yellow is inserted text. Everything else is ink, grey or paper.
5. **The shell never moves, and numbers never animate** (§7).

## 2. Tokens

### 2.1 Type

Faces:
- **Archivo** (variable: weight 400-700, width 62-125) for the interface and every figure. Set
  `font-variant-numeric: tabular-nums lining-nums` on `body`. Archivo's digits are proportional by default
  (1111 measures 84px against 92px for 0000); `tnum` makes them equal. Verified.
- **Source Serif 4** (optical sizes) for the reader, quoted passages, 8-K lines, assistant excerpts and the
  wordmark (italic 600).
- **Codes** (tickers, form types, CIK, accession numbers, item numbers, key caps): Archivo at
  `font-stretch: 82%`, weight 600, `letter-spacing: .02em`. No mono and no condensed family: two families
  load in total.

| Token | Size / line | Weight | Use |
| --- | --- | --- | --- |
| `micro` | 11 / 16 | 500 | Axis labels, column sub-labels, readings letters, footing quarter labels. **The floor: nothing smaller, charts included** |
| `meta` | 12 / 16 (18 in notes) | 400-500 | Crumbs, labels above figures, legends, footnotes, card titles |
| `ui` | 13 / 20 | 400-500 | Tables, buttons, chips, toolbars |
| `body` | 14 / 22 | 400 | UI prose, kv rows, tabs, the assistant's answer |
| `section` | 16 / 24 | 650 | Section titles (title case over a 1px ink rule), footing terms; the plain-English sentence at 16/26 weight 400 |
| `figure` | 20 / 28 | 600 | A card's value, the hero's change |
| `title` | 28 / 36 | 650, −0.02em | Page titles (24/32 on a phone) |
| `hero` | 48 / 56 | 650, −0.025em | One per page (40/48 on a phone) |
| `quote` | Serif 15 / 24 | 400, figures 600 | Quotes, 8-K lines, the card's filing line |
| `reader` | Serif 17 / 28 | 400 | The reader and What changed, on a 660px measure |
| `display` | Serif 56 / 60 | 600, −0.02em | The front-door headline only |

A page uses at most nine sizes (the sans steps it needs plus one serif size), plus the wordmark. No fractional sizes: live uses 10.5, 11.5 and 13.5. Use
weights 400, 500, 600 and 650 only.

### 2.2 Spacing and rows

- **Scale:** 4, 8, 12, 16, 24, 32, 40, 48, 64. Nothing else: no 6, 10, 14, 18 or 22.
- **Row heights:** table 32, ratio sub-row 28, kv ledger row 36, rail row 32.
- **Coarse pointer** (`@media (pointer: coarse)`): table rows 40, every target at least 44.
- **Gaps:** 8 between buttons, 16 from a section title to its content, 24 grid gutter, 40 between sections
  in a stack.

### 2.3 Colour

Contrast ratios are WCAG 2.x, measured on each ground (`tools/colour.py`). The rules come first.

- **Seal** is the ✓ tick and the logo. Never a line, a box, body text or a change.
- **Gain and loss** colour a signed change (QoQ, YoY, growth, bp) only, always with `+` or a true minus
  `−`. Never a level. A change in debt is uncoloured.
- **A ✓ never sits on a coloured figure.** Ticks go on levels, colour goes on changes, so seal and loss can
  never share a cell. The two hues also sit far apart (CIEDE2000 15.2 light, 26.9 dark). Sana's pair
  measured 5.7 and 5.2: the same red.
- **Yellow** is inserted text in What changed only: tint plus underline. No yellow notices, dots, counts or
  hero swipes.
- **`disabled`** is for disabled controls only, never content. Content never goes below `pencil`.

| Token | Light | on paper / sheet / well | Dark | on paper / sheet / well | Job |
| --- | --- | --- | --- | --- | --- |
| `paper` | #F4F4EF | | #0E100F | | Page ground, rail |
| `sheet` | #FFFFFF | 1.10 vs paper | #151816 | 1.07 vs paper | The reader's page, popovers, the panel, active rail item, row hover |
| `well` | #ECEDE7 | 1.07 vs paper | #1C201D | 1.16 vs paper | Fields, segmented tracks, skeleton values |
| `ink` | #111513 | 16.69 / 18.41 / 15.64 | #E8EBE7 | 15.88 / 14.88 / 13.71 | Text, primary button, header and total rules, focus ring |
| `ink-2` | #39413D | 9.53 / 10.51 / 8.93 | #BCC4BE | 10.71 / 10.03 / 9.25 | Secondary text, row labels, Σ and *d* marks, change bars |
| `pencil` | #5C6560 | 5.46 / 6.03 / 5.12 | #98A29C | 7.26 / 6.80 / 6.27 | Meta, units, captions, **deleted text** |
| `disabled` | #8E9691 | 2.75 / 3.03 / 2.58 | #5D6661 | 3.22 / 3.01 / 2.78 | Disabled controls only |
| `rule` | #DEE2DC | 1.19 / 1.31 / 1.11 | #252A27 | 1.31 / 1.23 / 1.13 | Row hairlines (decorative: structure also comes from alignment) |
| `rule-2` | #7F8781 | 3.35 / 3.69 / 3.14 | #69736D | 3.89 / 3.64 / 3.36 | Field, chip and secondary-button outlines (≥ 3:1) |
| `seal` | #A3121D | 7.15 / 7.89 / 6.70 | #F7889C | 8.14 / 7.63 / 7.03 | The ✓ and the logo (logo disk stays #B3111E) |
| `gain` | #0E6B41 | 5.95 / 6.57 / 5.58 | #5CC68E | 9.02 / 8.45 / 7.79 | Positive change |
| `loss` | #A8460B | 5.37 / 5.92 / 5.03 | #EE9A5A | 8.55 / 8.01 / 7.38 | Negative change (rust, not seal red) |
| `ins-bg` | #FFEC99 | ink on it 15.53 | #4A3F12 | ink on it 8.69 | Inserted text's tint |
| `ins-line` | #8A6A00 | 4.59 / 5.07 | #E6C13A | 10.96 / 10.27 | Inserted text's 2px underline |
| `bar` / `bar-edge` | #C9CFC9 / #858D87 | edge 3.09 / 3.41 | #2C332F / #5F6862 | edge 3.31 / 3.10 | Chart history bars: a light fill with a 1px edge at ≥ 3:1 (WCAG 1.4.11) |
| `bar-latest` | #111513 | 5.40 vs bar edge | #E8EBE7 | 4.79 vs bar edge | The latest period |

Charts with more than one company: ink for the target company, then at most three more series that differ in
both colour and dash or marker. The categorical values must pass a colour-vision-deficiency check before they
ship; neither Jeff's palette nor mine has been tested.

### 2.4 Surfaces, radius and focus

- **One depth strategy:** hairlines plus surface steps. A single shadow, `--float`
  (`0 0 0 1px rgba(17,21,19,.10), 0 14px 36px -10px rgba(17,21,19,.28)`; dark
  `0 0 0 1px rgba(232,235,231,.12), 0 18px 44px -10px rgba(0,0,0,.75)`), goes on things that float only:
  popovers, menus, dialogs and the palette.
- **No cards.** The reader's page and the assistant panel are `sheet` surfaces with a hairline.
- **Radius:** 3px controls, 4px segmented track, 6px popovers and dialogs, 0 tables and sections.
- **Focus:** a 2px `ink` outline with a 2px offset on every control, as the live site does today. A heading
  that receives focus after navigation shows no ring.

### 2.5 Controls

- **Height:** 32px on desktop. On a coarse pointer, 40px visible inside a 44px target.
- **Buttons:**
  - Primary: `ink` fill with `paper` text (16.69:1). Exactly one per page header.
  - Secondary: `sheet` with an inset `rule-2` outline.
  - Quiet: text only, for actions inside rows.
  - Destructive actions live in the `···` menu, never as red text.
- **Fields:** `well` fill, inset `rule-2` outline, 2px ink ring on focus. Labels sit above, 12px/500, in
  `ink-2`.
- **Segmented control:** a `well` track with a `sheet` thumb and a `rule-2` ring, 2 to 4 options. At most
  two in one toolbar; anything more goes into a "View ▾" menu.
- **Chips** (filters): 32px, `rule-2` outline; an applied filter gets an `ink` outline. Applied filters show
  as chips above the table: one filter pattern everywhere.
- **Selects and date pickers** use a headless popover, never the native `<input type=date>`.

## 3. Grid and breakpoints

| | ≥ 1440 | 1200-1439 (drawn at 1280) | 768-1199 (tablet) | < 768 (drawn at 390) |
| --- | --- | --- | --- | --- |
| Top bar (48) | Logo · search 440 · Live · Alerts · Assistant · account chip | Search `clamp(200px, 30vw, 440px)` | Menu button · logo · search · Assistant; no account chip | Menu · logo · search icon · "Assistant" |
| Rail | 232, same paper, hairline right | 232 | Drawer, 280 wide, over a scrim | Drawer |
| Content | 32 side padding, max 1144 | 32; 984 at 1280 | 32 | 16 |
| Columns | 12, 24 gutters | 12, 24 | 8, 24 | 4, 16 |
| Lead + side (7 + 5) | Side by side | Side by side | Stacked when the content box is < 900 | Stacked |
| Reader (TOC · page · notes) | 208 · ≤ 660 · ≥ 180 | Same | TOC in a "Contents" menu; notes fold under each change below 860 | Same; one "Options" control replaces Against and Order |
| Tables | Full width, no column pagers | 8 quarters with no pager | Scroll sideways inside the table, sticky first column | Same; rows 40 |
| Assistant panel | 400, pushes content | 400, pushes content | Overlays content | Full screen |

- **Collapse by container, not viewport.** Lead + side stacks on `@container (max-width: 900px)`, so opening
  the 400px panel at 1440 (content 744) collapses the Overview the same way a narrow window does. Reader
  notes fold at `@container (max-width: 860px)`.
- **No horizontal page scroll at any width.** Only tables scroll, inside their own box. The prototype
  measures `scrollWidth` = viewport at 390, 1280 and 1440.
- **Measured targets (prototype):**

| Width | Tabs end | Hero | Supporting ledger |
| --- | --- | --- | --- |
| 1440 | y = 268 (live 394) | y = 312 | y = 627 |
| 1280 | 316 | 360 | 701 |
| 390 | 453 | 497 (live's first standalone figure: 1026) | 972 |

## 4. Components

- **Top bar:** logo; the search button (Ctrl K) opens the command palette. A status dot with "Live" opens
  a popover: "Newest filing listed 1 min ago · typically 51 s from EDGAR to here", linking to Data status.
  Then Alerts with a count, Assistant (Ctrl J) and the account chip ("Demo sandbox · resets in 23 h"). The
  theme control moves to Settings.
- **Rail:** Home, Companies (with count), Compare, Charts, Analysis, Portfolio, Your data. Below them,
  Watching: code, name and a "new" label (11px/600) for a new filing; no figures, no yellow dots. Then
  Workspaces. The footer holds Data status · API · Settings · Shortcuts ?. The active item is a `sheet` chip
  with a `rule` hairline.
- **Page header:** optional 12px crumb, 28px title, one 13px meta row with 16px gaps. Actions sit on the
  title's baseline: secondaries, then one primary at the far right.
- **Company header:**
  - Crumb (Companies / SIC label · SIC code); name, code at 16px and exchange at 13px.
  - One meta row: fiscal year end, filer status, state, CIK, Data confidence.
  - Actions: Watching, Compare, `···`, then Start an analysis as the primary.
  - An optional one-line notice ("Results out Nov 17 · …" with "Read the release →" at the right).
  - Then 40px tabs, with "As of" as a Today | Pick a day segmented control.
  - **Must end at ≤ 270px from the page top at 1440.**
  - On a phone: one action row (the primary plus `···`) and a notice of at most two lines.
- **Tabs:** 14px/500 `ink-2`; the active tab is `ink` with a 2px ink indicator. Counts are 12px `pencil`
  ("Data notes 45"). Tabs are links (each is a route) with `aria-current="page"`.
- **Ledger table:**
  - Header cells carry up to three lines: label (12/500 `ink-2`), period end (11 `pencil`), source form
    (11/600 at 82% width, "10-Q", "10-K *d*"). The unit goes in the first header cell ("USD billions"), one
    per table, with 3-4 significant figures.
  - A 1px ink rule under the header and no vertical rules.
  - Sub-rows indent 16px in `ink-2`. Totals are weight 650 under a 1px ink rule. Net income and total assets
    also get a 3px double rule beneath.
  - Ratio rows are 28px, italic, `ink-2`. Derived columns are italic.
  - Negatives are in parentheses; changes take a true minus.
  - Hover tints the row `sheet` (instant on enter, 80ms out). A selected or watched row gets a 2px ink notch
    at its left edge.
  - Row actions sit in a fixed 64px slot at the row's end: hover-only on a fine pointer, always visible on a
    coarse one.
  - Sorting turns the header `ink` and adds "↓".
- **Keyboard grid (every ledger):** one tab stop per table. ↑↓←→ move between cells, Home/End jump to the
  row's ends, Enter or Space opens the cell's source card, and Escape returns focus to the cell. This is the
  live site's own model; the prototype implements it.
- **Figure + mark:** a `<button>` that wraps the figure and its mark, so the whole figure is the target
  (≥ 24px tall on a fine pointer, ≥ 44px on a coarse one). The mark is 14px (22px on the hero).
- **Source card** (non-modal popover, `role=dialog`, 368px):
  - The card holds: the title (12px: what and which period); the value as the filing prints it (20/600,
    "$96,221 million") with its mark and words ("✓ agrees to the filing"); where it sits (form, filing date,
    Part and Item, statement name); the filing's line in serif on `paper` with the figure underlined in
    seal; and "Open at this line in the reader →".
  - A derived figure shows its rule ("the 10-K's fiscal year less the three 10-Qs before it").
  - Σ shows its terms. A ratio shows its formula and inputs.
  - A card never retypes a quote and never shows an exact figure Tributary doesn't hold.
- **Readings:** a dash plus an 11px/600 letter, with the legend under the table. `a` not available for this
  company, `b` none filed since a date (in the card), `c` annual figure (20-F/40-F, year to a date), `k` a
  bank (the line doesn't describe one), `s` older balance sheet. "n/m" stays as text. On a bank's own pages
  its own lines replace the dashes.
- **Footing line** (§5.3).
- **Notice:** one line on `sheet` with a hairline, the bold label first and the link at the right; at most
  two lines on a phone. No yellow edge.
- **8-K row:** date (13/600) · the quote in serif 15/23, always pasted, cut with "…" at sentence
  boundaries · `8-K 5.02` (code) · item name in words. Never an item number without its name.
- **Quote block:** serif 15/24 with the figures in weight 600, and the source under it (form, filed date,
  exhibit, heading).
- **Chart:** ink for the latest period, `bar` with `bar-edge` for history, hatched in `bar-edge` for derived
  periods. Hairline gridlines at round values and 11px axis labels. Values label the first and last bar
  only. No title inside the chart, no dual axes. "View as table" sits on the section title row. A footing
  term's hover or focus outlines its bar.
- **Reader:**
  - TOC: 13px, item numbers in a 22px `pencil` column; the current item is a `sheet` chip. In What changed
    mode, change counts appear as numbers (650 weight).
  - The page itself: a `sheet` with a hairline, Source Serif 17/28 at ≤ 660px.
  - The filing's tables keep the filing's units and column groups ("Three Months Ended" spans its columns).
  - A cited line keeps a 2px seal frame round the figure, and the margin says where Tributary uses it
    ("Cited on Nvidia's pages … shown as $96.22B ✓").
- **What changed (blackline):**
  - Insertions: `ins-bg` tint plus a 2px `ins-line` underline at 4px offset.
  - Deletions: struck through in `pencil`.
  - A 2px `ink-2` change bar beside each changed passage.
  - A wholly new passage is labelled "New passage" and barred, not tinted.
  - Screen-reader text marks the start and end of each insertion and deletion.
  - Notes beside each change carry only the ranker's facts, in its words: "rewritten (32% different)",
    "4 new · 3 gone", "$14.7 → $99.3", "new: tariff", "Liquidity and capital resources". No headlines, no
    invented labels or units.
  - Toolbar: Read | What changed, then "Against ▾" and "Order ▾" as menus.
- **Assistant panel:** 400px `sheet` with a hairline left.
  - The question at 14/600; the answer in Archivo 14/22 with figures at 600 and the same marks as the page.
  - Quoted drivers in serif with their source line.
  - A sources list, then the machine check ("✓ 8 figures found in the data it was given").
  - The input in the footer, with the quota as an 11px `pencil` line, not a meter.
  - Hovering a cited figure in the answer outlines the same figure on the page (product work, §8).
- **Command palette:** 560px, centred at 12vh, with an input and a list of results. Arrow keys and Enter;
  Escape closes. Opens and closes **with no animation** (§7).
- **Menus and dialogs:** `sheet`, 6px radius and `--float`. Menus are origin-aware; dialogs are centred.
  Every action lives in a menu; Compare, Chart and Ask are buttons, not menu items.
- **States, drawn for every data section:**
  - Loading: the page's own shape (§7.6).
  - Empty: the thing's shape with one line and one action, such as the holdings table header with a dash
    row and "Add a holding · Import from Fidelity".
  - Error: inline per section with a retry, so the rest of the page loads.
  - Partial: the section with readings letters.
  - Expired sandbox: a notice plus "Start a new sandbox".
  - Quota spent: the input disabled, with the reset time.
  - First-run Home: "This is a sample: the sandbox watches six companies for you."

## 5. The citation and tick system

### 5.1 Marks

| Mark | Colour | Means | Put it on |
| --- | --- | --- | --- |
| ✓ | `seal` | **Agrees to the filing**: this figure is printed in one filing at this value (rounded as shown) | Standalone levels: a quarter's line, a balance (Cash $22.44B ✓) |
| Σ | `ink-2` | Added up from figures that carry a mark | Twelve-month sums and totals (Revenue $303.0B Σ) |
| *d* | `ink-2`, italic, and the figure italic | Derived: fiscal Q4 = the 10-K's year less its three 10-Qs | Derived quarters (*68.13 d*) |
| no mark | | A ratio or difference of marked figures; its card shows the formula | Margins, ratios, net cash |
| a b c k s, n/m | `ink-2`, 11px | A reading: not a plain figure | Dashes, annual figures, banks |

- **Tables don't put marks in cells.** Each column names its source in its header ("10-Q Aug 26, 2026",
  "10-K *d*"); every cell still opens its own card. This keeps a 96-cell statement from becoming 96 ticks.
- **The legend** sits on one line directly under the schedule it explains, never at the page foot.
- **The assistant uses the same marks and the same card,** so an answer looks like a page of the product.

### 5.2 Interaction

- **Hover:** a standalone figure opens its card after 200ms on a fine pointer, and immediately if another
  card closed less than 400ms ago (the tooltip rule). Table cells never open on hover; they tint.
- **Click, tap, Enter or Space** opens the card. Opened by keyboard, focus moves into the card's link.
  Escape closes it and returns focus. A click outside closes it. Focus alone does not open a card.
- **"Open at this line in the reader →"** opens the filing with the line in view and framed (§7.4).

### 5.3 The footing (the signature)

Under every hero that is a sum, show its arithmetic as **one ledger line** between hairlines:

`57.01 ✓ + 68.13 d + 81.62 ✓ + 96.22 ✓ = 302.98 Σ`, with quarter labels under each term (11px, "Q3 FY26")
and the unit under the total ("USD billions").

- Each term is a figure button (≥ 44px tall) that opens its own card and outlines its bar in the chart.
- The line replaces any "these four" bracket on the chart. It is 58px tall, where Sana's table took 230.
- On a phone it wraps to two lines.

## 6. Page rules

- **Front door:** no card. The headline is in Source Serif 56px ("Every figure sealed to the filing it came
  from."), then the lead, "Try the demo" (primary, 40px) and "Sign in with GitHub".
  - At the right, the specimen: the 10-Q's income-statement lines with 96,221 framed in seal and
    "✓ agrees to the filing". No shadow.
  - Below, the proof as one ledger row (51 s typical, documents stored, 128 of 128 figures traced, 0 of 59
    attacks leaked), read live from Data status rather than typed in. Then "Not here, on purpose".
  - No eyebrow, no stat tiles, no numbered list.
- **Home:** "Since you last looked".
  - Lead column: new filings on watched companies, grouped by day, each with code, item name in words and
    one pasted line; 10-Q and 10-K rows link to What changed.
  - Side column: Coming up (expected filings as a ledger with a tilde on estimated dates) and a one-line
    data-notes summary.
  - Then the watchlist ledger, full width. First run says it's a sample.
- **Company Overview:**
  - Lead column: hero (twelve-month revenue, 48px, with its change), then the footing line, then the legend,
    then the plain-English sentence (16/26).
  - Then "The rest of the twelve months" as a two-column kv ledger with marks, then Latest quarter (Q, prior
    Q, QoQ, year-ago Q, YoY; per-column sources).
  - Side column: Revenue by quarter (8 bars, derived ones hatched), Since the last report (8-K rows), and the
    Outlook as the release states it (quoted whole), then Peers as a dot plot.
  - No tiles.
  - Banks get their own lines (net interest income, provision, loans, deposits, efficiency, CET1) in the
    same ledger.
  - A 20-F filer opens on its annual figures in its own currency (TWD shown in the unit cell and the hero).
    Absent sections collapse to one line in the section header ("Files 6-Ks, not 8-Ks · Filings →").
- **Earnings:** the quarters as a segmented row; the release headline; the outlook quoted (never re-typed
  into a table); reconciliations as collapsible ledgers titled in sentence case.
- **Statements:**
  - One toolbar: statement and period segmented, an "8 quarters · Fiscal ▾" menu, Export CSV at the right.
  - One unit; YoY and margin rows; derived columns italic; the keyboard grid.
  - Annual filers default to Annual.
  - No micro-bar column, no column pagers.
- **Revenue / Segments:** each breakdown is one section: the chart (7 columns) and its ledger (5 columns).
  "Sum vs reported" is a footnoted total row; overlapping members are indented under their parent.
- **Ratios:** a full-width ledger with a 200px name column and names on one line; a row expands to show its
  inputs.
- **Filings:** a full-width ledger (Filed · Form · Period · Items in words · actions in a fixed slot: Read,
  What changed); the form filter as a segmented control; the date range as one "Any time ▾" popover.
  Insiders sit in a sub-tab, grouped by insider.
- **Data notes:** a summary line with counts by tier, then a ledger of notes; each explanation expands in its
  row.
- **Peers:** a ledger with one line per peer. Why it's a peer becomes a short pencil tag ("named as a
  competitor · same SIC"), never keyword soup. The median is a total row.
- **Companies (screener):** applied filters as chips above the table; "Save and watch…" as the primary
  action; 9 metric columns at 1440 and 7 at 1280, no pagers; readings letters; a row opens the company
  (§7.4).
- **Reader / What changed:** §4. The Filings row's "What changed" opens the reader in that mode.
- **Analysis:** a step list on the left, the step's work in the centre, each step ending in one
  "Next: …" action. On the Thesis step, each claim sits beside its figure, with its mark and its current
  value.
- **Assistant:** the panel (§4); the full page is the same component at reader width.
- **Compare, Charts, Portfolio, Alerts, Your data, Settings, Status, API:** a standard page header, one
  ledger each, and empty states that show the table's shape. Alerts use Home's filing row. Status leads with
  the proof ledger. API is a reference layout (TOC 3 columns, content 9).

## 7. Motion system

### 7.1 What motion is for

This is a tool people use for hours. Motion has three jobs only: to say where you went (continuity), to
confirm the tool heard you (feedback), and to hand loading over to content without a flash. It is fast,
uses only `transform` and `opacity` (plus the source frame's box), and never draws attention to itself.
Nothing bounces, overshoots, staggers in or counts up.

### 7.2 What never moves

- **The shell:** top bar, rail, an open assistant panel. On a tab change the company header stays put too,
  and only the tab indicator slides.
- **Numbers:**
  - A figure never tweens, rolls or counts.
  - A changed value swaps. When content fades, the old view is gone before the new one is legible, so two
    figures are never readable in the same place at once.
  - Chart bars never grow from zero; they appear at their value.
- **Keyboard-initiated actions:** page changes by Enter, Ctrl K, Ctrl J, tab and grid navigation, cards
  opened with Enter. They are instant: detect with `event.detail === 0` for activation and key events
  for shortcuts.
- **The command palette:** opened 100 times a day, never animated, whichever way it opens.
- **Sorting a table and moving through it with the keyboard:** instant.

### 7.3 Tokens

| Token | Value | Used for |
| --- | --- | --- |
| `--ease-enter` | `cubic-bezier(.23, 1, .32, 1)` | Things appearing, fades, popovers, press (the live site already uses it at 120ms) |
| `--ease-move` | `cubic-bezier(.32, .72, 0, 1)` | Things that travel: shared title, source frame, tab indicator, panels |
| `--t-press` | 120ms | `scale(.97)` on buttons, not rows or links |
| `--t-hover-out` | 80ms | Hover background off (on is instant) |
| `--t-out` | 90ms | Old view fades out |
| `--t-in` / `--t-delay-in` | 160ms after 60ms | New view fades in (and slides 12px on a depth change) |
| `--t-pop-in` / `--t-pop-out` | 140ms / 90ms | Source card, menus |
| `--t-tab` | 180ms | Tab indicator, segmented thumb |
| `--t-shared` | 260ms | Shared title |
| `--t-frame` | 280ms | Source frame |
| `--t-panel-in` / `--t-panel-out` | 240ms / 180ms | Assistant panel, rail drawer |
| `--t-values` | 120ms | Loaded values replacing their skeleton |
| `--shift` | 12px | The only travel distance for page content |

No UI motion is longer than 280ms; exits are faster than entrances. There is no ease-in, no spring, no
`transition: all`.

### 7.4 Each transition

| Transition | What moves | Timing | Rule |
| --- | --- | --- | --- |
| **Lateral page change** (rail: Home ↔ Companies) | The page head and content only | Old 90ms out, new 160ms in after 60ms; no slide | The shell is static (named and frozen); nothing travels |
| **Deeper: screener row → company page** | The company name travels from its row into the page title. Content enters 12px from the right | Title 260ms `ease-move`. Content as above. The head fades in after 100ms so the travelling title never crosses legible text | A shared element joins two views of **the same thing** only: never a row to a different company, never a figure to a different value |
| **Back: company → screener** | The reverse: the title flies back into its row; content enters 12px from the left | Same | Direction comes from depth (screener 1, company 2, reader 3), not from browser history |
| **Company tab switch** | The 2px indicator slides to the new tab; the panel cross-fades in place | Indicator 180ms `ease-move`; panel 90 out, 160 in after 60, no slide | The header doesn't move or fade. By keyboard: instant |
| **Figure → filing reader** (card's "Open at this line", or a statement cell) | A seal **source frame** travels from the figure to the line it came from, and stays there as the cited state. The rest is a deeper page change | Frame 280ms `ease-move`, drawn by the transition group itself (no bitmap scaling) | The digits never morph (96.22 never blends into 96,221): only the empty frame travels. Back from the reader flies it home |
| **Reader: Read ↔ What changed** | The segmented thumb slides; the page cross-fades in place | Thumb 180ms; page 90 / 160 after 60 | The header and TOC position don't move |
| **Source card open / close** | Scales from 0.97 at the trigger's corner, with opacity | 140ms in, 90ms out (opacity only) | Origin-aware (`transform-origin` at the trigger). Instant if another card closed < 400ms ago, or opened by keyboard |
| **Menus** (filters, View ▾) | As the card | 140 / 90 | Origin at the trigger |
| **Dialogs** | Scale 0.98 → 1 with opacity, centred | 180 / 120 | Centre origin |
| **Assistant panel open** | The content reflows to its new width at once, at frame 0; the panel slides in over the freed space | Slide 240ms `ease-move` | Main never animates its width (no per-frame table reflow). Opened with Ctrl J: instant |
| **Assistant panel close** | Content widens at once; the panel slides away, uncovering it | 180ms `ease-enter` | Focus returns to the opener |
| **Rail drawer** (< 1200) | Slides from the left over a scrim | 240 in / 180 out | |
| **Filters updating a table** | Rows that stay don't move or flash; new rows fade in; removed rows go at once; the count swaps | New rows 120ms opacity. A 2px progress line on the header rule appears only if results take > 150ms | No row choreography: the user wants the result, not a show |
| **Loading → content** | §7.6 | Values 120ms | No layout shift |
| **Hover / press** | Row and button tint on at once, off in 80ms; buttons press to 0.97 | 120ms | Hover effects only under `(hover: hover) and (pointer: fine)` |

### 7.5 `prefers-reduced-motion`

Nothing travels and nothing overlaps:
- **Page changes:** the old view goes at once, and the new one fades in over 120ms (linear). There is no
  12px slide.
- **Shared elements:** no shared title and no travelling frame. The reader simply shows its framed line.
- **Indicators:** the tab indicator and segmented thumb jump.
- **Popovers:** 100ms opacity only.
- **Panels:** the assistant panel and rail drawer fade in 120ms instead of sliding.
- **Small motion:** no button press scale, no skeleton pulse; the progress line is static.

Colour and opacity changes that aid comprehension stay. The prototype honours the system setting and has a
manual override.

### 7.6 Loading hands over to content

Live today, a row click hard-cuts to six generic grey bars at 48ms, and the page arrives at ~390ms. A tab
switch flashes bars for ~180ms. The rule that replaces it:

1. **The skeleton is the page's own shape:** real section titles, row labels, column headers and rules,
   with only the values blank. Each blank value is a `well` block the exact width of the value to come.
   Nothing shimmers.
2. **The head renders at once,** because it is known from the row that was clicked. The transition carries
   the skeleton in.
3. **When data arrives, values fade in over 120ms in place,** as colour from transparent to ink: no tween,
   no shift. Marks and chart bars fade in with them.
4. **Still loading after 1s:** the blanks start a slow 1.2s pulse to say "still working". After 10s, the
   section's inline error with a retry.
5. **Cached data** (Back, a revisited tab) skips the skeleton entirely.

### 7.7 Implementation

- **Same-document View Transitions**, via `document.startViewTransition(update)`. Set
  `document.documentElement.dataset.nav` to `forward | back | lateral | tab | mode` before the call, and
  clear it on `finished`. Always catch the aborted promises (`ready`, `updateCallbackDone`, `finished`): an
  aborted transition still updates the DOM. Scroll to top, or to the cited line, inside `update`.
- **Names:**
  - `topbar`, `rail` and `assist`: `animation: none`, old hidden. The shell is frozen.
  - `page-head` and `content`: no group animation; old fades, new fades and slides.
  - `entity-title`: set on the clicked row's name and on the page's `h1` only when both show the same
    entity.
  - `source-frame`: set on an empty frame element at both ends. The group itself draws the 2px seal border
    and the image pair is hidden, so the box morphs crisply.
  - Remove the transient names on `finished`.
- **Fallback** (no View Transitions API): the same choreography with the Web Animations API.
  - New content fades in and slides with the same timings.
  - The title flips (FLIP from the row's rect), and the head's other parts fade in after 100ms.
  - The source frame is a fixed-position ghost animated between the two rects.
  - The tab indicator and segmented thumb are CSS transitions in both paths.
- **Accessibility:**
  - On a page change, focus moves to the `h1` (`tabindex=-1`, no ring) and an `aria-live` region announces
    "Nvidia, Overview". On a tab or mode change, focus stays.
  - A loading panel has `aria-busy`.
- **Interruptibility:** CSS transitions and the Web Animations API, not keyframes, for anything repeatable.
  A new navigation during a transition skips the old one.
- **QA:** check every transition at 5× speed (the prototype has the switch). Pause and seek it with
  `document.getAnimations()`, as `tools/frames.js` does. The frames in `prototype/frames/`:
  - `01-*` screener → company, with the skeleton and values fading in
  - `02-*` tab switch
  - `03-*` card
  - `04-*` figure → reader
  - `05-*` assistant open and close
  - `06-*` filter update and menu
  - `07-*` Read → What changed
  - `08-*` back from the reader
  - `09-*` reduced motion
  - `10-*` phone
  - `11-*` dark
  - `12-*` fallback

## 8. Product work, not design (scope it separately)

- Cited figures in assistant answers linked to the page by hover, and the front door's "128 of 128 figures
  traced" re-tested after that change.
- Thesis pillars with Holds / Watch / Broken. Ship the cheap part now: the current value beside each red line.
- "Ask about these changes" in the reader (label it new; "Ask about this section" exists).
- The source card's exact printed figure for every cell. Today the reader holds the statements; the card
  needs a lookup from a cell to its filing line.
- Live readings on the front door's proof row and the top bar's popover.

## 9. Acceptance checks (the build is done when these pass)

| Check | Target | How |
| --- | --- | --- |
| Text size | Nothing under 11px on any page, charts included | `tools/measure-page.js` on each page, light and dark |
| Text contrast | 0 failures (ignore the chart's latest-value label measured against its own bar) | Same |
| Non-text contrast | Field outlines, chart bar edges ≥ 3:1 | Token table §2.3 |
| Seal vs loss | Never on one cell; ΔE00 ≥ 15 | Review the ledgers; `tools/colour.py` |
| Horizontal scroll | `scrollWidth` = viewport at 390, 768, 1280, 1440 | Script |
| Company header | Tabs end ≤ 270px from the top at 1440 | `tools/firstfig.js` |
| First screen at 1440x900 | Hero, footing and the supporting ledger all above 900 | Same |
| Targets | Figure buttons ≥ 24px (fine), ≥ 44px (coarse); controls 32 / 44 | `tools/hit.js` |
| Keyboard | One tab stop per ledger; Enter opens a card with focus inside; Escape returns focus; Ctrl K / Ctrl J instant | `tools/proto-keys.js` pattern |
| Fidelity | Every quote matches the live filing text exactly; no label or unit the data doesn't give | Diff against the reader |
| Motion | Every UI duration ≤ 280ms; no overlap of two legible figures; reduced motion has no translation | Frames at fixed times, as in `prototype/frames/` |
| Loading | No generic skeleton; no layout shift when values land | Record a navigation as `tools/live-motion.js` does |
