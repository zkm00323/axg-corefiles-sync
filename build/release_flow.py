"""發布版本號流程(reserve → 寫號 → 打包 → publish)。

這支模組刻意跟 Start.py 分開,理由有兩個:

1. Start.py 的 process() 是一個一千行的巨大閉包,任何邏輯放進去都測不到。這裡的每一
   個型別都可以在不啟動打包線程、不碰網路、不碰 VMProtect 的情況下單獨驗證。
2. 這條線是**預設關閉的可選功能**。沒有在 Setup.json 打開 `release.enabled` 的產品,
   Start.py 連 ReleaseCycle 都不會建構,行為與加這支模組之前逐字相同。aig4 這種線上
   已經有舊客戶端在吃的產品,靠的就是這條界線。

流程(對應 server 的契約):

    偵測到某產品的 Setup/<product>/Src 有新 commit
      → POST /release/<target>/reserve 取號(冪等標籤綁 commit sha,重試不會連續 +2)
      → 把號碼寫進待打包內容的 Res/Version.json
      → VMProtect + 打包 + 上傳 fileAmount 份
      → **全部都在遠端了**才 POST /release/<target>/publish

「打包前取號」不是可以商量的順序:Setup/<product>/Src 是已經打包好的成品,publish
之後才 +1 會讓 server 說 46、客戶端讀出來 45,客戶端會無限提示更新。
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid


# ---------------------------------------------------------------------------
# 設定解析
# ---------------------------------------------------------------------------

DEFAULT_API_BASE_URL = "https://server.axggame.com"
DEFAULT_MAX_PUBLISH_ATTEMPTS = 5
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_RETRIES = 3

_TARGET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_WRITER_MODES = ("plaintext-json", "respack")


class ReleaseConfigError(Exception):
    """Setup.json / env.json 的 release 設定有問題。"""


def parse_release_config(setup_config, folder_name):
    """從 Setup.json 的內容解析出 release 設定。

    回傳 (config_or_None, errors)。`release` 這個 key 不存在、或 `enabled` 不是 true
    時一律回 (None, []) —— 也就是「這個產品不走發布流程」,呼叫端必須據此完全不碰
    網路。任何**打開了但設定不完整**的情況都回 errors,呼叫端要讓這個產品驗證失敗,
    不可以退化成「安靜地照舊打包但不發布」:那會讓使用者以為版本號有在動。
    """
    raw = setup_config.get("release")
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["'release' must be an object"]

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        return None, ["'release.enabled' must be a boolean"]
    if not enabled:
        return None, []

    errors = []

    target = raw.get("target", folder_name.lower())
    if not isinstance(target, str) or not target.strip():
        errors.append("'release.target' must be a non-empty string")
        target = ""
    else:
        target = target.strip()
        if not _TARGET_PATTERN.match(target):
            errors.append(
                f"'release.target' must be lowercase [a-z0-9_-]: {target}"
            )

    api_base_url = raw.get("apiBaseUrl", DEFAULT_API_BASE_URL)
    if not isinstance(api_base_url, str) or not api_base_url.strip():
        errors.append("'release.apiBaseUrl' must be a non-empty string")
        api_base_url = DEFAULT_API_BASE_URL
    api_base_url = api_base_url.strip().rstrip("/")

    max_publish_attempts = raw.get("maxPublishAttempts", DEFAULT_MAX_PUBLISH_ATTEMPTS)
    if not isinstance(max_publish_attempts, int) or isinstance(max_publish_attempts, bool):
        errors.append("'release.maxPublishAttempts' must be an integer")
        max_publish_attempts = DEFAULT_MAX_PUBLISH_ATTEMPTS
    elif max_publish_attempts <= 0:
        errors.append("'release.maxPublishAttempts' must be greater than 0")
        max_publish_attempts = DEFAULT_MAX_PUBLISH_ATTEMPTS

    writer_raw = raw.get("versionWriter")
    if not isinstance(writer_raw, dict):
        errors.append("'release.versionWriter' must be an object")
        writer = None
    else:
        writer, writer_errors = _parse_version_writer(writer_raw)
        errors.extend(writer_errors)

    if errors:
        return None, errors

    return {
        "enabled": True,
        "target": target,
        "apiBaseUrl": api_base_url,
        "maxPublishAttempts": max_publish_attempts,
        "versionWriter": writer,
    }, []


def _parse_version_writer(raw):
    errors = []

    mode = raw.get("mode")
    if not isinstance(mode, str) or mode not in _WRITER_MODES:
        errors.append(
            "'release.versionWriter.mode' must be one of " + "/".join(_WRITER_MODES)
        )
        return None, errors

    version_key = raw.get("versionKey", "Version")
    if not isinstance(version_key, str) or not version_key.strip():
        errors.append("'release.versionWriter.versionKey' must be a non-empty string")
        version_key = "Version"

    writer = {"mode": mode, "versionKey": version_key.strip()}

    if mode == "plaintext-json":
        path = raw.get("path")
        if not isinstance(path, str) or not path.strip():
            errors.append("'release.versionWriter.path' must be a non-empty string")
        elif os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
            errors.append(
                "'release.versionWriter.path' must be a relative path without '..'"
            )
        else:
            writer["path"] = path.strip().replace("\\", "/")
    else:  # respack
        template_path = raw.get("templatePath", "Version.template.json")
        if not isinstance(template_path, str) or not template_path.strip():
            errors.append(
                "'release.versionWriter.templatePath' must be a non-empty string"
            )
        elif os.path.isabs(template_path) or ".." in template_path.replace("\\", "/").split("/"):
            errors.append(
                "'release.versionWriter.templatePath' must be a relative path without '..'"
            )
        else:
            writer["templatePath"] = template_path.strip().replace("\\", "/")

        tree_subdir = raw.get("treeSubdir", "")
        if not isinstance(tree_subdir, str):
            errors.append("'release.versionWriter.treeSubdir' must be a string")
        elif tree_subdir and (
            os.path.isabs(tree_subdir) or ".." in tree_subdir.replace("\\", "/").split("/")
        ):
            errors.append(
                "'release.versionWriter.treeSubdir' must be a relative path without '..'"
            )
        else:
            writer["treeSubdir"] = tree_subdir.strip().replace("\\", "/")

    return (None if errors else writer), errors


def resolve_release_secrets(env_config, environ=None):
    """取出 token 與 respack 相關路徑。

    優先序一律是**環境變數 > build/env.json**,而且 build/env.json 本身在 .gitignore
    裡。密鑰永遠不進 Setup.json —— Setup.json 是 commit 進 repo 的。
    """
    environ = os.environ if environ is None else environ
    release_env = env_config.get("release") if isinstance(env_config, dict) else None
    if not isinstance(release_env, dict):
        release_env = {}

    def pick(env_name, json_key):
        value = environ.get(env_name)
        if value is None:
            value = release_env.get(json_key)
        if not isinstance(value, str):
            return ""
        return value.strip()

    return {
        "token": pick("AXG_RELEASE_TOKEN", "token"),
        "apiBaseUrl": pick("AXG_RELEASE_API_BASE_URL", "apiBaseUrl"),
        "respackPath": pick("AXG_RESPACK_PATH", "respackPath"),
        "keyFile": pick("AXG_RES_KEY_FILE", "keyFile"),
    }


def validate_release_runtime(release_config, secrets):
    """打開了 release 但缺密鑰 / 缺工具時,回傳錯誤清單。

    刻意在掃描階段就擋掉,而不是等到打包到一半才炸:缺 token 卻照樣打包,產出的包會
    帶著上一版的版本號送給使用者,而 server 那側的 latest 沒有動 —— 那是一種不會有任
    何錯誤訊息的錯誤。
    """
    errors = []
    if not secrets.get("token"):
        errors.append(
            "'release.enabled' is true but no release token found "
            "(set AXG_RELEASE_TOKEN or build/env.json release.token)"
        )
    writer = release_config.get("versionWriter") or {}
    if writer.get("mode") == "respack":
        respack_path = secrets.get("respackPath")
        if not respack_path:
            errors.append(
                "versionWriter.mode=respack requires respack.exe "
                "(set AXG_RESPACK_PATH or build/env.json release.respackPath)"
            )
        elif not os.path.isfile(respack_path):
            errors.append(f"respack.exe not found: {respack_path}")
        key_file = secrets.get("keyFile")
        if not key_file:
            errors.append(
                "versionWriter.mode=respack requires the resource key file "
                "(set AXG_RES_KEY_FILE or build/env.json release.keyFile)"
            )
        elif not os.path.isfile(key_file):
            errors.append(f"resource key file not found: {key_file}")
    return errors


# ---------------------------------------------------------------------------
# HTTP 客戶端
# ---------------------------------------------------------------------------

class ReleaseApiError(Exception):
    """打 release API 失敗。

    `terminal=True` 代表重試沒有意義(token 錯、target 不存在、版號落後……),呼叫端
    必須停止重試並大聲記一筆,而不是無限重打。
    """

    def __init__(self, message, status=None, error_code=None, terminal=False):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.terminal = terminal


# 這些 errorCode 重打一百次也是一樣的結果。
_TERMINAL_ERROR_CODES = {
    "RELEASE_TARGET_INVALID",
    "RELEASE_TARGET_NOT_FOUND",
    "RELEASE_TOKEN_MISSING",
    "RELEASE_TOKEN_INVALID",
    "RELEASE_TOKEN_NOT_CONFIGURED",
    "RELEASE_VERSION_INVALID",
    "RELEASE_VERSION_BEHIND",
    "RELEASE_RESERVATION_MISMATCH",
}


class ReleaseClient:
    """薄薄一層 requests 包裝:固定逾時、有限重試、把 errorCode 翻成 terminal 與否。

    session 可以注入,測試就不需要真的開 socket —— 也就不會有人不小心對
    production server 打出一次真的發布。
    """

    def __init__(
        self,
        base_url,
        token,
        session=None,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_retries=DEFAULT_MAX_RETRIES,
        retry_delay=2,
        sleep=time.sleep,
        log=print,
    ):
        if session is None:
            import requests  # 延後 import,測試不必安裝 requests

            session = requests.Session()
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._session = session
        self._timeout = timeout
        self._max_retries = max(1, int(max_retries))
        self._retry_delay = retry_delay
        self._sleep = sleep
        self._log = log

    def reserve(self, target, reservation_id=None):
        body = {}
        if reservation_id:
            body["reservationId"] = reservation_id
        return self._post(f"/release/{target}/reserve", body)

    def publish(self, target, version, reservation_id=None):
        body = {"version": int(version)}
        if reservation_id:
            body["reservationId"] = reservation_id
        return self._post(f"/release/{target}/publish", body)

    def _post(self, path, body):
        url = f"{self._base_url}{path}"
        last_error = None
        for attempt in range(self._max_retries):
            try:
                response = self._session.post(
                    url,
                    json=body,
                    headers={"X-Release-Token": self._token},
                    timeout=self._timeout,
                )
            except Exception as exc:  # 連線層失敗一律當成可重試
                last_error = ReleaseApiError(f"{path} request failed: {exc}")
            else:
                status = getattr(response, "status_code", 0)
                payload = _safe_json(response)
                if 200 <= status < 300:
                    return payload if isinstance(payload, dict) else {}
                error_code = ""
                message = ""
                if isinstance(payload, dict):
                    error_code = str(payload.get("errorCode") or "")
                    message = str(payload.get("message") or "")
                terminal = error_code in _TERMINAL_ERROR_CODES or 400 <= status < 500
                last_error = ReleaseApiError(
                    f"{path} failed: HTTP {status} {error_code} {message}".strip(),
                    status=status,
                    error_code=error_code,
                    terminal=terminal,
                )
                if terminal:
                    raise last_error

            if attempt < self._max_retries - 1:
                self._log(
                    f"[Release] retry {path} in {self._retry_delay}s "
                    f"({attempt + 1}/{self._max_retries}): {last_error}"
                )
                self._sleep(self._retry_delay)

        raise last_error


def _safe_json(response):
    try:
        return response.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 狀態檔
# ---------------------------------------------------------------------------

def state_path(state_dir, folder_name):
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", folder_name)
    return os.path.join(state_dir, f"{safe}.json")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        # 狀態檔壞掉不該讓打包線程死掉。當成「沒有預約」重來一次,最差的後果是多取一
        # 個號 —— server 那側的舊預約會被下一次 publish 消費掉或留著,不會 +2。
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 「這個產品的 Src 是哪個 commit 動的」
# ---------------------------------------------------------------------------

def source_commit(repo_root, folder_name, runner=None):
    """回傳最後一個動到 Setup/<folder>/Src 的 commit sha,取不到回 None。

    用「最後動到這個路徑的 commit」而不是 HEAD,一次解掉三件事:
      - 連推三個 commit 被 fetch 成一次 → 仍然只有一個 sha → 只 +1。
      - 打包機重啟後補拉 → sha 沒變 → 不會重複取號。
      - 某次 commit 只動了別的產品 → 這個產品的 sha 沒變 → 不會被連坐 +1。
    """
    rel = f"Setup/{folder_name}/Src"
    cmd = ["git", "-C", repo_root, "log", "-1", "--format=%H", "--", rel]
    try:
        if runner is None:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        else:
            result = runner(cmd)
        if getattr(result, "returncode", 1) != 0:
            return None
        sha = (result.stdout or "").strip()
        return sha or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 把版本號寫進待打包內容
# ---------------------------------------------------------------------------

class VersionWriteError(Exception):
    """版本號沒寫進去。呼叫端**必須**讓這一輪打包失敗,不可以吞掉繼續打包。"""


def write_version(writer, gen_path, product_dir, version, secrets, runner=None, log=print):
    mode = writer["mode"]
    if mode == "plaintext-json":
        return _write_version_plaintext(writer, gen_path, version, log)
    if mode == "respack":
        return _write_version_respack(
            writer, gen_path, product_dir, version, secrets, runner, log
        )
    raise VersionWriteError(f"unsupported versionWriter mode: {mode}")


def _write_version_plaintext(writer, gen_path, version, log):
    target = os.path.join(gen_path, *writer["path"].split("/"))
    if not os.path.isfile(target):
        raise VersionWriteError(f"version file not found in gen: {target}")
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        raise VersionWriteError(f"version file is not valid JSON: {target}: {exc}")
    if not isinstance(payload, dict):
        raise VersionWriteError(f"version file is not a JSON object: {target}")
    payload[writer["versionKey"]] = int(version)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    log(f"[Release] stamped {writer['versionKey']}={version} into {writer['path']}")
    return target


def _write_version_respack(writer, gen_path, product_dir, version, secrets, runner, log):
    """用 respack update 把 Res/Version.json 換掉。

    respack 只碰 --source 裡出現過的檔案,所以來源目錄只放一個 Version.json 就等於
    「只換這一個檔案」,樹裡其它東西(Engine/*.onnx 上百 MB)一個位元組都不會重寫。
    """
    template_path = os.path.join(product_dir, *writer["templatePath"].split("/"))
    if not os.path.isfile(template_path):
        raise VersionWriteError(f"version template not found: {template_path}")
    try:
        with open(template_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        raise VersionWriteError(f"version template is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise VersionWriteError("version template is not a JSON object")
    payload[writer["versionKey"]] = int(version)

    tree_dir = gen_path
    if writer.get("treeSubdir"):
        tree_dir = os.path.join(gen_path, *writer["treeSubdir"].split("/"))
    if not os.path.isdir(tree_dir):
        raise VersionWriteError(f"encrypted tree dir not found: {tree_dir}")

    respack_path = secrets.get("respackPath")
    key_file = secrets.get("keyFile")
    if not respack_path or not key_file:
        raise VersionWriteError("respack path / key file is not configured")

    staging = tempfile.mkdtemp(prefix="axg_release_ver_")
    try:
        res_dir = os.path.join(staging, "Res")
        os.makedirs(res_dir, exist_ok=True)
        with open(os.path.join(res_dir, "Version.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))

        cmd = [
            respack_path,
            "update",
            "--source", res_dir,
            "--runtime", tree_dir,
            "--key-file", key_file,
        ]
        log(f"[Release] respack update (Version={version}) runtime={tree_dir}")
        if runner is None:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        else:
            result = runner(cmd)
        if getattr(result, "returncode", 1) != 0:
            detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
            raise VersionWriteError(
                f"respack update failed (exit {getattr(result, 'returncode', '?')}): {detail}"
            )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"[Release] stamped {writer['versionKey']}={version} via respack")
    return tree_dir


# ---------------------------------------------------------------------------
# 一條產品線的發布狀態機
# ---------------------------------------------------------------------------

class ReleaseCycle:
    """把 reserve / 寫號 / publish 串成一個對重啟安全的狀態機。

    重要的不變量:
      - 一個 process 生命週期裡,同一個 commit 的 20 份包**帶同一個版本號**。
      - reserve 的冪等標籤綁 commit sha,所以重試、重啟、fetch 到三個 commit,都只會
        停在同一個號上,不會連續 +2。
      - publish 只在「20 份都在遠端了」之後呼叫一次;失敗有次數上限,用完就放棄並大聲
        記一筆,絕不讓打包線程卡死或無限重打。
    """

    def __init__(
        self,
        folder_name,
        product_dir,
        gen_path,
        release_config,
        secrets,
        state_dir,
        repo_root,
        client,
        log=print,
        commit_resolver=None,
        version_writer=None,
    ):
        self.folder_name = folder_name
        self.product_dir = product_dir
        self.gen_path = gen_path
        self.config = release_config
        self.secrets = secrets
        self.state_file = state_path(state_dir, folder_name)
        self.repo_root = repo_root
        self.client = client
        self.log = log
        self._commit_resolver = commit_resolver
        self._version_writer = version_writer
        self.state = load_state(self.state_file)

    # -- reserve ------------------------------------------------------------

    @property
    def target(self):
        return self.config["target"]

    @property
    def version(self):
        value = self.state.get("reservedVersion")
        return value if isinstance(value, int) else None

    def ensure_reserved(self):
        """確保手上有一個對應當前 commit 的版本號。

        回傳 (ok, version_changed)。ok=False 時呼叫端**不可以**產生任何檔案 —— 沒有
        版本號就打包,等於送出一包帶著舊版本號的成品。
        """
        resolver = self._commit_resolver or source_commit
        commit = resolver(self.repo_root, self.folder_name)
        if not commit:
            self.log(
                f"❌[Release] {self.folder_name}: 無法判定 Setup/{self.folder_name}/Src "
                "的 commit(不是 git repo?路徑沒被追蹤?),這一輪不取號也不打包"
            )
            return False, False

        if (
            self.state.get("sourceCommit") == commit
            and isinstance(self.state.get("reservedVersion"), int)
        ):
            return True, False

        reservation_id = f"{self.target}-{commit[:12]}"
        try:
            payload = self.client.reserve(self.target, reservation_id)
        except ReleaseApiError as exc:
            self.log(f"❌[Release] {self.folder_name}: reserve 失敗,這一輪不打包: {exc}")
            return False, False

        version = payload.get("reservedVersion")
        if not isinstance(version, int):
            self.log(
                f"❌[Release] {self.folder_name}: reserve 回傳沒有整數 reservedVersion: {payload}"
            )
            return False, False

        previous = self.state.get("reservedVersion")
        self.state = {
            "target": self.target,
            "sourceCommit": commit,
            "reservedVersion": version,
            "reservationId": payload.get("reservationId") or reservation_id,
            "published": False,
            "publishAttempts": 0,
            "publishGaveUp": False,
        }
        save_state(self.state_file, self.state)
        self.log(
            f"✅[Release] {self.folder_name}: reserved version={version} "
            f"target={self.target} alreadyReserved={payload.get('alreadyReserved')}"
        )
        return True, previous != version

    # -- 寫號 ----------------------------------------------------------------

    def stamp(self):
        version = self.version
        if version is None:
            raise VersionWriteError("no reserved version to stamp")
        writer = self._version_writer or write_version
        writer(
            self.config["versionWriter"],
            self.gen_path,
            self.product_dir,
            version,
            self.secrets,
            None,
            self.log,
        )

    # -- publish -------------------------------------------------------------

    @property
    def needs_publish(self):
        if self.version is None:
            return False
        if self.state.get("published"):
            return False
        if self.state.get("publishGaveUp"):
            return False
        return True

    def try_publish(self):
        if not self.needs_publish:
            return False
        version = self.version
        try:
            payload = self.client.publish(
                self.target, version, self.state.get("reservationId")
            )
        except ReleaseApiError as exc:
            attempts = int(self.state.get("publishAttempts", 0)) + 1
            self.state["publishAttempts"] = attempts
            limit = self.config["maxPublishAttempts"]
            if exc.terminal or attempts >= limit:
                self.state["publishGaveUp"] = True
                self.state["publishGaveUpReason"] = str(exc)
                self.log(
                    f"❌[Release] {self.folder_name}: publish 放棄(第 {attempts}/{limit} 次,"
                    f"terminal={exc.terminal}): {exc}。包已經上傳,但 server 的 latest "
                    "沒有前進;要補發請人工處理。打包線程繼續運作。"
                )
            else:
                self.log(
                    f"⚠️[Release] {self.folder_name}: publish 失敗 "
                    f"(第 {attempts}/{limit} 次),下一輪再試: {exc}"
                )
            save_state(self.state_file, self.state)
            return False

        self.state["published"] = True
        self.state["publishedAt"] = payload.get("version", {}).get("latestBumpedAt")
        save_state(self.state_file, self.state)
        self.log(
            f"✅[Release] {self.folder_name}: published version={version} "
            f"published={payload.get('published')}"
        )
        return True


def should_publish(cycle, target_indexes, existing_indexes, sync_ok):
    """publish 的守門條件:**20 份全部產出來、而且全部上傳成功**才准。

    抽成純函式是因為它是整條線最容易寫錯、後果也最嚴重的一個判斷:太早 publish 就是
    「server 說 46、客戶端下載到的還是 45」——正是無限提示更新的成因。
    """
    if cycle is None or not cycle.needs_publish:
        return False
    if not set(target_indexes).issubset(set(existing_indexes)):
        return False
    return bool(sync_ok)


def new_reservation_id(target):
    """給沒有 commit 可綁的情境用(目前沒有呼叫端,保留給人工補發布)。"""
    return f"{target}-{uuid.uuid4()}"
