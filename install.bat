@echo off
chcp 65001 >nul
title King-AI-video 一键安装
cd /d "%~dp0"

rem 注意：本文件必须保存为 UTF-8（无 BOM）+ CRLF 换行。
rem LF 换行会让 cmd 解析中断、双击闪退（启动创作工作台.bat 2026-07 踩过同款坑）。

echo ============================================
echo   King-AI-video 一键安装
echo   步骤：环境检查 - Python 依赖 - 特效依赖
echo ============================================
echo.

rem ---------- 1. 系统级软件检查 ----------
set MISSING=0

where python >nul 2>&1
if errorlevel 1 ( echo [缺失] Python —— 去 https://www.python.org/downloads/ 安装，勾选 Add to PATH & set MISSING=1 ) else ( for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v )

where node >nul 2>&1
if errorlevel 1 ( echo [缺失] Node.js —— 去 https://nodejs.org/ 安装 LTS 版 & set MISSING=1 ) else ( for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo [OK] Node %%v )

where ffmpeg >nul 2>&1
if errorlevel 1 ( echo [缺失] ffmpeg —— 下载后把 bin 目录加进 PATH（见 安装说明.md） & set MISSING=1 ) else ( echo [OK] ffmpeg 已就位 )

if "%MISSING%"=="1" (
  echo.
  echo [中止] 请先装齐上面标记[缺失]的软件，然后重开本脚本（新装的 PATH 要重开窗口才生效）。
  pause
  exit /b 1
)
echo.

rem ---------- 2. Python 虚拟环境与依赖 ----------
if not exist ".venv\Scripts\python.exe" (
  echo [执行] 创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 ( echo [失败] 创建 .venv 失败，请检查 Python 安装。 & pause & exit /b 1 )
) else (
  echo [跳过] .venv 已存在
)

echo [执行] 安装 Python 依赖（清华镜像；公司网络拦镜像时删掉本行 -i 参数改官方源）...
".venv\Scripts\python.exe" -m pip install -e ".[dev,tts,asr]" -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 ( echo [失败] pip 安装失败，见上方报错（网络问题居多，可重跑本脚本续装）。 & pause & exit /b 1 )
echo.

rem ---------- 3. Remotion 特效依赖 ----------
echo [执行] 安装特效渲染依赖（remotion/ npm install，首次约 1~3 分钟）...
pushd remotion
call npm install
if errorlevel 1 ( popd & echo [失败] npm install 失败（可先执行 npm config set registry https://registry.npmmirror.com 换国内镜像后重跑）。 & pause & exit /b 1 )
popd
echo.

rem ---------- 4. 运行目录准备 ----------
if not exist "music" ( mkdir music & echo [执行] 已创建 music\ 文件夹（把 BGM 音乐文件丢进去） ) else ( echo [跳过] music\ 已存在 )
echo.

rem ---------- 5. 体检汇总 ----------
echo ============================================
echo   安装完成！依赖体检：
".venv\Scripts\python.exe" -c "import importlib.util as u; print('  faster-whisper:', 'OK' if u.find_spec('faster_whisper') else '缺失'); print('  edge-tts      :', 'OK' if u.find_spec('edge_tts') else '缺失'); print('  Pillow        :', 'OK' if u.find_spec('PIL') else '缺失')"
echo.
echo   接下来：
echo   1. 从老电脑拷 credentials.yaml 到本目录（或启动后在页面里重填密钥）
echo   2. 把 BGM 丢进 music\ 文件夹
echo   3. 双击 启动创作工作台.bat 开始使用
echo ============================================
pause
