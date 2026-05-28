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

"""ACPI table detection via /dev/mem (no sysfs)."""
from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass, field

_RSDP_SIG = b"RSD PTR "
_BIOS_SEARCH_START = 0xE0000
_BIOS_SEARCH_END = 0x100000  # exclusive
_PAGE_SIZE = 4096

# ACPI table signature → CONFIG symbol hints
_TABLE_TO_CONFIG: dict[str, list[str]] = {
    "APIC": ["X86_LOCAL_APIC", "X86_IO_APIC", "ACPI"],
    "HPET": ["HPET", "HPET_TIMER"],
    "MCFG": ["PCI_MMCONFIG", "PCIEPORTBUS"],
    "FACP": ["ACPI"],
    "SSDT": ["ACPI"],
    "DSDT": ["ACPI"],
    "SRAT": ["ACPI_NUMA", "NUMA"],
    "DMAR": ["INTEL_IOMMU", "IOMMU_SUPPORT"],
    "IVRS": ["AMD_IOMMU", "IOMMU_SUPPORT"],
    "BGRT": ["ACPI_BGRT"],
    "FPDT": ["ACPI"],
    "WAET": ["ACPI"],
    "TPM2": ["TCG_TPM", "TCG_CRB"],
    "TCPA": ["TCG_TPM"],
    "LPIT": ["ACPI_LPIT"],
    "MCHI": ["ACPI"],
    "NHLT": ["SND_SOC_INTEL_SKYLAKE"],
    "WSMT": ["ACPI"],
}


@dataclass
class ACPIInfo:
    has_acpi: bool
    table_sigs: list[str] = field(default_factory=list)
    has_mcfg: bool = False
    mcfg_base: int = 0  # PCIe ECAM base address (from MCFG)

    def implied_configs(self) -> list[str]:
        configs: list[str] = []
        if self.has_acpi:
            configs.append("ACPI")
        for sig in self.table_sigs:
            configs.extend(_TABLE_TO_CONFIG.get(sig, []))
        return sorted(set(configs))


def _checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def _mmap_phys(fd: int, phys_addr: int, length: int) -> bytes | None:
    page_offset = phys_addr % _PAGE_SIZE
    aligned_addr = phys_addr - page_offset
    map_len = length + page_offset
    try:
        mm = mmap.mmap(
            fd, map_len,
            mmap.MAP_PRIVATE,
            mmap.PROT_READ,
            offset=aligned_addr,
        )
        data = mm[page_offset: page_offset + length]
        mm.close()
        return bytes(data)
    except (OSError, ValueError):
        return None


def _parse_rsdp(data: bytes) -> dict | None:
    if len(data) < 20 or data[:8] != _RSDP_SIG:
        return None
    if _checksum(data[:20]) != 0:
        return None
    revision = data[15]
    rsdt_addr = struct.unpack_from("<I", data, 16)[0]
    xsdt_addr = 0
    if revision >= 2 and len(data) >= 36:
        xsdt_addr = struct.unpack_from("<Q", data, 24)[0]
    return {"revision": revision, "rsdt": rsdt_addr, "xsdt": xsdt_addr}


def _read_acpi_table_header(fd: int, phys: int) -> tuple[str, int] | None:
    """Return (signature, table_length) for an ACPI table at physical address."""
    hdr = _mmap_phys(fd, phys, 8)
    if not hdr or len(hdr) < 8:
        return None
    sig = hdr[:4].decode("ascii", errors="replace").strip("\x00")
    length = struct.unpack_from("<I", hdr, 4)[0]
    return sig, length


def _parse_rsdt(fd: int, rsdt_phys: int) -> list[int]:
    """Return list of physical addresses from RSDT."""
    result = _read_acpi_table_header(fd, rsdt_phys)
    if not result:
        return []
    _, length = result
    data = _mmap_phys(fd, rsdt_phys, min(length, 4096))
    if not data or len(data) < 36:
        return []
    # Entries start at offset 36, each is 4 bytes
    addrs = []
    offset = 36
    while offset + 4 <= len(data):
        addr = struct.unpack_from("<I", data, offset)[0]
        if addr:
            addrs.append(addr)
        offset += 4
    return addrs


def _parse_xsdt(fd: int, xsdt_phys: int) -> list[int]:
    """Return list of physical addresses from XSDT."""
    result = _read_acpi_table_header(fd, xsdt_phys)
    if not result:
        return []
    _, length = result
    data = _mmap_phys(fd, xsdt_phys, min(length, 4096))
    if not data or len(data) < 36:
        return []
    # Entries start at offset 36, each is 8 bytes
    addrs = []
    offset = 36
    while offset + 8 <= len(data):
        addr = struct.unpack_from("<Q", data, offset)[0]
        if addr:
            addrs.append(addr)
        offset += 8
    return addrs


def _parse_mcfg(fd: int, phys: int) -> tuple[int, bool]:
    """Return (ecam_base_address, found)."""
    result = _read_acpi_table_header(fd, phys)
    if not result:
        return 0, False
    _, length = result
    data = _mmap_phys(fd, phys, min(length, 256))
    if not data or len(data) < 44:
        return 0, False
    # First allocation record starts at offset 44
    base = struct.unpack_from("<Q", data, 44)[0]
    return base, True


def scan_acpi() -> tuple[ACPIInfo, str | None]:
    """Detect ACPI tables via /dev/mem. Returns (info, error_message)."""
    try:
        fd = os.open("/dev/mem", os.O_RDONLY)
    except (PermissionError, FileNotFoundError, OSError) as e:
        return ACPIInfo(has_acpi=False), f"Cannot open /dev/mem: {e} — run as root"

    try:
        # Search BIOS area for RSDP (16-byte aligned)
        bios_data = _mmap_phys(fd, _BIOS_SEARCH_START, _BIOS_SEARCH_END - _BIOS_SEARCH_START)
        if not bios_data:
            return ACPIInfo(has_acpi=False), "Cannot mmap BIOS area from /dev/mem"

        rsdp_offset = -1
        pos = 0
        while pos < len(bios_data) - 8:
            if bios_data[pos:pos + 8] == _RSDP_SIG:
                rsdp_offset = pos
                break
            pos += 16

        if rsdp_offset < 0:
            return ACPIInfo(has_acpi=False), None  # No ACPI (not an error)

        rsdp_phys = _BIOS_SEARCH_START + rsdp_offset
        rsdp_data = bios_data[rsdp_offset:rsdp_offset + 36]
        rsdp = _parse_rsdp(rsdp_data)
        if not rsdp:
            return ACPIInfo(has_acpi=False), "RSDP checksum failed"

        # Get table addresses from XSDT (preferred) or RSDT
        if rsdp["xsdt"]:
            table_phys_list = _parse_xsdt(fd, rsdp["xsdt"])
        else:
            table_phys_list = _parse_rsdt(fd, rsdp["rsdt"])

        info = ACPIInfo(has_acpi=True)
        for tphys in table_phys_list:
            result = _read_acpi_table_header(fd, tphys)
            if not result:
                continue
            sig, _ = result
            if sig not in info.table_sigs:
                info.table_sigs.append(sig)
            if sig == "MCFG":
                base, found = _parse_mcfg(fd, tphys)
                if found:
                    info.has_mcfg = True
                    info.mcfg_base = base

        return info, None
    finally:
        os.close(fd)
