#!/usr/bin/env python3
"""Regenerate agent-kit/vendor/ from pinned upstream commits.

This is the maintainer tool. End users never need it: they run install.py,
which only copies the already-built vendor/ trees. Run this when you want to
pull newer upstream content, then run tools/build_zip.py and commit the
resulting vendor/ changes together with agent-kit.zip.

Requires: git, node (>=18) and npm on PATH, plus network access to GitHub and
the npm registry.

It produces two bundles:

- all_in_one_generic_agents (from GENERIC_REPO). That project ships its own
  manifest-driven Node installer; instead of re-implementing its rules, this
  script runs it into throwaway HOME directories (one per target) and vendors
  the resulting trees, with these documented changes (see vendor/MANIFEST.json):
  install-state records dropped; settings.json / opencode.json split out so
  install.py can merge them; the base64 install root in hook commands replaced
  by a placeholder; project-relative opencode paths fixed; skills its resolver
  skipped for opencode added; the double-loaded opencode plugin fixed; the
  curated GENERIC_EXCLUDE list removed; its self-named items renamed.

- SDLC_agents (from SDLC_REPO), plain files copied directly. Two names collide
  with the generic bundle (agent "code-reviewer", command "plan"); the generic
  bundle keeps the plain names because dozens of its files reference them, and
  the SDLC copies become sdlc-code-reviewer / sdlc-plan with their references
  rewritten.
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
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SDLC_REPO = "https://github.com/addyosmani/agent-skills.git"
GENERIC_REPO = "https://github.com/affaan-m/ECC.git"

# Pinned upstream commits. Bump these to refresh, then re-run this script.
SDLC_REF = "bcab6a1b8503100e8618c3b4e32cc78de43de769"
GENERIC_REF = "bf70150eb2df8070024e5bdf08e4aa08959e2735"

GENERIC_ROOT_PLACEHOLDER = "__AGENT_KIT_GENERIC_ROOT_B64__"
SDLC_HOOKS_PLACEHOLDER = "__AGENT_KIT_SDLC_HOOKS_DIR__"
# Every generic-bundle hook command embeds its root resolver (hooks split across
# plugin-hook-bootstrap.js and lifecycle-hook-bootstrap.js, but all resolve the
# root the same way). install.py uses this to recognise, and replace on
# reinstall, the hooks this bundle owns.
GENERIC_HOOK_MARKER = "resolve-ecc-root"
SDLC_HOOK_MARKER = "/hooks/SDLC_agents/"

SDLC = "SDLC_agents"
GENERIC = "all_in_one_generic_agents"
ARCHIFY = "archify"
# archify is taken unchanged from this repository's own release zip (not downloaded).
ARCHIFY_ZIP = Path(__file__).resolve().parents[2] / "archify-skill-v2.16.0.zip"
# Router variants: which bundles each generated using-agent-skills copy describes.
# "full" (all three) lives in the SDLC_agents tree itself; the others under
# vendor/SDLC_agents/meta/<variant>/<target>/ for partial installs.
META_VARIANTS = {"full": (SDLC, GENERIC, ARCHIFY), "generic": (SDLC, GENERIC),
                 "archify": (SDLC, ARCHIFY), "stock": (SDLC,)}

GENERIC_STATE_FILES = {"ecc/install-state.json", "ecc/state.db", "ecc-install-state.json"}

# The upstream project's self-named items, renamed so the installed kit does not
# carry its name. Nothing executable refers to them (the only code hits are
# comments in its installer's link-rewriter, which never runs here), so only
# text references are rewritten. Script internals keep their names (env vars
# such as ECC_HOOK_PROFILE, resolve-ecc-root.js, ecc-hooks.ts) because the
# hooks depend on them.
GENERIC_RENAMES = {
    "skills/ecc-guide": "skills/generic-agents-guide",
    "skills/ecc-recipes": "skills/generic-agents-recipes",
    "skills/ecc-tools-cost-audit": "skills/generic-agents-tools-cost-audit",
    "commands/ecc-guide.md": "commands/generic-agents-guide.md",
    "rules/ecc": "rules/generic-agents",
    "skills/github-ops/references/ecc-release-checklist.md": "skills/github-ops/references/generic-agents-release-checklist.md",
}
GENERIC_TOKEN_REWRITES = [  # text references, longest first
    ("ecc-release-checklist", "generic-agents-release-checklist"),
    ("ecc-tools-cost-audit", "generic-agents-tools-cost-audit"),
    ("configure-ecc", "configure-generic-agents"),
    ("ecc-recipes", "generic-agents-recipes"),
    ("ecc-guide", "generic-agents-guide"),
    ("rules/ecc", "rules/generic-agents"),
]
TEXT_SUFFIXES = (".md", ".json", ".txt", ".yaml", ".yml", ".toml")

# Text scrub (on request): the generic bundle's prose names its project and its
# author. Those words are rewritten or removed in .md/.txt files and in the
# descriptions inside opencode.json. Functional references are left alone:
# URLs and repo names (affaan-m/ECC, the separate ECC-Tools repo), issue ids
# (ECC-031), env vars (ECC_*), CLI/plugin identifiers, and anything in backticks.
GENERIC_PROSE_WORD = re.compile(r"(?<![/\w`\[.@-])ECC(?=[\s'.,;:)!?*\"]|-[a-z]|$)", re.M)
GENERIC_PROSE_REPLACEMENTS = [("non-ECC", "non-" + "all_in_one_generic_agents"),
                              ("Everything Claude Code", "all_in_one_generic_agents"),
                              ("Addy's *Loop Engineering*", "*Loop Engineering*")]
CREDIT_LINE = re.compile(r"^.*x\.com/affaanmustafa.*\n", re.M)     # links to the author's guides
PROMO_BLOCK_START = "If you haven't read the previous guides, start here:"
PROMO_BLOCK_LINES = re.compile(r"^(>.*|\s*|go do that.*|- \[github\.com/affaan-m/[\w-]+\]\(https://github\.com/affaan-m/[\w-]+\))$")
# after removal, list/table lines pointing at the removed reinstallers are dropped
REMOVED_ITEM_REFERENCE = re.compile(r"`/?(configure-generic-agents|auto-update)`|/auto-update\b")

# all_in_one_generic_agents slimmed to SDLC + Python, Java/JVM, JS/TS web, Go, Rust, C++ and .NET.
# The upstream installer's components cannot express this (every lang:*/framework:* component
# maps to the single framework-language module), so it is done per file. Every
# name below was confirmed with the user; nothing is matched by pattern.
GENERIC_EXCLUDE = {
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
    "reinstallers of the original project (running them would undo this kit)": {
        "skills": ["configure-ecc"], "commands": ["auto-update"],
    },
}

# name collisions with all_in_one_generic_agents -> the SDLC_agents copy is renamed
SDLC_RENAMES = {
    "agents": {"code-reviewer": "sdlc-code-reviewer"},
    "commands": {"plan": "sdlc-plan"},
}
# Left out on request. Only files that exist solely for this persona are listed:
# the skills it uses (performance-optimization, browser-testing-with-devtools)
# and references/performance-checklist.md are shared with /review, /test and
# several other skills, so they stay.
SDLC_EXCLUDE = {
    "agents/web-performance-auditor.md": "web-performance-auditor",
    "commands/webperf.md": "/webperf",
}
SDLC_HOOK_SCRIPTS = ["sdd-cache-pre.sh", "sdd-cache-post.sh", "simplify-ignore.sh"]

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
# The upstream module grouping, used to list the remaining generic skills by area.
GENERIC_AREAS = [
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
# Where both bundles cover the same step: the SDLC_agents phase skill stays the
# process, the generic item adds depth.
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
SDLC_HOOK_DOCS = ["SDD-CACHE.md", "SIMPLIFY-IGNORE.md"]


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


# ------------------------------------------------ all_in_one_generic_agents

def upstream_install(src: Path, target: str, home: Path, hooks: bool) -> Path:
    home.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in ("OPENCODE_CONFIG_DIR", "XDG_CONFIG_HOME")}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    flag = "--enable-hooks" if hooks else "--no-hooks"
    out = run(["node", "scripts/install-apply.js", "--target", target, "--profile", "full", flag, "--json"],
              cwd=src, env=env)
    result = json.loads(out)["result"]
    root = Path(result["targetRoot"] if "targetRoot" in result else result["plan"]["targetRoot"])
    if not root.is_dir():
        sys.exit(f"upstream installer reported target root {root} but it does not exist")
    return root


def apply_generic_exclusions(out: Path, oc: dict) -> tuple[dict, dict]:
    """Delete GENERIC_EXCLUDE entries from both trees and from opencode.json's inline config."""
    trees = {"claude": out / "claude", "opencode": out / "opencode"}
    layout = {  # kind -> (claude path, opencode path); opencode has no agents/ or rules/ dirs
        "skills": ("skills/{}", "skills/{}"),
        "agents": ("agents/{}.md", "prompts/agents/{}.txt"),
        "commands": ("commands/{}.md", "commands/{}.md"),
        "rules": ("rules/ecc/{}", None),
    }
    removed = {"files": 0, "names": {}}
    for reason, kinds in GENERIC_EXCLUDE.items():
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
                    sys.exit(f"GENERIC_EXCLUDE: {kind[:-1]} '{name}' ({reason}) does not exist; typo or upstream rename")
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

    # Report (don't fail on) kept files that still mention a removed name: the
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


def apply_generic_renames(out: Path, config: Path) -> tuple[list, int]:
    """Rename GENERIC_RENAMES in both trees and rewrite text references to them."""
    moved = []
    for tree in (out / "claude", out / "opencode"):
        for old, new in GENERIC_RENAMES.items():
            src, dst = tree / old, tree / new
            if src.exists():
                if dst.exists():
                    sys.exit(f"cannot rename {tree.name}/{old}: {new} already exists")
                src.rename(dst)
                moved.append(f"{tree.name}/{old}")
    if len(moved) < len(GENERIC_RENAMES):
        sys.exit(f"GENERIC_RENAMES: only found {moved}; an upstream item was renamed or removed")

    patterns = [(re.compile(rf"(?<![\w-]){re.escape(old)}(?![\w-])"), new) for old, new in GENERIC_TOKEN_REWRITES]
    rewritten = 0
    for root in (out / "claude", out / "opencode", config):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="surrogateescape")
                new_text = text
                for pattern, new in patterns:
                    new_text = pattern.sub(new, new_text)
                if new_text != text:
                    path.write_text(new_text, encoding="utf-8", errors="surrogateescape")
                    rewritten += 1
                for pattern, _ in patterns:
                    if pattern.search(new_text):
                        sys.exit(f"{path} still references {pattern.pattern} after rewriting")
    return moved, rewritten


def scrub_generic_text(out: Path, config: Path) -> dict:
    """Rewrite/remove project and author names in prose (see GENERIC_PROSE_WORD)."""
    stats = {"files": 0, "words": 0, "credit_lines": 0, "promo_blocks": 0, "removed_item_lines": 0}
    for tree in (out / "claude", out / "opencode"):
        for path in sorted(tree.rglob("*")):
            if not (path.is_file() and path.suffix in (".md", ".txt")):
                continue
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            new = text
            if PROMO_BLOCK_START in new:
                head, tail = new.split(PROMO_BLOCK_START, 1)
                if not all(PROMO_BLOCK_LINES.match(line) for line in tail.splitlines()[1:]):
                    sys.exit(f"{path}: promotional block is no longer at the end of the file; re-check the scrub")
                new = re.sub(r"\n---\s*\n\s*$", "\n", head.rstrip() + "\n")
                stats["promo_blocks"] += 1
            new, n = CREDIT_LINE.subn("", new)
            stats["credit_lines"] += n
            kept = []
            for line in new.splitlines(keepends=True):
                if REMOVED_ITEM_REFERENCE.search(line):
                    if not re.match(r"\s*([-*] |\|)", line):
                        sys.exit(f"{path}: prose (not a list/table line) references a removed item: {line.strip()[:120]}")
                    stats["removed_item_lines"] += 1
                    continue
                kept.append(line)
            new = "".join(kept)
            for old, repl in GENERIC_PROSE_REPLACEMENTS:
                new = new.replace(old, repl)
            new, n = GENERIC_PROSE_WORD.subn("all_in_one_generic_agents", new)
            stats["words"] += n
            if new != text:
                path.write_text(new, encoding="utf-8", errors="surrogateescape")
                stats["files"] += 1
            for leftover in ("Everything Claude Code", "affaanmustafa", "Addy"):
                if leftover in new:
                    sys.exit(f"{path}: still mentions {leftover!r} after the scrub")
            if GENERIC_PROSE_WORD.search(new) or REMOVED_ITEM_REFERENCE.search(new):
                sys.exit(f"{path}: scrub left a prose mention behind")

    oc_path = config / "opencode.json"
    oc = json.loads(oc_path.read_text())
    for section in ("agent", "command"):
        for spec in oc.get(section, {}).values():
            if isinstance(spec.get("description"), str):
                d = spec["description"]
                for old, repl in GENERIC_PROSE_REPLACEMENTS:
                    d = d.replace(old, repl)
                spec["description"] = GENERIC_PROSE_WORD.sub("all_in_one_generic_agents", d)
    oc_path.write_text(json.dumps(oc, indent=2) + "\n")
    return stats


def build_generic(src: Path, work: Path, out: Path, meta: dict) -> None:
    log("all_in_one_generic_agents: installing npm dependencies (--ignore-scripts)")
    run(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"], cwd=src)
    log("all_in_one_generic_agents: compiling the opencode plugin payload")
    run(["node", "scripts/build-opencode.js"], cwd=src)
    meta["version"] = (src / "VERSION").read_text().strip()

    roots = {}
    for target in ("claude", "opencode"):
        for hooks in (True, False):
            key = f"{target}-{'hooks' if hooks else 'nohooks'}"
            log(f"all_in_one_generic_agents: running upstream installer --target {target} --profile full {'with' if hooks else 'without'} hooks")
            roots[key] = upstream_install(src, target, work / f"home-{key}", hooks)

    config = out / "config"
    config.mkdir(parents=True)

    # ---- Claude Code
    c_root = roots["claude-hooks"]
    settings = json.loads((c_root / "settings.json").read_text())
    real_b64 = base64.b64encode(str(c_root).encode()).decode()
    commands = [h["command"] for groups in settings.get("hooks", {}).values() for g in groups for h in g["hooks"]]
    if not commands:
        sys.exit("upstream settings.json contains no hooks; installer output changed shape")
    for cmd in commands:
        if GENERIC_HOOK_MARKER not in cmd or real_b64 not in cmd:
            sys.exit(f"upstream hook command lacks the expected marker/root, refusing to vendor:\n{cmd[:300]}")
    settings_text = json.dumps(settings, indent=2).replace(real_b64, GENERIC_ROOT_PLACEHOLDER)
    if str(c_root) in settings_text:
        sys.exit("plain build path still present in settings.json after placeholder substitution")
    (config / "claude-settings.json").write_text(settings_text + "\n")
    copytree(c_root, out / "claude", skip=GENERIC_STATE_FILES | {"settings.json", "ecc"})

    # ---- OpenCode
    o_root = roots["opencode-hooks"]
    oc = json.loads((o_root / "opencode.json").read_text())
    dropped_paths = oc.get("skills", {}).pop("paths", None)
    if "skills" in oc and not oc["skills"]:
        del oc["skills"]
    (config / "opencode.json").write_text(json.dumps(oc, indent=2) + "\n")
    copytree(o_root, out / "opencode", skip=GENERIC_STATE_FILES | {"opencode.json"})

    # opencode auto-loads every plugins/*.ts as its own module, and upstream ships
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

    excluded, dangling = apply_generic_exclusions(out, oc)
    (config / "opencode.json").write_text(json.dumps(oc, indent=2) + "\n")
    added = [s for s in added if (oc_skills / s).exists()]
    renamed, rewritten = apply_generic_renames(out, config)
    scrub = scrub_generic_text(out, config)

    # Any path left holding a build-machine absolute path is a bug.
    for tree in (out / "claude", out / "opencode", config):
        for rel in files_under(tree):
            data = (tree / rel).read_bytes()
            if str(work).encode() in data or base64.b64encode(str(work).encode())[:24] in data:
                sys.exit(f"build path leaked into vendored file {tree.name}/{rel}")

    # Hook-runtime paths, as the upstream installer classifies them: what --enable-hooks
    # installs that --no-hooks does not.
    hook_paths = {}
    for target in ("claude", "opencode"):
        with_h = files_under(roots[f"{target}-hooks"]) - GENERIC_STATE_FILES
        without = files_under(roots[f"{target}-nohooks"]) - GENERIC_STATE_FILES
        hook_paths[target] = sorted(with_h - without)
    # The upstream --no-hooks still ships the opencode hooks plugin (plugins/ is
    # auto-loaded by opencode), so treat it as hook runtime too.
    hook_paths["opencode"] = sorted(set(hook_paths["opencode"]) |
                                    {p for p in files_under(out / "opencode") if p.startswith("plugins/")})

    meta.update(
        profile="full",
        hook_runtime_paths=hook_paths,
        hook_marker=GENERIC_HOOK_MARKER,
        root_placeholder=GENERIC_ROOT_PLACEHOLDER,
        opencode_plugin_entry="./plugins",
        transforms=[
            "dropped install-state records: " + ", ".join(sorted(GENERIC_STATE_FILES)),
            "settings.json and opencode.json moved to vendor/all_in_one_generic_agents/config/ for merging",
            f"base64 install root in {len(commands)} hook commands replaced by {GENERIC_ROOT_PLACEHOLDER}",
            f"opencode.json skills.paths {dropped_paths!r} dropped (project-relative, dangling in a global install)",
            f"added {len(added)} skills to opencode that the upstream resolver skipped transitively",
            "opencode: removed plugins/index.ts and the './plugins' entry (the hooks plugin was "
            "loaded twice, firing every hook twice); root index.ts re-pointed at the single plugin module",
            f"slimmed to SDLC + Python/Java/JS-TS/Go/Rust/C++/.NET: removed "
            f"{sum(len(v) for v in excluded['names'].values())} skills/agents/commands/rule sets "
            f"({excluded['files']} files) across {len(excluded['names'])} categories",
            "renamed self-named items: " + ", ".join(f"{o} -> {n}" for o, n in GENERIC_RENAMES.items())
            + f"; references rewritten in {rewritten} text files",
            f"text scrub: project name rewritten {scrub['words']} times in {scrub['files']} files; "
            f"{scrub['credit_lines']} author-credit lines and {scrub['promo_blocks']} promotional block removed; "
            f"{scrub['removed_item_lines']} list/table lines pointing at removed reinstallers dropped",
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


# ------------------------------------------------------------ SDLC_agents

def rewrite_names(text: str) -> str:
    # word-bounded so e.g. "code-reviewer's" is rewritten but "my-code-reviewer" is not
    text = re.sub(r"(?<![\w-])code-reviewer(?![\w-])", "sdlc-code-reviewer", text)
    text = re.sub(r"(?<![\w/-])/plan(?![\w-])", "/sdlc-plan", text)
    # upstream uses "@addy" as an example owner handle in templates
    text = re.sub(r"@addy\b", "@owner", text)
    # commands name skills plugin-style ("agent-skills:test-driven-development"); installed
    # as plain skills they are just "test-driven-development"
    text = re.sub(r"(?<![\w-])agent-skills:(?=[a-z])", "", text)
    text = re.sub(r"(?<![\w-])agent-skills(?![\w:-])", SDLC, text)
    return text


def build_sdlc(src: Path, out: Path, meta: dict) -> None:
    plugin = json.loads((src / ".claude-plugin" / "plugin.json").read_text())
    meta["version"] = plugin.get("version")

    staging = out / "_staging"
    copytree(src / "skills", staging / "skills")
    copytree(src / "references", staging / "references")
    copytree(src / "agents", staging / "agents")
    # commands/*.toml are Gemini CLI format; the Claude/opencode markdown ones live here
    copytree(src / ".claude" / "commands", staging / "commands")

    for rel in SDLC_EXCLUDE:
        (staging / rel).unlink()
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for rel, name in SDLC_EXCLUDE.items():
                if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
                    sys.exit(f"{path.relative_to(staging)} still references excluded {name} ({rel}); "
                             "update SDLC_EXCLUDE or rewrite that reference")

    renamed = []
    for kind, mapping in SDLC_RENAMES.items():
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

    fm = (staging / "agents" / "sdlc-code-reviewer.md").read_text()
    if not re.search(r"^name:\s*sdlc-code-reviewer\s*$", fm, re.M):
        sys.exit("renamed agent frontmatter was not updated")

    for target in ("claude", "opencode"):
        copytree(staging, out / target)
    shutil.rmtree(staging)

    hooks_dir = out / "claude" / "hooks" / SDLC
    hooks_dir.mkdir(parents=True)
    for name in SDLC_HOOK_SCRIPTS + SDLC_HOOK_DOCS:
        shutil.copy2(src / "hooks" / name, hooks_dir / name)

    cmd = lambda script: f'bash "{SDLC_HOOKS_PLACEHOLDER}/{script}"'
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
        hook_marker=SDLC_HOOK_MARKER,
        hooks_placeholder=SDLC_HOOKS_PLACEHOLDER,
        transforms=[
            "commands taken from .claude/commands/*.md (commands/*.toml are Gemini CLI format)",
            "excluded on request: " + ", ".join(sorted(SDLC_EXCLUDE)) + " (the performance skills and "
            "checklist it uses are shared with /review and /test, so they are kept)",
            *[f"renamed {r} (name collides with all_in_one_generic_agents)" for r in renamed],
            f"rewrote code-reviewer -> sdlc-code-reviewer, /plan -> /sdlc-plan and example @-handles in {len(rewritten)} files",
            "hooks: only the three wireable scripts are shipped (session-start.sh is deliberately "
            "not wired for Claude Code by its author; *-test.sh are upstream tests)",
            "hook wiring is opt-in (install.py --sdlc-hooks), matching upstream's per-project guidance",
        ],
        rewritten_files=rewritten,
        counts={t: {d: len(list((out / t / d).iterdir())) for d in ("agents", "commands", "skills", "references")}
                for t in ("claude", "opencode")},
    )


# ------------------------------------------------------------------ archify

def build_archify(out: Path, meta: dict) -> None:
    """Unpack the repository's own archify release zip, unchanged, as a shared tree."""
    if not ARCHIFY_ZIP.is_file():
        sys.exit(f"{ARCHIFY_ZIP} not found")
    dest = out / "common" / "skills"
    dest.mkdir(parents=True)
    with zipfile.ZipFile(ARCHIFY_ZIP) as zf:
        names = zf.namelist()
        bad = [n for n in names if not n.startswith("archify/") or ".." in Path(n).parts or n.startswith("/")]
        if bad:
            sys.exit(f"unexpected paths in {ARCHIFY_ZIP.name}: {bad[:5]}")
        zf.extractall(dest)
    skill_md = dest / "archify" / "SKILL.md"
    if not re.search(r"^name:\s*archify\s*$", skill_md.read_text(encoding="utf-8"), re.M):
        sys.exit("archify SKILL.md frontmatter name is not 'archify'")
    release = json.loads((dest / "archify" / "skill-release.json").read_text())
    meta.update(
        version=release["version"],
        source_zip=ARCHIFY_ZIP.name,
        source_zip_sha256=hashlib.sha256(ARCHIFY_ZIP.read_bytes()).hexdigest(),
        files=len(files_under(dest)),
        transforms=[f"unpacked unchanged from {ARCHIFY_ZIP.name} in this repository; one shared copy "
                    "(vendor/archify/common) is installed into both tools"],
    )


# ---------------------------------------------------------------- URL policy
#
# On request, nothing in the kit mentions or reaches the open internet: every
# such address becomes "<url>" (Markdown links keep only their text). Kept, by
# decision: localhost/example addresses; XML namespace and JSON-schema
# identifiers (never fetched; required for SVG and validation); Go module
# paths, Kubernetes API groups and container image names (identifiers, not
# links); and two runtime fetches the user chose to keep: Google Fonts in
# archify's diagram HTML and the Mermaid CDN in plan-canvas's browser view.

URL_PLACEHOLDER = "<url>"
SCHEME_URL = re.compile(r"(?:https?|ssh|git)://[^\s<>\"'`)\]\\|]+")
ESCAPED_URL = re.compile(r"(?:https?|ssh):\\/\\/(?:git@)?(?:[A-Za-z0-9-]|\\\.)+")
SCHEMELESS_URL = re.compile(r"(?<![\w@./:-])(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
                            r"\.(?:com|org|io|dev|net|ai|app|sh|co|gg|so|me|tools|design|page)/[A-Za-z0-9_./#?=&%+~-]*", re.I)
SSH_REMOTE = re.compile(r"git@[a-z0-9.-]+\.[a-z]+:[\w./-]+", re.I)
# local, reserved-for-docs, or templated hosts (e.g. http://${host}:${port} for the kit's own local servers)
LOCAL_URL = re.compile(r"^(?:[a-z]+://)?(?:[^/@]*@)?(?:localhost|127\.|0\.0\.0\.0|\[::1\]|[\w.-]*\.(?:test|local|localhost|invalid)\b"
                       r"|(?:[\w-]+\.)*example\.(?:com|org|net)\b|\$\{|\{\{?|<)", re.I)
IDENTIFIER_URL = re.compile(r"www\.w3\.org/(?:2000/svg|1999/xlink|1999/xhtml|2001/XMLSchema)|json-schema\.org"
                            r"|schemastore\.org|opencode\.ai/config\.json|\.schema\.json\b", re.I)
IDENTIFIER_NAMESPACE = re.compile(r"^(?:www\.)?(?:[\w.-]*\.k8s\.io|[\w.-]*\.kubernetes\.io|cert-manager\.io|golang\.org/x/"
                                  r"|ghcr\.io|docker\.io|gcr\.io|quay\.io|[\w-]+\.app/Contents)", re.I)
GO_MODULE_LINE = re.compile(r"Module:|Import:|\bgo (?:get|install)\b|import \(|import \"|packageKeys|require \(")
KEPT_FETCHES = {  # (path suffix, host) pairs the user chose to keep
    ("archify/assets/template.html", "fonts.googleapis.com"), ("archify/assets/template.html", "fonts.gstatic.com"),
    (".html", "fonts.googleapis.com"), (".html", "fonts.gstatic.com"),
    ("plan-canvas/ui.js", "cdn.jsdelivr.net"),
}
URL_EXACT = [  # (path suffix, old, new): parsers whose alternations must stay valid regex syntax
    (".mjs", "^https://github\\\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\\\\.git)?/?$", "^<url>$"),
    (".json", "^https://github\\\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\\\\.git)?/?$", "^<url>$"),
    ("repository-evidence.mjs", r"(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)", r"(?:<url>\/)"),
    ("github-origin.js", r"(?:https:\/\/github\.com\/|ssh:\/\/git@github\.com\/|git@github\.com:)", r"(?:<url>\/)"),
]
MANIFEST_FILES = {"package.json", "plugin.json", "marketplace.json", "skill-release.json"}
MANIFEST_URL_KEYS = {"homepage", "repository", "bugs", "updateManifestUrl", "source", "url"}
URL_DELETE = ["mcp-configs/mcp-servers.json", "skills/continuous-learning-v2/scripts/test_parse_instinct.py"]
CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".map", ".py", ".sh")
URL_SUFFIXES = CODE_SUFFIXES + (".md", ".txt", ".json", ".html", ".yaml", ".yml", ".toml", ".css")


def _host(url: str) -> str:
    return re.sub(r"^[a-z]+://(?:[^/@]*@)?", "", url, flags=re.I).split("/")[0].lower()


def _kept(rel: str, url: str) -> bool:
    if LOCAL_URL.match(url) or IDENTIFIER_URL.search(url):
        return True
    return any(rel.endswith(suffix) and _host(url) == host for suffix, host in KEPT_FETCHES)


def open_urls(rel: str, text: str) -> list:
    """Open-internet addresses in `text` that the URL policy does not keep."""
    found = [m.group(0) for m in SCHEME_URL.finditer(text) if not _kept(rel, m.group(0))]
    found += [m.group(0) for m in ESCAPED_URL.finditer(text)]
    if not rel.endswith(CODE_SUFFIXES):
        found += [m.group(0) for m in SSH_REMOTE.finditer(text)]
        for line in text.splitlines():
            if GO_MODULE_LINE.search(line):
                continue
            found += [m.group(0) for m in SCHEMELESS_URL.finditer(re.sub(r"[a-z]+://\S+", " ", line))
                      if not IDENTIFIER_NAMESPACE.match(m.group(0)) and not LOCAL_URL.match(m.group(0))]
    return found


def _sub_url(rel: str):
    def repl(m):
        url = m.group(0)
        tail = re.search(r"[.,;:!?*]+$", url)
        core, trail = (url[:tail.start()], tail.group(0)) if tail else (url, "")
        return m.group(0) if _kept(rel, core) else URL_PLACEHOLDER + trail
    return repl


def scrub_url_text(rel: str, text: str) -> str:
    for suffix, old, new in URL_EXACT:
        if rel.endswith(suffix):
            text = text.replace(old, new)
    if rel.endswith((".md", ".txt")):
        looks_like_address = lambda t: bool(SCHEME_URL.match(t) or SCHEMELESS_URL.match(t) or re.match(r"^[\w.-]+\.(com|org|io|dev|net)\b", t))
        def link(m):
            label, url = m.group(2), m.group(3)
            if _kept(rel, url):
                return m.group(0)
            return URL_PLACEHOLDER if looks_like_address(label.strip()) or not label.strip() else label
        text = re.sub(r"(!?)\[([^\]\n]*)\]\(\s*((?:https?|ssh|git)://[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)", link, text)
        text = re.sub(r"^[ \t]*\[[^\]\n]+\]:[ \t]*((?:https?|ssh|git)://\S+).*\n",
                      lambda m: m.group(0) if _kept(rel, m.group(1)) else "", text, flags=re.M)
        text = re.sub(r"<((?:https?|ssh|git)://[^>\s]+)>", lambda m: m.group(0) if _kept(rel, m.group(1)) else URL_PLACEHOLDER, text)
    text = SCHEME_URL.sub(_sub_url(rel), text)
    text = ESCAPED_URL.sub(URL_PLACEHOLDER, text)
    if not rel.endswith(CODE_SUFFIXES):
        text = SSH_REMOTE.sub(URL_PLACEHOLDER, text)
        out = []
        for line in text.splitlines(keepends=True):
            if not GO_MODULE_LINE.search(line):
                line = SCHEMELESS_URL.sub(lambda m: m.group(0) if IDENTIFIER_NAMESPACE.match(m.group(0))
                                          or LOCAL_URL.match(m.group(0)) else URL_PLACEHOLDER, line)
            out.append(line)
        text = "".join(out)
    return text


def _drop_manifest_urls(node):
    if isinstance(node, dict):
        return {k: _drop_manifest_urls(v) for k, v in node.items() if k not in MANIFEST_URL_KEYS}
    if isinstance(node, list):
        return [_drop_manifest_urls(v) for v in node]
    return node


def apply_url_policy(staged: Path) -> dict:
    stats = {"files": 0, "deleted": 0, "manifests": 0}
    roots = [d for b in (SDLC, GENERIC, ARCHIFY) for d in sorted((staged / b).iterdir()) if d.is_dir()]
    for root in roots:
        for rel in URL_DELETE:
            if (root / rel).is_file():
                (root / rel).unlink()
                stats["deleted"] += 1
    # archify: drop the instruction that runs the online update checker
    skill_md = staged / ARCHIFY / "common" / "skills" / "archify" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    new = re.sub(r"\n## Update awareness\n.*?(?=\n## )", "\n", text, count=1, flags=re.S)
    if new == text:
        sys.exit("archify SKILL.md no longer has an 'Update awareness' section; re-check the URL policy")
    skill_md.write_text(new, encoding="utf-8")

    exact_hits = {i: 0 for i in range(len(URL_EXACT))}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not path.name.endswith(URL_SUFFIXES):
                continue
            rel = path.relative_to(staged).as_posix()
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            for i, (suffix, old, _) in enumerate(URL_EXACT):
                if rel.endswith(suffix):
                    exact_hits[i] += text.count(old)
            new = text
            if path.name in MANIFEST_FILES:
                data = json.loads(text)
                cleaned = _drop_manifest_urls(data)
                if cleaned != data:
                    new = json.dumps(cleaned, indent=2) + "\n"
                    stats["manifests"] += 1
            new = scrub_url_text(rel, new)
            if new != text:
                path.write_text(new, encoding="utf-8", errors="surrogateescape")
                stats["files"] += 1
    missing = [URL_EXACT[i][1] for i, n in exact_hits.items() if n == 0]
    if missing:
        sys.exit(f"URL_EXACT patterns not found (upstream changed?): {missing}")
    if stats["deleted"] < 2 * len(URL_DELETE):
        sys.exit(f"expected to delete {URL_DELETE} from both tools, deleted {stats['deleted']}")
    return stats


def assert_no_open_urls(staged: Path) -> None:
    offenders = []
    for path in sorted(staged.rglob("*")):
        if path.is_file() and path.name.endswith(URL_SUFFIXES):
            rel = path.relative_to(staged).as_posix()
            for url in open_urls(rel, path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(f"{rel}: {url}")
    if offenders:
        sys.exit("open-internet addresses left in the kit:\n  " + "\n  ".join(offenders[:40]))


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


def inventory(staged: Path, target: str, bundles=(SDLC, GENERIC, ARCHIFY)) -> dict:
    """Every skill/agent/command installed into `target` by `bundles`, with descriptions."""
    inv = {"skills": {}, "agents": {}, "commands": {}}
    for source in bundles:
        root = staged / source / target
        if not root.is_dir():
            root = staged / source / "common"             # bundle shared by every target
        for skill in sorted((root / "skills").iterdir()) if (root / "skills").is_dir() else []:
            if (skill / "SKILL.md").is_file():
                inv["skills"][skill.name] = (source, short(frontmatter_description(skill / "SKILL.md")))
        for kind in ("agents", "commands"):
            for f in sorted((root / kind).glob("*.md")) if (root / kind).is_dir() else []:
                inv[kind][f.stem] = (source, short(frontmatter_description(f)))
    if target == "opencode" and GENERIC in bundles:   # the generic bundle defines these inline in opencode.json
        oc = json.loads((staged / GENERIC / "config" / "opencode.json").read_text())
        for name, spec in oc.get("agent", {}).items():
            mode = " (primary agent)" if spec.get("mode") == "primary" else ""
            inv["agents"].setdefault(name, (GENERIC, short(spec.get("description", "")) + mode))
        for name, spec in oc.get("command", {}).items():
            inv["commands"].setdefault(name, (GENERIC, short(spec.get("description", ""))))
    return inv


def code(names, prefix=""):
    return ", ".join(f"`{prefix}{n}`" for n in names) or "—"


def render_full_kit(inv: dict, target: str, skill_area: dict, bundles=(SDLC, GENERIC, ARCHIFY)) -> str:
    has = lambda kind, n: n in inv[kind]
    generic, archify = GENERIC in bundles, ARCHIFY in bundles and has("skills", "archify")
    delegate = ("use the Agent tool with `subagent_type` set to the agent's name"
                if target == "claude" else "mention it as `@agent-name`, or let the primary agent call it via the task tool")
    others = [b for b in bundles if b != SDLC]
    out = [GEN_BEGIN, "", "## Full kit: " + " + ".join(bundles), "",
           f"This environment has **{SDLC}** (the phase workflow above) installed together with "
           + " and ".join(f"**{b}**" for b in others) + ". Combine them like this:", ""]
    steps = ["**Pick the phase with the flowchart above.** The SDLC_agents phase skills are the default process."]
    if generic:
        steps += ["**Layer in the stack-specific skills** for the language you are touching (table below). They add "
                  "idioms, testing and verification detail to the phase skill; they do not replace it.",
                  f"**Delegate to a specialist agent** for a focused review or build fix: {delegate}."]
    if archify:
        steps += ["**Draw diagrams with the `archify` skill** when the user asks to visualise architecture, "
                  "infrastructure, workflows, API sequences, data flows or state machines, or to convert Mermaid. "
                  "It produces a validated, self-contained HTML diagram and needs Node.js 18 or newer."]
    if generic:
        steps += ["**Suggest slash commands** to the user when one fits. Commands are typed by the user; never claim one ran."]
    steps += ["**Everything installed is in `catalog.md`** next to this file, with one-line descriptions. "
              "Read it when nothing below fits."]
    out += [f"{i}. {step}" for i, step in enumerate(steps, 1)] + [""]

    if generic:
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
                "Follow the SDLC_agents skill as the process and pull in the all_in_one_generic_agents item for depth:", ""]
        for sdlc_skill, generic_items in OVERLAPS:
            present = [f"`{n}` skill" if has("skills", n) else f"`{n}` agent"
                       for n in generic_items if has("skills", n) or has("agents", n)]
            if has("skills", sdlc_skill) and present:
                out.append(f"- `{sdlc_skill}` → also {', '.join(present)}")

        out += ["", "### Other all_in_one_generic_agents skills by area", ""]
        for module, label in GENERIC_AREAS:
            names = [n for n in skill_area.get(module, []) if has("skills", n) and n not in routed]
            if names:
                out.append(f"- **{label}:** {code(names)}")
        out.append("")

    if archify:
        out += ["### Diagrams", "",
                "- `archify` — architecture, workflow, sequence, data-flow and lifecycle/state diagrams as explorable "
                "standalone HTML (PNG/SVG/WebM export). Follow its SKILL.md: write the JSON spec, validate, then deliver.",
                ""]

    if generic:
        sdlc_agents = sorted(n for n, (src, _) in inv["agents"].items() if src == SDLC)
        generic_agents = sorted(n for n, (src, _) in inv["agents"].items() if src == GENERIC and n not in routed)
        out += ["### Agents", "",
                f"- **SDLC_agents personas** (fanned out by `/ship`): {code(sdlc_agents)}",
                f"- **all_in_one_generic_agents, general-purpose:** {code(generic_agents)}",
                "- Language reviewers and build resolvers are in the table above.", ""]

        sdlc_cmds = sorted(n for n, (src, _) in inv["commands"].items() if src == SDLC)
        generic_cmds = sorted(n for n, (src, _) in inv["commands"].items() if src == GENERIC and n not in routed)
        out += ["### Commands", "",
                f"- **SDLC_agents lifecycle:** {code(sdlc_cmds, '/')}",
                f"- **all_in_one_generic_agents:** {code(generic_cmds, '/')}",
                "- Language-specific commands are in the table above.", ""]
    out += [GEN_END, ""]
    return "\n".join(out)


def render_catalog(inv: dict, target: str) -> str:
    label = {b: b for b in (SDLC, GENERIC, ARCHIFY)}
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


def build_meta_skill(staged: Path, generic_src: Path, manifest: dict) -> None:
    modules = json.loads((generic_src / "manifests" / "install-modules.json").read_text())["modules"]
    skill_area = {}
    for m in modules:
        for p in m["paths"]:
            if p.startswith("skills/"):
                p = GENERIC_RENAMES.get(p, p)             # follow our renames of self-named skills
                skill_area.setdefault(m["id"], []).append(p.split("/", 1)[1])

    excluded = {n for kinds in GENERIC_EXCLUDE.values() for names in kinds.values() for n in names}
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
    for target in ("claude", "opencode"):
        tree_dir = staged / SDLC / target / META_REL
        stock = (tree_dir / "SKILL.md").read_text(encoding="utf-8")
        for variant, bundles in META_VARIANTS.items():
            dest = tree_dir if variant == "full" else staged / SDLC / "meta" / variant / target
            dest.mkdir(parents=True, exist_ok=True)
            if variant == "stock":
                (dest / "SKILL.md").write_text(stock, encoding="utf-8")
                continue
            inv = inventory(staged, target, bundles)
            extras = [b for b in bundles if b != SDLC]
            combined = stock.replace(
                "This is the meta-skill that governs how all other skills are discovered and invoked.",
                "This is the meta-skill that governs how all other skills are discovered and invoked, including "
                "the installed " + " and ".join(extras) + " skills, agents and commands.", 1)
            if combined == stock:
                sys.exit("using-agent-skills description changed upstream; update build_meta_skill")
            combined = combined.rstrip("\n") + "\n\n" + render_full_kit(inv, target, skill_area, bundles)
            (dest / "SKILL.md").write_text(combined, encoding="utf-8")
            (dest / "catalog.md").write_text(render_catalog(inv, target), encoding="utf-8")
            stats.setdefault(variant, {})[target] = {k: len(v) for k, v in inv.items()}

    manifest["sources"][SDLC]["transforms"].append(
        "using-agent-skills: appended a generated 'Full kit' routing section plus catalog.md, per target. The full "
        "variant (all bundles) is in the tree; variants for partial installs are in vendor/SDLC_agents/meta/")
    manifest["sources"][SDLC]["meta_skill"] = {"rel": META_REL, "variants": {k: list(v) for k, v in META_VARIANTS.items()},
                                               "catalog": stats}


# ------------------------------------------------------------------ main

def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sdlc-ref", default=SDLC_REF, help="SDLC_agents upstream commit/branch/tag (default: pinned)")
    ap.add_argument("--generic-ref", default=GENERIC_REF, help="all_in_one_generic_agents upstream commit/branch/tag (default: pinned)")
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

        # The shipped manifest records only commit, date and version (no upstream names or URLs).
        def pinned(repo, ref, dest):
            info = fetch(repo, ref, dest)
            info.pop("repo")
            return info

        log(f"fetching {SDLC} @ {args.sdlc_ref}")
        sdlc = pinned(SDLC_REPO, args.sdlc_ref, work / "src-sdlc")
        build_sdlc(work / "src-sdlc", staged / SDLC, sdlc)
        manifest["sources"][SDLC] = sdlc

        log(f"fetching {GENERIC} @ {args.generic_ref}")
        generic = pinned(GENERIC_REPO, args.generic_ref, work / "src-generic")
        build_generic(work / "src-generic", work, staged / GENERIC, generic)
        manifest["sources"][GENERIC] = generic

        log(f"unpacking {ARCHIFY} from {ARCHIFY_ZIP.name}")
        archify = {}
        build_archify(staged / ARCHIFY, archify)
        manifest["sources"][ARCHIFY] = archify

        log("applying the URL policy (no open-internet addresses)")
        url_stats = apply_url_policy(staged)
        manifest["url_policy"] = {
            "placeholder": URL_PLACEHOLDER, **url_stats,
            "kept": ["localhost/example addresses", "XML namespace and JSON-schema identifiers",
                     "Go module paths, Kubernetes API groups, container image names",
                     "Google Fonts in archify diagram HTML", "Mermaid CDN in plan-canvas browser view"],
            "removed": ["archify online update-check instruction", "package/plugin metadata URLs",
                        "mcp-configs/mcp-servers.json", "an upstream test file with sample remotes"],
        }

        log("generating the combined using-agent-skills catalog")
        build_meta_skill(staged, work / "src-generic", manifest)

        assert_no_open_urls(staged)
        manifest["tree_sha256"] = {
            f"{s}/{t}": tree_digest(staged / s / t) for s in (SDLC, GENERIC, ARCHIFY) for t in ("claude", "opencode", "common")
            if (staged / s / t).is_dir()
        }
        (staged / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")

        if args.out.exists():
            shutil.rmtree(args.out)
        shutil.copytree(staged, args.out)
        log(f"wrote {args.out}")
        for key, src in manifest["sources"].items():
            pin = f"{src['commit'][:12]} ({src['commit_date']})" if "commit" in src else src.get("source_zip", "")
            log(f"  {key}: v{src.get('version')} {pin}")
    finally:
        if args.keep_work:
            log(f"work dir kept at {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
