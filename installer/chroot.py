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

"""Chroot context manager with bind-mount lifecycle."""
from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_BIND_MOUNTS = [
    ("proc",     "proc",  "proc",  []),
    ("sys",      "sys",   "sysfs", ["--make-rslave"]),
    ("dev",      "dev",   "devtmpfs", ["--rbind", "--make-rslave"]),
    ("dev/pts",  "dev/pts", "devpts", []),
    ("run",      "run",   "tmpfs", []),
]


def _is_mounted(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["mountpoint", "-q", str(path)],
            check=False, capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        with open("/proc/mounts") as f:
            return str(path) in f.read()


def mount_pseudofs(mountpoint: Path) -> None:
    """Bind-mount kernel pseudo-filesystems into the chroot."""
    for rel, src, fstype, extra_flags in _BIND_MOUNTS:
        target = mountpoint / rel
        target.mkdir(parents=True, exist_ok=True)

        if _is_mounted(target):
            continue

        if rel in ("dev", "run"):
            cmd = ["mount", "--rbind", f"/{src}", str(target)] + extra_flags
        elif rel == "dev/pts":
            if _is_mounted(target):
                continue
            cmd = ["mount", "-t", fstype, fstype, str(target)]
        else:
            cmd = ["mount", "-t", fstype, fstype, str(target)] + extra_flags

        subprocess.run(cmd, check=True, capture_output=True)

        if "--make-rslave" in extra_flags and rel in ("sys", "dev"):
            subprocess.run(
                ["mount", "--make-rslave", str(target)],
                check=True, capture_output=True,
            )


def umount_pseudofs(mountpoint: Path) -> None:
    """Unmount pseudo-filesystems in reverse order."""
    for rel, *_ in reversed(_BIND_MOUNTS):
        target = mountpoint / rel
        if _is_mounted(target):
            subprocess.run(
                ["umount", "-l", str(target)],
                check=False, capture_output=True,
            )


def chroot_run(
    mountpoint: Path,
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command inside the chroot."""
    base_env = {
        "HOME": "/root",
        "TERM": os.environ.get("TERM", "xterm"),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    if env:
        base_env.update(env)

    full_cmd = ["chroot", str(mountpoint)] + cmd
    return subprocess.run(
        full_cmd,
        env=base_env,
        capture_output=capture,
        check=check,
        text=True,
    )


@contextmanager
def chroot_context(mountpoint: Path) -> Iterator[None]:
    """Context manager: mount pseudofs, yield, unmount."""
    mount_pseudofs(mountpoint)
    try:
        yield
    finally:
        umount_pseudofs(mountpoint)


def umount_all(mountpoint: Path) -> None:
    """Unmount everything under mountpoint in reverse order."""
    umount_pseudofs(mountpoint)
    result = subprocess.run(
        ["findmnt", "--raw", "--noheadings", "--output", "TARGET",
         "--submounts", str(mountpoint)],
        capture_output=True, text=True, check=False,
    )
    targets = sorted(result.stdout.strip().splitlines(), key=len, reverse=True)
    for t in targets:
        subprocess.run(["umount", "-l", t], check=False, capture_output=True)
    subprocess.run(["umount", "-l", str(mountpoint)], check=False, capture_output=True)
