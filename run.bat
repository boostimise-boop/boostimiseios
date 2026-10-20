@echo off
rem Boostimise - all-in-one tools for WhatsApp sellers.
rem The first run sets up a private Python environment (.venv) with everything the tools need.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo First run: setting up, this takes a few minutes ...
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Could not create a Python environment. Install Python 3.10+ from python.org and try again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
)

rem Install or complete the packages when one is missing (also after requirements.txt changes).
".venv\Scripts\python.exe" -c "import importlib.util as u, sys; sys.exit(any(u.find_spec(m) is None for m in ('PySide6', 'playwright', 'PIL', 'numpy', 'anthropic', 'rapidocr_onnxruntime', 'openvino', 'pi_heif', 'phonenumbers')))"
if errorlevel 1 (
    echo Installing the required packages ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py"
