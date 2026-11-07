@echo off
setlocal
echo === Syncing Live2D assets from infj source to Eris project ===
rem Optional first argument overrides the source model folder.
set "SRC=%USERPROFILE%\Documents\infj"
if defined ERIS_LIVE2D_SOURCE set "SRC=%ERIS_LIVE2D_SOURCE%"
if not "%~1"=="" set "SRC=%~f1"
set "DST=%~dp0src\assets\live2d\infj"
if not exist "%SRC%\infj.moc3" (
    echo [ERROR] Missing source model: "%SRC%\infj.moc3"
    exit /b 1
)
if not exist "%DST%" mkdir "%DST%"

echo [1/4] Copying moc3 + cdi3 + model3 + physics3...
copy /Y "%SRC%\infj.moc3" "%DST%\infj.moc3"
copy /Y "%SRC%\infj.cdi3.json" "%DST%\infj.cdi3.json"
copy /Y "%SRC%\infj.model3.json" "%DST%\infj.model3.json"
copy /Y "%SRC%\infj.physics3.json" "%DST%\infj.physics3.json"

echo [2/4] Copying expressions...
robocopy "%SRC%\expressions" "%DST%\expressions" *.json /E /NP

echo [3/4] Copying motions...
robocopy "%SRC%\motions" "%DST%\motions" *.json /E /NP

echo [4/4] Done!
echo.
echo === Files in destination: ===
dir "%DST%" /B
pause
