@echo off
chcp 65001 >nul
title Media Renamer - 影视文件智能整理工具
echo ============================================
echo   影视文件智能整理工具（芝杜刮削友好版）
echo ============================================
echo.

:: 配置区（改这里）
set TMDB_KEY=fe717bbe0351637ab4a8cd6f7c754686
set SCAN_DIR=PASTE_YOUR_DIR_HERE
set APPLY=N

echo [1] 预览模式（推荐先跑这个看效果）
echo [2] 执行模式（真正重命名）
echo.
set /p CHOICE=请选择 (1/2): 

if "%CHOICE%"=="2" (
    python "%~dp0media_renamer.py" --key %TMDB_KEY% --scan "%SCAN_DIR%" --apply
) else (
    python "%~dp0media_renamer.py" --key %TMDB_KEY% --scan "%SCAN_DIR%"
)

echo.
pause
