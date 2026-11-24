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
        acao = (request.form.get("acao") or "cadastrar").strip().lower()
        cliente_editar_id = request.args.get("editar", type=int)

        if request.method == "POST":
            if acao == "editar":
                cli_id = request.form.get("cliente_id", type=int)
                nome = (request.form.get("nome") or "").strip()
                cpf = (request.form.get("cpf") or "").strip() or None
                cpf_digits = _somente_digitos(cpf) if cpf else None
                telefone = (request.form.get("telefone") or "").strip() or None
                email = (request.form.get("email") or "").strip() or None
                cep = (request.form.get("cep") or "").strip() or None
                endereco = (request.form.get("endereco") or "").strip() or None

                if not cli_id:
                    flash("Cliente invalido para edicao.", "warning")
                elif not nome:
                    flash("Informe o nome completo do cliente.", "warning")
                    return redirect(url_for("clientes", editar=cli_id))
                elif cpf_digits and len(cpf_digits) != 11:
                    flash("Informe um CPF valido com 11 digitos.", "warning")
                    return redirect(url_for("clientes", editar=cli_id))
                else:
                    duplicado = False
                    if cpf_digits:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT id FROM clientes
                                WHERE empresa_id = %s AND cpf_digits = %s AND id <> %s;
                                """,
                                (empresa_id, cpf_digits, cli_id),
                            )
                            if cur.fetchone():
                                duplicado = True
                    if duplicado:
                        flash("Ja existe outro cliente com este CPF nesta empresa.", "warning")
                        return redirect(url_for("clientes", editar=cli_id))

                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE clientes
                               SET nome = %s,
                                   cpf = %s,
                                   cpf_digits = %s,
                                   telefone = %s,
                                   email = %s,
                                   cep = %s,
                                   endereco = %s
                             WHERE id = %s AND empresa_id = %s;
                            """,
                            (nome, cpf, cpf_digits, telefone, email, cep, endereco, cli_id, empresa_id),
                        )
                        atualizado = cur.rowcount
                    conn.commit()
                    if atualizado:
                        flash("Cliente atualizado com sucesso.", "success")
                    else:
                        flash("Cliente nao encontrado nesta empresa.", "warning")
                return redirect(url_for("clientes"))

            elif acao == "excluir":
                cli_id = request.form.get("cliente_id", type=int)
                if not cli_id:
                    flash("Cliente invalido para exclusao.", "warning")
                else:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT nome FROM clientes WHERE id = %s AND empresa_id = %s;",
                                (cli_id, empresa_id),
                            )
                            row_cli = cur.fetchone()
                            if not row_cli:
                                flash("Cliente nao encontrado nesta empresa.", "warning")
                            else:
                                nome_cli = row_cli[0]
                                tem_vendas = False
                                cur.execute("SELECT to_regclass('public.vendas');")
                                reg_v = cur.fetchone()
                                if reg_v and reg_v[0]:
                                    cur.execute(
                                        """
                                        SELECT 1 FROM information_schema.columns
                                        WHERE table_schema = 'public' AND table_name = 'vendas' AND column_name = 'cliente_id'
                                        LIMIT 1;
                                        """
                                    )
                                    if cur.fetchone():
                                        cur.execute(
                                            "SELECT 1 FROM vendas WHERE cliente_id = %s AND empresa_id = %s LIMIT 1;",
                                            (cli_id, empresa_id),
                                        )
                                        if cur.fetchone():
                                            tem_vendas = True
                                if tem_vendas:
                                    flash("Nao e possivel excluir: este cliente possui vendas registradas.", "warning")
                                else:
                                    cur.execute(
                                        "DELETE FROM clientes WHERE id = %s AND empresa_id = %s;",
                                        (cli_id, empresa_id),
                                    )
                                    conn.commit()
                                    flash(f"Cliente '{nome_cli}' excluido com sucesso.", "success")
                    except Exception as exc:
                        conn.rollback()
                        flash(f"Nao foi possivel excluir o cliente: {exc}", "danger")
                return redirect(url_for("clientes"))

            else:
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

        cliente_para_editar = None
        if cliente_editar_id:
            for c in lista_clientes:
                if c[0] == cliente_editar_id:
                    cliente_para_editar = c
                    break

    return render_template(
        "clientes.html",
        clientes=lista_clientes,
        cliente_para_editar=cliente_para_editar,
        active_page="clientes",
    )


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


def garantir_tabelas_base(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS empresas (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(160) NOT NULL,
                documento VARCHAR(40),
                telefone VARCHAR(40),
                email VARCHAR(120),
                ativo BOOLEAN NOT NULL DEFAULT TRUE,
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(120) NOT NULL,
                email VARCHAR(160) UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                empresa_id INTEGER REFERENCES empresas(id) ON DELETE SET NULL,
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                empresa_id INTEGER REFERENCES empresas(id) ON DELETE CASCADE,
                nome VARCHAR(160) NOT NULL,
                telefone VARCHAR(40),
                email VARCHAR(120),
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id) ON DELETE CASCADE;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                empresa_id INTEGER REFERENCES empresas(id) ON DELETE CASCADE,
                nome VARCHAR(160) NOT NULL,
                preco NUMERIC(12, 2) NOT NULL DEFAULT 0,
                estoque INTEGER NOT NULL DEFAULT 0,
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id) ON DELETE CASCADE;")
        cur.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS preco NUMERIC(12, 2) NOT NULL DEFAULT 0;")
        cur.execute("ALTER TABLE produtos ADD COLUMN IF NOT EXISTS estoque INTEGER NOT NULL DEFAULT 0;")


def garantir_estrutura_vendas(conn):
    garantir_tabelas_base(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS venda_itens (
                id SERIAL PRIMARY KEY,
                venda_id INTEGER NOT NULL REFERENCES vendas(id) ON DELETE CASCADE,
                produto_id INTEGER NOT NULL REFERENCES produtos(id) ON DELETE RESTRICT,
                quantidade INTEGER NOT NULL CHECK (quantidade > 0),
                preco_unitario NUMERIC(12, 2) NOT NULL DEFAULT 0,
                subtotal NUMERIC(12, 2) NOT NULL DEFAULT 0
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vendas (
                id SERIAL PRIMARY KEY,
                empresa_id INTEGER REFERENCES empresas(id) ON DELETE CASCADE,
                cliente_id INTEGER,
                forma_pagamento VARCHAR(40) NOT NULL DEFAULT 'À vista',
                total NUMERIC(12, 2) NOT NULL DEFAULT 0,
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id) ON DELETE CASCADE;")
        cur.execute("ALTER TABLE vendas ADD COLUMN IF NOT EXISTS forma_pagamento VARCHAR(40) NOT NULL DEFAULT 'À vista';")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_vendas_empresa_id ON vendas (empresa_id, id DESC);")


@app.route("/vendas/<int:venda_id>/comprovante")
def comprovante_venda(venda_id):
    with get_db_connection() as conn:
        garantir_tabelas_base(conn)
        garantir_estrutura_vendas(conn)
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        empresa_id = _empresa_id_seguro(empresa)
        if not empresa_id:
            flash("Selecione ou cadastre uma empresa antes de visualizar o comprovante.", "warning")
            return redirect(url_for("empresas"))

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    v.id,
                    v.empresa_id,
                    v.cliente_id,
                    COALESCE(v.forma_pagamento, 'À vista') AS forma_pagamento,
                    v.total,
                    v.criado_em,
                    COALESCE(c.nome, 'Consumidor final') AS cliente_nome,
                    COALESCE(e.nome, 'Empresa') AS empresa_nome
                FROM vendas v
                LEFT JOIN clientes c ON c.id = v.cliente_id AND c.empresa_id = v.empresa_id
                LEFT JOIN empresas e ON e.id = v.empresa_id
                WHERE v.id = %s AND v.empresa_id = %s;
                """,
                (venda_id, empresa_id),
            )
            venda = cur.fetchone()
            if not venda:
                flash("Comprovante não encontrado para a empresa ativa.", "warning")
                return redirect(url_for("vendas"))

            cur.execute(
                """
                SELECT
                    vi.id,
                    p.nome,
                    vi.quantidade,
                    vi.preco_unitario,
                    vi.subtotal
                FROM venda_itens vi
                JOIN produtos p ON p.id = vi.produto_id
                WHERE vi.venda_id = %s
                ORDER BY vi.id ASC;
                """,
                (venda_id,),
            )
            itens = cur.fetchall()

    return render_template(
        "comprovante_venda.html",
        venda=venda,
        itens=itens,
        empresa_ativa=empresa,
    )

@app.route("/vendas", methods=["GET", "POST"])
def vendas():
    with get_db_connection() as conn:
        garantir_tabelas_compras_financeiro(conn)
        garantir_estrutura_vendas(conn)
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        empresa_id = _empresa_id_seguro(empresa)
        if not empresa_id:
            flash("Selecione ou cadastre uma empresa antes de acessar Vendas.", "warning")
            return redirect(url_for("empresas"))

        erro_venda = None

        if request.method == "POST":
            cliente_raw = request.form.get("cliente_id", "").strip()
            produtos_raw = request.form.getlist("produto_id")
            qtds_raw = request.form.getlist("quantidade")
            forma_pagamento = request.form.get("forma_pagamento", "À vista").strip() or "À vista"

            try:
                cliente_id = int(cliente_raw) if cliente_raw else None
            except ValueError:
                cliente_id = None

            itens_agrupados = {}
            erro_validacao_itens = False
            for idx, p_raw in enumerate(produtos_raw):
                p_str = (p_raw or "").strip()
                q_str = (qtds_raw[idx] if idx < len(qtds_raw) else "1").strip()
                if not p_str and not q_str:
                    continue
                try:
                    pid = int(p_str)
                    qtd = int(q_str)
                except ValueError:
                    erro_validacao_itens = True
                    break
                if pid <= 0 or qtd <= 0:
                    erro_validacao_itens = True
                    break
                itens_agrupados[pid] = itens_agrupados.get(pid, 0) + qtd

            if erro_validacao_itens or not itens_agrupados:
                erro_venda = "Selecione ao menos uma mercadoria válida e informe quantidade maior que zero."
            else:
                try:
                    with conn.cursor() as cur:
                        itens_resolvidos = []
                        total_venda = Decimal("0.00")

                        for pid, qtd in itens_agrupados.items():
                            cur.execute(
                                "SELECT id, nome, preco, estoque FROM produtos WHERE id = %s AND empresa_id = %s FOR UPDATE;",
                                (pid, empresa_id),
                            )
                            prod = cur.fetchone()
                            if not prod:
                                erro_venda = "Uma das mercadorias selecionadas não foi encontrada para a empresa ativa."
                                break
                            if int(prod[3]) < qtd:
                                erro_venda = f"Estoque insuficiente para {prod[1]} (disponível: {prod[3]} un., solicitado: {qtd} un.)."
                                break

                            preco_unit = Decimal(str(prod[2] or "0.00"))
                            subtotal = (preco_unit * Decimal(qtd)).quantize(Decimal("0.01"))
                            total_venda += subtotal
                            itens_resolvidos.append(
                                {
                                    "id": prod[0],
                                    "nome": prod[1],
                                    "qtd": qtd,
                                    "preco_unit": preco_unit,
                                    "subtotal": subtotal,
                                }
                            )

                        if erro_venda:
                            conn.rollback()
                        else:
                            total_venda = total_venda.quantize(Decimal("0.01"))
                            cliente_nome = "Consumidor final"
                            if cliente_id is not None:
                                cur.execute(
                                    "SELECT nome FROM clientes WHERE id = %s AND empresa_id = %s;",
                                    (cliente_id, empresa_id),
                                )
                                cli_row = cur.fetchone()
                                if cli_row:
                                    cliente_nome = cli_row[0]
                                else:
                                    cliente_id = None

                            cur.execute(
                                """
                                INSERT INTO vendas (empresa_id, cliente_id, forma_pagamento, total)
                                VALUES (%s, %s, %s, %s)
                                RETURNING id;
                                """,
                                (empresa_id, cliente_id, forma_pagamento, total_venda),
                            )
                            venda_id = cur.fetchone()[0]

                            resumo_itens = []
                            for item in itens_resolvidos:
                                cur.execute(
                                    "UPDATE produtos SET estoque = estoque - %s WHERE id = %s AND empresa_id = %s;",
                                    (item["qtd"], item["id"], empresa_id),
                                )
                                cur.execute(
                                    """
                                    INSERT INTO venda_itens (venda_id, produto_id, quantidade, preco_unitario, subtotal)
                                    VALUES (%s, %s, %s, %s, %s);
                                    """,
                                    (venda_id, item["id"], item["qtd"], item["preco_unit"], item["subtotal"]),
                                )
                                resumo_itens.append(f"{item['nome']} ({item['qtd']}x)")

                            descricao_itens = ", ".join(resumo_itens)
                            cur.execute(
                                """
                                INSERT INTO lancamentos_financeiros (
                                    empresa_id, tipo, origem, referencia_id,
                                    descricao, forma_pagamento, valor, status, pago_em
                                )
                                VALUES (%s, 'entrada', 'venda', %s, %s, %s, %s, 'Pago', CURRENT_TIMESTAMP);
                                """,
                                (
                                    empresa_id,
                                    venda_id,
                                    f"Venda #{venda_id} - {descricao_itens} - {cliente_nome}",
                                    forma_pagamento,
                                    total_venda,
                                ),
                            )

                    if not erro_venda:
                        conn.commit()
                        flash(
                            f"Venda #{venda_id} confirmada ({descricao_itens}) — Total: R$ {total_venda:.2f}.",
                            "success",
                        )
                        return redirect(url_for("comprovante_venda", venda_id=venda_id))
                except Exception as exc:
                    conn.rollback()
                    erro_venda = f"Erro ao registrar venda: {exc}"

        clientes_lista = []
        produtos_lista = []
        vendas_lista = []

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, nome FROM clientes WHERE empresa_id = %s ORDER BY nome ASC;",
                (empresa_id,),
            )
            clientes_lista = cur.fetchall()

            cur.execute(
                "SELECT id, nome, preco, estoque FROM produtos WHERE empresa_id = %s ORDER BY nome ASC;",
                (empresa_id,),
            )
            produtos_lista = cur.fetchall()

            cur.execute(
                """
                SELECT
                    v.id,
                    COALESCE(c.nome, 'Consumidor final') AS cliente_nome,
                    COALESCE(v.forma_pagamento, 'À vista') AS forma_pagamento,
                    v.total
                FROM vendas v
                LEFT JOIN clientes c ON c.id = v.cliente_id AND c.empresa_id = v.empresa_id
                WHERE v.empresa_id = %s
                ORDER BY v.id DESC
                LIMIT 30;
                """,
                (empresa_id,),
            )
            vendas_lista = cur.fetchall()

    return render_template(
        "vendas.html",
        active_page="vendas",
        erro_venda=erro_venda,
        clientes=clientes_lista,
        produtos=produtos_lista,
        vendas=vendas_lista,
    )


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

@app.route("/empresas/cadastrar", methods=["GET", "POST"])
def cadastrar_empresa():
    _garantir_colunas_empresas()
    erro = None

    if request.method == "POST":
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

    return render_template("cadastrar_empresa.html", erro=erro, active_page="cadastrar_empresa")


@app.route("/empresas", methods=["GET", "POST"])
def empresas():
    _garantir_colunas_empresas()
    acao = (request.form.get("acao") or "entrar").strip().lower()
    erro = None
    empresa_login_id = request.args.get("entrar", type=int)
    empresa_editar_id = request.args.get("editar", type=int)

    if request.method == "POST":
        if acao == "cadastrar":
            return redirect(url_for("cadastrar_empresa"))

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



def _empresa_id_seguro(empresa):
    if isinstance(empresa, dict):
        val = empresa.get("id")
        return int(val) if val is not None else None
    if isinstance(empresa, (tuple, list)) and len(empresa) > 0:
        return int(empresa[0]) if empresa[0] is not None else None
    return None


def garantir_tabelas_compras_financeiro(conn):
    garantir_tabelas_base(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS compras (
                id SERIAL PRIMARY KEY,
                empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                produto_id INTEGER NOT NULL REFERENCES produtos(id),
                fornecedor VARCHAR(140),
                quantidade INTEGER NOT NULL CHECK (quantidade > 0),
                custo_unitario NUMERIC(12, 2) NOT NULL CHECK (custo_unitario >= 0),
                valor_total NUMERIC(12, 2) NOT NULL CHECK (valor_total >= 0),
                forma_pagamento VARCHAR(40) NOT NULL DEFAULT 'Dinheiro',
                status_pagamento VARCHAR(20) NOT NULL DEFAULT 'Pendente',
                status VARCHAR(20) NOT NULL DEFAULT 'Concluida',
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_compras_empresa_id
            ON compras (empresa_id, id DESC);
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lancamentos_financeiros (
                id SERIAL PRIMARY KEY,
                empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                tipo VARCHAR(15) NOT NULL CHECK (tipo IN ('entrada', 'saida')),
                origem VARCHAR(30) NOT NULL DEFAULT 'manual',
                referencia_id INTEGER,
                descricao VARCHAR(180) NOT NULL,
                forma_pagamento VARCHAR(40) NOT NULL DEFAULT 'Dinheiro',
                valor NUMERIC(12, 2) NOT NULL CHECK (valor >= 0),
                status VARCHAR(20) NOT NULL DEFAULT 'Pendente' CHECK (status IN ('Pendente', 'Pago', 'Cancelado')),
                vencimento DATE,
                pago_em TIMESTAMP,
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lancamentos_empresa_id
            ON lancamentos_financeiros (empresa_id, id DESC);
            """
        )


@app.route("/compras", methods=["GET", "POST"])
def compras():
    with get_db_connection() as conn:
        garantir_tabelas_compras_financeiro(conn)
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        empresa_id = _empresa_id_seguro(empresa)
        if not empresa_id:
            flash("Selecione ou cadastre uma empresa antes de acessar Compras.", "warning")
            return redirect(url_for("empresas"))

        if request.method == "POST":
            produto_raw = request.form.get("produto_id", "").strip()
            qtd_raw = request.form.get("quantidade", "").strip()
            custo_raw = request.form.get("custo_unitario", "0").strip().replace(",", ".")
            fornecedor = request.form.get("fornecedor", "").strip() or "Fornecedor nao informado"
            forma_pagamento = request.form.get("forma_pagamento", "Dinheiro").strip() or "Dinheiro"
            status_pagamento = request.form.get("status_pagamento", "Pendente").strip()
            if status_pagamento not in ("Pendente", "Pago"):
                status_pagamento = "Pendente"

            try:
                produto_id = int(produto_raw)
                quantidade = int(qtd_raw)
                custo_unitario = Decimal(custo_raw).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                produto_id = None
                quantidade = None
                custo_unitario = None

            if not produto_id or not quantidade or quantidade <= 0 or custo_unitario is None or custo_unitario < Decimal("0.00"):
                flash("Informe um produto valido, quantidade maior que zero e custo valido.", "error")
                return redirect(url_for("compras"))

            valor_total = (custo_unitario * Decimal(quantidade)).quantize(Decimal("0.01"))
            pago_em_sql = "CURRENT_TIMESTAMP" if status_pagamento == "Pago" else "NULL"

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, nome FROM produtos WHERE id = %s AND empresa_id = %s FOR UPDATE;",
                        (produto_id, empresa_id),
                    )
                    prod = cur.fetchone()
                    if not prod:
                        flash("Produto nao encontrado para a empresa ativa.", "error")
                        return redirect(url_for("compras"))

                    nome_produto = prod[1]

                    cur.execute(
                        """
                        INSERT INTO compras (
                            empresa_id, produto_id, fornecedor, quantidade,
                            custo_unitario, valor_total, forma_pagamento,
                            status_pagamento, status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Concluida')
                        RETURNING id;
                        """,
                        (
                            empresa_id,
                            produto_id,
                            fornecedor,
                            quantidade,
                            custo_unitario,
                            valor_total,
                            forma_pagamento,
                            status_pagamento,
                        ),
                    )
                    compra_id = cur.fetchone()[0]

                    cur.execute(
                        """
                        UPDATE produtos
                        SET estoque = estoque + %s
                        WHERE id = %s AND empresa_id = %s;
                        """,
                        (quantidade, produto_id, empresa_id),
                    )

                    cur.execute(
                        f"""
                        INSERT INTO lancamentos_financeiros (
                            empresa_id, tipo, origem, referencia_id,
                            descricao, forma_pagamento, valor, status, pago_em
                        )
                        VALUES (%s, 'saida', 'compra', %s, %s, %s, %s, %s, {pago_em_sql});
                        """,
                        (
                            empresa_id,
                            compra_id,
                            f"Compra #{compra_id} - {nome_produto} ({quantidade}x)",
                            forma_pagamento,
                            valor_total,
                            status_pagamento,
                        ),
                    )

                conn.commit()
                flash(f"Compra #{compra_id} registrada: +{quantidade} un. em {nome_produto}.", "success")
            except Exception as exc:
                conn.rollback()
                flash(f"Erro ao registrar compra: {exc}", "error")

            return redirect(url_for("compras"))

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, nome, estoque FROM produtos WHERE empresa_id = %s ORDER BY nome ASC;",
                (empresa_id,),
            )
            produtos_lista = cur.fetchall()

            cur.execute(
                """
                SELECT
                    c.id,
                    c.criado_em,
                    p.nome,
                    c.fornecedor,
                    c.quantidade,
                    c.custo_unitario,
                    c.valor_total,
                    c.forma_pagamento,
                    c.status_pagamento,
                    c.status
                FROM compras c
                JOIN produtos p ON p.id = c.produto_id AND p.empresa_id = c.empresa_id
                WHERE c.empresa_id = %s
                ORDER BY c.id DESC;
                """,
                (empresa_id,),
            )
            compras_lista = cur.fetchall()

    return render_template(
        "compras.html",
        active_page="compras",
        produtos=produtos_lista,
        compras=compras_lista,
    )


@app.route("/compras/<int:compra_id>/cancelar", methods=["POST"])
def cancelar_compra(compra_id):
    with get_db_connection() as conn:
        garantir_tabelas_compras_financeiro(conn)
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        empresa_id = _empresa_id_seguro(empresa)
        if not empresa_id:
            flash("Selecione ou cadastre uma empresa antes de continuar.", "warning")
            return redirect(url_for("empresas"))

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, produto_id, quantidade, status
                    FROM compras
                    WHERE id = %s AND empresa_id = %s
                    FOR UPDATE;
                    """,
                    (compra_id, empresa_id),
                )
                compra = cur.fetchone()
                if not compra:
                    flash("Compra nao encontrada para a empresa ativa.", "error")
                    return redirect(url_for("compras"))

                if compra[3] == "Cancelada":
                    flash("Esta compra ja esta cancelada.", "warning")
                    return redirect(url_for("compras"))

                produto_id = compra[1]
                qtd_compra = int(compra[2])

                cur.execute(
                    "SELECT estoque, nome FROM produtos WHERE id = %s AND empresa_id = %s FOR UPDATE;",
                    (produto_id, empresa_id),
                )
                prod = cur.fetchone()
                if not prod or int(prod[0]) < qtd_compra:
                    flash(
                        "Nao e possivel estornar: o estoque atual do produto e menor que a quantidade comprada.",
                        "error",
                    )
                    conn.rollback()
                    return redirect(url_for("compras"))

                cur.execute(
                    "UPDATE produtos SET estoque = estoque - %s WHERE id = %s AND empresa_id = %s;",
                    (qtd_compra, produto_id, empresa_id),
                )
                cur.execute(
                    "UPDATE compras SET status = 'Cancelada' WHERE id = %s AND empresa_id = %s;",
                    (compra_id, empresa_id),
                )
                cur.execute(
                    """
                    UPDATE lancamentos_financeiros
                    SET status = 'Cancelado'
                    WHERE empresa_id = %s AND origem = 'compra' AND referencia_id = %s AND status != 'Cancelado';
                    """,
                    (empresa_id, compra_id),
                )

            conn.commit()
            flash(f"Compra #{compra_id} cancelada e estoque estornado (-{qtd_compra} un.).", "success")
        except Exception as exc:
            conn.rollback()
            flash(f"Erro ao cancelar compra: {exc}", "error")

    return redirect(url_for("compras"))


@app.route("/financeiro", methods=["GET", "POST"])
def financeiro():
    with get_db_connection() as conn:
        garantir_tabelas_compras_financeiro(conn)
        empresa = get_empresa_ativa(conn, session.get("empresa_id"))
        empresa_id = _empresa_id_seguro(empresa)
        if not empresa_id:
            flash("Selecione ou cadastre uma empresa antes de acessar o Financeiro.", "warning")
            return redirect(url_for("empresas"))

        if request.method == "POST":
            acao = request.form.get("acao", "criar").strip()

            if acao == "baixar":
                lanc_id_raw = request.form.get("lancamento_id", "").strip()
                try:
                    lanc_id = int(lanc_id_raw)
                except ValueError:
                    lanc_id = None

                if lanc_id:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE lancamentos_financeiros
                            SET status = 'Pago', pago_em = COALESCE(pago_em, CURRENT_TIMESTAMP)
                            WHERE id = %s AND empresa_id = %s AND status = 'Pendente';
                            """,
                            (lanc_id, empresa_id),
                        )
                        afetados = cur.rowcount
                    conn.commit()
                    if afetados:
                        flash(f"Lancamento #{lanc_id} marcado como Pago.", "success")
                    else:
                        flash("Lancamento nao encontrado ou ja baixado/cancelado.", "warning")
                return redirect(url_for("financeiro"))

            tipo = request.form.get("tipo", "saida").strip()
            descricao = request.form.get("descricao", "").strip()
            forma_pagamento = request.form.get("forma_pagamento", "Dinheiro").strip() or "Dinheiro"
            status = request.form.get("status", "Pendente").strip()
            valor_raw = request.form.get("valor", "0").strip().replace(",", ".")
            vencimento_raw = request.form.get("vencimento", "").strip() or None

            if tipo not in ("entrada", "saida"):
                tipo = "saida"
            if status not in ("Pendente", "Pago"):
                status = "Pendente"

            try:
                valor = Decimal(valor_raw).quantize(Decimal("0.01"))
            except InvalidOperation:
                valor = None

            if not descricao or valor is None or valor <= Decimal("0.00"):
                flash("Informe descricao e valor maior que zero para o lancamento.", "error")
                return redirect(url_for("financeiro"))

            pago_em_sql = "CURRENT_TIMESTAMP" if status == "Pago" else "NULL"
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO lancamentos_financeiros (
                        empresa_id, tipo, origem, descricao, forma_pagamento,
                        valor, status, vencimento, pago_em
                    )
                    VALUES (%s, %s, 'manual', %s, %s, %s, %s, %s, {pago_em_sql});
                    """,
                    (
                        empresa_id,
                        tipo,
                        descricao,
                        forma_pagamento,
                        valor,
                        status,
                        vencimento_raw,
                    ),
                )
            conn.commit()
            flash("Lancamento financeiro registrado com sucesso.", "success")
            return redirect(url_for("financeiro"))

        resumo = {
            "total_avista": Decimal("0.00"),
            "qtd_vendas": 0,
            "ticket_medio": Decimal("0.00"),
            "a_receber": Decimal("0.00"),
            "a_pagar": Decimal("0.00"),
            "saldo_caixa": Decimal("0.00"),
        }
        por_pagamento = []
        historico = []
        lancamentos = []

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN tipo = 'entrada' AND status = 'Pago' THEN valor ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN tipo = 'saida' AND status = 'Pago' THEN valor ELSE 0 END), 0) AS saldo_caixa,
                    COALESCE(SUM(CASE WHEN tipo = 'entrada' AND status = 'Pendente' THEN valor ELSE 0 END), 0) AS a_receber,
                    COALESCE(SUM(CASE WHEN tipo = 'saida' AND status = 'Pendente' THEN valor ELSE 0 END), 0) AS a_pagar
                FROM lancamentos_financeiros
                WHERE empresa_id = %s AND status != 'Cancelado';
                """,
                (empresa_id,),
            )
            fin_row = cur.fetchone()
            if fin_row:
                resumo["saldo_caixa"] = Decimal(str(fin_row[0] or 0))
                resumo["a_receber"] = Decimal(str(fin_row[1] or 0))
                resumo["a_pagar"] = Decimal(str(fin_row[2] or 0))

            cur.execute(
                """
                SELECT
                    id, criado_em, tipo, origem, descricao,
                    forma_pagamento, valor, status, vencimento
                FROM lancamentos_financeiros
                WHERE empresa_id = %s
                ORDER BY id DESC;
                """,
                (empresa_id,),
            )
            lancamentos = cur.fetchall()

            cur.execute("SELECT to_regclass('public.vendas');")
            tem_vendas = cur.fetchone()[0] is not None
            if tem_vendas:
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'vendas';
                    """
                )
                cols_vendas = {r[0] for r in cur.fetchall()}
                if "empresa_id" in cols_vendas:
                    valor_col = "total" if "total" in cols_vendas else ("valor_total" if "valor_total" in cols_vendas else None)
                    pag_col = "forma_pagamento" if "forma_pagamento" in cols_vendas else None
                    data_col = "criado_em" if "criado_em" in cols_vendas else ("data" if "data" in cols_vendas else None)

                    if valor_col:
                        cur.execute(
                            f"""
                            SELECT COUNT(*), COALESCE(SUM({valor_col}), 0)
                            FROM vendas
                            WHERE empresa_id = %s;
                            """,
                            (empresa_id,),
                        )
                        v_res = cur.fetchone()
                        qtd = int(v_res[0] or 0)
                        tot = Decimal(str(v_res[1] or 0))
                        resumo["qtd_vendas"] = qtd
                        resumo["total_avista"] = tot
                        resumo["ticket_medio"] = (tot / Decimal(qtd)).quantize(Decimal("0.01")) if qtd > 0 else Decimal("0.00")

                        if pag_col:
                            cur.execute(
                                f"""
                                SELECT COALESCE({pag_col}, 'Nao informado'), COUNT(*), COALESCE(SUM({valor_col}), 0)
                                FROM vendas
                                WHERE empresa_id = %s
                                GROUP BY COALESCE({pag_col}, 'Nao informado')
                                ORDER BY 3 DESC;
                                """,
                                (empresa_id,),
                            )
                            por_pagamento = cur.fetchall()

                        order_expr = f"{data_col} DESC NULLS LAST, id DESC" if data_col else "id DESC"
                        dt_expr = data_col if data_col else "NULL"
                        pg_expr = pag_col if pag_col else "'Nao informado'"
                        cur.execute(
                            f"""
                            SELECT id, {dt_expr}, 'Consumidor final', '-', {pg_expr}, {valor_col}
                            FROM vendas
                            WHERE empresa_id = %s
                            ORDER BY {order_expr}
                            LIMIT 50;
                            """,
                            (empresa_id,),
                        )
                        historico = cur.fetchall()

    return render_template(
        "financeiro.html",
        active_page="financeiro",
        resumo=resumo,
        por_pagamento=por_pagamento,
        historico=historico,
        lancamentos=lancamentos,
    )


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(host=host, port=port, debug=debug)
