@echo off
python "C:\Users\palves\Desktop\projetos\model\eur_model.py"
if errorlevel 1 (
    echo.
    echo Erro ao rodar o modelo. Pressione qualquer tecla para fechar.
    pause >nul
)
