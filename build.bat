@echo off
rem Boostimise - makes the installer dist\Boostimise-Setup-<version>.exe that anyone can install.
rem Needs: the .venv that run.bat sets up, and Inno Setup 6 (https://jrsoftware.org/isdl.php).
rem Takes about 5-10 minutes. Change the version in core\theme.py (APP_VERSION) before a new release.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Run run.bat once first: it sets up the Python environment this build uses.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -c "import PyInstaller" 2>nul || (
    echo Installing PyInstaller ...
    ".venv\Scripts\python.exe" -m pip install pyinstaller || (pause & exit /b 1)
)

".venv\Scripts\python.exe" packaging\build.py
if errorlevel 1 (
    echo.
    echo The build failed, see the messages above.
    pause
    exit /b 1
)
echo.
explorer "%~dp0dist"
pause
