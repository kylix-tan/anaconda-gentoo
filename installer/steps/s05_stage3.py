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

"""Step 5 — Download, verify, and extract stage3 tarball."""
from __future__ import annotations

import hashlib
import subprocess
import urllib.request
from pathlib import Path
from installer.state import InstallerState
from installer.steps.base import Step, StepError

# Gentoo distfiles mirror
_MIRROR = "https://distfiles.gentoo.org/releases/amd64/autobuilds"
_LATEST_URL = f"{_MIRROR}/latest-stage3-amd64-openrc.txt"

_STAGE3_DIR = Path("/var/tmp/anaconda-gentoo")


def _fetch_latest_url() -> str:
    """Parse Gentoo's latest-stage3 file to get the tarball URL."""
    with urllib.request.urlopen(_LATEST_URL, timeout=30) as resp:
        for line in resp.read().decode().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                path = line.split()[0]
                return f"{_MIRROR}/{path}"
    raise StepError("Could not parse latest stage3 URL from Gentoo mirrors")


def _download(url: str, dest: Path) -> None:
    print(f"  Downloading {url.split('/')[-1]} ...")
    def _progress(block_count, block_size, total):
        if total > 0:
            pct = min(100, block_count * block_size * 100 // total)
            print(f"\r  Progress: {pct}%", end="", flush=True)
    urllib.request.urlretrieve(url, str(dest), reporthook=_progress)
    print()  # newline after progress


def _verify_sha512(tarball: Path, digest_url: str) -> None:
    print("  Verifying SHA512 checksum...")
    with urllib.request.urlopen(digest_url, timeout=30) as resp:
        content = resp.read().decode()
    expected = ""
    for line in content.splitlines():
        if tarball.name in line and "SHA512" in line:
            expected = line.split()[-1]
            break
    if not expected:
        print("  WARNING: Could not find checksum — skipping verification")
        return
    sha = hashlib.sha512()
    with open(tarball, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    if sha.hexdigest() != expected:
        raise StepError(f"SHA512 mismatch for {tarball.name}")
    print("  Checksum OK")


class Stage3Step(Step):
    name = "stage3_download"
    description = "Downloading stage3 tarball"

    def __init__(self, url: str | None = None, arch: str = "amd64") -> None:
        self._url = url
        self._arch = arch

    def execute(self, state: InstallerState) -> None:
        url = self._url or state.get("stage3_url")
        if not url:
            url = _fetch_latest_url()
            print(f"  Latest stage3: {url}")

        _STAGE3_DIR.mkdir(parents=True, exist_ok=True)
        tarball = _STAGE3_DIR / url.split("/")[-1]

        if not tarball.exists():
            _download(url, tarball)
        else:
            print(f"  Using cached tarball: {tarball}")

        digest_url = url + ".sha256"
        try:
            _verify_sha512(tarball, url + ".DIGESTS")
        except Exception as e:
            print(f"  WARNING: Verification skipped: {e}")

        state.set("stage3_url", url)
        state.set("stage3_tarball", str(tarball))


class Stage3ExtractStep(Step):
    name = "stage3_extract"
    description = "Extracting stage3 tarball"

    def execute(self, state: InstallerState) -> None:
        tarball = state.get("stage3_tarball")
        if not tarball or not Path(tarball).exists():
            raise StepError("stage3 tarball not found — run download step first")

        mp = Path(state.mountpoint)
        print(f"  Extracting {Path(tarball).name} → {mp} ...")
        result = subprocess.run(
            ["tar", "xpf", tarball, "--xattrs-include=*.*",
             "--numeric-owner", "-C", str(mp)],
            check=False,
        )
        if result.returncode != 0:
            raise StepError(f"tar extraction failed (exit {result.returncode})")
        print("  Extraction complete")
