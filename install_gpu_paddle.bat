@echo off
REM DocAgent GPU installer — safe/transactional. See install_gpu_paddle.py
cd /d "%~dp0"
python install_gpu_paddle.py
echo.
pause
