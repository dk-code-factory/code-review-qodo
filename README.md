# code-review-qodo

Independent MIT plugin for Claude Code and Codex. Version 1.0.0.

## Install

Claude Code:

```text
/plugin marketplace add dk-code-factory/code-review-qodo
/plugin install code-review-qodo@code-review-qodo
```

Codex CLI:

```sh
codex plugin marketplace add dk-code-factory/code-review-qodo
codex plugin add code-review-qodo@code-review-qodo
```

Each repository catalogs only its own plugin. Installing this plugin does not install DK or another reviewer. The [organization catalog](https://github.com/dk-code-factory/skills) offers independent selection of all four plugins.

## Skills

- `code-review-qodo`

Both manifests select their own native skills directory. Instructions and runtime resources remain inside this plugin. No build step, sibling checkout, submodule, or generated host package is required. Use the host's skill selector with the names above; Claude also exposes `/PLUGIN:SKILL` commands.

## Requirements

Python 3.11+ for lifecycle helpers; Git for repository workflows. Authenticate the selected host using its normal login. GitHub workflows also use `gh` authenticated for the target repository. External reviewers and their tools are optional and needed only when selected. Credentials, state, and caches stay outside the plugin and reviewed repository.

## Updates

The self-contained `scripts/update.py` runs at skill entry and completion. It checks this repository's latest stable GitHub release at most once per 24 hours. Network failures do not block work. Default policy is `notify`; it reports a newer version once without installing it. `auto` schedules installation after all active workflows finish, including nested invocations across DK plugins. `off` skips release checks. Interrupted workflows retain completion tokens; complete them explicitly after their work has stopped. Do not discard active lifecycle records to force an update.

Set script policy with:

```sh
python3 PLUGIN_ROOT/scripts/update.py policy --host codex --mode auto
python3 PLUGIN_ROOT/scripts/update.py policy --host claude --plugin code-review-qodo --mode notify
```

`PLUGIN_ROOT` means the installed plugin root. Settings live at `${XDG_CONFIG_HOME:-~/.config}/dk-code-factory/updates.json`; checks and lifecycle state live at `${XDG_STATE_HOME:-~/.local/state}/dk-code-factory/updates/`. Policy is global with per-plugin overrides. Host registry identity, version, marketplace, pins, and scopes govern installation. Pinned versions, non-main refs, local checkouts, and unregistered plugin directories do not auto-update. The script uses supported host commands and never edits a plugin cache or pulls inside it. It confirms the installed version before reporting success and reports reload requirements or failed updates.

Claude's native marketplace auto-update is separate: enable or disable it in `/plugin` → Marketplaces. Native updates may require `/reload-plugins` or a new session. Script `off` does not disable Claude's own updater. Manual Claude refresh uses `claude plugin marketplace update MARKETPLACE` then `claude plugin update PLUGIN@MARKETPLACE`.

Codex manual refresh uses `codex plugin marketplace upgrade MARKETPLACE` then `codex plugin add PLUGIN@MARKETPLACE`; start a new session after installation. Script policy does not replace host refresh behavior. Each reviewer updates independently.

## Development and releases

Run `python3 scripts/check.py` and `python3 scripts/test.py` (development dependencies: pytest and pytest-subtests). Runtime is standard-library Python; the AD inventory helper also uses Node.js. Host manifests must share a semantic version. Use `python3 scripts/version.py X.Y.Z` before changing skill/runtime behavior on main. CI enforces version bumps on pull requests; a `vX.Y.Z` tag validates main ancestry, runs checks, and creates the GitHub release. Catalogs consume released main; keep unpublished work on branches.

Owned source provenance is recorded in `PROVENANCE.json`. No installed system skills, external plugins, credentials, private rules/state, caches, or benchmark results are distributed.

## Qodo lifecycle

Requires Qodo CLI 1.6.0+ with async/context/recovery capabilities and an authenticated Qodo account. The helper preserves submission receipts, errors, and operation IDs outside the repository. Resume the same operation after interruption; an expired result never counts as a clean review. No DK installation is needed. Both host skills use the shared portable helper and instructions in `common/review/`.
