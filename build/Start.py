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
import random
import string
import subprocess
import tempfile
import git
import signal
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
        
        if errors:
            return False, config, errors
        
        return True, config, []
        
    except json.JSONDecodeError as e:
        return False, None, [f"不是有效的 JSON 格式: {e}"]
    except Exception as e:
        return False, None, [f"讀取檔案時發生錯誤: {e}"]

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
        is_valid_json, config, json_errors = validate_setup_json(setup_json_path, folder_name)
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
            valid_folders.append({
                'folder_name': folder_name,
                'folder_path': str(folder.absolute()),
                'remotePath': config['remotePath'],
                'getNeedURL': config['getNeedURL'],
                'vmpFiles': config['vmpFiles'],
                'fileAmount': config['fileAmount']
            })
    
    return valid_folders



# path是資料夾路徑
# remotePath是遠端路徑
# needURL是獲取需要上傳的檔案的URL
def process(data):
    global threads_count
    threads_count += 1

    name = data['folder_name']
    path = data['folder_path']
    remotePath = data['remotePath']
    needURL = data['getNeedURL']
    vmpFiles = data['vmpFiles']
    fileAmount = data['fileAmount']
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

    

    def sync_remote(remotePath, output_path):
        env = get_env()
        host = env["host"]
        ssh_key_path = env.get("ssh_key_path", "").strip()
        rsync_use_wsl = env.get("rsync_use_wsl", True)
        rsync_bin = env.get("rsync_bin", "rsync").strip() or "rsync"

        local_path = output_path
        key_path = ssh_key_path
        if rsync_use_wsl:
            local_path = windows_to_wsl_path(local_path)
            if key_path:
                key_path = windows_to_wsl_path(key_path)
        local_path = local_path.rstrip("/\\") + "/"

        ssh_cmd = "ssh -o StrictHostKeyChecking=accept-new"
        if key_path:
            ssh_cmd += f" -i {shlex.quote(key_path)}"

        remote_target = f"{host}:{remotePath}"
        rsync_cmd = (
            f"{rsync_bin} -az --delete --mkpath "
            f"-e {shlex.quote(ssh_cmd)} "
            f"{shlex.quote(local_path)} {shlex.quote(remote_target)}"
        )

        if rsync_use_wsl:
            cmd = ["wsl", "-e", "bash", "-lc", rsync_cmd]
        else:
            cmd = rsync_cmd

        print("[Sync] Run rsync:", rsync_cmd)
        success = execute_with_timeout(cmd, timeout_seconds=300, max_retries=3)
        
        if success:
            print("✅[Sync]同步完成")
        else:
            print("❌[Sync]同步失敗")

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
    file_pattern = re.compile(rf"^{re.escape(code)}_(\d+)\.zip$")

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
        return os.path.join(output_path, f"{code}_{index_value}.zip")

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
        print(f"[GenFile] generate zip for index={target_index}")
        reset_gen_folder(gen_path)
        need_vmp_file_list = get_vmp_file_list(gen_path, vmpFiles)
        for file in need_vmp_file_list:
            vmp_file(file)
        zip_folder(gen_path, output_file_path(target_index))
        print("[GenFile] zip generated")

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

            for stale_index in sorted(existing_indexes - target_indexes):
                stale_path = existing_files[stale_index]
                print(f"[Sync] remove stale: {stale_path}")
                os.remove(stale_path)
                changed = True

            for missing_index in sorted(target_indexes - existing_indexes):
                if process_stop:
                    break
                gen_file(missing_index)
                changed = True

            should_sync = (changed or not has_synced_once) and not process_stop
            if should_sync:
                sync_remote(remotePath, output_path)
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

    while(files_count(output_path)>0):
        remove_oldest_file(output_path)
    sync_remote(remotePath, output_path)
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
