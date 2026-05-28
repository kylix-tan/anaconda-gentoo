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

"""Step 8 — Kernel: config generation, compile, install."""
from __future__ import annotations

import multiprocessing
import subprocess
from pathlib import Path

from installer.state import InstallerState
from installer.steps.base import Step, StepError
from installer.chroot import chroot_context, chroot_run
from kconfig_builder.matchers import build_source_index, MakefileDB
from kconfig_builder.kconfig import KconfigDB
from kconfig_builder.generator import ConfigGenerator
from kconfig_builder.bootpath import BootPath, apply_boot_path
from kconfig_builder.hardware.pci import PCIDevice
from kconfig_builder.hardware.usb import USBDevice


def _rebuild_devices(hw: dict) -> tuple[list[PCIDevice], list[USBDevice]]:
    """Reconstruct device objects from saved hardware state."""
    pci = []
    for d in hw.get("pci", []):
        pci.append(PCIDevice(
            slot=d["slot"], vendor=d["vendor"], device=d["device"],
            subvendor=d.get("subvendor", 0xFFFF),
            subdevice=d.get("subdevice", 0xFFFF),
            cls=d["cls"], header_type=0,
        ))
    usb = []
    for d in hw.get("usb", []):
        usb.append(USBDevice(
            bus=0, address=0,
            vendor=d["vendor"], product=d["product"],
            bcd_device=d.get("bcd_device", 0),
            dev_class=d["dev_class"],
            dev_subclass=d.get("dev_subclass", 0),
            dev_protocol=d.get("dev_protocol", 0),
            iface_class=d.get("iface_class", 0),
            iface_subclass=d.get("iface_subclass", 0),
            iface_protocol=d.get("iface_protocol", 0),
        ))
    return pci, usb


def _build_boot_path(state: InstallerState) -> BootPath:
    parts = state.get("partitions", {})
    root_part = parts.get("root", "")
    disk = state.get("disk", "")
    root_fs = state.get("root_fs", "ext4")

    return BootPath(
        root_device=root_part,
        root_fs=root_fs,
        underlying_device=disk,
        has_lvm=state.get("has_lvm", False),
        has_luks=state.get("has_luks", False),
        has_raid=state.get("has_raid", False),
    )


class KernelConfigStep(Step):
    name = "kernel_config"
    description = "Generating kernel configuration"

    def __init__(
        self,
        kernel_src: str | None = None,
        cache_dir: str | None = None,
        driver_value: str = "m",
    ) -> None:
        self._kernel_src = kernel_src
        self._cache_dir = cache_dir
        self._driver_value = driver_value

    def execute(self, state: InstallerState) -> None:
        kernel_src = Path(self._kernel_src or state.get("kernel_src", ""))
        if not kernel_src.is_dir():
            raise StepError(
                f"Kernel source not found at {kernel_src}. "
                "Set --kernel-src or state.config.kernel_src"
            )

        cache_path = None
        if self._cache_dir:
            cache_path = Path(self._cache_dir) / "source_index.json"

        print("  Indexing kernel source (may take 30–90s)...")
        src_index = build_source_index(kernel_src, cache_path=cache_path)
        makefile_db = MakefileDB(kernel_src)
        makefile_db.build()

        hw = state.get("hardware", {})
        pci_devices, usb_devices = _rebuild_devices(hw)
        acpi_info_dict = hw.get("acpi", {})

        # Match devices → CONFIG symbols
        config_symbols: set[str] = set()
        all_aliases = (
            [d.alias for d in pci_devices] +
            [d.alias for d in usb_devices]
        )
        for alias in all_aliases:
            for entry in src_index.match(alias):
                config_symbols.update(makefile_db.get_configs(entry.module))

        if acpi_info_dict.get("has_acpi"):
            config_symbols.add("ACPI")
            if "HPET" in acpi_info_dict.get("tables", []):
                config_symbols.add("HPET_TIMER")
            if "APIC" in acpi_info_dict.get("tables", []):
                config_symbols.update(["X86_LOCAL_APIC", "X86_IO_APIC"])
            if "MCFG" in acpi_info_dict.get("tables", []):
                config_symbols.add("PCI_MMCONFIG")

        # Resolve Kconfig dependencies
        print(f"  Matched {len(config_symbols)} CONFIG symbols, resolving deps...")
        kdb = KconfigDB(kernel_src)
        kdb.load()
        config_symbols = kdb.resolve_dependencies(config_symbols)

        # Build generator and lock boot path symbols to =y
        gen = ConfigGenerator(detected_acpi=acpi_info_dict.get("has_acpi", False))
        gen.add_many(config_symbols, value=self._driver_value, section="hardware")

        boot_path = _build_boot_path(state)
        static = apply_boot_path(gen, boot_path)
        print(f"  Boot path: {boot_path.summary()}")
        print(f"  Locked {len(static)} symbols as =y (boot-critical)")

        # Write .config into kernel source tree
        dot_config = kernel_src / ".config"
        gen.save(dot_config, meta={"generator": "anaconda-gentoo"})
        print(f"  Written {dot_config} ({gen.total_symbols} entries)")

        # Run olddefconfig to fill in remaining options
        subprocess.run(
            ["make", "olddefconfig"],
            cwd=str(kernel_src), check=True, capture_output=True,
        )
        print("  make olddefconfig OK")

        state.set("kernel_src", str(kernel_src))
        state.set("kernel_config_symbols", len(config_symbols))


class KernelCompileStep(Step):
    name = "kernel_compile"
    description = "Compiling kernel"

    def execute(self, state: InstallerState) -> None:
        kernel_src = Path(state.get("kernel_src", ""))
        if not kernel_src.is_dir():
            raise StepError("Kernel source path not set — run kernel_config step first")

        jobs = multiprocessing.cpu_count()
        print(f"  make -j{jobs} (this will take a while)...")
        result = subprocess.run(
            ["make", f"-j{jobs}"],
            cwd=str(kernel_src),
            check=False,
        )
        if result.returncode != 0:
            raise StepError(f"Kernel compilation failed (exit {result.returncode})")
        print("  Kernel compiled OK")


class KernelInstallStep(Step):
    name = "kernel_install"
    description = "Installing kernel and modules"

    def execute(self, state: InstallerState) -> None:
        kernel_src = Path(state.get("kernel_src", ""))
        mp = Path(state.mountpoint)

        print("  Installing modules...")
        subprocess.run(
            ["make", f"INSTALL_MOD_PATH={mp}", "modules_install"],
            cwd=str(kernel_src), check=True, capture_output=True,
        )

        print("  Installing kernel image...")
        boot_dir = Path(state.get("boot_dir", str(mp / "boot")))
        boot_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            ["make", f"INSTALL_PATH={boot_dir}", "install"],
            cwd=str(kernel_src), check=True, capture_output=True,
        )
        print(f"  Kernel installed to {boot_dir}")
