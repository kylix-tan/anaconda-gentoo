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

"""Step 2 — Disk selection and partitioning."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from installer.state import InstallerState
from installer.steps.base import Step, StepError


@dataclass
class PartitionPlan:
    disk: str           # e.g. "/dev/nvme0n1"
    boot_part: str      # e.g. "/dev/nvme0n1p1"  (ESP or /boot)
    swap_part: str      # e.g. "/dev/nvme0n1p2"  (empty string = no swap)
    root_part: str      # e.g. "/dev/nvme0n1p3"
    boot_size: str      # e.g. "512MiB"
    swap_size: str      # e.g. "8GiB"  (empty = no swap)
    uefi: bool          # True = GPT+EFI, False = MBR+BIOS


def list_disks() -> list[dict]:
    """Return list of block devices suitable as install targets."""
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--output",
             "NAME,SIZE,TYPE,MOUNTPOINT,MODEL,TRAN"],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        disks = []
        for dev in data.get("blockdevices", []):
            if dev.get("type") == "disk" and not dev.get("mountpoint"):
                disks.append({
                    "name": f"/dev/{dev['name']}",
                    "size": dev.get("size", "?"),
                    "model": dev.get("model", ""),
                    "tran": dev.get("tran", ""),
                })
        return disks
    except (subprocess.CalledProcessError, FileNotFoundError, KeyError):
        # Fallback: read from /sys/block
        disks = []
        for entry in sorted(Path("/sys/block").iterdir()):
            if entry.name.startswith(("sd", "nvme", "vd", "mmcblk", "hd")):
                size_path = entry / "size"
                try:
                    blocks = int(size_path.read_text().strip())
                    size_gb = blocks * 512 // (1024 ** 3)
                    disks.append({"name": f"/dev/{entry.name}",
                                  "size": f"{size_gb}G", "model": "", "tran": ""})
                except (FileNotFoundError, ValueError):
                    pass
        return disks


def _detect_uefi() -> bool:
    return Path("/sys/firmware/efi").exists()


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def _wipe_disk(disk: str) -> None:
    _run(["wipefs", "--all", disk])
    _run(["sgdisk", "--zap-all", disk])


def _create_gpt_layout(plan: PartitionPlan) -> None:
    """GPT layout: EFI (ESP) + optional swap + root."""
    disk = plan.disk
    _wipe_disk(disk)

    cmds: list[str] = [
        f"--new=1:0:+{plan.boot_size}",
        "--typecode=1:ef00",          # EFI System Partition
        "--change-name=1:EFI",
    ]
    part_num = 1

    if plan.swap_size:
        part_num += 1
        cmds += [
            f"--new={part_num}:0:+{plan.swap_size}",
            f"--typecode={part_num}:8200",
            f"--change-name={part_num}:swap",
        ]

    part_num += 1
    cmds += [
        f"--new={part_num}:0:0",      # root: remaining space
        f"--typecode={part_num}:8300",
        f"--change-name={part_num}:root",
    ]

    _run(["sgdisk"] + cmds + [disk])
    _run(["partprobe", disk])


def _create_mbr_layout(plan: PartitionPlan) -> None:
    """MBR layout: /boot + optional swap + root."""
    disk = plan.disk
    _wipe_disk(disk)
    script_lines = ["o", "n", "p", "1", "", f"+{plan.boot_size}", "a", "1"]
    part_num = 1

    if plan.swap_size:
        part_num += 1
        script_lines += ["n", "p", str(part_num), "", f"+{plan.swap_size}",
                         "t", str(part_num), "82"]
    part_num += 1
    script_lines += ["n", "p", str(part_num), "", "", "w"]

    proc = subprocess.Popen(
        ["fdisk", disk],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    proc.communicate("\n".join(script_lines) + "\n")
    _run(["partprobe", disk])


def _partition_numbers(disk: str) -> list[str]:
    """Return sorted list of partition device paths for a disk."""
    import re
    base = Path(disk).name
    parts = sorted(
        str(p) for p in Path("/dev").iterdir()
        if re.match(rf"^{re.escape(base)}p?\d+$", p.name)
    )
    return parts


class PartitionStep(Step):
    name = "disk_select"
    description = "Partitioning disk"

    def __init__(
        self,
        disk: str | None = None,
        boot_size: str = "512MiB",
        swap_size: str = "8GiB",
        uefi: bool | None = None,
    ) -> None:
        self._disk = disk
        self._boot_size = boot_size
        self._swap_size = swap_size
        self._uefi = uefi

    def execute(self, state: InstallerState) -> None:
        disk = self._disk or state.get("disk")
        if not disk:
            disks = list_disks()
            if not disks:
                raise StepError("No suitable disks found")
            disk = disks[0]["name"]
            print(f"  Auto-selected disk: {disk}")

        uefi = self._uefi if self._uefi is not None else _detect_uefi()
        print(f"  Boot mode: {'UEFI' if uefi else 'BIOS'}")
        print(f"  Disk: {disk}  boot={self._boot_size}  swap={self._swap_size}")

        parts = _partition_numbers(disk)
        # Determine partition names after creation
        sep = "p" if "nvme" in disk or "mmcblk" in disk else ""
        boot_part = f"{disk}{sep}1"
        if self._swap_size:
            swap_part = f"{disk}{sep}2"
            root_part = f"{disk}{sep}3"
        else:
            swap_part = ""
            root_part = f"{disk}{sep}2"

        plan = PartitionPlan(
            disk=disk,
            boot_part=boot_part,
            swap_part=swap_part,
            root_part=root_part,
            boot_size=self._boot_size,
            swap_size=self._swap_size,
            uefi=uefi,
        )

        if uefi:
            _create_gpt_layout(plan)
        else:
            _create_mbr_layout(plan)

        state.set("disk", disk)
        state.set("partitions", {
            "boot": boot_part,
            "swap": swap_part,
            "root": root_part,
            "uefi": uefi,
        })
        print(f"  Partitions: boot={boot_part} swap={swap_part or 'none'} root={root_part}")
