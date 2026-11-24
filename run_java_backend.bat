@echo off
setlocal
cd /d "%~dp0"

if exist "C:\Program Files\Java\jdk-27\bin\java.exe" (
    set "JAVA_HOME=C:\Program Files\Java\jdk-27"
    set "PATH=C:\Program Files\Java\jdk-27\bin;%PATH%"
)

echo [ERIS JAVA BACKEND] JAVA_HOME=%JAVA_HOME%
java -version

cd /d "%~dp0\backend-java"
echo [ERIS JAVA BACKEND] Starting Spring Boot 4 on http://127.0.0.1:8080 (Swagger: /swagger-ui.html)
echo [TIP] To connect Live2D Desktop with Java Backend:
echo       set ERIS_JAVA_BACKEND_URL=http://127.0.0.1:8080
echo       set ERIS_JAVA_LEARNING_MODE=mirror
echo       ..\run_eris.bat
mvn spring-boot:run
