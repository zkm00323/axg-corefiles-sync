# -*- coding: utf-8 -*-
"""發布版本號流程的測試。

這組測試**一個封包都不會送出去**:所有 HTTP 都走注入的假 session,所有 respack 呼叫
都走注入的假 runner。跑這支測試不會碰到 server.axggame.com,也不會觸發任何一次真的
發布。

跑法:
    build\\venv\\Scripts\\python.exe -m pytest build/tests -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import release_flow  # noqa: E402


# ---------------------------------------------------------------------------
# 測試替身
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """記錄每一次 POST,並依序吐出預先排好的回應。

    `calls` 是整組測試的主要斷言對象:「開關關掉時完全不呼叫 API」這條,靠的就是
    len(calls) == 0。
    """

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "body": json, "headers": headers, "timeout": timeout})
        if not self._responses:
            return FakeResponse(200, {})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_client(session, **kwargs):
    kwargs.setdefault("retry_delay", 0)
    kwargs.setdefault("sleep", lambda _s: None)
    kwargs.setdefault("log", lambda *_a, **_k: None)
    return release_flow.ReleaseClient("https://mock.invalid", "test-token", session=session, **kwargs)


def make_cycle(tmp_path, session, folder="aig5", commit="c" * 40, writer=None, **cfg_over):
    config = {
        "enabled": True,
        "target": folder,
        "apiBaseUrl": "https://mock.invalid",
        "maxPublishAttempts": 3,
        "versionWriter": writer or {"mode": "plaintext-json", "path": "Res/Version.json", "versionKey": "Version"},
    }
    config.update(cfg_over)
    stamped = []

    def fake_writer(_writer, _gen, _product, version, _secrets, _runner, _log):
        stamped.append(version)

    cycle = release_flow.ReleaseCycle(
        folder_name=folder,
        product_dir=str(tmp_path / folder),
        gen_path=str(tmp_path / folder / "gen"),
        release_config=config,
        secrets={"token": "test-token"},
        state_dir=str(tmp_path / "state"),
        repo_root=str(tmp_path),
        client=make_client(session),
        log=lambda *_a, **_k: None,
        commit_resolver=lambda _root, _folder: commit,
        version_writer=fake_writer,
    )
    cycle.stamped = stamped
    return cycle


# ---------------------------------------------------------------------------
# 1. 開關關閉時完全不碰 API
# ---------------------------------------------------------------------------

def test_release_absent_means_disabled():
    config, errors = release_flow.parse_release_config({"vmpFiles": ["*.exe"]}, "aig4")
    assert config is None
    assert errors == []


def test_release_enabled_false_means_disabled():
    config, errors = release_flow.parse_release_config(
        {"release": {"enabled": False, "target": "aig5"}}, "aig5"
    )
    assert config is None
    assert errors == []


def test_disabled_product_never_builds_a_client(tmp_path):
    """關閉的產品連 ReleaseCycle 都不會被建構,自然一個封包都不會送。

    Start.py 那側是 `if release_config:` —— parse 回 None 就整條線都不存在。
    """
    session = FakeSession()
    config, errors = release_flow.parse_release_config({"release": {"enabled": False}}, "aig4")
    assert (config, errors) == (None, [])
    assert session.calls == []


def test_enabled_but_incomplete_config_is_an_error_not_a_silent_skip():
    config, errors = release_flow.parse_release_config(
        {"release": {"enabled": True, "target": "aig5"}}, "aig5"
    )
    assert config is None
    assert any("versionWriter" in e for e in errors)


def test_enabled_without_token_fails_validation():
    config, errors = release_flow.parse_release_config(
        {
            "release": {
                "enabled": True,
                "versionWriter": {"mode": "plaintext-json", "path": "Res/Version.json"},
            }
        },
        "aig5",
    )
    assert errors == []
    assert config["target"] == "aig5"
    runtime_errors = release_flow.validate_release_runtime(config, {"token": ""})
    assert any("release token" in e for e in runtime_errors)


def test_target_defaults_to_folder_name_lowercased():
    config, errors = release_flow.parse_release_config(
        {
            "release": {
                "enabled": True,
                "versionWriter": {"mode": "plaintext-json", "path": "Res/Version.json"},
            }
        },
        "AIG5",
    )
    assert errors == []
    assert config["target"] == "aig5"


# ---------------------------------------------------------------------------
# 2. reserve 重試不會連續 +2
# ---------------------------------------------------------------------------

def test_reserve_is_called_once_per_commit(tmp_path):
    session = FakeSession([
        FakeResponse(200, {"target": "aig5", "reservedVersion": 46, "reservationId": "r1", "alreadyReserved": False}),
    ])
    cycle = make_cycle(tmp_path, session)
    assert cycle.ensure_reserved() == (True, True)
    assert cycle.version == 46
    # 同一個 commit 再跑一百輪也不會再打一次 API。
    for _ in range(100):
        assert cycle.ensure_reserved() == (True, False)
    assert len(session.calls) == 1


def test_reserve_survives_process_restart_without_bumping(tmp_path):
    """重啟後從狀態檔讀回同一個號,不再打 API —— 版本號不會因為重啟而 +1。"""
    session = FakeSession([
        FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1", "alreadyReserved": False}),
    ])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()

    session2 = FakeSession()
    cycle2 = make_cycle(tmp_path, session2)  # 同一個 state_dir、同一個 commit
    assert cycle2.ensure_reserved() == (True, False)
    assert cycle2.version == 46
    assert session2.calls == []


def test_reservation_id_is_derived_from_commit(tmp_path):
    """冪等標籤綁 commit sha,所以「同一次發布重試」在 server 那側是同一筆預約。"""
    commit = "a" * 40
    session = FakeSession([FakeResponse(200, {"reservedVersion": 46, "reservationId": "x"})])
    cycle = make_cycle(tmp_path, session, commit=commit)
    cycle.ensure_reserved()
    assert session.calls[0]["body"] == {"reservationId": "aig5-" + commit[:12]}


def test_reserve_already_reserved_returns_same_number(tmp_path):
    """server 回 alreadyReserved=true 時就是把上一次沒被 publish 的號交還,不是 +2。"""
    session = FakeSession([
        FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1", "alreadyReserved": True}),
    ])
    cycle = make_cycle(tmp_path, session)
    assert cycle.ensure_reserved() == (True, True)
    assert cycle.version == 46


def test_reserve_network_failure_blocks_the_pass(tmp_path):
    """reserve 失敗 -> 這一輪不打包。沒有版本號就打包等於送出帶舊版號的成品。"""
    session = FakeSession([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    cycle = make_cycle(tmp_path, session)
    assert cycle.ensure_reserved() == (False, False)
    assert cycle.version is None
    assert len(session.calls) == 3  # 有重試上限,不是無限重打


def test_reserve_terminal_error_does_not_retry(tmp_path):
    session = FakeSession([
        FakeResponse(401, {"message": "bad token", "errorCode": "RELEASE_TOKEN_INVALID"}),
    ])
    cycle = make_cycle(tmp_path, session)
    assert cycle.ensure_reserved() == (False, False)
    assert len(session.calls) == 1


def test_reserve_uses_release_token_header(tmp_path):
    session = FakeSession([FakeResponse(200, {"reservedVersion": 1, "reservationId": "r"})])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    assert session.calls[0]["headers"]["X-Release-Token"] == "test-token"
    assert session.calls[0]["timeout"] == release_flow.DEFAULT_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# 3. 多個 commit 一次拉進來 / 不同產品互不影響
# ---------------------------------------------------------------------------

def test_three_commits_pulled_at_once_bump_only_once(tmp_path):
    """連推三個 commit 被 fetch 成一次:Src 的「最後動到它的 commit」只有一個 sha。"""
    session = FakeSession([FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"})])
    cycle = make_cycle(tmp_path, session, commit="d" * 40)
    cycle.ensure_reserved()

    # 打包機重啟(補拉),HEAD 已經是第三個 commit,但動到 Src 的還是同一個 sha。
    session2 = FakeSession()
    cycle2 = make_cycle(tmp_path, session2, commit="d" * 40)
    cycle2.ensure_reserved()
    assert cycle2.version == 46
    assert session2.calls == []


def test_new_commit_touching_src_bumps_again(tmp_path):
    session = FakeSession([FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"})])
    make_cycle(tmp_path, session, commit="d" * 40).ensure_reserved()

    session2 = FakeSession([FakeResponse(200, {"reservedVersion": 47, "reservationId": "r2"})])
    cycle2 = make_cycle(tmp_path, session2, commit="e" * 40)
    assert cycle2.ensure_reserved() == (True, True)  # version_changed=True -> 舊成品作廢
    assert cycle2.version == 47


def test_products_do_not_interfere(tmp_path):
    """一個 commit 只動 aig5 的 Src 時,aig4 的 sha 沒變 -> aig4 不會被連坐 +1。"""
    s5 = FakeSession([FakeResponse(200, {"reservedVersion": 46, "reservationId": "r5"})])
    s4 = FakeSession([FakeResponse(200, {"reservedVersion": 12, "reservationId": "r4"})])
    c5 = make_cycle(tmp_path, s5, folder="aig5", commit="5" * 40)
    c4 = make_cycle(tmp_path, s4, folder="aig4", commit="4" * 40)
    c5.ensure_reserved()
    c4.ensure_reserved()
    assert c5.version == 46 and c4.version == 12

    # aig5 又有新 commit,aig4 完全不動。
    s5b = FakeSession([FakeResponse(200, {"reservedVersion": 47, "reservationId": "r5b"})])
    s4b = FakeSession()
    assert make_cycle(tmp_path, s5b, folder="aig5", commit="6" * 40).ensure_reserved() == (True, True)
    assert make_cycle(tmp_path, s4b, folder="aig4", commit="4" * 40).ensure_reserved() == (True, False)
    assert s4b.calls == []

    # 兩個產品的狀態檔是分開的,沒有共用鎖也不會互相蓋掉。
    assert os.path.isfile(str(tmp_path / "state" / "aig5.json"))
    assert os.path.isfile(str(tmp_path / "state" / "aig4.json"))
    with open(str(tmp_path / "state" / "aig4.json"), encoding="utf-8") as handle:
        assert json.load(handle)["reservedVersion"] == 12


def test_no_commit_resolvable_blocks_the_pass(tmp_path):
    session = FakeSession()
    cycle = make_cycle(tmp_path, session, commit=None)
    assert cycle.ensure_reserved() == (False, False)
    assert session.calls == []


# ---------------------------------------------------------------------------
# 4. publish 的前提與失敗處理
# ---------------------------------------------------------------------------

def test_publish_sends_version_and_reservation(tmp_path):
    session = FakeSession([
        FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"}),
        FakeResponse(200, {"published": True, "version": {"latestVersion": 46, "latestBumpedAt": "t"}}),
    ])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    assert cycle.try_publish() is True
    assert session.calls[1]["url"].endswith("/release/aig5/publish")
    assert session.calls[1]["body"] == {"version": 46, "reservationId": "r1"}
    assert cycle.needs_publish is False


def test_publish_is_not_repeated_after_success(tmp_path):
    session = FakeSession([
        FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"}),
        FakeResponse(200, {"published": True, "version": {}}),
    ])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    cycle.try_publish()
    for _ in range(10):
        assert cycle.try_publish() is False
    assert len(session.calls) == 2


def test_publish_false_noop_is_still_a_success(tmp_path):
    """server 回 published:false 代表這個版號已經是 latest,是 no-op,不是失敗。"""
    session = FakeSession([
        FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"}),
        FakeResponse(200, {"published": False, "version": {}}),
    ])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    assert cycle.try_publish() is True
    assert cycle.needs_publish is False


def test_publish_transient_failure_gives_up_after_the_limit(tmp_path):
    """publish 失敗不可以讓線程無限重打:次數用完就放棄,並且不再嘗試。"""
    session = FakeSession(
        [FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"})]
        + [FakeResponse(500, {"message": "boom"})] * 100
    )
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    reserve_calls = len(session.calls)

    for _ in range(3):  # maxPublishAttempts=3
        assert cycle.try_publish() is False
    assert cycle.needs_publish is False  # 已放棄
    calls_after_giving_up = len(session.calls)

    for _ in range(20):
        assert cycle.try_publish() is False
    assert len(session.calls) == calls_after_giving_up  # 放棄之後一個封包都不再送

    state = release_flow.load_state(cycle.state_file)
    assert state["publishGaveUp"] is True
    assert state["publishAttempts"] == 3
    assert calls_after_giving_up > reserve_calls


def test_publish_terminal_failure_gives_up_immediately(tmp_path):
    session = FakeSession([
        FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"}),
        FakeResponse(409, {"message": "behind", "errorCode": "RELEASE_VERSION_BEHIND"}),
    ])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    assert cycle.try_publish() is False
    assert cycle.needs_publish is False
    assert len(session.calls) == 2  # terminal -> 連 client 內部都不重試


def test_publish_recovers_on_a_later_pass(tmp_path):
    session = FakeSession([
        FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"}),
        FakeResponse(500, {"message": "boom"}),
        FakeResponse(500, {"message": "boom"}),
        FakeResponse(500, {"message": "boom"}),   # 第一輪:client 內部重試 3 次
        FakeResponse(200, {"published": True, "version": {}}),  # 第二輪:成功
    ])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    assert cycle.try_publish() is False
    assert cycle.needs_publish is True
    assert cycle.try_publish() is True


def test_publish_is_not_attempted_without_a_reserved_version(tmp_path):
    session = FakeSession()
    cycle = make_cycle(tmp_path, session)
    assert cycle.needs_publish is False
    assert cycle.try_publish() is False
    assert session.calls == []


# ---------------------------------------------------------------------------
# 5. 寫版本號
# ---------------------------------------------------------------------------

def test_plaintext_writer_merges_version(tmp_path):
    gen = tmp_path / "gen" / "Res"
    gen.mkdir(parents=True)
    target = gen / "Version.json"
    target.write_text(json.dumps({"productId": "aig", "Title": "AIG"}), encoding="utf-8")

    release_flow.write_version(
        {"mode": "plaintext-json", "path": "Res/Version.json", "versionKey": "Version"},
        str(tmp_path / "gen"), str(tmp_path), 46, {}, log=lambda *_a: None,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {"productId": "aig", "Title": "AIG", "Version": 46}


def test_plaintext_writer_raises_when_file_is_missing(tmp_path):
    (tmp_path / "gen").mkdir()
    with pytest.raises(release_flow.VersionWriteError):
        release_flow.write_version(
            {"mode": "plaintext-json", "path": "Res/Version.json", "versionKey": "Version"},
            str(tmp_path / "gen"), str(tmp_path), 46, {}, log=lambda *_a: None,
        )


def test_respack_writer_only_ships_version_json(tmp_path):
    """respack 只碰 --source 裡出現過的檔案,所以來源目錄裡只能有 Version.json。"""
    product = tmp_path / "aig5"
    product.mkdir()
    (product / "Version.template.json").write_text(
        json.dumps({"productId": "aig", "DownloadUrl": "https://example.invalid"}), encoding="utf-8"
    )
    gen = tmp_path / "gen"
    gen.mkdir()
    seen = {}

    def runner(cmd):
        seen["cmd"] = cmd
        source = cmd[cmd.index("--source") + 1]
        seen["files"] = sorted(os.listdir(source))
        with open(os.path.join(source, "Version.json"), encoding="utf-8") as handle:
            seen["payload"] = json.load(handle)
        return FakeCompleted(0)

    release_flow.write_version(
        {"mode": "respack", "templatePath": "Version.template.json", "treeSubdir": "", "versionKey": "Version"},
        str(gen), str(product), 46,
        {"respackPath": "respack.exe", "keyFile": "key.bin"},
        runner=runner, log=lambda *_a: None,
    )
    assert seen["files"] == ["Version.json"]
    assert seen["payload"]["Version"] == 46
    assert seen["payload"]["productId"] == "aig"
    assert seen["cmd"][1] == "update"
    assert seen["cmd"][cmd_index(seen["cmd"], "--runtime")] == str(gen)


def cmd_index(cmd, flag):
    return cmd.index(flag) + 1


def test_respack_writer_raises_on_nonzero_exit(tmp_path):
    product = tmp_path / "aig5"
    product.mkdir()
    (product / "Version.template.json").write_text('{"productId":"aig"}', encoding="utf-8")
    gen = tmp_path / "gen"
    gen.mkdir()
    with pytest.raises(release_flow.VersionWriteError):
        release_flow.write_version(
            {"mode": "respack", "templatePath": "Version.template.json", "treeSubdir": "", "versionKey": "Version"},
            str(gen), str(product), 46,
            {"respackPath": "respack.exe", "keyFile": "key.bin"},
            runner=lambda _cmd: FakeCompleted(5, stderr="nope"), log=lambda *_a: None,
        )


def test_cycle_stamp_uses_the_reserved_version(tmp_path):
    session = FakeSession([FakeResponse(200, {"reservedVersion": 46, "reservationId": "r1"})])
    cycle = make_cycle(tmp_path, session)
    cycle.ensure_reserved()
    cycle.stamp()
    assert cycle.stamped == [46]


def test_cycle_stamp_refuses_without_a_version(tmp_path):
    cycle = make_cycle(tmp_path, FakeSession())
    with pytest.raises(release_flow.VersionWriteError):
        cycle.stamp()


# ---------------------------------------------------------------------------
# 6. 密鑰來源
# ---------------------------------------------------------------------------

def test_env_var_beats_env_json():
    secrets = release_flow.resolve_release_secrets(
        {"release": {"token": "from-json"}}, environ={"AXG_RELEASE_TOKEN": "from-env"}
    )
    assert secrets["token"] == "from-env"


def test_env_json_is_the_fallback():
    secrets = release_flow.resolve_release_secrets({"release": {"token": "from-json"}}, environ={})
    assert secrets["token"] == "from-json"


def test_no_token_anywhere_is_empty_not_a_crash():
    assert release_flow.resolve_release_secrets({}, environ={})["token"] == ""


def test_setup_json_cannot_carry_a_token():
    """Setup.json 是 commit 進 repo 的,所以它不接受任何密鑰欄位——多寫也不會被讀。"""
    config, errors = release_flow.parse_release_config(
        {
            "release": {
                "enabled": True,
                "token": "oops-a-secret-in-git",
                "versionWriter": {"mode": "plaintext-json", "path": "Res/Version.json"},
            }
        },
        "aig5",
    )
    assert errors == []
    assert "token" not in config


# ---------------------------------------------------------------------------
# 7. 狀態檔的韌性
# ---------------------------------------------------------------------------

def test_corrupt_state_file_does_not_crash(tmp_path):
    path = str(tmp_path / "state" / "aig5.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert release_flow.load_state(path) == {}


def test_state_path_sanitises_the_folder_name(tmp_path):
    assert release_flow.state_path(str(tmp_path), "aig/../5").endswith("aig_.._5.json")
