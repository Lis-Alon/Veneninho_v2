@echo off
rem Gera dist\VenenoBot\VenenoBot.exe (abre direto na janela, sem terminal)
cd /d "%~dp0"

rem O pacote "interception" (outro projeto) sobrescreve arquivos do interception-python e quebra os cliques
python -m pip uninstall -y interception >nul 2>&1
python -m pip install -r requirements.txt pyinstaller || goto :erro
python -m pip install --force-reinstall --no-deps interception-python==1.13.6 || goto :erro

python -m PyInstaller --noconfirm --clean --windowed --name VenenoBot --icon assets\icon.ico ^
  --add-data "src\ui;ui" ^
  --collect-all torch --collect-all torchvision --collect-all scipy ^
  src\main.py || goto :erro

rem As imagens ficam ao lado do .exe para dar pra adicionar pokemons novos sem recompilar
xcopy /E /I /Y "..\imags" "dist\VenenoBot\imags" >nul

echo.
echo Pronto: dist\VenenoBot\VenenoBot.exe
echo Para mandar pra alguem, compacte a pasta dist\VenenoBot inteira.
pause
exit /b 0

:erro
echo.
echo Falhou. Veja a mensagem acima.
pause
exit /b 1
