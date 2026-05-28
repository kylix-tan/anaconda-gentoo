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

"""Installer state machine with JSON persistence."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# State file is written to both locations so it survives the reboot
_LIVE_STATE = Path("/var/lib/anaconda-gentoo/state.json")
_TARGET_REL  = Path("var/lib/anaconda-gentoo/state.json")  # relative to mountpoint

STAGE1_STEPS = [
    "hardware_detect",
    "disk_select",
    "partition",
    "format",
    "mount",
    "stage3_download",
    "stage3_extract",
    "makeconf",
    "chroot_init",
    "portage_sync",
    "timezone",
    "locale",
    "kernel_config",
    "kernel_compile",
    "kernel_install",
    "fstab",
    "hostname",
    "root_password",
    "network",
    "bootloader",
    "continuation_agent",
    "unmount",
]

STAGE3_STEPS = [
    "portage_update",
    "world_update",
    "cleanup",
    "disable_agent",
]


@dataclass
class InstallerState:
    phase: str = "stage1"                    # "stage1" | "stage3"
    completed: list[str] = field(default_factory=list)
    current: str | None = None
    failed: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    mountpoint: str = "/mnt/gentoo"
    log: list[str] = field(default_factory=list)

    # ── persistence ────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path | None = None) -> "InstallerState":
        for candidate in _candidates(path):
            if candidate.exists():
                try:
                    data = json.loads(candidate.read_text())
                    return cls(**data)
                except Exception:
                    pass
        return cls()

    def save(self, path: Path | None = None) -> None:
        data = json.dumps(asdict(self), indent=2)
        for candidate in _candidates(path):
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(data)
            except OSError:
                pass
        # Mirror into target filesystem if mounted
        target = Path(self.mountpoint) / _TARGET_REL
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data)
        except OSError:
            pass

    # ── step control ───────────────────────────────────────────────────────

    def is_done(self, step: str) -> bool:
        return step in self.completed

    def mark_started(self, step: str) -> None:
        self.current = step
        self.failed = None
        self._append_log(f"START {step}")
        self.save()

    def mark_done(self, step: str) -> None:
        if step not in self.completed:
            self.completed.append(step)
        self.current = None
        self._append_log(f"DONE  {step}")
        self.save()

    def mark_failed(self, step: str, reason: str) -> None:
        self.failed = step
        self.current = None
        self._append_log(f"FAIL  {step}: {reason}")
        self.save()

    # ── config helpers ─────────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value
        self.save()

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def pending_steps(self, phase: str | None = None) -> list[str]:
        steps = STAGE1_STEPS if (phase or self.phase) == "stage1" else STAGE3_STEPS
        return [s for s in steps if s not in self.completed]

    def progress(self) -> tuple[int, int]:
        steps = STAGE1_STEPS if self.phase == "stage1" else STAGE3_STEPS
        return len(self.completed), len(steps)

    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")
        if len(self.log) > 500:
            self.log = self.log[-500:]


def _candidates(path: Path | None) -> list[Path]:
    if path:
        return [path]
    return [_LIVE_STATE]
