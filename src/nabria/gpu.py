"""Which GPU the transcription engine should run on.

Vulkan enumerates physical devices in whatever order the driver reports, and on
a hybrid laptop that is usually the integrated one -- measured here at 2.5x
slower than realtime on large-v3-turbo, against under 4s for 45s of audio on
the discrete card. MESA_VK_DEVICE_SELECT reorders that list.

The value used to be a hardcoded `vendor:device` for one particular RTX 4070.
This finds it instead, from sysfs, with no external command and no dependency.
"""

from __future__ import annotations

from pathlib import Path

DRM = Path("/sys/class/drm")

# PCI vendor ids. Intel is integrated on every machine that has it; AMD and
# NVIDIA can be either, so they are ranked by whether the kernel says the
# device is on a removable/discrete bus rather than by vendor alone.
NVIDIA = 0x10DE
AMD = 0x1002
INTEL = 0x8086


def _read_hex(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip(), 16)
    except (OSError, ValueError):
        return None


def _is_discrete(device: Path) -> bool:
    """True when the card has its own memory rather than sharing system RAM.

    `mem_info_vram_total` exists only on cards with dedicated VRAM, which is
    exactly the distinction that matters here and is cheaper and more reliable
    than matching device ids.
    """
    if (device / "mem_info_vram_total").exists():
        return True
    vendor = _read_hex(device / "vendor")
    if vendor == NVIDIA:
        # The proprietary driver exposes no VRAM node; an NVIDIA GPU on this
        # kind of machine is discrete in every case that matters.
        return True
    return False


def candidates() -> list[tuple[str, bool]]:
    """Every render node as (`vendor:device`, is_discrete), discrete first."""
    found: list[tuple[str, bool]] = []
    try:
        cards = sorted(DRM.glob("card[0-9]*"))
    except OSError:
        return []
    for card in cards:
        if "-" in card.name:  # card0-eDP-1 and friends are connectors
            continue
        device = card / "device"
        vendor = _read_hex(device / "vendor")
        product = _read_hex(device / "device")
        if vendor is None or product is None:
            continue
        identity = f"{vendor:04x}:{product:04x}"
        if any(identity == seen for seen, _ in found):
            continue
        found.append((identity, _is_discrete(device)))
    found.sort(key=lambda entry: not entry[1])
    return found


def preferred() -> str:
    """The best `vendor:device` for MESA_VK_DEVICE_SELECT, or "" if unclear.

    Empty when there is nothing to choose between -- a single GPU needs no
    reordering, and setting the variable anyway would be a claim about hardware
    this cannot check.
    """
    found = candidates()
    if len(found) < 2:
        return ""
    identity, discrete = found[0]
    return identity if discrete else ""
