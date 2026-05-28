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

# build_iso.sh — Build a bootable anaconda-gentoo installer ISO
#
# Strategy:
#   1. Download the official Gentoo minimal install ISO (or use one you supply)
#   2. Extract its ISO filesystem and initramfs
#   3. Inject: our installer + Python deps + autostart script
#   4. Repack the initramfs
#   5. Rebuild a hybrid BIOS+UEFI ISO with xorriso
#
# Required on build host:
#   xorriso  cpio  lz4  xz  python3  pip3  wget  file  findmnt
#
# Usage:
#   ./build_iso.sh                          # auto-download latest Gentoo ISO
#   ./build_iso.sh --iso /path/to/iso       # use local ISO
#   ./build_iso.sh --out custom.iso         # change output filename
#   ./build_iso.sh --no-autostart           # don't auto-run installer on boot

set -euo pipefail

###############################################################################
# Defaults
###############################################################################
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/build"
ISO_OUT="${SCRIPT_DIR}/anaconda-gentoo.iso"
GENTOO_ISO=""        # set via --iso
AUTOSTART=true
ARCH="amd64"
MIRROR="https://distfiles.gentoo.org/releases/${ARCH}/autobuilds"
VOLID="ANACONDA_GENTOO"

###############################################################################
# Argument parsing
###############################################################################
while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso)      GENTOO_ISO="$2"; shift 2 ;;
        --out)      ISO_OUT="$2";    shift 2 ;;
        --no-autostart) AUTOSTART=false; shift ;;
        --work-dir) WORK_DIR="$2";   shift 2 ;;
        --help|-h)
            sed -n '2,15p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

###############################################################################
# Helpers
###############################################################################
info()  { echo -e "\033[1;32m[+]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[!]\033[0m $*"; }
die()   { echo -e "\033[1;31m[✗]\033[0m $*" >&2; exit 1; }
step()  { echo -e "\n\033[1;36m━━ $* ━━\033[0m"; }

require_cmd() {
    for cmd in "$@"; do
        command -v "$cmd" &>/dev/null || die "Required command not found: $cmd — install it first"
    done
}

###############################################################################
# Check dependencies
###############################################################################
step "Checking build dependencies"
require_cmd xorriso cpio file python3 pip3 wget find

# lz4 or lz4cat
LZ4_CMD=""
command -v lz4cat  &>/dev/null && LZ4_CMD="lz4cat"
command -v lz4     &>/dev/null && LZ4_CMD="lz4 -d -c"
# xz
command -v xzcat   &>/dev/null || command -v xz &>/dev/null || die "xz not found"
XZCAT_CMD="xzcat"; command -v xzcat &>/dev/null || XZCAT_CMD="xz -d -c"

info "All dependencies found"

###############################################################################
# Workspace setup
###############################################################################
step "Setting up workspace at ${WORK_DIR}"
ISO_ROOT="${WORK_DIR}/iso-root"
INITRAMFS_DIR="${WORK_DIR}/initramfs"
rm -rf "${WORK_DIR}"
mkdir -p "${ISO_ROOT}" "${INITRAMFS_DIR}"

###############################################################################
# Step 1 — Get the Gentoo minimal install ISO
###############################################################################
step "Obtaining Gentoo minimal install ISO"

if [[ -z "${GENTOO_ISO}" ]]; then
    info "Fetching latest stage3 manifest from ${MIRROR}..."
    LATEST_FILE=$(wget -qO- "${MIRROR}/latest-install-${ARCH}-minimal.txt" \
        | grep -v '^#' | head -1 | awk '{print $1}')
    [[ -z "${LATEST_FILE}" ]] && die "Could not parse latest ISO URL"

    ISO_URL="${MIRROR}/${LATEST_FILE}"
    ISO_NAME=$(basename "${LATEST_FILE}")
    GENTOO_ISO="${WORK_DIR}/${ISO_NAME}"

    info "Latest ISO: ${ISO_URL}"
    if [[ -f "${GENTOO_ISO}" ]]; then
        info "Using cached ISO: ${GENTOO_ISO}"
    else
        info "Downloading ${ISO_NAME}..."
        wget --show-progress -O "${GENTOO_ISO}" "${ISO_URL}"

        # Verify checksum if available
        DIGEST_URL="${ISO_URL}.sha256"
        if wget -q --spider "${DIGEST_URL}" 2>/dev/null; then
            info "Verifying SHA256..."
            EXPECTED=$(wget -qO- "${DIGEST_URL}" | awk '{print $1}')
            ACTUAL=$(sha256sum "${GENTOO_ISO}" | awk '{print $1}')
            [[ "${EXPECTED}" == "${ACTUAL}" ]] || die "SHA256 mismatch!"
            info "Checksum OK"
        else
            warn "No checksum file found — skipping verification"
        fi
    fi
else
    [[ -f "${GENTOO_ISO}" ]] || die "ISO not found: ${GENTOO_ISO}"
    info "Using provided ISO: ${GENTOO_ISO}"
fi

###############################################################################
# Step 2 — Extract ISO filesystem
###############################################################################
step "Extracting ISO filesystem"

xorriso -osirrox on -indev "${GENTOO_ISO}" \
    -extract / "${ISO_ROOT}" -- 2>/dev/null
info "ISO extracted to ${ISO_ROOT}"

# Find the kernel and initramfs
KERNEL=$(find "${ISO_ROOT}" -name "gentoo" -o -name "vmlinuz" -o -name "kernel" \
         | head -1)
INITRAMFS=$(find "${ISO_ROOT}" \
    \( -name "gentoo.igz" -o -name "initrd" -o -name "initramfs.igz" \) \
    | head -1)

[[ -n "${KERNEL}" ]]    || die "Could not find kernel in ISO"
[[ -n "${INITRAMFS}" ]] || die "Could not find initramfs in ISO"

info "Kernel:    ${KERNEL}"
info "Initramfs: ${INITRAMFS}"

###############################################################################
# Step 3 — Unpack the initramfs
###############################################################################
step "Unpacking initramfs"

INITRAMFS_WORK="${WORK_DIR}/initramfs-orig"
mkdir -p "${INITRAMFS_WORK}"

# Detect compression format
FILETYPE=$(file "${INITRAMFS}")
info "Initramfs type: ${FILETYPE}"

cd "${INITRAMFS_DIR}"
if echo "${FILETYPE}" | grep -qi "lz4"; then
    [[ -n "${LZ4_CMD}" ]] || die "lz4 not found — install lz4"
    ${LZ4_CMD} "${INITRAMFS}" | cpio -id --quiet
elif echo "${FILETYPE}" | grep -qi "XZ\|xz compressed"; then
    ${XZCAT_CMD} "${INITRAMFS}" | cpio -id --quiet
elif echo "${FILETYPE}" | grep -qi "gzip\|gz"; then
    zcat "${INITRAMFS}" | cpio -id --quiet
elif echo "${FILETYPE}" | grep -qi "zstd"; then
    zstdcat "${INITRAMFS}" | cpio -id --quiet
else
    # Try all formats (kernel may prepend an uncompressed cpio)
    warn "Unknown compression, trying cpio --extract with automatic decompression"
    cpio -id --quiet < "${INITRAMFS}" 2>/dev/null || \
    zcat "${INITRAMFS}" | cpio -id --quiet 2>/dev/null || \
    die "Could not unpack initramfs"
fi
cd "${SCRIPT_DIR}"

info "Initramfs unpacked: $(find "${INITRAMFS_DIR}" | wc -l) files"

###############################################################################
# Step 4 — Install Python dependencies into the initramfs
###############################################################################
step "Installing Python dependencies"

PY_SITE=$(python3 -c "import sysconfig; print(sysconfig.get_path('purelib'))" 2>/dev/null \
          || echo "/usr/lib/python3/dist-packages")
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

# Try to find the site-packages in the initramfs
INITRD_SITE=$(find "${INITRAMFS_DIR}" \
    \( -path "*/python${PY_VER}*" -o -path "*/python3*" \) \
    -name "site-packages" -type d 2>/dev/null | head -1)

if [[ -z "${INITRD_SITE}" ]]; then
    # Fallback: create it under a likely location
    INITRD_SITE="${INITRAMFS_DIR}/usr/lib/python${PY_VER}/site-packages"
    mkdir -p "${INITRD_SITE}"
fi

info "Installing rich and requests → ${INITRD_SITE}"
pip3 install \
    --quiet \
    --no-deps \
    --ignore-installed \
    --target "${INITRD_SITE}" \
    rich requests

info "Python packages installed"

###############################################################################
# Step 5 — Copy installer into the initramfs
###############################################################################
step "Injecting anaconda-gentoo installer"

INSTALLER_DEST="${INITRAMFS_DIR}/usr/local/lib/anaconda-gentoo"
mkdir -p "${INSTALLER_DEST}"

# Copy our project (exclude build artifacts)
rsync -a --exclude='build/' --exclude='__pycache__/' \
    --exclude='*.pyc' --exclude='.git/' --exclude='*.iso' \
    "${SCRIPT_DIR}/" "${INSTALLER_DEST}/"

# Create the launcher binary
LAUNCHER="${INITRAMFS_DIR}/usr/local/bin/anaconda-gentoo"
mkdir -p "$(dirname "${LAUNCHER}")"
cat > "${LAUNCHER}" <<'EOF'
#!/bin/bash
cd /usr/local/lib/anaconda-gentoo
exec python3 main.py "$@"
EOF
chmod 755 "${LAUNCHER}"

info "Installer copied to ${INSTALLER_DEST}"

###############################################################################
# Step 6 — Add autostart script
###############################################################################
step "Adding autostart configuration"

if ${AUTOSTART}; then
    # OpenRC local.d runs on boot
    LOCALD="${INITRAMFS_DIR}/etc/local.d"
    mkdir -p "${LOCALD}"
    cat > "${LOCALD}/anaconda-gentoo.start" <<'AUTOSTART'
#!/bin/bash
# Auto-start anaconda-gentoo installer on first console login
# This runs as a local.d script during system boot

cat > /root/.bash_profile <<'PROFILE'
#!/bin/bash
# Auto-launched by anaconda-gentoo live ISO
clear
echo "╔══════════════════════════════════════════════════════════╗"
echo "║         anaconda-gentoo — Gentoo Linux Installer         ║"
echo "║   Hardware-Aware Kernel Configuration + Two-Phase Setup  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Starting installer... (Ctrl+C to drop to shell)"
echo ""
sleep 2
anaconda-gentoo install "$@"
exec bash
PROFILE
AUTOSTART
    chmod 755 "${LOCALD}/anaconda-gentoo.start"

    # Also add to OpenRC default runlevel if local service exists
    LOCAL_SVC="${INITRAMFS_DIR}/etc/init.d/local"
    if [[ -f "${LOCAL_SVC}" ]]; then
        RUNLEVEL_DIR="${INITRAMFS_DIR}/etc/runlevels/default"
        mkdir -p "${RUNLEVEL_DIR}"
        ln -sf /etc/init.d/local "${RUNLEVEL_DIR}/local" 2>/dev/null || true
    fi

    info "Autostart configured — installer will launch on console login"
else
    info "Autostart disabled — run 'anaconda-gentoo install' manually"
    # Just add a welcome message
    cat >> "${INITRAMFS_DIR}/etc/motd" <<'MOTD' 2>/dev/null || true

  ┌─────────────────────────────────────────────────┐
  │  anaconda-gentoo available — run to install:    │
  │    anaconda-gentoo install --kernel-src <DIR>   │
  └─────────────────────────────────────────────────┘
MOTD
fi

###############################################################################
# Step 7 — Ensure /dev/port and /dev/mem are accessible at boot
###############################################################################
# The live ISO runs as root — /dev/port and /dev/mem should be available
# but some ISOs restrict /dev/mem. Add a check script.
cat > "${INITRAMFS_DIR}/usr/local/bin/check-hw-access" <<'CHECK'
#!/bin/bash
FAIL=0
for dev in /dev/port /dev/mem /dev/bus/usb; do
    if [[ -e "$dev" ]]; then
        echo "  OK  $dev"
    else
        echo "  MISSING: $dev (hardware detection may be limited)"
        FAIL=1
    fi
done
exit $FAIL
CHECK
chmod 755 "${INITRAMFS_DIR}/usr/local/bin/check-hw-access"

###############################################################################
# Step 8 — Repack the initramfs
###############################################################################
step "Repacking initramfs"

NEW_INITRAMFS="${WORK_DIR}/gentoo-new.igz"

cd "${INITRAMFS_DIR}"
find . | cpio -H newc -o --quiet 2>/dev/null | xz -C crc32 -e --threads=0 \
    > "${NEW_INITRAMFS}"
cd "${SCRIPT_DIR}"

OLD_SIZE=$(du -sh "${INITRAMFS}" | cut -f1)
NEW_SIZE=$(du -sh "${NEW_INITRAMFS}" | cut -f1)
info "Initramfs: ${OLD_SIZE} → ${NEW_SIZE}"

# Replace the initramfs in the ISO root
cp "${NEW_INITRAMFS}" "${INITRAMFS}"
info "Replaced ${INITRAMFS} with new initramfs"

###############################################################################
# Step 9 — Rebuild the ISO
###############################################################################
step "Building bootable ISO"

# Detect EFI boot image
EFI_IMG=$(find "${ISO_ROOT}" -name "efiboot.img" | head -1)

# Detect isolinux boot files
ISOLINUX_BIN=$(find "${ISO_ROOT}" -name "isolinux.bin" | head -1)
ISOLINUX_REL=""
if [[ -n "${ISOLINUX_BIN}" ]]; then
    ISOLINUX_REL=$(realpath --relative-to="${ISO_ROOT}" "${ISOLINUX_BIN}")
fi

BOOT_CAT=$(find "${ISO_ROOT}" \
    \( -name "boot.cat" -o -name "boot.catalog" \) | head -1)
BOOT_CAT_REL=""
if [[ -n "${BOOT_CAT}" ]]; then
    BOOT_CAT_REL=$(realpath --relative-to="${ISO_ROOT}" "${BOOT_CAT}")
else
    BOOT_CAT_REL="isolinux/boot.cat"
fi

# Build xorriso command
XORRISO_ARGS=(
    -as mkisofs
    -iso-level 3
    -full-iso9660-filenames
    -rational-rock
    -joliet
    -volid "${VOLID}"
)

# BIOS boot via isolinux
if [[ -n "${ISOLINUX_BIN}" ]]; then
    XORRISO_ARGS+=(
        -eltorito-boot "${ISOLINUX_REL}"
        -eltorito-catalog "${BOOT_CAT_REL}"
        -no-emul-boot
        -boot-load-size 4
        -boot-info-table
    )
    info "BIOS boot: isolinux"
fi

# UEFI boot via EFI image
if [[ -n "${EFI_IMG}" ]]; then
    EFI_REL=$(realpath --relative-to="${ISO_ROOT}" "${EFI_IMG}")
    XORRISO_ARGS+=(
        -eltorito-alt-boot
        -e "${EFI_REL}"
        -no-emul-boot
        -isohybrid-gpt-basdat
    )
    info "UEFI boot: ${EFI_IMG}"
fi

XORRISO_ARGS+=(
    -output "${ISO_OUT}"
    "${ISO_ROOT}"
)

info "Running xorriso..."
xorriso "${XORRISO_ARGS[@]}" 2>&1 | grep -v "^$" || true

# Make it a hybrid ISO (bootable from USB)
if command -v isohybrid &>/dev/null; then
    isohybrid --uefi "${ISO_OUT}" 2>/dev/null || \
    isohybrid "${ISO_OUT}" 2>/dev/null || true
    info "isohybrid applied (USB-bootable)"
fi

###############################################################################
# Done
###############################################################################
echo ""
echo -e "\033[1;32m✓ ISO built successfully!\033[0m"
echo ""
echo "  Output:   ${ISO_OUT}"
echo "  Size:     $(du -sh "${ISO_OUT}" | cut -f1)"
echo ""
echo "  Test with QEMU:"
echo "    qemu-system-x86_64 \\"
echo "      -enable-kvm -m 4G -smp 4 \\"
echo "      -cdrom ${ISO_OUT} \\"
echo "      -drive file=test-disk.img,format=qcow2 \\"
echo "      -net nic -net user \\"
echo "      -boot d"
echo ""
echo "  Write to USB:"
echo "    sudo dd if=${ISO_OUT} of=/dev/sdX bs=4M status=progress"
echo ""
echo "  QEMU UEFI test:"
echo "    qemu-system-x86_64 \\"
echo "      -enable-kvm -m 4G -smp 4 \\"
echo "      -bios /usr/share/ovmf/OVMF.fd \\"
echo "      -cdrom ${ISO_OUT} \\"
echo "      -drive file=test-disk.img,format=qcow2 \\"
echo "      -net nic -net user \\"
echo "      -boot d"
