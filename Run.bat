@echo off
chcp 65001 >nul
echo 啟動 AXG File Sync Tool...
echo.

REM 切換到專案根目錄
cd /d "%~dp0"

REM 檢查虛擬環境是否存在
if not exist "build\venv\Scripts\activate.bat" (
    echo 錯誤：虛擬環境不存在，請先創建虛擬環境
    echo 請執行：cd build && py -m venv venv
    pause
    exit /b 1
)

REM 啟動虛擬環境並執行 Start.py
echo 啟動虛擬環境...
call build\venv\Scripts\activate.bat

echo 執行 Start.py...
python build\Start.py

REM 保持視窗開啟
echo.
echo 程式執行完成，按任意鍵關閉...
pause >nul
