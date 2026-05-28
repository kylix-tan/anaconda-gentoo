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

"""PCI device detection via raw CF8/CFC port I/O (no sysfs)."""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Iterator

# PCI config space port addresses
_CONFIG_ADDRESS = 0xCF8
_CONFIG_DATA = 0xCFC

_PCI_ANY_ID = 0xFFFF


@dataclass(frozen=True)
class PCIDevice:
    slot: str          # "0000:BB:DD.F"
    vendor: int        # 16-bit
    device: int        # 16-bit
    subvendor: int     # 16-bit
    subdevice: int     # 16-bit
    cls: int           # 24-bit class code (base|sub|prog-if)
    header_type: int   # raw header type byte

    @property
    def base_class(self) -> int:
        return (self.cls >> 16) & 0xFF

    @property
    def sub_class(self) -> int:
        return (self.cls >> 8) & 0xFF

    @property
    def prog_if(self) -> int:
        return self.cls & 0xFF

    @property
    def alias(self) -> str:
        return (
            f"pci:v{self.vendor:08X}d{self.device:08X}"
            f"sv{self.subvendor:08X}sd{self.subdevice:08X}"
            f"bc{self.base_class:02X}sc{self.sub_class:02X}i{self.prog_if:02X}"
        )

    def __str__(self) -> str:
        return (
            f"PCI [{self.slot}] {self.vendor:04x}:{self.device:04x} "
            f"sub={self.subvendor:04x}:{self.subdevice:04x} "
            f"class={self.cls:06x}"
        )


class PCIScanner:
    """Enumerate PCI devices via legacy CF8/CFC port I/O."""

    def __init__(self) -> None:
        self._fd: int = -1

    def open(self) -> bool:
        try:
            self._fd = os.open("/dev/port", os.O_RDWR)
            return True
        except (PermissionError, FileNotFoundError, OSError):
            return False

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> "PCIScanner":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _read32(self, bus: int, dev: int, fn: int, offset: int) -> int:
        addr = 0x80000000 | (bus << 16) | (dev << 11) | (fn << 8) | (offset & 0xFC)
        os.lseek(self._fd, _CONFIG_ADDRESS, os.SEEK_SET)
        os.write(self._fd, struct.pack("<I", addr))
        os.lseek(self._fd, _CONFIG_DATA, os.SEEK_SET)
        raw = os.read(self._fd, 4)
        return struct.unpack("<I", raw)[0]

    def _read16(self, bus: int, dev: int, fn: int, offset: int) -> int:
        dw = self._read32(bus, dev, fn, offset & ~1)
        return (dw >> (8 * (offset & 2))) & 0xFFFF

    def _read8(self, bus: int, dev: int, fn: int, offset: int) -> int:
        dw = self._read32(bus, dev, fn, offset & ~3)
        return (dw >> (8 * (offset & 3))) & 0xFF

    def scan(self) -> list[PCIDevice]:
        if self._fd < 0:
            return []
        devices: list[PCIDevice] = []
        for bus in range(256):
            for dev in range(32):
                for fn in range(8):
                    try:
                        vid = self._read16(bus, dev, fn, 0x00)
                    except OSError:
                        continue
                    if vid == 0xFFFF:
                        if fn == 0:
                            break  # No device at slot, skip all functions
                        continue

                    did = self._read16(bus, dev, fn, 0x02)
                    cls_raw = self._read32(bus, dev, fn, 0x08)
                    cls = (cls_raw >> 8) & 0xFFFFFF
                    hdr = self._read8(bus, dev, fn, 0x0E)

                    # Subsystem IDs only valid for header type 0x00 (endpoint)
                    if (hdr & 0x7F) == 0x00:
                        svid = self._read16(bus, dev, fn, 0x2C)
                        sdid = self._read16(bus, dev, fn, 0x2E)
                    else:
                        svid = 0xFFFF
                        sdid = 0xFFFF

                    slot = f"0000:{bus:02x}:{dev:02x}.{fn:01x}"
                    devices.append(
                        PCIDevice(slot, vid, did, svid, sdid, cls, hdr)
                    )

                    if fn == 0 and not (hdr & 0x80):
                        break  # Single-function device
        return devices


def scan_pci() -> tuple[list[PCIDevice], str | None]:
    """Return (devices, error_message). error_message is None on success."""
    scanner = PCIScanner()
    if not scanner.open():
        return [], "Cannot open /dev/port — run as root for PCI detection"
    try:
        return scanner.scan(), None
    finally:
        scanner.close()
