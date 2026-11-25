"""Tests for the later features: budgets, monthly trend, search, CSV export,
change password, upgrading an old database, and the demo-data command.

Run everything with:  python -m unittest discover -s tests -v"""
import csv
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

from app import create_app
from test_app import AppTestCase


class BudgetTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def status(self, month="2026-10", client=None):
        return (client or self.client).get("/api/budget/" + month).get_json()

    def set_budget(self, limit, month="2026-10", client=None):
        return (client or self.client).put("/api/budget/" + month, json={"limit": limit})

    def test_no_budget_set(self):
        self.add(amount="100")
        self.assertEqual(self.status(), {"month": "2026-10", "spent": 100.0, "limit": None,
                                         "remaining": None, "percent": None, "exceeded": False})

    def test_set_and_read(self):
        r = self.set_budget("1000")
        self.assertEqual(r.status_code, 200)
        self.add(date="2026-10-05", amount="120")
        self.add(date="2026-10-06", amount="40")
        self.assertEqual(self.status(), {"month": "2026-10", "spent": 160.0, "limit": 1000.0,
                                         "remaining": 840.0, "percent": 16.0, "exceeded": False})

    def test_exactly_on_budget_is_not_exceeded_but_one_paisa_over_is(self):
        self.set_budget("1000")
        self.add(date="2026-10-05", amount="1000")
        s = self.status()
        self.assertEqual((s["remaining"], s["percent"], s["exceeded"]), (0.0, 100.0, False))
        self.add(date="2026-10-06", amount="0.01")
        self.assertTrue(self.status()["exceeded"])

    def test_setting_again_replaces_the_limit(self):
        self.set_budget("1000")
        self.set_budget("2500.50")
        self.assertEqual(self.status()["limit"], 2500.5)
        count = sqlite3.connect(self.db_path).execute("SELECT COUNT(*) FROM budgets").fetchone()[0]
        self.assertEqual(count, 1)

    def test_delete(self):
        self.set_budget("1000")
        self.assertEqual(self.client.delete("/api/budget/2026-10").status_code, 204)
        self.assertIsNone(self.status()["limit"])
        self.assertEqual(self.client.delete("/api/budget/2026-10").status_code, 404)

    def test_bad_months_rejected(self):
        for bad in ["2026-13", "2026-1", "abc", "2026-00", "1999-12", "2101-01", "2026-10-01"]:
            self.assertEqual(self.client.get("/api/budget/" + bad).status_code, 400, bad)
            self.assertEqual(self.set_budget("100", month=bad).status_code, 400, bad)

    def test_bad_limits_rejected(self):
        for bad in [0, -1, "x", None, 1000001, "1.234", True, ""]:
            r = self.set_budget(bad)
            self.assertEqual(r.status_code, 400, repr(bad))
            self.assertEqual(r.get_json()["field"], "limit")
            self.assertIn("Budget", r.get_json()["error"])

    def test_only_that_month_counts(self):
        self.set_budget("1000")
        self.add(date="2026-09-30", amount="500")
        self.add(date="2026-10-01", amount="100")
        self.add(date="2026-10-31", amount="200")
        self.add(date="2026-11-01", amount="999")
        self.assertEqual(self.status()["spent"], 300.0)

    def test_leap_february_has_29_days(self):
        self.add(date="2028-02-29", amount="10")
        self.add(date="2028-03-01", amount="5")
        self.assertEqual(self.status("2028-02")["spent"], 10.0)

    def test_alert_when_an_expense_pushes_the_month_over(self):
        self.set_budget("100")
        ok = self.add(date="2026-10-05", amount="60")
        self.assertNotIn("budget_alert", ok.get_json())
        over = self.add(date="2026-10-06", amount="50")
        alert = over.get_json()["budget_alert"]
        self.assertEqual((alert["month"], alert["limit"], alert["spent"], alert["exceeded"]),
                         ("2026-10", 100.0, 110.0, True))

    def test_alert_is_for_the_expenses_own_month(self):
        self.set_budget("100", month="2026-10")
        r = self.add(date="2026-11-05", amount="5000")            # November has no budget
        self.assertNotIn("budget_alert", r.get_json())

    def test_alert_on_edit(self):
        self.set_budget("100")
        self.add(date="2026-10-05", amount="60")
        self.add(date="2026-10-06", amount="30")
        r = self.client.put("/api/expenses/2", json={"date": "2026-10-06", "category": "Food",
                                                     "amount": "80", "note": ""})
        self.assertTrue(r.get_json()["budget_alert"]["exceeded"])

    def test_deleting_expenses_brings_spending_back_under(self):
        self.set_budget("100")
        self.add(amount="150")
        self.assertTrue(self.status()["exceeded"])
        self.client.delete("/api/expenses/1")
        self.assertFalse(self.status()["exceeded"])

    def test_budgets_are_private(self):
        self.set_budget("1000")
        bob = self.other_client()
        self.assertIsNone(self.status(client=bob)["limit"])
        self.set_budget("50", client=bob)
        self.assertEqual(self.status()["limit"], 1000.0)
        self.assertEqual(self.status(client=bob)["limit"], 50.0)

    def test_login_required(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/budget/2026-10").status_code, 401)
        self.assertEqual(anon.put("/api/budget/2026-10", json={"limit": 5}).status_code, 401)
        self.assertEqual(anon.delete("/api/budget/2026-10").status_code, 401)


class MonthlyTrendTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def months(self, query=""):
        r = self.client.get("/api/summary/monthly" + query)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["months"]

    def test_zero_filled_and_in_order(self):
        self.add(date="2026-08-10", amount="100")
        self.add(date="2026-08-20", amount="50")
        self.add(date="2026-10-01", amount="30")
        result = self.months("?to=2026-10&months=4")
        self.assertEqual(result, [
            {"month": "2026-07", "total": 0, "count": 0},
            {"month": "2026-08", "total": 150.0, "count": 2},
            {"month": "2026-09", "total": 0, "count": 0},
            {"month": "2026-10", "total": 30.0, "count": 1},
        ])

    def test_default_is_six_months(self):
        self.assertEqual(len(self.months("?to=2026-10")), 6)

    def test_crosses_a_year_boundary(self):
        self.add(date="2025-12-31", amount="7")
        names = [m["month"] for m in self.months("?to=2026-02&months=4")]
        self.assertEqual(names, ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_ignores_expenses_outside_the_window(self):
        self.add(date="2026-03-15", amount="999")
        self.assertEqual(sum(m["total"] for m in self.months("?to=2026-10&months=3")), 0)

    def test_bad_parameters(self):
        for query in ["?months=0", "?months=13", "?months=abc", "?months=2.5", "?to=2026-13", "?to=bad"]:
            self.assertEqual(self.client.get("/api/summary/monthly" + query).status_code, 400, query)

    def test_default_month_is_this_month(self):
        self.assertEqual(self.months()[-1]["month"], date.today().strftime("%Y-%m"))

    def test_private_to_each_user(self):
        self.add(date="2026-10-01", amount="30")
        bob = self.other_client()
        r = bob.get("/api/summary/monthly?to=2026-10&months=1").get_json()["months"]
        self.assertEqual(r, [{"month": "2026-10", "total": 0, "count": 0}])

    def test_login_required(self):
        self.assertEqual(self.app.test_client().get("/api/summary/monthly").status_code, 401)


class SearchTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()
        for note in ["Lunch at canteen", "100% done", "a_b", "axb", "Back\\slash", "Chai", ""]:
            self.add(note=note, amount="10")

    def notes(self, query):
        r = self.client.get("/api/expenses" + query)
        self.assertEqual(r.status_code, 200)
        return sorted(e["note"] for e in r.get_json())

    def test_substring_and_case_insensitive(self):
        self.assertEqual(self.notes("?q=LUNCH"), ["Lunch at canteen"])
        self.assertEqual(self.notes("?q=can"), ["Lunch at canteen"])

    def test_percent_and_underscore_are_literal(self):
        self.assertEqual(self.notes("?q=%25"), ["100% done"])
        self.assertEqual(self.notes("?q=_"), ["a_b"])

    def test_backslash_is_literal(self):
        self.assertEqual(self.notes("?q=%5C"), ["Back\\slash"])

    def test_empty_search_means_no_filter(self):
        self.assertEqual(len(self.notes("?q=")), 7)

    def test_combines_with_category(self):
        self.add(note="Lunch bus", category="Transport", amount="5")
        self.assertEqual(self.notes("?q=lunch&category=Transport"), ["Lunch bus"])

    def test_too_long_is_rejected(self):
        self.assertEqual(self.client.get("/api/expenses?q=" + "x" * 51).status_code, 400)

    def test_injection_text_is_just_text(self):
        self.assertEqual(self.notes("?q=%27%20OR%20%271%27%3D%271"), [])


class CsvExportTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.add(date="2026-10-05", category="Food", amount="120", note="Lunch at canteen")
        self.add(date="2026-10-06", category="Transport", amount="40.5", note="Auto, to \"college\"")
        self.add(date="2026-11-01", category="Food", amount="75.50", note="=1+1")

    def export(self, query="", client=None):
        r = (client or self.client).get("/api/expenses.csv" + query)
        self.assertEqual(r.status_code, 200)
        text = r.get_data(as_text=True)
        return r, list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))

    def test_headers_and_header_row(self):
        r, rows = self.export()
        self.assertTrue(r.content_type.startswith("text/csv"))
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertTrue(r.get_data(as_text=True).startswith("\ufeff"))
        self.assertEqual(rows[0], ["Date", "Category", "Amount (Rs)", "Note"])

    def test_rows_newest_first_with_two_decimals(self):
        _, rows = self.export()
        self.assertEqual([r[0] for r in rows[1:]], ["2026-11-01", "2026-10-06", "2026-10-05"])
        self.assertEqual([r[2] for r in rows[1:]], ["75.50", "40.50", "120.00"])

    def test_commas_and_quotes_survive(self):
        _, rows = self.export()
        self.assertIn('Auto, to "college"', [r[3] for r in rows])

    def test_spreadsheet_formulas_are_neutralised(self):
        self.add(note="@SUM(A1)", amount="1")
        self.add(note="+1", amount="1")
        self.add(note="-2+3", amount="1")
        _, rows = self.export()
        notes = [r[3] for r in rows[1:]]
        self.assertIn("'=1+1", notes)
        self.assertIn("'@SUM(A1)", notes)
        self.assertIn("'+1", notes)
        self.assertIn("'-2+3", notes)
        self.assertFalse([n for n in notes if n[:1] in "=+-@"])

    def test_filters_apply(self):
        _, rows = self.export("?category=Food&to=2026-10-31")
        self.assertEqual([r[3] for r in rows[1:]], ["Lunch at canteen"])
        _, rows = self.export("?q=auto")
        self.assertEqual(len(rows), 2)

    def test_only_your_own_data(self):
        bob = self.other_client()
        _, rows = self.export(client=bob)
        self.assertEqual(rows, [["Date", "Category", "Amount (Rs)", "Note"]])

    def test_login_required(self):
        self.assertEqual(self.app.test_client().get("/api/expenses.csv").status_code, 401)

    def test_bad_filter_rejected(self):
        self.assertEqual(self.client.get("/api/expenses.csv?from=nope").status_code, 400)


class ChangePasswordTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def change(self, current="secret123", new="newsecret456", client=None):
        return (client or self.client).post("/api/change-password",
                                            json={"current_password": current, "new_password": new})

    def test_success_and_new_password_works(self):
        self.assertEqual(self.change().status_code, 200)
        self.client.post("/api/logout")
        self.assertEqual(self.login(password="secret123").status_code, 401)
        self.assertEqual(self.login(password="newsecret456").status_code, 200)

    def test_this_device_stays_signed_in(self):
        self.change()
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        self.assertEqual(self.add().status_code, 201)

    def test_other_devices_are_signed_out(self):
        phone = self.app.test_client()
        self.assertEqual(self.login(client=phone).status_code, 200)
        self.assertEqual(phone.get("/api/me").status_code, 200)
        self.change()
        self.assertEqual(phone.get("/api/me").status_code, 401)
        self.assertEqual(self.login(client=phone, password="newsecret456").status_code, 200)

    def test_other_users_are_not_affected(self):
        bob = self.other_client()
        self.change()
        self.assertEqual(bob.get("/api/me").status_code, 200)

    def test_wrong_current_password(self):
        r = self.change(current="wrongwrong1")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["field"], "current_password")
        self.client.post("/api/logout")
        self.assertEqual(self.login().status_code, 200)             # password was not changed

    def test_weak_or_same_new_password(self):
        for bad in ["short", "x" * 65, None, 12345678]:
            r = self.change(new=bad)
            self.assertEqual(r.status_code, 400, repr(bad))
            self.assertEqual(r.get_json()["field"], "new_password")
        r = self.change(new="secret123")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["field"], "new_password")

    def test_missing_current_password(self):
        r = self.client.post("/api/change-password", json={"new_password": "newsecret456"})
        self.assertEqual(r.status_code, 400)

    def test_guessing_the_current_password_is_limited(self):
        for _ in range(5):
            self.assertEqual(self.change(current="wrongwrong1").status_code, 400)
        self.assertEqual(self.change(current="wrongwrong1").status_code, 429)
        self.assertEqual(self.change().status_code, 429)             # even the right one is refused for now

    def test_login_required_and_json_required(self):
        self.assertEqual(self.app.test_client().post("/api/change-password", json={}).status_code, 401)
        r = self.client.post("/api/change-password", data={"current_password": "secret123",
                                                           "new_password": "newsecret456"})
        self.assertEqual(r.status_code, 400)

    def test_old_sessions_without_a_version_still_work(self):
        with self.client.session_transaction() as s:
            s.pop("v", None)
        self.assertEqual(self.client.get("/api/me").status_code, 200)


class UpgradeOldDatabaseTests(unittest.TestCase):
    """A database made by the first version of the app must keep working."""

    def test_old_database_is_upgraded_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                                    password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                                       date TEXT NOT NULL, category TEXT NOT NULL, amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
                                       note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         ("olduser", generate_password_hash("oldpass123")))
            conn.execute("INSERT INTO expenses (user_id, date, category, amount_paise, note) VALUES (1, '2026-10-05', 'Food', 12000, 'Old lunch')")
            conn.commit()
            conn.close()

            app = create_app({"TESTING": True, "SECRET_KEY": "k", "DATABASE": path})
            client = app.test_client()
            r = client.post("/api/login", json={"username": "olduser", "password": "oldpass123"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(client.get("/api/expenses").get_json()[0]["note"], "Old lunch")
            self.assertEqual(client.put("/api/budget/2026-10", json={"limit": 500}).status_code, 200)
            self.assertEqual(client.post("/api/change-password",
                                         json={"current_password": "oldpass123", "new_password": "brandnew123"}).status_code, 200)


class SeedDemoTests(AppTestCase):
    def run_seed(self):
        return self.app.test_cli_runner().invoke(args=["seed-demo"])

    def test_creates_a_usable_demo_account(self):
        result = self.run_seed()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("demo12345", result.output)
        self.assertEqual(self.login(username="demo", password="demo12345").status_code, 200)

        expenses = self.client.get("/api/expenses").get_json()
        self.assertGreaterEqual(len(expenses), 30)
        today = date.today().isoformat()
        self.assertTrue(all(e["date"] <= today for e in expenses), "no future-dated demo expenses")
        self.assertTrue(all(e["amount"] > 0 for e in expenses))
        self.assertEqual(len({e["category"] for e in expenses}) >= 4, True)

        this_month = date.today().strftime("%Y-%m")
        self.assertEqual(self.client.get("/api/budget/" + this_month).get_json()["limit"], 4500.0)
        trend = self.client.get("/api/summary/monthly?months=3").get_json()["months"]
        self.assertTrue(all(m["count"] > 0 for m in trend))

    def test_running_twice_does_nothing_new(self):
        self.run_seed()
        before = sqlite3.connect(self.db_path).execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        again = self.run_seed()
        self.assertEqual(again.exit_code, 0)
        self.assertIn("already exists", again.output)
        after = sqlite3.connect(self.db_path).execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
