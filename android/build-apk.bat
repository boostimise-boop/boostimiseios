@echo off
rem Builds the Boostimise Android app: app\build\outputs\apk\release\app-release.apk
rem Needs JDK 17 and the Android SDK (see README.md). Java's socket files must not be under the
rem user folder on this PC, hence jdk.net.unixdomain.tmpdir.
if not exist C:\Temp mkdir C:\Temp
set JAVA_TOOL_OPTIONS=-Djdk.net.unixdomain.tmpdir=C:/Temp
if "%JAVA_HOME%"=="" set JAVA_HOME=%LOCALAPPDATA%\Programs\Microsoft\jdk-17.0.10.7-hotspot
cd /d "%~dp0"
call gradlew.bat assembleRelease
if errorlevel 1 (
  echo.
  echo Build failed.
  pause
  exit /b 1
)
copy /y app\build\outputs\apk\release\app-release.apk Boostimise.apk >nul
echo.
echo Done: %~dp0Boostimise.apk
pause
