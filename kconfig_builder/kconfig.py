# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024  anaconda-gentoo contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Kconfig file parser and dependency resolver."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SOURCE_RE = re.compile(r'^\s*(?:or)?source\s+"?([^"#\n]+)"?', re.MULTILINE)
_SYMBOL_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


@dataclass
class KconfigEntry:
    name: str
    kind: str           # bool, tristate, int, string, hex
    prompt: str
    depends: list[str] = field(default_factory=list)
    selects: list[str] = field(default_factory=list)
    implies: list[str] = field(default_factory=list)

    def depends_symbols(self) -> set[str]:
        """Extract non-negated symbol names from all depends expressions."""
        symbols: set[str] = set()
        for expr in self.depends:
            # Strip negated sub-expressions  !FOO or !(...)
            cleaned = re.sub(r"!\s*\([^)]*\)", "", expr)
            cleaned = re.sub(r"!\s*[A-Z][A-Z0-9_]*", "", cleaned)
            for sym in _SYMBOL_RE.findall(cleaned):
                if sym not in ("n", "y", "m", "MODULES"):
                    symbols.add(sym)
        return symbols


class KconfigDB:
    """Parse all Kconfig files in a kernel source tree."""

    def __init__(self, kernel_src: Path) -> None:
        self._src = kernel_src
        self._entries: dict[str, KconfigEntry] = {}
        self._visited: set[str] = set()

    def load(self) -> None:
        top = self._src / "Kconfig"
        if top.exists():
            self._parse_file(top)

    def _resolve_src_path(self, src_str: str, from_dir: Path) -> Path | None:
        src_str = src_str.strip()
        src_str = re.sub(r"\$\(srctree\)", str(self._src), src_str)
        src_str = re.sub(r"\$\(src\)", str(from_dir), src_str)
        p = Path(src_str)
        if p.is_absolute():
            return p if p.exists() else None
        # Relative to kernel root first, then relative to current dir
        for base in (self._src, from_dir):
            candidate = base / p
            if candidate.exists():
                return candidate
        return None

    def _parse_file(self, path: Path) -> None:
        key = str(path.resolve())
        if key in self._visited:
            return
        self._visited.add(key)

        try:
            content = path.read_text(errors="replace")
        except (PermissionError, FileNotFoundError, OSError):
            return

        for m in _SOURCE_RE.finditer(content):
            resolved = self._resolve_src_path(m.group(1), path.parent)
            if resolved:
                self._parse_file(resolved)

        self._parse_entries(content)

    def _parse_entries(self, content: str) -> None:
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            i += 1

            if not (stripped.startswith("config ") or stripped.startswith("menuconfig ")):
                continue

            parts = stripped.split()
            if len(parts) < 2:
                continue
            name = parts[1]
            entry = KconfigEntry(name=name, kind="", prompt="")

            # Parse body — indented lines following the config header
            while i < len(lines):
                body = lines[i]
                body_stripped = body.strip()

                # Empty lines are OK inside a config block
                if not body_stripped:
                    i += 1
                    continue

                # A non-indented, non-empty line signals the end of this entry
                if body and body[0] not in ("\t", " ") and body_stripped:
                    break

                if body_stripped.startswith(("bool", "tristate", "int", "string", "hex")):
                    tok = body_stripped.split(None, 1)
                    entry.kind = tok[0]
                    if len(tok) > 1:
                        entry.prompt = tok[1].strip('"')

                elif body_stripped.startswith("depends on "):
                    entry.depends.append(body_stripped[len("depends on "):].strip())

                elif body_stripped.startswith("select "):
                    sel_tok = body_stripped[len("select "):].split()
                    if sel_tok:
                        entry.selects.append(sel_tok[0])

                elif body_stripped.startswith("imply "):
                    imp_tok = body_stripped[len("imply "):].split()
                    if imp_tok:
                        entry.implies.append(imp_tok[0])

                i += 1

            self._entries[name] = entry

    def get(self, name: str) -> KconfigEntry | None:
        return self._entries.get(name)

    def resolve_dependencies(self, symbols: set[str]) -> set[str]:
        """BFS expansion: add selects, implies, and depends for all symbols."""
        resolved: set[str] = set()
        queue = list(symbols)

        while queue:
            sym = queue.pop()
            if sym in resolved:
                continue
            resolved.add(sym)

            entry = self._entries.get(sym)
            if not entry:
                continue

            for s in entry.selects:
                if s not in resolved:
                    queue.append(s)
            for s in entry.implies:
                if s not in resolved:
                    queue.append(s)
            for s in entry.depends_symbols():
                if s not in resolved:
                    queue.append(s)

        return resolved

    @property
    def entry_count(self) -> int:
        return len(self._entries)
