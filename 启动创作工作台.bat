@echo off
chcp 65001 >nul
title 创作工作台 - 关闭此窗口即停止服务
cd /d "%~dp0"

rem 注意：本文件必须保存为 UTF-8（无 BOM）+ CRLF 换行。
rem LF 换行会让 cmd 解析中断、双击闪退（2026-07 修复过，别再用 LF 保存）。

if not exist ".venv\Scripts\python.exe" ( echo [错误] 未找到 .venv 虚拟环境，请先完成项目安装。 & pause & exit /b 1 )

rem 启动前清掉仍占用 56090 端口的旧进程：否则旧进程还占着端口，新进程 bind 失败会直接
rem 闪退，表现为"双击重启后还是旧代码/改动没生效"（2026-07-14 定位到的根因）。
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":56090"') do taskkill /F /PID %%a >nul 2>&1

echo ============================================
echo   创作工作台启动中（首次约需几秒）...
echo   地址: http://127.0.0.1:56090/
echo   关闭本窗口即停止服务
echo   （提示：浏览器里按 Ctrl+F5 强制刷新才会加载最新页面）
echo ============================================

rem 2 秒后自动打开浏览器（等服务先起来）
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:56090/"

".venv\Scripts\python.exe" -m video_factory.studio --port 56090

echo.
echo 服务已停止（若上方有报错，多半是依赖缺失）。
pause
