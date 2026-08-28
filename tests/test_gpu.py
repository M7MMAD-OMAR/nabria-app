"""Device selection.

The case that matters most is a machine with nothing but an integrated GPU,
because that is the commonest laptop there is and because the wrong answer
there is not "a bit slower" -- it is three times slower than the CPU and then a
driver crash. Every test below is written against synthetic device lists so it
runs the same on CI hardware with no GPU at all.
"""

from __future__ import annotations

from nabria import gpu

INTEL = gpu.Device(0, "Intel(R) Graphics (RPL-S)", "integrated", 0x8086, 0xA78B)
NVIDIA = gpu.Device(1, "NVIDIA GeForce RTX 4070 Laptop GPU", "discrete", 0x10DE, 0x2860)
LLVMPIPE = gpu.Device(2, "llvmpipe", "cpu", 0x10005, 0x0000)


def test_discrete_is_chosen_by_its_raw_vulkan_index():
    # The index handed to GGML_VK_VISIBLE_DEVICES has to be the raw Vulkan one,
    # including the devices ggml itself would filter out, or it names the wrong
    # card. Here the NVIDIA is Vulkan index 1 even though it is the only
    # candidate ggml would keep.
    decision = gpu.plan("auto", [INTEL, NVIDIA, LLVMPIPE])
    assert decision.use_gpu is True
    assert decision.visible == 1


def test_integrated_only_falls_back_to_cpu():
    decision = gpu.plan("auto", [INTEL, LLVMPIPE])
    assert decision.use_gpu is False
    assert decision.visible is None
    assert "Intel" in decision.reason  # says what it saw and rejected


def test_no_vulkan_at_all_falls_back_to_cpu():
    decision = gpu.plan("auto", [])
    assert decision.use_gpu is False


def test_software_rasteriser_is_not_a_gpu():
    # llvmpipe reports itself as a Vulkan device and would "work", at a speed
    # that makes the tool useless.
    decision = gpu.plan("auto", [LLVMPIPE])
    assert decision.use_gpu is False


def test_explicit_vendor_device_wins():
    decision = gpu.plan("8086:a78b", [INTEL, NVIDIA])
    assert decision.use_gpu is True
    assert decision.visible == 0  # asked for the integrated one by name; obeyed


def test_explicit_device_that_is_absent_is_reported_not_ignored():
    decision = gpu.plan("dead:beef", [INTEL, NVIDIA])
    assert decision.use_gpu is False
    assert "dead:beef" in decision.reason


def test_cpu_and_any_are_honoured():
    assert gpu.plan("cpu", [NVIDIA]).use_gpu is False
    left_alone = gpu.plan("any", [INTEL, NVIDIA])
    assert left_alone.use_gpu is True
    assert left_alone.visible is None  # ggml keeps its own default list


def test_probe_survives_a_broken_subprocess(monkeypatch):
    # A driver that aborts on instance creation must degrade to "no GPU", not
    # take the daemon down. This is why the probe is a subprocess at all.
    def explode(*args, **kwargs):
        raise OSError("no python here")

    gpu.forget()
    monkeypatch.setattr(gpu.subprocess, "run", explode)
    try:
        assert gpu.probe() == []
        assert gpu.plan("auto").use_gpu is False
    finally:
        gpu.forget()


def test_the_probe_is_only_paid_for_once(monkeypatch):
    # ~100ms a time, nearly all of it the Vulkan loader opening every driver.
    # Two callers want it and neither should pay twice.
    calls = []
    gpu.forget()

    class Result:
        stdout = "[]"

    monkeypatch.setattr(gpu.subprocess, "run", lambda *a, **k: calls.append(1) or Result())
    try:
        gpu.probe()
        gpu.probe()
        assert len(calls) == 1
    finally:
        gpu.forget()


def test_enumeration_matches_this_machine_if_vulkan_is_present():
    """Not a unit test -- checks the ctypes struct layout against a real driver.

    Skipped where there is no Vulkan. When it does run it catches the class of
    bug that a synthetic device list never can: a mis-declared argtype that
    truncates a device handle, which is exactly how the first version of this
    module segfaulted.
    """
    gpu.forget()
    devices = gpu.probe()
    if not devices:
        import pytest

        pytest.skip("no Vulkan devices on this machine")
    for device in devices:
        assert device.name  # a garbled struct read gives an empty or junk name
        assert device.kind in set(gpu.KINDS.values())
        assert 0 < device.vendor < 0x1FFFF
    assert [d.index for d in devices] == list(range(len(devices)))
