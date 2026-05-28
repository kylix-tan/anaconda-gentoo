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

"""Device Tree detection from raw FDT binary (no sysfs)."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

_FDT_MAGIC = 0xD00DFEED

# FDT structure token types
_FDT_BEGIN_NODE = 0x00000001
_FDT_END_NODE = 0x00000002
_FDT_PROP = 0x00000003
_FDT_NOP = 0x00000004
_FDT_END = 0x00000009


@dataclass(frozen=True)
class DTDevice:
    node_path: str
    compatible: list[str]

    @property
    def aliases(self) -> list[str]:
        result = []
        for compat in self.compatible:
            result.append(f"of:{compat}")
            # Also generate platform alias from base name (without vendor prefix)
            base = compat.split(",")[-1]
            result.append(f"platform:{base}")
        return result

    def __str__(self) -> str:
        return f"DT [{self.node_path}] compatible={self.compatible}"


def _read_u32_be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _align4(n: int) -> int:
    return (n + 3) & ~3


def parse_fdt(data: bytes) -> list[DTDevice] | None:
    """Parse a raw FDT blob and return list of DTDevice objects."""
    if len(data) < 8:
        return None
    magic = _read_u32_be(data, 0)
    if magic != _FDT_MAGIC:
        return None

    # FDT header fields (all big-endian uint32)
    totalsize = _read_u32_be(data, 4)
    off_dt_struct = _read_u32_be(data, 8)
    off_dt_strings = _read_u32_be(data, 12)
    # off_mem_rsvmap = _read_u32_be(data, 16)  # unused here

    struct_data = data[off_dt_struct:]
    strings_data = data[off_dt_strings:]

    def read_string(str_offset: int) -> str:
        end = strings_data.index(b"\x00", str_offset)
        return strings_data[str_offset:end].decode("utf-8", errors="replace")

    devices: list[DTDevice] = []
    path_stack: list[str] = []
    node_props: dict[str, bytes] = {}
    offset = 0

    while offset + 4 <= len(struct_data):
        token = _read_u32_be(struct_data, offset)
        offset += 4

        if token == _FDT_BEGIN_NODE:
            # Read null-terminated node name
            name_end = struct_data.index(b"\x00", offset)
            name = struct_data[offset:name_end].decode("utf-8", errors="replace")
            offset = _align4(name_end + 1)
            path_stack.append(name or "/")
            node_props = {}

        elif token == _FDT_END_NODE:
            # Save device if it has compatible property
            if "compatible" in node_props and path_stack:
                raw = node_props["compatible"]
                # compatible is a null-separated list of strings
                parts = raw.split(b"\x00")
                compats = [p.decode("utf-8", errors="replace") for p in parts if p]
                if compats:
                    path = "/" + "/".join(path_stack)
                    devices.append(DTDevice(node_path=path, compatible=compats))
            if path_stack:
                path_stack.pop()
            node_props = {}

        elif token == _FDT_PROP:
            if offset + 8 > len(struct_data):
                break
            prop_len = _read_u32_be(struct_data, offset)
            name_off = _read_u32_be(struct_data, offset + 4)
            offset += 8
            prop_data = struct_data[offset: offset + prop_len]
            offset = _align4(offset + prop_len)
            prop_name = read_string(name_off)
            node_props[prop_name] = bytes(prop_data)

        elif token == _FDT_NOP:
            pass

        elif token == _FDT_END:
            break

    return devices


def _find_dtb_paths(dtb_hint: str | None) -> list[Path]:
    """Return candidate DTB file paths to try."""
    candidates: list[Path] = []
    if dtb_hint:
        candidates.append(Path(dtb_hint))
        return candidates

    # Common locations for DTB files
    search_dirs = [
        Path("/boot/dtb"),
        Path("/boot/dtbs"),
        Path("/boot"),
        Path("/"),
    ]
    for d in search_dirs:
        if d.exists():
            candidates.extend(sorted(d.glob("*.dtb"))[:5])
            candidates.extend(sorted(d.glob("**/*.dtb"))[:5])

    return candidates


def scan_dt(dtb_path: str | None = None) -> tuple[list[DTDevice], str | None]:
    """Scan Device Tree for compatible strings. Returns (devices, error_message)."""
    paths = _find_dtb_paths(dtb_path)
    if not paths:
        return [], "No DTB file found — use --dtb to specify one (ARM/embedded only)"

    for p in paths:
        try:
            data = p.read_bytes()
        except (PermissionError, FileNotFoundError, OSError):
            continue
        devices = parse_fdt(data)
        if devices is not None:
            return devices, None

    return [], "No valid FDT/DTB found — skipping Device Tree detection"
