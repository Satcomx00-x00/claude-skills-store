# claude-skills-store

Mirror of every **Anthropic-authored** plugin in the Anthropic Directory sections
**Code, Design, Engineering, Operations and Product management**, pulled from each
plugin's upstream repo (`anthropics/claude-plugins-official`, `anthropics/knowledge-work-plugins`,
plus pinned `git-subdir` sources such as `modelcontextprotocol/ext-apps`).

- [`skills/README.md`](skills/README.md) — per-plugin skills/commands/agents/hooks/MCP/LSP counts, license, redistributability, upstream commit
- [`skills/catalog.json`](skills/catalog.json) — the same, machine-readable
- [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) — makes this repo installable: `/plugin marketplace add satcomx00-x00/claude-skills-store`

## Refresh

```sh
python3 tools/sync_plugins.py                 # all sections, regenerates skills/ atomically
python3 tools/sync_plugins.py --section code  # subset (still regenerates skills/)
```

CI runs this nightly at 03:17 UTC (`.github/workflows/sync-plugins.yml`) and commits any upstream changes to `main`;
trigger it manually from the Actions tab (**Nightly plugin sync → Run workflow**).

Requires Python ≥ 3.12 and `git`; no third-party packages. Dev checks: `pytest`, `ruff check`, `mypy`.

## Selection rules

| Section | Source |
|---|---|
| Code | Anthropic-authored plugins in `claude-plugins-official` (category ≠ `design`) |
| Design | `knowledge-work-plugins/design` + Anthropic `design`-category plugins in `claude-plugins-official` |
| Engineering / Operations / Product management | `knowledge-work-plugins/<section>` |

"Anthropic-authored" = marketplace or `plugin.json` author contains "Anthropic", or an `@anthropic.com` author email.
Third-party/partner plugins are filtered on marketplace metadata and never cloned. The in-app directory
(claude.ai) is auth-gated, so sections are derived from upstream marketplace manifests.

## Licensing

Licenses are detected from the plugin's own `LICENSE*`/`COPYING*` files (including nested ones),
then `plugin.json#license`, then the upstream repo root. Anything not in the allowlist
(Apache-2.0, MIT, BSD, ISC, MPL-2.0, CC-BY-4.0, CC0, Unlicense) is **flagged and not mirrored** —
only its metadata is kept. When a plugin inherits its license from the repo root, that `LICENSE` is
copied into the mirrored plugin (Apache-2.0 §4(a)).

### Exclude list

[`plugin-excludes.txt`](plugin-excludes.txt) names plugins to skip entirely — they are **never downloaded**
(checkouts are blobless + sparse, so only the paths of included plugins are fetched). One name per line,
`# reason` optional; reasons appear in `skills/README.md`. Use `--exclude-file PATH` to point elsewhere.

Currently excluded: **`claude-security`** — proprietary Anthropic license ("you may not distribute,
publish… or otherwise make the Plugin… available to any third party"). Install it from the official
marketplace instead.

The license check remains a safety net: a new restrictive plugin that is not yet listed is downloaded
for inspection, flagged and left out — add it to the exclude list to stop the download.
