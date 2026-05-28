# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024  anaconda-gentoo contributors
"""Tests for kconfig_builder.bootpath — boot chain static config locking."""
import pytest
from kconfig_builder.bootpath import BootPath, apply_boot_path
from kconfig_builder.generator import ConfigGenerator


class TestBootPath:

    def _bp(self, device, fs, **kw):
        return BootPath(
            root_device=device,
            root_fs=fs,
            underlying_device=device.rsplit("p", 1)[0] if "nvme" in device else device[:-1],
            **kw,
        )

    # ── filesystem detection ────────────────────────────────────────────────

    def test_ext4_included(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4")
        assert "EXT4_FS" in bp.static_configs()

    def test_btrfs_included(self):
        bp = self._bp("/dev/sda2", "btrfs")
        assert "BTRFS_FS" in bp.static_configs()

    def test_xfs_included(self):
        bp = self._bp("/dev/sda2", "xfs")
        assert "XFS_FS" in bp.static_configs()

    def test_unknown_fs_raises(self):
        bp = self._bp("/dev/sda2", "reiser4")
        with pytest.raises(ValueError, match="Unknown root filesystem"):
            bp.static_configs()

    # ── storage controller detection ────────────────────────────────────────

    def test_nvme_configs(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4")
        static = bp.static_configs()
        assert "BLK_DEV_NVME" in static
        assert "NVME_CORE" in static

    def test_sata_configs(self):
        bp = BootPath("/dev/sda2", "ext4", "/dev/sda")
        static = bp.static_configs()
        assert "AHCI" in static
        assert "LIBATA" in static

    def test_virtio_configs(self):
        bp = BootPath("/dev/vda2", "ext4", "/dev/vda")
        static = bp.static_configs()
        assert "VIRTIO_BLK" in static
        assert "VIRTIO_PCI" in static

    def test_emmc_configs(self):
        bp = BootPath("/dev/mmcblk0p2", "ext4", "/dev/mmcblk0")
        static = bp.static_configs()
        assert "MMC" in static
        assert "MMC_BLOCK" in static

    # ── optional layers ─────────────────────────────────────────────────────

    def test_luks_configs(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4", has_luks=True)
        static = bp.static_configs()
        assert "DM_CRYPT" in static
        assert "BLK_DEV_DM" in static
        assert "CRYPTO_AES" in static
        assert "CRYPTO_XTS" in static

    def test_lvm_configs(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4", has_lvm=True)
        static = bp.static_configs()
        assert "BLK_DEV_DM" in static
        assert "DM_MOD" in static

    def test_raid_configs(self):
        bp = self._bp("/dev/sda2", "ext4", has_raid=True)
        static = bp.static_configs()
        assert "MD" in static
        assert "MD_RAID1" in static

    def test_all_layers_combined(self):
        bp = self._bp("/dev/nvme0n1p3", "btrfs",
                      has_lvm=True, has_luks=True, has_raid=True)
        static = bp.static_configs()
        assert "BTRFS_FS" in static
        assert "BLK_DEV_NVME" in static
        assert "DM_CRYPT" in static
        assert "BLK_DEV_DM" in static
        assert "MD" in static

    # ── always-present symbols ──────────────────────────────────────────────

    def test_always_present(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4")
        static = bp.static_configs()
        assert "BLOCK" in static
        assert "BLK_DEV" in static

    # ── generator integration ───────────────────────────────────────────────

    def test_apply_locks_to_y(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4")
        gen = ConfigGenerator(detected_acpi=False)
        apply_boot_path(gen, bp)
        # Simulate something else trying to set it to 'm'
        gen.add("EXT4_FS", "m", "hardware")
        import io
        buf = io.StringIO()
        gen.write(buf)
        assert "CONFIG_EXT4_FS=y" in buf.getvalue()
        assert "CONFIG_EXT4_FS=m" not in buf.getvalue()

    def test_apply_returns_symbol_set(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4", has_luks=True)
        gen = ConfigGenerator(detected_acpi=False)
        locked = apply_boot_path(gen, bp)
        assert isinstance(locked, set)
        assert len(locked) > 5
        assert "EXT4_FS" in locked

    def test_summary(self):
        bp = self._bp("/dev/nvme0n1p3", "ext4", has_lvm=True, has_luks=True)
        s = bp.summary()
        assert "ext4" in s
        assert "LVM" in s
        assert "LUKS" in s
