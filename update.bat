@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Print Round Robin - Update Repository

REM ============================================================
REM Print Round Robin: обновление существующей копии или клонирование.
REM Git-клон обновляется через fetch + fast-forward merge.
REM ZIP-копия обновляется без удаления пользовательских данных.
REM Приложение не требует сторонних Python-пакетов.
REM ============================================================

cd /d "%~dp0"
set "PROJECT_ROOT=%CD%"
set "REPO_URL=https://github.com/ORAMAN/Print-Round-Robin.git"
set "BRANCH=main"
set "ZIP_URL=https://github.com/ORAMAN/Print-Round-Robin/archive/refs/heads/main.zip"
set "TMP_DIR=%TEMP%\PrintRoundRobin_update_%RANDOM%_%RANDOM%"

REM Очищаем переменные, которые могут перенаправить Git в другой каталог.
set "GIT_DIR="
set "GIT_WORK_TREE="
set "GIT_INDEX_FILE="
set "GIT_COMMON_DIR="
set "GIT_OBJECT_DIRECTORY="
set "GIT_ALTERNATE_OBJECT_DIRECTORIES="

echo.
echo ============================================================
echo Print Round Robin - обновление репозитория
echo ============================================================
echo [INFO] Папка: %PROJECT_ROOT%
echo.

REM Git-репозиторий: используем безопасное обновление через fast-forward.
if exist ".git" goto :git_update

REM ZIP-копия проекта: обновляем файлы, не удаляя локальные данные.
if exist "main.py" if exist "print_round_robin\__init__.py" goto :zip_update

REM Проекта в текущем каталоге нет: клонируем в подпапку.
goto :clone_repo


:git_update
echo [STEP] Найден Git-репозиторий.
call :ensure_git
if errorlevel 1 goto :error

git rev-parse --is-inside-work-tree >nul 2>&1
if not errorlevel 1 goto :git_ok

echo [WARN] Git не распознаёт репозиторий. Проверяю safe.directory...
git config --global --add safe.directory "%PROJECT_ROOT%" >nul 2>&1
git rev-parse --is-inside-work-tree >nul 2>&1
if not errorlevel 1 goto :git_ok

echo [ERROR] Каталог .git найден, но репозиторий Git не распознаётся.
echo Проверьте каталог .git или восстановите его из резервной копии.
goto :error


:git_ok
set "CURRENT_BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
if /I not "!CURRENT_BRANCH!"=="%BRANCH%" (
    echo [ERROR] Текущая ветка: "!CURRENT_BRANCH!", ожидается "%BRANCH%".
    echo Переключите репозиторий на main и повторите обновление.
    goto :error
)

set "ORIGIN="
for /f "delims=" %%R in ('git config --get remote.origin.url 2^>nul') do set "ORIGIN=%%R"
if /I "!ORIGIN!"=="https://github.com/ORAMAN/Print-Round-Robin.git" goto :origin_ok
if /I "!ORIGIN!"=="https://github.com/ORAMAN/Print-Round-Robin" goto :origin_ok
if /I "!ORIGIN!"=="git@github.com:ORAMAN/Print-Round-Robin.git" goto :origin_ok
if /I "!ORIGIN!"=="git@github.com:ORAMAN/Print-Round-Robin" goto :origin_ok

echo [ERROR] Удалённый origin не соответствует ORAMAN/Print-Round-Robin:
echo         !ORIGIN!
echo Обновление отменено во избежание изменения другого репозитория.
goto :error


:origin_ok
git diff --quiet
if errorlevel 1 goto :local_changes
git diff --cached --quiet
if errorlevel 1 goto :local_changes
goto :fetch_and_update


:local_changes
echo.
echo [WARN] В отслеживаемых файлах обнаружены локальные изменения:
echo ------------------------------------------------------------
git status --short --untracked-files=no
echo ------------------------------------------------------------
echo.
echo   [1] Выйти без изменений.
echo   [2] Продолжить и ПЕРЕЗАПИСАТЬ локальные изменения.
echo.
echo ВНИМАНИЕ: вариант 2 выполнит git reset --hard и удалит локальные
echo правки и staged-изменения в отслеживаемых файлах.
echo Перед продолжением сохраните нужные изменения отдельно.
echo Неотслеживаемые файлы автоматически не удаляются, если они
echo не мешают восстановлению файлов из репозитория.
echo.

:local_changes_prompt
set "LOCAL_CHANGE_CHOICE="
set /p "LOCAL_CHANGE_CHOICE=Ваш выбор [1/2]: "
if "!LOCAL_CHANGE_CHOICE!"=="1" goto :cancelled
if "!LOCAL_CHANGE_CHOICE!"=="2" goto :force_update
echo [WARN] Введите 1 или 2.
goto :local_changes_prompt


:fetch_and_update
echo [STEP] Получение последних изменений с GitHub...
git fetch --prune origin
if errorlevel 1 (
    echo [ERROR] Не удалось получить изменения с GitHub.
    goto :error
)

echo [STEP] Применение обновления (fast-forward)...
git merge --ff-only origin/%BRANCH%
if errorlevel 1 (
    echo [ERROR] Обновление невозможно выполнить автоматически.
    echo Локальная ветка могла разойтись с origin/%BRANCH%.
    echo Локальные изменения не сбрасывались.
    goto :error
)

echo.
echo [OK] Репозиторий успешно обновлён.
goto :post_update


:force_update
echo.
echo [STEP] Получение изменений с GitHub перед перезаписью...
git fetch --prune origin
if errorlevel 1 (
    echo [ERROR] Не удалось получить изменения. Локальные файлы не сбрасывались.
    goto :error
)
echo [WARN] Сбрасываю текущую ветку к origin/%BRANCH%...
git reset --hard origin/%BRANCH%
if errorlevel 1 (
    echo [ERROR] Не удалось выполнить принудительное обновление.
    goto :error
)
echo.
echo [OK] Отслеживаемые файлы заменены версией из GitHub.
goto :post_update


:zip_update
echo [INFO] Git-клон не найден. Обнаружена ZIP-копия проекта.
echo [INFO] Загружаю актуальный архив main.
echo [INFO] .git, виртуальные окружения и локальные данные сохраняются.

call :download_archive
if errorlevel 1 (
    echo [ERROR] Не удалось скачать или распаковать архив GitHub.
    call :cleanup_temp
    goto :error
)

robocopy "%TMP_DIR%\Print-Round-Robin-main" "%PROJECT_ROOT%" /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XD ".git" ".venv" "venv" "__pycache__" ".pytest_cache" "build" "dist" /XF "*.log" "*.tmp" "*.bak" "settings.json" "state.json"
set "ROBOCOPY_RC=!ERRORLEVEL!"
call :cleanup_temp
if !ROBOCOPY_RC! GEQ 8 (
    echo [ERROR] Robocopy завершился с кодом !ROBOCOPY_RC!.
    goto :error
)

echo.
echo [OK] Файлы проекта обновлены из GitHub.
echo [INFO] Устаревшие файлы, отсутствующие в репозитории, автоматически не удалялись.
goto :post_update


:clone_repo
set "TARGET_DIR=%~dp0Print-Round-Robin"
if exist "!TARGET_DIR!" (
    echo [ERROR] Папка назначения уже существует:
    echo         !TARGET_DIR!
    echo Переместите её или выберите другое место для клонирования.
    goto :error
)

echo [INFO] Проект в текущем каталоге не найден.
echo [INFO] Проверяю наличие Git...
call :locate_git
if not errorlevel 1 goto :clone_with_git

echo [INFO] Git не найден. Скачаю ZIP-архив проекта.
call :download_archive
if errorlevel 1 (
    echo [ERROR] Не удалось скачать или распаковать архив GitHub.
    call :cleanup_temp
    goto :error
)

mkdir "!TARGET_DIR!" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Не удалось создать папку назначения.
    call :cleanup_temp
    goto :error
)

robocopy "%TMP_DIR%\Print-Round-Robin-main" "!TARGET_DIR!" /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XD ".git" ".venv" "venv" "__pycache__" ".pytest_cache" "build" "dist" /XF "*.log" "*.tmp" "*.bak" "settings.json" "state.json"
set "ROBOCOPY_RC=!ERRORLEVEL!"
call :cleanup_temp
if !ROBOCOPY_RC! GEQ 8 (
    echo [ERROR] Не удалось скопировать файлы проекта. Код: !ROBOCOPY_RC!.
    goto :error
)

echo.
echo [OK] Проект скачан в папку:
echo      !TARGET_DIR!
echo [NEXT] Перейдите в неё и запустите update.bat для последующих обновлений.
echo [NEXT] Для запуска приложения выполните: python main.py
goto :success


:clone_with_git
echo [STEP] Клонирование %REPO_URL% ...
git clone --branch "%BRANCH%" "%REPO_URL%" "!TARGET_DIR!"
if errorlevel 1 (
    echo [ERROR] Не удалось клонировать репозиторий.
    goto :error
)

echo.
echo [OK] Репозиторий склонирован в папку:
echo      !TARGET_DIR!
echo [NEXT] Для запуска приложения выполните: python main.py
goto :success


REM ============================================================
REM Проверка/установка Git for Windows.
REM Сначала winget, затем официальный x64 установщик GitHub.
REM ============================================================
:ensure_git
call :locate_git
if not errorlevel 1 goto :git_ready

echo [WARN] Git не найден. Пытаюсь установить Git for Windows...
where winget >nul 2>&1
if errorlevel 1 goto :git_direct_install

echo [STEP] Установка Git через winget...
winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements
call :locate_git
if not errorlevel 1 goto :git_ready
echo [WARN] winget не установил Git. Пробую официальный установщик...

:git_direct_install
set "GIT_INSTALLER=%TEMP%\PrintRoundRobin_GitSetup_%RANDOM%.exe"
echo [STEP] Загрузка последнего установщика Git for Windows...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $h=@{'User-Agent'='PrintRoundRobin-Updater'}; $r=Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers $h; $a=$r.assets | Where-Object { $_.name -match '^Git-.*-64-bit\.exe$' } | Select-Object -First 1; if (-not $a) { throw 'Не найден x64 установщик Git for Windows' }; Invoke-WebRequest -UseBasicParsing -Uri $a.browser_download_url -OutFile $env:GIT_INSTALLER; if (-not (Test-Path -LiteralPath $env:GIT_INSTALLER)) { throw 'Установщик не загружен' }"
if errorlevel 1 (
    echo [ERROR] Не удалось скачать установщик Git for Windows.
    exit /b 1
)

echo [STEP] Установка Git for Windows. Может появиться запрос UAC...
start /wait "" "%GIT_INSTALLER%" /VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS
del /q "%GIT_INSTALLER%" >nul 2>&1
call :locate_git
if not errorlevel 1 goto :git_ready

echo [ERROR] Git не найден после установки. Проверьте права и завершение установщика.
exit /b 1


:git_ready
echo [INFO] Git доступен: "!GIT_EXE!"
"!GIT_EXE!" --version
exit /b 0


:locate_git
set "GIT_EXE="
set "GIT_CMD_DIR="
for /f "delims=" %%G in ('where git 2^>nul') do if not defined GIT_EXE set "GIT_EXE=%%G"
if not defined GIT_EXE if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT_EXE=%ProgramFiles%\Git\cmd\git.exe"
if not defined GIT_EXE if exist "%ProgramFiles(x86)%\Git\cmd\git.exe" set "GIT_EXE=%ProgramFiles(x86)%\Git\cmd\git.exe"
if not defined GIT_EXE if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT_EXE=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
if not defined GIT_EXE exit /b 1

"!GIT_EXE!" --version >nul 2>&1
if errorlevel 1 (
    set "GIT_EXE="
    exit /b 1
)
for %%D in ("!GIT_EXE!") do set "GIT_CMD_DIR=%%~dpD"
set "PATH=!GIT_CMD_DIR!;!PATH!"
exit /b 0


:download_archive
if not exist "%TMP_DIR%" mkdir "%TMP_DIR%" >nul 2>&1
set "PPRR_UPDATE_TMP=%TMP_DIR%"
set "PPRR_UPDATE_URL=%ZIP_URL%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $zip=Join-Path $env:PPRR_UPDATE_TMP 'repo.zip'; Invoke-WebRequest -UseBasicParsing -Uri $env:PPRR_UPDATE_URL -OutFile $zip; Expand-Archive -LiteralPath $zip -DestinationPath $env:PPRR_UPDATE_TMP -Force; if (-not (Test-Path -LiteralPath (Join-Path $env:PPRR_UPDATE_TMP 'Print-Round-Robin-main\main.py'))) { throw 'В архиве не найдена точка входа main.py' }; if (-not (Test-Path -LiteralPath (Join-Path $env:PPRR_UPDATE_TMP 'Print-Round-Robin-main\print_round_robin\__init__.py'))) { throw 'В архиве отсутствует пакет print_round_robin' }"
if errorlevel 1 exit /b 1
exit /b 0


:cleanup_temp
if exist "%TMP_DIR%" rmdir /s /q "%TMP_DIR%" >nul 2>&1
exit /b 0


:post_update
echo.
echo [INFO] В проекте нет сторонних Python-зависимостей.
echo [INFO] Для запуска требуется Python 3.10+ с компонентом Tkinter.
call :find_python
if errorlevel 1 (
    echo [WARN] Python не найден. Установите Python 3.10+ с компонентом Tcl/Tk.
    goto :success
)

echo [INFO] Проверка Python и Tkinter...
!PYTHON_RUN! -c "import sys, tkinter; print('Python:', sys.version.split()[0]); print('Tkinter: OK')" 
if errorlevel 1 (
    echo [WARN] Tkinter недоступен в выбранной установке Python.
    echo Установите Python заново, включив компонент "tcl/tk and IDLE".
    goto :success
)

echo.
set "RUN_APP="
set /p "RUN_APP=Запустить Print Round Robin сейчас? [y/N]: "
if /I not "!RUN_APP!"=="Y" goto :success
echo [STEP] Запуск приложения...
!PYTHON_RUN! main.py
if errorlevel 1 (
    echo [ERROR] Приложение завершилось с ошибкой.
    goto :error
)
goto :success


:find_python
set "PYTHON_RUN="
where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_RUN=py -3"
    exit /b 0
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_RUN=python"
    exit /b 0
)
exit /b 1


:cancelled
echo.
echo ============================================================
echo [INFO] Обновление отменено. Локальные файлы не изменялись.
echo ============================================================
pause
exit /b 0


:success
echo.
echo ============================================================
echo [OK] Операция завершена.
echo ============================================================
pause
exit /b 0


:error
echo.
echo ============================================================
echo [ERROR] Операция не завершена. См. сообщения выше.
echo ============================================================
pause
exit /b 1
