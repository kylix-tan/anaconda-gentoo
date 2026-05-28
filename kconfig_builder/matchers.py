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

"""Match device aliases to kernel CONFIG symbols via source tree scanning."""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_PCI_ANY_ID = 0xFFFFFFFF
_USB_ANY = "*"

# Directories to scan for drivers
_SCAN_DIRS = ["drivers", "net", "sound", "crypto", "block"]

# Regex: MODULE_DEVICE_TABLE(bus_type, table_name)
_MOD_TABLE_RE = re.compile(
    r"MODULE_DEVICE_TABLE\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)"
)

# Regex: obj-$(CONFIG_FOO) += bar.o  or  obj-$(CONFIG_FOO) += bar/
_OBJ_RE = re.compile(
    r"obj-\$\(CONFIG_([A-Z0-9_]+)\)\s*\+=\s*(\S+)"
)

# PCI macro patterns
_PCI_DEVICE_RE = re.compile(
    r"PCI_DEVICE\s*\(\s*(0[xX][0-9a-fA-F]+|\d+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*\)"
)
_PCI_VDEVICE_RE = re.compile(
    r"PCI_VDEVICE\s*\(\s*(\w+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*\)"
)
_PCI_DEVICE_CLASS_RE = re.compile(
    r"PCI_DEVICE_CLASS\s*\(\s*(0[xX][0-9a-fA-F]+|\d+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*\)"
)
_PCI_ANY_RE = re.compile(r"\bPCI_ANY_ID\b")

# USB macro patterns
_USB_DEVICE_RE = re.compile(
    r"USB_DEVICE\s*\(\s*(0[xX][0-9a-fA-F]+|\d+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*\)"
)
_USB_IFACE_RE = re.compile(
    r"USB_INTERFACE_INFO\s*\(\s*(0[xX][0-9a-fA-F]+|\d+)\s*,"
    r"\s*(0[xX][0-9a-fA-F]+|\d+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*\)"
)
_USB_DEV_IFACE_RE = re.compile(
    r"USB_DEVICE_AND_INTERFACE_INFO\s*\(\s*(0[xX][0-9a-fA-F]+|\d+)\s*,"
    r"\s*(0[xX][0-9a-fA-F]+|\d+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*,"
    r"\s*(0[xX][0-9a-fA-F]+|\d+)\s*,\s*(0[xX][0-9a-fA-F]+|\d+)\s*\)"
)

# String entry for ACPI/platform/OF device IDs: { "STRING", ... }
_STRING_ENTRY_RE = re.compile(r'\{\s*"([^"]+)"')
# OF compatible: .compatible = "vendor,name"
_OF_COMPAT_RE = re.compile(r'\.compatible\s*=\s*"([^"]+)"')


def _hex(s: str) -> int:
    return int(s, 16) if s.lower().startswith("0x") else int(s)


@dataclass(frozen=True)
class DriverEntry:
    alias_pattern: str  # fnmatch-style pattern matching device alias
    module: str         # kernel module name (derived from source path)
    source_file: str    # relative path in kernel tree


@dataclass
class SourceIndex:
    """Index of alias_pattern → DriverEntry built from kernel C source."""
    entries: list[DriverEntry] = field(default_factory=list)

    def match(self, device_alias: str) -> list[DriverEntry]:
        return [e for e in self.entries if fnmatch.fnmatch(device_alias, e.alias_pattern)]

    def to_json(self) -> str:
        return json.dumps(
            [{"alias": e.alias_pattern, "module": e.module, "src": e.source_file}
             for e in self.entries],
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "SourceIndex":
        idx = cls()
        for item in json.loads(text):
            idx.entries.append(DriverEntry(item["alias"], item["module"], item["src"]))
        return idx


def _module_from_path(src_path: Path, kernel_src: Path) -> str:
    """Derive module name from source file path."""
    rel = src_path.relative_to(kernel_src)
    # Use the stem of the file (e.g., igb_main → igb_main, then strip _main/_core)
    stem = src_path.stem
    # Common suffixes that are part of the module name
    for suffix in ("_main", "_core", "_drv", "_pci", "_base"):
        if stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def _pci_alias(
    vendor: int, device: int,
    subvendor: int = _PCI_ANY_ID, subdevice: int = _PCI_ANY_ID,
    cls: int = 0, cls_mask: int = 0,
) -> str:
    v = f"{vendor:08X}" if vendor != _PCI_ANY_ID else "*"
    d = f"{device:08X}" if device != _PCI_ANY_ID else "*"
    sv = f"{subvendor:08X}" if subvendor != _PCI_ANY_ID else "*"
    sd = f"{subdevice:08X}" if subdevice != _PCI_ANY_ID else "*"
    # Class: use wildcards if class/mask not set
    if cls_mask == 0 or cls == 0:
        bc, sc, i = "*", "*", "*"
    else:
        bc = f"{(cls >> 16) & 0xFF:02X}" if cls_mask & 0xFF0000 else "*"
        sc = f"{(cls >> 8) & 0xFF:02X}" if cls_mask & 0x00FF00 else "*"
        i = f"{cls & 0xFF:02X}" if cls_mask & 0x0000FF else "*"
    return f"pci:v{v}d{d}sv{sv}sd{sd}bc{bc}sc{sc}i{i}"


# Known PCI vendor name → vendor ID (for PCI_VDEVICE macro)
_VENDOR_NAMES: dict[str, int] = {
    "INTEL": 0x8086, "AMD": 0x1022, "NVIDIA": 0x10DE, "BROADCOM": 0x14E4,
    "REALTEK": 0x10EC, "MARVELL": 0x11AB, "ATHEROS": 0x168C, "QUALCOMM": 0x17CB,
    "SAMSUNG": 0x144D, "SANDISK": 0x15B7, "WESTERN_DIGITAL": 0x1B96,
    "SEAGATE": 0x1BB1, "TOSHIBA": 0x1179, "MICRON": 0x1344,
    "REDHAT": 0x1AF4, "VIRTIO": 0x1AF4, "VMWARE": 0x15AD,
    "QEMU": 0x1234, "XEN": 0x5853,
    "MELLANOX": 0x15B3, "CHELSIO": 0x1425, "SOLARFLARE": 0x1924,
    "NETRONOME": 0x19EE, "CAVIUM": 0x177D,
}


def _extract_pci_entries(content: str, module: str, src: str) -> list[DriverEntry]:
    entries: list[DriverEntry] = []

    for m in _PCI_DEVICE_RE.finditer(content):
        v, d = _hex(m.group(1)), _hex(m.group(2))
        entries.append(DriverEntry(_pci_alias(v, d), module, src))

    for m in _PCI_VDEVICE_RE.finditer(content):
        vname = m.group(1).upper()
        vid = _VENDOR_NAMES.get(vname, _PCI_ANY_ID)
        did = _hex(m.group(2))
        entries.append(DriverEntry(_pci_alias(vid, did), module, src))

    for m in _PCI_DEVICE_CLASS_RE.finditer(content):
        cls_val = _hex(m.group(1))
        cls_mask = _hex(m.group(2))
        entries.append(DriverEntry(
            _pci_alias(_PCI_ANY_ID, _PCI_ANY_ID, _PCI_ANY_ID, _PCI_ANY_ID, cls_val, cls_mask),
            module, src,
        ))

    return entries


def _usb_alias(
    vendor: int = 0xFFFF, product: int = 0xFFFF,
    dc: int = 0xFF, dsc: int = 0xFF, dp: int = 0xFF,
    ic: int = 0xFF, isc: int = 0xFF, ip: int = 0xFF,
) -> str:
    v = f"{vendor:04X}" if vendor != 0xFFFF else "*"
    p = f"{product:04X}" if product != 0xFFFF else "*"
    return (
        f"usb:v{v}p{p}d*"
        f"dc{'*' if dc == 0xFF else f'{dc:02X}'}"
        f"dsc{'*' if dsc == 0xFF else f'{dsc:02X}'}"
        f"dp{'*' if dp == 0xFF else f'{dp:02X}'}"
        f"ic{'*' if ic == 0xFF else f'{ic:02X}'}"
        f"isc{'*' if isc == 0xFF else f'{isc:02X}'}"
        f"ip{'*' if ip == 0xFF else f'{ip:02X}'}in*"
    )


def _extract_usb_entries(content: str, module: str, src: str) -> list[DriverEntry]:
    entries: list[DriverEntry] = []

    for m in _USB_DEVICE_RE.finditer(content):
        v, p = _hex(m.group(1)), _hex(m.group(2))
        entries.append(DriverEntry(_usb_alias(v, p), module, src))

    for m in _USB_IFACE_RE.finditer(content):
        ic, isc, ip = _hex(m.group(1)), _hex(m.group(2)), _hex(m.group(3))
        entries.append(DriverEntry(_usb_alias(ic=ic, isc=isc, ip=ip), module, src))

    for m in _USB_DEV_IFACE_RE.finditer(content):
        v, p = _hex(m.group(1)), _hex(m.group(2))
        ic, isc, ip = _hex(m.group(3)), _hex(m.group(4)), _hex(m.group(5))
        entries.append(DriverEntry(_usb_alias(v, p, ic=ic, isc=isc, ip=ip), module, src))

    return entries


def _extract_acpi_entries(content: str, module: str, src: str) -> list[DriverEntry]:
    return [
        DriverEntry(f"acpi:{m.group(1)}:", module, src)
        for m in _STRING_ENTRY_RE.finditer(content)
        if m.group(1)  # non-empty HID
    ]


def _extract_platform_entries(content: str, module: str, src: str) -> list[DriverEntry]:
    return [
        DriverEntry(f"platform:{m.group(1)}", module, src)
        for m in _STRING_ENTRY_RE.finditer(content)
        if m.group(1)
    ]


def _extract_of_entries(content: str, module: str, src: str) -> list[DriverEntry]:
    return [
        DriverEntry(f"of:{m.group(1)}", module, src)
        for m in _OF_COMPAT_RE.finditer(content)
    ]


_BUS_EXTRACTORS = {
    "pci": _extract_pci_entries,
    "usb": _extract_usb_entries,
    "acpi": _extract_acpi_entries,
    "platform": _extract_platform_entries,
    "of": _extract_of_entries,
    "i2c": _extract_platform_entries,
    "spi": _extract_platform_entries,
}


def _scan_c_file(path: Path, kernel_src: Path) -> list[DriverEntry]:
    try:
        content = path.read_text(errors="replace")
    except (PermissionError, OSError):
        return []

    # Check if file has any MODULE_DEVICE_TABLE declaration
    if "MODULE_DEVICE_TABLE" not in content:
        return []

    src_rel = str(path.relative_to(kernel_src))
    module = _module_from_path(path, kernel_src)
    entries: list[DriverEntry] = []

    for m in _MOD_TABLE_RE.finditer(content):
        bus_type = m.group(1).lower()
        extractor = _BUS_EXTRACTORS.get(bus_type)
        if extractor:
            entries.extend(extractor(content, module, src_rel))

    return entries


def build_source_index(
    kernel_src: Path,
    cache_path: Path | None = None,
    verbose: bool = False,
) -> SourceIndex:
    """Scan kernel source tree and build alias → driver index."""
    if cache_path and cache_path.exists():
        if verbose:
            print(f"  Loading cached source index from {cache_path}")
        return SourceIndex.from_json(cache_path.read_text())

    index = SourceIndex()
    total_files = 0

    for scan_dir_name in _SCAN_DIRS:
        scan_dir = kernel_src / scan_dir_name
        if not scan_dir.exists():
            continue
        for c_file in scan_dir.rglob("*.c"):
            entries = _scan_c_file(c_file, kernel_src)
            index.entries.extend(entries)
            total_files += 1
            if verbose and total_files % 1000 == 0:
                print(f"  Scanned {total_files} files, {len(index.entries)} entries...")

    if verbose:
        print(f"  Total: {total_files} files scanned, {len(index.entries)} alias entries")

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(index.to_json())
        if verbose:
            print(f"  Saved source index to {cache_path}")

    return index


class MakefileDB:
    """Maps module names → CONFIG_* symbols by parsing kernel Makefiles."""

    def __init__(self, kernel_src: Path) -> None:
        self._src = kernel_src
        self._module_to_configs: dict[str, set[str]] = {}
        self._built = False

    def build(self, verbose: bool = False) -> None:
        if self._built:
            return
        count = 0
        for scan_dir in _SCAN_DIRS:
            d = self._src / scan_dir
            if d.exists():
                self._scan_dir(d)
                count += 1
        if verbose:
            print(f"  Indexed {len(self._module_to_configs)} modules from {count} driver trees")
        self._built = True

    def _scan_dir(self, directory: Path) -> None:
        for name in ("Makefile", "Kbuild"):
            mf = directory / name
            if mf.exists():
                self._parse_makefile(mf, directory)
        try:
            for child in directory.iterdir():
                if child.is_dir():
                    self._scan_dir(child)
        except PermissionError:
            pass

    def _parse_makefile(self, makefile: Path, base_dir: Path) -> None:
        try:
            content = makefile.read_text(errors="replace")
        except (PermissionError, OSError):
            return
        # Flatten line continuations
        content = re.sub(r"\\\n\s*", " ", content)
        for line in content.splitlines():
            m = _OBJ_RE.search(line)
            if not m:
                continue
            config = m.group(1)
            target = m.group(2).rstrip("/")
            if target.endswith((".o", ".ko")):
                module = Path(target).stem
                self._module_to_configs.setdefault(module, set()).add(config)
            else:
                # Subdirectory target — associate config with directory name
                self._module_to_configs.setdefault(target, set()).add(config)

    def get_configs(self, module: str) -> list[str]:
        """Return CONFIG symbols for a given module name."""
        # Try exact, then with hyphen→underscore substitution
        for key in (module, module.replace("-", "_"), module.replace("_", "-")):
            if key in self._module_to_configs:
                return sorted(self._module_to_configs[key])
        # Partial prefix match (e.g. "igb_main" → "igb")
        for key, configs in self._module_to_configs.items():
            if module.startswith(key) or key.startswith(module):
                return sorted(configs)
        return []
