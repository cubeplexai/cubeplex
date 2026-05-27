# China-Vendor MCP Sources — Curation + Catalog Integration

**Date:** 2026-05-27
**Branch:** `feat/mcp-china-sources` (off `main`)
**Issue:** #147
**Status:** ready for review

**Related:**
- `backend/cubebox/mcp/template_seed.py` — the connector template seed (the
  schema every catalog entry must fill).
- `backend/cubebox/models/mcp.py` — `MCPConnectorTemplate` table columns.
- `backend/docs/mcp_catalog_oauth.md` — install / OAuth / static-auth runbook.
- `docs/dev/specs/2026-05-22-preset-catalog-redesign-design.md` — the *LLM
  provider* preset catalog (a separate catalog; named here only to avoid
  confusing the two).

---

## 1. Problem & Motivation

cubebox ships a curated MCP connector catalog — the one-click "install" list a
workspace admin sees. Today that catalog
(`CATALOG` in `backend/cubebox/mcp/template_seed.py`) is entirely
Western-vendor: GitHub, Notion, Linear, Atlassian, Asana, Slack, Cloudflare,
Sentry, Intercom, Google Workspace, Microsoft Learn.

For users operating in China this catalog is close to empty in practice:

- The maps, payments, and office-collaboration tools they actually use every
  day (高德/Amap, 百度地图/Baidu Maps, 支付宝/Alipay, 飞书/Feishu, 钉钉/DingTalk,
  企业微信/WeCom) are simply not offered.
- Several Western entries are network-unreachable or low-value from inside
  China.

This spec is a **research deliverable first, integration plan second.** The
core output is a curated, cited list of mainstream, actively-maintained
China-vendor MCP servers grouped by domain, each recorded with the exact
fields our catalog schema needs (transport, auth method, server URL,
maintenance signal). The integration section then maps those records onto the
existing template schema and flags where the schema does **not yet** cover a
vendor's auth model.

---

## 2. Goals / Non-Goals

**Goals**

- Produce a curated, cited list of China-vendor MCP servers by domain, each
  with: name, vendor, domain, repo/official URL, transport, auth method,
  maintenance signal (official vs community, freshness).
- State, per source, how its record maps onto the existing
  `MCPConnectorTemplateSeedEntry` fields.
- Identify the auth shapes our catalog schema does **not** support yet
  (notably API-key-as-URL-query-param remote servers) and record them as open
  questions rather than papering over them.
- Recommend a v1 subset to land first, biased toward official + remote-hosted +
  standard-auth servers.

**Non-Goals**

- Implementing the seed entries or any schema migration. This is a design /
  research doc; the actual `CATALOG` edits and any new columns are follow-up
  work gated on the open questions in §8.
- Building a localhost / stdio launcher. cubebox installs **remote** connectors
  (`server_url` + transport); stdio-only servers (npm/uvx packages a user runs
  locally) are out of scope until we have a managed-launcher story.
- Curating Western vendors' China regions of LLM *model* providers — that's the
  separate LLM provider catalog (§Related).
- E-commerce transactional connectors (淘宝/京东/拼多多): no official MCP servers
  exist as of this writing (§4.7).

---

## 3. How the Preset Catalog Works Today

The connector catalog is a pure-Python list seeded into Postgres by an explicit
deploy step. There are two relevant files.

### 3.1 The seed entry schema

`backend/cubebox/mcp/template_seed.py` defines `MCPConnectorTemplateSeedEntry`.
Every catalog addition must fill these fields:

| Field | Meaning / constraint |
|---|---|
| `slug` | Stable catalog id, also the env-var prefix for static OAuth apps. |
| `name` / `provider` / `description` | Display strings. |
| `server_url` | The **remote** MCP endpoint. cubebox connects to this; it does not spawn local processes. |
| `transport` | `"streamable_http"` or `"sse"` (the only two `Literal` values). |
| `supported_auth_methods` | Subset of `["oauth", "static", "none"]`. |
| `default_credential_policy` | `"org" \| "workspace" \| "user" \| "none"`. |
| `oauth_dcr_supported` | `True` if the AS supports Dynamic Client Registration; `False` needs a pre-registered app via env vars; `None` for non-OAuth. |
| `oauth_default_scope` | OAuth scope string, or `None`. |
| `oauth_static_client_id_env` / `oauth_static_client_secret_env` | Env-var names for a pre-registered OAuth app (DCR=False); `None` otherwise. |
| `static_form_schema` | List of field dicts the install UI renders (e.g. the shared `_TOKEN_FIELD`). |
| `static_auth_header_template` | How the static credential becomes a header, e.g. `"Bearer {token}"`. |
| `template_metadata` | Free dict; today holds `{"docs_url": ...}`. |
| `tool_citation_defaults` | Per-tool citation mapping (see WebTools entry). |

The matching DB columns are on `MCPConnectorTemplate`
(`backend/cubebox/models/mcp.py`, lines 18–62). Notably the table stores
`server_url`, `transport`, `supported_auth_methods` (JSON), the OAuth fields,
and `static_form_schema` / `static_auth_header_template`. **There is no field
for "API key carried as a URL query parameter"** — see §6.2 and §8.

### 3.2 The seeder

`seed_templates()` (same file) is idempotent: it upserts each entry by `slug`,
encrypts any static OAuth client secret into a system-level `Credential`
(`org_id IS NULL`), and marks DB rows whose slug left the list as
`status='deprecated'`. Invoked out-of-band:
`python -m cubebox.cli seed-mcp-templates`. Not run at FastAPI startup.

Auth at runtime (`backend/docs/mcp_catalog_oauth.md`): OAuth flows go through
the shared `/api/v1/oauth/mcp/callback`; static credentials are encrypted into
the vault and applied via `static_auth_header_template`. **Both paths assume the
credential travels in an HTTP header.**

---

## 4. Curated Source List

Maintenance signal legend: **Official** = published by the vendor's own org;
**Community** = third-party. Freshness reflects observation at time of writing
(May 2026). Transport values map to our `transport` literal where applicable;
"stdio" entries are local-launch packages and are **not directly installable**
under the current remote-only model (§2 Non-Goals).

### 4.1 Maps / Mobility

| Name | Vendor | Repo / Official URL | Transport | Auth | Maintenance | Citation |
|---|---|---|---|---|---|---|
| Amap / 高德地图 | AutoNavi (Alibaba) | `https://mcp.amap.com/sse?key=<KEY>` (remote); `@amap/amap-maps-mcp-server` (stdio) | SSE (remote), stdio | **API key in URL query param** (`?key=`) | Official | [1][2] |
| Baidu Maps / 百度地图 | Baidu | `https://github.com/baidu-maps/mcp`; remote SSE/Streamable on lbs.baidu.com (enable "MCP (SSE)") | Streamable HTTP (recommended), SSE, stdio | **API key (AK) in URL** | Official, first CN maps vendor on MCP | [3][4][5] |
| Tencent Location / 腾讯位置服务 | Tencent | `https://lbs.qq.com/service/MCPServer/MCPServerGuide/overview` | SSE + Streamable HTTP | **API key in URL**, built on WebServiceAPI | Official | [6][7] |

All three official maps servers expose geocoding, reverse-geocoding, POI
search, route planning, weather, IP location. The catalog-relevant catch: they
authenticate by a **`key`/`ak` query parameter on the URL**, not an `Authorization`
header (§6.2).

### 4.2 Payments

| Name | Vendor | Repo / Official URL | Transport | Auth | Maintenance | Citation |
|---|---|---|---|---|---|---|
| Alipay / 支付宝 | Ant Group | `@alipay/mcp-server-alipay` (npm); `https://open.alipay.com/` | stdio (local) | App ID + RSA private/public key pair (env) | Official, China's first payment MCP | [8][9][10] |
| Alipay+ Global | Ant Group | `https://github.com/alipay/global-alipayplus-mcp` | stdio | App credentials | Official | [11] |

Alipay's official server is **stdio / local-launch** (an npm package the user
runs), and its auth is an asymmetric key pair entered as env vars — neither the
remote-URL model nor the header-template static model fits cleanly (§6.2, §8).

### 4.3 Office Collaboration

| Name | Vendor | Repo / Official URL | Transport | Auth | Maintenance | Citation |
|---|---|---|---|---|---|---|
| Feishu / Lark | ByteDance | `https://github.com/larksuite/lark-openapi-mcp`; `@larksuiteoapi/lark-mcp` (npm); remote mode documented | stdio + SSE; **remote mode** available | App Access Token + User Access Token; **OAuth** for user token | Official | [12][13][14] |
| DingTalk / 钉钉 | Alibaba | `https://github.com/open-dingtalk/dingtalk-mcp`; server-API MCP on open.dingtalk.com | stdio (per repo); server-side API MCP | Client ID + Client Secret (env) | Official (published Jan 2026) | [15][16] |
| WeCom / 企业微信 | Tencent | OpenClaw plugin + Tencent Cloud bot MCP (`cloud.tencent.com/developer/mcp/server/10854`); community `loonghao/wecom-bot-mcp-server` | stdio / SSE | Bot webhook key or app secret | Official (bot MCP) + community | [17][18][19] |

Feishu is the strongest office-collaboration candidate: official, supports a
documented **remote mode**, and has an OAuth user-token flow that could map to
our `oauth` path.

### 4.4 Cloud / DevOps

| Name | Vendor | Repo / Official URL | Transport | Auth | Maintenance | Citation |
|---|---|---|---|---|---|---|
| Alibaba Cloud (Bailian-hosted) | Alibaba | `https://bailian.console.aliyun.com/?tab=mcp` — hosts/registers MCP services | Hosted remote (varies per service) | Per-service (often API key) | Official platform | [20][21] |
| Tencent CloudBase | Tencent | `https://github.com/TencentCloudBase/CloudBase-MCP` | stdio / remote | Tencent Cloud credentials (env) | Official | [22] |
| Tencent EdgeOne Pages | Tencent | `cloud.tencent.com/developer/mcp/server/10011` | Remote | API token | Official | [23] |
| Tencent Cloud (community aggregator) | Tencent community | `https://github.com/TencentCloudCommunity/mcp-server` | Remote + local | Tencent Cloud SecretId/SecretKey | Community (Tencent-affiliated) | [24] |

Alibaba's "Bailian" console is itself an **MCP hosting marketplace** — it
registers and hosts third-party MCP services rather than being a single
connector. That makes it a *source of* remote MCP URLs, not a single catalog
row; we'd curate specific hosted services, not "Bailian" as one entry.

### 4.5 Content / Media / AI Platforms

| Name | Vendor | Repo / Official URL | Transport | Auth | Maintenance | Citation |
|---|---|---|---|---|---|---|
| MiniMax MCP | MiniMax | `https://github.com/MiniMax-AI/MiniMax-MCP` (py); `MiniMax-MCP-JS` | stdio + SSE | `MINIMAX_API_KEY` + `MINIMAX_API_HOST` (region: api.minimax.io / api.minimaxi.com) | Official | [25][26][27] |
| MiniMax Search | MiniMax | `https://github.com/MiniMax-AI/minimax_search` | stdio | API key | Official | [28] |
| ModelScope MCP Square | Alibaba (ModelScope) | `https://www.modelscope.cn/mcp` — ~1500 hosted MCP services | Hosted remote | Per-service | Official marketplace | [29][30] |

MiniMax bundles TTS, voice cloning, image and video generation as MCP tools —
high product value for a content/media workspace. Like Bailian, ModelScope's
"MCP 广场" is a hosting marketplace (a source of URLs), not a single connector.

### 4.6 Financial / Market Data

| Name | Vendor | Repo / Official URL | Transport | Auth | Maintenance | Citation |
|---|---|---|---|---|---|---|
| Tushare (CN financial data) | Tushare + community | `https://tushare.pro/`; servers: `sunyalou/tushare-mcp-server`, `hanxuanliang/...`, `guangxiangdebizi/FinanceMCP-DCTHS` | stdio / HTTP (FastMCP) | Tushare Pro API token (env) | Data source official; **MCP servers community** | [31][32][33] |

Tushare exposes A-share / fund / futures data; the data API is first-party but
**every MCP wrapper is community-maintained**. Treat as community trust tier
(§5).

### 4.7 E-commerce — no official sources (recorded for completeness)

As of May 2026, 淘宝/Taobao, 京东/JD, and 拼多多/Pinduoduo have **not** published
official MCP servers; commentary notes the big platforms are deliberately
holding back. Only community scrapers / affiliate-link tools exist
(`JeremyDong22/taobao_mcp`, `liuliang520530/taoke-mcp`). **Recommendation: do
not include in v1** — community scrapers risk ToS/anti-bot breakage and carry
no maintenance guarantee. [34][35]

---

## 5. Selection Criteria

How each candidate was judged for inclusion / trust tier:

1. **Official over community.** First-party vendor org is tier 1. A first-party
   *data* API wrapped by a community MCP server (Tushare) is tier 2. Pure
   community (e-commerce scrapers) is tier 3 and excluded from v1.
2. **Remote-installable now.** A server we can reach over `server_url` +
   `streamable_http`/`sse` fits the current model. stdio-only packages are
   recorded but parked until we have a managed launcher (§2).
3. **Standard auth.** Header-bearer (`static`) or OAuth maps onto the existing
   schema. URL-query-param keys and asymmetric key pairs do **not** (§6.2) and
   are flagged.
4. **Maintenance freshness.** Recent commits / active vendor platform. DingTalk
   (Jan 2026), Feishu (active), MiniMax (active) clear this.
5. **Product value in a CN workspace.** Maps, payments, office collaboration,
   media generation are daily-driver categories; obscure single-purpose servers
   are deprioritized.
6. **Licensing clarity.** Vendor servers under a clear OSS license or vendor
   ToS preferred; ambiguous-license community repos excluded (§8).

---

## 6. Catalog Integration

### 6.1 Mapping a source onto `MCPConnectorTemplateSeedEntry`

For a source that *does* fit the current model (remote + header/OAuth auth),
the mapping is mechanical. Worked example — **Feishu** (remote mode, static App
token):

```text
slug                          = "feishu"
name / provider               = "Feishu / Lark" / "Feishu"
description                   = "Feishu/Lark OpenAPI: docs, messages, calendar, bitable."
server_url                    = <documented Feishu remote-mode endpoint>
transport                     = "sse"            # or streamable_http if offered
supported_auth_methods        = ["static"]       # ["oauth"] if we wire the user-token OAuth flow
default_credential_policy     = "workspace"
oauth_* fields                = None             # unless OAuth path adopted
static_form_schema            = _TOKEN_FIELD     # App Access Token
static_auth_header_template   = "Bearer {token}"
template_metadata             = {"docs_url": "https://open.larksuite.com/document/..."}
```

MiniMax maps the same way (`static`, `Bearer {token}`, `MINIMAX_API_KEY`),
**if** a remote endpoint is used; the public repos are stdio, so MiniMax is a
v1.5 candidate (§7).

### 6.2 Auth shapes the current schema does NOT cover

Two recurring CN-vendor auth models have **no home** in the schema today:

- **API key as a URL query parameter** (Amap, Baidu Maps, Tencent Location):
  the key lives in `?key=`/`?ak=` on `server_url`, not in a header.
  `static_auth_header_template` cannot express this, and baking a secret into
  `server_url` is wrong (the URL is stored plaintext; the vault is for secrets).
  This needs either a new `auth_query_param` field on the template + an install
  flow that injects the vault secret into the URL, or a small server-side proxy.
  **Blocking for the maps domain** (§8).
- **Asymmetric key pair** (Alipay: App ID + RSA private key): not a bearer
  token; the server signs requests. Our `static` model is single-secret-bearer.
  Alipay is also stdio-only. **Out of scope until both gaps close** (§8).

### 6.3 Possible new fields (deferred to follow-up, not decided here)

If we adopt the URL-query-param auth model, the minimal addition is one
nullable column on `MCPConnectorTemplate` (e.g. `static_auth_query_param: str |
None`) plus install-flow handling that reads the vault secret and appends it to
the connect URL — keeping the secret in the vault, never in `server_url`. This
is called out as an open question (§8), not specified here, per "don't prescribe
implementation minutiae."

---

## 7. Rollout / v1 Subset

Land in waves, biased to what fits the schema unchanged.

**v1 — fits the current schema, official, remote, header/OAuth auth:**

- **Feishu / Lark** — official, remote mode, `static` Bearer (OAuth as a
  fast-follow). The single highest-value entry that needs no schema change.

That is genuinely the only candidate that drops in cleanly today. Everything
else needs one of the gaps in §6.2 closed first. Being honest about that is the
point of this spec.

**v1.5 — needs a small, well-scoped change:**

- **Amap, Baidu Maps, Tencent Location** — unblocked by the
  URL-query-param auth field (§6.3). High value; recommended as the first
  schema follow-up.
- **MiniMax** — needs a remote endpoint (or a managed stdio launcher) since the
  official servers are stdio; auth itself (`Bearer`) already fits.

**v2 — needs a launcher and/or richer auth:**

- **Alipay** (stdio + asymmetric keys), **DingTalk / WeCom** (largely stdio),
  **Tencent CloudBase / EdgeOne**, **Tushare** (community tier).

**Marketplaces (Bailian, ModelScope) are not single entries** — they are
sources of individual hosted MCP URLs; if adopted, we curate specific hosted
services from them as their own rows.

---

## 8. Open Questions

- **URL-query-param auth (maps).** Amap / Baidu / Tencent maps put the key in
  `?key=`/`?ak=`. Add a `static_auth_query_param` template field + install-time
  URL injection (secret stays in the vault), or stand up a proxy? This blocks
  the entire maps domain (§6.2).
- **stdio / local-launch servers.** Alipay, DingTalk, WeCom, MiniMax (official),
  Tushare are npm/uvx packages run locally. cubebox installs remote URLs only.
  Do we add a managed launcher, or wait for vendors to publish remote
  endpoints? Blocks payments + most office-collaboration.
- **Alipay asymmetric-key auth.** App ID + RSA private key is not a bearer
  token. Does the vault + install flow get a "key pair" credential kind, or is
  Alipay deferred indefinitely?
- **Marketplace sources (Bailian / ModelScope).** Curate individual hosted
  services as catalog rows, or skip aggregators entirely? Each hosted service
  has its own URL, auth, and (unclear) uptime/SLA.
- **Community-MCP licensing & trust (Tushare, e-commerce).** Several community
  servers have unclear licenses and no maintenance guarantee. What's the bar for
  admitting a community-maintained wrapper to an official-looking catalog?
- **Regional host selection (MiniMax).** `api.minimax.io` vs `api.minimaxi.com`
  must match the key's region. Does the install UI need a region selector, or do
  we ship two slugs?
- **`mcp_catalog_oauth.md` drift.** That doc still describes the older M2
  `mcp_catalog_connectors` / `catalog_seed.py` naming; the live schema is
  `mcp_connector_templates` / `template_seed.py`. Refresh it when these entries
  land.

---

## 9. References

1. Amap remote MCP endpoint — https://github.com/sugarforever/amap-mcp-server
2. `@amap/amap-maps-mcp-server` (npm) — https://www.npmjs.com/package/@amap/amap-maps-mcp-server
3. Baidu Maps MCP (official) — https://github.com/baidu-maps/mcp
4. Baidu Maps MCP quickstart — https://lbs.baidu.com/faq/api?title=mcpserver/quickstart
5. Official Baidu Maps MCP (PulseMCP) — https://www.pulsemcp.com/servers/baidu-maps
6. Tencent Location MCP guide — https://lbs.qq.com/service/MCPServer/MCPServerGuide/overview
7. Tencent Location MCP access — https://tcb.cloud.tencent.com/mcp-server/mcp-tencent-map
8. Alipay MCP (npm) — https://www.npmjs.com/package/@alipay/mcp-server-alipay
9. Alipay Open Platform — https://open.alipay.com/
10. Alipay MCP (ModelScope) — https://modelscope.cn/mcp/servers/Alipay/mcp-server-alipay
11. Alipay+ Global MCP — https://github.com/alipay/global-alipayplus-mcp
12. Feishu/Lark OpenAPI MCP (official) — https://github.com/larksuite/lark-openapi-mcp
13. `@larksuiteoapi/lark-mcp` (npm) — https://www.npmjs.com/package/@larksuiteoapi/lark-mcp
14. Lark remote-mode docs — https://open.larksuite.com/document/mcp_open_tools/call-feishu-mcp-server-in-remote-mode
15. DingTalk MCP (official) — https://github.com/open-dingtalk/dingtalk-mcp
16. DingTalk server-API MCP overview — https://open.dingtalk.com/document/ai-dev/dingtalk-server-api-mcp-overview
17. WeCom bot MCP (Tencent Cloud) — https://cloud.tencent.com/developer/mcp/server/10854
18. WeCom OpenClaw integration — https://work.weixin.qq.com/nl/index/openclaw
19. WeCom bot MCP (community) — https://github.com/loonghao/wecom-bot-mcp-server
20. Alibaba Cloud Bailian MCP tab — https://bailian.console.aliyun.com/?tab=mcp
21. Bailian product page — https://www.aliyun.com/product/bailian
22. Tencent CloudBase MCP (official) — https://github.com/TencentCloudBase/CloudBase-MCP
23. Tencent EdgeOne Pages MCP — https://cloud.tencent.com/developer/mcp/server/10011
24. Tencent Cloud MCP server (community) — https://github.com/TencentCloudCommunity/mcp-server
25. MiniMax MCP (official, py) — https://github.com/MiniMax-AI/MiniMax-MCP
26. MiniMax MCP JS — https://github.com/MiniMax-AI/MiniMax-MCP-JS
27. MiniMax MCP guide — https://platform.minimax.io/docs/guides/mcp-guide
28. MiniMax Search MCP — https://github.com/MiniMax-AI/minimax_search
29. ModelScope MCP Square — https://www.modelscope.cn/mcp
30. ModelScope MCP community launch — https://modelscope.cn/headlines/article/1142
31. Tushare data platform — https://tushare.pro/
32. Tushare MCP server (community) — https://github.com/sunyalou/tushare-mcp-server
33. FinanceMCP-DCTHS (Tushare + 东财 + 同花顺) — https://github.com/guangxiangdebizi/FinanceMCP-DCTHS
34. Taobao MCP (community) — https://github.com/JeremyDong22/taobao_mcp
35. Taoke MCP (淘宝客/京东客/多多客, community) — https://github.com/liuliang520530/taoke-mcp
