# Archify skill — unpacked, ready to copy

The [Archify](https://github.com/tt-a1i/archify) agent skill (stable release
**v2.16.0**), committed as plain files so you can grab the folder directly
instead of running the `npx skills add` installer.

The skill lives in [`archify/`](archify/) — that whole folder is what goes into
your skills directory. A packaged [`archify-skill-v2.16.0.zip`](archify-skill-v2.16.0.zip)
is kept alongside it for one-click download.

## Install

### Option A — download the ZIP

Grab [`archify-skill-v2.16.0.zip`](archify-skill-v2.16.0.zip), then:

```bash
mkdir -p ~/.claude/skills
unzip archify-skill-v2.16.0.zip -d ~/.claude/skills
```

The archive's top level is already `archify/`, so this lands correctly.

### Option B — clone and copy the folder

```bash
git clone https://github.com/rajkotraja/Archifyskill.git
mkdir -p ~/.claude/skills
cp -r Archifyskill/archify ~/.claude/skills/
```

### Option C — one project only

Run either option from the project root, targeting `.claude/skills` instead of
`~/.claude/skills`.

### Option D — no terminal

Use **Code → Download ZIP** on GitHub, expand it, and drag the inner `archify`
folder into `~/.claude/skills/`. In Finder press <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>G</kbd>
and type `~/.claude/skills` to get there. On Windows the path is
`C:\Users\<you>\.claude\skills`.

Whichever route you take, you should end up with:

```
~/.claude/skills/archify/SKILL.md
~/.claude/skills/archify/bin/archify.mjs
~/.claude/skills/archify/schemas/
~/.claude/skills/archify/examples/
...
```

Not `~/.claude/skills/Archifyskill/archify/...` — the `archify` folder must sit
directly inside `skills/`. Restart Claude Code afterwards so it loads.

### Claude.ai

Upload the ZIP under **Settings → Capabilities → Skills**.

### Option E — base64 text

[`archify-skill-v2.16.0.zip.base64.txt`](archify-skill-v2.16.0.zip.base64.txt)
is the same ZIP as base64 text (1.7 MB, 23,128 lines wrapped at 76 columns),
for moving the skill through a channel that only carries text. Decode it back
to the archive:

```bash
# macOS / Linux
base64 -d archify-skill-v2.16.0.zip.base64.txt > archify.zip

# Windows PowerShell
[IO.File]::WriteAllBytes("archify.zip", [Convert]::FromBase64String(
  (Get-Content archify-skill-v2.16.0.zip.base64.txt -Raw) -replace '\s',''))
```

Then verify and install as in Option A:

```bash
sha256sum archify.zip
# 4c59fa6557a2385beaaef8c7219cc414573acc9f0c30a932d5053b0b20689a46
unzip archify.zip -d ~/.claude/skills
```

GitHub will not preview a file this large in the browser — use the **Raw**
button or `curl` the raw URL.

## Requirements

Node.js 18 or newer — that is all. The renderer has no runtime npm dependencies,
so there is nothing to `npm install`.

```bash
node --version
```

## Check that it works

```bash
cd ~/.claude/skills/archify
node bin/archify.mjs deliver architecture \
  examples/production-deployment.architecture.json /tmp/demo.html --quality showcase
```

Expected:

```
delivered architecture /tmp/demo.html
9/9 artifact checks; composition showcase: pass; ... sha256 547c27a1c2d5
```

Open `/tmp/demo.html` in a browser to see the diagram.

## Use it

Ask in plain language:

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

- Archify occasionally makes an HTTPS request to see whether a newer version
  exists. It only prints a reminder and never downloads anything. Set
  `ARCHIFY_UPDATE_CHECK_DISABLED=1` to turn that off.
- `.gitattributes` pins `* text=auto eol=lf`. Without it, Git on Windows
  (`core.autocrlf=true`) rewrites the checked-out `.mjs` and `.html` files to
  CRLF, which breaks Archify's byte-exact template and validator checks
  (upstream issue #144). `*.png binary` keeps that rule away from image bytes.
- To update later, replace the `archify` folder with a newer release.

## Attached diagram

`transaction-alert-flow.png` (1536x1024, 1.2 MB) is a transaction alert-triage
flow: `TRXN's → RULEZ → ATL / BTL / NOISE → ML → alerts`, with the occurrence
and recurrence breakdown below it. It is unrelated to the Archify skill itself
and is kept here only for reference.

`transaction-alert-flow.png.base64.txt` is the same image as base64 text
(1.6 MB, 21,295 lines wrapped at 76 columns). Decode it back to the PNG:

```bash
# macOS / Linux
base64 -d transaction-alert-flow.png.base64.txt > diagram.png

# Windows PowerShell
[IO.File]::WriteAllBytes("diagram.png", [Convert]::FromBase64String(
  (Get-Content transaction-alert-flow.png.base64.txt -Raw) -replace '\s',''))
```

Verify:

```bash
sha256sum diagram.png
# a657c0524b9a11f418c43606034b5a598f04cb878c052a4e56f423f789b235a5
```

## Provenance

The skill is extracted verbatim from `archify.zip` at tag `v2.16.0` of
<https://github.com/tt-a1i/archify>. Upstream license: MIT (`archify/LICENSE`).

## Also in this repo: agent-kit

[`agent-kit/`](agent-kit/) installs three bundles, **SDLC_agents**, **all_in_one_generic_agents** and
the **archify** skill, into Claude Code and opencode from local copies:

```bash
cd agent-kit && python3 install.py
```

Or download [`agent-kit.zip`](agent-kit.zip). It holds the same script and its `vendor/` content
folder: `unzip agent-kit.zip && cd agent-kit && python3 install.py`. The same zip is also available as
base64 text in [`agent-kit.zip.base64.txt`](agent-kit.zip.base64.txt); decode it with
`base64 -d agent-kit.zip.base64.txt > agent-kit.zip`.

See [`agent-kit/README.md`](agent-kit/README.md) for what it installs, the options, and how it
merges with an existing setup.
