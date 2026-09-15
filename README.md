# Archify skill — ready-to-drop ZIP

A packaged copy of the [Archify](https://github.com/tt-a1i/archify) agent skill
(stable release **v2.16.0**), so you can install it by hand instead of running
the `npx skills add` installer.

**File:** [`archify-skill-v2.16.0.zip`](archify-skill-v2.16.0.zip) (1.3 MB, 76 files)

The archive already has the skill folder at its top level, so unzipping it into a
`skills/` directory produces `skills/archify/SKILL.md` — exactly the layout
Claude Code expects.

## Install

### Claude Code — all projects (global)

```bash
mkdir -p ~/.claude/skills
unzip archify-skill-v2.16.0.zip -d ~/.claude/skills
```

### Claude Code — one project only

Run this from the project root:

```bash
mkdir -p .claude/skills
unzip archify-skill-v2.16.0.zip -d .claude/skills
```

### Without a terminal

Double-click the ZIP to expand it, then drag the resulting `archify` folder into
`~/.claude/skills/`. On macOS, open Finder and press <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>G</kbd>,
then type `~/.claude/skills` to get there. On Windows the path is
`C:\Users\<you>\.claude\skills`.

Either way you should end up with:

```
~/.claude/skills/archify/SKILL.md
~/.claude/skills/archify/bin/archify.mjs
~/.claude/skills/archify/schemas/
~/.claude/skills/archify/examples/
...
```

Restart Claude Code (or start a new session) so it picks the skill up.

### Claude.ai

Upload the same ZIP under **Settings → Capabilities → Skills**.

## Requirements

Node.js 18 or newer — that is all. The renderer has no runtime npm
dependencies, so there is nothing to `npm install`.

```bash
node --version
```

## Check that it works

```bash
cd ~/.claude/skills/archify
node bin/archify.mjs deliver architecture \
  examples/production-deployment.architecture.json /tmp/demo.html --quality showcase
```

Expected output:

```
delivered architecture /tmp/demo.html
9/9 artifact checks; composition showcase: pass; ...
```

Open `/tmp/demo.html` in a browser to see the diagram.

## Use it

Just ask in plain language, for example:

```
Use Archify to draw: Browser -> API -> Redis cache -> PostgreSQL fallback.
```

or, inside a repository:

```
Analyze this repository, then use archify to create a high-level runtime
architecture diagram. Show 8-12 core components, one primary path, external
dependencies, and trust boundaries.
```

## Notes

- Archify makes an occasional HTTPS request to check whether a newer version
  exists; it only shows a reminder and never downloads anything. Set
  `ARCHIFY_UPDATE_CHECK_DISABLED=1` to turn that off entirely.
- To update later, replace the `archify` folder with a newer release.

## Provenance

Extracted verbatim from `archify.zip` at tag `v2.16.0` of
<https://github.com/tt-a1i/archify>. Upstream license: MIT (see
`LICENSE` inside the archive).
