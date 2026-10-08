@echo off
chcp 932 >nul

python app_main_base_pc.py
if %ERRORLEVEL% neq 0 (
    py app_main_base_pc.py
    if %ERRORLEVEL% neq 0 (
        echo.
        echo [ERROR] Failed to start.
        echo Please check setup.bat or file name.
        echo.
        pause
    )
)
