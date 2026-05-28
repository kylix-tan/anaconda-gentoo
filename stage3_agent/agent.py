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

"""Stage 3 continuation agent — runs after first boot into new system."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from installer.state import InstallerState, STAGE3_STEPS
from installer.steps.base import StepError


_LOG = Path("/var/log/anaconda-gentoo-stage3.log")


def _emerge(args: list[str]) -> int:
    cmd = ["emerge"] + args
    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    return result.returncode


def run_stage3(state: InstallerState) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("anaconda-gentoo: Stage 3 continuation")
    print("=" * 60)

    done, total = state.progress()
    print(f"Stage 1 completed. Running Stage 3 ({total} steps).\n")

    # ── portage_update ──────────────────────────────────────────────────────
    if not state.is_done("portage_update"):
        state.mark_started("portage_update")
        print("[1/4] Syncing Portage tree...")
        rc = _emerge(["--sync", "--quiet"])
        if rc != 0:
            # Non-fatal — mirror might be slow
            print("  WARNING: emerge --sync failed, continuing with existing tree")
        state.mark_done("portage_update")

    # ── world_update ────────────────────────────────────────────────────────
    if not state.is_done("world_update"):
        state.mark_started("world_update")
        print("[2/4] Running emerge @world (this will take hours)...")
        rc = _emerge(["--update", "--deep", "--newuse", "--quiet-build", "@world"])
        if rc != 0:
            # Try --resume in case of transient failures
            print("  Trying emerge --resume ...")
            rc = _emerge(["--resume", "--skipfirst"])
        if rc != 0:
            state.mark_failed("world_update", f"emerge @world failed (exit {rc})")
            print(f"  ERROR: emerge @world failed. Fix manually and re-run:")
            print("    anaconda-gentoo --continue")
            sys.exit(1)
        state.mark_done("world_update")

    # ── cleanup ─────────────────────────────────────────────────────────────
    if not state.is_done("cleanup"):
        state.mark_started("cleanup")
        print("[3/4] Cleaning up orphan packages...")
        _emerge(["--depclean", "--quiet"])
        state.mark_done("cleanup")

    # ── disable_agent ───────────────────────────────────────────────────────
    if not state.is_done("disable_agent"):
        state.mark_started("disable_agent")
        print("[4/4] Disabling continuation agent...")
        init = state.get("init_system", "openrc")
        if init == "systemd":
            subprocess.run(
                ["systemctl", "disable", "--now", "anaconda-gentoo-continue"],
                check=False,
            )
        else:
            subprocess.run(
                ["rc-update", "del", "anaconda-gentoo-continue", "default"],
                check=False,
            )
        state.mark_done("disable_agent")

    print("\n" + "=" * 60)
    print("Stage 3 complete — Gentoo installation finished!")
    print("=" * 60)
    state.phase = "complete"
    state.save()
