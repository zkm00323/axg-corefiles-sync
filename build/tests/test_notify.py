# -*- coding: utf-8 -*-
"""新版上傳通知的測試——刻意跟 release 開關脫鉤。

這條通知現在的規則只有兩件事:
  1. 這一輪 process() 所有 remote target 都上傳成功(last_sync_ok=True)。
  2. Setup/<folder>/Src 最後一次被動到的 commit sha(release_flow.source_commit)
     跟上次已通知的值不一樣。

跟 Setup.json 的 release.enabled 完全無關——這支測試特意用 release=None 的產品跑過
整條 process(),證明沒開發布的產品一樣會收到通知。去重狀態存在獨立的
build/notify-state/<folder>.json,不借用 release-state。
"""

import json
import os
import sys

import pytest

BUILD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BUILD_DIR)

import Start  # noqa: E402
import release_flow  # noqa: E402


FILE_AMOUNT = 2
START_INDEX = 1


class _StopLoop(Exception):
    """測試用:第一輪 reconcile 結束時把 process() 整個彈出來。"""


def run_one_pass(info):
    try:
        Start.process(info)
    except _StopLoop:
        return
    raise AssertionError("process() 沒有跑到等待迴圈就結束了")


class FakeCompleted:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def build_product(tmp_path, folder_name="aig4"):
    """一個**沒有開 release** 的產品——證明通知跟 release.enabled 無關。"""
    product = tmp_path / folder_name
    src = product / "Src"
    (src / "Res").mkdir(parents=True)
    (src / "AIAIM.exe").write_bytes(b"MZ fake exe")
    (src / "Res" / "Version.json").write_text(
        json.dumps({"productId": "aig"}), encoding="utf-8"
    )

    return {
        "folder_name": folder_name,
        "folder_path": str(product),
        "remoteTargets": [
            {"name": "default", "type": "rsync", "remotePath": "/srv/x", "host": "u@h", "rsync_use_wsl": False}
        ],
        "getNeedURL": "https://mock.invalid/download/aig/output/index",
        "vmpFiles": ["*.exe"],
        "fileAmount": FILE_AMOUNT,
        "packageFormat": "zip",
        "archiveToolPath": "",
        "release": None,
    }


@pytest.fixture
def harness(tmp_path, monkeypatch):
    state = {"sync_calls": 0, "notify_calls": []}

    env_stub = {
        "archiveToolPath": "",
        "host": "u@h",
        "telegramNotifier": {"url": "https://notify.invalid/notify"},
    }
    monkeypatch.setattr(Start, "get_env", lambda: dict(env_stub))
    monkeypatch.setattr(Start, "restart_application", lambda: None)
    monkeypatch.setattr(Start.shutil, "which", lambda name: "C:/fake/" + str(name))
    monkeypatch.setattr(Start.subprocess, "run", lambda *_a, **_k: FakeCompleted(0))

    def fake_execute(cmd, **_kwargs):
        state["sync_calls"] += 1
        return state.get("sync_ok", True)

    monkeypatch.setattr(Start, "execute_with_timeout", fake_execute)

    class FakeIndexResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"index": START_INDEX}

    monkeypatch.setattr(Start.requests, "get", lambda *_a, **_k: FakeIndexResponse())

    def fake_notify_telegram(url, project, message):
        state["notify_calls"].append({"url": url, "project": project, "message": message})

    monkeypatch.setattr(Start, "notify_telegram", fake_notify_telegram)

    def fake_sleep(_seconds):
        raise _StopLoop()

    monkeypatch.setattr(Start.time, "sleep", fake_sleep)
    monkeypatch.setattr(Start, "RELEASE_STATE_DIR", str(tmp_path / "release-state"))
    monkeypatch.setattr(Start, "NOTIFY_STATE_DIR", str(tmp_path / "notify-state"))
    monkeypatch.setattr(Start, "PROJECT_ROOT", str(tmp_path))

    state["commit"] = "a" * 40
    monkeypatch.setattr(
        release_flow, "source_commit", lambda _root, _folder: state["commit"]
    )

    Start.process_stop = False
    Start.threads_count = 0
    yield state
    Start.process_stop = False
    Start.threads_count = 0


def test_new_signal_sends_one_notification(tmp_path, harness):
    info = build_product(tmp_path)
    run_one_pass(info)

    assert len(harness["notify_calls"]) == 1
    call = harness["notify_calls"][0]
    assert call["project"] == "aig4"
    assert "version_signal=" + ("a" * 12) in call["message"]
    assert "remote_targets=default" in call["message"]

    state_file = release_flow.state_path(str(tmp_path / "notify-state"), "aig4")
    assert os.path.isfile(state_file)
    saved = json.loads(open(state_file, "r", encoding="utf-8").read())
    assert saved["lastNotifiedVersionSignal"] == "a" * 40


def test_same_signal_does_not_notify_again(tmp_path, harness):
    info = build_product(tmp_path)
    run_one_pass(info)
    assert len(harness["notify_calls"]) == 1

    # 模擬「重啟」:process() 再跑一輪,commit sha 沒變。
    Start.process_stop = False
    Start.threads_count = 0
    run_one_pass(info)

    assert len(harness["notify_calls"]) == 1


def test_signal_change_notifies_again(tmp_path, harness):
    info = build_product(tmp_path)
    run_one_pass(info)
    assert len(harness["notify_calls"]) == 1

    harness["commit"] = "b" * 40
    Start.process_stop = False
    Start.threads_count = 0
    run_one_pass(info)

    assert len(harness["notify_calls"]) == 2
    assert "version_signal=" + ("b" * 12) in harness["notify_calls"][1]["message"]


def test_upload_failure_does_not_notify(tmp_path, harness):
    harness["sync_ok"] = False
    info = build_product(tmp_path)
    run_one_pass(info)

    assert harness["notify_calls"] == []


def test_no_url_configured_does_not_notify(tmp_path, harness, monkeypatch):
    monkeypatch.setattr(Start, "get_env", lambda: {"archiveToolPath": "", "host": "u@h"})
    info = build_product(tmp_path)
    run_one_pass(info)

    assert harness["notify_calls"] == []


def test_notification_is_independent_of_release_enabled(tmp_path, harness, monkeypatch):
    """release=None(開關完全沒開)的產品一樣會收到通知——這是本輪要達成的解耦。"""
    calls = []

    class Tripwire:
        def __init__(self, *_a, **_k):
            calls.append("constructed")

    monkeypatch.setattr(release_flow, "ReleaseClient", Tripwire)

    info = build_product(tmp_path)
    assert info["release"] is None
    run_one_pass(info)

    assert calls == []  # release API 完全沒被碰
    assert len(harness["notify_calls"]) == 1  # 但通知照樣發生
