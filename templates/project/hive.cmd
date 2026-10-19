@echo off
rem HiveMind managed project CLI
setlocal
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
python "%~dp0hive.py" %*
exit /b %errorlevel%
:python_launcher
py -3 "%~dp0hive.py" %*
exit /b %errorlevel%
