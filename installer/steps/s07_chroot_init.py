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

"""Step 7 — Chroot initialisation: DNS, portage tree, timezone, locale."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from installer.state import InstallerState
from installer.steps.base import Step, StepError
from installer.chroot import chroot_context, chroot_run


class ChrootInitStep(Step):
    name = "chroot_init"
    description = "Initialising chroot environment"

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)

        # Copy DNS configuration
        resolv = Path("/etc/resolv.conf")
        if resolv.exists():
            dest = mp / "etc" / "resolv.conf"
            shutil.copy2(str(resolv), str(dest))
            print("  Copied /etc/resolv.conf")

        # Ensure portage directory layout exists
        for d in ["package.use", "package.accept_keywords", "package.mask"]:
            (mp / "etc" / "portage" / d).mkdir(parents=True, exist_ok=True)

        print("  Chroot init OK")


class PortageSyncStep(Step):
    name = "portage_sync"
    description = "Syncing Portage tree"

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)
        with chroot_context(mp):
            print("  Running emerge-webrsync inside chroot ...")
            chroot_run(mp, ["emerge-webrsync"])
            print("  Portage tree synced")


class TimezoneStep(Step):
    name = "timezone"
    description = "Setting timezone"

    def __init__(self, timezone: str = "UTC") -> None:
        self._tz = timezone

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)
        tz = state.get("timezone", self._tz)

        tz_file = mp / "usr" / "share" / "zoneinfo" / tz
        if not tz_file.exists():
            raise StepError(f"Timezone not found: {tz}")

        (mp / "etc" / "timezone").write_text(tz + "\n")
        shutil.copy2(str(tz_file), str(mp / "etc" / "localtime"))
        print(f"  Timezone: {tz}")
        state.set("timezone", tz)


class LocaleStep(Step):
    name = "locale"
    description = "Configuring locale"

    def __init__(self, locale: str = "en_US.UTF-8 UTF-8") -> None:
        self._locale = locale

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)
        locale = state.get("locale", self._locale)

        locale_gen = mp / "etc" / "locale.gen"
        existing = locale_gen.read_text() if locale_gen.exists() else ""
        if locale not in existing:
            with open(locale_gen, "a") as f:
                f.write(f"{locale}\n")

        with chroot_context(mp):
            chroot_run(mp, ["locale-gen"])

        # Set /etc/locale.conf
        lang = locale.split()[0]
        (mp / "etc" / "locale.conf").write_text(f"LANG={lang}\n")
        print(f"  Locale: {lang}")
        state.set("locale", locale)
