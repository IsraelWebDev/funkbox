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


# --- sidecar requirements (hosted image worker) -------------------------------------------------

from funkbox.studio.sdcore import ModelReadiness  # noqa: E402


def test_cpu_uses_model_dir_from_env(tmp_path):
    d = tmp_path / "snapshots" / "b261bac6fd2cf515557d5d0707481eafa0485ec2"
    d.mkdir(parents=True)
    s = sdcore.engine_settings("CPU", env={"FUNKBOX_IMAGE_MODEL_DIR": str(d)})
    assert s["lcm_model_id"] == str(d)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_cpu_falls_back_to_model_name_when_env_unset(value):
    env = {} if value is None else {"FUNKBOX_IMAGE_MODEL_DIR": value}
    assert sdcore.engine_settings("CPU", env=env)["lcm_model_id"] == "stabilityai/sd-turbo"


def test_cpu_model_dir_that_does_not_exist_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="FUNKBOX_IMAGE_MODEL_DIR"):
        sdcore.engine_settings("CPU", env={"FUNKBOX_IMAGE_MODEL_DIR": str(tmp_path / "missing")})


def test_cpu_sets_safety_checker_off_explicitly():
    assert sdcore.engine_settings("CPU", env={})["use_safety_checker"] is False


def test_gpu_settings_ignore_the_model_dir_env(tmp_path):
    s = sdcore.engine_settings("GPU", env={"FUNKBOX_IMAGE_MODEL_DIR": str(tmp_path)})
    assert s == {
        "use_openvino": True,
        "openvino_lcm_model_id": "rupeshs/sd-turbo-openvino",
        "use_tiny_auto_encoder": True,
    }


def test_readiness_is_false_until_a_model_load_is_marked():
    r = ModelReadiness()
    assert r.ready is False and r.error is None
    r.mark_loaded()
    assert r.ready is True and r.error is None


def test_failed_warm_up_stays_not_ready_and_keeps_the_reason():
    r = ModelReadiness()
    r.mark_failed("no weights")
    assert r.ready is False
    assert r.error == "no weights"


def test_launcher_reports_readiness_and_warms_up_before_serving():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert '"ready": READY.ready' in text
    assert "threading.Thread(target=_warm_up" in text
    assert text.index("threading.Thread(target=_warm_up") < text.index("uvicorn.run(")


def test_launcher_mounts_mcp_only_when_asked_and_installed():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'os.environ.get("FASTSD_MCP", "1") != "0"' in text
    assert "except ImportError" in text
    assert "\nFastApiMCP(" not in text  # no unconditional module-level mount
