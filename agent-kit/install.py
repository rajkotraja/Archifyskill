#!/usr/bin/env python3
"""Install the vendored SDLC_agents + all_in_one_generic_agents bundles into Claude Code and opencode.

    python install.py                 # everything, into ~/.claude and ~/.config/opencode
    python install.py --dry-run       # show what would change, touch nothing
    python install.py --uninstall     # remove what this script installed, restore backups

Everything is copied from ./vendor (built by tools/build_vendor.py); nothing is
downloaded. Re-running is safe: it updates what it installed before, removes
files it installed that are no longer wanted, and never deletes a file you
have edited. Files it would overwrite that it did not install are backed up
first, and --uninstall puts them back.

Config files are merged, never replaced: your own settings, hooks, agents and
commands always win, and the script records exactly which keys it added so
--uninstall can remove only those.

Python 3.8+, standard library only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

if sys.version_info < (3, 8):
    sys.exit("install.py needs Python 3.8 or newer")

KIT = Path(__file__).resolve().parent
VENDOR = KIT / "vendor"
STATE_FILE = ".agent-kit-state.json"
BACKUP_DIR = ".agent-kit-backups"
SDLC, GENERIC = "SDLC_agents", "all_in_one_generic_agents"
ARCHIFY = "archify"
SOURCES = (SDLC, GENERIC, ARCHIFY)
TARGETS = ("claude", "opencode")
SOURCE_LABEL = {s: s for s in SOURCES}
TARGET_LABEL = {"claude": "Claude Code", "opencode": "opencode"}
# opencode manages <config>/package.json itself; the generic bundle's copy is only installed
# when the user does not already have one.
OPENCODE_PKG_FILES = ("package.json", "package-lock.json")
# The SDLC_agents meta-skill (the router) is generated per combination of bundles
# present in a target, so it only ever describes what is installed. The "full"
# variant sits in the bundle tree itself; the rest under vendor/SDLC_agents/meta/.
META_FILES = ("skills/using-agent-skills/SKILL.md", "skills/using-agent-skills/catalog.md")
# State written by earlier versions of this script used other bundle keys; they
# are migrated on load so a re-run cleans up files from renamed items.
LEGACY_SOURCES = {"addy": SDLC, "ecc": GENERIC}
LEGACY_SDLC_HOOK_MARKER = "/hooks/addy/"
MISSING = object()


# ----------------------------------------------------------------- helpers

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path, what: str):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise SystemExit(
            f"error: {path} is not valid JSON ({e}).\n"
            f"       Fix or move it, then re-run. Nothing was changed for {what}."
        )
    if not isinstance(data, dict):
        raise SystemExit(f"error: {path} must contain a JSON object. Nothing was changed for {what}.")
    return data


def dump_json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def vendor_files(tree: Path):
    """Relative POSIX paths of every file in a vendored tree."""
    return sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file())


def source_tree(source: str, target: str) -> Path:
    """A bundle's files for one tool; bundles identical for both tools ship one "common" tree."""
    tree = VENDOR / source / target
    return tree if tree.is_dir() else VENDOR / source / "common"


def meta_variant(present) -> str:
    generic, archify = GENERIC in present, ARCHIFY in present
    return "full" if generic and archify else "generic" if generic else "archify" if archify else "stock"


def default_claude_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def default_opencode_dir() -> Path:
    # Same resolution order as the upstream installer of the generic bundle.
    if os.environ.get("OPENCODE_CONFIG_DIR"):
        return Path(os.environ["OPENCODE_CONFIG_DIR"]).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg).expanduser() if xdg else Path.home() / ".config") / "opencode"


# ------------------------------------------------ ownership-tracked merging
#
# `owned` records the value this script last wrote for a key. A key is treated
# as ours only while its current value still equals what we wrote; the moment
# the user changes it, it becomes theirs and is left alone.

def merge_scalar(container: dict, key: str, want, owned, changes: list, section: str | None = None):
    cur = container.get(key, MISSING)
    if want is MISSING:                                   # removing
        if owned is not MISSING and cur == owned:
            del container[key]
            changes.append(("removed", section, key, owned))
        return MISSING
    if cur is MISSING:
        container[key] = want
        changes.append(("added", section, key, want))
        return want
    if owned is not MISSING and cur == owned:             # ours from last run
        if cur != want:
            container[key] = want
            changes.append(("updated", section, key, want))
        return want
    if cur != want:
        changes.append(("kept", section, key, cur))
    return MISSING                                        # user's value


def merge_dict(container: dict, key: str, want, owned, changes: list):
    want = {} if want is MISSING else want
    owned = owned if isinstance(owned, dict) else {}
    cur = container.get(key, MISSING)
    if cur is not MISSING and not isinstance(cur, dict):
        changes.append(("skipped", None, key, "your value is not an object"))
        return {}
    section = {} if cur is MISSING else cur
    new_owned = {}
    for sub in list(dict.fromkeys([*want, *owned])):
        v = merge_scalar(section, sub, want.get(sub, MISSING), owned.get(sub, MISSING), changes, key)
        if v is not MISSING:
            new_owned[sub] = v
    if section:
        container[key] = section
    else:
        container.pop(key, None)
    return new_owned


def merge_list(container: dict, key: str, want, owned, changes: list):
    want = [] if want is MISSING else want
    owned = owned if isinstance(owned, list) else []
    cur = container.get(key, MISSING)
    if cur is not MISSING and not isinstance(cur, list):
        changes.append(("skipped", None, key, "your value is not a list"))
        return []
    items = [] if cur is MISSING else list(cur)
    for entry in owned:
        if entry not in want and entry in items:
            items.remove(entry)
            changes.append(("removed", key, entry, MISSING))
    new_owned = []
    for entry in want:
        if entry not in items:
            items.append(entry)
            new_owned.append(entry)
            changes.append(("added", key, entry, MISSING))
        elif entry in owned:
            new_owned.append(entry)
    if items:
        container[key] = items
    else:
        container.pop(key, None)
    return new_owned


def merge_config(config: dict, want: dict, owned: dict, changes: list) -> dict:
    """Merge `want` into `config` one level deep. Pass want={} to remove ours."""
    new_owned = {}
    for key in list(dict.fromkeys([*want, *owned])):
        w, o = want.get(key, MISSING), owned.get(key, MISSING)
        kind = w if w is not MISSING else o
        if isinstance(kind, dict):
            v = merge_dict(config, key, w, o, changes)
            if v:
                new_owned[key] = v
        elif isinstance(kind, list):
            v = merge_list(config, key, w, o, changes)
            if v:
                new_owned[key] = v
        else:
            v = merge_scalar(config, key, w, o, changes)
            if v is not MISSING:
                new_owned[key] = v
    return new_owned


def describe(changes: list, verbose: bool) -> list:
    """Turn change tuples into report lines, grouping routine per-entry changes."""
    lines, grouped = [], {}
    for verb, section, name, value in changes:
        if verb == "note":
            lines.append(name)
        elif verb == "kept":
            where = f"{section}.{name}" if section else name
            lines.append(f"kept your {where} (differs from the bundled value)")
        elif verb == "skipped":
            lines.append(f"skipped '{name}': {value}")
        elif section is None:
            shown = "" if value is MISSING else f" = {json.dumps(value)}"
            lines.append(f"{verb} {name}{shown}")
        elif verbose:
            lines.append(f"{verb} {section} entry {name!r}")
        else:
            grouped[(verb, section)] = grouped.get((verb, section), 0) + 1
    for (verb, section), n in grouped.items():
        lines.append(f"{verb} {n} {section} entr{'y' if n == 1 else 'ies'}")
    return lines


def strip_hooks(settings: dict, marker: str) -> int:
    """Remove every hook group containing `marker`; returns how many.

    Empty containers are left in place so re-adding keeps the user's key order;
    call prune_hooks() afterwards.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event, groups in hooks.items():
        if isinstance(groups, list):
            keep = [g for g in groups if marker not in json.dumps(g)]
            removed += len(groups) - len(keep)
            hooks[event] = keep
    return removed


def prune_hooks(settings: dict):
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in [e for e, g in hooks.items() if g == []]:
        del hooks[event]
    if not hooks:
        del settings["hooks"]


def add_hooks(settings: dict, fragment: dict) -> int:
    hooks = settings.setdefault("hooks", {})
    added = 0
    for event, groups in fragment.items():
        hooks.setdefault(event, []).extend(groups)
        added += len(groups)
    return added


# --------------------------------------------------------------- installer

class Target:
    def __init__(self, name: str, root: Path, manifest: dict, dry_run: bool, verbose: bool, force: bool = False):
        self.name, self.root, self.manifest = name, root, manifest
        self.dry, self.verbose, self.force = dry_run, verbose, force
        self.state_path = root / STATE_FILE
        self.state = read_json(self.state_path, TARGET_LABEL[name]) or {"schema": 1, "sources": {}}
        self.state.setdefault("sources", {})
        for old, new in LEGACY_SOURCES.items():
            if old in self.state["sources"] and new not in self.state["sources"]:
                self.state["sources"][new] = self.state["sources"].pop(old)
        legacy_opts = self.state.get("options", {})
        if "addy_hooks" in legacy_opts:
            legacy_opts.setdefault("sdlc_hooks", legacy_opts.pop("addy_hooks"))
        self.backup_root = root / BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.stats = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0, "backed up": 0, "restored": 0}
        self.notes = []
        self.kept_edited = []

    # -- file primitives ---------------------------------------------------
    def dest(self, rel: str) -> Path:
        return self.root.joinpath(*rel.split("/"))

    def backup(self, path: Path) -> str:
        rel = path.relative_to(self.root)
        target = self.backup_root / rel
        if not self.dry:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        self.stats["backed up"] += 1
        return target.relative_to(self.root).as_posix()

    def say(self, msg: str):
        if self.verbose:
            print(f"    {msg}")

    def install_file(self, src: Path, rel: str, prev: dict | None) -> dict | None:
        dest = self.dest(rel)
        new_hash = sha256(src)
        record = {"sha256": new_hash, "backup": (prev or {}).get("backup")}
        if dest.is_symlink():
            self.notes.append(f"skipped {rel}: it is a symlink (left untouched)")
            return prev
        if dest.is_dir():
            self.notes.append(f"skipped {rel}: a directory exists at that path")
            return prev
        if dest.exists():
            cur = sha256(dest)
            if cur == new_hash:
                self.stats["unchanged"] += 1
                return record
            ours_unmodified = prev is not None and prev.get("sha256") == cur
            if prev is not None and not ours_unmodified and not self.force:
                # We installed it and the user has edited it since: their customisation wins.
                self.kept_edited.append(rel)
                return prev
            if not ours_unmodified:
                saved = self.backup(dest)
                if record["backup"] is None:          # keep the oldest, pre-kit copy for uninstall
                    record["backup"] = saved
                self.say(f"backed up {rel} -> {saved}")
            self.stats["updated"] += 1
            self.say(f"update  {rel}")
        else:
            self.stats["created"] += 1
            self.say(f"create  {rel}")
        if not self.dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return record

    def remove_file(self, rel: str, record: dict) -> bool:
        """Remove a file we installed. Returns False (and keeps it) if the user edited it."""
        dest = self.dest(rel)
        if not dest.exists() or dest.is_symlink():
            return True
        if sha256(dest) != record.get("sha256"):
            if not self.force:
                self.kept_edited.append(rel)
                return False
            self.backup(dest)                             # forced: never lose the user's edit
        backup = record.get("backup")
        backup_path = self.root.joinpath(*backup.split("/")) if backup else None
        if backup_path and backup_path.exists():
            if not self.dry:
                shutil.copy2(backup_path, dest)
            self.stats["restored"] += 1
            self.say(f"restore {rel} from {backup}")
        else:
            if not self.dry:
                dest.unlink()
                self.prune_empty_dirs(dest.parent)
            self.stats["removed"] += 1
            self.say(f"remove  {rel}")
        return True

    def prune_empty_dirs(self, d: Path):
        while d != self.root and self.root in d.parents:
            try:
                d.rmdir()
            except OSError:
                return
            d = d.parent

    def write_config(self, path: Path, before: str | None, data: dict, changes: list, label: str):
        old = json.loads(before) if before and before.strip() else {}
        if old == data:
            return
        after = dump_json(data) if data else ""
        if before is not None and path.exists():
            self.backup(path)
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            if data:
                path.write_text(after, encoding="utf-8")
            elif path.exists():
                path.unlink()
        for line in describe(changes, self.verbose):
            print(f"    {label}: {line}")

    # -- planning ------------------------------------------------------------
    def planned_files(self, source: str, opts: dict) -> dict:
        tree = source_tree(source, self.name)
        files = {rel: tree / rel for rel in vendor_files(tree)}
        if source == GENERIC and not opts["hooks"]:
            for rel in self.manifest["sources"][GENERIC]["hook_runtime_paths"][self.name]:
                files.pop(rel, None)
        if source == SDLC and not opts["sdlc_hooks"]:
            files = {r: p for r, p in files.items() if not r.startswith(f"hooks/{SDLC}/")}
        if source == SDLC:
            for rel in META_FILES:
                files.pop(rel, None)
            files.update(self.sdlc_meta_plan(opts["present"]))
        if source == GENERIC and self.name == "opencode":
            owned = self.state["sources"].get(GENERIC, {}).get("files", {})
            pkg = self.dest("package.json")
            if pkg.exists() and "package.json" not in owned:
                for rel in OPENCODE_PKG_FILES:
                    files.pop(rel, None)
                self.notes.append("kept your existing package.json (opencode adds the plugin "
                                  "dependency the hooks plugin needs by itself)")
        return files

    def sdlc_meta_plan(self, present) -> dict:
        variant = meta_variant(present)
        if variant == "full":
            tree = VENDOR / SDLC / self.name
            return {rel: tree / rel for rel in META_FILES}
        folder = VENDOR / SDLC / "meta" / variant / self.name
        return {rel: folder / Path(rel).name for rel in META_FILES if (folder / Path(rel).name).is_file()}

    def resync_sdlc_meta(self, present):
        """Keep the router in step when other bundles were (un)installed without SDLC_agents."""
        entry = self.state["sources"].get(SDLC)
        if not entry:
            return
        files = entry.setdefault("files", {})
        want = self.sdlc_meta_plan(present)
        for rel, src in want.items():
            rec = self.install_file(src, rel, files.get(rel))
            if rec:
                files[rel] = rec
        for rel in META_FILES:
            if rel not in want and rel in files and self.remove_file(rel, files[rel]):
                del files[rel]

    # -- config --------------------------------------------------------------
    def configure_claude(self, sources, opts):
        path = self.root / "settings.json"
        before = path.read_text(encoding="utf-8-sig") if path.exists() else None
        settings = read_json(path, TARGET_LABEL["claude"])
        changes = []
        hooks_before = json.dumps(settings.get("hooks"), sort_keys=True)
        hook_msgs = []
        root_b64 = base64.b64encode(str(self.root.absolute()).encode()).decode()

        if GENERIC in sources:
            meta = self.manifest["sources"][GENERIC]
            frag_text = (VENDOR / GENERIC / "config" / "claude-settings.json").read_text(encoding="utf-8")
            fragment = json.loads(frag_text.replace(meta["root_placeholder"], root_b64))
            generic_hooks = fragment.pop("hooks", {})     # hooks are handled here, never by merge_config
            removed = strip_hooks(settings, meta["hook_marker"])
            added = add_hooks(settings, generic_hooks) if opts["hooks"] else 0
            hook_msgs.append(f"{GENERIC} hooks: {added} wired" if added else f"{GENERIC} hooks: {removed} removed")
            owned = self.state["sources"].setdefault(GENERIC, {}).get("settings_owned", {})
            self.state["sources"][GENERIC]["settings_owned"] = merge_config(settings, fragment, owned, changes)

        if SDLC in sources:
            meta = self.manifest["sources"][SDLC]
            hooks_dir = (self.root / "hooks" / SDLC).absolute().as_posix()
            frag_text = (VENDOR / SDLC / "config" / "claude-hooks.json").read_text(encoding="utf-8")
            fragment = json.loads(frag_text.replace(meta["hooks_placeholder"], hooks_dir))
            removed = strip_hooks(settings, meta["hook_marker"]) + strip_hooks(settings, LEGACY_SDLC_HOOK_MARKER)
            added = add_hooks(settings, fragment["hooks"]) if opts["sdlc_hooks"] else 0
            hook_msgs.append(f"{SDLC} hooks: {added} wired" if added else f"{SDLC} hooks: {removed} removed")

        prune_hooks(settings)
        if json.dumps(settings.get("hooks"), sort_keys=True) != hooks_before:
            for msg in reversed(hook_msgs):
                if not msg.endswith(": 0 removed"):
                    changes.insert(0, ("note", None, msg, MISSING))
        self.write_config(path, before, settings, changes, "settings.json")
        return settings

    def configure_opencode(self, sources, opts):
        if GENERIC not in sources:
            return
        path = self.root / "opencode.json"
        before = path.read_text(encoding="utf-8-sig") if path.exists() else None
        config = read_json(path, TARGET_LABEL["opencode"])
        want = json.loads((VENDOR / GENERIC / "config" / "opencode.json").read_text(encoding="utf-8"))
        meta = self.manifest["sources"][GENERIC]

        # opencode resolves relative "instructions" against the current *project*,
        # so point entries that ship with the bundle at their installed location.
        tree = VENDOR / GENERIC / "opencode"
        want["instructions"] = [
            self.dest(e).absolute().as_posix() if (tree / e).is_file() else e
            for e in want.get("instructions", [])
        ]
        if not opts["hooks"]:
            want["plugin"] = [p for p in want.get("plugin", []) if p != meta["opencode_plugin_entry"]]
            if not want["plugin"]:
                del want["plugin"]

        changes = []
        owned = self.state["sources"].setdefault(GENERIC, {}).get("opencode_owned", {})
        self.state["sources"][GENERIC]["opencode_owned"] = merge_config(config, want, owned, changes)
        jsonc = self.root / "opencode.jsonc"
        if jsonc.exists() and changes:
            self.notes.append("you have opencode.jsonc; the bundled settings went into opencode.json, which "
                              "opencode merges underneath it, so your opencode.jsonc still wins")
        self.write_config(path, before, config, changes, "opencode.json")

    def unconfigure(self, sources):
        if self.name == "claude":
            path = self.root / "settings.json"
            if not path.exists():
                return
            before = path.read_text(encoding="utf-8-sig")
            settings = read_json(path, TARGET_LABEL["claude"])
            changes = []
            for s in sources:
                marker = self.manifest["sources"][s].get("hook_marker")   # bundles without hooks have none
                n = strip_hooks(settings, marker) if marker else 0
                if n:
                    changes.append(("note", None, f"removed {n} {SOURCE_LABEL[s]} hook groups", MISSING))
                owned = self.state["sources"].get(s, {}).get("settings_owned", {})
                merge_config(settings, {}, owned, changes)
            prune_hooks(settings)
            self.write_config(path, before, settings, changes, "settings.json")
        elif GENERIC in sources:
            path = self.root / "opencode.json"
            if not path.exists():
                return
            before = path.read_text(encoding="utf-8-sig")
            config = read_json(path, TARGET_LABEL["opencode"])
            changes = []
            merge_config(config, {}, self.state["sources"].get(GENERIC, {}).get("opencode_owned", {}), changes)
            self.write_config(path, before, config, changes, "opencode.json")

    # -- entry points ----------------------------------------------------------
    def install(self, sources, cli_opts):
        remembered = self.state.get("options", {})
        opts = {k: (cli_opts[k] if cli_opts[k] is not None else remembered.get(k, default))
                for k, default in (("hooks", True), ("sdlc_hooks", False))}
        if self.name == "opencode":
            opts["sdlc_hooks"] = False                    # Claude Code hook format only
        present = set(sources) | set(self.state["sources"])

        # Validate config files before touching anything.
        read_json(self.root / ("settings.json" if self.name == "claude" else "opencode.json"), TARGET_LABEL[self.name])

        plans = {s: self.planned_files(s, {**opts, "present": present}) for s in sources}
        claimed = {}
        for s, files in [*plans.items(), *[(s, v.get("files", {})) for s, v in self.state["sources"].items()
                                           if s not in sources]]:
            for rel in files:
                if rel in claimed and claimed[rel] != s:
                    raise SystemExit(f"error: {rel} is provided by both {claimed[rel]} and {s}; "
                                     "the vendor bundle is inconsistent, rebuild it")
                claimed[rel] = s

        for s in sources:
            entry = self.state["sources"].setdefault(s, {})
            prev_files = entry.get("files", {})
            new_files = {}
            for rel, src in plans[s].items():
                rec = self.install_file(src, rel, prev_files.get(rel))
                if rec:
                    new_files[rel] = rec
            for rel in sorted(set(prev_files) - set(plans[s])):
                if not self.remove_file(rel, prev_files[rel]):
                    new_files[rel] = prev_files[rel]
            entry.update(files=new_files, commit=self.manifest["sources"][s].get("commit"),
                         version=self.manifest["sources"][s].get("version"))

        if SDLC not in sources:
            self.resync_sdlc_meta(present)
        if self.name == "claude":
            self.configure_claude(sources, opts)
        else:
            self.configure_opencode(sources, opts)

        self.state["options"] = {**remembered, **opts}
        self.state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if not self.dry:
            self.root.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(dump_json(self.state), encoding="utf-8")
        return opts

    def uninstall(self, sources):
        self.unconfigure(sources)
        for s in sources:
            entry = self.state["sources"].get(s)
            if not entry:
                continue
            kept = {rel: rec for rel, rec in entry.get("files", {}).items() if not self.remove_file(rel, rec)}
            if kept:
                entry["files"] = kept
                entry.pop("settings_owned", None)
                entry.pop("opencode_owned", None)
            else:
                del self.state["sources"][s]
        if SDLC not in sources:
            self.resync_sdlc_meta(set(self.state["sources"]) - set(sources))
        if self.dry:
            return
        if self.state["sources"]:
            self.state_path.write_text(dump_json(self.state), encoding="utf-8")
        elif self.state_path.exists():
            self.state_path.unlink()

    def summary(self):
        s = self.stats
        parts = [f"{v} {k}" for k, v in s.items() if v]
        print(f"    files: {', '.join(parts) if parts else 'nothing to do'}")
        if self.kept_edited:
            shown = ", ".join(self.kept_edited[:5]) + (f" (+{len(self.kept_edited) - 5} more)" if len(self.kept_edited) > 5 else "")
            print(f"    kept {len(self.kept_edited)} file(s) you edited after install: {shown}")
            print("      (re-run with --force to overwrite them; your versions are backed up first)")
        for n in self.notes:
            print(f"    note: {n}")
        if s["backed up"] and not self.dry:
            print(f"    backups: {self.backup_root}")


# -------------------------------------------------------------------- main

def check_prereqs(targets, sources, opts_by_target):
    warn = []
    if GENERIC in sources and any(o["hooks"] for t, o in opts_by_target.items() if t == "claude"):
        if not shutil.which("node"):
            warn.append(f"{GENERIC} Claude Code hooks run with `node`, which is not on PATH. "
                        "Install Node.js 18+ or re-run with --no-hooks.")
    if ARCHIFY in sources and not shutil.which("node"):
        warn.append("the archify skill renders diagrams with `node`, which is not on PATH. Install Node.js 18+.")
    if any(o.get("sdlc_hooks") for o in opts_by_target.values()):
        missing = [t for t in ("bash", "jq", "curl", "perl") if not shutil.which(t)]
        if not (shutil.which("shasum") or shutil.which("sha1sum")):
            missing.append("shasum/sha1sum")
        if missing:
            warn.append(f"{SDLC} hooks need " + ", ".join(missing) + " on PATH.")
    for w in warn:
        print(f"warning: {w}")


def main():
    ap = argparse.ArgumentParser(
        description=f"Install {SDLC} and {GENERIC} into Claude Code and opencode.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Hook choices are remembered, so a plain re-run keeps whatever you chose last time.",
    )
    ap.add_argument("--target", choices=("all",) + TARGETS, default="all",
                    help="which tool to install into (default: all)")
    ap.add_argument("--source", choices=("all",) + SOURCES, default="all",
                    help="which bundle to install (default: all)")
    hooks = ap.add_mutually_exclusive_group()
    hooks.add_argument("--hooks", dest="hooks", action="store_const", const=True,
                       help=f"wire the {GENERIC} hooks (default on first install)")
    hooks.add_argument("--no-hooks", dest="hooks", action="store_const", const=False,
                       help=f"install everything except the {GENERIC} hook runtime")
    sdlc = ap.add_mutually_exclusive_group()
    sdlc.add_argument("--sdlc-hooks", dest="sdlc_hooks", action="store_const", const=True,
                      help=f"also wire the opt-in {SDLC} Claude Code hooks (WebFetch cache, simplify-ignore)")
    sdlc.add_argument("--no-sdlc-hooks", dest="sdlc_hooks", action="store_const", const=False,
                      help=f"unwire the {SDLC} hooks")
    ap.add_argument("--claude-dir", type=Path, help="Claude Code config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    ap.add_argument("--opencode-dir", type=Path,
                    help="opencode config dir (default: $OPENCODE_CONFIG_DIR, $XDG_CONFIG_HOME/opencode or ~/.config/opencode)")
    ap.add_argument("--dry-run", action="store_true", help="show what would change without writing anything")
    ap.add_argument("--uninstall", action="store_true", help="remove what this script installed and restore backups")
    ap.add_argument("--force", action="store_true",
                    help="also overwrite (or on --uninstall, remove) installed files you have edited; backups are kept")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every file operation")
    args = ap.parse_args()

    manifest_path = VENDOR / "MANIFEST.json"
    if not manifest_path.exists():
        sys.exit(f"error: {manifest_path} not found. Run this script from inside the agent-kit folder.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    targets = TARGETS if args.target == "all" else (args.target,)
    sources = SOURCES if args.source == "all" else (args.source,)
    dirs = {"claude": (args.claude_dir.expanduser() if args.claude_dir else default_claude_dir()),
            "opencode": (args.opencode_dir.expanduser() if args.opencode_dir else default_opencode_dir())}
    cli_opts = {"hooks": args.hooks, "sdlc_hooks": args.sdlc_hooks}

    verb = "Uninstalling" if args.uninstall else "Installing"
    print(f"{verb} {', '.join(SOURCE_LABEL[s] for s in sources)}" + ("  [dry run: nothing will be written]" if args.dry_run else ""))
    for s in sources:
        m = manifest["sources"][s]
        pin = f"{m['commit'][:12]} ({m['commit_date']})" if "commit" in m else m.get("source_zip", "")
        print(f"  {SOURCE_LABEL[s]}: v{m.get('version')} {pin}")

    opts_by_target = {}
    for t in targets:
        target = Target(t, dirs[t], manifest, args.dry_run, args.verbose, args.force)
        print(f"\n[{TARGET_LABEL[t]}] {dirs[t]}")
        if args.uninstall:
            target.uninstall(sources)
        else:
            opts = target.install(sources, cli_opts)
            opts_by_target[t] = opts
            flags = [f"{GENERIC} hooks {'on' if opts['hooks'] else 'off'}"]
            if t == "claude":
                flags.append(f"{SDLC} hooks {'on' if opts['sdlc_hooks'] else 'off'}")
            print(f"    options: {', '.join(flags)}")
        target.summary()

    if not args.uninstall:
        print()
        check_prereqs(targets, sources, opts_by_target)
        if not args.dry_run:
            print("Done. Restart Claude Code / opencode so they pick up the new agents, skills and hooks.")
    elif not args.dry_run:
        print("\nDone.")


if __name__ == "__main__":
    main()
