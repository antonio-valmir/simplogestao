import os
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for
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


@app.route("/")
def index():
    return redirect(url_for("clientes"))


@app.route("/clientes", methods=["GET", "POST"])
def clientes():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        telefone = request.form.get("telefone", "").strip() or None
        email = request.form.get("email", "").strip() or None

        if nome:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO clientes (nome, telefone, email)
                        VALUES (%s, %s, %s);
                        """,
                        (nome, telefone, email),
                    )
                conn.commit()

        return redirect(url_for("clientes"))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, nome, telefone, email
                FROM clientes
                ORDER BY id DESC;
                """
            )
            lista_clientes = cur.fetchall()

    return render_template("clientes.html", clientes=lista_clientes, active_page="clientes")


@app.route("/produtos")
def produtos():
    return render_template("base.html", active_page="produtos")


@app.route("/vendas")
def vendas():
    return render_template("base.html", active_page="vendas")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
