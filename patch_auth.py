import hashlib, os, secrets, smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash

APP_PATH = Path("app.py")
source = APP_PATH.read_text(encoding="utf-8")

if "# === AUTH_SIMPLOGESTAO ===" in source:
    print("Auth ja aplicada.")
    raise SystemExit(0)

if "from flask import " in source and "flash" not in source:
    source = source.replace("from flask import ", "from flask import flash, ", 1)

part1 = '''
# === AUTH_SIMPLOGESTAO ===
def _hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

def _enviar_email_redefinicao(destinatario: str, link_reset: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        app.logger.info("[MVP RESET SENHA] %s -> %s", destinatario, link_reset)
        return False
    msg = EmailMessage()
    msg["Subject"] = "Redefinicao de senha - SimploGestao"
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "nao-responder@simplogestao.local"))
    msg["To"] = destinatario
    msg.set_content(f"Acesse o link (valido por 30 min):\\n{link_reset}")
    with smtplib.SMTP(smtp_host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as srv:
        if os.getenv("SMTP_USE_TLS", "1").strip().lower() in ("1", "true", "yes", "on"):
            srv.starttls()
        u, p = os.getenv("SMTP_USER", "").strip(), os.getenv("SMTP_PASS", "")
        if u and p:
            srv.login(u, p)
        srv.send_message(msg)
    return True

@app.before_request
def exigir_autenticacao():
    ep = request.endpoint or ""
    if ep in {"login", "logout", "esqueci_senha", "redefinir_senha", "static"} or ep.startswith("static"):
        return None
    if not session.get("usuario_id"):
        if request.method == "GET":
            return redirect(url_for("login", next=request.path))
        flash("Faca login para continuar.", "warning")
        return redirect(url_for("login"))
    return None

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("usuario_id"):
        return redirect(url_for("index"))
    email_preenchido = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        email_preenchido = email
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, nome, email, senha_hash, empresa_id, ativo FROM usuarios WHERE lower(email) = %s LIMIT 1", (email,))
                user = cur.fetchone()
        finally:
            conn.close()
        if not user or not user[5] or not check_password_hash(user[3], senha):
            flash("E-mail ou senha invalidos.", "danger")
            return render_template("login.html", email_preenchido=email_preenchido)
        session.clear()
        session["usuario_id"], session["usuario_nome"], session["usuario_email"] = user[0], user[1], user[2]
        session["empresa_id"] = user[4] or session.get("empresa_id") or 1
        flash(f"Bem-vindo, {user[1]}!", "success")
        nxt = request.args.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
    return render_template("login.html", email_preenchido=email_preenchido)

@app.route("/logout")
def logout():
    session.clear()
    flash("Voce saiu da sua conta.", "info")
    return redirect(url_for("login"))
'''
Path("patch_part1.txt").write_text(part1, encoding="utf-8")
print("Parte 1 gerada.")
