import os
import sys
import json
import threading
import requests
import time
from pathlib import Path
from urllib.parse import urlparse
import re
import fnmatch
import shutil
import zipfile
import subprocess
import tempfile
import git
import shlex

process_stop = False
threads_count = 0

def execute_with_timeout(cmd, timeout_seconds=300, max_retries=3, retry_delay=5):
    """執行命令並處理超時和重試"""
    def decode_output(raw):
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        for enc in ("utf-8", "utf-16le", "gbk"):
            try:
                return raw.decode(enc)
            except Exception:
                pass
        return raw.decode("utf-8", errors="replace")

    for attempt in range(max_retries):
        try:
            print(f"🔄[Execute] 嘗試執行命令 (第 {attempt + 1} 次)")
            result = subprocess.run(
                cmd,
                shell=isinstance(cmd, str),
                timeout=timeout_seconds,
                capture_output=True,
                text=False,
            )
            stdout = decode_output(result.stdout)
            stderr = decode_output(result.stderr)
            
            if result.returncode == 0:
                print("✅[Execute] 命令執行成功")
                return True
            else:
                print(f"⚠️[Execute] 命令執行失敗 (返回碼: {result.returncode})")
                if stderr:
                    print(f"錯誤訊息: {stderr}")
                elif stdout:
                    print(f"輸出訊息: {stdout}")

                if result.returncode in (-1, 4294967295) and (
                    "wsl.exe --install" in stdout
                    or "Windows Subsystem for Linux has no installed distributions" in stdout
                    or "适用于 Linux 的 Windows 子系统没有已安装的分发" in stdout
                ):
                    print("⚠️[Execute] WSL 尚未安裝發行版，請先安裝 Ubuntu 後再重試。")
                    return False
                    
        except subprocess.TimeoutExpired:
            print(f"⏰[Execute] 命令執行超時 ({timeout_seconds} 秒)")
        except Exception as e:
            print(f"❌[Execute] 命令執行異常: {e}")
        
        if attempt < max_retries - 1:
            print(f"⏳[Execute] 等待 {retry_delay} 秒後重試...")
            time.sleep(retry_delay)
    
    print("❌[Execute] 所有重試都失敗")
    return False

def restart_application():
    """重新啟動應用程式"""
    try:
        print("🔄[Restart] 正在重新啟動應用程式...")
        
        # 取得當前 Python 執行檔路徑
        python_executable = sys.executable
        script_path = os.path.abspath(__file__)
        
        # 在 Windows 上使用 subprocess.Popen 重新啟動
        if os.name == 'nt':  # Windows
            # 使用 subprocess 重新啟動
            subprocess.Popen([python_executable, script_path], 
                           cwd=os.path.dirname(script_path))
        else:  # Linux/Unix
            # 在 Linux 上使用 os.execv 重新啟動
            os.execv(python_executable, [python_executable, script_path])
        
        # 退出當前程序
        sys.exit(0)
        
    except Exception as e:
        print(f"❌[Restart] 重新啟動失敗: {e}")

def stop_thread(path):
    global threads_count
    print(f"🛑[Sync] {path} 線程已停止 (剩餘: {threads_count})")
    threads_count -= 1
    if(threads_count == 0):
        restart_application()

def is_valid_path(path_str):
    """檢查是否為合法路徑"""
    if not path_str or not isinstance(path_str, str):
        return False
    
    # 檢查是否包含非法字符
    invalid_chars = ['<', '>', ':', '"', '|', '?', '*']
    if any(char in path_str for char in invalid_chars):
        return False
    
    # 檢查是否為絕對路徑或相對路徑格式
    if path_str.startswith('/') or path_str.startswith('\\'):
        return True
    
    # 檢查相對路徑格式
    if re.match(r'^[a-zA-Z]:[\\/]', path_str):  # Windows 絕對路徑
        return True
    
    if re.match(r'^[./\\]', path_str):  # 相對路徑
        return True
    
    return True

def is_valid_url(url_str):
    """檢查是否為合法URL"""
    if not url_str or not isinstance(url_str, str):
        return False
    
    try:
        result = urlparse(url_str)
        return all([result.scheme, result.netloc])
    except:
        return False

def normalize_remote_paths(config):
    remote_paths = []

    if 'remotePaths' in config:
        if not isinstance(config['remotePaths'], list):
            return None, ["'remotePaths' must be a list"]
        if len(config['remotePaths']) == 0:
            return None, ["'remotePaths' cannot be empty"]

        for idx, remote_path in enumerate(config['remotePaths']):
            if not isinstance(remote_path, str):
                return None, [f"'remotePaths[{idx}]' must be a string"]
            if not remote_path.strip():
                return None, [f"'remotePaths[{idx}]' cannot be empty"]
            if not is_valid_path(remote_path):
                return None, [f"'remotePaths[{idx}]' is not a valid path: {remote_path}"]
            remote_paths.append(remote_path)
    elif 'remotePath' in config:
        remote_path = config['remotePath']
        if not isinstance(remote_path, str):
            return None, ["'remotePath' must be a string"]
        if not remote_path.strip():
            return None, ["'remotePath' cannot be empty"]
        if not is_valid_path(remote_path):
            return None, [f"'remotePath' is not a valid path: {remote_path}"]
        remote_paths.append(remote_path)
    else:
        return None, ["missing 'remotePath' or 'remotePaths'"]

    deduped_remote_paths = []
    seen = set()
    for remote_path in remote_paths:
        if remote_path in seen:
            continue
        seen.add(remote_path)
        deduped_remote_paths.append(remote_path)

    return deduped_remote_paths, []

def normalize_remote_targets(config):
    remote_targets = []

    if 'remoteTargets' in config:
        if not isinstance(config['remoteTargets'], list):
            return None, ["'remoteTargets' must be a list"]
        if len(config['remoteTargets']) == 0:
            return None, ["'remoteTargets' cannot be empty"]

        for idx, remote_target in enumerate(config['remoteTargets']):
            if not isinstance(remote_target, dict):
                return None, [f"'remoteTargets[{idx}]' must be an object"]

            host = remote_target.get('host')
            remote_path = remote_target.get('remotePath')

            if not isinstance(remote_path, str) or not remote_path.strip():
                return None, [f"'remoteTargets[{idx}].remotePath' must be a non-empty string"]
            if not is_valid_path(remote_path):
                return None, [f"'remoteTargets[{idx}].remotePath' is not a valid path: {remote_path}"]

            normalized_target = {
                'remotePath': remote_path,
            }

            if host is not None:
                if not isinstance(host, str):
                    return None, [f"'remoteTargets[{idx}].host' must be a string"]
                if not host.strip():
                    return None, [f"'remoteTargets[{idx}].host' cannot be empty"]
                normalized_target['host'] = host.strip()

            if 'ssh_key_path' in remote_target:
                ssh_key_path = remote_target['ssh_key_path']
                if not isinstance(ssh_key_path, str):
                    return None, [f"'remoteTargets[{idx}].ssh_key_path' must be a string"]
                if ssh_key_path.strip():
                    normalized_target['ssh_key_path'] = ssh_key_path.strip()

            if 'rsync_use_wsl' in remote_target:
                rsync_use_wsl = remote_target['rsync_use_wsl']
                if not isinstance(rsync_use_wsl, bool):
                    return None, [f"'remoteTargets[{idx}].rsync_use_wsl' must be a boolean"]
                normalized_target['rsync_use_wsl'] = rsync_use_wsl

            if 'rsync_bin' in remote_target:
                rsync_bin = remote_target['rsync_bin']
                if not isinstance(rsync_bin, str):
                    return None, [f"'remoteTargets[{idx}].rsync_bin' must be a string"]
                if not rsync_bin.strip():
                    return None, [f"'remoteTargets[{idx}].rsync_bin' cannot be empty"]
                normalized_target['rsync_bin'] = rsync_bin.strip()

            remote_targets.append(normalized_target)

        return remote_targets, []

    remote_paths, remote_path_errors = normalize_remote_paths(config)
    if remote_path_errors:
        return None, remote_path_errors

    normalized_targets = []
    for remote_path in remote_paths:
        normalized_targets.append({
            'remotePath': remote_path,
        })

    return normalized_targets, []

def resolve_remote_targets_from_setup(config, env_config):
    setup_remote_targets = config.get('remoteTargets')

    if isinstance(setup_remote_targets, dict):
        if len(setup_remote_targets) == 0:
            return None, ["'remoteTargets' cannot be empty"]

        env_remote_targets = env_config.get('remoteTargets', {})
        if env_remote_targets and not isinstance(env_remote_targets, dict):
            return None, ["'build/env.json remoteTargets' must be an object"]

        resolved_targets = []
        for target_name, remote_path in setup_remote_targets.items():
            if not isinstance(target_name, str) or not target_name.strip():
                return None, ["'remoteTargets' keys must be non-empty strings"]
            if not isinstance(remote_path, str) or not remote_path.strip():
                return None, [f"'remoteTargets.{target_name}' must be a non-empty string"]
            if not is_valid_path(remote_path):
                return None, [f"'remoteTargets.{target_name}' is not a valid path: {remote_path}"]

            target_name = target_name.strip()
            env_target = env_remote_targets.get(target_name)

            # Preserve legacy behavior for old env.json files that only define top-level host settings.
            if env_target is None and target_name == "default":
                env_target = {
                    "host": env_config.get("host", ""),
                    "ssh_key_path": env_config.get("ssh_key_path", ""),
                    "rsync_use_wsl": env_config.get("rsync_use_wsl", True),
                    "rsync_bin": env_config.get("rsync_bin", "rsync"),
                }

            if not isinstance(env_target, dict):
                return None, [f"remote target '{target_name}' is not defined in build/env.json"]

            target_type = env_target.get("type", "rsync")
            if not isinstance(target_type, str) or not target_type.strip():
                return None, [f"build/env.json remote target '{target_name}'.type must be a non-empty string"]
            target_type = target_type.strip().lower()

            resolved_target = {
                "name": target_name,
                "type": target_type,
                "remotePath": remote_path,
            }

            if target_type == "rsync":
                host = env_target.get("host", "")
                if not isinstance(host, str) or not host.strip():
                    return None, [f"build/env.json remote target '{target_name}' is missing 'host'"]
                resolved_target["host"] = host.strip()

                ssh_key_path = env_target.get("ssh_key_path", "")
                if ssh_key_path:
                    if not isinstance(ssh_key_path, str):
                        return None, [f"build/env.json remote target '{target_name}'.ssh_key_path must be a string"]
                    resolved_target["ssh_key_path"] = ssh_key_path.strip()

                if "rsync_use_wsl" in env_target:
                    if not isinstance(env_target["rsync_use_wsl"], bool):
                        return None, [f"build/env.json remote target '{target_name}'.rsync_use_wsl must be a boolean"]
                    resolved_target["rsync_use_wsl"] = env_target["rsync_use_wsl"]

                if "rsync_bin" in env_target:
                    rsync_bin = env_target["rsync_bin"]
                    if not isinstance(rsync_bin, str) or not rsync_bin.strip():
                        return None, [f"build/env.json remote target '{target_name}'.rsync_bin must be a non-empty string"]
                    resolved_target["rsync_bin"] = rsync_bin.strip()
            elif target_type == "s3":
                provider = env_target.get("provider", "Other")
                endpoint = env_target.get("endpoint", "")
                bucket = env_target.get("bucket", "")
                access_key_id = env_target.get("access_key_id", "")
                secret_access_key = env_target.get("secret_access_key", "")

                if not isinstance(provider, str) or not provider.strip():
                    return None, [f"build/env.json remote target '{target_name}'.provider must be a non-empty string"]
                if not isinstance(endpoint, str) or not endpoint.strip():
                    return None, [f"build/env.json remote target '{target_name}'.endpoint must be a non-empty string"]
                if not isinstance(bucket, str) or not bucket.strip():
                    return None, [f"build/env.json remote target '{target_name}'.bucket must be a non-empty string"]
                if not isinstance(access_key_id, str) or not access_key_id.strip():
                    return None, [f"build/env.json remote target '{target_name}'.access_key_id must be a non-empty string"]
                if not isinstance(secret_access_key, str) or not secret_access_key.strip():
                    return None, [f"build/env.json remote target '{target_name}'.secret_access_key must be a non-empty string"]

                resolved_target["provider"] = provider.strip()
                resolved_target["endpoint"] = endpoint.strip()
                resolved_target["bucket"] = bucket.strip()
                resolved_target["access_key_id"] = access_key_id.strip()
                resolved_target["secret_access_key"] = secret_access_key.strip()

                region = env_target.get("region", "")
                if region:
                    if not isinstance(region, str):
                        return None, [f"build/env.json remote target '{target_name}'.region must be a string"]
                    resolved_target["region"] = region.strip()

                if "rclone_bin" in env_target:
                    rclone_bin = env_target["rclone_bin"]
                    if not isinstance(rclone_bin, str) or not rclone_bin.strip():
                        return None, [f"build/env.json remote target '{target_name}'.rclone_bin must be a non-empty string"]
                    resolved_target["rclone_bin"] = rclone_bin.strip()

                if "no_check_bucket" in env_target:
                    if not isinstance(env_target["no_check_bucket"], bool):
                        return None, [f"build/env.json remote target '{target_name}'.no_check_bucket must be a boolean"]
                    resolved_target["no_check_bucket"] = env_target["no_check_bucket"]
            else:
                return None, [f"build/env.json remote target '{target_name}' has unsupported type: {target_type}"]

            resolved_targets.append(resolved_target)

        return resolved_targets, []

    return normalize_remote_targets(config)

def validate_setup_json(setup_path, folder_name):
    """驗證 Setup.json 檔案格式"""
    try:
        with open(setup_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        errors = []
        
        # 檢查 vmpFiles
        if 'vmpFiles' not in config:
            errors.append("缺少 'vmpFiles' 欄位")
        elif not isinstance(config['vmpFiles'], list):
            errors.append("'vmpFiles' 必須是陣列格式")
        elif len(config['vmpFiles']) == 0:
            errors.append("'vmpFiles' 陣列不能為空")
        
        # 檢查 remotePath
        if 'remotePath' not in config:
            errors.append("缺少 'remotePath' 欄位")
        elif not isinstance(config['remotePath'], str):
            errors.append("'remotePath' 必須是字串格式")
        elif not config['remotePath']:
            errors.append("'remotePath' 不能為空")
        elif not is_valid_path(config['remotePath']):
            errors.append(f"'remotePath' 不是合法路徑: {config['remotePath']}")
        
        # 檢查 getNeedURL
        if 'getNeedURL' not in config:
            errors.append("缺少 'getNeedURL' 欄位")
        elif not isinstance(config['getNeedURL'], str):
            errors.append("'getNeedURL' 必須是字串格式")
        elif not config['getNeedURL']:
            errors.append("'getNeedURL' 不能為空")
        elif not is_valid_url(config['getNeedURL']):
            errors.append(f"'getNeedURL' 不是合法URL: {config['getNeedURL']}")

        if 'fileAmount' not in config:
            errors.append("缺少 'fileAmount' 欄位")
        elif not isinstance(config['fileAmount'], int):
            errors.append("'fileAmount' 必須是整數格式")
        elif config['fileAmount'] <= 0:
            errors.append("'fileAmount' 必須大於 0")

        enabled = config.get('enabled', True)
        if not isinstance(enabled, bool):
            errors.append("'enabled' 必須是布林值")

        package_format = config.get('packageFormat', 'zip')
        if not isinstance(package_format, str):
            errors.append("'packageFormat' 必須是字串格式")
        elif package_format not in ('zip', 'sfx-exe'):
            errors.append("'packageFormat' 僅支援 'zip' 或 'sfx-exe'")

        if 'archiveToolPath' in config:
            if not isinstance(config['archiveToolPath'], str):
                errors.append("'archiveToolPath' 必須是字串格式")
            elif not config['archiveToolPath'].strip():
                errors.append("'archiveToolPath' 不能為空字串")
        
        if errors:
            return False, config, errors
        
        return True, config, []
        
    except json.JSONDecodeError as e:
        return False, None, [f"不是有效的 JSON 格式: {e}"]
    except Exception as e:
        return False, None, [f"讀取檔案時發生錯誤: {e}"]

def validate_setup_json_v2(setup_path, folder_name, env_config):
    """Validate Setup.json and normalize legacy/new remote path fields."""
    try:
        with open(setup_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        errors = []

        if 'vmpFiles' not in config:
            errors.append("missing 'vmpFiles'")
        elif not isinstance(config['vmpFiles'], list):
            errors.append("'vmpFiles' must be a list")
        elif len(config['vmpFiles']) == 0:
            errors.append("'vmpFiles' cannot be empty")

        remote_targets, remote_target_errors = resolve_remote_targets_from_setup(config, env_config)
        if remote_target_errors:
            errors.extend(remote_target_errors)
        else:
            config['remoteTargets'] = remote_targets

        if 'getNeedURL' not in config:
            errors.append("missing 'getNeedURL'")
        elif not isinstance(config['getNeedURL'], str):
            errors.append("'getNeedURL' must be a string")
        elif not config['getNeedURL']:
            errors.append("'getNeedURL' cannot be empty")
        elif not is_valid_url(config['getNeedURL']):
            errors.append(f"'getNeedURL' is not a valid URL: {config['getNeedURL']}")

        if 'fileAmount' not in config:
            errors.append("missing 'fileAmount'")
        elif not isinstance(config['fileAmount'], int):
            errors.append("'fileAmount' must be an integer")
        elif config['fileAmount'] <= 0:
            errors.append("'fileAmount' must be greater than 0")

        enabled = config.get('enabled', True)
        if not isinstance(enabled, bool):
            errors.append("'enabled' must be a boolean")

        package_format = config.get('packageFormat', 'zip')
        if not isinstance(package_format, str):
            errors.append("'packageFormat' must be a string")
        elif package_format not in ('zip', 'sfx-exe'):
            errors.append("'packageFormat' must be 'zip' or 'sfx-exe'")

        if 'archiveToolPath' in config:
            if not isinstance(config['archiveToolPath'], str):
                errors.append("'archiveToolPath' must be a string")
            elif not config['archiveToolPath'].strip():
                errors.append("'archiveToolPath' cannot be empty")

        if errors:
            return False, config, errors

        return True, config, []

    except json.JSONDecodeError as e:
        return False, None, [f"invalid JSON: {e}"]
    except Exception as e:
        return False, None, [f"unexpected error: {e}"]

def check_src_folder(src_path, folder_name):
    """檢查 Src 資料夾是否存在且不為空"""
    if not os.path.exists(src_path):
        return False, ["缺少 Src 資料夾"]
    
    if not os.path.isdir(src_path):
        return False, ["Src 不是資料夾"]
    
    # 檢查 Src 資料夾是否為空
    try:
        items = os.listdir(src_path)
        if not items:
            return False, ["Src 資料夾為空"]
        
        return True, []
        
    except Exception as e:
        return False, [f"檢查 Src 資料夾時發生錯誤: {e}"]

def scan_setup_folders():
    """掃描 Setup 資料夾中的所有子資料夾"""
    # 取得當前腳本所在目錄的父目錄（專案根目錄）
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    setup_base = project_root / "Setup"
    
    if not setup_base.exists():
        print("❌[Setup]Setup 資料夾不存在")
        return []
    
    if not setup_base.is_dir():
        print("❌[Setup]Setup 不是資料夾")
        return []
    
    valid_folders = []
    env_config = get_env()
    
    # 掃描所有子資料夾
    for folder in setup_base.iterdir():
        if not folder.is_dir():
            continue
        
        folder_name = folder.name
        all_errors = []
        
        # 檢查 Setup.json
        setup_json_path = folder / "Setup.json"
        if not setup_json_path.exists():
            print(f"❌[Setup]{folder_name}不符合結構: 缺少 Setup.json 檔案")
            continue
        
        # 驗證 Setup.json 格式
        is_valid_json, config, json_errors = validate_setup_json_v2(setup_json_path, folder_name, env_config)
        if not is_valid_json:
            all_errors.extend(json_errors)
        
        # 檢查 Src 資料夾
        src_path = folder / "Src"
        is_valid_src, src_errors = check_src_folder(src_path, folder_name)
        if not is_valid_src:
            all_errors.extend(src_errors)
        
        # 輸出結果
        if all_errors:
            print(f"❌[Setup]{folder_name}不符合結構: {'; '.join(all_errors)}")
        else:
            print(f"✔[Setup]{folder_name}符合結構")
            if not config.get('enabled', True):
                print(f"[Setup]{folder_name} skipped: enabled=false")
                continue
            valid_folders.append({
                'folder_name': folder_name,
                'folder_path': str(folder.absolute()),
                'remoteTargets': config['remoteTargets'],
                'getNeedURL': config['getNeedURL'],
                'vmpFiles': config['vmpFiles'],
                'fileAmount': config['fileAmount'],
                'packageFormat': config.get('packageFormat', 'zip'),
                'archiveToolPath': config.get('archiveToolPath', '')
            })
    
    return valid_folders



# path是資料夾路徑
# remotePath是遠端路徑
# needURL是獲取需要上傳的檔案的URL
def process(data):
    global threads_count
    threads_count += 1

    env = get_env()
    name = data['folder_name']
    path = data['folder_path']
    remote_targets = data['remoteTargets']
    needURL = data['getNeedURL']
    vmpFiles = data['vmpFiles']
    fileAmount = data['fileAmount']
    package_format = data.get('packageFormat', 'zip')
    archive_tool_path = env.get('archiveToolPath', '').strip() or data.get('archiveToolPath', '')
    src_path = os.path.join(path, "Src")
    gen_path = os.path.join(path, "gen")
    output_path = os.path.join(path, "Output")

    def windows_to_wsl_path(path_str):
        if re.match(r"^[a-zA-Z]:[\\/]", path_str):
            drive = path_str[0].lower()
            rest = path_str[2:].replace("\\", "/")
            if not rest.startswith("/"):
                rest = "/" + rest
            return f"/mnt/{drive}{rest}"
        return path_str.replace("\\", "/")

    def vmp_file(file):
        print("⏳[GenFlie]加密檔案"+file)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        vmprotect_exe = os.path.join(script_dir, "VMProtect_Con.exe")
        os.system(f"{vmprotect_exe} {file} {file}")

    def reset_gen_folder(path):
        # 如果 gen 存在就整個刪掉
        if os.path.exists(gen_path):
            shutil.rmtree(gen_path)
        # 複製整個 Src 到 gen
        shutil.copytree(src_path, gen_path)

    def get_vmp_file_list(path, vmpFiles):
        matched_files = []
        for file in os.listdir(path):
            for pattern in vmpFiles:
                if fnmatch.fnmatch(file, pattern):
                    matched_files.append(os.path.join(path, file))
                    break
        return matched_files
    
    def zip_folder(path, target_path):
        print("⏳[GenFlie]壓縮檔案"+path+"到"+target_path)
        target_dir = os.path.dirname(target_path)
        if target_dir and not os.path.exists(target_dir):
            os.makedirs(target_dir)
        
        # 獲取資料夾名稱作為 ZIP 內的主資料夾
        folder_name = os.path.basename(path)
        
        with zipfile.ZipFile(target_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(path):
                for file in files:
                    abs_file = os.path.join(root, file)
                    # 計算相對於 path 的路徑
                    rel_path = os.path.relpath(abs_file, path)
                    # 將文件放在主資料夾內
                    zip_path = os.path.join(folder_name, rel_path)
                    zipf.write(abs_file, zip_path)

    def resolve_rar_executable():
        candidates = []
        if archive_tool_path:
            candidates.append(archive_tool_path)
        candidates.extend([
            shutil.which("rar.exe"),
            shutil.which("rar"),
            r"C:\Program Files\WinRAR\rar.exe",
            r"C:\Program Files (x86)\WinRAR\rar.exe",
        ])

        for candidate in candidates:
            if not candidate:
                continue
            if os.path.isfile(candidate):
                return candidate

        raise FileNotFoundError(
            f"[GenFile] packageFormat={package_format} requires WinRAR rar.exe. "
            "Install WinRAR or set archiveToolPath in build/env.json."
        )

    def create_sfx_archive(path, target_path):
        rar_exe = resolve_rar_executable()
        print(f"[GenFile] create sfx archive: {target_path}")
        target_dir = os.path.dirname(target_path)
        if target_dir and not os.path.exists(target_dir):
            os.makedirs(target_dir)
        if os.path.exists(target_path):
            os.remove(target_path)

        archive_items = []
        for item_name in os.listdir(path):
            archive_items.append(item_name)
        if not archive_items:
            raise RuntimeError(f"create sfx archive failed: source folder is empty: {path}")
        sfx_script = (
            ";The comment below contains SFX script commands\n"
            "Path=.\\\n"
            "Silent=1\n"
            "Overwrite=1\n"
            "Setup=AIAIM.exe\n"
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as temp_file:
            temp_file.write(sfx_script)
            script_path = temp_file.name
        try:
            result = subprocess.run(
                [rar_exe, "a", "-r", "-sfx", f"-z{script_path}", target_path, *archive_items],
                cwd=path,
                capture_output=True,
                text=True,
            )
        finally:
            if os.path.exists(script_path):
                os.remove(script_path)
        if result.returncode != 0:
            raise RuntimeError(
                f"create sfx archive failed (code={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def get_output_extension():
        if package_format == "sfx-exe":
            return ".exe"
        return ".zip"

    def build_output_file(path, target_path):
        if package_format == "sfx-exe":
            create_sfx_archive(path, target_path)
            return
        zip_folder(path, target_path)

    def sync_rsync_target(remote_target, output_path):
        host = remote_target.get("host", env.get("host", "")).strip()
        remote_path = remote_target["remotePath"]
        ssh_key_path = remote_target.get("ssh_key_path", env.get("ssh_key_path", "")).strip()
        rsync_use_wsl = remote_target.get("rsync_use_wsl", env.get("rsync_use_wsl", True))
        rsync_bin = remote_target.get("rsync_bin", env.get("rsync_bin", "rsync")).strip() or "rsync"

        if not host:
            raise ValueError("remote target host is missing")

        local_path = output_path
        key_path = ssh_key_path
        if rsync_use_wsl:
            local_path = windows_to_wsl_path(local_path)
            if key_path:
                key_path = windows_to_wsl_path(key_path)
        local_path = local_path.rstrip("/\\") + "/"

        ssh_cmd_base = "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=15"
        remote_target_path = f"{host}:{remote_path}"

        if rsync_use_wsl:
            # WSL often rejects private keys on /mnt/* due to permissive file metadata.
            if key_path and key_path.startswith("/mnt/"):
                safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name.lower())
                temp_key = f"/tmp/axg_sync_{safe_name}_key"
                ssh_cmd = f"{ssh_cmd_base} -i {shlex.quote(temp_key)}"
                rsync_cmd = (
                    f"{rsync_bin} -az --delete --mkpath "
                    f"-e {shlex.quote(ssh_cmd)} "
                    f"{shlex.quote(local_path)} {shlex.quote(remote_target_path)}"
                )
                bash_cmd = (
                    f"umask 077; "
                    f"cp {shlex.quote(key_path)} {shlex.quote(temp_key)} && "
                    f"chmod 600 {shlex.quote(temp_key)} && "
                    f"{rsync_cmd}; "
                    f"rm -f {shlex.quote(temp_key)}"
                )
                cmd = ["wsl", "-e", "bash", "-lc", bash_cmd]
                printed_rsync_cmd = rsync_cmd.replace(temp_key, "<temp_key>")
            else:
                ssh_cmd = ssh_cmd_base
                if key_path:
                    ssh_cmd += f" -i {shlex.quote(key_path)}"
                rsync_cmd = (
                    f"{rsync_bin} -az --delete --mkpath "
                    f"-e {shlex.quote(ssh_cmd)} "
                    f"{shlex.quote(local_path)} {shlex.quote(remote_target_path)}"
                )
                cmd = ["wsl", "-e", "bash", "-lc", rsync_cmd]
                printed_rsync_cmd = rsync_cmd
        else:
            ssh_cmd = ssh_cmd_base
            if key_path:
                ssh_cmd += f" -i {shlex.quote(key_path)}"
            rsync_cmd = (
                f"{rsync_bin} -az --delete --mkpath "
                f"-e {shlex.quote(ssh_cmd)} "
                f"{shlex.quote(local_path)} {shlex.quote(remote_target_path)}"
            )
            cmd = rsync_cmd
            printed_rsync_cmd = rsync_cmd

        print("[Sync] Run rsync:", printed_rsync_cmd)
        return execute_with_timeout(cmd, timeout_seconds=300, max_retries=3)
        success = execute_with_timeout(cmd, timeout_seconds=300, max_retries=3)
        
        if success:
            print("✅[Sync]同步完成")
        else:
            print("❌[Sync]同步失敗")

    def sync_s3_target(remote_target, output_path):
        rclone_bin = remote_target.get("rclone_bin", env.get("rclone_bin", "rclone")).strip() or "rclone"
        local_path = output_path.rstrip("/\\")
        remote_name = f"axg_s3_{re.sub(r'[^a-zA-Z0-9_.-]', '_', remote_target.get('name', name.lower()))}"
        bucket = remote_target["bucket"]
        remote_path = remote_target["remotePath"].strip("/").replace("\\", "/")
        remote_spec = f"{remote_name}:{bucket}"
        if remote_path:
            remote_spec += f"/{remote_path}"

        config_lines = [
            f"[{remote_name}]",
            "type = s3",
            f"provider = {remote_target.get('provider', 'Other')}",
            "env_auth = false",
            f"access_key_id = {remote_target['access_key_id']}",
            f"secret_access_key = {remote_target['secret_access_key']}",
            f"endpoint = {remote_target['endpoint']}",
            f"region = {remote_target.get('region', 'auto') or 'auto'}",
            f"no_check_bucket = {str(remote_target.get('no_check_bucket', False)).lower()}",
            "",
        ]

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".conf", delete=False) as temp_config:
            temp_config.write("\n".join(config_lines))
            temp_config_path = temp_config.name

        try:
            cmd = [rclone_bin, "--config", temp_config_path, "sync", local_path, remote_spec, "--create-empty-src-dirs"]
            print("[Sync] Run rclone:", f"{rclone_bin} sync <local_output> <s3_remote>")
            return execute_with_timeout(cmd, timeout_seconds=300, max_retries=3)
        finally:
            if os.path.exists(temp_config_path):
                os.remove(temp_config_path)

    def sync_remote(remote_target, output_path):
        target_type = remote_target.get("type", "rsync")
        if target_type == "rsync":
            success = sync_rsync_target(remote_target, output_path)
        elif target_type == "s3":
            success = sync_s3_target(remote_target, output_path)
        else:
            raise ValueError(f"unsupported remote target type: {target_type}")

        if success:
            print("✅[Sync]同步完成")
        else:
            print("❌[Sync]同步失敗")

    def sync_all_remote_targets():
        for remote_target in remote_targets:
            target_name = remote_target.get("name", "<inline>")
            target_type = remote_target.get("type", "rsync")
            target_host = remote_target.get("host", env.get("host", ""))
            target_path = remote_target["remotePath"]
            if target_type == "s3":
                target_bucket = remote_target.get("bucket", "")
                print(f"[Sync] target={target_name} s3://{target_bucket}/{target_path}")
            else:
                print(f"[Sync] target={target_name} {target_host}:{target_path}")
            sync_remote(remote_target, output_path)

    def files_count(folder):
        if not os.path.exists(folder):
            print(f"警告: {folder} 不存在，無法計算檔案數量")
            return 0
        if not os.path.isdir(folder):
            print(f"警告: {folder} 不是資料夾，無法計算檔案數量")
            return 0
        files = [os.path.join(folder, f) for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        return len(files)

    def remove_oldest_file(folder):
        if not os.path.isdir(folder):
            print(f"警告: {folder} 不是資料夾，無法刪除最舊檔案")
            return
        files = [os.path.join(folder, f) for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        oldest = min(files, key=os.path.getctime)
        print("⏳[GenFlie]刪除最舊的檔案", oldest)
        os.remove(oldest)

    def resolve_code():
        try:
            parsed = urlparse(needURL)
            parts = [p for p in parsed.path.split("/") if p]
            # expected: /download/:code/output/index
            for i in range(len(parts) - 3):
                if parts[i] == "download" and parts[i + 2] == "output" and parts[i + 3] == "index":
                    return parts[i + 1].lower()
        except Exception:
            pass
        return name.lower()

    code = resolve_code()
    output_ext = get_output_extension()
    file_pattern = re.compile(rf"^{re.escape(code)}_(\d+){re.escape(output_ext)}$")

    def get_current_index():
        resp = requests.get(needURL, timeout=15)
        resp.raise_for_status()
        try:
            payload = resp.json()
            if isinstance(payload, dict) and "index" in payload:
                return int(payload["index"])
        except Exception:
            pass
        return int(resp.text.strip())

    def output_file_path(index_value):
        return os.path.join(output_path, f"{code}_{index_value}{output_ext}")

    def list_existing_index_files():
        mapping = {}
        if not os.path.isdir(output_path):
            return mapping
        for fname in os.listdir(output_path):
            fpath = os.path.join(output_path, fname)
            if not os.path.isfile(fpath):
                continue
            match = file_pattern.match(fname)
            if match:
                mapping[int(match.group(1))] = fpath
        return mapping

    def gen_file(target_index):
        print(f"[GenFile] generate {package_format} for index={target_index}")
        reset_gen_folder(gen_path)
        need_vmp_file_list = get_vmp_file_list(gen_path, vmpFiles)
        for file in need_vmp_file_list:
            vmp_file(file)
        build_output_file(gen_path, output_file_path(target_index))
        print(f"[GenFile] {package_format} generated")

    os.makedirs(output_path, exist_ok=True)
    has_synced_once = False
    while not process_stop:
        changed = False
        try:
            current_index = get_current_index()
            target_indexes = set(range(current_index, current_index + fileAmount))
            print(f"[Sync] index={current_index}, keep={current_index}~{current_index + fileAmount - 1}")

            existing_files = list_existing_index_files()
            existing_indexes = set(existing_files.keys())
            stale_changed = False

            for stale_index in sorted(existing_indexes - target_indexes):
                stale_path = existing_files[stale_index]
                print(f"[Sync] remove stale: {stale_path}")
                os.remove(stale_path)
                changed = True
                stale_changed = True

            generated_any = False
            for missing_index in sorted(target_indexes - existing_indexes):
                if process_stop:
                    break
                gen_file(missing_index)
                changed = True
                generated_any = True
                if not process_stop:
                    sync_all_remote_targets()
                    has_synced_once = True

            # If only stale files were removed, or first run has not synced yet, sync once.
            should_sync = ((stale_changed and not generated_any) or not has_synced_once) and not process_stop
            if should_sync:
                sync_all_remote_targets()
                has_synced_once = True
            elif not changed:
                print("[Sync] no file changes")
        except Exception as e:
            print(f"[Sync] reconcile failed: {e}")

        print("[Sync] wait 60s")
        count = 0
        while(count < 60):
            time.sleep(1)
            count += 1
            if(process_stop):
                break

    if process_stop:
        while(files_count(output_path)>0):
            remove_oldest_file(output_path)
    stop_thread(path)

def start_Threads(valid_folders):
    """啟動多線程處理"""
    if not valid_folders:
        print("⚠️[Start] 沒有找到有效的設定資料夾")
        return
    
    print(f"✅[Start] 找到 {len(valid_folders)} 個有效設定，開始處理...")
    for folder_info in valid_folders:
        threading.Thread(target=process, args=(folder_info,)).start()

def check_git_updates():
    """檢查 Git 遠端是否有更新"""
    try:
        # 取得專案根目錄（當前目錄的父目錄）
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        
        # 初始化 Git 倉庫
        repo = git.Repo(project_root)
        
        # 檢查是否有遠端倉庫
        if not repo.remotes:
            print("⚠️[Git] 沒有遠端倉庫設定")
            return False
        
        # 取得遠端更新
        origin = repo.remotes.origin
        origin.fetch()
        
        # 比較本地和遠端
        local_commit = repo.head.commit
        remote_commit = origin.refs.master.commit if hasattr(origin.refs, 'master') else origin.refs.main.commit
        
        if local_commit.hexsha != remote_commit.hexsha:
            print(f"🔄[Git] 發現遠端更新，正在拉取...")
            print(f"   本地: {local_commit.hexsha[:8]}")
            print(f"   遠端: {remote_commit.hexsha[:8]}")
            
            try:
                # 嘗試正常拉取
                origin.pull()
                print("✅[Git] 更新完成")
                return True
            except git.exc.GitCommandError as e:
                print(f"⚠️[Git] 正常拉取失敗，嘗試強制重置: {e}")
                try:
                    # 強制重置到遠端版本
                    repo.git.reset('--hard', 'origin/master' if hasattr(origin.refs, 'master') else 'origin/main')
                    print("✅[Git] 強制更新完成")
                    return True
                except Exception as reset_error:
                    print(f"❌[Git] 強制更新也失敗: {reset_error}")
                    return False
        else:
            print("💤[Git] 已是最新版本")
            return False
            
    except Exception as e:
        print(f"❌[Git] 檢查更新時發生錯誤: {e}")
        return False

def git_update_monitor():
    """Git 更新監控線程，每10秒檢查一次"""
    global process_stop
    print("🔄[GitMonitor] Git 更新監控線程已啟動")
    
    while not process_stop:
        try:
            if check_git_updates():
                process_stop = True
                break
            
            time.sleep(60)
        except Exception as e:
            print(f"❌[GitMonitor] 監控線程發生錯誤: {e}")
            # 發生錯誤時等待30秒再重試
            time.sleep(30)

def get_env():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, "env.json")
    with open(env_path, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    print("🚀[Start] AXG Core Files Sync Tool 啟動中...")
    
    start_Threads(scan_setup_folders())
    threading.Thread(target=git_update_monitor).start()

    return 0

if __name__ == "__main__":
    sys.exit(main()) 
