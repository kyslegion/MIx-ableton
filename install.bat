@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  Ableton AutoMix V11 - installation Windows
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo Python n'est pas trouve dans le PATH.
  echo Installe Python 3.11 ou plus recent puis relance ce fichier.
  pause
  exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Echec de l'installation des dependances.
  pause
  exit /b 1
)
echo.
echo Installation terminee.
echo Tu peux maintenant lancer RUN_AUTOMIX.bat
pause
