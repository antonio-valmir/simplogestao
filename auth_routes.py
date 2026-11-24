import hashlib, os, secrets, smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for, render_template_string
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
            return render_template_string('{% extends "base.html" %}\n{% block title %}Entrar - SimploGestão{% endblock %}\n{% block content %}\n<div class="page-header">\n  <div>\n    <h1>Entrar</h1>\n    <p>Acesse sua conta para abrir suas empresas vinculadas no SimploGestão.</p>\n  </div>\n</div>\n\n<div class="card" style="max-width:560px;">\n  <form method="post" action="{{ url_for(\'auth.login\') }}" class="form-stack">\n    <div class="form-group">\n      <label for="email">E-mail</label>\n      <input id="email" type="email" name="email" value="{{ email_preenchido|e }}" placeholder="seu@email.com" required>\n    </div>\n\n    <div class="form-group">\n      <label for="senha">Senha</label>\n      <input id="senha" type="password" name="senha" placeholder="Sua senha" required>\n    </div>\n\n    <div style="display:flex;gap:10px;align-items:center;margin-top:8px;">\n      <button type="submit" class="btn btn-primary">Entrar</button>\n      <a href="{{ url_for(\'auth.cadastro\') }}" class="btn btn-secondary">Criar Conta</a>\n    </div>\n  </form>\n</div>\n{% endblock %}', pagina="login", email_preenchido=email_pre)
        empresas_da_conta = set()
        conn2 = current_app.config["GET_DB_CONN"]()
        try:
            with conn2.cursor() as cur2:
                cur2.execute(
                    "SELECT to_regclass('public.usuario_empresas');"
                )
                tem_tab = cur2.fetchone()
                if tem_tab and tem_tab[0]:
                    cur2.execute(
                        "SELECT empresa_id FROM usuario_empresas WHERE usuario_id = %s;",
                        (int(u[0]),),
                    )
                    for (eid,) in cur2.fetchall():
                        if eid:
                            empresas_da_conta.add(int(eid))
        finally:
            conn2.close()
        if u[4]:
            empresas_da_conta.add(int(u[4]))

        session.clear()
        session.permanent = True
        session["usuario_id"], session["usuario_nome"], session["usuario_email"] = u[0], u[1], u[2]
        if empresas_da_conta:
            ordenadas = sorted(empresas_da_conta)
            session["empresas_autenticadas"] = ordenadas
            session["empresas_sessao"] = ordenadas
            session["empresa_id"] = int(u[4]) if u[4] in empresas_da_conta else ordenadas[-1]
        else:
            session["empresas_autenticadas"] = []
            session["empresas_sessao"] = []
            session.pop("empresa_id", None)
        flash("Bem-vindo, {}!".format(u[1]), "success")
        nxt = request.args.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
    return render_template_string('{% extends "base.html" %}\n{% block title %}Entrar - SimploGestão{% endblock %}\n{% block content %}\n<div class="page-header">\n  <div>\n    <h1>Entrar</h1>\n    <p>Entre com seu e-mail e senha para acessar o SimploGestão.</p>\n  </div>\n</div>\n\n<div class="card" style="max-width:560px;">\n  <form method="post" action="{{ url_for(\'auth.login\') }}" class="form-stack">\n    <div class="form-group">\n      <label for="email">E-mail</label>\n      <input id="email" type="email" name="email" value="{{ email_preenchido|e }}" placeholder="seu@email.com" required>\n    </div>\n\n    <div class="form-group">\n      <label for="senha">Senha</label>\n      <input id="senha" type="password" name="senha" placeholder="Sua senha" required>\n    </div>\n\n    <div style="display:flex;gap:10px;align-items:center;margin-top:8px;">\n      <button type="submit" class="btn btn-primary">Entrar</button>\n      <a href="{{ url_for(\'auth.cadastro\') }}" class="btn btn-secondary">Criar Conta</a>\n    </div>\n  </form>\n</div>\n{% endblock %}', pagina="login", email_preenchido=email_pre)

@auth_bp.route("/logout")
def logout():
    session.clear()
    return render_template_string('{% extends "base.html" %}\n{% block title %}Sair - SimploGestão{% endblock %}\n{% block content %}\n<div class="page-header">\n  <div>\n    <h1>Sair</h1>\n    <p>Encerre sua sessão atual e finalize o acesso ao SimploGestão.</p>\n  </div>\n</div>\n\n<div class="card" style="max-width:560px;">\n  <div class="form-stack">\n    <div class="form-group">\n      <label>Status da sessão</label>\n      <p style="margin:0;">Sessão encerrada com sucesso. Nenhuma conta está ativa neste navegador.</p>\n    </div>\n\n    <div style="display:flex;gap:10px;align-items:center;margin-top:8px;">\n      <a href="{{ url_for(\'auth.login\') }}" class="btn btn-primary">Entrar</a>\n      <a href="{{ url_for(\'auth.cadastro\') }}" class="btn btn-secondary">Criar Conta</a>\n    </div>\n  </div>\n</div>\n{% endblock %}', pagina="logout")

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


@auth_bp.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    nome_pre = ""
    cpf_pre = ""
    email_pre = ""
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        cpf_raw = (request.form.get("cpf") or "").strip()
        cpf_digitos = "".join(ch for ch in cpf_raw if ch.isdigit())
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        confirmar = request.form.get("confirmar_senha") or ""
        nome_pre = nome
        cpf_pre = cpf_raw
        email_pre = email

        if len(nome) < 2:
            flash("Informe seu nome (pelo menos 2 caracteres).", "danger")
        elif len(cpf_digitos) != 11:
            flash("Informe um CPF valido com 11 digitos.", "danger")
        elif "@" not in email or "." not in email.split("@")[-1]:
            flash("Informe um e-mail valido.", "danger")
        elif len(senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
        elif senha != confirmar:
            flash("A confirmacao da senha nao confere.", "danger")
        else:
            conn = current_app.config["GET_DB_CONN"]()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM usuarios WHERE lower(email) = %s LIMIT 1;", (email,))
                        if cur.fetchone():
                            flash("Ja existe uma conta cadastrada com este e-mail.", "danger")
                        else:
                            senha_hash = generate_password_hash(senha)
                            cur.execute(
                                """
                                INSERT INTO usuarios (nome, cpf, email, senha_hash)
                                VALUES (%s, %s, %s, %s)
                                RETURNING id;
                                """,
                                (nome, cpf_digitos, email, senha_hash),
                            )
                            row = cur.fetchone()
                            novo_id = row[0] if row else None
                            if novo_id is not None:
                                session.clear()
                                session.permanent = True
                                session["usuario_id"] = novo_id
                                session["usuario_nome"] = nome
                                session["usuario_email"] = email
                                session["empresas_autenticadas"] = []
                                session["empresas_sessao"] = []
                            flash("Conta criada com sucesso!", "success")
                            return redirect(url_for("empresas"))
            except Exception as exc:
                flash(f"Erro ao criar conta: {exc}", "danger")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    html = """{% extends "base.html" %}
{% block title %}Criar Conta - SimploGestão{% endblock %}
{% block content %}
<div class="page-header">
  <div>
    <h1>Criar Conta</h1>
    <p>Cadastre um novo usuário com CPF para acessar o SimploGestão.</p>
  </div>
</div>

<div class="card" style="max-width:560px;">
  <form method="post" action="{{ url_for('auth.cadastro') }}" class="form-stack">
    <div class="form-group">
      <label for="nome">Nome completo</label>
      <input id="nome" type="text" name="nome" value="{{ nome_pre|e }}" placeholder="Seu nome completo" required>
    </div>

    <div class="form-group">
      <label for="cpf">CPF</label>
      <input id="cpf" type="text" name="cpf" value="{{ cpf_pre|e }}" placeholder="Ex.: 000.000.000-00" maxlength="14" required>
    </div>

    <div class="form-group">
      <label for="email">E-mail</label>
      <input id="email" type="email" name="email" value="{{ email_pre|e }}" placeholder="seu@email.com" required>
    </div>

    <div class="form-group">
      <label for="senha">Senha</label>
      <input id="senha" type="password" name="senha" placeholder="Mínimo de 6 caracteres" required>
    </div>

    <div class="form-group">
      <label for="confirmar_senha">Confirmar senha</label>
      <input id="confirmar_senha" type="password" name="confirmar_senha" placeholder="Repita a senha" required>
    </div>

    <div style="display:flex;gap:10px;align-items:center;margin-top:8px;">
      <button type="submit" class="btn btn-primary">Criar Conta</button>
      <a href="{{ url_for('clientes') }}" class="btn btn-secondary">Voltar</a>
    </div>
  </form>
</div>
{% endblock %}"""

    return render_template_string(
        html,
        pagina="cadastro",
        nome_pre=nome_pre,
        cpf_pre=cpf_pre,
        email_pre=email_pre,
    )

@auth_bp.route("/excluir-conta", methods=["GET", "POST"])
def excluir_conta():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        flash("Faca login para acessar a exclusao de conta.", "warning")
        return redirect(url_for("auth.login"))

    conn = current_app.config["GET_DB_CONN"]()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, nome, email, senha_hash FROM usuarios WHERE id = %s LIMIT 1;",
                (int(usuario_id),),
            )
            usuario = cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not usuario:
        session.clear()
        flash("Conta nao encontrada.", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        senha_atual = request.form.get("senha_atual") or ""
        confirmacao = (request.form.get("confirmacao") or "").strip().upper()

        if not check_password_hash(usuario[3] or "", senha_atual):
            flash("Senha atual incorreta.", "danger")
        elif confirmacao != "EXCLUIR":
            flash("Digite EXCLUIR para confirmar a remocao permanente da conta.", "danger")
        else:
            conn = current_app.config["GET_DB_CONN"]()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT to_regclass('public.usuario_empresas');")
                        reg_ue = cur.fetchone()
                        if reg_ue and reg_ue[0]:
                            cur.execute("DELETE FROM usuario_empresas WHERE usuario_id = %s;", (int(usuario_id),))
                        cur.execute("DELETE FROM usuarios WHERE id = %s;", (int(usuario_id),))
                session.clear()
                flash("Sua conta foi excluida permanentemente.", "success")
                return redirect(url_for("auth.login"))
            except Exception as exc:
                flash(f"Erro ao excluir conta: {exc}", "danger")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    html = """{% extends "base.html" %}
{% block title %}Excluir Conta - SimploGestão{% endblock %}
{% block content %}
<section class="module-hero">
  <div>
    <span class="module-hero-tag">SimploGestão • Conta</span>
    <h1>Excluir Conta</h1>
    <p>Remova permanentemente o seu acesso de usuário do SimploGestão.</p>
  </div>
</section>

<div class="card" style="max-width:560px;">
  <h2>Confirmar Exclusão Permanente</h2>
  <p style="margin-bottom:14px;color:var(--text-muted);">
    Usuário: <strong>{{ usuario_nome|e }}</strong> ({{ usuario_email|e }}). Esta ação remove sua conta e desvincula suas empresas deste acesso.
  </p>
  <form method="post" action="{{ url_for('auth.excluir_conta') }}" onsubmit="return confirm('Confirma a exclusao permanente da sua conta?');">
    <div class="form-group">
      <label for="senha_atual">Senha atual *</label>
      <input id="senha_atual" type="password" name="senha_atual" placeholder="Digite sua senha atual" required>
    </div>

    <div class="form-group">
      <label for="confirmacao">Digite EXCLUIR para confirmar *</label>
      <input id="confirmacao" type="text" name="confirmacao" placeholder="EXCLUIR" required>
    </div>

    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px;">
      <button type="submit" class="btn-primary">Excluir Minha Conta</button>
      <a href="{{ url_for('empresas') }}" class="btn-primary" style="text-decoration:none;opacity:0.85;">Cancelar</a>
    </div>
  </form>
</div>
{% endblock %}"""

    return render_template_string(
        html,
        active_page="excluir_conta",
        usuario_nome=usuario[1],
        usuario_email=usuario[2],
    )
