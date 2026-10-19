#!/usr/bin/env python3
"""Mirror Anthropic-authored Claude plugins into ``skills/``.

Covers the Anthropic Directory sections Code, Design, Engineering, Operations and
Product management, sourced from each plugin's upstream repository. For every
plugin it records skill/command/agent/hook/MCP/LSP counts and its license, and
refuses to copy anything whose license does not permit redistribution.

Usage: python3 tools/sync_plugins.py [--out skills] [--section code ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

type Json = dict[str, Any]

log = logging.getLogger("sync_plugins")

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
GITHUB: Final = "https://github.com"
ANTHROPIC_ORGS: Final = frozenset({"anthropics", "modelcontextprotocol"})
IGNORED_NAMES: Final = frozenset({".git", "__pycache__", ".DS_Store", "node_modules"})
LICENSE_GLOBS: Final = ("LICENSE*", "LICENCE*", "COPYING*")
EXCLUDE_FILE: Final = "plugin-excludes.txt"


# ── Catalog configuration ────────────────────────────────────────────────────


class Section(StrEnum):
    CODE = "code"
    DESIGN = "design"
    ENGINEERING = "engineering"
    OPERATIONS = "operations"
    PRODUCT_MANAGEMENT = "product-management"


@dataclass(frozen=True, slots=True)
class Marketplace:
    repo: str
    section_of: Callable[[Json], Section | None]

    @property
    def url(self) -> str:
        return f"{GITHUB}/{self.repo}.git"


def _official_section(entry: Json) -> Section | None:
    # Every Anthropic plugin in claude-plugins-official is a Claude Code plugin.
    return Section.DESIGN if entry.get("category") == "design" else Section.CODE


def _knowledge_work_section(entry: Json) -> Section | None:
    # knowledge-work-plugins ships one plugin per Directory section, named after it.
    try:
        return Section(entry["name"])
    except ValueError:
        return None


MARKETPLACES: Final = (
    Marketplace("anthropics/claude-plugins-official", _official_section),
    Marketplace("anthropics/knowledge-work-plugins", _knowledge_work_section),
)


# ── Licensing ────────────────────────────────────────────────────────────────

REDISTRIBUTABLE: Final = frozenset(
    {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "CC-BY-4.0", "CC0-1.0", "Unlicense"}
)
PROPRIETARY: Final = "LicenseRef-Proprietary"
UNKNOWN: Final = "LicenseRef-Unknown"

_LICENSE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (spdx, re.compile(rx, re.IGNORECASE | re.DOTALL))
    for spdx, rx in (
        (PROPRIETARY, r"\bproprietary\b.*\bmay not\b.{0,80}\bdistribut"),
        ("Apache-2.0", r"Apache License.{0,40}Version 2\.0"),
        ("MIT", r"Permission is hereby granted, free of charge"),
        ("BSD-3-Clause", r"Neither the name of .{0,200} may be used to endorse"),
        ("BSD-2-Clause", r"Redistributions in binary form must reproduce"),
        ("ISC", r"Permission to use, copy, modify, and/or distribute this software for any purpose"),
        ("MPL-2.0", r"Mozilla Public License,? (Version|v\.) 2\.0"),
        ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 3"),
        ("CC-BY-4.0", r"Creative Commons Attribution 4\.0"),
        ("Unlicense", r"This is free and unencumbered software released into the public domain"),
    )
)
_SPDX_FIELD: Final = re.compile(r"^[A-Za-z0-9.+-]+$")


def classify_license(text: str) -> str:
    return next((spdx for spdx, rx in _LICENSE_PATTERNS if rx.search(text)), UNKNOWN)


@dataclass(frozen=True, slots=True)
class LicenseFile:
    spdx: str
    path: str


@dataclass(frozen=True, slots=True)
class LicenseReport:
    spdx: str
    files: tuple[LicenseFile, ...]
    inherited_from_repo: bool

    @property
    def flags(self) -> tuple[str, ...]:
        return tuple(
            f"{f.path}: {f.spdx} does not permit redistribution" for f in self.files if f.spdx not in REDISTRIBUTABLE
        )

    @property
    def redistributable(self) -> bool:
        return not self.flags


def _license_files(root: Path) -> Iterator[Path]:
    for pattern in LICENSE_GLOBS:
        yield from (p for p in root.rglob(pattern) if p.is_file() and not IGNORED_NAMES & set(p.parts))


def inspect_license(plugin: Path, repo: Path, manifest: Json) -> LicenseReport:
    files = tuple(
        LicenseFile(classify_license(p.read_text(errors="replace")), p.relative_to(plugin).as_posix())
        for p in sorted(set(_license_files(plugin)))
    )
    if own := next((f for f in files if "/" not in f.path), None):
        return LicenseReport(own.spdx, files, inherited_from_repo=False)
    declared = manifest.get("license")
    if isinstance(declared, str) and _SPDX_FIELD.match(declared):
        return LicenseReport(declared, (LicenseFile(declared, "plugin.json#license"), *files), False)
    if repo_license := next((p for g in LICENSE_GLOBS for p in sorted(repo.glob(g)) if p.is_file()), None):
        root = LicenseFile(classify_license(repo_license.read_text(errors="replace")), f"<repo>/{repo_license.name}")
        return LicenseReport(root.spdx, (root, *files), inherited_from_repo=True)
    return LicenseReport(UNKNOWN, (LicenseFile(UNKNOWN, "<none>"), *files), inherited_from_repo=False)


# ── Component inspection ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Components:
    skills: int
    commands: int
    agents: int
    hooks: int
    mcp_servers: int
    lsp_servers: int


def _declared_paths(root: Path, manifest: Json, key: str, default: str) -> list[Path]:
    match manifest.get(key):
        case str() as one:
            extra = [one]
        case list() as many:
            extra = [v for v in many if isinstance(v, str)]
        case _:
            extra = []
    return [root / default, *(root / v for v in extra)]


def _files(paths: Iterable[Path], pattern: str) -> set[Path]:
    found: set[Path] = set()
    for p in paths:
        if p.is_file() and p.match(pattern):
            found.add(p.resolve())
        elif p.is_dir():
            found.update(f.resolve() for f in p.rglob(pattern) if f.is_file())
    return found


def _load_json(path: Path) -> Json:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _hook_sources(root: Path, manifest: Json) -> Iterator[Json]:
    yield _load_json(root / "hooks" / "hooks.json")
    match manifest.get("hooks"):
        case dict() as inline:
            yield inline
        case str() as path:
            yield _load_json(root / path)
        case list() as items:
            for item in items:
                yield item if isinstance(item, dict) else _load_json(root / str(item))
        case _:
            pass


def _count_hooks(sources: Iterable[Json]) -> int:
    seen: set[str] = set()
    for src in sources:
        events = src.get("hooks", src)
        for event, matchers in events.items() if isinstance(events, dict) else ():
            for m in matchers if isinstance(matchers, list) else ():
                for h in m.get("hooks", []) if isinstance(m, dict) else ():
                    seen.add(json.dumps([event, m.get("matcher"), h], sort_keys=True))
    return len(seen)


def _servers(root: Path, manifest: Json, key: str, file: str) -> int:
    names: set[str] = set()
    for declared in (_load_json(root / file), manifest.get(key)):
        match declared:
            case str() as path:
                src = _load_json(root / path)
            case dict() as src:
                pass
            case _:
                continue
        servers = src.get(key, src)
        names.update(servers if isinstance(servers, dict) else ())
    return len(names)


def inspect_components(root: Path, manifest: Json) -> Components:
    skills = _declared_paths(root, manifest, "skills", "skills")
    return Components(
        skills=len(_files(skills, "SKILL.md")),
        commands=len(_files(_declared_paths(root, manifest, "commands", "commands"), "*.md")),
        agents=len(_files(_declared_paths(root, manifest, "agents", "agents"), "*.md")),
        hooks=_count_hooks(_hook_sources(root, manifest)),
        mcp_servers=_servers(root, manifest, "mcpServers", ".mcp.json"),
        lsp_servers=_servers(root, manifest, "lspServers", ".lsp.json"),
    )


# ── Git retrieval ────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


@dataclass(frozen=True, slots=True)
class Checkout:
    """Blobless, sparse checkout: only root files and paths passed to ``include`` are ever downloaded."""

    url: str
    sha: str
    path: Path

    def include(self, subdir: str) -> None:
        if subdir in {"", "."}:
            _git("-C", str(self.path), "sparse-checkout", "disable")
        else:
            _git("-C", str(self.path), "sparse-checkout", "add", subdir)


@dataclass(slots=True)
class GitCache:
    workdir: Path
    _checkouts: dict[tuple[str, str], Checkout] = field(default_factory=dict)

    def get(self, url: str, rev: str = "HEAD") -> Checkout:
        if hit := self._checkouts.get((url, rev)):
            return hit
        dest = self.workdir / f"{len(self._checkouts):02d}-{Path(url).stem}"
        log.info("fetching %s @ %s", url, rev)
        _git("init", "--quiet", str(dest))
        _git("-C", str(dest), "fetch", "--quiet", "--depth", "1", "--filter=blob:none", url, rev)
        _git("-C", str(dest), "sparse-checkout", "set", "--cone", ".claude-plugin")
        _git("-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "--quiet", "FETCH_HEAD")
        sha = _git("-C", str(dest), "rev-parse", "HEAD").strip()
        return self._checkouts.setdefault((url, rev), Checkout(url, sha, dest))

    @property
    def checkouts(self) -> tuple[Checkout, ...]:
        return tuple(self._checkouts.values())


@dataclass(frozen=True, slots=True)
class Upstream:
    checkout: Checkout
    subdir: str

    @property
    def path(self) -> Path:
        return (self.checkout.path / self.subdir).resolve()


def resolve_source(source: Any, home: Checkout, git: GitCache) -> Upstream | None:
    match source:
        case str() as rel:
            return Upstream(home, rel.removeprefix("./"))
        case {"source": "git-subdir", "url": str(url), "path": str(sub)}:
            return Upstream(git.get(url, source.get("sha") or source.get("ref") or "HEAD"), sub)
        case {"source": "url", "url": str(url)}:
            return Upstream(git.get(url, source.get("sha") or source.get("ref") or "HEAD"), ".")
        case {"source": "github", "repo": str(repo)}:
            return Upstream(git.get(f"{GITHUB}/{repo}.git", source.get("sha") or source.get("ref") or "HEAD"), ".")
        case _:
            return None


# ── Plugin records ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Plugin:
    name: str
    section: Section
    version: str | None
    description: str
    author: str
    upstream_url: str
    upstream_sha: str
    upstream_path: str
    components: Components
    license: LicenseReport
    entry: Json
    source_dir: Path

    @property
    def tree_url(self) -> str:
        sub = "" if self.upstream_path in {".", ""} else self.upstream_path.removeprefix("./")
        return f"{self.upstream_url.removesuffix('.git')}/tree/{self.upstream_sha}/{sub}".rstrip("/")

    @property
    def stored_path(self) -> str | None:
        return f"{self.section}/{self.name}" if self.license.redistributable else None

    def to_json(self) -> Json:
        return {
            "name": self.name,
            "section": self.section.value,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "upstream": {"url": self.upstream_url, "sha": self.upstream_sha, "path": self.upstream_path},
            "components": asdict(self.components),
            "license": self.license.spdx,
            "license_files": [asdict(f) for f in self.license.files],
            "redistributable": self.license.redistributable,
            "flags": list(self.license.flags),
            "stored_path": self.stored_path,
        }


def _author(entry: Json, manifest: Json) -> str:
    for src in (entry, manifest):
        match src.get("author"):
            case {"name": str(name)}:
                return name
            case str(name):
                return name
    return ""


def _author_email(manifest: Json) -> str:
    author = manifest.get("author")
    return str(author.get("email", "")) if isinstance(author, dict) else ""


def is_anthropic(entry: Json, manifest: Json) -> bool:
    return "anthropic" in _author(entry, manifest).lower() or _author_email(manifest).endswith("@anthropic.com")


def worth_fetching(entry: Json) -> bool:
    """Decide from marketplace metadata alone, so third-party repos are never cloned."""
    if author := _author(entry, {}):
        return "anthropic" in author.lower()
    match entry.get("source"):
        case str():
            return True  # lives in the (Anthropic-owned) marketplace repo; plugin.json decides
        case {"url": str(url)}:
            return url.removeprefix(f"{GITHUB}/").split("/", 1)[0] in ANTHROPIC_ORGS
        case {"repo": str(repo)}:
            return repo.split("/", 1)[0] in ANTHROPIC_ORGS
        case _:
            return False


@dataclass(frozen=True, slots=True)
class Excluded:
    name: str
    section: Section
    reason: str


def load_excludes(path: Path) -> dict[str, str]:
    """Parse ``name  # reason`` lines; blank lines and full-line comments are ignored."""
    if not path.is_file():
        return {}
    excludes: dict[str, str] = {}
    for line in path.read_text().splitlines():
        name, _, reason = line.partition("#")
        if name := name.strip():
            excludes[name] = reason.strip()
    return excludes


def collect(
    sections: frozenset[Section], git: GitCache, excludes: Mapping[str, str]
) -> tuple[list[Plugin], list[Excluded]]:
    plugins: dict[str, Plugin] = {}
    excluded: dict[str, Excluded] = {}
    for mp in MARKETPLACES:
        home = git.get(mp.url)
        catalog = _load_json(home.path / ".claude-plugin" / "marketplace.json")
        for entry in catalog.get("plugins", []):
            name = entry.get("name", "")
            section = mp.section_of(entry)
            if name in plugins or name in excluded or section not in sections or not worth_fetching(entry):
                continue
            if name in excludes:
                log.info("excluded (not downloaded): %s — %s", name, excludes[name] or "no reason given")
                excluded[name] = Excluded(name, section, excludes[name])
                continue
            if (up := resolve_source(entry.get("source"), home, git)) is None:
                log.warning("skip %s: unsupported source %r", name, entry.get("source"))
                continue
            up.checkout.include(up.subdir)
            if not up.path.is_dir():
                log.warning("skip %s: unsupported or missing source %r", name, entry.get("source"))
                continue
            manifest = _load_json(up.path / ".claude-plugin" / "plugin.json")
            if not is_anthropic(entry, manifest):
                continue
            merged = manifest | entry  # marketplace entries override plugin.json (strict=false semantics)
            plugins[name] = Plugin(
                name=name,
                section=section,
                version=merged.get("version"),
                description=str(merged.get("description", "")),
                author=_author(entry, manifest),
                upstream_url=up.checkout.url,
                upstream_sha=up.checkout.sha,
                upstream_path=up.subdir,
                components=inspect_components(up.path, merged),
                license=inspect_license(up.path, up.checkout.path, manifest),
                entry=entry,
                source_dir=up.path,
            )
    return (
        sorted(plugins.values(), key=lambda p: (p.section, p.name)),
        sorted(excluded.values(), key=lambda e: (e.section, e.name)),
    )


# ── Output ───────────────────────────────────────────────────────────────────


def _ignore(root: Path) -> Callable[[str, list[str]], set[str]]:
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(IGNORED_NAMES & set(names))
        for n in names:
            p = Path(directory, n)
            if p.is_symlink() and not p.resolve().is_relative_to(root):
                log.warning("skip symlink escaping plugin root: %s", p)
                skipped.add(n)
        return skipped

    return ignore


def store(plugin: Plugin, out: Path, repo_license: Path | None) -> None:
    dest = out / plugin.section / plugin.name
    shutil.copytree(plugin.source_dir, dest, symlinks=True, ignore=_ignore(plugin.source_dir))
    if plugin.license.inherited_from_repo and repo_license and not (dest / "LICENSE").exists():
        shutil.copy2(repo_license, dest / "LICENSE")  # Apache-2.0 §4(a): ship the license with the copy


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_readme(plugins: list[Plugin], excluded: Sequence[Excluded] = ()) -> str:
    cols = (
        "Section",
        "Plugin",
        "Version",
        "Skills",
        "Commands",
        "Agents",
        "Hooks",
        "MCP",
        "LSP",
        "License",
        "Redistributable",
        "Upstream",
    )
    rows = [
        (
            p.section.value,
            f"[{p.name}]({p.stored_path})" if p.stored_path else p.name,
            p.version or "—",
            *asdict(p.components).values(),
            p.license.spdx,
            "✅" if p.license.redistributable else "⛔ **NO**",
            f"[{Path(p.upstream_url).stem}@{p.upstream_sha[:7]}]({p.tree_url})",
        )
        for p in plugins
    ]
    totals = {k: sum(getattr(p.components, k) for p in plugins) for k in (f.name for f in fields(Components))}
    flagged = [p for p in plugins if not p.license.redistributable]
    return "\n".join(
        [
            "# Anthropic plugins mirror",
            "",
            "Generated by `tools/sync_plugins.py` — do not edit by hand. Re-run the script to refresh.",
            "",
            f"**{len(plugins)} plugins** · {' · '.join(f'{v} {k.replace("_", " ")}' for k, v in totals.items())}",
            "",
            "| " + " | ".join(cols) + " |",
            "|" + "---|" * len(cols),
            *("| " + " | ".join(map(_cell, r)) + " |" for r in rows),
            "",
            "## ⛔ Not redistributable",
            "",
            *(
                [
                    f"- **{p.name}** ({p.section}): {'; '.join(p.license.flags)}. Metadata only; content not mirrored."
                    for p in flagged
                ]
                or ["None."]
            ),
            "",
            f"## 🚫 Excluded by `{EXCLUDE_FILE}` (never downloaded)",
            "",
            *([f"- **{e.name}** ({e.section}): {e.reason or 'no reason given'}" for e in excluded] or ["None."]),
            "",
        ]
    )


def marketplace_entry(plugin: Plugin, out_rel: str) -> Json:
    entry = {k: v for k, v in plugin.entry.items() if k != "source"}
    return {**entry, "source": f"./{out_rel}/{plugin.stored_path}"}


def write_outputs(
    plugins: list[Plugin], excluded: list[Excluded], out: Path, repo_licenses: Mapping[str, Path | None]
) -> None:
    staging = Path(tempfile.mkdtemp(prefix=".skills-", dir=out.parent))
    try:
        for p in plugins:
            if p.license.redistributable:
                store(p, staging, repo_licenses.get(p.upstream_url))
            else:
                log.warning("NOT mirrored (license): %s — %s", p.name, "; ".join(p.license.flags))
        (staging / "catalog.json").write_text(json.dumps([p.to_json() for p in plugins], indent=2) + "\n")
        (staging / "README.md").write_text(render_readme(plugins, excluded))
        if out.exists():
            shutil.rmtree(out)
        staging.rename(out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    rel = out.relative_to(REPO_ROOT).as_posix() if out.is_relative_to(REPO_ROOT) else None
    if rel is None:
        return
    manifest = {
        "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
        "name": REPO_ROOT.name,
        "description": f"Mirror of Anthropic-authored Claude plugins: {', '.join(s.value for s in Section)}.",
        "owner": {"name": REPO_ROOT.name},
        "plugins": [marketplace_entry(p, rel) for p in plugins if p.stored_path],
    }
    target = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "skills", help="output directory (fully regenerated)")
    ap.add_argument("--section", type=Section, choices=list(Section), action="append", help="limit to section(s)")
    ap.add_argument(
        "--exclude-file",
        type=Path,
        default=REPO_ROOT / EXCLUDE_FILE,
        help="plugin names to skip without downloading, one per line (`name  # reason`)",
    )
    ap.add_argument("--cache", type=Path, help="keep git checkouts in this directory instead of a temp dir")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    sections = frozenset(args.section or Section)
    out: Path = args.out.resolve()

    with tempfile.TemporaryDirectory(prefix="plugin-sync-") as tmp:
        workdir = args.cache.resolve() if args.cache else Path(tmp)
        workdir.mkdir(parents=True, exist_ok=True)
        git = GitCache(workdir)
        plugins, excluded = collect(sections, git, load_excludes(args.exclude_file))
        repo_licenses = {
            c.url: next((p for g in LICENSE_GLOBS for p in sorted(c.path.glob(g)) if p.is_file()), None)
            for c in git.checkouts
        }
        write_outputs(plugins, excluded, out, repo_licenses)

    flagged = sum(not p.license.redistributable for p in plugins)
    log.info(
        "mirrored %d/%d plugins into %s (%d flagged, %d excluded)",
        len(plugins) - flagged,
        len(plugins),
        out,
        flagged,
        len(excluded),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
