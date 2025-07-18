@echo off
chcp 65001 >nul
echo ========================================
echo    AXG Core Files Sync Tool 自動部署
echo ========================================
echo.

REM 切換到專案根目錄
cd /d "%~dp0"

echo [1/6] 檢查 Python 環境...
where py >nul 2>&1
if %errorlevel% neq 0 (
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        echo ❌ 錯誤：未找到 Python，請先安裝 Python 3.8 或更高版本
        echo 下載地址：https://www.python.org/downloads/
        pause
        exit /b 1
    ) else (
        set PYTHON_CMD=python
        echo ✔ 找到 Python (python 命令)
    )
) else (
    set PYTHON_CMD=py
    echo ✔ 找到 Python (py 命令)
)

echo.
echo [2/6] 檢查 build 目錄...
if not exist "build" (
    echo ❌ 錯誤：build 目錄不存在
    pause
    exit /b 1
)
echo ✔ build 目錄存在

echo.
echo [3/6] 檢查並創建虛擬環境...
if exist "build\venv" (
    echo ⚠️  發現現有虛擬環境，自動重新創建...
    rmdir /s /q "build\venv"
    echo ✔ 已刪除舊虛擬環境
)

echo 創建虛擬環境...
cd build

REM 嘗試創建虛擬環境
echo 正在創建虛擬環境...
%PYTHON_CMD% -m venv venv
if %errorlevel% neq 0 (
    echo ❌ 錯誤：創建虛擬環境失敗
    echo 請檢查 Python 版本、權限和磁碟空間
    pause
    exit /b 1
)
echo ✔ 虛擬環境創建成功

:install_deps
echo.
echo [4/6] 安裝 Python 依賴套件...
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo ❌ 錯誤：激活虛擬環境失敗
    pause
    exit /b 1
)

if exist "requirements.txt" (
    echo 從 requirements.txt 安裝依賴...
    pip install -r requirements.txt
) else (
    echo 手動安裝 requests 套件...
    pip install requests
)

if %errorlevel% neq 0 (
    echo ❌ 錯誤：安裝依賴套件失敗
    pause
    exit /b 1
)
echo ✔ 依賴套件安裝完成

echo.
echo [5/6] 檢查必要檔案...
cd ..

REM 檢查 Start.py
if not exist "build\Start.py" (
    echo ❌ 錯誤：缺少 build\Start.py
    pause
    exit /b 1
)
echo ✔ Start.py 存在

REM 檢查 env.json
if not exist "build\env.json" (
    echo ⚠️  警告：缺少 build\env.json，創建預設配置...
    echo {> build\env.json
    echo     "host": "username@your-server.com",>> build\env.json
    echo     "winscp_path": "C:\\Program Files\\WinSCP\\WinSCP.com">> build\env.json
    echo }>> build\env.json
    echo ⚠️  請編輯 build\env.json 設定正確的伺服器資訊
) else (
    echo ✔ env.json 存在
)

REM 檢查 VMProtect_Con.exe
if not exist "build\VMProtect_Con.exe" (
    echo ⚠️  警告：缺少 build\VMProtect_Con.exe
    echo 請確保 VMProtect 工具已放置在 build 目錄
) else (
    echo ✔ VMProtect_Con.exe 存在
)

echo.
echo [6/6] 檢查 Setup 目錄結構...
if not exist "Setup" (
    echo ⚠️  警告：Setup 目錄不存在，創建目錄...
    mkdir Setup
    echo 請在 Setup 目錄下創建專案設定資料夾
) else (
    echo ✔ Setup 目錄存在
)

echo.
echo ========================================
echo           部署完成！
echo ========================================
echo.
echo 下一步操作：
echo 1. 編輯 build\env.json 設定伺服器資訊
echo 2. 在 Setup 目錄下創建專案設定資料夾
echo 3. 執行 Run.bat 啟動程式
echo.
echo 如需重新部署，請再次執行此腳本
echo.
pause 