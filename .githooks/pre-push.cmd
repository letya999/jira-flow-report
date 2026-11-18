@echo off
rem >>> AI REPO SAFETY PRE-PUSH >>>
where ai-repo-safety >nul 2>nul
if %ERRORLEVEL%==0 (
  ai-repo-safety prepush --target .
  exit /b %ERRORLEVEL%
)
where uv >nul 2>nul
if %ERRORLEVEL%==0 if exist pyproject.toml (
  uv run ai-repo-safety prepush --target .
  exit /b %ERRORLEVEL%
)
where uvx >nul 2>nul
if %ERRORLEVEL%==0 (
  uvx ai-repo-safety prepush --target .
  exit /b %ERRORLEVEL%
)
if exist .repo-safety\scripts\prepush.py (
  python .repo-safety\scripts\prepush.py
  exit /b %ERRORLEVEL%
)
if exist scripts\security\prepush.py (
  python scripts\security\prepush.py
  exit /b %ERRORLEVEL%
)
echo [repo-safety] ai-repo-safety not found; refusing push
exit /b 1
rem <<< AI REPO SAFETY PRE-PUSH <<<
