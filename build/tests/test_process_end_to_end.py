# -*- coding: utf-8 -*-
"""把 Start.process() 整條跑一遍(不連網、不跑 VMProtect、不跑 rsync)。

單元測試只能證明每個零件自己對;這支證明**串起來之後也對**——特別是這兩件事:

  1. 版本號真的寫進了「送出去的那個壓縮檔」裡。測試會把產出的 zip 打開,讀出
     Res/Version.json,比對版本號。只驗證「有呼叫 stamp()」是不夠的:順序放錯
     (寫在壓縮之後)測試照樣綠,但使用者拿到的包裡是舊版號。
  2. publish 只在 fileAmount 份**全部**產完並上傳成功之後才發生一次。

外部指令全部被替身接管:VMProtect / rsync / WSL 探測 / server 都不會真的被呼叫。
"""

import json
import os
import sys
import zipfile

import pytest

BUILD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BUILD_DIR)

import Start  # noqa: E402
import release_flow  # noqa: E402


FILE_AMOUNT = 3
START_INDEX = 7


class _StopLoop(Exception):
    """測試用:第一輪 reconcile 結束時把 process() 整個彈出來。"""


def run_one_pass(info):
    """跑剛好一輪 reconcile。"""
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


class FakeReleaseClient:
    def __init__(self, base_url, token, **_kwargs):
        self.base_url = base_url
        self.token = token
        self.reserve_calls = []
        self.publish_calls = []
        FakeReleaseClient.last = self

    def reserve(self, target, reservation_id=None):
        self.reserve_calls.append((target, reservation_id))
        return {"reservedVersion": 46, "reservationId": reservation_id, "alreadyReserved": False}

    def publish(self, target, version, reservation_id=None):
        self.publish_calls.append((target, version, reservation_id))
        return {"published": True, "version": {"latestVersion": version}}


def build_product(tmp_path, with_release=True):
    product = tmp_path / "aig5"
    src = product / "Src"
    (src / "Res").mkdir(parents=True)
    (src / "AIAIM.exe").write_bytes(b"MZ fake exe")
    (src / "Res" / "Version.json").write_text(
        json.dumps({"productId": "aig", "Title": "AIG"}), encoding="utf-8"
    )

    info = {
        "folder_name": "aig5",
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
    if with_release:
        info["release"] = {
            "enabled": True,
            "target": "aig5",
            "apiBaseUrl": "https://mock.invalid",
            "maxPublishAttempts": 3,
            "versionWriter": {
                "mode": "plaintext-json",
                "path": "Res/Version.json",
                "versionKey": "Version",
            },
        }
    return info


@pytest.fixture
def harness(tmp_path, monkeypatch):
    state = {"sync_calls": 0, "passes": 0}

    monkeypatch.setattr(Start, "get_env", lambda: {"archiveToolPath": "", "host": "u@h"})
    monkeypatch.setattr(Start, "restart_application", lambda: None)
    monkeypatch.setattr(Start.shutil, "which", lambda name: "C:/fake/" + str(name))
    monkeypatch.setattr(Start.subprocess, "run", lambda *_a, **_k: FakeCompleted(0))

    def fake_execute(cmd, **_kwargs):
        state["sync_calls"] += 1
        return True

    monkeypatch.setattr(Start, "execute_with_timeout", fake_execute)

    class FakeIndexResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"index": START_INDEX}

    monkeypatch.setattr(Start.requests, "get", lambda *_a, **_k: FakeIndexResponse())

    # 跑完第一輪就收工。刻意用例外跳出整個 process(),而不是設 process_stop ——
    # process_stop 那條路徑會把 Output 全部刪光(這是既有行為),測試就沒東西可以驗了。
    def fake_sleep(_seconds):
        state["passes"] += 1
        raise _StopLoop()

    monkeypatch.setattr(Start.time, "sleep", fake_sleep)
    monkeypatch.setattr(release_flow, "ReleaseClient", FakeReleaseClient)
    monkeypatch.setattr(
        release_flow, "source_commit", lambda _root, _folder: "f" * 40
    )
    monkeypatch.setattr(Start, "RELEASE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(Start, "PROJECT_ROOT", str(tmp_path))

    Start.process_stop = False
    Start.threads_count = 0
    yield state
    Start.process_stop = False
    Start.threads_count = 0


def read_version_from_zip(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if n.endswith("Res/Version.json")]
        assert names, archive.namelist()
        return json.loads(archive.read(names[0]).decode("utf-8"))


def test_enabled_product_stamps_the_version_into_every_shipped_zip(tmp_path, harness):
    info = build_product(tmp_path, with_release=True)
    run_one_pass(info)

    output = tmp_path / "aig5" / "Output"
    produced = sorted(os.listdir(str(output)))
    assert produced == [f"aig_{i}.zip" for i in range(START_INDEX, START_INDEX + FILE_AMOUNT)]

    for name in produced:
        payload = read_version_from_zip(str(output / name))
        # 這是整條線最重要的一個斷言:使用者真正拿到的那個檔案裡,版本號是取到的號。
        assert payload["Version"] == 46, name
        assert payload["productId"] == "aig"

    client = FakeReleaseClient.last
    assert client.reserve_calls == [("aig5", "aig5-" + "f" * 12)]
    assert client.publish_calls == [("aig5", 46, "aig5-" + "f" * 12)]

    # Src 是 git 追蹤的成品目錄,一個位元組都不能被動到。
    src_version = json.loads(
        (tmp_path / "aig5" / "Src" / "Res" / "Version.json").read_text(encoding="utf-8")
    )
    assert "Version" not in src_version


def test_disabled_product_never_touches_the_release_api(tmp_path, harness, monkeypatch):
    """開關沒開 -> 一次 reserve/publish 都不會發生,產出的包也不會被塞版本號。"""
    calls = []

    class Tripwire:
        def __init__(self, *_a, **_k):
            calls.append("constructed")

    monkeypatch.setattr(release_flow, "ReleaseClient", Tripwire)

    info = build_product(tmp_path, with_release=False)
    run_one_pass(info)

    assert calls == []
    output = tmp_path / "aig5" / "Output"
    produced = sorted(os.listdir(str(output)))
    assert len(produced) == FILE_AMOUNT
    payload = read_version_from_zip(str(output / produced[0]))
    assert "Version" not in payload  # 逐字沿用 Src 的內容


def test_publish_does_not_happen_when_uploads_fail(tmp_path, harness, monkeypatch):
    monkeypatch.setattr(Start, "execute_with_timeout", lambda *_a, **_k: False)

    info = build_product(tmp_path, with_release=True)
    run_one_pass(info)

    client = FakeReleaseClient.last
    assert client.reserve_calls  # 取號有發生(打包前就要取)
    assert client.publish_calls == []  # 但沒有上傳成功就絕不 publish


def test_reserve_failure_blocks_packaging_entirely(tmp_path, harness, monkeypatch):
    """取不到號就不產檔——寧可什麼都不出,也不要出一包帶舊版號的成品。"""

    class FailingClient(FakeReleaseClient):
        def reserve(self, target, reservation_id=None):
            raise release_flow.ReleaseApiError("server down")

    monkeypatch.setattr(release_flow, "ReleaseClient", FailingClient)

    info = build_product(tmp_path, with_release=True)
    run_one_pass(info)

    output = tmp_path / "aig5" / "Output"
    assert os.listdir(str(output)) == []
    assert FakeReleaseClient.last.publish_calls == []


def test_stamp_failure_does_not_produce_a_package(tmp_path, harness, monkeypatch):
    """寫版本號失敗(例如 Version.json 不在 Src 裡)-> 這一輪不產出任何檔案。"""
    info = build_product(tmp_path, with_release=True)
    os.remove(str(tmp_path / "aig5" / "Src" / "Res" / "Version.json"))

    run_one_pass(info)

    output = tmp_path / "aig5" / "Output"
    assert os.listdir(str(output)) == []
    assert FakeReleaseClient.last.publish_calls == []
