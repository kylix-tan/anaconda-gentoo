# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024  anaconda-gentoo contributors
"""Tests for kconfig_builder.kconfig — Kconfig parser and dependency resolver."""
import textwrap
import tempfile
from pathlib import Path

import pytest

from kconfig_builder.kconfig import KconfigDB, KconfigEntry


SIMPLE_KCONFIG = textwrap.dedent("""\
    config NET
        bool "Networking support"
        default y

    config IGB
        tristate "Intel Gigabit Ethernet"
        depends on PCI && NET
        select PHYLIB

    config PHYLIB
        tristate "PHY library"
        depends on NET

    config PCI
        bool "PCI support"
""")


def _make_kconfig_db(content: str) -> KconfigDB:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "Kconfig").write_text(content)
        db = KconfigDB(root)
        db.load()
    return db


class TestKconfigEntry:

    def test_depends_symbols_simple(self):
        e = KconfigEntry("IGB", "tristate", "", depends=["PCI && NET"])
        syms = e.depends_symbols()
        assert "PCI" in syms
        assert "NET" in syms

    def test_depends_symbols_negation_excluded(self):
        e = KconfigEntry("FOO", "bool", "", depends=["BAR && !BAZ"])
        syms = e.depends_symbols()
        assert "BAR" in syms
        assert "BAZ" not in syms

    def test_depends_symbols_or_expr(self):
        e = KconfigEntry("FOO", "bool", "", depends=["NET || USB"])
        syms = e.depends_symbols()
        assert "NET" in syms
        assert "USB" in syms

    def test_depends_symbols_empty(self):
        e = KconfigEntry("FOO", "bool", "")
        assert e.depends_symbols() == set()


class TestKconfigDB:

    def test_parse_entries(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        assert db.entry_count == 4

    def test_get_existing(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        e = db.get("IGB")
        assert e is not None
        assert e.kind == "tristate"
        assert "PHYLIB" in e.selects

    def test_get_missing(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        assert db.get("NONEXISTENT") is None

    def test_resolve_selects(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        resolved = db.resolve_dependencies({"IGB"})
        # IGB selects PHYLIB → PHYLIB should be in resolved
        assert "PHYLIB" in resolved

    def test_resolve_depends(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        resolved = db.resolve_dependencies({"IGB"})
        # IGB depends on PCI && NET
        assert "PCI" in resolved
        assert "NET" in resolved

    def test_resolve_transitive(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        # PHYLIB depends on NET; IGB selects PHYLIB
        resolved = db.resolve_dependencies({"IGB"})
        assert "NET" in resolved

    def test_resolve_empty(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        assert db.resolve_dependencies(set()) == set()

    def test_resolve_unknown_symbol(self):
        db = _make_kconfig_db(SIMPLE_KCONFIG)
        # Unknown symbols are included but don't cause errors
        resolved = db.resolve_dependencies({"UNKNOWN_DRIVER"})
        assert "UNKNOWN_DRIVER" in resolved

    def test_resolve_no_infinite_loop(self):
        # Mutually depending symbols should not cause infinite loop
        circular = textwrap.dedent("""\
            config AMOD
                bool "Module A"
                depends on BMOD

            config BMOD
                bool "Module B"
                depends on AMOD
        """)
        db = _make_kconfig_db(circular)
        resolved = db.resolve_dependencies({"AMOD"})
        assert "AMOD" in resolved
        assert "BMOD" in resolved

    def test_source_include(self, tmp_path):
        (tmp_path / "Kconfig").write_text('source "sub/Kconfig"\n')
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "Kconfig").write_text(
            "config SUBMOD\n    tristate \"Sub module\"\n"
        )
        db = KconfigDB(tmp_path)
        db.load()
        assert db.get("SUBMOD") is not None
