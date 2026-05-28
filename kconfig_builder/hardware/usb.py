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

"""USB device detection via /dev/bus/usb/ + USBDEVFS ioctl (no sysfs)."""
from __future__ import annotations

import ctypes
import fcntl
import os
import struct
from dataclasses import dataclass
from pathlib import Path

# USB request constants
_USB_DIR_IN = 0x80
_USB_TYPE_STANDARD = 0x00
_USB_RECIP_DEVICE = 0x00
_USB_RECIP_INTERFACE = 0x01
_USB_REQ_GET_DESCRIPTOR = 0x06
_USB_DT_DEVICE = 0x01
_USB_DT_CONFIG = 0x02
_USB_DT_INTERFACE = 0x04

# USBDEVFS_CONTROL ioctl: _IOWR('U', 0, struct usbdevfs_ctrltransfer)
# struct size on 64-bit Linux: 1+1+2+2+2+4+pad+8 = 24 bytes → ioctl = 0xC0185500
_USBDEVFS_CONTROL = 0xC0185500


class _USBCtrlTransfer(ctypes.Structure):
    _fields_ = [
        ("bRequestType", ctypes.c_uint8),
        ("bRequest", ctypes.c_uint8),
        ("wValue", ctypes.c_uint16),
        ("wIndex", ctypes.c_uint16),
        ("wLength", ctypes.c_uint16),
        ("timeout", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class USBDevice:
    bus: int
    address: int
    vendor: int        # idVendor
    product: int       # idProduct
    bcd_device: int    # bcdDevice
    dev_class: int     # bDeviceClass
    dev_subclass: int  # bDeviceSubClass
    dev_protocol: int  # bDeviceProtocol
    iface_class: int   # bInterfaceClass (first interface, or 0 if composite)
    iface_subclass: int
    iface_protocol: int

    @property
    def alias(self) -> str:
        return (
            f"usb:v{self.vendor:04X}p{self.product:04X}"
            f"d{self.bcd_device:04X}"
            f"dc{self.dev_class:02X}dsc{self.dev_subclass:02X}"
            f"dp{self.dev_protocol:02X}"
            f"ic{self.iface_class:02X}isc{self.iface_subclass:02X}"
            f"ip{self.iface_protocol:02X}in*"
        )

    def __str__(self) -> str:
        return (
            f"USB [{self.bus:03d}/{self.address:03d}] "
            f"{self.vendor:04x}:{self.product:04x} "
            f"class={self.dev_class:02x}/{self.dev_subclass:02x}"
        )


def _ctrl_transfer(
    fd: int,
    req_type: int,
    request: int,
    value: int,
    index: int,
    length: int,
    timeout_ms: int = 1000,
) -> bytes | None:
    buf = (ctypes.c_uint8 * length)()
    xfer = _USBCtrlTransfer(
        bRequestType=req_type,
        bRequest=request,
        wValue=value,
        wIndex=index,
        wLength=length,
        timeout=timeout_ms,
        data=ctypes.cast(buf, ctypes.c_void_p),
    )
    try:
        fcntl.ioctl(fd, _USBDEVFS_CONTROL, xfer)
        return bytes(buf)
    except OSError:
        return None


def _read_device_descriptor(fd: int) -> dict | None:
    data = _ctrl_transfer(
        fd,
        _USB_DIR_IN | _USB_TYPE_STANDARD | _USB_RECIP_DEVICE,
        _USB_REQ_GET_DESCRIPTOR,
        (_USB_DT_DEVICE << 8) | 0,
        0,
        18,
    )
    if not data or len(data) < 18:
        return None
    # struct usb_device_descriptor
    fields = struct.unpack_from("<BBHBBBBHHHBBBB", data)
    return {
        "bLength": fields[0],
        "bDescriptorType": fields[1],
        "bcdUSB": fields[2],
        "bDeviceClass": fields[3],
        "bDeviceSubClass": fields[4],
        "bDeviceProtocol": fields[5],
        "bMaxPacketSize0": fields[6],
        "idVendor": fields[7],
        "idProduct": fields[8],
        "bcdDevice": fields[9],
        "iManufacturer": fields[10],
        "iProduct": fields[11],
        "iSerialNumber": fields[12],
        "bNumConfigurations": fields[13],
    }


def _read_first_interface(fd: int) -> tuple[int, int, int]:
    """Return (iface_class, iface_subclass, iface_protocol) from first interface."""
    # Get config descriptor — request full descriptor (max 255 bytes)
    data = _ctrl_transfer(
        fd,
        _USB_DIR_IN | _USB_TYPE_STANDARD | _USB_RECIP_DEVICE,
        _USB_REQ_GET_DESCRIPTOR,
        (_USB_DT_CONFIG << 8) | 0,
        0,
        255,
    )
    if not data or len(data) < 9:
        return 0, 0, 0
    # Walk descriptors looking for first interface descriptor (type 0x04)
    offset = 0
    while offset + 2 <= len(data):
        bLength = data[offset]
        bType = data[offset + 1]
        if bLength < 2:
            break
        if bType == _USB_DT_INTERFACE and offset + 9 <= len(data):
            # struct usb_interface_descriptor
            return data[offset + 5], data[offset + 6], data[offset + 7]
        offset += bLength
    return 0, 0, 0


def _scan_usb_device(dev_path: Path, bus: int, address: int) -> USBDevice | None:
    try:
        fd = os.open(str(dev_path), os.O_RDWR)
    except (PermissionError, OSError):
        try:
            fd = os.open(str(dev_path), os.O_RDONLY)
        except (PermissionError, OSError):
            return None
    try:
        desc = _read_device_descriptor(fd)
        if not desc:
            return None
        ic, isc, ip = _read_first_interface(fd)
        return USBDevice(
            bus=bus,
            address=address,
            vendor=desc["idVendor"],
            product=desc["idProduct"],
            bcd_device=desc["bcdDevice"],
            dev_class=desc["bDeviceClass"],
            dev_subclass=desc["bDeviceSubClass"],
            dev_protocol=desc["bDeviceProtocol"],
            iface_class=ic,
            iface_subclass=isc,
            iface_protocol=ip,
        )
    finally:
        os.close(fd)


def scan_usb() -> tuple[list[USBDevice], str | None]:
    """Return (devices, error_message). error_message is None on success."""
    usb_root = Path("/dev/bus/usb")
    if not usb_root.exists():
        return [], "No /dev/bus/usb — USB detection unavailable"

    devices: list[USBDevice] = []
    errors: list[str] = []

    for bus_dir in sorted(usb_root.iterdir()):
        if not bus_dir.is_dir():
            continue
        try:
            bus_num = int(bus_dir.name)
        except ValueError:
            continue
        for dev_file in sorted(bus_dir.iterdir()):
            if not dev_file.is_file() and not dev_file.is_char_device():
                continue
            try:
                addr = int(dev_file.name)
            except ValueError:
                continue
            dev = _scan_usb_device(dev_file, bus_num, addr)
            if dev:
                devices.append(dev)
            else:
                errors.append(str(dev_file))

    err_msg = None
    if not devices and errors:
        err_msg = f"Could not read {len(errors)} USB devices — try running as root"
    return devices, err_msg
