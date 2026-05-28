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

"""Step 10 — Hostname, root password, network, continuation agent, unmount."""
from __future__ import annotations

import subprocess
from pathlib import Path
from installer.state import InstallerState
from installer.steps.base import Step, StepError
from installer.chroot import chroot_context, chroot_run, umount_all

_SYSTEMD_SERVICE = """\
[Unit]
Description=Gentoo Installer Stage 3 Continuation
After=network-online.target
Wants=network-online.target
ConditionPathExists=/var/lib/anaconda-gentoo/state.json

[Service]
Type=oneshot
ExecStart=/usr/local/bin/anaconda-gentoo --continue
RemainAfterExit=yes
StandardOutput=journal+console

[Install]
WantedBy=multi-user.target
"""

_OPENRC_SCRIPT = """\
#!/sbin/openrc-run
description="Gentoo Installer Stage 3 Continuation"
depend() { need net; }
start() {
    ebegin "Running anaconda-gentoo stage 3"
    /usr/local/bin/anaconda-gentoo --continue >> /var/log/anaconda-gentoo.log 2>&1
    eend $?
    rc-update del anaconda-gentoo-continue default
}
"""


class HostnameStep(Step):
    name = "hostname"
    description = "Setting hostname"

    def __init__(self, hostname: str = "gentoo") -> None:
        self._hostname = hostname

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)
        hostname = state.get("hostname", self._hostname)
        (mp / "etc" / "hostname").write_text(hostname + "\n")

        # /etc/hosts
        hosts = mp / "etc" / "hosts"
        content = hosts.read_text() if hosts.exists() else ""
        if hostname not in content:
            with open(hosts, "a") as f:
                f.write(f"127.0.1.1\t{hostname}.localdomain\t{hostname}\n")

        print(f"  Hostname: {hostname}")
        state.set("hostname", hostname)


class RootPasswordStep(Step):
    name = "root_password"
    description = "Setting root password"

    def __init__(self, password_hash: str | None = None) -> None:
        # Accepts a pre-hashed password (openssl passwd -6) or None for interactive
        self._hash = password_hash

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)
        pw_hash = self._hash or state.get("root_password_hash")

        if pw_hash:
            with chroot_context(mp):
                chroot_run(mp, [
                    "usermod", "-p", pw_hash, "root"
                ])
            print("  Root password set")
        else:
            print("  WARNING: No root password set — system will have empty root password")
            print("  Set it after boot with: passwd root")


class NetworkStep(Step):
    name = "network"
    description = "Configuring network"

    def __init__(self, iface: str = "eth0", dhcp: bool = True) -> None:
        self._iface = iface
        self._dhcp = dhcp

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)
        iface = state.get("network_iface", self._iface)
        dhcp = state.get("network_dhcp", self._dhcp)

        net_conf = mp / "etc" / "conf.d" / "net"
        if dhcp:
            net_conf.write_text(f'config_{iface}="dhcp"\n')
        else:
            ip = state.get("network_ip", "192.168.1.100/24")
            gw = state.get("network_gateway", "192.168.1.1")
            net_conf.write_text(
                f'config_{iface}="{ip}"\n'
                f'routes_{iface}="default via {gw}"\n'
            )

        # Enable OpenRC net service
        net_link = mp / "etc" / "init.d" / f"net.{iface}"
        net_template = mp / "etc" / "init.d" / "net.lo"
        if net_template.exists() and not net_link.exists():
            net_link.symlink_to("net.lo")

        with chroot_context(mp):
            chroot_run(mp, [
                "rc-update", "add", f"net.{iface}", "default"
            ], check=False)

        print(f"  Network: {iface} {'DHCP' if dhcp else 'static'}")


class ContinuationAgentStep(Step):
    name = "continuation_agent"
    description = "Installing Stage 3 continuation agent"

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)

        # Install this installer script into target
        installer_bin = mp / "usr" / "local" / "bin" / "anaconda-gentoo"
        installer_bin.parent.mkdir(parents=True, exist_ok=True)

        # Write a minimal launcher that runs main.py from the installed copy
        installer_bin.write_text(
            "#!/bin/bash\n"
            "exec python3 /usr/local/lib/anaconda-gentoo/main.py \"$@\"\n"
        )
        installer_bin.chmod(0o755)

        # Copy installer source into target
        import shutil, os
        src_root = Path(__file__).parent.parent.parent
        dest_lib = mp / "usr" / "local" / "lib" / "anaconda-gentoo"
        if dest_lib.exists():
            shutil.rmtree(str(dest_lib))
        shutil.copytree(str(src_root), str(dest_lib),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))

        # State file already mirrored into target by state.save()
        state_dest = mp / "var" / "lib" / "anaconda-gentoo"
        state_dest.mkdir(parents=True, exist_ok=True)

        # Detect init system
        init = "openrc"
        if (mp / "lib" / "systemd").exists() or (mp / "usr" / "lib" / "systemd").exists():
            init = "systemd"

        if init == "systemd":
            svc_dir = mp / "etc" / "systemd" / "system"
            svc_dir.mkdir(parents=True, exist_ok=True)
            svc = svc_dir / "anaconda-gentoo-continue.service"
            svc.write_text(_SYSTEMD_SERVICE)
            with chroot_context(mp):
                chroot_run(mp, [
                    "systemctl", "enable", "anaconda-gentoo-continue"
                ], check=False)
            print("  Installed systemd continuation service")
        else:
            initd = mp / "etc" / "init.d" / "anaconda-gentoo-continue"
            initd.write_text(_OPENRC_SCRIPT)
            initd.chmod(0o755)
            with chroot_context(mp):
                chroot_run(mp, [
                    "rc-update", "add", "anaconda-gentoo-continue", "default"
                ], check=False)
            print("  Installed OpenRC continuation script")

        state.set("init_system", init)
        state.phase = "stage1_complete"
        state.save()


class UnmountStep(Step):
    name = "unmount"
    description = "Unmounting filesystems"

    def execute(self, state: InstallerState) -> None:
        mp = Path(state.mountpoint)
        print(f"  Unmounting {mp} ...")
        umount_all(mp)
        print("  Unmounted OK")
        print("\n  Installation complete — rebooting into Gentoo")
        print("  Stage 3 (emerge @world) will run automatically on first boot")
