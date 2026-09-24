#!/usr/bin/env python3
"""End-to-end tests for install.py, run against throwaway HOME directories.

    python tools/test_install.py            # all tests
    python tools/test_install.py -k hooks   # a subset

Nothing outside the temporary directories is touched.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
INSTALL = KIT / "install.py"
MANIFEST = json.loads((KIT / "vendor" / "MANIFEST.json").read_text())
ECC = MANIFEST["sources"]["ecc"]
ECC_MARKER, ADDY_MARKER = ECC["hook_marker"], MANIFEST["sources"]["addy"]["hook_marker"]


def snapshot(root: Path) -> dict:
    """rel path -> sha256 for every file, ignoring the kit's own bookkeeping."""
    out = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and ".agent-kit-backups" not in rel and not rel.endswith(".agent-kit-state.json"):
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def hook_groups(settings: dict, marker: str) -> int:
    return sum(marker in json.dumps(g) for groups in settings.get("hooks", {}).values() for g in groups)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="agent-kit-test-"))
        self.claude = self.home / ".claude"
        self.oc = self.home / ".config" / "opencode"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_install(self, *args, expect=0, env_extra=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "OPENCODE_CONFIG_DIR")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), **(env_extra or {}))
        r = subprocess.run([sys.executable, str(INSTALL), *args], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, expect, f"exit {r.returncode}\n{r.stdout}\n{r.stderr}")
        return r.stdout + r.stderr

    def settings(self):
        return json.loads((self.claude / "settings.json").read_text())

    def opencode(self):
        return json.loads((self.oc / "opencode.json").read_text())

    # ------------------------------------------------------------------
    def test_fresh_install_layout(self):
        self.run_install()
        c = MANIFEST["sources"]
        for d in ("agents", "commands"):
            n = ECC["counts"]["claude"][d] + c["addy"]["counts"]["claude"][d]
            self.assertEqual(len(list((self.claude / d).glob("*.md"))), n, d)
        self.assertEqual(len([p for p in (self.claude / "skills").iterdir() if p.is_dir()]),
                         ECC["counts"]["claude"]["skills"] + c["addy"]["counts"]["claude"]["skills"])
        # renamed collisions: both personas exist, ECC keeps the plain names
        self.assertTrue((self.claude / "agents" / "code-reviewer.md").exists())
        self.assertIn("name: addy-code-reviewer", (self.claude / "agents" / "addy-code-reviewer.md").read_text())
        self.assertTrue((self.claude / "commands" / "addy-plan.md").exists())
        # addy's skills link to ../../references/, which must sit beside skills/
        self.assertTrue((self.claude / "references" / "security-checklist.md").exists())
        self.assertTrue((self.oc / "references" / "security-checklist.md").exists())
        # install-state records from the build machine are never shipped
        self.assertFalse((self.claude / "ecc" / "state.db").exists())
        self.assertFalse((self.oc / "ecc-install-state.json").exists())
        self.assertEqual(hook_groups(self.settings(), ECC_MARKER), ECC["counts"]["claude_hook_commands"])
        self.assertEqual(hook_groups(self.settings(), ADDY_MARKER), 0, "addy hooks must be opt-in")
        self.assertEqual(len(self.opencode()["agent"]), ECC["counts"]["opencode_inline_agents"])

    def test_web_performance_auditor_is_excluded(self):
        self.run_install()
        for root in (self.claude, self.oc):
            self.assertFalse((root / "agents" / "web-performance-auditor.md").exists())
            self.assertFalse((root / "commands" / "webperf.md").exists())
            # shared dependencies stay: /review and /test still rely on them
            self.assertTrue((root / "skills" / "performance-optimization" / "SKILL.md").exists())
            self.assertTrue((root / "skills" / "browser-testing-with-devtools" / "SKILL.md").exists())
            self.assertTrue((root / "references" / "performance-checklist.md").exists())

    def installed_names(self, root: Path, target: str) -> dict:
        names = {"skills": {p.parent.name for p in (root / "skills").glob("*/SKILL.md")},
                 "agents": {p.stem for p in (root / "agents").glob("*.md")} if (root / "agents").is_dir() else set(),
                 "commands": {p.stem for p in (root / "commands").glob("*.md")}}
        if target == "opencode":
            oc = json.loads((root / "opencode.json").read_text())
            names["agents"] |= set(oc.get("agent", {}))
            names["commands"] |= set(oc.get("command", {}))
        return names

    def test_meta_skill_routes_only_to_installed_items(self):
        self.run_install()
        for target, root in (("claude", self.claude), ("opencode", self.oc)):
            meta = root / "skills" / "using-agent-skills"
            installed = self.installed_names(root, target)
            catalog = (meta / "catalog.md").read_text()
            sections = dict(re.findall(r"^## (Skills|Agents|Commands) \(\d+\)\n(.*?)(?=^## |\Z)", catalog, re.M | re.S))
            for kind, title in (("skills", "Skills"), ("agents", "Agents"), ("commands", "Commands")):
                listed = {n.lstrip("/") for n in re.findall(r"^- `([^`]+)`", sections[title], re.M)}
                self.assertEqual(listed, installed[kind], f"{target} catalog {kind} must match what is installed")
            generated = (meta / "SKILL.md").read_text().split("BEGIN agent-kit generated section", 1)[1]
            everything = set().union(*installed.values())
            for name in re.findall(r"`/?([a-z0-9][a-z0-9-]*)`", generated):
                if name not in ("subagent_type", "catalog", "agent-name"):
                    self.assertIn(name, everything, f"{target} SKILL.md routes to missing '{name}'")
            for gone in ("web-performance-auditor", "kotlin-reviewer", "flutter-reviewer", "gradle-build", "webperf"):
                self.assertNotIn(gone, generated + catalog)

    def test_meta_skill_follows_ecc_presence(self):
        meta = self.claude / "skills" / "using-agent-skills"
        stock = (KIT / "vendor" / "addy" / "standalone" / "claude" / "skills" / "using-agent-skills" / "SKILL.md").read_text()
        self.run_install("--source", "addy")
        self.assertEqual((meta / "SKILL.md").read_text(), stock, "without ECC the stock meta-skill is installed")
        self.assertFalse((meta / "catalog.md").exists())
        self.run_install("--source", "ecc")
        self.assertIn("Full kit: agent-skills + ECC", (meta / "SKILL.md").read_text())
        self.assertTrue((meta / "catalog.md").exists())
        self.run_install("--uninstall", "--source", "ecc")
        self.assertEqual((meta / "SKILL.md").read_text(), stock)
        self.assertFalse((meta / "catalog.md").exists())
        self.assertTrue((self.claude / "skills" / "spec-driven-development").exists(), "addy stays installed")
        self.run_install("--uninstall")
        self.assertEqual(snapshot(self.home), {})

    def test_zip_is_current(self):
        sys.path.insert(0, str(KIT / "tools"))
        import build_zip
        committed = KIT.parent / "agent-kit.zip"
        self.assertTrue(committed.exists(), "agent-kit.zip missing; run tools/build_zip.py")
        fresh = self.home / "fresh.zip"
        build_zip.build(fresh)
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(digest(committed), digest(fresh),
                         "agent-kit.zip is stale; run tools/build_zip.py and commit it")

    def test_second_run_changes_nothing(self):
        self.run_install()
        before = snapshot(self.home)
        out = self.run_install()
        self.assertEqual(snapshot(self.home), before)
        self.assertNotIn("backed up", out)
        self.assertFalse((self.claude / ".agent-kit-backups").exists())

    def test_dry_run_writes_nothing(self):
        (self.claude).mkdir(parents=True)
        (self.claude / "settings.json").write_text('{"model": "opus"}\n')
        before = snapshot(self.home)
        out = self.run_install("--dry-run")
        self.assertIn("dry run", out)
        self.assertEqual(snapshot(self.home), before)
        self.assertFalse((self.claude / ".agent-kit-state.json").exists())

    def test_hook_root_points_at_the_install(self):
        self.run_install()
        cmds = [h["command"] for gs in self.settings()["hooks"].values() for g in gs for h in g["hooks"]]
        roots = {base64.b64decode(m).decode() for c in cmds for m in re.findall(r"Buffer\.from\('([^']+)'", c)}
        self.assertEqual(roots, {str(self.claude.absolute())})
        self.assertNotIn(ECC["root_placeholder"], json.dumps(self.settings()))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_installed_hook_actually_runs(self):
        self.run_install()
        cmd = self.settings()["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        project = self.home / "project"
        project.mkdir()
        payload = {"session_id": "t", "hook_event_name": "SessionStart", "source": "startup", "cwd": str(project)}
        r = subprocess.run(["sh", "-c", cmd], input=json.dumps(payload), capture_output=True, text=True,
                           cwd=project, env={**os.environ, "HOME": str(self.home)}, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"hookEventName":"SessionStart"', r.stdout)

    def test_opencode_instructions_resolve(self):
        self.run_install()
        for entry in self.opencode()["instructions"]:
            if os.path.isabs(entry):
                self.assertTrue(Path(entry).is_file(), entry)
        self.assertNotIn("skills", self.opencode(), "dangling skills.paths must not be installed")
        # the five skills ECC's own opencode.json lists but its resolver skipped
        for skill in ("coding-standards", "frontend-patterns", "frontend-slides", "backend-patterns", "api-design"):
            self.assertTrue((self.oc / "skills" / skill / "SKILL.md").is_file(), skill)

    def test_merges_existing_user_config(self):
        self.claude.mkdir(parents=True)
        (self.claude / "agents").mkdir()
        (self.claude / "agents" / "code-reviewer.md").write_text("---\nname: code-reviewer\n---\nmine\n")
        (self.claude / "CLAUDE.md").write_text("my global instructions\n")
        user_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        (self.claude / "settings.json").write_text(json.dumps({
            "model": "opus", "includeCoAuthoredBy": True,
            "permissions": {"allow": ["Bash(ls:*)"]}, "hooks": {"PreToolUse": [user_hook]}}))
        self.oc.mkdir(parents=True)
        (self.oc / "opencode.json").write_text(json.dumps({
            "model": "anthropic/claude-x", "agent": {"build": {"description": "MY build"}},
            "instructions": ["~/my-rules.md"], "plugin": ["my-plugin"]}))
        (self.oc / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}\n')

        out = self.run_install()
        s = self.settings()
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["permissions"], {"allow": ["Bash(ls:*)"]})
        self.assertIs(s["includeCoAuthoredBy"], True, "user's value must win")
        self.assertEqual(s["hooks"]["PreToolUse"][0], user_hook, "user hook stays first")
        self.assertEqual((self.claude / "CLAUDE.md").read_text(), "my global instructions\n")
        # a pre-existing file that collides is replaced, but backed up
        mine = "---\nname: code-reviewer\n---\nmine\n"
        self.assertEqual((self.claude / "agents" / "code-reviewer.md").read_bytes(),
                         (KIT / "vendor" / "ecc" / "claude" / "agents" / "code-reviewer.md").read_bytes())
        backups = list((self.claude / ".agent-kit-backups").rglob("code-reviewer.md"))
        self.assertEqual([b.read_text() for b in backups], [mine])

        o = self.opencode()
        self.assertEqual(o["model"], "anthropic/claude-x")
        self.assertEqual(o["agent"]["build"], {"description": "MY build"}, "user's agent must win")
        self.assertIn("planner", o["agent"])
        self.assertEqual(o["instructions"][0], "~/my-rules.md")
        self.assertEqual(o["plugin"][0], "my-plugin")
        self.assertEqual(json.loads((self.oc / "package.json").read_text()), {"dependencies": {"left-pad": "1.0.0"}})
        self.assertFalse((self.oc / "package-lock.json").exists())
        self.assertIn("kept your agent.build", out)

    def test_uninstall_restores_original_state(self):
        self.claude.mkdir(parents=True)
        (self.claude / "agents").mkdir()
        (self.claude / "agents" / "code-reviewer.md").write_text("mine\n")
        (self.claude / "settings.json").write_text(json.dumps(
            {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo bye"}]}]}}))
        self.oc.mkdir(parents=True)
        (self.oc / "opencode.json").write_text(json.dumps({"instructions": ["~/mine.md"]}))
        original = snapshot(self.home)
        original_configs = (json.loads((self.claude / "settings.json").read_text()),
                            json.loads((self.oc / "opencode.json").read_text()))

        self.run_install("--addy-hooks")
        self.run_install("--uninstall")
        after = snapshot(self.home)
        for cfg in (".claude/settings.json", ".config/opencode/opencode.json"):
            original.pop(cfg), after.pop(cfg)
        self.assertEqual(after, original, "every file must be removed or restored")
        self.assertEqual((json.loads((self.claude / "settings.json").read_text()),
                          json.loads((self.oc / "opencode.json").read_text())), original_configs)

    def test_uninstall_on_clean_home_leaves_nothing(self):
        self.run_install()
        self.run_install("--uninstall")
        self.assertEqual(snapshot(self.home), {})

    def test_edited_files_are_kept(self):
        self.run_install()
        mine = self.claude / "agents" / "architect.md"
        mine.write_text("my customised architect\n")
        out = self.run_install()
        self.assertEqual(mine.read_text(), "my customised architect\n")
        self.assertIn("kept 1 file(s) you edited", out)
        self.run_install("--uninstall")
        self.assertEqual(mine.read_text(), "my customised architect\n", "uninstall must not delete edits")
        self.run_install()
        self.run_install("--force")
        self.assertNotEqual(mine.read_text(), "my customised architect\n")
        backups = [p for p in (self.claude / ".agent-kit-backups").rglob("architect.md")]
        self.assertTrue(any(p.read_text() == "my customised architect\n" for p in backups))

    def test_no_hooks_converges_and_is_remembered(self):
        self.run_install()
        self.run_install("--no-hooks")
        self.assertEqual(hook_groups(self.settings(), ECC_MARKER), 0)
        for target, root in (("claude", self.claude), ("opencode", self.oc)):
            for rel in ECC["hook_runtime_paths"][target]:
                self.assertFalse((root / rel).exists(), f"{target}: {rel}")
        self.assertNotIn("plugin", self.opencode())
        self.assertFalse((self.oc / "plugins").exists())
        self.run_install()                                   # plain re-run keeps the choice
        self.assertEqual(hook_groups(self.settings(), ECC_MARKER), 0)
        self.run_install("--hooks")
        self.assertEqual(hook_groups(self.settings(), ECC_MARKER), ECC["counts"]["claude_hook_commands"])
        self.assertTrue((self.oc / "plugins" / "ecc-hooks.ts").is_file())

    def test_opencode_loads_ecc_hooks_plugin_once(self):
        # opencode auto-loads every plugins/*.{ts,js} as a separate module; ECC's
        # upstream output shipped a re-export too, so its hooks fired twice.
        self.run_install()
        modules = sorted(p.name for p in (self.oc / "plugins").iterdir() if p.suffix in (".ts", ".js"))
        self.assertEqual(modules, ["ecc-hooks.ts"])
        self.assertNotIn("./plugins", self.opencode().get("plugin", []))
        self.assertIn('from "./plugins/ecc-hooks.ts"', (self.oc / "index.ts").read_text())

    def test_addy_hooks_opt_in(self):
        self.run_install("--addy-hooks")
        self.assertEqual(hook_groups(self.settings(), ADDY_MARKER), 5)
        cmd = json.dumps(self.settings()["hooks"])
        script = (self.claude / "hooks" / "addy" / "simplify-ignore.sh").absolute().as_posix()
        self.assertIn(script, cmd)
        self.assertTrue(Path(script).is_file())
        self.run_install("--no-addy-hooks")
        self.assertEqual(hook_groups(self.settings(), ADDY_MARKER), 0)
        self.assertFalse((self.claude / "hooks" / "addy").exists())
        self.assertEqual(hook_groups(self.settings(), ECC_MARKER), ECC["counts"]["claude_hook_commands"])

    def test_source_selection_is_independent(self):
        self.run_install("--source", "addy")
        self.assertTrue((self.claude / "skills" / "spec-driven-development").exists())
        self.assertFalse((self.claude / "agents" / "architect.md").exists())
        self.assertFalse((self.claude / "settings.json").exists(), "addy alone must not touch settings")
        self.run_install("--source", "ecc")
        self.run_install("--uninstall", "--source", "addy")
        self.assertFalse((self.claude / "skills" / "spec-driven-development").exists())
        self.assertTrue((self.claude / "agents" / "architect.md").exists(), "ECC must survive addy's uninstall")
        self.assertEqual(hook_groups(self.settings(), ECC_MARKER), ECC["counts"]["claude_hook_commands"])

    def test_target_selection(self):
        self.run_install("--target", "opencode")
        self.assertFalse(self.claude.exists())
        self.assertTrue((self.oc / "opencode.json").exists())

    def test_invalid_json_aborts_before_any_change(self):
        self.claude.mkdir(parents=True)
        (self.claude / "settings.json").write_text("{ not json")
        before = snapshot(self.home)
        out = self.run_install("--target", "claude", expect=1)
        self.assertIn("not valid JSON", out)
        self.assertEqual(snapshot(self.home), before)

    def test_respects_claude_config_dir(self):
        custom = self.home / "custom-claude"
        self.run_install("--target", "claude", env_extra={"CLAUDE_CONFIG_DIR": str(custom)})
        self.assertTrue((custom / "settings.json").exists())
        self.assertFalse(self.claude.exists())

    def test_existing_opencode_jsonc_is_untouched(self):
        self.oc.mkdir(parents=True)
        jsonc = self.oc / "opencode.jsonc"
        jsonc.write_text('{\n  // my comment\n  "model": "x"\n}\n')
        out = self.run_install("--target", "opencode")
        self.assertEqual(jsonc.read_text(), '{\n  // my comment\n  "model": "x"\n}\n')
        self.assertTrue((self.oc / "opencode.json").exists())
        self.assertIn("opencode.jsonc", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
