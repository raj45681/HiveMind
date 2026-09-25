@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" goto local_python
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
python "%~dp0bootstrap.py" %*
exit /b %errorlevel%
:python_launcher
py -3 "%~dp0bootstrap.py" %*
exit /b %errorlevel%
:local_python
"%~dp0.venv\Scripts\python.exe" "%~dp0bootstrap.py" %*
exit /b %errorlevel%
