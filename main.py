#!/usr/bin/env python3
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

"""
anaconda-gentoo — Gentoo Linux installer with hardware-aware kernel config.

Stage 1 (this run, from live environment):
  Hardware detect → partition → format → mount → stage3 → make.conf
  → portage sync → kernel config/compile → bootloader → reboot

Stage 3 (automatic, after first boot):
  emerge --sync → emerge @world → cleanup → disable agent
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from installer.state import InstallerState
from installer.tui import console, print_banner, print_summary, run_all_steps
from installer.steps.base import Step

from installer.steps.s01_hardware import HardwareDetectStep
from installer.steps.s02_partition import PartitionStep
from installer.steps.s03_format import FormatStep
from installer.steps.s04_mount import MountStep
from installer.steps.s05_stage3 import Stage3Step, Stage3ExtractStep
from installer.steps.s06_makeconf import MakeConfStep
from installer.steps.s07_chroot_init import (
    ChrootInitStep, PortageSyncStep, TimezoneStep, LocaleStep,
)
from installer.steps.s08_kernel import (
    KernelConfigStep, KernelCompileStep, KernelInstallStep,
)
from installer.steps.s09_bootloader import FstabStep, BootloaderStep
from installer.steps.s10_finalize import (
    HostnameStep, RootPasswordStep, NetworkStep,
    ContinuationAgentStep, UnmountStep,
)
from stage3_agent.agent import run_stage3


def build_stage1_steps(args: argparse.Namespace) -> list[Step]:
    return [
        HardwareDetectStep(),
        PartitionStep(
            disk=args.disk,
            boot_size=args.boot_size,
            swap_size=args.swap_size,
        ),
        FormatStep(root_fs=args.root_fs),
        MountStep(),
        Stage3Step(url=args.stage3_url),
        Stage3ExtractStep(),
        MakeConfStep(march=args.march, extra_use=args.use or []),
        ChrootInitStep(),
        PortageSyncStep(),
        TimezoneStep(timezone=args.timezone),
        LocaleStep(locale=args.locale),
        KernelConfigStep(
            kernel_src=args.kernel_src,
            cache_dir=args.cache_dir,
            driver_value=args.driver_value,
        ),
        KernelCompileStep(),
        KernelInstallStep(),
        FstabStep(),
        HostnameStep(hostname=args.hostname),
        RootPasswordStep(),
        NetworkStep(iface=args.network_iface, dhcp=not args.static_ip),
        BootloaderStep(),
        ContinuationAgentStep(),
        UnmountStep(),
    ]


def cmd_install(args: argparse.Namespace) -> None:
    print_banner()

    state = InstallerState.load()
    if args.mountpoint:
        state.mountpoint = args.mountpoint

    done, total = state.progress()
    if done > 0:
        console.print(
            f"[yellow]Resuming installation ({done}/{total} steps already done)[/yellow]\n"
        )
        print_summary(state)

    steps = build_stage1_steps(args)
    ok = run_all_steps(steps, state)

    if ok:
        console.print("\n[bold green]Stage 1 complete![/bold green]")
        console.print(
            "Reboot now — Stage 3 will continue automatically on first boot.\n"
        )


def cmd_continue(args: argparse.Namespace) -> None:
    """Called by the continuation agent after first boot."""
    state = InstallerState.load()

    if state.phase not in ("stage1_complete", "stage3"):
        console.print(f"[red]Unexpected phase: {state.phase!r}[/red]")
        sys.exit(1)

    state.phase = "stage3"
    state.save()
    run_stage3(state)


def cmd_status(args: argparse.Namespace) -> None:
    state = InstallerState.load()
    print_summary(state)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anaconda-gentoo",
        description="Gentoo Linux installer with hardware-aware kernel configuration",
    )
    sub = parser.add_subparsers(dest="command")

    # ── install ──────────────────────────────────────────────────────────────
    inst = sub.add_parser("install", help="Run Stage 1 installation (default)")
    inst.add_argument(
        "--kernel-src", "-k", metavar="DIR",
        help="Linux kernel source tree (required for kernel config)",
    )
    inst.add_argument(
        "--disk", metavar="DEV",
        help="Target disk (e.g. /dev/nvme0n1); auto-detected if omitted",
    )
    inst.add_argument(
        "--root-fs", default="ext4",
        choices=["ext4", "btrfs", "xfs", "f2fs"],
        help="Root filesystem type (default: ext4)",
    )
    inst.add_argument("--boot-size", default="512MiB", metavar="SIZE",
                      help="Boot partition size (default: 512MiB)")
    inst.add_argument("--swap-size", default="8GiB", metavar="SIZE",
                      help="Swap partition size (default: 8GiB)")
    inst.add_argument("--stage3-url", metavar="URL",
                      help="Stage3 tarball URL (auto-detected from Gentoo mirrors)")
    inst.add_argument("--hostname", default="gentoo",
                      help="System hostname (default: gentoo)")
    inst.add_argument("--timezone", default="UTC",
                      help="Timezone (default: UTC)")
    inst.add_argument("--locale", default="en_US.UTF-8 UTF-8",
                      help="Locale (default: en_US.UTF-8 UTF-8)")
    inst.add_argument("--march", default="native",
                      help="GCC -march value for CFLAGS (default: native)")
    inst.add_argument("--use", nargs="*", metavar="FLAG",
                      help="Extra USE flags to add to make.conf")
    inst.add_argument("--network-iface", default="eth0",
                      help="Primary network interface (default: eth0)")
    inst.add_argument("--static-ip", action="store_true",
                      help="Configure static IP instead of DHCP")
    inst.add_argument(
        "--driver-value", choices=["y", "m"], default="m",
        help="Kernel driver config value: y=built-in m=module (default: m)",
    )
    inst.add_argument("--cache-dir", metavar="DIR",
                      help="Cache kernel source index for faster re-runs")
    inst.add_argument("--mountpoint", default="/mnt/gentoo",
                      help="Install target mountpoint (default: /mnt/gentoo)")
    inst.add_argument("--dtb", metavar="FILE",
                      help="Device Tree blob (.dtb) for ARM/embedded systems")

    # ── continue ─────────────────────────────────────────────────────────────
    cont = sub.add_parser(
        "continue", help="Resume Stage 3 (runs automatically on first boot)"
    )

    # ── status ───────────────────────────────────────────────────────────────
    sub.add_parser("status", help="Show current installation progress")

    args = parser.parse_args()

    if args.command is None:
        # Default: run install with remaining argv
        args = parser.parse_args(["install"] + sys.argv[1:])
        cmd_install(args)
    elif args.command == "install":
        cmd_install(args)
    elif args.command == "continue":
        cmd_continue(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
