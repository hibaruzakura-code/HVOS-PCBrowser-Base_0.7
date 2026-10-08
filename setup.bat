@echo off
echo ==========================================
echo  HVOS PC Browser ver0.7 Setup
echo ==========================================

set PKGS=flask pyautogui keyboard pillow google-genai qrcode
set PIPCMD=

pip --version >nul 2>&1 && set "PIPCMD=pip"
if not defined PIPCMD (
    python -m pip --version >nul 2>&1 && set "PIPCMD=python -m pip"
)
if not defined PIPCMD (
    py -m pip --version >nul 2>&1 && set "PIPCMD=py -m pip"
)
if not defined PIPCMD (
    echo.
    echo [ERROR] Python または pip が見つかりませんでした。
    echo 1. PCを再起動してから再度実行してみてください。
    echo 2. Pythonを再インストールし、「Add python.exe to PATH」にチェックを入れてください。
    echo.
    pause
    exit /b 1
)

%PIPCMD% install %PKGS%
if errorlevel 1 (
    echo.
    echo [ERROR] 部品のインストールに失敗しました。
    echo 上に表示されたエラー文を、AI に見せて相談してみてください。
    echo.
    pause
    exit /b 1
)

echo.
echo ------------------------------------------
echo セットアップが完了しました！ (全6部品)
echo ------------------------------------------
pause
