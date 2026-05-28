# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024  anaconda-gentoo contributors
"""Tests for kconfig_builder.matchers — alias generation and source scanning."""
import fnmatch
import json
import textwrap
from pathlib import Path

import pytest

from kconfig_builder.matchers import (
    _pci_alias, _usb_alias, _PCI_ANY_ID,
    SourceIndex, DriverEntry, MakefileDB,
    _extract_pci_entries, _extract_usb_entries,
    _extract_acpi_entries, _extract_of_entries,
)
from kconfig_builder.hardware.pci import PCIDevice
from kconfig_builder.hardware.usb import USBDevice


class TestPCIAlias:

    def test_specific_vendor_device(self):
        alias = _pci_alias(0x8086, 0x1521)
        assert alias == "pci:v00008086d00001521sv*sd*bc*sc*i*"

    def test_any_id_becomes_wildcard(self):
        alias = _pci_alias(_PCI_ANY_ID, _PCI_ANY_ID)
        assert alias == "pci:v*d*sv*sd*bc*sc*i*"

    def test_with_subids(self):
        alias = _pci_alias(0x8086, 0x1521, 0x1462, 0x7C02)
        assert "sv00001462" in alias
        assert "sd00007C02" in alias

    def test_device_alias_matches_driver_pattern(self):
        dev = PCIDevice("0000:00:1f.6", 0x8086, 0x15BE, 0x8086, 0x0000, 0x020000, 0)
        driver_pattern = _pci_alias(0x8086, 0x15BE)
        assert fnmatch.fnmatch(dev.alias, driver_pattern)

    def test_wrong_vendor_no_match(self):
        dev = PCIDevice("0000:00:1f.6", 0x8086, 0x15BE, 0, 0, 0, 0)
        driver_pattern = _pci_alias(0x10DE, 0x15BE)
        assert not fnmatch.fnmatch(dev.alias, driver_pattern)

    def test_any_vendor_matches_any_device(self):
        dev = PCIDevice("0000:01:00.0", 0x1002, 0x7340, 0, 0, 0x030200, 0)
        driver_pattern = _pci_alias(_PCI_ANY_ID, _PCI_ANY_ID)
        assert fnmatch.fnmatch(dev.alias, driver_pattern)


class TestUSBAlias:

    def test_specific_device(self):
        alias = _usb_alias(vendor=0x0483, product=0x5740)
        assert "v0483" in alias
        assert "p5740" in alias

    def test_wildcard_vendor(self):
        alias = _usb_alias()
        assert "v*" in alias
        assert "p*" in alias

    def test_interface_info(self):
        alias = _usb_alias(ic=0x02, isc=0x02, ip=0x01)
        assert "ic02" in alias
        assert "isc02" in alias
        assert "ip01" in alias

    def test_device_alias_matches_driver_pattern(self):
        dev = USBDevice(
            bus=1, address=2,
            vendor=0x0483, product=0x5740,
            bcd_device=0x0200,
            dev_class=0x02, dev_subclass=0x00, dev_protocol=0x00,
            iface_class=0x02, iface_subclass=0x02, iface_protocol=0x01,
        )
        driver_pattern = _usb_alias(vendor=0x0483, product=0x5740)
        assert fnmatch.fnmatch(dev.alias, driver_pattern)


class TestSourceIndex:

    def test_match_exact(self):
        idx = SourceIndex()
        idx.entries.append(DriverEntry(
            "pci:v00008086d00001521sv*sd*bc*sc*i*", "igb", "drivers/net/igb/igb_main.c"
        ))
        dev = PCIDevice("0000:01:00.0", 0x8086, 0x1521, 0x1462, 0x7C02, 0x020000, 0)
        matches = idx.match(dev.alias)
        assert len(matches) == 1
        assert matches[0].module == "igb"

    def test_no_match(self):
        idx = SourceIndex()
        idx.entries.append(DriverEntry(
            "pci:v00008086d00001521sv*sd*bc*sc*i*", "igb", "igb_main.c"
        ))
        dev = PCIDevice("0000:01:00.0", 0x10DE, 0x1234, 0, 0, 0x030200, 0)
        assert idx.match(dev.alias) == []

    def test_json_round_trip(self):
        idx = SourceIndex()
        idx.entries.append(DriverEntry("pci:v*d*sv*sd*bc*sc*i*", "e1000", "e1000.c"))
        restored = SourceIndex.from_json(idx.to_json())
        assert len(restored.entries) == 1
        assert restored.entries[0].module == "e1000"


class TestExtractors:

    def test_pci_device_macro(self):
        content = textwrap.dedent("""\
            static const struct pci_device_id igb_pci_tbl[] = {
                { PCI_DEVICE(0x8086, 0x1521) },
                { PCI_DEVICE(0x8086, 0x1522) },
                { 0 }
            };
            MODULE_DEVICE_TABLE(pci, igb_pci_tbl);
        """)
        entries = _extract_pci_entries(content, "igb", "drivers/igb.c")
        assert len(entries) == 2
        assert all("v00008086" in e.alias_pattern for e in entries)

    def test_usb_device_macro(self):
        content = textwrap.dedent("""\
            static const struct usb_device_id cdc_ids[] = {
                { USB_DEVICE(0x0483, 0x5740) },
                { }
            };
            MODULE_DEVICE_TABLE(usb, cdc_ids);
        """)
        entries = _extract_usb_entries(content, "cdc_acm", "drivers/cdc_acm.c")
        assert len(entries) == 1
        assert "v0483" in entries[0].alias_pattern
        assert "p5740" in entries[0].alias_pattern

    def test_acpi_hid_string(self):
        content = textwrap.dedent("""\
            static const struct acpi_device_id foo_ids[] = {
                { "PNP0501", 0 },
                { "PNP0500", 0 },
                { }
            };
            MODULE_DEVICE_TABLE(acpi, foo_ids);
        """)
        entries = _extract_acpi_entries(content, "serial", "drivers/serial.c")
        hids = [e.alias_pattern for e in entries]
        assert "acpi:PNP0501:" in hids
        assert "acpi:PNP0500:" in hids

    def test_of_compatible(self):
        content = textwrap.dedent("""\
            static const struct of_device_id spi_ids[] = {
                { .compatible = "vendor,my-spi" },
                { .compatible = "vendor,my-spi-v2" },
                {}
            };
            MODULE_DEVICE_TABLE(of, spi_ids);
        """)
        entries = _extract_of_entries(content, "my_spi", "drivers/spi.c")
        compats = [e.alias_pattern for e in entries]
        assert "of:vendor,my-spi" in compats
        assert "of:vendor,my-spi-v2" in compats


class TestMakefileDB:
    """MakefileDB scans _SCAN_DIRS = [drivers, net, ...] under kernel_src."""

    def _drivers(self, tmp_path):
        """Create and return a drivers/ subdir in tmp_path."""
        d = tmp_path / "drivers"
        d.mkdir(exist_ok=True)
        return d

    def test_parse_obj_module(self, tmp_path):
        d = self._drivers(tmp_path)
        (d / "Makefile").write_text(
            "obj-$(CONFIG_IGB) += igb.o\n"
            "obj-$(CONFIG_E1000E) += e1000e.o\n"
        )
        db = MakefileDB(tmp_path)
        db.build()
        assert "IGB" in db.get_configs("igb")
        assert "E1000E" in db.get_configs("e1000e")

    def test_parse_subdir(self, tmp_path):
        d = self._drivers(tmp_path)
        (d / "Makefile").write_text("obj-$(CONFIG_NET_VENDOR_INTEL) += intel/\n")
        intel = d / "intel"
        intel.mkdir()
        (intel / "Makefile").write_text("obj-$(CONFIG_IGB) += igb.o\n")
        db = MakefileDB(tmp_path)
        db.build()
        assert "IGB" in db.get_configs("igb")

    def test_line_continuation(self, tmp_path):
        d = self._drivers(tmp_path)
        (d / "Makefile").write_text(
            "obj-$(CONFIG_IGB) \\\n"
            "    += igb.o\n"
        )
        db = MakefileDB(tmp_path)
        db.build()
        assert "IGB" in db.get_configs("igb")

    def test_hyphen_underscore_normalization(self, tmp_path):
        d = self._drivers(tmp_path)
        (d / "Makefile").write_text("obj-$(CONFIG_USB_SERIAL) += usb-serial.o\n")
        db = MakefileDB(tmp_path)
        db.build()
        assert "USB_SERIAL" in db.get_configs("usb_serial")

    def test_missing_module_returns_empty(self, tmp_path):
        d = self._drivers(tmp_path)
        (d / "Makefile").write_text("obj-$(CONFIG_IGB) += igb.o\n")
        db = MakefileDB(tmp_path)
        db.build()
        assert db.get_configs("nonexistent") == []
