from werkzeug.security import generate_password_hash, check_password_hash
import re
import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from flask import flash, session, Flask, current_app, redirect, render_template, render_template_string, request, url_for
import psycopg

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=180)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.before_request
def _tornar_sessao_permanente():
    session.permanent = True


def get_db_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "simplogestao_db"),
        user=os.getenv("DB_USER", "simplogestao_user"),
        password=os.getenv("DB_PASSWORD", ""),
    )



app.config["GET_DB_CONN"] = get_db_connection
def _somente_digitos(valor):
    return re.sub(r"[^0-9]", "", valor or "")

def _empresas_autenticadas_set():
    raw = session.get("empresas_autenticadas", [])
    return {int(x) for x in raw if str(x).isdigit()}

def _marcar_empresa_autenticada(empresa_id):
    auth_set = _empresas_autenticadas_set()
    auth_set.add(int(empresa_id))
    session["empresas_autenticadas"] = sorted(auth_set)
    sess_set = {int(x) for x in session.get("empresas_sessao", []) if str(x).isdigit()}
    sess_set.add(int(empresa_id))
    session["empresas_sessao"] = sorted(sess_set)
    session["empresa_id"] = int(empresa_id)

# --- VINCULO CONTA PLATAFORMA (USUARIO <-> EMPRESA) ---
def _garantir_vinculo_usuario_empresas():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS usuario_empresas (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                    empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (usuario_id, empresa_id)
                );
                """
            )
        conn.commit()


def _vincular_empresa_a_conta(empresa_id):
    usuario_id = session.get("usuario_id")
    if not usuario_id or not empresa_id:
        return
    _garantir_vinculo_usuario_empresas()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usuario_empresas (usuario_id, empresa_id)
                VALUES (%s, %s)
                ON CONFLICT (usuario_id, empresa_id) DO NOTHING;
                """,
                (int(usuario_id), int(empresa_id)),
            )
            cur.execute(
                """
                UPDATE usuarios
                SET empresa_id = COALESCE(empresa_id, %s)
                WHERE id = %s;
                """,
                (int(empresa_id), int(usuario_id)),
            )
        conn.commit()


def _ids_empresas_da_conta():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return set()
    _garantir_vinculo_usuario_empresas()
    ids = set()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT empresa_id FROM usuario_empresas WHERE usuario_id = %s;",
                (int(usuario_id),),
            )
            for (eid,) in cur.fetchall():
                if eid:
                    ids.add(int(eid))
            cur.execute(
                "SELECT empresa_id FROM usuarios WHERE id = %s AND empresa_id IS NOT NULL;",
                (int(usuario_id),),
            )
            row = cur.fetchone()
            if row and row[0]:
                ids.add(int(row[0]))
    return ids


def _empresas_visiveis_ids():
    if not session.get("usuario_id"):
        return set()
    permitidas = _ids_empresas_da_conta()
    if not permitidas:
        return set()
    autenticadas = _empresas_autenticadas_set()
    return permitidas & autenticadas if autenticadas else permitidas


def _garantir_colunas_empresas():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS senha_hash VARCHAR(255);")
            cur.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS doc_digits VARCHAR(20);")
            cur.execute("SELECT id, nome, documento, senha_hash, doc_digits FROM empresas ORDER BY id ASC;")
            rows = cur.fetchall()
            for emp_id, nome, doc, s_hash, d_dig in rows:
                novo_dig = _somente_digitos(doc) or f"EMP{emp_id:04d}"
                novo_hash = s_hash or generate_password_hash("123456")
                if novo_dig != (d_dig or "") or novo_hash != (s_hash or ""):
                    cur.execute(
                        "UPDATE empresas SET doc_digits = %s, senha_hash = %s WHERE id = %s;",
                        (novo_dig, novo_hash, emp_id),
                    )
        conn.commit()

def get_empresa_ativa(conn, empresa_id=None):
    target_id = empresa_id if empresa_id is not None else session.get("empresa_id")
    if not target_id:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT id, nome, documento, status FROM empresas WHERE id = %s LIMIT 1;", (int(target_id),))
        return cur.fetchone()



@app.context_processor
def inject_globals():
    def safe_url(endpoint, fallback="#"):
        if endpoint in current_app.view_functions:
            return url_for(endpoint)
        return fallback

    empresa_info = None
    try:
        with get_db_connection() as conn:
            emp = get_empresa_ativa(conn, session.get("empresa_id"))
            if emp:
                empresa_info = {
                    "id": emp[0],
                    "nome": emp[1],
                    "documento": emp[2] or "",
                    "status": emp[3],
                }
    except Exception:
        empresa_info = None

    return {
        "safe_url": safe_url,
        "empresa_ativa": empresa_info,
    }


@app.route("/")
def index():
    return redirect(url_for("clientes"))


def _garantir_colunas_clientes(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE clientes
                ADD COLUMN IF NOT EXISTS cpf VARCHAR(20),
                ADD COLUMN IF NOT EXISTS cpf_digits VARCHAR(11),
                ADD COLUMN IF NOT EXISTS cep VARCHAR(10),
                ADD COLUMN IF NOT EXISTS endereco VARCHAR(200);
            """
        )
    conn.commit()


@app.route("/clientes", methods=["GET", "POST"])
def clientes():
    with get_db_connection() as conn:
        _garantir_colunas_clientes(conn)
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        if not empresa:
            flash("Selecione ou cadastre uma empresa antes de acessar esta area.", "warning")
            return redirect(url_for("empresas"))
        empresa_id = empresa[0]

        if request.method == "POST":
            nome = (request.form.get("nome") or "").strip()
            cpf = (request.form.get("cpf") or "").strip() or None
            cpf_digits = _somente_digitos(cpf) if cpf else None
            telefone = (request.form.get("telefone") or "").strip() or None
            email = (request.form.get("email") or "").strip() or None
            cep = (request.form.get("cep") or "").strip() or None
            endereco = (request.form.get("endereco") or "").strip() or None

            if not nome:
                flash("Informe o nome completo do cliente.", "warning")
            elif cpf_digits and len(cpf_digits) != 11:
                flash("Informe um CPF valido com 11 digitos.", "warning")
            else:
                duplicado = False
                if cpf_digits:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT id FROM clientes
                            WHERE empresa_id = %s AND cpf_digits = %s;
                            """,
                            (empresa_id, cpf_digits),
                        )
                        if cur.fetchone():
                            duplicado = True

                if duplicado:
                    flash("Ja existe um cliente com este CPF nesta empresa.", "warning")
                else:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO clientes (empresa_id, nome, cpf, cpf_digits, telefone, email, cep, endereco)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                            """,
                            (empresa_id, nome, cpf, cpf_digits, telefone, email, cep, endereco),
                        )
                    conn.commit()
                    flash("Cliente cadastrado com sucesso.", "success")

            return redirect(url_for("clientes"))

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, nome, cpf, telefone, email, cep, endereco
                FROM clientes
                WHERE empresa_id = %s
                ORDER BY id DESC;
                """,
                (empresa_id,),
            )
            lista_clientes = cur.fetchall()

    return render_template("clientes.html", clientes=lista_clientes, active_page="clientes")


@app.route("/produtos", methods=["GET", "POST"])
def produtos():
    with get_db_connection() as conn:
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        if not empresa:
            flash("Selecione ou cadastre uma empresa antes de acessar esta area.", "warning")
            return redirect(url_for("empresas"))
        empresa_id = empresa[0]

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            preco_raw = request.form.get("preco", "0").strip().replace(",", ".")
            estoque_raw = request.form.get("estoque", "0").strip()

            try:
                preco = Decimal(preco_raw).quantize(Decimal("0.01"))
                estoque = int(estoque_raw)
            except (InvalidOperation, ValueError):
                preco = None
                estoque = None

            if nome and preco is not None and estoque is not None and preco >= Decimal("0.00") and estoque >= 0:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO produtos (empresa_id, nome, preco, estoque)
                        VALUES (%s, %s, %s, %s);
                        """,
                        (empresa_id, nome, preco, estoque),
                    )
                conn.commit()

            return redirect(url_for("produtos"))

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, nome, preco, estoque
                FROM produtos
                WHERE empresa_id = %s
                ORDER BY id DESC;
                """,
                (empresa_id,),
            )
            lista_produtos = cur.fetchall()

    return render_template("produtos.html", produtos=lista_produtos, active_page="produtos")


@app.route("/vendas")
def vendas():
    return render_template("base.html", active_page="vendas")


def _contar_registros_seguro(cur, tabela, empresa_id=None):
    try:
        cur.execute("SELECT to_regclass(%s);", (f"public.{tabela}",))
        existe = cur.fetchone()
        if not existe or not existe[0]:
            return None
        if empresa_id is not None:
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s AND column_name = 'empresa_id'
                LIMIT 1;
                """,
                (tabela,),
            )
            tem_emp = cur.fetchone() is not None
            if tem_emp:
                cur.execute(f"SELECT COUNT(*) FROM {tabela} WHERE empresa_id = %s;", (int(empresa_id),))
                row = cur.fetchone()
                return int(row[0]) if row else 0
        cur.execute(f"SELECT COUNT(*) FROM {tabela};")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None


@app.route("/dashboard")
def dashboard():
    totais = {
        "empresas": 0,
        "clientes": 0,
        "produtos": None,
        "vendas": None,
    }
    empresa = None
    try:
        with get_db_connection() as conn:
            empresa = get_empresa_ativa(conn, session.get("empresa_id"))
            with conn.cursor() as cur:
                emp_cnt = _contar_registros_seguro(cur, "empresas")
                totais["empresas"] = emp_cnt if emp_cnt is not None else 0
                emp_id = empresa["id"] if isinstance(empresa, dict) and empresa.get("id") else None
                cli_cnt = _contar_registros_seguro(cur, "clientes", emp_id)
                totais["clientes"] = cli_cnt if cli_cnt is not None else 0
                totais["produtos"] = _contar_registros_seguro(cur, "produtos", emp_id)
                totais["vendas"] = _contar_registros_seguro(cur, "vendas", emp_id)
    except Exception as exc:
        flash(f"Nao foi possivel carregar todos os indicadores: {exc}", "warning")

    nome_empresa = (
        empresa.get("nome") if isinstance(empresa, dict) and empresa.get("nome") else "Nenhuma empresa ativa"
    )
    html_dashboard = """{% extends "base.html" %}
{% block title %}Dashboard - SimploGestão{% endblock %}
{% block content %}
<div class="page-header">
  <div>
    <h1>Dashboard</h1>
    <p>Visão geral da empresa ativa e acesso rápido aos módulos do SimploGestão.</p>
  </div>
</div>

<div class="card" style="max-width:560px;margin-bottom:16px;">
  <div class="form-stack">
    <div class="form-group">
      <label>Empresa ativa</label>
      <p style="margin:0;font-weight:600;">{{ nome_empresa|e }}</p>
    </div>

    <div class="form-group">
      <label>Resumo operacional</label>
      <p style="margin:0;">Empresas cadastradas: <strong>{{ totais.empresas }}</strong> &middot; Clientes da empresa: <strong>{{ totais.clientes }}</strong>{% if totais.produtos is not none %} &middot; Produtos: <strong>{{ totais.produtos }}</strong>{% endif %}{% if totais.vendas is not none %} &middot; Vendas: <strong>{{ totais.vendas }}</strong>{% endif %}</p>
    </div>

    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px;">
      <a href="{{ safe_url('clientes', '/clientes') }}" class="btn btn-primary">Abrir Clientes</a>
      <a href="{{ safe_url('empresas', '/empresas') }}" class="btn btn-secondary">Gerenciar Empresas</a>
    </div>
  </div>
</div>
{% endblock %}"""
    return render_template_string(
        html_dashboard,
        pagina="dashboard",
        nome_empresa=nome_empresa,
        totais=totais,
    )

@app.route("/empresas", methods=["GET", "POST"])
def empresas():
    _garantir_colunas_empresas()
    acao = (request.form.get("acao") or "cadastrar").strip().lower()
    erro = None
    empresa_login_id = request.args.get("entrar", type=int)
    empresa_editar_id = request.args.get("editar", type=int)

    if request.method == "POST":
        if acao == "cadastrar":
            nome = (request.form.get("nome") or "").strip()
            documento = (request.form.get("documento") or "").strip()
            senha = request.form.get("senha") or ""
            status = (request.form.get("status") or "Ativa").strip()
            doc_digits = _somente_digitos(documento)

            if not nome:
                erro = "Informe o nome da empresa."
            elif len(doc_digits) not in (11, 14):
                erro = "Informe um CPF (11 digitos) ou CNPJ (14 digitos) valido."
            elif len(senha) < 4:
                erro = "A senha da empresa deve ter pelo menos 4 caracteres."
            else:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM empresas WHERE doc_digits = %s;", (doc_digits,))
                        if cur.fetchone():
                            erro = "Ja existe uma empresa cadastrada com esse CPF/CNPJ."
                        else:
                            senha_hash = generate_password_hash(senha)
                            cur.execute(
                                """
                                INSERT INTO empresas (nome, documento, doc_digits, senha_hash, status)
                                VALUES (%s, %s, %s, %s, %s)
                                RETURNING id;
                                """,
                                (nome, documento, doc_digits, senha_hash, status),
                            )
                            nova_id = cur.fetchone()[0]
                    conn.commit()
                if not erro:
                    _marcar_empresa_autenticada(nova_id)
                    _vincular_empresa_a_conta(nova_id)
                    flash(f"Empresa '{nome}' cadastrada e aberta na sua sessao.", "success")
                    return redirect(url_for("dashboard"))

        elif acao == "entrar":
            emp_id_form = request.form.get("empresa_id", type=int)
            documento_login = (request.form.get("documento_login") or "").strip()
            senha_login = request.form.get("senha_login") or ""
            doc_digits_login = _somente_digitos(documento_login)
            empresa_login_id = emp_id_form

            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    if emp_id_form:
                        cur.execute(
                            "SELECT id, nome, doc_digits, senha_hash FROM empresas WHERE id = %s;",
                            (emp_id_form,),
                        )
                    else:
                        cur.execute(
                            "SELECT id, nome, doc_digits, senha_hash FROM empresas WHERE doc_digits = %s;",
                            (doc_digits_login,),
                        )
                    emp_row = cur.fetchone()

            if not emp_row:
                erro = "Empresa nao encontrada."
            elif emp_id_form and doc_digits_login and emp_row[2] != doc_digits_login:
                erro = "O CPF/CNPJ informado nao corresponde a esta empresa."
            elif not emp_row[3] or not check_password_hash(emp_row[3], senha_login):
                erro = "CPF/CNPJ ou senha incorretos para esta empresa."
            else:
                _marcar_empresa_autenticada(emp_row[0])
                _vincular_empresa_a_conta(emp_row[0])
                flash(f"Acesso liberado na empresa '{emp_row[1]}'.", "success")
                return redirect(url_for("dashboard"))

        elif acao == "ativar_sessao":
            emp_id_form = request.form.get("empresa_id", type=int)
            if emp_id_form and emp_id_form in _empresas_autenticadas_set():
                session["empresa_id"] = int(emp_id_form)
                flash("Sessao alternada para a empresa ja autenticada.", "success")
                return redirect(url_for("dashboard"))
            empresa_login_id = emp_id_form
            erro = "Informe CPF/CNPJ e senha para entrar nesta empresa."

        elif acao == "editar":
            emp_id_form = request.form.get("empresa_id", type=int)
            nome = (request.form.get("nome") or "").strip()
            documento = (request.form.get("documento") or "").strip()
            status = (request.form.get("status") or "Ativa").strip()
            nova_senha = request.form.get("senha") or ""
            doc_digits = _somente_digitos(documento)
            empresa_editar_id = emp_id_form

            if not emp_id_form:
                erro = "Empresa invalida para edicao."
            elif not nome:
                erro = "Informe o nome da empresa."
            elif len(doc_digits) not in (11, 14):
                erro = "Informe um CPF (11 digitos) ou CNPJ (14 digitos) valido."
            elif nova_senha and len(nova_senha) < 4:
                erro = "A nova senha da empresa deve ter pelo menos 4 caracteres."
            else:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM empresas WHERE id = %s;", (emp_id_form,))
                        if not cur.fetchone():
                            erro = "Empresa nao encontrada."
                        else:
                            cur.execute(
                                "SELECT id FROM empresas WHERE doc_digits = %s AND id <> %s;",
                                (doc_digits, emp_id_form),
                            )
                            if cur.fetchone():
                                erro = "Ja existe outra empresa cadastrada com esse CPF/CNPJ."
                            else:
                                if nova_senha:
                                    senha_hash = generate_password_hash(nova_senha)
                                    cur.execute(
                                        """
                                        UPDATE empresas
                                           SET nome = %s,
                                               documento = %s,
                                               doc_digits = %s,
                                               status = %s,
                                               senha_hash = %s
                                         WHERE id = %s;
                                        """,
                                        (nome, documento, doc_digits, status, senha_hash, emp_id_form),
                                    )
                                else:
                                    cur.execute(
                                        """
                                        UPDATE empresas
                                           SET nome = %s,
                                               documento = %s,
                                               doc_digits = %s,
                                               status = %s
                                         WHERE id = %s;
                                        """,
                                        (nome, documento, doc_digits, status, emp_id_form),
                                    )
                    if not erro:
                        conn.commit()
                if not erro:
                    flash(f"Empresa '{nome}' atualizada com sucesso.", "success")
                    return redirect(url_for("empresas"))

        elif acao == "excluir":
            emp_id_form = request.form.get("empresa_id", type=int)
            if not emp_id_form:
                erro = "Empresa invalida para exclusao."
            else:
                try:
                    with get_db_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT nome FROM empresas WHERE id = %s;", (emp_id_form,))
                            row_emp = cur.fetchone()
                            if not row_emp:
                                erro = "Empresa nao encontrada."
                            else:
                                nome_excluida = row_emp[0]
                                for tbl_check in ("clientes", "produtos", "vendas", "financeiro", "usuario_empresas", "conta_empresas"):
                                    cur.execute("SELECT to_regclass(%s);", (f"public.{tbl_check}",))
                                    reg = cur.fetchone()
                                    if reg and reg[0]:
                                        cur.execute(
                                            """
                                            SELECT 1 FROM information_schema.columns
                                            WHERE table_schema = 'public' AND table_name = %s AND column_name = 'empresa_id'
                                            LIMIT 1;
                                            """,
                                            (tbl_check,),
                                        )
                                        if cur.fetchone():
                                            cur.execute(f"DELETE FROM {tbl_check} WHERE empresa_id = %s;", (emp_id_form,))
                                cur.execute("DELETE FROM empresas WHERE id = %s;", (emp_id_form,))
                        if not erro:
                            conn.commit()
                except Exception as exc:
                    erro = f"Nao foi possivel excluir a empresa: {exc}"

                if not erro:
                    auth_set = set(_empresas_autenticadas_set())
                    auth_set.discard(emp_id_form)
                    session["empresas_autenticadas"] = sorted(auth_set)

                    sess_set = set(session.get("empresas_sessao", []))
                    sess_set.discard(emp_id_form)
                    session["empresas_sessao"] = sorted(sess_set)

                    if session.get("empresa_id") == emp_id_form:
                        if auth_set:
                            session["empresa_id"] = sorted(auth_set)[0]
                        else:
                            session.pop("empresa_id", None)

                    flash(f"Empresa '{nome_excluida}' excluida com sucesso.", "success")
                    return redirect(url_for("empresas"))

    auth_ids = _empresas_visiveis_ids()
    empresa_atual_id = session.get("empresa_id")
    empresas_fmt = []
    empresa_para_login = None
    empresa_para_editar = None

    if auth_ids:
        placeholders = ",".join(["%s"] * len(auth_ids))
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, nome, documento, doc_digits, status, criado_em FROM empresas WHERE id IN ({placeholders}) ORDER BY id DESC;",
                    tuple(auth_ids),
                )
                lista_empresas = cur.fetchall()

        for e in lista_empresas:
            item = {
                "id": e[0],
                "nome": e[1],
                "documento": e[2] or e[3] or "",
                "status": e[4],
                "criado_em": e[5],
                "autenticada": True,
                "ativa": empresa_atual_id == e[0],
            }
            empresas_fmt.append(item)
            if empresa_login_id and e[0] == empresa_login_id:
                empresa_para_login = item
            if empresa_editar_id and e[0] == empresa_editar_id:
                empresa_para_editar = item

    return render_template(
        "empresas.html",
        pagina="empresas",
        empresas=empresas_fmt,
        erro=erro,
        empresa_para_login=empresa_para_login,
        empresa_para_editar=empresa_para_editar,
    )


try:
    from auth_routes import auth_bp
    app.register_blueprint(auth_bp)
except Exception as _auth_err:
    app.logger.warning("Nao foi possivel registrar auth_bp: %s", _auth_err)

if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host=host, port=port, debug=debug)
