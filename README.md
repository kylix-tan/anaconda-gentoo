# anaconda-gentoo

A Gentoo Linux installer with automatic hardware detection and kernel configuration.

Detects your hardware directly from the bus (no sysfs, no `lspci`), matches
devices against the kernel source tree, and generates a minimal `.config` with
every driver your machine needs — root filesystem drivers locked as built-in so
the system boots without an initramfs.

Installation runs in two phases separated by a reboot:

```
Phase 1 (live ISO)       Phase 3 (first boot)
──────────────────       ────────────────────
Hardware detect          emerge --sync
Partition + format       emerge @world
Extract stage3           Cleanup
make.conf                Disable agent
Portage sync             Done
Timezone / locale
Kernel config ──────────── hardware-matched, rootfs =y
Kernel compile
Install + GRUB
Reboot ──────────────────────────────────────────────▶
```

Phase 3 runs automatically via a systemd or OpenRC one-shot service installed
during Phase 1. If interrupted at any point, re-running the installer resumes
from the last completed step.

---

## Requirements

### Build host (to create the ISO)

| Tool               | Purpose               |
| ------------------ | --------------------- |
| `xorriso`          | ISO creation          |
| `cpio`             | initramfs packing     |
| `lz4` or `xz`      | initramfs compression |
| `python3` + `pip3` | dependency injection  |
| `wget`             | ISO download          |

```bash
# Debian / Ubuntu
apt install xorriso cpio lz4 xz-utils python3-pip wget

# Fedora / RHEL
dnf install xorriso cpio lz4 xz python3-pip wget

# Arch
pacman -S xorriso cpio lz4 xz python-pip wget
```

### Live environment (inside the ISO at install time)

- Root access (required for `/dev/port` and `/dev/mem`)
- Network connection (for stage3 download and portage sync)
- Linux kernel source tree on a reachable path (for kernel config generation)

### Target machine

- x86_64 CPU (ARM supported via `--dtb`)
- 20 GB+ disk
- 2 GB+ RAM (4 GB+ recommended for kernel compilation)

---

## Quick start

### 1. Build the ISO

```bash
# Auto-download latest Gentoo minimal install ISO and inject the installer
./build_iso.sh

# Or use a local ISO you already have
./build_iso.sh --iso /path/to/install-amd64-minimal-*.iso

# Output: anaconda-gentoo.iso (~700 MB)
```

### 2. Test in QEMU

```bash
# BIOS boot (creates a 40 GB qcow2 test disk automatically)
./test_iso.sh

# UEFI boot
./test_iso.sh --uefi

# More RAM for kernel compile
./test_iso.sh --ram 8G --disk 80G
```

### 3. Write to USB (real hardware)

```bash
sudo dd if=anaconda-gentoo.iso of=/dev/sdX bs=4M status=progress
```

### 4. Run the installer

The installer launches automatically on console login. To run manually:

```bash
anaconda-gentoo install --kernel-src /usr/src/linux
```

---

## Installation options

```
anaconda-gentoo install [options]

  --kernel-src DIR      Linux kernel source tree for config generation
  --disk DEV            Target disk (e.g. /dev/nvme0n1); auto-detected
  --root-fs TYPE        Root filesystem: ext4 btrfs xfs f2fs  (default: ext4)
  --boot-size SIZE      Boot partition size  (default: 512MiB)
  --swap-size SIZE      Swap size            (default: 8GiB)
  --hostname NAME       System hostname      (default: gentoo)
  --timezone TZ         Timezone             (default: UTC)
  --locale LOCALE       Locale               (default: en_US.UTF-8 UTF-8)
  --march MARCH         GCC -march value     (default: native)
  --use FLAG [FLAG...]  Extra USE flags added to make.conf
  --driver-value y|m    Kernel driver value  (default: m  = loadable module)
  --cache-dir DIR       Cache kernel source index between runs
  --dtb FILE            Device Tree blob for ARM/embedded targets
  --mountpoint DIR      Install target mountpoint  (default: /mnt/gentoo)

anaconda-gentoo status   # show progress of current/resumed install
anaconda-gentoo continue # manually trigger Phase 3 (normally automatic)
```

---

## How it works

### Hardware detection

Reads hardware directly — no sysfs, no userspace tools:

| Bus         | Method                                                        |
| ----------- | ------------------------------------------------------------- |
| PCI         | CF8/CFC port I/O (`/dev/port`) — enumerate all 256 buses      |
| USB         | `USBDEVFS_CONTROL` ioctl on `/dev/bus/usb/` character devices |
| ACPI        | Search for RSDP signature in `/dev/mem` 0xE0000–0xFFFFF       |
| Device Tree | Parse raw FDT binary (ARM/embedded, via `--dtb`)              |

### Kernel configuration

1. Walk `drivers/`, `net/`, `sound/` for C files containing `MODULE_DEVICE_TABLE`
2. Parse device ID tables (PCI vendor/device, USB vid/pid, ACPI HID, OF compatible)
3. Match detected devices against these tables using fnmatch
4. Trace matched source files back to `CONFIG_*` symbols via Makefile analysis
5. Resolve Kconfig `depends on` / `select` chains (BFS)
6. Generate `.config`; run `make olddefconfig` to fill remaining options

Result: a minimal config with only the drivers your hardware needs.

### Root filesystem — always built-in

Every driver in the path from PCI bus to mounted root is forced to `=y`:

```
PCI bus → storage controller → [RAID] → [LVM] → [LUKS] → filesystem
  =y          =y                  =y       =y       =y         =y
```

This lets the kernel boot directly without an initramfs. `BootPath` detects
the chain from the partition choices made during installation.

Example — NVMe + LUKS + ext4:

```
BLK_DEV_NVME=y  NVME_CORE=y  BLK_DEV_DM=y  DM_CRYPT=y
CRYPTO_AES=y  CRYPTO_XTS=y  EXT4_FS=y
```

### Resumable state machine

Every completed step is recorded in a JSON state file written to both the live
environment and the target filesystem:

```
/var/lib/anaconda-gentoo/state.json
```

On re-run, completed steps are skipped and execution resumes from where it
stopped. All steps are idempotent.

### Phase 3 continuation

At the end of Phase 1, a one-shot service is installed:

```
systemd:  /etc/systemd/system/anaconda-gentoo-continue.service
OpenRC:   /etc/init.d/anaconda-gentoo-continue
```

On first boot it runs `emerge --sync && emerge @world`, then disables and
removes itself. If `emerge @world` fails, `emerge --resume` is attempted
automatically. Re-running `anaconda-gentoo continue` resumes manually.

---

## Project structure

```
anaconda-gentoo/
├── kconfig_builder/          Kernel config engine
│   ├── hardware/
│   │   ├── pci.py            PCI scan via CF8/CFC port I/O
│   │   ├── usb.py            USB via USBDEVFS ioctl
│   │   ├── acpi.py           ACPI via /dev/mem
│   │   └── dt.py             Device Tree FDT parser
│   ├── bootpath.py           Boot-chain static symbol locking
│   ├── matchers.py           Source scanner + Makefile DB
│   ├── kconfig.py            Kconfig parser + dependency resolver
│   └── generator.py          .config writer
├── installer/
│   ├── state.py              State machine + JSON persistence
│   ├── chroot.py             Bind-mount chroot context manager
│   ├── tui.py                Rich TUI
│   └── steps/
│       ├── s01_hardware.py   Hardware detection
│       ├── s02_partition.py  Disk partitioning (GPT/MBR)
│       ├── s03_format.py     Filesystem creation
│       ├── s04_mount.py      Mount partitions
│       ├── s05_stage3.py     Stage3 download + extract
│       ├── s06_makeconf.py   make.conf generation
│       ├── s07_chroot_init.py  Portage sync, timezone, locale
│       ├── s08_kernel.py     Kernel config + compile + install
│       ├── s09_bootloader.py fstab + GRUB2
│       └── s10_finalize.py   Hostname, users, network, agent, unmount
├── stage3_agent/
│   └── agent.py              Phase 3 runner (emerge @world)
├── main.py                   CLI entry point
├── build_iso.sh              Remaster Gentoo minimal ISO
├── test_iso.sh               QEMU test launcher
└── requirements.txt          rich, requests
```

---

## make.conf generation

The installer generates `make.conf` from detected hardware:

| Detected                         | Generated                             |
| -------------------------------- | ------------------------------------- |
| Intel GPU (PCI 8086 class 03xx)  | `VIDEO_CARDS="intel"`                 |
| AMD GPU (PCI 1002 class 03xx)    | `VIDEO_CARDS="amdgpu radeon"`         |
| NVIDIA GPU (PCI 10DE class 03xx) | `VIDEO_CARDS="nvidia"`                |
| Audio controller (class 04xx)    | `USE="alsa"`                          |
| ACPI tables present              | `USE="acpi"`                          |
| UEFI partition table             | `USE="efi"` `GRUB_PLATFORMS="efi-64"` |
| CPU count N                      | `MAKEOPTS="-jN"`                      |

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
