@echo off
chcp 65001 >nul
echo === Connexion Codex ===
where codex >nul 2>nul
if errorlevel 1 (
  echo La commande Codex n'est pas installee.
  echo Installe Codex CLI puis relance ce fichier.
  pause
  exit /b 1
)
codex login
pause
