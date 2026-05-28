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

"""Step 1 — Hardware detection."""
from __future__ import annotations

from installer.state import InstallerState
from installer.steps.base import Step, StepError
from kconfig_builder.hardware.pci import scan_pci
from kconfig_builder.hardware.usb import scan_usb
from kconfig_builder.hardware.acpi import scan_acpi
from kconfig_builder.hardware.dt import scan_dt


class HardwareDetectStep(Step):
    name = "hardware_detect"
    description = "Detecting hardware"

    def execute(self, state: InstallerState) -> None:
        pci_devices, pci_err = scan_pci()
        if pci_err:
            print(f"  WARNING: {pci_err}")

        usb_devices, usb_err = scan_usb()
        if usb_err:
            print(f"  WARNING: {usb_err}")

        acpi_info, acpi_err = scan_acpi()
        if acpi_err:
            print(f"  WARNING: {acpi_err}")

        dtb_path = state.get("dtb_path")
        dt_devices, _ = scan_dt(dtb_path)

        state.set("hardware", {
            "pci": [{"slot": d.slot, "vendor": d.vendor, "device": d.device,
                     "cls": d.cls, "alias": d.alias} for d in pci_devices],
            "usb": [{"vendor": d.vendor, "product": d.product,
                     "dev_class": d.dev_class, "alias": d.alias} for d in usb_devices],
            "acpi": {"has_acpi": acpi_info.has_acpi,
                     "tables": acpi_info.table_sigs,
                     "has_mcfg": acpi_info.has_mcfg},
            "dt": [{"path": d.node_path, "compatible": d.compatible}
                   for d in dt_devices],
        })

        total = len(pci_devices) + len(usb_devices) + len(dt_devices)
        print(f"  Found {len(pci_devices)} PCI, {len(usb_devices)} USB, "
              f"{len(dt_devices)} DT devices")
        if acpi_info.has_acpi:
            print(f"  ACPI tables: {' '.join(acpi_info.table_sigs)}")
        if total == 0 and not acpi_info.has_acpi:
            raise StepError(
                "No devices detected — ensure /dev/port and /dev/mem are accessible (run as root)"
            )
