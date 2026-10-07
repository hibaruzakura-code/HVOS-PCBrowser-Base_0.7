@echo off
echo ==========================================
echo  HVOS PC Browser ver0.7 Setup
echo ==========================================

set PKGS=flask pyautogui keyboard pillow google-genai qrcode

pip --version >nul 2>&1
if %errorlevel% == 0 (
    pip install %PKGS%
    if errorlevel 1 goto INSTALL_ERROR
    goto SUCCESS
)

python -m pip --version >nul 2>&1
if %errorlevel% == 0 (
    python -m pip install %PKGS%
    if errorlevel 1 goto INSTALL_ERROR
    goto SUCCESS
)

py -m pip --version >nul 2>&1
if %errorlevel% == 0 (
    py -m pip install %PKGS%
    if errorlevel 1 goto INSTALL_ERROR
    goto SUCCESS
)

:ERROR
echo.
echo [ERROR] Python または pip が見つかりませんでした。
echo 1. PCを再起動してから再度実行してみてください。
echo 2. Pythonを再インストールし、「Add python.exe to PATH」にチェックを入れてください。
echo.
pause
exit /b 1

:INSTALL_ERROR
echo.
echo [ERROR] 部品のインストールに失敗しました。
echo 上に表示されたエラー文を、AI に見せて相談してみてください。
echo.
pause
exit /b 1

:SUCCESS
echo.
echo ------------------------------------------
echo セットアップが完了しました！ (全6部品)
echo ------------------------------------------
pause
