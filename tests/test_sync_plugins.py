from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from sync_plugins import (
    PROPRIETARY,
    UNKNOWN,
    Components,
    Section,
    classify_license,
    inspect_components,
    inspect_license,
    is_anthropic,
    load_excludes,
    worth_fetching,
)

if TYPE_CHECKING:
    from pathlib import Path

APACHE = "Apache License\n Version 2.0, January 2004\n http://www.apache.org/licenses/"
MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy"
PROPRIETARY_TEXT = (
    "Copyright (c) 2026 Anthropic, PBC. All rights reserved. This software is proprietary to Anthropic.\n"
    "Except as the Agreement expressly permits, you may not: (a) distribute, publish, sublicense"
)


@pytest.mark.parametrize(
    ("text", "spdx"),
    [(APACHE, "Apache-2.0"), (MIT, "MIT"), (PROPRIETARY_TEXT, PROPRIETARY), ("custom terms", UNKNOWN)],
)
def test_classify_license(text: str, spdx: str) -> None:
    assert classify_license(text) == spdx


def _write(path: Path, content: str | dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))


def test_inspect_components(tmp_path: Path) -> None:
    _write(tmp_path / "skills/a/SKILL.md", "---\nname: a\n---")
    _write(tmp_path / "extra/b/SKILL.md", "---\nname: b\n---")
    _write(tmp_path / "commands/run.md", "# run")
    _write(tmp_path / "agents/x.md", "# x")
    _write(tmp_path / "agents/y.md", "# y")
    hook = {"type": "command", "command": "true"}
    _write(tmp_path / "hooks/hooks.json", {"hooks": {"Stop": [{"hooks": [hook]}], "PreToolUse": [{"hooks": [hook]}]}})
    _write(tmp_path / ".mcp.json", {"mcpServers": {"github": {}, "linear": {}}})
    manifest = {"skills": ["./extra"], "hooks": "./hooks/hooks.json", "lspServers": {"pyright": {}}}

    assert inspect_components(tmp_path, manifest) == Components(
        skills=2, commands=1, agents=2, hooks=2, mcp_servers=2, lsp_servers=1
    )


def test_license_inherited_from_repo_root(tmp_path: Path) -> None:
    _write(tmp_path / "LICENSE", APACHE)
    plugin = tmp_path / "plugins/p"
    plugin.mkdir(parents=True)

    report = inspect_license(plugin, tmp_path, {})

    assert (report.spdx, report.inherited_from_repo, report.redistributable) == ("Apache-2.0", True, True)


def test_nested_proprietary_license_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path / "LICENSE", APACHE)
    _write(tmp_path / "vendor/LICENSE", PROPRIETARY_TEXT)

    report = inspect_license(tmp_path, tmp_path, {})

    assert report.spdx == "Apache-2.0"
    assert not report.redistributable
    assert report.flags == (f"vendor/LICENSE: {PROPRIETARY} does not permit redistribution",)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"author": {"name": "Anthropic"}, "source": {"url": "https://github.com/acme/x.git"}}, True),
        ({"author": {"name": "Acme"}, "source": "./plugins/x"}, False),
        ({"source": "./plugins/x"}, True),
        ({"source": {"source": "url", "url": "https://github.com/acme/x.git"}}, False),
        ({"source": {"source": "url", "url": "https://github.com/anthropics/x.git"}}, True),
    ],
)
def test_worth_fetching(entry: dict[str, object], expected: bool) -> None:
    assert worth_fetching(entry) is expected


def test_is_anthropic_by_email() -> None:
    assert is_anthropic({}, {"author": {"name": "Jane", "email": "jane@anthropic.com"}})


def test_sections_match_directory() -> None:
    assert {s.value for s in Section} == {"code", "design", "engineering", "operations", "product-management"}


def test_load_excludes(tmp_path: Path) -> None:
    f = tmp_path / "excludes.txt"
    f.write_text("# header comment\n\nclaude-security  # proprietary\nfoo\n")

    assert load_excludes(f) == {"claude-security": "proprietary", "foo": ""}


def test_load_excludes_missing_file(tmp_path: Path) -> None:
    assert load_excludes(tmp_path / "nope.txt") == {}
