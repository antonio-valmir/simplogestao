from werkzeug.security import generate_password_hash, check_password_hash
import re
import os
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from flask import flash, session, Flask, current_app, redirect, render_template, request, url_for
import psycopg

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")


def get_db_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "simplogestao_db"),
        user=os.getenv("DB_USER", "simplogestao_user"),
        password=os.getenv("DB_PASSWORD", ""),
    )


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


@app.route("/clientes", methods=["GET", "POST"])
def clientes():
    with get_db_connection() as conn:
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        if not empresa:
            flash("Selecione ou cadastre uma empresa antes de acessar esta area.", "warning")
            return redirect(url_for("empresas"))
        empresa_id = empresa[0]

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            telefone = request.form.get("telefone", "").strip() or None
            email = request.form.get("email", "").strip() or None

            if nome:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO clientes (empresa_id, nome, telefone, email)
                        VALUES (%s, %s, %s, %s);
                        """,
                        (empresa_id, nome, telefone, email),
                    )
                conn.commit()

            return redirect(url_for("clientes"))

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, nome, telefone, email
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


@app.route("/dashboard")
def dashboard():
    if "index" in app.view_functions:
        return app.view_functions["index"]()
    if "/" in [r.rule for r in app.url_map.iter_rules()]:
        return redirect("/")
    return render_template("base.html", active_page="dashboard")

@app.route("/empresas", methods=["GET", "POST"])
def empresas():
    _garantir_colunas_empresas()
    acao = (request.form.get("acao") or "cadastrar").strip().lower()
    erro = None
    empresa_login_id = request.args.get("entrar", type=int)

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

    auth_ids = _empresas_autenticadas_set()
    empresa_atual_id = session.get("empresa_id")
    empresas_fmt = []
    empresa_para_login = None

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

    return render_template(
        "empresas.html",
        pagina="empresas",
        empresas=empresas_fmt,
        erro=erro,
        empresa_para_login=empresa_para_login,
    )

if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host=host, port=port, debug=debug)
