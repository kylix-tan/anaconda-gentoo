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

"""Step 3 — Format partitions."""
from __future__ import annotations

import subprocess
from installer.state import InstallerState
from installer.steps.base import Step, StepError


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


class FormatStep(Step):
    name = "format"
    description = "Formatting partitions"

    def __init__(self, root_fs: str = "ext4") -> None:
        self._root_fs = root_fs

    def execute(self, state: InstallerState) -> None:
        parts = state.get("partitions")
        if not parts:
            raise StepError("No partition info in state — run partition step first")

        uefi = parts.get("uefi", False)
        boot = parts["boot"]
        swap = parts.get("swap", "")
        root = parts["root"]
        root_fs = state.get("root_fs", self._root_fs)

        print(f"  Formatting boot ({boot}) as {'FAT32/EFI' if uefi else 'ext2'}...")
        if uefi:
            _run(["mkfs.fat", "-F32", boot])
        else:
            _run(["mkfs.ext2", boot])

        if swap:
            print(f"  Formatting swap ({swap})...")
            _run(["mkswap", swap])

        print(f"  Formatting root ({root}) as {root_fs}...")
        if root_fs == "ext4":
            _run(["mkfs.ext4", root])
        elif root_fs == "btrfs":
            _run(["mkfs.btrfs", "-f", root])
        elif root_fs == "xfs":
            _run(["mkfs.xfs", "-f", root])
        elif root_fs == "f2fs":
            _run(["mkfs.f2fs", root])
        else:
            raise StepError(f"Unsupported root filesystem: {root_fs!r}")

        state.set("root_fs", root_fs)
        state.set("partitions", parts)
