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
                'folder_path': str(folder),
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
    path = data['folder_path']
    remotePath = data['remotePath']
    needURL = data['getNeedURL']
    vmpFiles = data['vmpFiles']
    fileAmount = data['fileAmount']
    src_path = os.path.join(path, "Src")
    gen_path = os.path.join(path, "gen")
    output_path = os.path.join(path, "Output")

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
        with zipfile.ZipFile(target_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(path):
                for file in files:
                    abs_file = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_file, path)
                    zipf.write(abs_file, rel_path)

    def sync_remote(remotePath, output_path):
        env = get_env()
        host = env["host"]
        winscp_path = env["winscp_path"]

        # 每個指令一行，避免卡住
        script = f"""open {host}
        synchronize remote -delete \"{output_path}\" {remotePath}
        exit
        """
        # 寫入暫存檔
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt') as f:
            script_path = f.name
            f.write(script)
        
        # 執行 WinSCP（用 os.system）
        cmd = f'{winscp_path} /script="{script_path}"'
        print("執行 WinSCP 同步指令：", cmd)
        os.system(cmd)
        os.remove(script_path)
        print("✅[Sync]同步完成")

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

    def random_name(count):
        return "".join(random.choices(string.ascii_letters + string.digits, k=count))

    def gen_file():
        print("▶️[GenFlie]開始生成檔案")
        reset_gen_folder(gen_path)
        need_vmp_file_list = get_vmp_file_list(gen_path, vmpFiles)
        for file in need_vmp_file_list:
            vmp_file(file)
        zip_folder(gen_path, output_path+"\\APG_Run["+random_name(8)+"].zip")
        if(files_count(output_path) > fileAmount):
            remove_oldest_file(output_path)
        sync_remote(remotePath, output_path)
        print("✅[GenFlie]生成檔案完成")

    while(files_count(output_path)>0):
        remove_oldest_file(output_path)
    sync_remote(remotePath, output_path)

    while True:
        remote_need = int(requests.get(needURL).text);
        local_count = files_count(output_path)
        print("💤[Sync]本地檔案/目標數量：", local_count,"/",fileAmount)
        if(remote_need>0):
            print("💤[Sync]需要上傳的檔案數量：", remote_need)

        needGen = max(fileAmount-local_count,remote_need)
        if(needGen>0):
            print("▶️[Sync]開始生成檔案")
            for i in range(needGen):
                gen_file()
        else:
            print("💤[Sync]沒有需要上傳的檔案")

        print("💤[Sync]等待60秒")
        time.sleep(60)

def start_Threads(valid_folders):
    """啟動多線程處理"""
    if not valid_folders:
        print("⚠️[Start] 沒有找到有效的設定資料夾")
        return
    
    print(f"✅[Start] 找到 {len(valid_folders)} 個有效設定，開始處理...")
    threads = []
    for folder_info in valid_folders:
        thread = threading.Thread(target=process, args=(folder_info,))
        threads.append(thread)
        thread.start()

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

def git_update_monitor():
    """Git 更新監控線程，每10秒檢查一次"""
    print("🔄[GitMonitor] Git 更新監控線程已啟動")
    
    while True:
        try:
            if check_git_updates():
                print("🔄[GitMonitor] 檢測到更新，準備重新啟動...")
                # 等待一下讓其他線程有時間完成當前任務
                time.sleep(2)
                restart_application()
                break
            
            # 等待10秒後再次檢查
            time.sleep(10)
            
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