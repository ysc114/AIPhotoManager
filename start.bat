@echo off
rem ============================================================
rem  AIPhotoManager 启动脚本
rem  使用 Python 3.10（已安装 PySide6）显式启动，避免
rem  默认 python (3.13/3.14) 缺少 PySide6 导致打不开。
rem ============================================================
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.10 main.py
    if %errorlevel%==0 goto :end
)

"C:\Program Files\Python310\python.exe" main.py
if errorlevel 1 goto :fail
goto :end

:fail
echo.
echo [启动失败] 请确认已安装 Python 3.10 且包含 PySide6:
echo   py -3.10 -m pip install PySide6
pause
exit /b 1

:end
