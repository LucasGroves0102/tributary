# Tributary design system: "Seal"

Chosen by Lucas on 2026-11-11 after three rounds of mockups. Reference mockup: `docs/design/seal-mockup.html`
(local) and https://claude.ai/artifact/6TvPnzsNhBYsqVHWw9xMfP. The idea: an altar missal kept as an accounting ledger.
Red rubrics in the margin, a sentence first, ledger rules, red-letter days, a double-ruled bottom line, and a red wax
seal on every figure taken from a filing. Professional before decorative: no arches, no inscription capitals, no glow.

## What Lucas liked, and didn't (2026-11-11)

- Liked: Rubric's professional calm (white page, black serif, red rubrics in the margin, ledger rules, double-ruled
  bottom line, red-letter days); Reliquary's warmth and the wax seal; the seal as logo and as "taken from the
  filing"; red and gold; the "altar of finance" idea behind it.
- Didn't: Reliquary's video-game feel (gold capitals on black, arched triptych panels, glow, gold sunburst rays).
  Round 1 and Temple/Institution were "a start" but not enough identity.
- Wants: professional first, Radix Primitives for controls, inspiration from real professional sites, not shadcn's
  stock look. Redesign starts 2026-11-11 afternoon, on a branch; nothing deploys without his OK.

## Direction and feel

- Who: an analyst at a desk reading filings and building a thesis; a recruiter or hiring manager in their first 30 seconds.
- Feel: an annual report and a ledger. Calm, exact, trustworthy; the seal says "this came from the filing".
- One bold thing: the seal. Everything else stays quiet.

## Colour (tokens; both themes)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--page` | #fbfaf8 | #131011 | page ground |
| `--sheet` | #ffffff | #1a1617 | panels, the document |
| `--ink` | #161314 | #ede6dc | figures, names, headings |
| `--ink-2` | #4c4646 | #b9b0a6 | labels, supporting text |
| `--ink-3` | #706965 | #958c83 | periods, notes, metadata (tuned from #78716d / #8f867d for AA on `--sheet-2`) |
| `--rule` | #e6e0db | #2f2829 | row and section rules |
| `--red` | #b3111e | #e05a63 | the one accent: actions, rubrics, red-letter days, links, focus |
| `--red-ink` | #ffffff | #1a0d0f | text on red |
| `--red-rule` | rgba(179,17,30,.36) | rgba(224,90,99,.42) | ledger column rules |
| `--red-wash` | rgba(179,17,30,.05) | rgba(224,90,99,.07) | current column, hover on quiet buttons |
| `--gold` | #a87b2c | #d2b06a | only on the seal |
| `--up` | #1d6944 | #6cc495 | a rise, in prose and deltas |

Rules: colour means something or it isn't used. Red is never "loss": negatives are in parentheses, as filings print
them; a fall in prose is worded ("down 12%"). Gold appears only on the seal. No gradients except inside the seal.

## Type

- Source Serif 4 (variable, opsz): headings, company names, the lead sentence, body text, and table figures
  (`lining-nums tabular-nums`). Opsz 60 for display sizes, 30 for the lead sentence.
- Archivo (variable): controls, navigation, tabs, metadata rows, form marks, small sans notes.
- Self-host both (OFL; the app's CSP blocks external fonts): bundle woff2 with the build, pinned versions.
- Scale (px): 12 notes, 13.5 meta, 15 body and tables, 16 list titles, 22 section headings and the lead sentence,
  46 company name, 60 front-door headline. Weights: 400 body, 600 controls, 650 headings and totals, 700 grand totals.
- No all-caps labels, no small labels above every block, no middle-dot meta strings, no monospace for data.

## Layout

- Margin rubrics: a 190px left column of red labels (sans bold name + serif italic line) beside each section,
  right-aligned; collapses above the section below 1100px.
- Sections separated by one `--rule` line, 28px air above each.
- Company page order: name, meta row, actions, tabs; then The year (a sentence with the seal), Figures (ledger),
  Since the report (red-letter days), Statement (table).
- Top bar: 2px `--ink` underline. Decided 2026-11-14: the left sidebar stays (watchlist, workspaces, next filings),
  restyled: the page's ground, one rule to the page, where you are is the ink with a 2px red rule at its edge.

## Components

- Buttons: square corners. Primary `--red` fill; secondary 1px `--ink` outline, transparent; press scale(.97) 120ms.
  One primary per view.
- Tabs and nav: sans 14px, active = `--ink` + 2px `--red` inset underline.
- Toggle group: 1px `--ink` border, active segment inverted (`--ink` bg).
- Ledger (figures): two columns of label / value rows, each value after a 1px `--red-rule` left rule, right-aligned,
  min-width 120px, a 14px seal after a filed figure; a figure a later filing may change is `--red` with `*`.
- Table: serif 15px, tabular; header row 2px `--ink` bottom rule; 1px `--red-rule` between columns; totals 650;
  the bottom line 700 with a 3px double `--ink` rule; derived figures italic `--ink-2`; current column `--red-wash`;
  negatives in parentheses.
- Red-letter days: 74px date column (34px serif day, sans month), red day for filings that can change figures
  (material 8-K items), form mark (1px outline, sans 12px).
- Seal: the wax seal SVG (red, gold ring and T). Logo at 30px, inline after the lead sentence at 20px, in the ledger at
  14px. Clicking a seal opens the filing at the figure (it replaces the old hover-only source).
- Controls with behaviour (menus, dialogs, popovers, tooltips/hover cards, tabs, toggle groups) use Radix Primitives,
  styled with these tokens; never shadcn's stock look.

## Constraints from the codebase (services/web/CLAUDE.md)

- CSP: no inline styles or external resources; fonts bundled; style via classes only.
- Table cells' textContent must stay the figure (tests compare it): the seal and marks are separate elements or CSS.
- Sign convention: the web's one place for outflow signs stays (F5); parentheses are how a negative displays, the CSV
  and hovers keep exact values.
- Every number keeps its link to the filing; blanks vocabulary (`—`, `·`, n/a, n/m, old) unchanged.

## Built (branch ui-seal, batch 1, 2026-11-14)

Decisions taken while building, so later batches hold to them:

- Theme follows the visitor's system (`prefers-color-scheme`, in `tokens.css` itself) until they choose: the top bar's
  Theme button says which is in force and cycles Light, Dark, System; System forgets the stored choice. Both palettes as above; the dark block appears twice
  (system and chosen) and `tokens.test.ts` keeps them identical.
- Extra tokens: `--sheet-2` (#f4f1ed / #231e1f, a recessed sheet), `--rule-2` (#d3cbc4 / #433a3b, a field's edge),
  `--red-soft` (#f7e9e9 / #2a191c, an error's ground), `--up-soft`, `--warn` (#8f4e14 / #dc9a5e, an umber for "your own
  writing" and warnings, kept apart from the seal's gold) and `--warn-soft`; `--lift` the one shadow (dialogs, the
  front door's sheet, tooltips). Older names (`--panel`, `--text`, `--accent`, `--neg`...) read from these.
- `.neg` (a fall) is the ink, `.pos` (a rise) `--up`; `--neg` as a colour is the red and means an error.
- Chart series: the ink first (#5c5350 / #cdc3b8), then slate, umber, deep teal, plum, olive, rose, warm grey; never the
  accent red. The deck export (SVG/PNG) uses the light series and inks too.
- Type scale as tokens: `--fs-xs` 12, `--fs-sm` 13, `--fs-meta` 13.5, `--fs-base` 14 (controls), `--fs-md` 15 (body,
  tables), `--fs-lg` 16, `--fs-xl` 22, `--fs-name` 46, `--fs-hero` 60. Corners are 0 everywhere (`--radius*`).
- Motion: `--press` 120ms cubic-bezier(.23,1,.32,1); a press sinks to .97; tooltips and the dialog fade/scale from .96;
  all dropped under reduced motion.
- Components in code: `Seal`, `SealLink` (a seal on a figure: opens the filing at its statements; its source is a Radix
  tooltip and the link's accessible description), `Folio` (rubric + section), `Choices` (Radix toggle group). The
  confirm dialog is a Radix alert dialog with its own backdrop (Radix's Overlay injects a `<style>` the CSP refuses).
- Parentheses: `format.ts` `paren`, display only, on money in statements, the ledger, the recap and the screener.
  Costs keep the API's sign and print positive, as most 10-Ks print them; parentheses only for a negative amount (the
  mockup's "(12.89)" costs are left out, decided 2026-11-14).
- Ledger: amounts in the first column, rates in the second; a figure from another period than the margin's says when;
  a negative net debt reads "Net cash" with Cash and short-term investments beside Cash.
- The front door makes no claim the API doesn't give: its figures are `/v1/status/data`'s; the mockup's "within a
  minute" and sample filings were left out.
- A masthead carries two actions in the open (the one primary, and Watch); the rest go in a "More" menu (Radix
  DropdownMenu, `modal={false}`). No access pills in a masthead: access is a hover. No line for an absent fact ("No
  expected filing date" is gone).
- A section says a thing once: the Overview's flags are the ledger's red "*" plus one line under Since the last report
  (the no-pro-forma explanation in its hover), not a box.
- Long notes under a table go behind "How to read these figures" (`details`, closed), one sentence left in view; the
  blanks are a legend (`·`, `—`, `$0` each with its meaning), never a dotted string.
- No middle dot as a separator anywhere in new copy: separate items (flex gap) or sentences.
- Statement row labels stay on one line where the table has 900px or more (`@container tw`), and wrap below it.
- Screener: column rules between figures only.
