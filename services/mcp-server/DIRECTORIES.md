# Connector directories: the owner's checklist

What Anthropic's [Connectors Directory](https://claude.com/docs/connectors/building/submission)
(claude.ai/directory/manage, any paid Claude plan) and OpenAI's
[ChatGPT plugin directory](https://developers.openai.com/apps-sdk/deploy/submission) (the OpenAI
platform dashboard, a verified organization) ask for, and where Tributary stands. Read 2026-11-17;
both portals change, so re-read them before submitting. The owner submits.

## Ready

| Asked for | Tributary |
|---|---|
| Remote MCP over public HTTPS | `https://tributary.lucasgroves.dev/mcp`, Streamable HTTP, stateless |
| OAuth for a user's account | the Query API's authorization server: client ID metadata documents (preferred by both) and dynamic registration, PKCE S256, `resource` (RFC 8707), `iss` (RFC 9207), refresh rotation; a 401 with `resource_metadata` starts sign-in |
| Callback URLs | any https or loopback URI the client registers or publishes: claude.ai's `https://claude.ai/api/mcp/auth_callback` (and claude.com's), Claude Code's loopback ports, ChatGPT's `https://chatgpt.com/connector_platform_oauth_redirect` and `/connector/oauth/{id}` |
| Every tool with a `title` and `readOnlyHint` | all 28 (`test_tools` checks); no write tools |
| Tool descriptions written for a model | yes; filing text described as data, never instructions |
| No token or key in a URL or a server header | the credential is only `Authorization: Bearer` |
| Data handling | Tributary's own API over public SEC data; no health data, no payments, no sponsored content |

## Missing (the owner's to do)

1. **Privacy policy URL** (both; OpenAI also checks it names the same publisher): what is
   collected (GitHub id and login or the account's username, saved work, uploads, usage counts),
   what a connected app sees (read-only, as the user), retention (sandboxes 24 h, logs, backups 14
   days), no selling or sharing, contact. A public page on the site, e.g. `#/privacy`.
2. **Terms of service URL** (OpenAI requires it; Anthropic's directory terms bind the submitter).
3. **Support contact** (both) and **website URL** (OpenAI): an email address or support page.
4. **Icon**: square, at least 48 px (OpenAI: PNG, JPEG, WebP or SVG, at most 5 MiB; Anthropic
   asks for an icon). The wax seal (`services/web/src/components/Seal.tsx`) exported as a file.
5. **Listing words**: Anthropic: name (100), one-liner (200), description (2,000), 1-5
   categories, documentation URL (`https://tributary.lucasgroves.dev/#/api`), slug (permanent),
   use cases, company and contact. OpenAI: display name and subtitle (30 each), long description
   (4,000), category, developer name, up to 3 starter prompts, brand colour.
6. **A reviewer account**: `query-api-account create reviewer` in the query-api pod (no MFA, no
   email codes, works at once), with sample data in its workspace; Anthropic asks for test access
   by email for Verified review, OpenAI in the review form.
7. **OpenAI's review pack**: 5 positive test cases (prompt, expected tools, result), 3 negative
   ones (e.g. "change my watchlist": refused, read-only), a video walkthrough URL, release notes,
   policy attestations; organization verification; a **domain verification token** served as plain
   text at the URL the portal gives (nginx answers every other `/.well-known/` path with 404: add
   that one path to `services/web/deploy/nginx.conf` if it lives there).
8. **Cloudflare**: make sure no Bot Fight Mode, challenge or rate-limit rule touches `/mcp`,
   `/oauth/*` or `/.well-known/*`: Anthropic (160.79.104.0/21) and OpenAI call them from servers,
   with 10 s (Anthropic) to answer.
9. **Test in both apps first**: add the server as a custom connector in claude.ai and in
   ChatGPT's developer mode, connect, call every tool (Anthropic's Test & launch step asks you to
   confirm it), then submit.
