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

"""Step 4 — Mount partitions."""
from __future__ import annotations

import subprocess
from pathlib import Path
from installer.state import InstallerState
from installer.steps.base import Step, StepError


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def _is_mounted(path: Path) -> bool:
    result = subprocess.run(
        ["mountpoint", "-q", str(path)],
        check=False, capture_output=True,
    )
    return result.returncode == 0


class MountStep(Step):
    name = "mount"
    description = "Mounting partitions"

    def execute(self, state: InstallerState) -> None:
        parts = state.get("partitions")
        if not parts:
            raise StepError("No partition info — run partition step first")

        mp = Path(state.mountpoint)
        root = parts["root"]
        boot = parts["boot"]
        swap = parts.get("swap", "")
        uefi = parts.get("uefi", False)

        mp.mkdir(parents=True, exist_ok=True)

        if not _is_mounted(mp):
            print(f"  Mounting root {root} → {mp}")
            _run(["mount", root, str(mp)])

        boot_dir = mp / ("boot/efi" if uefi else "boot")
        boot_dir.mkdir(parents=True, exist_ok=True)

        if not _is_mounted(boot_dir):
            print(f"  Mounting boot {boot} → {boot_dir}")
            _run(["mount", boot, str(boot_dir)])

        if swap:
            print(f"  Activating swap {swap}")
            subprocess.run(["swapon", swap], check=False, capture_output=True)

        state.set("boot_dir", str(boot_dir))
