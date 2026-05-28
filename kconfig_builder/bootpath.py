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

"""Boot path analysis — determines which CONFIG symbols must be =y.

Every driver in the chain from PCI bus → block device → rootfs
must be built statically into the kernel. Modules cannot be loaded
before the root filesystem is mounted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Filesystem type → CONFIG symbol
_FS_CONFIG: dict[str, str] = {
    "ext2":    "EXT2_FS",
    "ext3":    "EXT3_FS",
    "ext4":    "EXT4_FS",
    "btrfs":   "BTRFS_FS",
    "xfs":     "XFS_FS",
    "f2fs":    "F2FS_FS",
    "jfs":     "JFS_FS",
    "reiserfs":"REISERFS_FS",
    "vfat":    "VFAT_FS",
    "fat":     "FAT_FS",
}

# Block device path pattern → required CONFIG symbols
_DEVICE_CONFIGS: list[tuple[re.Pattern, set[str]]] = [
    (re.compile(r"/dev/nvme"),   {"BLK_DEV_NVME", "NVME_CORE", "PCI"}),
    (re.compile(r"/dev/sd"),     {"ATA", "ATA_PIIX", "AHCI", "LIBAHCI", "LIBATA", "PCI"}),
    (re.compile(r"/dev/hd"),     {"IDE", "BLK_DEV_IDE", "ATA"}),
    (re.compile(r"/dev/vd"),     {"VIRTIO", "VIRTIO_PCI", "VIRTIO_BLK"}),
    (re.compile(r"/dev/mmcblk"), {"MMC", "MMC_BLOCK", "MMC_SDHCI"}),
    (re.compile(r"/dev/xvd"),    {"XEN_BLKDEV_FRONTEND"}),
    (re.compile(r"/dev/mapper"), set()),   # handled by LVM/LUKS flags
]

# Crypto algorithms needed for LUKS (AES-XTS is the default for cryptsetup)
_LUKS_CRYPTO: set[str] = {
    "BLK_DEV_DM", "DM_MOD", "DM_CRYPT",
    "CRYPTO", "CRYPTO_AES", "CRYPTO_XTS",
    "CRYPTO_CBC", "CRYPTO_SHA256",
    "CRYPTO_AES_X86_64",   # x86_64 AES-NI acceleration
}

_LVM_CONFIGS: set[str] = {
    "BLK_DEV_DM", "DM_MOD",
    "MD",                   # device mapper depends on MD in some configs
}

_RAID_CONFIGS: set[str] = {
    "MD", "BLK_DEV_MD",
    "MD_RAID0", "MD_RAID1", "MD_RAID10", "MD_RAID456",
}

# Always required for any bootable kernel
_ALWAYS_STATIC: set[str] = {
    "BLOCK", "BLK_DEV", "PCI_MSI",
    "GENERIC_IRQ_CHIP", "GENERIC_IRQ_PROBE",
}


@dataclass
class BootPath:
    """Describes the storage path to the root filesystem.

    Set from installer partition/format choices, then passed to the
    kernel config generator so all boot-critical drivers are =y.
    """
    root_device: str        # e.g. "/dev/nvme0n1p3" or "/dev/mapper/vg0-root"
    root_fs: str            # e.g. "ext4", "btrfs"
    underlying_device: str  # physical device, e.g. "/dev/nvme0n1"
    has_lvm: bool = False
    has_luks: bool = False
    has_raid: bool = False
    luks_cipher: str = "aes-xts-plain64"  # cryptsetup default

    # Extra static configs the installer wants to force =y
    extra_static: set[str] = field(default_factory=set)

    def static_configs(self) -> set[str]:
        """Return the full set of CONFIG symbols that MUST be =y."""
        configs: set[str] = set(_ALWAYS_STATIC)

        # Root filesystem
        fs_cfg = _FS_CONFIG.get(self.root_fs.lower())
        if fs_cfg:
            configs.add(fs_cfg)
        else:
            raise ValueError(f"Unknown root filesystem type: {self.root_fs!r}")

        # Block device type (use underlying physical device)
        dev = self.underlying_device or self.root_device
        for pattern, syms in _DEVICE_CONFIGS:
            if pattern.search(dev):
                configs.update(syms)
                break

        # LVM
        if self.has_lvm:
            configs.update(_LVM_CONFIGS)

        # LUKS / dm-crypt
        if self.has_luks:
            configs.update(_LUKS_CRYPTO)
            # Add cipher-specific modules
            cipher = self.luks_cipher.split("-")[0].upper()
            configs.add(f"CRYPTO_{cipher}")

        # Software RAID
        if self.has_raid:
            configs.update(_RAID_CONFIGS)

        # Caller overrides
        configs.update(self.extra_static)

        return configs

    def summary(self) -> str:
        flags = []
        if self.has_lvm:
            flags.append("LVM")
        if self.has_luks:
            flags.append("LUKS")
        if self.has_raid:
            flags.append("RAID")
        flag_str = "+" + "+".join(flags) if flags else ""
        return f"{self.root_device} ({self.root_fs}{flag_str})"


def apply_boot_path(
    generator: object,   # ConfigGenerator from generator.py
    boot_path: BootPath,
) -> set[str]:
    """Force all boot-critical configs to =y in the generator.

    Returns the set of symbols that were locked static.
    """
    static = boot_path.static_configs()
    for sym in static:
        generator.add(sym, value="y", section="boot-critical")  # type: ignore[attr-defined]
    return static
