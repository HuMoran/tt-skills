@echo off
setlocal enabledelayedexpansion
rem ---------------------------------------------------------------------------
rem  Run a Creo J-Link app on THIS Windows machine (no SSH, no scheduled task).
rem
rem  Usage:  creo-run.bat MyAppClass     compile + launch Creo running that class
rem          creo-run.bat --check        verify environment only, launch nothing
rem
rem  Put your .java file next to this .bat. Everything else is generated.
rem ---------------------------------------------------------------------------

rem ===================== EDIT THESE TWO LINES ================================
set "CREO_ROOT=C:\Program Files\PTC\Creo 13.4.0.0"
set "JDK=C:\Program Files\Eclipse Adoptium\jdk-25.0.3+9"
rem  Optional: uncomment if Creo cannot find your license on its own
rem set "PTC_D_LICENSE_FILE=C:\Program Files\PTC\license\license.dat"
rem ===========================================================================

set "WORK=%~dp0"
if "%WORK:~-1%"=="\" set "WORK=%WORK:~0,-1%"
set "PARAMETRIC=%CREO_ROOT%\Parametric\bin\parametric.bat"
set "OTK=%CREO_ROOT%\Common Files\text\java\otk.jar"

set "CLS=%~1"
if "%CLS%"=="" (
  echo Usage: creo-run.bat ^<ClassName^>  ^|  creo-run.bat --check
  exit /b 2
)

rem ---- 1. environment -------------------------------------------------------
if not exist "%PARAMETRIC%" (
  echo [FAIL] Creo not found: "%PARAMETRIC%"
  echo        Fix CREO_ROOT at the top of this file. Look under C:\Program Files\PTC\
  exit /b 1
)
if not exist "%JDK%\bin\javac.exe" (
  echo [FAIL] JDK not found: "%JDK%\bin\javac.exe"
  echo        Fix JDK at the top of this file.
  exit /b 1
)
if not exist "%OTK%" (
  echo [FAIL] otk.jar not found: "%OTK%"
  echo        This Creo install has no J-Link. Re-run the Creo installer with the J-Link option.
  exit /b 1
)
echo [ok] Creo       "%CREO_ROOT%"
echo [ok] JDK        "%JDK%"

rem ---- 2. THE version check that this whole recipe exists for ---------------
rem  Creo's bundled JVM is often older than the JVM otk.jar was compiled with.
rem  Mismatch = the applet dies silently and your app simply never runs.
rem  Take the MAX over every class: Creo 13.4's otk.jar mixes Java 7 and Java 25
rem  classes, so sampling one entry can report 7 and wave a broken JDK through.
for /f "usebackq delims=" %%v in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; $z=[IO.Compression.ZipFile]::OpenRead('%OTK%'); $r=0; foreach($e in $z.Entries){ if($e.FullName.EndsWith('.class')){ $s=$e.Open(); $b=New-Object byte[] 8; [void]$s.Read($b,0,8); $v=$b[6]*256+$b[7]-44; if($v -gt $r){$r=$v}; $s.Close() } }; $z.Dispose(); $r"`) do set "OTKJAVA=%%v"
for /f "tokens=2 delims= " %%v in ('""%JDK%\bin\javac.exe" -version" 2^>^&1') do set "JDKVER=%%v"
for /f "tokens=1 delims=." %%a in ("!JDKVER!") do set "JDKMAJ=%%a"
echo [ok] otk.jar needs Java !OTKJAVA! ^| your JDK is Java !JDKMAJ! ^(!JDKVER!^)
if "!OTKJAVA!"=="0" (
  echo [warn] could not read otk.jar's Java version - skipping the compatibility check
) else if !JDKMAJ! LSS !OTKJAVA! (
  echo [FAIL] JDK is too old. otk.jar is Java !OTKJAVA!, your JDK is Java !JDKMAJ!.
  echo        Creo would start but the J-Link applet would die silently.
  echo        Install Temurin !OTKJAVA! ^(https://adoptium.net^) and point JDK at it.
  exit /b 1
)

if /i "%CLS%"=="--check" (
  echo [ok] environment looks good.
  exit /b 0
)
if not exist "%WORK%\%CLS%.java" (
  echo [FAIL] no source file: "%WORK%\%CLS%.java"
  exit /b 1
)

rem ---- 3. generate the three files Creo reads from the startup directory ----
if not exist "%WORK%\classes" mkdir "%WORK%\classes"
if not exist "%WORK%\text"    mkdir "%WORK%\text"

rem  config.pro: class path ONLY. Never add a protkdat line here - it would
rem  double-register with the protk.dat below and hang Creo behind a modal dialog.
> "%WORK%\config.pro" echo add_java_class_path %WORK%\classes

rem  protk.dat is auto-loaded from the startup directory. text_dir must contain
rem  at least one message file or registration fails.
> "%WORK%\protk.dat" (
  echo name creo_jlink_app
  echo startup java
  echo java_app_class %CLS%
  echo java_app_start start
  echo java_app_stop stop
  echo allow_stop true
  echo delay_start false
  echo text_dir %WORK%\text
  echo end
)
> "%WORK%\text\msg_app.txt" (
  echo msg_app
  echo Creo JLink App
  echo #
  echo #
)
echo [ok] wrote config.pro / protk.dat / text\msg_app.txt

rem ---- 4. compile ----------------------------------------------------------
rem  --release 21 keeps the class loadable by any JVM 21+, and the app calls the
rem  pfc API by reflection so otk.jar is not needed on the compile classpath.
"%JDK%\bin\javac.exe" --release 21 -d "%WORK%\classes" "%WORK%\%CLS%.java"
if errorlevel 1 (
  echo [FAIL] compile failed
  exit /b 1
)
echo [ok] compiled %CLS%.java

rem ---- 5. launch Creo with the RIGHT JVM in front --------------------------
set "JAVA_HOME=%JDK%"
set "PRO_JAVA_COMMAND=%JDK%\bin\java.exe"
set "PATH=%JDK%\bin\server;%JDK%\bin;%PATH%"
cd /d "%WORK%"
echo [ok] starting Creo with %CLS% - watch your app's own log file
call "%PARAMETRIC%"
