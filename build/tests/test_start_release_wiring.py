# -*- coding: utf-8 -*-
"""Start.py 這一側的接線測試。

重點只有一個:**沒有在 Setup.json 打開 release 開關的產品,一定拿不到 release 設定,
所以整條發布線在它們身上不存在。** aig4 這種線上還有舊客戶端在吃的產品,靠的就是這條
界線;這支測試就是那條界線的看門狗。

這裡不啟動任何打包線程、不呼叫 VMProtect、不連線。
"""

import json
import os
import sys

import pytest

BUILD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BUILD_DIR)
sys.path.insert(0, BUILD_DIR)

import Start  # noqa: E402


ENV_STUB = {
    "remoteTargets": {
        "default": {"type": "rsync", "host": "user@example.invalid", "ssh_key_path": ""},
        "s3-space": {
            "type": "s3",
            "endpoint": "https://example.invalid",
            "bucket": "b",
            "access_key_id": "k",
            "secret_access_key": "s",
        },
    }
}


def validate(setup_payload, folder_name="aig4", env=None):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Setup.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(setup_payload, handle)
        return Start.validate_setup_json_v2(path, folder_name, env or ENV_STUB)


BASE_SETUP = {
    "enabled": True,
    "vmpFiles": ["*.exe", "aig.dll"],
    "remoteTargets": {"default": "/srv/axg/aig/output", "s3-space": "/corefiles/aig4"},
    "getNeedURL": "https://server.axggame.com/download/aig/output/index",
    "fileAmount": 20,
}


def test_setup_without_release_key_gets_release_none():
    ok, config, errors = validate(dict(BASE_SETUP))
    assert (ok, errors) == (True, [])
    assert config["release"] is None


def test_every_shipped_setup_json_still_validates_with_release_disabled():
    """repo 裡現有的每一份 Setup.json —— 一個都不能因為這次改動而變成 release 啟用。"""
    setup_root = os.path.join(REPO_ROOT, "Setup")
    seen = 0
    for folder in sorted(os.listdir(setup_root)):
        setup_json = os.path.join(setup_root, folder, "Setup.json")
        if not os.path.isfile(setup_json):
            continue
        seen += 1
        _ok, config, _errors = Start.validate_setup_json_v2(setup_json, folder, ENV_STUB)
        assert config is not None, folder
        release = config.get("release")
        assert release is None, f"{folder} 意外啟用了 release: {release}"
    assert seen >= 4


def test_release_enabled_without_token_fails_the_folder(monkeypatch):
    monkeypatch.delenv("AXG_RELEASE_TOKEN", raising=False)
    payload = dict(BASE_SETUP)
    payload["release"] = {
        "enabled": True,
        "target": "aig5",
        "versionWriter": {"mode": "plaintext-json", "path": "Res/Version.json"},
    }
    ok, _config, errors = validate(payload, "aig5", env=dict(ENV_STUB))
    assert ok is False
    assert any("release token" in e for e in errors)


def test_release_enabled_with_token_passes(monkeypatch):
    monkeypatch.setenv("AXG_RELEASE_TOKEN", "unit-test-token")
    payload = dict(BASE_SETUP)
    payload["release"] = {
        "enabled": True,
        "target": "aig5",
        "versionWriter": {"mode": "plaintext-json", "path": "Res/Version.json"},
    }
    ok, config, errors = validate(payload, "aig5", env=dict(ENV_STUB))
    assert (ok, errors) == (True, [])
    assert config["release"]["target"] == "aig5"
    assert config["release"]["apiBaseUrl"] == "https://server.axggame.com"


def test_release_respack_mode_requires_the_tooling(monkeypatch):
    monkeypatch.setenv("AXG_RELEASE_TOKEN", "unit-test-token")
    monkeypatch.delenv("AXG_RESPACK_PATH", raising=False)
    monkeypatch.delenv("AXG_RES_KEY_FILE", raising=False)
    payload = dict(BASE_SETUP)
    payload["release"] = {
        "enabled": True,
        "target": "aig5",
        "versionWriter": {"mode": "respack", "templatePath": "Version.template.json"},
    }
    ok, _config, errors = validate(payload, "aig5", env=dict(ENV_STUB))
    assert ok is False
    assert any("respack.exe" in e for e in errors)
    assert any("key file" in e for e in errors)


def test_scan_setup_folders_carries_release_through(monkeypatch):
    """scan_setup_folders 把 release 設定原封不動帶進 folder_info 給 process()。"""
    captured = []

    def fake_validate(setup_path, folder_name, env_config):
        config = dict(BASE_SETUP)
        config["remoteTargets"] = [{"name": "default", "type": "rsync", "remotePath": "/x", "host": "h"}]
        config["release"] = {"enabled": True, "target": folder_name.lower()}
        captured.append(folder_name)
        return True, config, []

    monkeypatch.setattr(Start, "validate_setup_json_v2", fake_validate)
    monkeypatch.setattr(Start, "get_env", lambda: ENV_STUB)
    monkeypatch.setattr(Start, "check_src_folder", lambda *_a: (True, []))

    folders = Start.scan_setup_folders()
    assert folders, "至少要掃到一個產品"
    for info in folders:
        assert info["release"]["target"] == info["folder_name"].lower()


class _StubCycle:
    def __init__(self, needs_publish=True):
        self.needs_publish = needs_publish


def test_publish_gate_requires_every_file_and_a_successful_upload():
    """上傳失敗、或 20 份沒產齊,就不准 publish。"""
    import release_flow

    target = set(range(10, 30))
    cycle = _StubCycle()

    assert release_flow.should_publish(cycle, target, target, True) is True
    # 少一份 -> 不 publish
    assert release_flow.should_publish(cycle, target, target - {29}, True) is False
    # 上傳沒成功 -> 不 publish
    assert release_flow.should_publish(cycle, target, target, False) is False
    # 已經 publish 過 / 已放棄 -> 不再 publish
    assert release_flow.should_publish(_StubCycle(needs_publish=False), target, target, True) is False
    # 沒開發布的產品(cycle 是 None)-> 永遠不 publish
    assert release_flow.should_publish(None, target, target, True) is False


@pytest.mark.parametrize(
    "url,expected_code",
    [
        ("https://server.axggame.com/download/aig/output/index", "aig"),
        ("https://server.axggame.com/download/aig1/output/index", "aig1"),
        ("https://server.axggame.com/download/aug/output/index", "aug"),
    ],
)
def test_download_code_is_not_the_release_target(url, expected_code):
    """getNeedURL 解析出來的 code 是**下載目標**,不是發布目標。

    aig4 與 aig5 的 getNeedURL 都是 .../download/aig/... -> code 都是 "aig",但發布目標
    必須是 aig4 / aig5 兩條各自遞增的版本線。所以 release.target 預設取資料夾名,
    絕不能拿 code 代入。
    """
    from urllib.parse import urlparse

    parts = [p for p in urlparse(url).path.split("/") if p]
    code = None
    for i in range(len(parts) - 3):
        if parts[i] == "download" and parts[i + 2] == "output" and parts[i + 3] == "index":
            code = parts[i + 1].lower()
    assert code == expected_code
