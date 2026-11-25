# Student Expense Tracker

A multi-user web app for students to record, review and control their spending.
Built with **Python, Flask and SQLite**, with HTML, CSS and plain JavaScript on the front end.
It grew out of a C command-line prototype and keeps the same design: layers, validation rules and categories.

![Dashboard](screenshots/03_dashboard.png)

## What it does

- **Accounts:** register, log in, log out, change password (other devices are signed out)
- **Expenses:** add, edit, delete; every user sees only their own
- **Monthly budget:** set a limit per month, see a progress bar, get a warning the moment an expense pushes the month over it
- **Spending views:** total, per-category bars, and a 6-month trend chart
- **Find things:** filter by date range and category, search notes
- **Export:** download your expenses as a CSV file (opens in Excel)
- **Phone friendly, light and dark mode**
- **Safe by design:** hashed passwords, input validation on the server, protection against SQL injection, script injection and forged requests (details below)

| Budget and trend | Phone | Dark mode |
|---|---|---|
| ![Budget](screenshots/04_budget_and_trend.png) | ![Phone](screenshots/08_dashboard_mobile.png) | ![Dark](screenshots/09_dashboard_dark.png) |

## Quick start

You need Python 3.9 or newer.

**Easiest:** double-click `run.bat` (Windows) or run `bash run.sh` (macOS / Linux). The first run installs Flask
(internet needed once), creates a demo account, and starts the app at http://127.0.0.1:5000.

**Or by hand:**

```
python -m venv venv
venv\Scripts\activate            (Windows)       source venv/bin/activate   (macOS / Linux)
pip install -r requirements.txt
flask --app app seed-demo        (optional: creates the demo account below)
flask --app app run --debug
```

**Demo account** (after `seed-demo`): username `demo`, password `demo12345`. It holds about 40 sample
expenses over three months and a budget for this month, so every screen has something to show.

## Two-minute demo script

1. Log in as `demo`. Point out the total, category bars, budget bar and 6-month trend.
2. Add an expense dated today. Watch the totals, budget and trend update without reloading.
3. Lower the budget (for example to 1500) and add another expense: the red over-budget warning appears.
4. Search for `lunch`, filter by a category, then click **Export CSV** and open the file.
5. Edit and delete an expense. Open **Change password**.
6. Register a second user: they see an empty dashboard and cannot touch the first user's data.

## How it is organised

| File | Job | In the C version |
|---|---|---|
| `templates/`, `static/` | What the browser shows: pages, styling, JavaScript | `ui.c` |
| `app.py` | Routes (the API endpoints), login, sessions, rules | `auth.c`, `expense.c` |
| `validators.py` | Checks every input | `validate.c` |
| `db.py`, `schema.sql` | The only code that touches the database | `storage.c` |
| `tests/` | Automated tests | `test.sh` |

The browser never touches the database. It calls the JSON API, the API checks the input,
and only then does the data layer run SQL.

## API reference

All requests and responses are JSON (except the CSV export). Errors look like
`{"error": "message", "field": "amount"}`. Everything except register and login needs a logged-in session (`401` otherwise).

| Method and path | What it does | Success |
|---|---|---|
| `POST /api/register` | `{username, password}` creates the account and logs in | 201 |
| `POST /api/login` | `{username, password}` | 200 |
| `POST /api/logout` | ends the session | 204 |
| `GET /api/me` | who is logged in | 200 |
| `POST /api/change-password` | `{current_password, new_password}`; signs out other devices | 200 |
| `GET /api/expenses?category=&from=&to=&q=` | your expenses, newest first; all filters optional | 200 |
| `GET /api/expenses.csv?...` | the same list as a CSV download | 200 |
| `POST /api/expenses` | `{date, category, amount, note}` | 201 |
| `PUT /api/expenses/<id>` | replace an expense's fields | 200 |
| `DELETE /api/expenses/<id>` | remove an expense | 204 |
| `GET /api/summary?from=&to=` | `{total, count, by_category[]}` | 200 |
| `GET /api/summary/monthly?to=YYYY-MM&months=6` | spending per month, zero-filled | 200 |
| `GET /api/budget/<YYYY-MM>` | `{spent, limit, remaining, percent, exceeded}` | 200 |
| `PUT /api/budget/<YYYY-MM>` | `{limit}` sets or replaces that month's budget | 200 |
| `DELETE /api/budget/<YYYY-MM>` | removes that month's budget | 204 |

Creating or editing an expense whose month is over budget adds a `budget_alert` object to the response.

```
POST /api/expenses
{"date": "2026-10-05", "category": "Food", "amount": "120", "note": "Lunch at canteen"}

201 {"id": 1, "date": "2026-10-05", "category": "Food", "amount": 120.0, "note": "Lunch at canteen"}
```

Other status codes: `400` invalid input, `404` not found (also used for other users' expenses, so their ids
are not revealed), `409` username taken, `413` request too large, `429` too many failed logins.

## Database

```
users     (id, username UNIQUE NOCASE, password_hash, token_version, created_at)
expenses  (id, user_id -> users.id, date YYYY-MM-DD, category, amount_paise, note, created_at)
budgets   (user_id -> users.id, month YYYY-MM, limit_paise)   primary key (user_id, month)
```

`amount_paise` and `limit_paise` are integers: Rs 120.50 is stored as `12050`, so totals are always exact.
The database file lives in `instance/expense_tracker.db` and is created automatically. An older database from
an earlier version of this app is upgraded in place the first time the new version starts.

## Security

- **Passwords** are stored only as salted hashes. Login gives the same message for "no such user" and
  "wrong password". Changing the password signs out every other device.
- **Brute force:** 5 wrong passwords for one username from one address locks logins for 5 minutes. The same
  counter protects the change-password form. It lives in memory, so it resets when the server restarts.
- **SQL injection:** every query uses `?` placeholders; user input is never pasted into SQL.
- **Script injection (XSS):** user text is shown with `textContent`, never `innerHTML`, and a
  Content-Security-Policy header blocks inline scripts.
- **Forged requests (CSRF):** the API only accepts JSON, which another website cannot send with your cookies;
  the session cookie is `HttpOnly` and `SameSite=Lax`.
- **Data isolation:** every query includes the logged-in user's id.
- **CSV safety:** a note starting with `=`, `+`, `-` or `@` is prefixed so Excel cannot run it as a formula.
- **Secret key:** set the `SECRET_KEY` environment variable when deploying. Locally a random key is created
  once in `instance/secret_key`.

## Tests

`python -m unittest discover -s tests -v` runs 108 tests, each with a fresh temporary database:
registration and hashing, login and lockout, expense CRUD and validation, filters and search (including
`%` and `_` typed as literal characters), summaries, budgets (month boundaries, exactly-on-limit, alerts),
the monthly trend (year rollover), CSV export (quoting, formula protection), change password (other devices
signed out), user isolation, access control, security headers, upgrading an old database, and the demo-data command.

To make sure the tests really catch mistakes, four deliberate bugs were put into the code one at a time
(formula protection removed, other-device sign-out removed, search escaping removed, budget limit off by one).
Each was caught by a failing test.

The finished app was also driven through a real browser (Chromium; desktop and phone sizes, light and dark mode):
register, add, budgets and warnings, trend chart, search, filters, CSV download, edit, delete, change password with a
second device signed out, a script-injection attempt, and no JavaScript or security-policy errors.

## Put it online (for your portfolio link)

**PythonAnywhere (recommended).** Its free plan runs Flask apps and keeps your files between restarts, which SQLite
needs. Free web apps expire after one month unless you extend them from the dashboard. Check their current
free-plan details before you start.

1. Create a free account and open a Bash console.
2. Get your code there: `git clone <your GitHub repo URL>`.
3. Create a virtualenv and run `pip install -r requirements.txt`.
4. In the Web tab, add a new web app (manual configuration, same Python version as your virtualenv)
   and set the virtualenv path.
5. Edit the WSGI file to contain:

```python
import os, sys
sys.path.insert(0, "/home/YOURNAME/YOUR-REPO-FOLDER")
os.environ["SECRET_KEY"] = "paste-a-long-random-string-here"
os.environ["COOKIE_SECURE"] = "1"
from app import create_app
application = create_app()
```

6. Click Reload and open `https://YOURNAME.pythonanywhere.com`.
7. Optional: in a Bash console run `flask --app app seed-demo` inside the project folder for a ready-made demo account.

Generate a secret key with: `python -c "import secrets; print(secrets.token_hex(32))"`

**Render and similar hosts.** The included `Procfile` (`gunicorn "app:create_app()"`) works there, but Render's free
web services lose local files, including SQLite databases, every time they restart or redeploy, so all accounts
and expenses would disappear. To keep data on such hosts you need a paid disk or a hosted PostgreSQL database
(which means changing `db.py`).

Never upload `instance/` to GitHub; `.gitignore` already excludes it.

## Known limitations

- No password reset by email: a user who forgets their password cannot recover the account.
- The login lockout is per server process (fine for one worker, not shared across many).
- Search is case-insensitive for English letters only (an SQLite limitation).
- Single currency (Rs), fixed list of six categories.
- Not built: charts beyond the 6-month trend, recurring expenses, receipt photos, importing the C version's CSV files.
