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
    "winscp_path": "C:\\Program Files\\WinSCP\\WinSCP.com"
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
    "remotePath": "/remote/path/on/server",
    "getNeedURL": "https://your-server.com/api/need-count",
    "fileAmount": 10
}
```

**參數說明：**
- `vmpFiles`：需要加密的檔案模式陣列（支援萬用字元）
- `remotePath`：遠端伺服器上的目標路徑
- `getNeedURL`：獲取遠端需求的 API URL
- `fileAmount`：本地保留的檔案數量上限

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