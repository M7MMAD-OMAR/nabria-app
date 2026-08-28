"""Which device the transcription engine should run on.

Left alone, ggml uses *every* discrete and integrated GPU it can see. On a
hybrid laptop that includes the integrated one, and an integrated GPU is not a
lesser accelerator here -- it is actively worse than not using a GPU at all.
Measured on 11 s of audio with large-v3-turbo:

    discrete (RTX 4070, Vulkan)     0.32 s
    CPU (16 threads)               21.4 s
    integrated (Intel, Vulkan)     63.5 s, then vk::Queue::submit: ErrorDeviceLost

So the policy is not "prefer discrete". It is: use a discrete GPU if there is
one, and otherwise run on the CPU and do not touch Vulkan at all. Getting this
backwards on the most common laptop in the world -- Intel integrated, no
discrete card -- means a tool that is three times slower than the obvious
fallback and crashes halfway through.

Devices are enumerated through libvulkan directly rather than by matching PCI
ids in sysfs, because the number ggml wants is a *Vulkan* physical device
index, and only Vulkan can say what that ordering is. `GGML_VK_VISIBLE_DEVICES`
takes exactly that index, so naming one device leaves ggml with a list of one.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from typing import NamedTuple

# VkPhysicalDeviceType
OTHER, INTEGRATED, DISCRETE, VIRTUAL, CPU = range(5)

KINDS = {
    OTHER: "other",
    INTEGRATED: "integrated",
    DISCRETE: "discrete",
    VIRTUAL: "virtual",
    CPU: "cpu",
}

VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1
VK_SUCCESS = 0
VK_MAX_PHYSICAL_DEVICE_NAME_SIZE = 256


class Device(NamedTuple):
    index: int          # the raw Vulkan index, which is what ggml wants
    name: str
    kind: str
    vendor: int
    product: int

    @property
    def usable(self) -> bool:
        """Whether running the engine here beats running it on the CPU.

        Only a discrete card qualifies. A virtual GPU is a passed-through
        discrete card often enough that it would be tempting to include, but it
        is also a software rasteriser often enough that it is not worth the
        risk of a silent 60x slowdown.
        """
        return self.kind == "discrete"


class Plan(NamedTuple):
    use_gpu: bool
    visible: int | None   # GGML_VK_VISIBLE_DEVICES, or None to leave ggml alone
    reason: str           # one line for the log; this decision is worth tracing


class _VkInstanceCreateInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_uint32),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.c_void_p),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.c_void_p),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.c_void_p),
    ]


class _VkPhysicalDeviceProperties(ctypes.Structure):
    # Only the head of the real struct is named. `limits` and
    # `sparseProperties` that follow are large and of no interest, but the
    # driver writes the whole thing, so the tail has to be allocated or the
    # write runs off the end of the buffer.
    _fields_ = [
        ("apiVersion", ctypes.c_uint32),
        ("driverVersion", ctypes.c_uint32),
        ("vendorID", ctypes.c_uint32),
        ("deviceID", ctypes.c_uint32),
        ("deviceType", ctypes.c_uint32),
        ("deviceName", ctypes.c_char * VK_MAX_PHYSICAL_DEVICE_NAME_SIZE),
        ("pipelineCacheUUID", ctypes.c_uint8 * 16),
        ("tail", ctypes.c_uint8 * 1024),
    ]


def enumerate_devices() -> list[Device]:
    """Ask Vulkan what is here. [] when Vulkan is absent or answers nothing.

    Runs in a throwaway subprocess in normal use -- see `probe`.
    """
    for candidate in ("libvulkan.so.1", "libvulkan.so"):
        try:
            vulkan = ctypes.CDLL(candidate)
            break
        except OSError:
            continue
    else:
        return []

    # Not optional. Indexing a (c_void_p * n) array yields a Python int, and
    # without an argtype ctypes marshals it as a C int -- truncating a 64-bit
    # device handle to 32 bits and segfaulting inside the driver.
    vulkan.vkCreateInstance.argtypes = [
        ctypes.POINTER(_VkInstanceCreateInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    ]
    vulkan.vkCreateInstance.restype = ctypes.c_int32
    vulkan.vkEnumeratePhysicalDevices.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p)
    ]
    vulkan.vkEnumeratePhysicalDevices.restype = ctypes.c_int32
    vulkan.vkGetPhysicalDeviceProperties.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_VkPhysicalDeviceProperties)
    ]
    vulkan.vkGetPhysicalDeviceProperties.restype = None
    vulkan.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    vulkan.vkDestroyInstance.restype = None

    info = _VkInstanceCreateInfo(sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO)
    instance = ctypes.c_void_p()
    if vulkan.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(instance)) != VK_SUCCESS:
        return []

    try:
        count = ctypes.c_uint32(0)
        if vulkan.vkEnumeratePhysicalDevices(
            instance, ctypes.byref(count), None
        ) != VK_SUCCESS or not count.value:
            return []
        handles = (ctypes.c_void_p * count.value)()
        if vulkan.vkEnumeratePhysicalDevices(
            instance, ctypes.byref(count), handles
        ) != VK_SUCCESS:
            return []

        found: list[Device] = []
        for index in range(count.value):
            properties = _VkPhysicalDeviceProperties()
            vulkan.vkGetPhysicalDeviceProperties(handles[index], ctypes.byref(properties))
            found.append(
                Device(
                    index=index,
                    name=properties.deviceName.decode("utf-8", "replace"),
                    kind=KINDS.get(properties.deviceType, "other"),
                    vendor=properties.vendorID,
                    product=properties.deviceID,
                )
            )
        return found
    finally:
        vulkan.vkDestroyInstance(instance, None)


def probe(timeout: float = 10.0) -> list[Device]:
    """`enumerate_devices` behind a subprocess.

    Creating a Vulkan instance loads the graphics drivers into whichever
    process does it. Two reasons not to let that be the daemon: a broken or
    half-installed driver that aborts on instance creation would take dictation
    down with it, and the daemon is also the GTK process -- it has no business
    holding a handle to the discrete card for the sake of one question asked
    once per engine start.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nabria.gpu"],
            capture_output=True, text=True, timeout=timeout,
        )
        return [Device(*entry) for entry in json.loads(result.stdout)]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return []


def plan(preference: str = "auto", devices: list[Device] | None = None) -> Plan:
    """Turn the `gpu_select` setting into an engine decision.

    `auto`  pick a discrete card, else CPU
    `cpu`   never use a GPU
    `any`   hand the choice back to ggml, integrated cards and all
    other   a `vendor:device` pair, matched case-insensitively against what
            Vulkan reports -- an escape hatch for the case this gets wrong
    """
    preference = (preference or "auto").strip().lower()
    if preference == "any":
        return Plan(True, None, "GPU selection left to the engine")
    if preference in {"cpu", "none", "off"}:
        return Plan(False, None, "CPU, by configuration")

    if devices is None:
        devices = probe()

    if preference not in {"auto", ""}:
        for device in devices:
            if f"{device.vendor:04x}:{device.product:04x}" == preference:
                return Plan(True, device.index, f"{device.name}, by configuration")
        # Naming a device that is not here is a mistake worth reporting rather
        # than quietly working anyway on something the user did not ask for.
        return Plan(False, None, f"no Vulkan device matches {preference!r}, using CPU")

    for device in devices:
        if device.usable:
            return Plan(True, device.index, f"{device.name} (discrete)")

    if devices:
        listed = ", ".join(f"{d.name} ({d.kind})" for d in devices)
        return Plan(False, None, f"CPU: no discrete GPU among {listed}")
    return Plan(False, None, "CPU: no Vulkan device")


if __name__ == "__main__":
    # The subprocess half of `probe`.
    print(json.dumps([list(device) for device in enumerate_devices()]))
