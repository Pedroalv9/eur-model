@echo off
REM Atualiza o modelo EUR e publica no GitHub Pages.
REM Chamado pelo Agendador de Tarefas do Windows (9h e 17h45, horario de Brasilia).
cd /d "%~dp0"
set PY="%~dp0.venv\Scripts\python.exe"
echo ==================================================>> publicar.log
echo %date% %time% - iniciando>> publicar.log
%PY% publicar.py>> publicar.log 2>&1
echo %date% %time% - fim (exit %errorlevel%)>> publicar.log
