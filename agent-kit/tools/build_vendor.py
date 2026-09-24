#!/usr/bin/env python3
"""Regenerate agent-kit/vendor/ from pinned upstream commits.

This is the maintainer tool. End users never need it: they run install.py,
which only copies the already-built vendor/ trees. Run this when you want to
pull newer upstream content, then commit the resulting vendor/ changes.

Requires: git, node (>=18) and npm on PATH, plus network access to GitHub and
the npm registry.

How ECC is vendored
-------------------
ECC ships its own manifest-driven Node installer. Instead of re-implementing
its module/target rules, this script runs that installer into throwaway HOME
directories (one per target) and vendors the resulting trees. The vendored
copy is therefore exactly what ECC itself installs, with these deliberate,
documented changes (all recorded in vendor/MANIFEST.json):

  1. Install-state records are dropped (ecc/install-state.json, ecc/state.db,
     ecc-install-state.json). They hold absolute paths of the build machine and
     no ECC hook reads them.
  2. settings.json / opencode.json are split out of the trees into
     vendor/ecc/config/ so install.py can MERGE them into the user's existing
     config rather than overwrite it.
  3. ECC's hook commands embed the install root base64-encoded and trust it
     without checking it exists. That value is replaced with a placeholder
     that install.py fills in with the real target root.
  4. opencode.json "skills.paths": ["../skills"] is dropped. opencode resolves
     it against the current *project* directory, so in a global install it
     points nowhere; opencode already scans <config>/skills natively.
  5. ECC's resolver skips the framework-language and machine-learning skill
     modules for opencode only because they depend on Claude-only modules
     (rules-core, agents-core). The skills themselves are plain SKILL.md
     folders, and ECC's own opencode.json lists five of them as instructions,
     so they are added to the opencode tree.

How agent-skills (skills.addy.ie) is vendored
---------------------------------------------
agent-skills is plain files, so it is copied directly. Two names collide with
ECC (agent "code-reviewer", command "plan"); ECC keeps the plain names because
dozens of its own files reference them, and agent-skills' copies are renamed
to addy-code-reviewer / addy-plan with their in-repo references rewritten.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ADDY_REPO = "https://github.com/addyosmani/agent-skills.git"
ECC_REPO = "https://github.com/affaan-m/ECC.git"

# Pinned upstream commits. Bump these to refresh, then re-run this script.
ADDY_REF = "bcab6a1b8503100e8618c3b4e32cc78de43de769"
ECC_REF = "bf70150eb2df8070024e5bdf08e4aa08959e2735"

ECC_ROOT_PLACEHOLDER = "__AGENT_KIT_ECC_ROOT_B64__"
ADDY_HOOKS_PLACEHOLDER = "__AGENT_KIT_ADDY_HOOKS_DIR__"
# Every ECC hook command embeds ECC's root resolver (hooks split across
# plugin-hook-bootstrap.js and lifecycle-hook-bootstrap.js, but all resolve the
# root the same way). install.py uses this to recognise, and replace on
# reinstall, the hooks ECC owns.
ECC_HOOK_MARKER = "resolve-ecc-root"
ADDY_HOOK_MARKER = "/hooks/addy/"

ECC_STATE_FILES = {"ecc/install-state.json", "ecc/state.db", "ecc-install-state.json"}

# ECC slimmed to SDLC + Python, Java/JVM, JS/TS web, Go, Rust, C++ and .NET.
# ECC's own components cannot express this (every lang:*/framework:* component
# maps to the single framework-language module), so it is done per file. Every
# name below was confirmed with the user; nothing is matched by pattern.
ECC_EXCLUDE = {
    "Kotlin / Android": {
        "skills": ["android-clean-architecture", "compose-multiplatform-patterns", "kotlin-coroutines-flows",
                   "kotlin-exposed-patterns", "kotlin-ktor-patterns", "kotlin-patterns", "kotlin-testing"],
        "agents": ["kotlin-reviewer", "kotlin-build-resolver"],
        # gradle-build is Android/KMP-only (compileDebugKotlin, Compose); plain
        # Java Gradle/Maven errors are covered by the java-build-resolver agent.
        "commands": ["kotlin-build", "kotlin-review", "kotlin-test", "gradle-build"],
        "rules": ["kotlin"],
    },
    "Flutter / Dart": {
        "skills": ["dart-flutter-patterns", "flutter-dart-code-review"],
        "agents": ["flutter-reviewer", "dart-build-resolver"],
        "commands": ["flutter-build", "flutter-review", "flutter-test"],
        "rules": ["dart"],
    },
    "Swift / iOS": {
        "skills": ["foundation-models-on-device", "ios-icon-gen", "liquid-glass-design", "swift-actor-persistence",
                   "swift-concurrency-6-2", "swift-protocol-di-testing", "swiftui-patterns"],
        "agents": ["swift-reviewer", "swift-build-resolver"],
        "rules": ["swift"],
    },
    "HarmonyOS / ArkTS": {"agents": ["harmonyos-app-resolver"], "rules": ["arkts"]},
    "React Native": {"skills": ["react-native-patterns"], "rules": ["react-native"]},
    "PHP / Laravel": {
        "skills": ["laravel-patterns", "laravel-plugin-discovery", "laravel-tdd", "laravel-verification",
                   "laravel-security"],
        "agents": ["php-reviewer"],
        "rules": ["php"],
    },
    "Ruby / Rails": {"skills": ["rails-patterns"], "rules": ["ruby"]},
    "Perl": {"skills": ["perl-patterns", "perl-testing", "perl-security"], "rules": ["perl"]},
    "network gear, Flox, Uncloud (DevOps)": {
        "skills": ["cisco-ios-patterns", "homelab-network-readiness", "homelab-network-setup", "homelab-pihole-dns",
                   "homelab-vlan-segmentation", "homelab-wireguard-vpn", "netmiko-ssh-automation",
                   "network-bgp-diagnostics", "network-config-validation", "network-interface-health",
                   "flox-environments", "uncloud"],
        "agents": ["homelab-architect", "network-architect", "network-config-reviewer", "network-troubleshooter"],
    },
    "domain security (DeFi, EVM, healthcare/HIPAA, trading, bug bounty)": {
        "skills": ["defi-amm-security", "evm-token-decimals", "nodejs-keccak256", "healthcare-cdss-patterns",
                   "healthcare-emr-patterns", "healthcare-eval-harness", "healthcare-phi-compliance",
                   "hipaa-compliance", "llm-trading-agent-security", "security-bounty-hunter"],
        "agents": ["healthcare-reviewer"],
    },
    "business, marketing and SEO content": {
        "skills": ["article-writing", "brand-discovery", "brand-voice", "competitive-platform-analysis",
                   "competitive-report-structure", "content-engine", "investor-materials", "investor-outreach",
                   "lead-intelligence", "market-research", "marketing-campaign", "product-capability", "seo",
                   "social-graph-ranker"],
        "agents": ["marketing-agent", "seo-specialist", "chief-of-staff"],
        "commands": ["marketing-campaign"],
    },
    "media and video generation": {
        "skills": ["blender-motion-state-inspection", "fal-ai-media", "manim-video", "remotion-video-creation",
                   "taste", "taste-application", "taste-distillation", "tasteforge-video", "ui-demo",
                   "video-editing", "videodb"],
    },
    "supply chain": {
        "skills": ["carrier-relationship-management", "customs-trade-compliance", "energy-procurement",
                   "inventory-demand-planning", "logistics-exception-management", "production-scheduling",
                   "quality-nonconformance", "returns-reverse-logistics"],
    },
    "prediction markets, Ito compute, Nasiko, operator-desk contracts": {
        "skills": ["ito-baskets", "prediction-market-oracle-research", "prediction-market-risk-review",
                   "ito-compute", "ito-inference", "ito-training", "nasiko-control-plane",
                   "counterparty-channel-discipline", "esign-field-placement", "master-agreement-generator",
                   "operator-approval-loop"],
    },
    "social posting": {"skills": ["crosspost", "social-publisher", "x-api"]},
    "document processing": {"skills": ["nutrient-document-processing", "visa-doc-translate"]},
}

# name collisions with ECC -> the agent-skills copy is renamed
ADDY_RENAMES = {
    "agents": {"code-reviewer": "addy-code-reviewer"},
    "commands": {"plan": "addy-plan"},
}
# Left out on request. Only files that exist solely for this persona are listed:
# the skills it uses (performance-optimization, browser-testing-with-devtools)
# and references/performance-checklist.md are shared with /review, /test and
# several other skills, so they stay.
ADDY_EXCLUDE = {
    "agents/web-performance-auditor.md": "web-performance-auditor",
    "commands/webperf.md": "/webperf",
}
ADDY_HOOK_SCRIPTS = ["sdd-cache-pre.sh", "sdd-cache-post.sh", "simplify-ignore.sh"]

# ---- using-agent-skills: routing data for the generated "Full kit" section.
# Every name is checked against the built trees; anything not installed in a
# target is simply left out of that target's copy.
LANGUAGE_MAP = {
    "Python": {
        "skills": ["python-patterns", "python-testing", "django-patterns", "django-tdd", "django-verification",
                   "django-celery", "django-security", "fastapi-patterns", "generating-python-installer"],
        "agents": ["python-reviewer", "django-reviewer", "django-build-resolver", "fastapi-reviewer"],
        "commands": ["python-review", "fastapi-review"],
    },
    "Java / JVM": {
        "skills": ["java-coding-standards", "springboot-patterns", "springboot-tdd", "springboot-verification",
                   "springboot-security", "quarkus-patterns", "quarkus-tdd", "quarkus-verification",
                   "quarkus-security", "jpa-patterns", "tinystruct-patterns"],
        "agents": ["java-reviewer", "java-build-resolver"],
        "commands": [],
    },
    "JavaScript / TypeScript (web)": {
        "skills": ["frontend-patterns", "react-patterns", "react-performance", "react-testing", "nextjs-turbopack",
                   "vue-patterns", "nuxt4-patterns", "ui-to-vue", "angular-developer", "nestjs-patterns",
                   "vite-patterns", "bun-runtime", "prisma-patterns", "design-system", "frontend-design-direction",
                   "frontend-a11y", "accessibility", "make-interfaces-feel-better", "motion-foundations",
                   "motion-patterns", "motion-advanced", "frontend-slides"],
        "agents": ["typescript-reviewer", "react-reviewer", "react-build-resolver", "vue-reviewer", "a11y-architect"],
        "commands": ["react-build", "react-review", "react-test", "vue-review", "pm2"],
    },
    "Go": {"skills": ["golang-patterns", "golang-testing"], "agents": ["go-reviewer", "go-build-resolver"],
           "commands": ["go-build", "go-review", "go-test"]},
    "Rust": {"skills": ["rust-patterns", "rust-testing"], "agents": ["rust-reviewer", "rust-build-resolver"],
             "commands": ["rust-build", "rust-review", "rust-test"]},
    "C++": {"skills": ["cpp-coding-standards", "cpp-testing"], "agents": ["cpp-reviewer", "cpp-build-resolver"],
            "commands": ["cpp-build", "cpp-review", "cpp-test"]},
    ".NET (C# / F#)": {"skills": ["dotnet-patterns", "csharp-testing", "fsharp-testing"],
                       "agents": ["csharp-reviewer", "fsharp-reviewer"], "commands": []},
    "Machine learning": {"skills": ["pytorch-patterns", "mle-workflow", "ml-adoption-playbook",
                                    "recsys-pipeline-architect"],
                         "agents": ["mle-reviewer", "pytorch-build-resolver", "rag-pipeline-reviewer"],
                         "commands": []},
    "Any backend": {"skills": ["api-design", "backend-patterns", "coding-standards", "contract-first",
                               "hexagonal-architecture", "mcp-server-patterns"], "agents": [], "commands": []},
}
# ECC's own module grouping, used to list its remaining skills by area.
ECC_AREAS = [
    ("workflow-quality", "Quality workflow (TDD, verification, review, git, e2e)"),
    ("security", "Security"),
    ("database", "Databases"),
    ("devops-infra", "DevOps and deployment"),
    ("agentic-patterns", "Agentic patterns (building and orchestrating AI agents)"),
    ("orchestration", "Parallel worktree orchestration"),
    ("research-apis", "Research"),
    ("operator-workflows", "Operator workflows (GitHub, Jira, project ops)"),
    ("optimization-workflows", "Benchmarking and performance"),
    ("skill-unified-memory", "Memory"),
]
# Where agent-skills and ECC cover the same step: the agent-skills phase skill
# stays the process, the ECC item adds depth.
OVERLAPS = [
    ("test-driven-development", ["tdd-workflow", "e2e-testing"]),
    ("code-review-and-quality", ["code-reviewer"]),
    ("security-and-hardening", ["security-review", "security-scan"]),
    ("git-workflow-and-versioning", ["git-workflow"]),
    ("documentation-and-adrs", ["architecture-decision-records"]),
    ("api-and-interface-design", ["api-design"]),
    ("planning-and-task-breakdown", ["planner"]),
    ("frontend-ui-engineering", ["frontend-patterns"]),
]
ADDY_HOOK_DOCS = ["SDD-CACHE.md", "SIMPLIFY-IGNORE.md"]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"command failed ({res.returncode}): {' '.join(cmd)}\n{res.stdout}\n{res.stderr}")
    return res.stdout


def fetch(repo: str, ref: str, dest: Path) -> dict:
    dest.mkdir(parents=True)
    run(["git", "init", "-q"], cwd=dest)
    run(["git", "remote", "add", "origin", repo], cwd=dest)
    run(["git", "fetch", "-q", "--depth", "1", "origin", ref], cwd=dest)
    run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest)
    sha, date, subject = run(["git", "log", "-1", "--format=%H%n%cs%n%s"], cwd=dest).splitlines()[:3]
    return {"repo": repo.removesuffix(".git"), "commit": sha, "commit_date": date, "commit_subject": subject}


def copytree(src: Path, dst: Path, skip: set[str] | None = None) -> None:
    """Copy src into dst; `skip` holds POSIX paths relative to src."""
    skip = skip or set()
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src).as_posix()
        if rel in skip or any(rel.startswith(s + "/") for s in skip):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def files_under(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for rel in sorted(files_under(root)):
        h.update(rel.encode() + b"\0" + (root / rel).read_bytes() + b"\0")
    return h.hexdigest()


# --------------------------------------------------------------------- ECC

def ecc_install(src: Path, target: str, home: Path, hooks: bool) -> Path:
    home.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in ("OPENCODE_CONFIG_DIR", "XDG_CONFIG_HOME")}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    flag = "--enable-hooks" if hooks else "--no-hooks"
    out = run(["node", "scripts/install-apply.js", "--target", target, "--profile", "full", flag, "--json"],
              cwd=src, env=env)
    result = json.loads(out)["result"]
    root = Path(result["targetRoot"] if "targetRoot" in result else result["plan"]["targetRoot"])
    if not root.is_dir():
        sys.exit(f"ECC reported target root {root} but it does not exist")
    return root


def apply_ecc_exclusions(out: Path, oc: dict) -> tuple[dict, dict]:
    """Delete ECC_EXCLUDE entries from both trees and from opencode.json's inline config."""
    trees = {"claude": out / "claude", "opencode": out / "opencode"}
    layout = {  # kind -> (claude path, opencode path); opencode has no agents/ or rules/ dirs
        "skills": ("skills/{}", "skills/{}"),
        "agents": ("agents/{}.md", "prompts/agents/{}.txt"),
        "commands": ("commands/{}.md", "commands/{}.md"),
        "rules": ("rules/ecc/{}", None),
    }
    removed = {"files": 0, "names": {}}
    for reason, kinds in ECC_EXCLUDE.items():
        for kind, names in kinds.items():
            for name in names:
                hits = 0
                for tree_key, pattern in zip(("claude", "opencode"), layout[kind]):
                    if pattern is None:
                        continue
                    path = trees[tree_key] / pattern.format(name)
                    if path.is_dir():
                        removed["files"] += sum(1 for p in path.rglob("*") if p.is_file())
                        shutil.rmtree(path)
                        hits += 1
                    elif path.is_file():
                        removed["files"] += 1
                        path.unlink()
                        hits += 1
                if kind == "agents" and oc.get("agent", {}).pop(name, None) is not None:
                    hits += 1
                if kind == "commands" and oc.get("command", {}).pop(name, None) is not None:
                    hits += 1
                if not hits:
                    sys.exit(f"ECC_EXCLUDE: {kind[:-1]} '{name}' ({reason}) does not exist; typo or upstream rename")
                removed["names"].setdefault(reason, []).append(f"{kind[:-1]}:{name}")

    # Inline opencode entries that point at something now gone would break at load time.
    for section in ("agent", "command"):
        for name, value in list(oc.get(section, {}).items()):
            for ref in re.findall(r"\{file:([^}]+)\}", json.dumps(value)):
                if not (trees["opencode"] / ref).exists():
                    sys.exit(f"opencode.json {section}.{name} references removed file {ref}")
            agent = value.get("agent") if isinstance(value, dict) else None
            if agent and agent not in oc.get("agent", {}) and agent not in ("build", "plan", "general", "explore"):
                sys.exit(f"opencode.json command.{name} uses removed agent {agent}")
    oc["instructions"] = [i for i in oc.get("instructions", [])
                          if not i.startswith("skills/") or (trees["opencode"] / i).exists()]

    # Report (don't fail on) kept files that still mention a removed name: ECC's
    # catalog/guide skills list everything, which is harmless prose.
    gone = {n.split(":", 1)[1] for names in removed["names"].values() for n in names}
    pattern = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, sorted(gone, key=len, reverse=True))) + r")(?![\w-])")
    dangling = {}
    for rel in files_under(trees["claude"]):
        if rel.endswith((".md", ".json", ".txt")):
            hits = set(pattern.findall((trees["claude"] / rel).read_text(encoding="utf-8", errors="ignore")))
            if hits:
                dangling[rel] = sorted(hits)
    return removed, dangling


def build_ecc(src: Path, work: Path, out: Path, meta: dict) -> None:
    log("ECC: installing npm dependencies (--ignore-scripts)")
    run(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"], cwd=src)
    log("ECC: compiling the opencode plugin payload")
    run(["node", "scripts/build-opencode.js"], cwd=src)
    meta["version"] = (src / "VERSION").read_text().strip()

    roots = {}
    for target in ("claude", "opencode"):
        for hooks in (True, False):
            key = f"{target}-{'hooks' if hooks else 'nohooks'}"
            log(f"ECC: running upstream installer --target {target} --profile full {'with' if hooks else 'without'} hooks")
            roots[key] = ecc_install(src, target, work / f"home-{key}", hooks)

    config = out / "config"
    config.mkdir(parents=True)

    # ---- Claude Code
    c_root = roots["claude-hooks"]
    settings = json.loads((c_root / "settings.json").read_text())
    real_b64 = base64.b64encode(str(c_root).encode()).decode()
    commands = [h["command"] for groups in settings.get("hooks", {}).values() for g in groups for h in g["hooks"]]
    if not commands:
        sys.exit("ECC settings.json contains no hooks; installer output changed shape")
    for cmd in commands:
        if ECC_HOOK_MARKER not in cmd or real_b64 not in cmd:
            sys.exit(f"ECC hook command lacks the expected marker/root, refusing to vendor:\n{cmd[:300]}")
    settings_text = json.dumps(settings, indent=2).replace(real_b64, ECC_ROOT_PLACEHOLDER)
    if str(c_root) in settings_text:
        sys.exit("plain build path still present in settings.json after placeholder substitution")
    (config / "claude-settings.json").write_text(settings_text + "\n")
    copytree(c_root, out / "claude", skip=ECC_STATE_FILES | {"settings.json", "ecc"})

    # ---- OpenCode
    o_root = roots["opencode-hooks"]
    oc = json.loads((o_root / "opencode.json").read_text())
    dropped_paths = oc.get("skills", {}).pop("paths", None)
    if "skills" in oc and not oc["skills"]:
        del oc["skills"]
    (config / "opencode.json").write_text(json.dumps(oc, indent=2) + "\n")
    copytree(o_root, out / "opencode", skip=ECC_STATE_FILES | {"opencode.json"})

    # opencode auto-loads every plugins/*.ts as its own module, and ECC ships
    # both ecc-hooks.ts and index.ts (a re-export of it); the "./plugins" entry
    # resolves to index.ts too. opencode only dedupes exports within a module,
    # so ECCHooksPlugin initialised twice and every hook fired twice (verified
    # against opencode 1.18.32). Keep the single real module.
    plugins_dir = out / "opencode" / "plugins"
    reexport = plugins_dir / "index.ts"
    if not re.search(r'export \{ ECCHooksPlugin, default \} from "\./ecc-hooks\.ts"', reexport.read_text()):
        sys.exit("plugins/index.ts is no longer a plain re-export of ecc-hooks.ts; re-check the double-load fix")
    reexport.unlink()
    root_index = out / "opencode" / "index.ts"
    root_index.write_text(root_index.read_text().replace(
        'export { default } from "./plugins/index.ts"', 'export { default } from "./plugins/ecc-hooks.ts"'))
    oc["plugin"] = [p for p in oc.get("plugin", []) if p != "./plugins"]
    if not oc["plugin"]:
        del oc["plugin"]
    (config / "opencode.json").write_text(json.dumps(oc, indent=2) + "\n")

    added = []
    oc_skills = out / "opencode" / "skills"
    for skill in sorted((c_root / "skills").iterdir()):
        if skill.is_dir() and not (oc_skills / skill.name).exists():
            copytree(skill, oc_skills / skill.name)
            added.append(skill.name)

    excluded, dangling = apply_ecc_exclusions(out, oc)
    (config / "opencode.json").write_text(json.dumps(oc, indent=2) + "\n")
    added = [s for s in added if (oc_skills / s).exists()]

    # Any path left holding a build-machine absolute path is a bug.
    for tree in (out / "claude", out / "opencode", config):
        for rel in files_under(tree):
            data = (tree / rel).read_bytes()
            if str(work).encode() in data or base64.b64encode(str(work).encode())[:24] in data:
                sys.exit(f"build path leaked into vendored file {tree.name}/{rel}")

    # Hook-runtime paths, as ECC itself classifies them: what --enable-hooks
    # installs that --no-hooks does not.
    hook_paths = {}
    for target in ("claude", "opencode"):
        with_h = files_under(roots[f"{target}-hooks"]) - ECC_STATE_FILES
        without = files_under(roots[f"{target}-nohooks"]) - ECC_STATE_FILES
        hook_paths[target] = sorted(with_h - without)
    # ECC's --no-hooks still ships the opencode hooks plugin (plugins/ is
    # auto-loaded by opencode), so treat it as hook runtime too.
    hook_paths["opencode"] = sorted(set(hook_paths["opencode"]) |
                                    {p for p in files_under(out / "opencode") if p.startswith("plugins/")})

    meta.update(
        profile="full",
        hook_runtime_paths=hook_paths,
        hook_marker=ECC_HOOK_MARKER,
        root_placeholder=ECC_ROOT_PLACEHOLDER,
        opencode_plugin_entry="./plugins",
        transforms=[
            "dropped install-state records: " + ", ".join(sorted(ECC_STATE_FILES)),
            "settings.json and opencode.json moved to vendor/ecc/config/ for merging",
            f"base64 install root in {len(commands)} hook commands replaced by {ECC_ROOT_PLACEHOLDER}",
            f"opencode.json skills.paths {dropped_paths!r} dropped (project-relative, dangling in a global install)",
            f"added {len(added)} skills to opencode that ECC's resolver skipped transitively",
            "opencode: removed plugins/index.ts and the './plugins' entry (ECCHooksPlugin was "
            "loaded twice, firing every hook twice); root index.ts re-pointed at plugins/ecc-hooks.ts",
            f"slimmed to SDLC + Python/Java/JS-TS/Go/Rust/C++/.NET: removed "
            f"{sum(len(v) for v in excluded['names'].values())} skills/agents/commands/rule sets "
            f"({excluded['files']} files) across {len(excluded['names'])} categories",
        ],
        excluded=excluded["names"],
        excluded_names_still_mentioned_in=dangling,
        opencode_added_skills=added,
        counts={
            "claude": {d: len(list((out / "claude" / d).iterdir())) for d in ("agents", "commands", "skills", "rules")},
            "opencode": {d: len(list((out / "opencode" / d).iterdir())) for d in ("commands", "skills")},
            "claude_hook_commands": len(commands),
            "opencode_inline_agents": len(oc.get("agent", {})),
            "opencode_inline_commands": len(oc.get("command", {})),
        },
    )


# ------------------------------------------------------------ agent-skills

def rewrite_names(text: str) -> str:
    # word-bounded so e.g. "code-reviewer's" is rewritten but "my-code-reviewer" is not
    text = re.sub(r"(?<![\w-])code-reviewer(?![\w-])", "addy-code-reviewer", text)
    text = re.sub(r"(?<![\w/-])/plan(?![\w-])", "/addy-plan", text)
    return text


def build_addy(src: Path, out: Path, meta: dict) -> None:
    plugin = json.loads((src / ".claude-plugin" / "plugin.json").read_text())
    meta["version"] = plugin.get("version")

    staging = out / "_staging"
    copytree(src / "skills", staging / "skills")
    copytree(src / "references", staging / "references")
    copytree(src / "agents", staging / "agents")
    # commands/*.toml are Gemini CLI format; the Claude/opencode markdown ones live here
    copytree(src / ".claude" / "commands", staging / "commands")

    for rel in ADDY_EXCLUDE:
        (staging / rel).unlink()
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for rel, name in ADDY_EXCLUDE.items():
                if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
                    sys.exit(f"{path.relative_to(staging)} still references excluded {name} ({rel}); "
                             "update ADDY_EXCLUDE or rewrite that reference")

    renamed = []
    for kind, mapping in ADDY_RENAMES.items():
        for old, new in mapping.items():
            (staging / kind / f"{old}.md").rename(staging / kind / f"{new}.md")
            renamed.append(f"{kind}/{old}.md -> {kind}/{new}.md")

    rewritten = []
    for path in sorted(staging.rglob("*.md")):
        before = path.read_text(encoding="utf-8")
        after = rewrite_names(before)
        if after != before:
            path.write_text(after, encoding="utf-8")
            rewritten.append(path.relative_to(staging).as_posix())

    fm = (staging / "agents" / "addy-code-reviewer.md").read_text()
    if not re.search(r"^name:\s*addy-code-reviewer\s*$", fm, re.M):
        sys.exit("renamed agent frontmatter was not updated")

    for target in ("claude", "opencode"):
        copytree(staging, out / target)
    shutil.rmtree(staging)

    hooks_dir = out / "claude" / "hooks" / "addy"
    hooks_dir.mkdir(parents=True)
    for name in ADDY_HOOK_SCRIPTS + ADDY_HOOK_DOCS:
        shutil.copy2(src / "hooks" / name, hooks_dir / name)

    cmd = lambda script: f'bash "{ADDY_HOOKS_PLACEHOLDER}/{script}"'
    fragment = {"hooks": {
        "PreToolUse": [
            {"matcher": "WebFetch", "hooks": [{"type": "command", "command": cmd("sdd-cache-pre.sh"), "timeout": 10}]},
            {"matcher": "Read", "hooks": [{"type": "command", "command": cmd("simplify-ignore.sh")}]},
        ],
        "PostToolUse": [
            {"matcher": "WebFetch", "hooks": [{"type": "command", "command": cmd("sdd-cache-post.sh"), "async": True, "timeout": 10}]},
            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": cmd("simplify-ignore.sh")}]},
        ],
        "Stop": [
            {"hooks": [{"type": "command", "command": cmd("simplify-ignore.sh")}]},
        ],
    }}
    (out / "config").mkdir()
    (out / "config" / "claude-hooks.json").write_text(json.dumps(fragment, indent=2) + "\n")

    meta.update(
        hook_marker=ADDY_HOOK_MARKER,
        hooks_placeholder=ADDY_HOOKS_PLACEHOLDER,
        transforms=[
            "commands taken from .claude/commands/*.md (commands/*.toml are Gemini CLI format)",
            "excluded on request: " + ", ".join(sorted(ADDY_EXCLUDE)) + " (the performance skills and "
            "checklist it uses are shared with /review and /test, so they are kept)",
            *[f"renamed {r} (name collides with ECC)" for r in renamed],
            f"rewrote code-reviewer -> addy-code-reviewer and /plan -> /addy-plan in {len(rewritten)} files",
            "hooks: only the three wireable scripts are shipped (session-start.sh is deliberately "
            "not wired for Claude Code by its author; *-test.sh are upstream tests)",
            "hook wiring is opt-in (install.py --enable-addy-hooks), matching upstream's per-project guidance",
        ],
        rewritten_files=rewritten,
        counts={t: {d: len(list((out / t / d).iterdir())) for d in ("agents", "commands", "skills", "references")}
                for t in ("claude", "opencode")},
    )


# ------------------------------------------------ using-agent-skills catalog

META_REL = "skills/using-agent-skills"
GEN_BEGIN = "<!-- BEGIN agent-kit generated section: regenerate with agent-kit/tools/build_vendor.py -->"
GEN_END = "<!-- END agent-kit generated section -->"


def frontmatter_description(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.startswith("---"):
        return ""
    lines = text[3:text.find("\n---", 3)].splitlines()
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            value = line[len("description:"):].strip()
            if value in ("", ">", "|", ">-", "|-"):
                value = " ".join(l.strip() for l in lines[i + 1:] if l.startswith((" ", "\t")))
            return value.strip().strip("\"'")
    return ""


def short(desc: str, limit: int = 150) -> str:
    desc = " ".join(desc.split())
    first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def inventory(staged: Path, target: str) -> dict:
    """Every skill/agent/command installed into `target`, by source, with descriptions."""
    inv = {"skills": {}, "agents": {}, "commands": {}}
    for source in ("addy", "ecc"):
        root = staged / source / target
        for skill in sorted((root / "skills").iterdir()) if (root / "skills").is_dir() else []:
            if (skill / "SKILL.md").is_file():
                inv["skills"][skill.name] = (source, short(frontmatter_description(skill / "SKILL.md")))
        for kind in ("agents", "commands"):
            for f in sorted((root / kind).glob("*.md")) if (root / kind).is_dir() else []:
                inv[kind][f.stem] = (source, short(frontmatter_description(f)))
    if target == "opencode":                          # ECC defines these inline in opencode.json
        oc = json.loads((staged / "ecc" / "config" / "opencode.json").read_text())
        for name, spec in oc.get("agent", {}).items():
            mode = " (primary agent)" if spec.get("mode") == "primary" else ""
            inv["agents"].setdefault(name, ("ecc", short(spec.get("description", "")) + mode))
        for name, spec in oc.get("command", {}).items():
            inv["commands"].setdefault(name, ("ecc", short(spec.get("description", ""))))
    return inv


def code(names, prefix=""):
    return ", ".join(f"`{prefix}{n}`" for n in names) or "—"


def render_full_kit(inv: dict, target: str, skill_area: dict) -> str:
    has = lambda kind, n: n in inv[kind]
    delegate = ("use the Agent tool with `subagent_type` set to the agent's name"
                if target == "claude" else "mention it as `@agent-name`, or let the primary agent call it via the task tool")
    out = [GEN_BEGIN, "", "## Full kit: agent-skills + ECC", "",
           "This environment also has **ECC** installed next to agent-skills. Combine them like this:", "",
           "1. **Pick the phase with the flowchart above.** The agent-skills phase skills are the default process.",
           "2. **Layer in the stack-specific skills** for the language you are touching (table below). They add "
           "idioms, testing and verification detail to the phase skill; they do not replace it.",
           f"3. **Delegate to a specialist agent** for a focused review or build fix: {delegate}.",
           "4. **Suggest slash commands** to the user when one fits. Commands are typed by the user; never claim one ran.",
           "5. **Everything installed is in `catalog.md`** next to this file, with one-line descriptions. "
           "Read it when nothing below fits.", ""]

    out += ["### By language", "", "| Stack | Skills | Agents | Commands |", "|---|---|---|---|"]
    routed = set()
    for stack, items in LANGUAGE_MAP.items():
        s = [n for n in items["skills"] if has("skills", n)]
        a = [n for n in items["agents"] if has("agents", n)]
        c = [n for n in items["commands"] if has("commands", n)]
        routed |= set(s) | set(a) | set(c)
        if s or a or c:
            out.append(f"| {stack} | {code(s)} | {code(a)} | {code(c, '/')} |")

    out += ["", "### When both kits cover the same step", "",
            "Follow the agent-skills skill as the process and pull in the ECC item for depth:", ""]
    for addy_skill, ecc_items in OVERLAPS:
        present = [f"`{n}` skill" if has("skills", n) else f"`{n}` agent"
                   for n in ecc_items if has("skills", n) or has("agents", n)]
        if has("skills", addy_skill) and present:
            out.append(f"- `{addy_skill}` → also {', '.join(present)}")

    out += ["", "### Other ECC skills by area", ""]
    for module, label in ECC_AREAS:
        names = [n for n in skill_area.get(module, []) if has("skills", n) and n not in routed]
        if names:
            out.append(f"- **{label}:** {code(names)}")

    addy_agents = sorted(n for n, (src, _) in inv["agents"].items() if src == "addy")
    ecc_agents = sorted(n for n, (src, _) in inv["agents"].items() if src == "ecc" and n not in routed)
    out += ["", "### Agents", "",
            f"- **agent-skills personas** (fanned out by `/ship`): {code(addy_agents)}",
            f"- **ECC general-purpose:** {code(ecc_agents)}",
            "- Language reviewers and build resolvers are in the table above."]

    addy_cmds = sorted(n for n, (src, _) in inv["commands"].items() if src == "addy")
    ecc_cmds = sorted(n for n, (src, _) in inv["commands"].items() if src == "ecc" and n not in routed)
    out += ["", "### Commands", "",
            f"- **agent-skills lifecycle:** {code(addy_cmds, '/')}",
            f"- **ECC:** {code(ecc_cmds, '/')}",
            "- Language-specific commands are in the table above.", "", GEN_END, ""]
    return "\n".join(out)


def render_catalog(inv: dict, target: str) -> str:
    label = {"addy": "agent-skills", "ecc": "ECC"}
    out = [f"# Installed catalog ({'Claude Code' if target == 'claude' else 'opencode'})", "",
           "Every skill, agent and command this kit installed, with the first sentence of its description. "
           "Generated by agent-kit/tools/build_vendor.py; do not edit by hand.", ""]
    for kind, title in (("skills", "Skills"), ("agents", "Agents"), ("commands", "Commands")):
        out += [f"## {title} ({len(inv[kind])})", ""]
        for name, (src, desc) in sorted(inv[kind].items()):
            shown = f"/{name}" if kind == "commands" else name
            out.append(f"- `{shown}` [{label[src]}] {desc}".rstrip())
        out.append("")
    return "\n".join(out)


def build_meta_skill(staged: Path, ecc_src: Path, manifest: dict) -> None:
    modules = json.loads((ecc_src / "manifests" / "install-modules.json").read_text())["modules"]
    skill_area = {}
    for m in modules:
        for p in m["paths"]:
            if p.startswith("skills/"):
                skill_area.setdefault(m["id"], []).append(p.split("/", 1)[1])

    excluded = {n for kinds in ECC_EXCLUDE.values() for names in kinds.values() for n in names}
    wanted = {n for items in LANGUAGE_MAP.values() for kind in items.values() for n in kind}
    wanted |= {n for a, e in OVERLAPS for n in [a, *e]}
    if wanted & excluded:
        sys.exit(f"routing data names excluded items: {sorted(wanted & excluded)}")

    invs = {t: inventory(staged, t) for t in ("claude", "opencode")}
    everywhere = {n for inv in invs.values() for kind in inv.values() for n in kind}
    if wanted - everywhere:
        sys.exit(f"routing data names items that are not installed anywhere: {sorted(wanted - everywhere)}")
    routed = {n for items in LANGUAGE_MAP.values() for n in items["skills"]}
    unrouted = [n for n in skill_area.get("framework-language", []) if n in invs["claude"]["skills"] and n not in routed]
    if unrouted:
        sys.exit(f"language skills missing from LANGUAGE_MAP (add them so the router knows them): {unrouted}")

    stats = {}
    for target, inv in invs.items():
        meta_dir = staged / "addy" / target / META_REL
        stock = (meta_dir / "SKILL.md").read_text(encoding="utf-8")
        standalone = staged / "addy" / "standalone" / target / META_REL / "SKILL.md"
        standalone.parent.mkdir(parents=True, exist_ok=True)
        standalone.write_text(stock, encoding="utf-8")

        combined = stock.replace(
            "This is the meta-skill that governs how all other skills are discovered and invoked.",
            "This is the meta-skill that governs how all other skills are discovered and invoked, including "
            "the installed ECC language skills, specialist agents and slash commands.", 1)
        if combined == stock:
            sys.exit("using-agent-skills description changed upstream; update build_meta_skill")
        combined = combined.rstrip("\n") + "\n\n" + render_full_kit(inv, target, skill_area)
        (meta_dir / "SKILL.md").write_text(combined, encoding="utf-8")
        (meta_dir / "catalog.md").write_text(render_catalog(inv, target), encoding="utf-8")
        stats[target] = {k: len(v) for k, v in inv.items()}

    manifest["sources"]["addy"]["transforms"].append(
        "using-agent-skills: appended a generated 'Full kit' routing section (by language, overlaps, ECC areas, "
        "agents, commands) plus catalog.md, per target; the stock SKILL.md is kept in vendor/addy/standalone "
        "for installs without ECC")
    manifest["sources"]["addy"]["meta_skill"] = {"rel": META_REL, "catalog": stats}


# ------------------------------------------------------------------ main

def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--addy-ref", default=ADDY_REF, help="agent-skills commit/branch/tag (default: pinned)")
    ap.add_argument("--ecc-ref", default=ECC_REF, help="ECC commit/branch/tag (default: pinned)")
    ap.add_argument("--out", type=Path, default=here.parent / "vendor", help="output directory (replaced)")
    ap.add_argument("--keep-work", action="store_true", help="keep the temporary work directory")
    args = ap.parse_args()

    for tool in ("git", "node", "npm"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH")

    work = Path(tempfile.mkdtemp(prefix="agent-kit-build-"))
    staged = work / "vendor"
    try:
        manifest = {"schema": 1, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "sources": {}}

        log(f"fetching agent-skills @ {args.addy_ref}")
        addy = {"name": "agent-skills (skills.addy.ie)", "license": "MIT",
                **fetch(ADDY_REPO, args.addy_ref, work / "src-addy")}
        build_addy(work / "src-addy", staged / "addy", addy)
        shutil.copy2(work / "src-addy" / "LICENSE", staged / "addy" / "LICENSE")
        manifest["sources"]["addy"] = addy

        log(f"fetching ECC @ {args.ecc_ref}")
        ecc = {"name": "ECC (Everything Claude Code) by Affaan Mustafa", "license": "MIT",
               **fetch(ECC_REPO, args.ecc_ref, work / "src-ecc")}
        build_ecc(work / "src-ecc", work, staged / "ecc", ecc)
        shutil.copy2(work / "src-ecc" / "LICENSE", staged / "ecc" / "LICENSE")
        manifest["sources"]["ecc"] = ecc

        log("generating the combined using-agent-skills catalog")
        build_meta_skill(staged, work / "src-ecc", manifest)

        manifest["tree_sha256"] = {
            f"{s}/{t}": tree_digest(staged / s / t) for s in ("addy", "ecc") for t in ("claude", "opencode")
        }
        (staged / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")

        if args.out.exists():
            shutil.rmtree(args.out)
        shutil.copytree(staged, args.out)
        log(f"wrote {args.out}")
        for key, src in manifest["sources"].items():
            log(f"  {key}: {src['commit'][:12]} ({src['commit_date']}) v{src.get('version')}")
    finally:
        if args.keep_work:
            log(f"work dir kept at {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
