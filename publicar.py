"""Gera o relatório do modelo EUR e publica no GitHub Pages.

Fluxo:
  1. Roda o modelo (busca Bloomberg -> regressão -> gráficos) e escreve docs/index.html.
     Se o Bloomberg falhar, eur_model.main levanta erro ANTES de escrever o arquivo,
     então o docs/index.html anterior é preservado e nada quebrado é publicado.
  2. Faz commit/push só se o HTML realmente mudou.

Uso: python publicar.py

Exit codes:
  0 -> publicado, ou sem mudança a publicar
  1 -> falha (Bloomberg indisponível, git, etc.)
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import eur_model

REPO = Path(__file__).resolve().parent
OUT = "docs/index.html"


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True
    )


def main():
    try:
        eur_model.main(output_path=OUT, open_browser=False)
    except Exception as exc:
        print(f"[ERRO] Falha ao gerar o relatório (nada publicado): {exc}")
        sys.exit(1)

    status = git("status", "--porcelain", OUT)
    if not status.stdout.strip():
        print("Sem mudanças no relatório; nada a publicar.")
        return

    git("add", OUT)
    msg = "Auto update " + datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = git("commit", "-m", msg)
    if commit.returncode != 0:
        print(f"[ERRO] git commit falhou:\n{commit.stdout}\n{commit.stderr}")
        sys.exit(1)

    push = git("push")
    if push.returncode != 0:
        print(f"[ERRO] git push falhou:\n{push.stdout}\n{push.stderr}")
        sys.exit(1)

    print(f"Publicado: {msg}")


if __name__ == "__main__":
    main()
