# AXG Core Files Sync Tool

一個自動化的檔案同步工具，用於加密、壓縮和同步檔案到遠端伺服器。

## 功能特色

- 🔐 **檔案加密**：使用 VMProtect 對指定檔案進行加密
- 📦 **自動壓縮**：將處理後的檔案自動打包成 ZIP 格式
- 🔄 **遠端同步**：使用 WinSCP 自動同步到遠端伺服器
- 📊 **數量控制**：自動維護本地和遠端檔案數量
- 🕐 **定時監控**：每60秒檢查一次遠端需求
- 🧵 **多線程處理**：支援多個設定檔同時處理

## 系統需求

- **作業系統**：Windows 10/11
- **Python**：3.8 或更高版本
- **WinSCP**：已安裝並配置
- **VMProtect**：VMProtect_Con.exe 工具

## 安裝步驟

### 方法一：自動部署（推薦）

使用提供的自動部署腳本：

```powershell
# 在專案根目錄執行
.\Setup.bat
```

自動部署腳本會：
- ✅ 檢查 Python 環境
- ✅ 自動創建虛擬環境在正確位置
- ✅ 安裝所需依賴套件
- ✅ 檢查必要檔案
- ✅ 創建預設配置檔案

### 方法二：手動安裝

#### 1. 克隆專案

```powershell
git clone <repository-url>
cd axg-corefiles-sync
```

#### 2. 創建虛擬環境 ⚠️ 重要

**必須在 `build/` 目錄下創建虛擬環境：**

```powershell
cd build
py -m venv venv
```

> **注意：** 虛擬環境必須創建在 `build/venv` 路徑，這是程式設計的固定路徑，不能更改！

#### 3. 激活虛擬環境

```powershell
.\venv\Scripts\Activate.ps1
```

#### 4. 安裝依賴套件

```powershell
pip install -r requirements.txt
```

或手動安裝：

```powershell
pip install requests
```

#### 5. 配置環境設定

編輯 `build/env.json` 文件：

```json
{
    "host": "username@your-server.com",
    "archiveToolPath": "C:\\Program Files\\WinRAR\\rar.exe",
    "remoteTargets": {
        "prod-a": {
            "host": "user1@server-a",
            "ssh_key_path": "C:\\Users\\YOUR_USER\\.ssh\\server-a-key"
        },
        "prod-b": {
            "host": "user2@server-b",
            "ssh_key_path": "C:\\Users\\YOUR_USER\\.ssh\\server-b-key"
        }
    }
}
```

**參數說明：**
- `host`：遠端伺服器的 SSH 連接字串
- `winscp_path`：WinSCP 命令列工具的完整路徑

## 專案結構

```
axg-corefiles-sync/
├── README.md                 # 專案說明文件
├── Setup.bat                 # 自動部署腳本
├── Run.bat                   # Windows 啟動腳本
├── build/                    # 主要程式目錄
│   ├── Start.py             # 主程式
│   ├── env.json             # 環境設定
│   ├── requirements.txt     # Python 依賴
│   ├── VMProtect_Con.exe    # VMProtect 加密工具
│   └── venv/                # Python 虛擬環境 ⚠️ 固定路徑
└── Setup/                   # 設定檔目錄
    └── [專案名稱]/          # 每個專案的設定資料夾
        ├── Setup.json       # 專案設定檔
        ├── Src/             # 原始檔案目錄
        ├── gen/             # 處理中檔案目錄（自動生成）
        └── Output/          # 輸出檔案目錄（自動生成）
```

> **重要：** `build/venv/` 是程式的固定虛擬環境路徑，不能更改位置！

## 設定檔格式

在 `Setup/` 目錄下為每個專案創建一個資料夾，並包含以下檔案：

### Setup.json 範例

```json
{
    "vmpFiles": ["*.exe", "*.dll"],
    "remoteTargets": {
        "prod-a": "/remote/path/on/server-a",
        "prod-b": "/remote/path/on/server-b"
    },
    "getNeedURL": "https://your-server.com/api/need-count",
    "fileAmount": 10
}
```

**參數說明：**
- `vmpFiles`：需要加密的檔案模式陣列（支援萬用字元）
- `remoteTargets`：同步目標列表，每筆可設定自己的 `host` 與 `remotePath`
- `remoteTargets[].host`：可選，未設定時沿用 `build/env.json` 的 `host`
- `remoteTargets[].ssh_key_path`：可選，指定該目標專用 SSH key
- `remoteTargets[].rsync_use_wsl`：可選，覆寫該目標是否走 WSL rsync
- `remoteTargets[].rsync_bin`：可選，覆寫該目標使用的 rsync 指令
- `remotePath`、`remotePaths`：舊版格式仍相容，會沿用 `build/env.json` 的 `host`
- `getNeedURL`：獲取遠端需求的 API URL
- `fileAmount`：本地保留的檔案數量上限

## 發布版本號(release)——預設關閉

打開之後,這條產品線每偵測到一次**自己的 `Src/` 有新 commit**,就會在打包之前先跟
server 取一個新版本號、把號碼寫進待打包的 `Res/Version.json`,等 `fileAmount` 份全部
上傳成功之後才 publish。新版先是 beta(客戶端靜默),擺滿 7 天沒被新版蓋過才自動升
格成正式版,正式版號往上跳之後客戶端才會出現更新按鈕。

**沒有寫 `release` 這個欄位、或 `release.enabled` 不是 `true` 的產品,行為與加這條線
之前逐字相同,一個封包都不會送出去。**

### `Setup/<product>/Setup.json`

```json
{
    "release": {
        "enabled": true,
        "target": "aig5",
        "apiBaseUrl": "https://server.axggame.com",
        "maxPublishAttempts": 5,
        "versionWriter": {
            "mode": "plaintext-json",
            "path": "Res/Version.json",
            "versionKey": "Version"
        }
    }
}
```

| 欄位 | 預設 | 語意 |
| --- | --- | --- |
| `release` | 不存在 | 整個物件不存在 = 這條產品線不走發布流程 |
| `release.enabled` | `false` | 總開關。`false` 時底下所有欄位都不會被讀 |
| `release.target` | 資料夾名的小寫 | **發布目標名**(`aig4` / `aig5` / `aug1` / `apg1`)。注意這**不是** `getNeedURL` 裡的下載代碼——aig4 與 aig5 的下載代碼都是 `aig`,但發布版本線必須分開 |
| `release.apiBaseUrl` | `https://server.axggame.com` | release API 的 base URL。環境變數 `AXG_RELEASE_API_BASE_URL` 會蓋過它(本機測試用 `http://localhost:3500`) |
| `release.maxPublishAttempts` | `5` | publish 失敗的重試上限。用完就放棄並記一筆 ERROR,**不會**讓打包線程卡死或無限重打 |
| `release.versionWriter.mode` | 必填 | `plaintext-json` 或 `respack`,見下 |
| `release.versionWriter.versionKey` | `Version` | 寫進 JSON 的哪個 key |
| `release.versionWriter.path` | 必填(`plaintext-json`) | 相對 `gen/` 的明文 JSON 路徑,例 `Res/Version.json`。檔案必須已經存在於 `Src/` |
| `release.versionWriter.templatePath` | `Version.template.json`(`respack`) | 相對 `Setup/<product>/` 的**明文模板**,發布時會複製一份、補上版本號,再用 `respack update` 換進加密樹 |
| `release.versionWriter.treeSubdir` | `""`(`respack`) | 加密樹在 `gen/` 底下的哪個子目錄,空字串 = `gen/` 根目錄 |

`versionWriter.mode`:

- **`plaintext-json`** —— `Src/` 裡有一份明文 JSON 時用。直接把版本號合併進去。
- **`respack`** —— `Src/` 裡是 respack 產出的加密資源樹時用。因為加密樹讀不出原內容,
  所以要另外準備一份明文模板;打包時把「模板 + 版本號」寫成一個只含 `Version.json`
  的暫時 `Res/` 目錄,再跑 `respack update`(respack 只碰來源目錄裡出現過的檔案,所以
  樹裡其它東西一個位元組都不會被重寫)。

### 密鑰與工具路徑(`build/env.json`,已在 `.gitignore` 裡)

**密鑰永遠不寫進 `Setup.json`** —— 那份檔案是 commit 進 repo 的。

```json
{
    "release": {
        "token": "PUT_THE_RELEASE_TOKEN_HERE",
        "apiBaseUrl": "",
        "respackPath": "C:\\path\\to\\respack.exe",
        "keyFile": "C:\\path\\to\\key.bin"
    }
}
```

環境變數優先於 `build/env.json`:`AXG_RELEASE_TOKEN`、`AXG_RELEASE_API_BASE_URL`、
`AXG_RESPACK_PATH`、`AXG_RES_KEY_FILE`。

打開了 `release.enabled` 卻找不到 token(或 `respack` 模式缺工具/金鑰)時,那個產品
會在掃描階段**驗證失敗並被跳過**,而不是安靜地照舊打包 —— 帶著舊版號的包送出去,是一
種不會有任何錯誤訊息的錯誤。

### 狀態檔

`build/release-state/<product>.json`(已在 `.gitignore` 裡)記錄「這條線目前綁在哪個
commit、取到哪個號、publish 了沒」。同一個 commit 重跑一百輪、打包機重啟、一次 fetch
拉進三個 commit,都只會停在同一個號上,不會連續 +2。

### 測試

```powershell
build\venv\Scripts\python.exe -m pip install -r build\requirements-dev.txt
build\venv\Scripts\python.exe -m pytest build\tests -q
```

測試全程使用替身,不會連到 `server.axggame.com`,也不會觸發任何一次真的發布。

### 檔案結構要求

每個專案資料夾必須包含：
- `Setup.json`：設定檔（必需）
- `Src/`：原始檔案目錄（必需，不能為空）

## 使用方法

### 方法一：使用啟動腳本（推薦）

```powershell
# 在專案根目錄執行
.\Run.bat
```

### 方法二：手動執行

```powershell
cd build
.\venv\Scripts\Activate.ps1
python Start.py
```

## 工作流程

1. **掃描設定**：程式啟動時掃描 `Setup/` 目錄下的所有專案
2. **驗證結構**：檢查每個專案的檔案結構是否符合要求
3. **多線程處理**：為每個有效專案啟動獨立的處理線程
4. **檔案處理**：
   - 複製 `Src/` 到 `gen/` 目錄
   - 對符合模式的檔案進行 VMProtect 加密
   - 將 `gen/` 目錄壓縮成 ZIP 檔案
   - 儲存到 `Output/` 目錄
5. **數量控制**：刪除最舊的檔案以維持數量限制
6. **遠端同步**：使用 WinSCP 同步到遠端伺服器
7. **定時監控**：每60秒檢查遠端需求並重複處理

## 錯誤處理

程式會自動處理以下情況：
- 設定檔格式錯誤
- 檔案結構不符合要求
- 網路連接問題
- 檔案操作失敗

## 日誌輸出

程式會顯示詳細的處理狀態：
- `✔[Setup]`：設定檔驗證成功
- `❌[Setup]`：設定檔驗證失敗
- `⏳[GenFlie]`：檔案處理中
- `✅[GenFlie]`：檔案處理完成
- `💤[Sync]`：同步狀態
- `✅[Sync]`：同步完成

## 故障排除

### 常見問題

1. **虛擬環境不存在**
   ```powershell
   cd build
   py -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install requests
   ```
   
   > **注意：** 確保在 `build/` 目錄下創建虛擬環境，路徑必須是 `build/venv`

2. **WinSCP 路徑錯誤**
   - 檢查 `env.json` 中的 `winscp_path` 是否正確
   - 確認 WinSCP 已正確安裝

3. **遠端連接失敗**
   - 檢查 `env.json` 中的 `host` 設定
   - 確認 SSH 金鑰或密碼設定正確

4. **VMProtect 工具缺失**
   - 確認 `VMProtect_Con.exe` 存在於 `build/` 目錄

### 重新生成虛擬環境 ⚠️ 重要

如果虛擬環境出現問題，可以重新生成：

```powershell
cd build
Remove-Item -Recurse -Force venv
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install requests
```

> **重要提醒：** 重新生成時也必須在 `build/` 目錄下執行，確保虛擬環境路徑為 `build/venv`

## 注意事項

- 確保遠端伺服器有足夠的儲存空間
- 定期檢查 `Output/` 目錄的檔案數量
- 監控網路連接狀態
- 備份重要的設定檔

## 授權

本專案僅供內部使用，請勿外流。

## 支援

如有問題，請聯繫開發團隊。 

## Remote Target V2 Example

`build/env.json`

```json
{
    "host": "username@legacy-rsync-host",
    "archiveToolPath": "C:\\Program Files\\WinRAR\\rar.exe",
    "remoteTargets": {
        "prod-rsync": {
            "type": "rsync",
            "host": "user@server-a",
            "ssh_key_path": "C:\\Users\\YOUR_USER\\.ssh\\server-a-key",
            "rsync_use_wsl": true,
            "rsync_bin": "rsync"
        },
        "backup-s3": {
            "type": "s3",
            "provider": "Other",
            "endpoint": "https://s3.example.com",
            "bucket": "axg-backup",
            "region": "auto",
            "access_key_id": "YOUR_ACCESS_KEY",
            "secret_access_key": "YOUR_SECRET_KEY",
            "rclone_bin": "rclone"
        }
    }
}
```

`Setup/<name>/Setup.json`

```json
{
    "remoteTargets": {
        "prod-rsync": "/srv/axg/apg1/output",
        "backup-s3": "apg1/output"
    }
}
```
