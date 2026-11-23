import hashlib, os, secrets, smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

auth_bp = Blueprint("auth", __name__)

def _hash_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _enviar_reset(dest, link):
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        current_app.logger.info("[MVP RESET] %s -> %s", dest, link)
        return False
    msg = EmailMessage()
    msg["Subject"] = "Redefinicao de senha - SimploGestao"
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "nao-responder@simplogestao.local"))
    msg["To"] = dest
    msg.set_content("Link valido por 30 min:\n" + link)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as s:
        if os.getenv("SMTP_USE_TLS", "1").strip().lower() in ("1", "true", "yes", "on"):
            s.starttls()
        u, p = os.getenv("SMTP_USER", "").strip(), os.getenv("SMTP_PASS", "")
        if u and p:
            s.login(u, p)
        s.send_message(msg)
    return True

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("usuario_id"):
        return redirect(url_for("index"))
    email_pre = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        email_pre = email
        conn = current_app.config["GET_DB_CONN"]()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, nome, email, senha_hash, empresa_id, ativo FROM usuarios WHERE lower(email) = %s LIMIT 1", (email,))
                u = cur.fetchone()
        finally:
            conn.close()
        if not u or not u[5] or not check_password_hash(u[3], senha):
            flash("E-mail ou senha invalidos.", "danger")
            return render_template("login.html", email_preenchido=email_pre)
        session.clear()
        session["usuario_id"], session["usuario_nome"], session["usuario_email"] = u[0], u[1], u[2]
        session["empresa_id"] = u[4] or session.get("empresa_id") or 1
        flash("Bem-vindo, {}!".format(u[1]), "success")
        nxt = request.args.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
    return render_template("login.html", email_preenchido=email_pre)

@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Voce saiu da sua conta.", "info")
    return redirect(url_for("auth.login"))

@auth_bp.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    link_mvp = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            flash("Informe um e-mail valido.", "danger")
            return render_template("esqueci_senha.html", link_recuperacao_mvp=None)
        conn = current_app.config["GET_DB_CONN"]()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, email, ativo FROM usuarios WHERE lower(email) = %s LIMIT 1", (email,))
                    u = cur.fetchone()
                    if u and u[2]:
                        raw = secrets.token_urlsafe(32)
                        exp = datetime.utcnow() + timedelta(minutes=30)
                        cur.execute("UPDATE usuarios SET reset_token_hash = %s, reset_token_expira_em = %s WHERE id = %s", (_hash_token(raw), exp, u[0]))
                        link = url_for("auth.redefinir_senha", token=raw, _external=True)
                        if not _enviar_reset(u[1], link):
                            link_mvp = link
        finally:
            conn.close()
        flash("Se este e-mail estiver cadastrado e ativo, o link de redefinicao foi gerado (valido por 30 min).", "info")
    return render_template("esqueci_senha.html", link_recuperacao_mvp=link_mvp)

@auth_bp.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    tok = (token or "").strip()
    th = _hash_token(tok) if tok else ""
    conn = current_app.config["GET_DB_CONN"]()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, reset_token_expira_em, ativo FROM usuarios WHERE reset_token_hash = %s LIMIT 1", (th,))
            u = cur.fetchone()
    finally:
        conn.close()
    valido = bool(u and u[3] and u[2] is not None and datetime.utcnow() <= u[2])
    if not valido:
        flash("Link invalido ou expirado. Solicite um novo.", "danger")
        return render_template("redefinir_senha.html", token=tok, token_valido=False, usuario_email=None)
    if request.method == "POST":
        s1 = request.form.get("nova_senha") or ""
        s2 = request.form.get("confirmar_senha") or ""
        if len(s1) < 6:
            flash("A nova senha deve ter pelo menos 6 caracteres.", "danger")
            return render_template("redefinir_senha.html", token=tok, token_valido=True, usuario_email=u[1])
        if s1 != s2:
            flash("A confirmacao da senha nao confere.", "danger")
            return render_template("redefinir_senha.html", token=tok, token_valido=True, usuario_email=u[1])
        nh = generate_password_hash(s1)
        conn = current_app.config["GET_DB_CONN"]()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE usuarios SET senha_hash = %s, reset_token_hash = NULL, reset_token_expira_em = NULL WHERE id = %s", (nh, u[0]))
        finally:
            conn.close()
        flash("Senha redefinida com sucesso! Faca login com sua nova senha.", "success")
        return redirect(url_for("auth.login"))
    return render_template("redefinir_senha.html", token=tok, token_valido=True, usuario_email=u[1])
