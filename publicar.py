"""Gera o relatório do modelo EUR e publica no GitHub Pages.

Fluxo:
  1. Roda o modelo (busca Bloomberg -> regressão -> gráficos) e escreve docs/index.html.
     Se o Bloomberg falhar, eur_model.main levanta erro ANTES de escrever o arquivo,
     então o docs/index.html anterior é preservado e nada quebrado é publicado.
  2. Faz commit/push só se o HTML realmente mudou.
  3. Em caso de falha, abre (ou comenta) uma issue de alerta no GitHub -> e-mail.
     Ao publicar com sucesso, fecha a issue de falha que estiver aberta.

Uso: python publicar.py

Exit codes:
  0 -> publicado, ou sem mudança a publicar
  1 -> falha (Bloomberg indisponível, git, etc.)
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import eur_model

REPO = Path(__file__).resolve().parent
OUT = "docs/index.html"

OWNER_REPO = "Pedroalv9/eur-model"
FAIL_TITLE = "[auto] Falha na publicação do modelo EUR"


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True
    )


# ---------------------------------------------------------------- GitHub alerts
def _github_token():
    """Pega o token da credencial já salva no Git Credential Manager."""
    try:
        r = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        for line in r.stdout.splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    return None


def _api(method, path, token, payload=None):
    url = "https://api.github.com" + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode() or "null"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def _open_failure_issue(token):
    """Número da issue de falha aberta, ou None."""
    status, issues = _api(
        "GET", f"/repos/{OWNER_REPO}/issues?state=open&per_page=50", token
    )
    if status != 200 or not issues:
        return None
    for it in issues:
        if "pull_request" in it:
            continue
        if it.get("title") == FAIL_TITLE:
            return it.get("number")
    return None


def notify_failure(token, reason):
    """Abre uma issue de falha, ou comenta na já aberta."""
    if not token:
        return
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    body = (
        f"Falha na rodada de **{ts}** (horário local).\n\n"
        f"```\n{reason}\n```\n\n"
        "O relatório anterior foi preservado (nada quebrado publicado). "
        "Verifique se o Bloomberg Terminal está aberto e logado, e rode "
        "`atualizar_e_publicar.bat` manualmente."
    )
    num = _open_failure_issue(token)
    if num is None:
        _api(
            "POST",
            f"/repos/{OWNER_REPO}/issues",
            token,
            {"title": FAIL_TITLE, "body": body},
        )
    else:
        _api(
            "POST",
            f"/repos/{OWNER_REPO}/issues/{num}/comments",
            token,
            {"body": body},
        )


def resolve_failure(token):
    """Fecha a issue de falha aberta (se houver) após uma publicação OK."""
    if not token:
        return
    num = _open_failure_issue(token)
    if num is None:
        return
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    _api(
        "POST",
        f"/repos/{OWNER_REPO}/issues/{num}/comments",
        token,
        {"body": f"Recuperado: publicação normalizada em **{ts}**. Fechando."},
    )
    _api("PATCH", f"/repos/{OWNER_REPO}/issues/{num}", token, {"state": "closed"})


# ---------------------------------------------------------------------- publish
def run_publish():
    """Gera e publica. Retorna 'published' ou 'unchanged'; levanta em falha."""
    eur_model.main(output_path=OUT, open_browser=False)

    status = git("status", "--porcelain", OUT)
    if not status.stdout.strip():
        print("Sem mudanças no relatório; nada a publicar.")
        return "unchanged"

    git("add", OUT)
    msg = "Auto update " + datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = git("commit", "-m", msg)
    if commit.returncode != 0:
        raise RuntimeError(f"git commit falhou:\n{commit.stdout}\n{commit.stderr}")

    push = git("push")
    if push.returncode != 0:
        raise RuntimeError(f"git push falhou:\n{push.stdout}\n{push.stderr}")

    print(f"Publicado: {msg}")
    return "published"


def main():
    token = _github_token()
    try:
        run_publish()
    except Exception as exc:
        print(f"[ERRO] Falha na publicação (nada quebrado publicado): {exc}")
        try:
            notify_failure(token, str(exc))
        except Exception as nexc:
            print(f"[aviso] não consegui abrir issue de alerta: {nexc}")
        sys.exit(1)

    # Publicou (ou sem mudança) sem erro -> normaliza qualquer alerta aberto.
    try:
        resolve_failure(token)
    except Exception as rexc:
        print(f"[aviso] não consegui fechar issue de alerta: {rexc}")


if __name__ == "__main__":
    main()
