"""The FastSD launcher picks its model by device: OpenVINO on the Intel GPU (Windows), PyTorch on CPU elsewhere.

These tests import only funkbox.studio.sdcore (no FastSD, no fastapi, no models). The launcher file itself is
checked as text, because it can only run inside a FastSD checkout.
"""

from pathlib import Path

import pytest

from funkbox.studio import sdcore

LAUNCHER = Path(sdcore.__file__).with_name("start_local_mcp.py")


def test_gpu_uses_the_openvino_build_and_tiny_autoencoder():
    s = sdcore.engine_settings("GPU")
    assert s == {
        "use_openvino": True,
        "openvino_lcm_model_id": "rupeshs/sd-turbo-openvino",
        "use_tiny_auto_encoder": True,
    }


@pytest.mark.parametrize("device", ["CPU", "cpu", " Cpu ", "", "anything-else"])
def test_non_gpu_runs_the_pytorch_model_without_openvino(device):
    s = sdcore.engine_settings(device)
    assert s["use_openvino"] is False
    assert s["lcm_model_id"] == "stabilityai/sd-turbo"
    assert s["use_tiny_auto_encoder"] is False
    assert "openvino_lcm_model_id" not in s


def test_generate_device_is_GPU_on_gpu_and_lowercase_cpu_otherwise():
    assert sdcore.generate_device("GPU") == "GPU"
    assert sdcore.generate_device("gpu") == "GPU"
    assert sdcore.generate_device("CPU") == "cpu"
    assert sdcore.generate_device("") == "cpu"


def test_launcher_no_longer_forces_openvino_unconditionally():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "L.use_openvino = True" not in text
    assert 'L.openvino_lcm_model_id = "rupeshs/sd-turbo-openvino"' not in text
    assert "engine_settings(" in text
    assert "generate_device(" in text
