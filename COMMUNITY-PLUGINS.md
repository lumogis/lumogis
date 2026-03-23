# Community Plugins

Curated list of community-contributed plugins and adapters for lumogis.

This is the discovery mechanism until a plugin registry exists. To submit your plugin, open a PR to this file — see [CONTRIBUTING.md](CONTRIBUTING.md#submitting-a-community-plugin) for the format.

> **Note:** Community plugins are not maintained by Lumogis. Review the source before installing. Plugins have access to your hook events — understand what a plugin does before adding it to your stack.

---

## Wanted integrations

Nothing here is implemented or endorsed by Lumogis yet — these are **gaps worth filling**. If you build one, publish the plugin and open a PR to add a row under the right section above (you can remove or narrow the matching row below).

### Privacy-forward storage

| Connector | Type | Difficulty | Notes |
|---|---|---|---|
| Proton Drive | Storage adapter | Medium | Privacy-first cloud; strong alignment with local-first values |
| Synology NAS | Storage adapter | Medium | DSM API; large self-hosted community |

### Messaging

| Connector | Type | Difficulty | Notes |
|---|---|---|---|
| WhatsApp | Signal source + action handler | Medium | Often via a `whatsapp-web.js`-style bridge; **unofficial** — check provider ToS and your jurisdiction |
| Telegram | Signal source + action handler | Low | Bot API; widely documented |

---

## Signal Sources

Adapters that implement `SignalSource` and poll external systems for new signals.

| Plugin | Description | Author |
|---|---|---|
| *No entries yet — be the first.* | | |

**Good first issues in this category:**
- [Add Hacker News signal source](https://github.com/lumogis/lumogis/issues/1) — poll HN top stories
- [Add Reddit signal source](https://github.com/lumogis/lumogis/issues/2) — poll subreddit feeds

---

## Action Handlers

Adapters that implement `ActionHandler` and execute operations in response to signals or LLM requests.

| Plugin | Description | Author |
|---|---|---|
| *No entries yet — be the first.* | | |

**Good first issues in this category:**
- [Add Notion action handler](https://github.com/lumogis/lumogis/issues/3) — create/update Notion pages
- [Add Slack action handler](https://github.com/lumogis/lumogis/issues/4) — send messages to Slack
- [Add email digest Notifier](https://github.com/lumogis/lumogis/issues/5) — send signal digests via email

---

## Storage Adapters

Adapters that implement `VectorStore` or `MetadataStore` with alternative backends.

| Plugin | Description | Author |
|---|---|---|
| *No entries yet — be the first.* | | |

**Good first issues in this category:**
- [Add Chroma vector store adapter](https://github.com/lumogis/lumogis/issues/6) — drop-in Qdrant alternative
- [Add SQLite MetadataStore adapter](https://github.com/lumogis/lumogis/issues/7) — zero-dependency dev backend

---

## File Extractors

Single-function adapters that extract text from additional file types (auto-discovered).

| Plugin / PR | File type | Description | Author |
|---|---|---|---|
| *No entries yet — be the first.* | | | |

**Good first issues in this category:**
- [Add .epub extractor](https://github.com/lumogis/lumogis/issues/8) — extract text from EPUB files
- [Add .html extractor](https://github.com/lumogis/lumogis/issues/9) — extract text from saved HTML
- [Add .csv/.xlsx extractor](https://github.com/lumogis/lumogis/issues/10) — extract tabular data as text

---

## Plugins

Full plugins that register hooks, tools, and routes.

| Plugin | Description | Author |
|---|---|---|
| *No entries yet — be the first.* | | |

---

## How to add your plugin

1. Publish your plugin to a public GitHub repository
2. Make sure it has a README explaining installation, configuration, and usage
3. Open a PR to lumogis that adds one row to the appropriate table above
4. Format: `| [Plugin Name](url) | One sentence description. | @handle |`

Your PR changes only this file — no code changes to lumogis required.

See [CONTRIBUTING.md](CONTRIBUTING.md#submitting-a-community-plugin) for full details.
