# agent-kit: SDLC_agents + all_in_one_generic_agents for Claude Code and opencode

One Python script that installs two agent bundles into **Claude Code** (`~/.claude`) and
**opencode** (`~/.config/opencode`) from local copies kept in this folder. Nothing is downloaded at
install time.

| Bundle | What it is |
|---|---|
| **SDLC_agents** | Engineering-lifecycle skills, personas and commands: spec, plan, build, test, review, ship |
| **all_in_one_generic_agents** | Language-specific skills, specialist agents, commands, rules and hooks |

## Install

**From the zip:** download [`agent-kit.zip`](../agent-kit.zip) from the repository root. It holds this
script and the `vendor/` content folder inside a single `agent-kit/` folder.

```bash
unzip agent-kit.zip
cd agent-kit
python3 install.py
```

**From a clone of this repository:**

```bash
cd agent-kit
python3 install.py --dry-run    # preview: shows what would change, writes nothing
python3 install.py              # install into Claude Code and opencode
```

Restart Claude Code / opencode afterwards.

**Requirements**

- Python 3.8+ (standard library only; tested on 3.8 and 3.13)
- Node.js 18+ for the all_in_one_generic_agents hooks in Claude Code, which run with `node`. Without
  it, use `--no-hooks`.
- opencode installs its own `@opencode-ai/plugin` dependency in the config folder the first time it
  starts (this needs network access).

## What you get

| | Claude Code | opencode |
|---|---|---|
| Skills | 220 (25 SDLC_agents + 195 all_in_one_generic_agents) | 220 |
| Agents | 55 (3 + 52) | 26 (3 + 23 defined in `opencode.json`) |
| Slash commands | 94 (8 + 86) | 100 |
| Hooks | 24 hooks in `settings.json` | a hooks plugin |
| Rules | `rules/generic-agents/` for the kept languages | (not an opencode feature) |

`skills/using-agent-skills` (the SDLC_agents router) is extended with a generated **Full kit**
section. That section routes by language to the generic bundle's skills, reviewer/build agents and
commands, and says which bundle to follow where they overlap. A `catalog.md` beside it lists every
installed skill, agent and command with a one-line description. Both are generated from the files
actually installed, and the tests check that they name nothing that is missing and leave nothing
out.

## What was left out (curated)

**SDLC_agents:** the `web-performance-auditor` agent and its `/webperf` command. The
`performance-optimization` and `browser-testing-with-devtools` skills it used stay, because `/review`,
`/test` and several other skills depend on them.

**all_in_one_generic_agents**, slimmed to SDLC plus Python, Java/JVM, JavaScript/TypeScript web, Go,
Rust, C++ and .NET. Removed, with their skills, agents, commands and rule sets:

- Kotlin / Android (including `/gradle-build`, which is Android/KMP-only; plain Java Gradle/Maven
  errors are handled by the `java-build-resolver` agent), Flutter / Dart, Swift / iOS,
  HarmonyOS / ArkTS, React Native, PHP / Laravel, Ruby / Rails, Perl
- Network gear (Cisco, homelab, BGP, WireGuard, Pi-hole), Flox, Uncloud
- Domain security: DeFi, EVM, healthcare / HIPAA, trading, bug bounty
- Business, marketing and SEO content; media and video generation; supply chain; prediction markets
  and compute marketplaces; operator-desk contracts; social posting; document processing

In total 129 skills, agents, commands and rule sets (628 files). The exact names are listed in
`GENERIC_EXCLUDE` in `tools/build_vendor.py` and recorded in `vendor/MANIFEST.json`.

Kept: the full quality workflow (TDD, verification, code review, git, e2e), databases,
Docker/Kubernetes/deployment, general security, machine learning, agentic patterns (including the GAN
harness), research, operator workflows (GitHub, Jira), benchmarking and memory.

## Naming

- The two bundles are called `SDLC_agents` and `all_in_one_generic_agents` everywhere in this kit: the
  `vendor/` folders, the `--source` values, the installer output, the router and the catalog.
- The generic bundle's self-named items are renamed:
  - `generic-agents-guide`, `generic-agents-recipes`, `configure-generic-agents` and
    `generic-agents-tools-cost-audit` (skills)
  - `/generic-agents-guide` (command)
  - `rules/generic-agents/` (rules folder)
- Where the two bundles clash on a name, the SDLC_agents copy is renamed: `sdlc-code-reviewer` and
  `/sdlc-plan`.
- **Unchanged:** names inside the generic bundle's scripts, such as the `ECC_*` environment variables
  and the hook script filenames. The hooks load and read those exact names, so renaming them would
  break the hooks. Text inside the bundled skills also still mentions the original project names in
  places.

## Options

| Option | Effect |
|---|---|
| `--target claude\|opencode\|all` | Which tool to install into (default: all) |
| `--source SDLC_agents\|all_in_one_generic_agents\|all` | Which bundle to install (default: all) |
| `--no-hooks` / `--hooks` | Leave out, or put back, the all_in_one_generic_agents hooks |
| `--sdlc-hooks` / `--no-sdlc-hooks` | Wire the opt-in SDLC_agents Claude Code hooks: the WebFetch cache and simplify-ignore (default: off; they act per project). They need bash, jq, curl, perl and shasum. |
| `--claude-dir PATH` | Default: `$CLAUDE_CONFIG_DIR` or `~/.claude` |
| `--opencode-dir PATH` | Default: `$OPENCODE_CONFIG_DIR`, `$XDG_CONFIG_HOME/opencode` or `~/.config/opencode` |
| `--dry-run` | Show what would change; write nothing |
| `--uninstall` | Remove what this script installed and restore anything it replaced |
| `--force` | Also overwrite (or remove) installed files you have edited; your versions are backed up first |
| `-v` | List every file operation |

Hook choices are remembered, so a plain `python3 install.py` later keeps whatever you chose last time.
Installs made by earlier versions of this kit are migrated automatically: files from renamed items
are cleaned up on the next run.

## How it treats your existing setup

- **Config files are merged, never replaced.** Your `settings.json` keys, permissions and hooks stay,
  and your hooks keep running first. In `opencode.json`, your own agents, commands, instructions and
  plugins win over the bundled ones on any name clash. If you use `opencode.jsonc`, it is left
  untouched: the bundled settings go into `opencode.json`, which opencode loads underneath it.
- **Ownership is tracked.** The script records every key and file it added (in
  `.agent-kit-state.json`), so re-runs update only its own entries and `--uninstall` removes exactly
  those.
- **Backups.** A file of yours that has to be replaced is copied to `.agent-kit-backups/<timestamp>/`
  first, and `--uninstall` puts it back.
- **Your edits are kept.** If you edit an installed file, re-runs and `--uninstall` leave it alone and
  tell you. Pass `--force` to override.
- **Re-running is idempotent.** An unchanged kit changes nothing: no rewrites, no backups.
- **It stops early on bad input.** A malformed `settings.json` / `opencode.json` stops the script before
  anything is written.
- **Your `package.json` is left alone.** An existing `~/.config/opencode/package.json` is never
  overwritten; opencode adds the one dependency the hooks plugin needs by itself.

## Behaviour to know about

- **The fact-forcing gate blocks the first Bash/Edit of each session** until Claude states some facts
  about the task. It is part of the generic bundle's hooks. To turn it off, set
  `GATEGUARD_DISABLED=1`, either in your shell or in `settings.json` under `"env"`. Other hooks can be
  tuned with `ECC_HOOK_PROFILE` (`minimal`, `standard` or `strict`) or disabled one by one with
  `ECC_DISABLED_HOOKS=<id>,<id>`.
- **The generic bundle sets `includeCoAuthoredBy: false`** in Claude Code if you haven't set it, which
  drops the "Co-Authored-By: Claude" commit trailer. If you already set it, your value is kept.
- **In opencode, the generic bundle redefines the primary `build` agent** (unless you have your own)
  and loads 14 instruction files into every session.
- **opencode also reads `~/.claude/skills`**, so with both tools installed it sees each skill twice. It
  keeps one copy per name and logs a "duplicate skill name" warning. Set
  `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` if the warnings bother you.
- **A few kept skills still mention removed items in their text.** For example, `plan-orchestrate`
  lists `kotlin-reviewer` among its options. The references are prose, not wiring; the full list is in
  `vendor/MANIFEST.json` under `excluded_names_still_mentioned_in`.
- **Windows is untested.** Paths are handled portably, and the generic bundle's hooks run through
  `node`, but the opt-in SDLC_agents hooks need bash.

## Fixes over a plain copy of the upstream output

Each of these was found by running the tools, and each is recorded in `vendor/MANIFEST.json`:

1. **The generic bundle's hook commands embed the build machine's install path**, base64-encoded, and
   trust it without checking. Copied as-is, 14 of the 24 hooks crash with `Cannot find module`. The
   installer writes your real path in.
2. **Its opencode hooks plugin loaded twice**, so every hook fired twice. A re-exporting module sat
   next to the real one. Measured with opencode 1.18.32: 2 initialisations before the fix, 1 after.
3. **Its opencode `instructions` are project-relative**, so in a global install they pointed at
   `<your project>/skills/...` and silently never loaded. They are rewritten to the installed files.
   A dangling `skills.paths` entry is dropped for the same reason.
4. **Its resolver skipped language skills for opencode** that its own `opencode.json` lists as
   instructions. The kept ones (56) are added.
5. **Its own `--no-hooks` still left the opencode hooks plugin active.** Here, `--no-hooks` removes
   it.
6. **Name clashes between the two bundles** (agent `code-reviewer`, command `/plan`) are resolved as
   described under *Naming*.
7. **Machine-specific install-state files are not shipped.**

## Maintaining

The maintainer tools live in the repository only; they are not in the zip.

```bash
python3 tools/test_install.py     # end-to-end tests in throwaway home directories
python3 tools/build_vendor.py     # rebuild vendor/ from the pinned upstream commits
python3 tools/build_zip.py        # regenerate ../agent-kit.zip (deterministic)
```

`tools/build_vendor.py` pins the two upstream Git repositories it builds from (`SDLC_REPO`,
`GENERIC_REPO`, with `SDLC_REF` / `GENERIC_REF` commits). To update, bump the refs, run it (needs git,
node and npm), then run `build_zip.py` and the tests, and commit `vendor/` together with the zip. A
test fails if the zip is out of date. The build refuses to finish if upstream changes break one of
its assumptions, for example an excluded or renamed name that no longer exists, or a language skill
that is not in the router's table.
