"""Student Expense Tracker - web version (Flask + SQLite).

Layers (same idea as the C version):
    templates/ + static/   presentation (what the browser shows)
    app.py                 routes = the API endpoints + business rules
    validators.py          input rules
    db.py + schema.sql     data access and storage
"""
import csv
import io
import os
import random
import secrets
import sqlite3
import time
from datetime import date
from functools import wraps

import click
from flask import Flask, Response, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

import db
from validators import (
    CATEGORIES, ValidationError, clean_amount, clean_category, clean_date, clean_month,
    clean_password, clean_search, clean_username, month_bounds, parse_expense, shift_month,
)

MAX_FAILED_LOGINS = 5        # wrong passwords allowed ...
LOCKOUT_SECONDS = 300        # ... within this many seconds

# Used to spend the same time on unknown usernames, so response time does not reveal who exists.
DUMMY_HASH = generate_password_hash("not-a-real-password")


class ApiError(Exception):
    """An error we want to send to the browser as JSON."""

    def __init__(self, message, status=400, field=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field


def load_secret_key(instance_path):
    """Use the SECRET_KEY environment variable if set (required when deploying).
    Otherwise create a random key once and keep it in instance/secret_key."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    os.makedirs(instance_path, exist_ok=True)
    path = os.path.join(instance_path, "secret_key")
    try:
        with open(path, "x") as f:
            key = secrets.token_hex(32)
            f.write(key)
        return key
    except FileExistsError:
        with open(path) as f:
            return f.read().strip() or secrets.token_hex(32)


def csv_safe(text):
    """Stop spreadsheet programs from running a note like '=1+1' as a formula."""
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.path.join(app.instance_path, "expense_tracker.db"),
        SESSION_COOKIE_HTTPONLY=True,          # JavaScript cannot read the session cookie
        SESSION_COOKIE_SAMESITE="Lax",         # not sent with cross-site POSTs
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",   # set to 1 when served over HTTPS
        MAX_CONTENT_LENGTH=16 * 1024,          # requests here are tiny; refuse big ones
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_secret_key(app.instance_path)
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)

    app.failed_logins = {}                     # (ip, username) -> [count, first_failure_time]
    db.init_app(app)

    # ------------------------------------------------------------ helpers

    def json_body():
        data = request.get_json(silent=True)   # None unless Content-Type is application/json
        if not isinstance(data, dict):
            raise ApiError("Send a JSON object with Content-Type: application/json.")
        return data

    def api_login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return jsonify(error="Please log in."), 401
            return view(*args, **kwargs)
        return wrapped

    def is_locked_out(key):
        entry = current_app.failed_logins.get(key)
        if entry is None:
            return False
        if time.time() - entry[1] > LOCKOUT_SECONDS:
            del current_app.failed_logins[key]
            return False
        return entry[0] >= MAX_FAILED_LOGINS

    def record_failure(key):
        table = current_app.failed_logins
        if len(table) > 10000:                 # keep memory bounded
            table.clear()
        entry = table.get(key)
        if entry is None or time.time() - entry[1] > LOCKOUT_SECONDS:
            table[key] = [1, time.time()]
        else:
            entry[0] += 1

    def expense_json(row):
        return {
            "id": row["id"],
            "date": row["date"],
            "category": row["category"],
            "amount": row["amount_paise"] / 100,
            "note": row["note"],
        }

    def date_filters():
        """Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD, as SQL conditions plus their values."""
        conditions, values = [], []
        if request.args.get("from"):
            conditions.append("date >= ?")
            values.append(clean_date(request.args["from"], "from"))
        if request.args.get("to"):
            conditions.append("date <= ?")
            values.append(clean_date(request.args["to"], "to"))
        return conditions, values

    def expense_filters():
        """Conditions shared by the list and the CSV export: ?category=&from=&to=&q="""
        conditions, values = ["user_id = ?"], [g.user["id"]]
        if request.args.get("category"):
            conditions.append("category = ?")
            values.append(clean_category(request.args["category"]))
        more_conditions, more_values = date_filters()
        conditions += more_conditions
        values += more_values
        if request.args.get("q"):
            text = clean_search(request.args["q"])
            # Make %, _ and \ match themselves instead of acting as wildcards.
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append("note LIKE ? ESCAPE '\\'")
            values.append("%" + escaped + "%")
        return conditions, values

    def select_expenses(conditions, values):
        # The WHERE text is built only from the fixed strings above; user input goes in as values.
        return db.get_db().execute(
            "SELECT * FROM expenses WHERE " + " AND ".join(conditions) +
            " ORDER BY date DESC, id DESC", values).fetchall()

    def budget_status(user_id, month):
        """How much was spent in `month` compared with that month's budget (if any)."""
        conn = db.get_db()
        first, last = month_bounds(month)
        spent = conn.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) FROM expenses "
            "WHERE user_id = ? AND date >= ? AND date <= ?", (user_id, first, last)).fetchone()[0]
        row = conn.execute("SELECT limit_paise FROM budgets WHERE user_id = ? AND month = ?",
                           (user_id, month)).fetchone()
        status = {"month": month, "spent": spent / 100, "limit": None,
                  "remaining": None, "percent": None, "exceeded": False}
        if row is not None:
            limit = row["limit_paise"]
            status.update(limit=limit / 100, remaining=(limit - spent) / 100,
                          percent=round(spent * 100 / limit, 1), exceeded=spent > limit)
        return status

    def expense_response(row, status_code):
        """The saved expense, plus a budget alert when that month is now over budget."""
        body = expense_json(row)
        status = budget_status(g.user["id"], row["date"][:7])
        if status["exceeded"]:
            body["budget_alert"] = status
        return jsonify(body), status_code

    # ------------------------------------------------------------ request hooks

    @app.before_request
    def load_user():
        g.user = None
        if request.endpoint == "static":
            return
        user_id = session.get("user_id")
        if user_id is not None:
            g.user = db.get_db().execute(
                "SELECT id, username, token_version FROM users WHERE id = ?", (user_id,)).fetchone()
            # Signed out if the account is gone or the password was changed on another device.
            if g.user is None or session.get("v", 0) != g.user["token_version"]:
                session.clear()
                g.user = None

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        # Only our own files may load; no inline scripts. This blocks most XSS attacks.
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"   # no stale pages after logout
        return response

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        return jsonify(error=error.message, field=error.field), error.status

    @app.errorhandler(ValidationError)
    def handle_validation_error(error):
        return jsonify(error=error.message, field=error.field), 400

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        if request.path.startswith("/api/"):
            return jsonify(error=error.name), error.code
        return error

    # ------------------------------------------------------------ pages

    @app.get("/")
    def index():
        return redirect(url_for("dashboard" if g.user else "login"))

    @app.get("/login")
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.get("/register")
    def register():
        if g.user:
            return redirect(url_for("dashboard"))
        return render_template("register.html")

    @app.get("/dashboard")
    def dashboard():
        if g.user is None:
            return redirect(url_for("login"))
        return render_template("dashboard.html", username=g.user["username"], categories=CATEGORIES)

    # ------------------------------------------------------------ API: accounts

    @app.post("/api/register")
    def api_register():
        data = json_body()
        username = clean_username(data.get("username"))
        password = clean_password(data.get("password"))
        conn = db.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)))
            conn.commit()
        except sqlite3.IntegrityError:
            raise ApiError("That username is already taken.", 409, "username")
        session.clear()
        session["user_id"] = cur.lastrowid
        session["v"] = 0
        return jsonify(id=cur.lastrowid, username=username), 201

    @app.post("/api/login")
    def api_login():
        data = json_body()
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ApiError("Enter your username and password.")
        username = username.strip()
        key = (request.remote_addr, username.lower())
        if is_locked_out(key):
            raise ApiError("Too many failed attempts. Please wait a few minutes and try again.", 429)

        row = db.get_db().execute(
            "SELECT id, username, password_hash, token_version FROM users WHERE username = ?",
            (username,)).fetchone()
        if row is None:
            check_password_hash(DUMMY_HASH, password)
            ok = False
        else:
            ok = check_password_hash(row["password_hash"], password)
        if not ok:
            record_failure(key)
            # Same message for "no such user" and "wrong password".
            raise ApiError("Invalid username or password.", 401)

        current_app.failed_logins.pop(key, None)
        session.clear()                        # fresh session on every login
        session["user_id"] = row["id"]
        session["v"] = row["token_version"]
        return jsonify(id=row["id"], username=row["username"])

    @app.post("/api/logout")
    def api_logout():
        session.clear()
        return "", 204

    @app.get("/api/me")
    @api_login_required
    def api_me():
        return jsonify(id=g.user["id"], username=g.user["username"])

    @app.post("/api/change-password")
    @api_login_required
    def api_change_password():
        data = json_body()
        current, new = data.get("current_password"), data.get("new_password")
        if not isinstance(current, str) or not current:
            raise ApiError("Enter your current password.", 400, "current_password")
        new = clean_password(new, "new_password")
        key = (request.remote_addr, g.user["username"].lower())
        if is_locked_out(key):
            raise ApiError("Too many failed attempts. Please wait a few minutes and try again.", 429)

        conn = db.get_db()
        stored = conn.execute("SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)).fetchone()
        if not check_password_hash(stored["password_hash"], current):
            record_failure(key)
            raise ApiError("Your current password is not correct.", 400, "current_password")
        if new == current:
            raise ApiError("Choose a password different from the current one.", 400, "new_password")

        current_app.failed_logins.pop(key, None)
        conn.execute("UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE id = ?",
                     (generate_password_hash(new), g.user["id"]))
        conn.commit()
        # Every other device is signed out (their token_version is now old); this one stays signed in.
        session["v"] = g.user["token_version"] + 1
        return jsonify(ok=True)

    # ------------------------------------------------------------ API: expenses (CRUD)
    # Every query includes "user_id = ?", so a user can only ever touch their own rows.

    @app.get("/api/expenses")
    @api_login_required
    def api_list_expenses():
        conditions, values = expense_filters()
        return jsonify([expense_json(r) for r in select_expenses(conditions, values)])

    @app.get("/api/expenses.csv")
    @api_login_required
    def api_export_csv():
        conditions, values = expense_filters()
        out = io.StringIO()
        out.write("\ufeff")                    # lets Excel read non-English notes correctly
        writer = csv.writer(out)
        writer.writerow(["Date", "Category", "Amount (Rs)", "Note"])
        for row in select_expenses(conditions, values):
            writer.writerow([row["date"], row["category"], "%d.%02d" % divmod(row["amount_paise"], 100),
                             csv_safe(row["note"])])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="expenses.csv"'})

    @app.post("/api/expenses")
    @api_login_required
    def api_create_expense():
        date_, category, paise, note = parse_expense(json_body())
        conn = db.get_db()
        cur = conn.execute(
            "INSERT INTO expenses (user_id, date, category, amount_paise, note) VALUES (?, ?, ?, ?, ?)",
            (g.user["id"], date_, category, paise, note))
        conn.commit()
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (cur.lastrowid,)).fetchone()
        return expense_response(row, 201)

    @app.put("/api/expenses/<int:expense_id>")
    @api_login_required
    def api_update_expense(expense_id):
        date_, category, paise, note = parse_expense(json_body())
        conn = db.get_db()
        cur = conn.execute(
            "UPDATE expenses SET date = ?, category = ?, amount_paise = ?, note = ? "
            "WHERE id = ? AND user_id = ?",
            (date_, category, paise, note, expense_id, g.user["id"]))
        conn.commit()
        if cur.rowcount == 0:
            raise ApiError("Expense not found.", 404)   # 404, not 403: do not reveal other users' ids
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        return expense_response(row, 200)

    @app.delete("/api/expenses/<int:expense_id>")
    @api_login_required
    def api_delete_expense(expense_id):
        conn = db.get_db()
        cur = conn.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?",
                           (expense_id, g.user["id"]))
        conn.commit()
        if cur.rowcount == 0:
            raise ApiError("Expense not found.", 404)
        return "", 204

    # ------------------------------------------------------------ API: summaries

    @app.get("/api/summary")
    @api_login_required
    def api_summary():
        conditions, values = ["user_id = ?"], [g.user["id"]]
        extra_conditions, extra_values = date_filters()
        conditions += extra_conditions
        values += extra_values
        rows = db.get_db().execute(
            "SELECT category, SUM(amount_paise) AS paise, COUNT(*) AS n FROM expenses WHERE " +
            " AND ".join(conditions) + " GROUP BY category", values).fetchall()
        found = {r["category"]: (r["paise"], r["n"]) for r in rows}
        by_category = []
        for name in CATEGORIES:                # every category, including zeros, in a fixed order
            paise, n = found.get(name, (0, 0))
            by_category.append({"category": name, "total": paise / 100, "count": n})
        total_paise = sum(p for p, _ in found.values())
        return jsonify(total=total_paise / 100, count=sum(n for _, n in found.values()),
                       by_category=by_category)

    @app.get("/api/summary/monthly")
    @api_login_required
    def api_monthly_summary():
        """Spending per month for the last N months ending at ?to=YYYY-MM (default: this month)."""
        to_month = clean_month(request.args.get("to") or date.today().strftime("%Y-%m"), "to")
        try:
            count = int(request.args.get("months", 6))
        except ValueError:
            count = 0
        if not 1 <= count <= 12:
            raise ValidationError("Months must be a whole number from 1 to 12.", "months")
        months = [shift_month(to_month, -i) for i in range(count - 1, -1, -1)]
        first, last = month_bounds(months[0])[0], month_bounds(months[-1])[1]
        rows = db.get_db().execute(
            "SELECT substr(date, 1, 7) AS m, SUM(amount_paise) AS paise, COUNT(*) AS n FROM expenses "
            "WHERE user_id = ? AND date >= ? AND date <= ? GROUP BY m",
            (g.user["id"], first, last)).fetchall()
        found = {r["m"]: (r["paise"], r["n"]) for r in rows}
        return jsonify(months=[
            {"month": m, "total": found.get(m, (0, 0))[0] / 100, "count": found.get(m, (0, 0))[1]}
            for m in months])

    # ------------------------------------------------------------ API: monthly budget

    @app.get("/api/budget/<month>")
    @api_login_required
    def api_get_budget(month):
        return jsonify(budget_status(g.user["id"], clean_month(month)))

    @app.put("/api/budget/<month>")
    @api_login_required
    def api_set_budget(month):
        month = clean_month(month)
        paise = clean_amount(json_body().get("limit"), "limit", "Budget")
        conn = db.get_db()
        conn.execute(
            "INSERT INTO budgets (user_id, month, limit_paise) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, month) DO UPDATE SET limit_paise = excluded.limit_paise",
            (g.user["id"], month, paise))
        conn.commit()
        return jsonify(budget_status(g.user["id"], month))

    @app.delete("/api/budget/<month>")
    @api_login_required
    def api_delete_budget(month):
        month = clean_month(month)
        conn = db.get_db()
        cur = conn.execute("DELETE FROM budgets WHERE user_id = ? AND month = ?", (g.user["id"], month))
        conn.commit()
        if cur.rowcount == 0:
            raise ApiError("No budget is set for that month.", 404)
        return "", 204

    # ------------------------------------------------------------ command line: demo data

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create a demo account (demo / demo12345) with sample expenses and a budget."""
        conn = db.get_db()
        if conn.execute("SELECT 1 FROM users WHERE username = 'demo'").fetchone():
            click.echo("The demo user already exists. Log in with  demo / demo12345")
            return
        user_id = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("demo", generate_password_hash("demo12345"))).lastrowid
        samples = {
            "Food": (["Lunch at canteen", "Tea and samosa", "Dinner with friends", "Breakfast"], 30, 220),
            "Transport": (["Bus fare", "Auto to college", "Metro recharge"], 20, 120),
            "Books": (["Textbook", "Notebook and pens", "Lab manual"], 80, 650),
            "Recharge": (["Mobile recharge", "Wi-Fi top-up"], 149, 399),
            "Entertainment": (["Movie with friends", "Cricket match tickets", "Game subscription"], 100, 450),
            "Other": (["Printouts", "Haircut", "Gift"], 40, 300),
        }
        weights = ["Food"] * 5 + ["Transport"] * 3 + ["Books"] + ["Recharge"] + ["Entertainment"] * 2 + ["Other"] * 2
        rng = random.Random(7)                  # same demo data every time
        today = date.today()
        this_month = today.strftime("%Y-%m")
        rows = []
        for back in (2, 1, 0):
            month = shift_month(this_month, -back)
            last_day = today.day if back == 0 else int(month_bounds(month)[1][-2:])
            for _ in range(14 if back else max(6, min(14, last_day))):
                category = rng.choice(weights)
                notes, low, high = samples[category]
                amount = rng.randrange(low, high + 1)
                rows.append((user_id, "%s-%02d" % (month, rng.randint(1, last_day)), category,
                             amount * 100 + rng.choice([0, 0, 0, 50]), rng.choice(notes)))
        conn.executemany("INSERT INTO expenses (user_id, date, category, amount_paise, note) "
                         "VALUES (?, ?, ?, ?, ?)", rows)
        conn.execute("INSERT INTO budgets (user_id, month, limit_paise) VALUES (?, ?, ?)",
                     (user_id, this_month, 450000))
        conn.commit()
        click.echo("Created the demo user with %d expenses. Log in with  demo / demo12345" % len(rows))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
