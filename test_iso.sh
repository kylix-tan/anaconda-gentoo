#!/usr/bin/env bash
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

# test_iso.sh — Run anaconda-gentoo.iso in QEMU for testing
#
# Usage:
#   ./test_iso.sh                    # BIOS boot
#   ./test_iso.sh --uefi             # UEFI boot (needs ovmf)
#   ./test_iso.sh --iso custom.iso   # use different ISO
#   ./test_iso.sh --ram 8G           # more RAM for kernel compile
#   ./test_iso.sh --disk 40G         # larger test disk
#   ./test_iso.sh --no-kvm           # disable KVM (slower, no root needed)
#   ./test_iso.sh --vnc              # VNC display instead of SDL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO="${SCRIPT_DIR}/anaconda-gentoo.iso"
DISK_IMG="${SCRIPT_DIR}/build/test-disk.qcow2"
DISK_SIZE="40G"
RAM="4G"
CPUS="4"
UEFI=false
NO_KVM=false
VNC=false
OVMF_PATH=""
SSH_PORT="2222"

# Search common OVMF paths
for p in \
    /usr/share/ovmf/OVMF.fd \
    /usr/share/OVMF/OVMF_CODE.fd \
    /usr/share/edk2/ovmf/OVMF_CODE.fd \
    /usr/local/share/qemu/edk2-x86_64-code.fd \
    /opt/homebrew/share/qemu/edk2-x86_64-code.fd; do
    [[ -f "$p" ]] && { OVMF_PATH="$p"; break; }
done

###############################################################################
# Argument parsing
###############################################################################
while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso)     ISO="$2";       shift 2 ;;
        --disk)    DISK_SIZE="$2"; shift 2 ;;
        --ram)     RAM="$2";       shift 2 ;;
        --cpus)    CPUS="$2";      shift 2 ;;
        --uefi)    UEFI=true;      shift ;;
        --no-kvm)  NO_KVM=true;    shift ;;
        --vnc)     VNC=true;       shift ;;
        --ovmf)    OVMF_PATH="$2"; shift 2 ;;
        --clean)
            rm -f "${DISK_IMG}"
            echo "Cleaned test disk"
            exit 0
            ;;
        --help|-h)
            sed -n '2,12p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

###############################################################################
# Checks
###############################################################################
command -v qemu-system-x86_64 &>/dev/null \
    || { echo "Error: qemu-system-x86_64 not found"; exit 1; }

[[ -f "${ISO}" ]] \
    || { echo "Error: ISO not found: ${ISO}"; echo "Run ./build_iso.sh first"; exit 1; }

if ${UEFI} && [[ -z "${OVMF_PATH}" ]]; then
    echo "Error: UEFI boot requested but OVMF firmware not found"
    echo "Install: apt install ovmf  OR  brew install qemu"
    exit 1
fi

###############################################################################
# Create test disk if needed
###############################################################################
mkdir -p "$(dirname "${DISK_IMG}")"
if [[ ! -f "${DISK_IMG}" ]]; then
    echo "[+] Creating ${DISK_SIZE} test disk at ${DISK_IMG}..."
    qemu-img create -f qcow2 "${DISK_IMG}" "${DISK_SIZE}"
fi

###############################################################################
# Build QEMU command
###############################################################################
QEMU_ARGS=(
    -name "anaconda-gentoo-test"
    -m "${RAM}"
    -smp "${CPUS}"
    -cdrom "${ISO}"
    -drive "file=${DISK_IMG},format=qcow2,if=virtio"
    -boot order=dc,menu=on
    # Network: user-mode NAT with SSH port forward
    -netdev "user,id=net0,hostfwd=tcp::${SSH_PORT}-:22"
    -device "virtio-net-pci,netdev=net0"
    # USB for USB device detection testing
    -usb
    -device usb-ehci
    # Serial console (useful for debugging)
    -serial mon:stdio
    # Enable ACPI
    -accel tcg
)

# KVM acceleration
if ! ${NO_KVM}; then
    if [[ -w /dev/kvm ]]; then
        QEMU_ARGS+=(-enable-kvm -cpu host)
        echo "[+] KVM enabled"
    else
        echo "[!] /dev/kvm not writable — falling back to TCG (slow)"
        echo "    Run as root or add yourself to the kvm group"
    fi
fi

# UEFI vs BIOS
if ${UEFI}; then
    QEMU_ARGS+=(-bios "${OVMF_PATH}")
    echo "[+] UEFI boot with ${OVMF_PATH}"
else
    echo "[+] BIOS boot"
fi

# Display
if ${VNC}; then
    QEMU_ARGS+=(-display vnc=:1 -vga std)
    echo "[+] VNC display on :5901 (connect with: vncviewer localhost:5901)"
else
    # Try SDL then GTK; both are optional
    QEMU_ARGS+=(-vga virtio)
    if command -v Xorg &>/dev/null || [[ -n "${DISPLAY:-}" ]]; then
        QEMU_ARGS+=(-display gtk)
    else
        QEMU_ARGS+=(-display curses)
    fi
fi

###############################################################################
# Print summary and run
###############################################################################
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  anaconda-gentoo QEMU test"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ISO:   ${ISO}"
echo "  Disk:  ${DISK_IMG} (${DISK_SIZE})"
echo "  RAM:   ${RAM}  CPUs: ${CPUS}"
echo "  Boot:  $(${UEFI} && echo UEFI || echo BIOS)"
echo "  SSH:   ssh root@localhost -p ${SSH_PORT}  (once booted)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Inside the VM, the installer launches automatically."
echo "To run manually: anaconda-gentoo install --kernel-src /usr/src/linux"
echo ""

exec qemu-system-x86_64 "${QEMU_ARGS[@]}"
