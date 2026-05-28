# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024  anaconda-gentoo contributors
"""Tests for kconfig_builder.hardware — alias format and FDT parsing.

Hardware scanning functions (scan_pci, scan_usb, scan_acpi) require
/dev/port and /dev/mem so they are skipped on non-Linux or non-root.
Alias format and FDT parser are tested without hardware access.
"""
import os
import struct
import sys

import pytest

from kconfig_builder.hardware.pci import PCIDevice
from kconfig_builder.hardware.usb import USBDevice
from kconfig_builder.hardware.acpi import ACPIInfo
from kconfig_builder.hardware.dt import DTDevice, parse_fdt, _FDT_MAGIC

LINUX_ROOT = sys.platform == "linux" and os.geteuid() == 0


class TestPCIDeviceAlias:

    def test_alias_format(self):
        dev = PCIDevice("0000:00:1f.6", 0x8086, 0x15BE, 0x8086, 0x0000, 0x020000, 0)
        assert dev.alias == "pci:v00008086d000015BEsv00008086sd00000000bc02sc00i00"

    def test_alias_uppercase_hex(self):
        dev = PCIDevice("0000:01:00.0", 0x10DE, 0xABCD, 0, 0, 0, 0)
        assert "v000010DE" in dev.alias
        assert "dABCD" in dev.alias or "d0000ABCD" in dev.alias

    def test_class_decomposition(self):
        # class 0x030200 = base=03 sub=02 prog=00
        dev = PCIDevice("0000:01:00.0", 0x1002, 0x7340, 0, 0, 0x030200, 0)
        assert "bc03" in dev.alias
        assert "sc02" in dev.alias
        assert "i00" in dev.alias

    def test_str_representation(self):
        dev = PCIDevice("0000:00:1f.6", 0x8086, 0x15BE, 0, 0, 0x020000, 0)
        s = str(dev)
        assert "8086" in s
        assert "15be" in s.lower()

    def test_base_class_property(self):
        dev = PCIDevice("0000:00:00.0", 0, 0, 0, 0, 0x0C0330, 0)
        assert dev.base_class == 0x0C
        assert dev.sub_class == 0x03
        assert dev.prog_if == 0x30


class TestUSBDeviceAlias:

    def _dev(self, vendor, product, dc=0, dsc=0, dp=0, ic=0, isc=0, ip=0):
        return USBDevice(
            bus=1, address=2,
            vendor=vendor, product=product, bcd_device=0x0200,
            dev_class=dc, dev_subclass=dsc, dev_protocol=dp,
            iface_class=ic, iface_subclass=isc, iface_protocol=ip,
        )

    def test_alias_format(self):
        dev = self._dev(0x0483, 0x5740, ic=0x02, isc=0x02, ip=0x01)
        assert "v0483" in dev.alias
        assert "p5740" in dev.alias
        assert "ic02" in dev.alias

    def test_alias_uppercase(self):
        dev = self._dev(0xABCD, 0xEF01)
        assert "vABCD" in dev.alias
        assert "pEF01" in dev.alias

    def test_str_representation(self):
        dev = self._dev(0x0483, 0x5740)
        assert "0483" in str(dev)
        assert "5740" in str(dev)


class TestACPIInfo:

    def test_implied_configs_with_acpi(self):
        info = ACPIInfo(has_acpi=True, table_sigs=["FACP", "HPET", "APIC"])
        configs = info.implied_configs()
        assert "ACPI" in configs
        assert "HPET_TIMER" in configs
        assert "X86_LOCAL_APIC" in configs

    def test_implied_configs_no_acpi(self):
        info = ACPIInfo(has_acpi=False)
        configs = info.implied_configs()
        assert "ACPI" not in configs

    def test_mcfg_implies_pci_mmconfig(self):
        info = ACPIInfo(has_acpi=True, table_sigs=["MCFG"])
        assert "PCI_MMCONFIG" in info.implied_configs()

    def test_dmar_implies_iommu(self):
        info = ACPIInfo(has_acpi=True, table_sigs=["DMAR"])
        assert "INTEL_IOMMU" in info.implied_configs()


class TestFDTParser:

    def _make_fdt(self, nodes: list[tuple[str, list[str]]]) -> bytes:
        """Build a minimal valid FDT blob with given (name, compatibles) nodes."""
        # FDT token constants
        FDT_BEGIN_NODE = 0x00000001
        FDT_END_NODE   = 0x00000002
        FDT_PROP       = 0x00000003
        FDT_END        = 0x00000009

        def align4(data: bytes) -> bytes:
            while len(data) % 4:
                data += b"\x00"
            return data

        strings_block = bytearray()
        str_offsets: dict[str, int] = {}

        def add_string(s: str) -> int:
            if s not in str_offsets:
                str_offsets[s] = len(strings_block)
                strings_block.extend(s.encode() + b"\x00")
            return str_offsets[s]

        compat_off = add_string("compatible")

        struct_block = b""

        def u32(v): return struct.pack(">I", v)

        # Root node (empty name = "/")
        struct_block += u32(FDT_BEGIN_NODE) + align4(b"\x00")

        for name, compats in nodes:
            name_bytes = name.encode() + b"\x00"
            struct_block += u32(FDT_BEGIN_NODE) + align4(name_bytes)

            if compats:
                # prop_len = raw (unpadded) byte count; data written padded to 4
                raw_val = b"\x00".join(c.encode() for c in compats) + b"\x00"
                padded_val = align4(raw_val)
                struct_block += (
                    u32(FDT_PROP)
                    + u32(len(raw_val))   # actual length, NOT padded
                    + u32(compat_off)
                    + padded_val
                )
            struct_block += u32(FDT_END_NODE)

        struct_block += u32(FDT_END_NODE)  # close root
        struct_block += u32(FDT_END)

        # FDT header is exactly 10 x uint32 = 40 bytes
        HDR_SIZE = 40
        off_dt_struct  = HDR_SIZE
        off_dt_strings = HDR_SIZE + len(struct_block)
        totalsize = HDR_SIZE + len(struct_block) + len(strings_block)

        header = struct.pack(
            ">IIIIIIIIII",   # 10 uint32s = 40 bytes
            _FDT_MAGIC,
            totalsize,
            off_dt_struct,
            off_dt_strings,
            HDR_SIZE,        # off_mem_rsvmap (point to end of header)
            17,              # version
            16,              # last_comp_version
            0,               # boot_cpuid_phys
            len(strings_block),
            len(struct_block),
        )
        return header + struct_block + bytes(strings_block)

    def test_invalid_magic(self):
        assert parse_fdt(b"\x00" * 8) is None

    def test_too_short(self):
        assert parse_fdt(b"\xd0\x0d") is None

    def test_parse_single_node(self):
        fdt = self._make_fdt([("eth0", ["vendor,my-eth"])])
        devices = parse_fdt(fdt)
        assert devices is not None
        assert len(devices) == 1
        assert "vendor,my-eth" in devices[0].compatible

    def test_parse_multiple_nodes(self):
        fdt = self._make_fdt([
            ("spi0", ["vendor,spi-v1", "generic,spi"]),
            ("i2c0", ["vendor,i2c"]),
        ])
        devices = parse_fdt(fdt)
        assert devices is not None
        assert len(devices) == 2

    def test_dt_device_aliases(self):
        dev = DTDevice("/soc/eth0", ["vendor,my-eth", "generic,eth"])
        aliases = dev.aliases
        assert "of:vendor,my-eth" in aliases
        assert "of:generic,eth" in aliases
        assert "platform:my-eth" in aliases

    def test_node_without_compatible_excluded(self):
        fdt = self._make_fdt([
            ("with-compat", ["vendor,foo"]),
            ("no-compat", []),
        ])
        devices = parse_fdt(fdt)
        assert devices is not None
        paths = [d.node_path for d in devices]
        assert any("with-compat" in p for p in paths)
        assert not any("no-compat" in p for p in paths)


@pytest.mark.skipif(not LINUX_ROOT, reason="requires Linux + root for /dev/port")
class TestPCIScannerLive:
    def test_scan_returns_list(self):
        from kconfig_builder.hardware.pci import scan_pci
        devices, err = scan_pci()
        assert isinstance(devices, list)
        # On real hardware there should be at least a host bridge
        assert len(devices) > 0

    def test_devices_have_valid_aliases(self):
        from kconfig_builder.hardware.pci import scan_pci
        devices, _ = scan_pci()
        for dev in devices:
            alias = dev.alias
            assert alias.startswith("pci:")
            assert "sv" in alias
            assert "bc" in alias


@pytest.mark.skipif(not LINUX_ROOT, reason="requires Linux + root for /dev/mem")
class TestACPIScanLive:
    def test_scan_returns_info(self):
        from kconfig_builder.hardware.acpi import scan_acpi
        info, err = scan_acpi()
        assert isinstance(info, ACPIInfo)
