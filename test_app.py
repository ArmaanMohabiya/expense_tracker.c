"""Automated tests. Run from the project folder with:

    python -m unittest discover -s tests -v

Each test gets a brand-new temporary database, so tests never affect each other
and your real data is never touched."""
import os
import sqlite3
import tempfile
import unittest

from app import create_app
from validators import ValidationError, clean_amount, clean_date, clean_note


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-key", "DATABASE": self.db_path})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def register(self, client=None, username="alice", password="secret123"):
        return (client or self.client).post("/api/register", json={"username": username, "password": password})

    def login(self, client=None, username="alice", password="secret123"):
        return (client or self.client).post("/api/login", json={"username": username, "password": password})

    def add(self, client=None, **overrides):
        body = {"date": "2026-10-05", "category": "Food", "amount": "120", "note": "Lunch"}
        body.update(overrides)
        return (client or self.client).post("/api/expenses", json=body)

    def other_client(self, username="bob"):
        client = self.app.test_client()
        self.register(client, username=username, password="bobpass123")
        return client


class RegistrationTests(AppTestCase):
    def test_register_logs_the_user_in(self):
        r = self.register()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["username"], "alice")
        self.assertEqual(self.client.get("/api/me").get_json()["username"], "alice")

    def test_bad_usernames_rejected(self):
        for bad in ["", "ab", "has space", "x" * 21, "bad!name", "emoji\U0001F600x", None, 123, ["a"]]:
            r = self.register(username=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(r.get_json()["field"], "username")

    def test_bad_passwords_rejected(self):
        for bad in ["", "short12", "x" * 65, None, 12345678]:
            r = self.register(password=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(r.get_json()["field"], "password")

    def test_duplicate_username_is_case_insensitive(self):
        self.assertEqual(self.register(username="Alice").status_code, 201)
        r = self.register(self.app.test_client(), username="alice")
        self.assertEqual(r.status_code, 409)

    def test_password_is_stored_hashed(self):
        self.register()
        row = sqlite3.connect(self.db_path).execute("SELECT password_hash FROM users").fetchone()
        self.assertNotIn("secret123", row[0])
        self.assertTrue(row[0].startswith(("scrypt:", "pbkdf2:")))

    def test_same_password_gets_different_hashes(self):
        self.register(username="alice")
        self.register(self.app.test_client(), username="carol")
        hashes = [r[0] for r in sqlite3.connect(self.db_path).execute("SELECT password_hash FROM users")]
        self.assertEqual(len(set(hashes)), 2)


class LoginTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.client.post("/api/logout")

    def test_login_success(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 200)

    def test_username_login_ignores_case(self):
        self.assertEqual(self.login(username="ALICE").status_code, 200)

    def test_wrong_password_and_unknown_user_look_identical(self):
        wrong = self.login(password="wrongpass1")
        unknown = self.login(username="nobody")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.get_json(), unknown.get_json())

    def test_lockout_after_five_failures(self):
        for _ in range(5):
            self.assertEqual(self.login(password="wrongpass1").status_code, 401)
        self.assertEqual(self.login(password="wrongpass1").status_code, 429)
        self.assertEqual(self.login().status_code, 429)            # even the right password is refused
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_lockout_is_per_username(self):
        self.register(self.app.test_client(), username="carol", password="carolpass1")
        for _ in range(5):
            self.login(password="wrongpass1")
        self.assertEqual(self.login(username="carol", password="carolpass1").status_code, 200)

    def test_success_resets_failure_count(self):
        for _ in range(4):
            self.login(password="wrongpass1")
        self.assertEqual(self.login().status_code, 200)
        self.client.post("/api/logout")
        for _ in range(4):
            self.assertEqual(self.login(password="wrongpass1").status_code, 401)

    def test_logout_ends_the_session(self):
        self.login()
        self.assertEqual(self.client.post("/api/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_login_needs_json(self):
        r = self.client.post("/api/login", data={"username": "alice", "password": "secret123"})
        self.assertEqual(r.status_code, 400)

    def test_login_with_missing_fields(self):
        self.assertEqual(self.client.post("/api/login", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/login", json={"username": "alice"}).status_code, 400)


class ExpenseCrudTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def test_create(self):
        r = self.add()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json(), {"id": 1, "date": "2026-10-05", "category": "Food",
                                        "amount": 120.0, "note": "Lunch"})

    def test_category_is_normalised(self):
        self.assertEqual(self.add(category="  tRaNsPoRt ").get_json()["category"], "Transport")

    def test_list_is_newest_first(self):
        self.add(date="2026-10-05", note="a")
        self.add(date="2026-10-07", note="b")
        self.add(date="2026-10-07", note="c")
        notes = [e["note"] for e in self.client.get("/api/expenses").get_json()]
        self.assertEqual(notes, ["c", "b", "a"])

    def test_update(self):
        self.add()
        r = self.client.put("/api/expenses/1", json={"date": "2026-10-06", "category": "Books",
                                                     "amount": 300.5, "note": "Edited"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"id": 1, "date": "2026-10-06", "category": "Books",
                                        "amount": 300.5, "note": "Edited"})
        self.assertEqual(self.client.get("/api/expenses").get_json()[0]["note"], "Edited")

    def test_delete(self):
        self.add()
        self.assertEqual(self.client.delete("/api/expenses/1").status_code, 204)
        self.assertEqual(self.client.get("/api/expenses").get_json(), [])
        self.assertEqual(self.client.delete("/api/expenses/1").status_code, 404)

    def test_update_and_delete_unknown_id(self):
        body = {"date": "2026-10-06", "category": "Books", "amount": 1, "note": ""}
        self.assertEqual(self.client.put("/api/expenses/99", json=body).status_code, 404)
        self.assertEqual(self.client.delete("/api/expenses/99").status_code, 404)

    def test_bad_dates_rejected(self):
        for bad in ["2026-13-01", "2026-02-30", "05-10-2026", "2026-1-5", "20261005", "1999-01-01",
                    "2101-01-01", "", None, 20261005, "2026-10-05\n", "\u0662\u0660\u0662\u0666-10-05"]:
            r = self.add(date=bad)
            self.assertEqual(r.status_code, 400, repr(bad))
            self.assertEqual(r.get_json()["field"], "date")

    def test_leap_day(self):
        self.assertEqual(self.add(date="2028-02-29").status_code, 201)
        self.assertEqual(self.add(date="2027-02-29").status_code, 400)

    def test_bad_amounts_rejected(self):
        for bad in ["abc", -5, 0, "0", 1000001, "1.234", True, None, "nan", "inf", "", [5], {"a": 1}]:
            r = self.add(amount=bad)
            self.assertEqual(r.status_code, 400, repr(bad))
            self.assertEqual(r.get_json()["field"], "amount")

    def test_good_amounts_accepted(self):
        for good, expected in [("120", 120.0), (45.5, 45.5), ("0.01", 0.01), (1000000, 1000000.0), ("7.10", 7.1)]:
            self.assertEqual(self.add(amount=good).get_json()["amount"], expected)

    def test_bad_categories_rejected(self):
        for bad in ["Gambling", "", None, 5, ["Food"]]:
            r = self.add(category=bad)
            self.assertEqual(r.status_code, 400, repr(bad))
            self.assertEqual(r.get_json()["field"], "category")

    def test_notes(self):
        self.assertEqual(self.add(note=None).get_json()["note"], "")
        self.assertEqual(self.add(note="  too   many\n spaces ").get_json()["note"], "too many spaces")
        self.assertEqual(self.add(note="x" * 101).status_code, 400)
        self.assertEqual(self.add(note="bad\x00char").status_code, 400)
        self.assertEqual(self.add(note=123).status_code, 400)

    def test_html_in_note_is_kept_as_plain_text(self):
        r = self.add(note="<script>alert(1)</script>")
        self.assertEqual(r.get_json()["note"], "<script>alert(1)</script>")
        self.assertEqual(r.content_type, "application/json")

    def test_money_is_stored_as_whole_paise(self):
        for _ in range(3):
            self.add(amount="0.10")
        self.assertEqual(self.client.get("/api/summary").get_json()["total"], 0.3)
        stored = sqlite3.connect(self.db_path).execute("SELECT amount_paise FROM expenses").fetchall()
        self.assertEqual(stored, [(10,), (10,), (10,)])

    def test_form_posts_are_refused(self):
        """A web page on another site can only send form data, never JSON, so this blocks forged requests."""
        r = self.client.post("/api/expenses", data={"date": "2026-10-05", "category": "Food", "amount": "1"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get("/api/expenses").get_json(), [])

    def test_body_must_be_a_json_object(self):
        for payload in ([], "text", 5, None):
            self.assertEqual(self.client.post("/api/expenses", json=payload).status_code, 400)

    def test_oversized_request_refused(self):
        r = self.client.post("/api/expenses", json={"note": "x" * 20000})
        self.assertEqual(r.status_code, 413)
        self.assertIn("error", r.get_json())


class FilterAndSummaryTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.add(date="2026-10-05", category="Food", amount="120")
        self.add(date="2026-10-06", category="Transport", amount="40")
        self.add(date="2026-10-07", category="Books", amount="350")
        self.add(date="2026-11-01", category="Food", amount="75.50")

    def amounts(self, query):
        return [e["amount"] for e in self.client.get("/api/expenses" + query).get_json()]

    def test_filter_by_category(self):
        self.assertEqual(self.amounts("?category=food"), [75.5, 120.0])

    def test_filter_by_date_range(self):
        self.assertEqual(self.amounts("?from=2026-10-06&to=2026-10-31"), [350.0, 40.0])
        self.assertEqual(self.amounts("?from=2026-11-01"), [75.5])
        self.assertEqual(self.amounts("?to=2026-10-05"), [120.0])

    def test_filters_combine(self):
        self.assertEqual(self.amounts("?category=Food&to=2026-10-31"), [120.0])

    def test_bad_filters_are_rejected(self):
        for query in ["?category=Nope", "?from=bad", "?to=2026-02-30"]:
            self.assertEqual(self.client.get("/api/expenses" + query).status_code, 400, query)

    def test_sql_injection_attempts_are_harmless(self):
        r = self.client.get("/api/expenses?category=Food'%20OR%20'1'='1")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.client.get("/api/expenses").get_json()), 4)

    def test_summary_all_time(self):
        s = self.client.get("/api/summary").get_json()
        self.assertEqual(s["total"], 585.5)
        self.assertEqual(s["count"], 4)
        by = {c["category"]: c for c in s["by_category"]}
        self.assertEqual(by["Food"], {"category": "Food", "total": 195.5, "count": 2})
        self.assertEqual(by["Recharge"]["total"], 0)
        self.assertEqual([c["category"] for c in s["by_category"]],
                         ["Food", "Transport", "Books", "Recharge", "Entertainment", "Other"])

    def test_summary_for_a_date_range(self):
        s = self.client.get("/api/summary?from=2026-10-01&to=2026-10-31").get_json()
        self.assertEqual((s["total"], s["count"]), (510.0, 3))

    def test_summary_empty_range(self):
        s = self.client.get("/api/summary?from=2030-01-01").get_json()
        self.assertEqual((s["total"], s["count"]), (0, 0))


class IsolationTests(AppTestCase):
    """One user must never see or change another user's data."""

    def setUp(self):
        super().setUp()
        self.register()
        self.add()                                   # alice's expense, id 1
        self.bob = self.other_client()

    def test_other_user_sees_nothing(self):
        self.assertEqual(self.bob.get("/api/expenses").get_json(), [])
        self.assertEqual(self.bob.get("/api/summary").get_json()["total"], 0)

    def test_other_user_cannot_update_or_delete(self):
        body = {"date": "2026-10-06", "category": "Books", "amount": 1, "note": "hacked"}
        self.assertEqual(self.bob.put("/api/expenses/1", json=body).status_code, 404)
        self.assertEqual(self.bob.delete("/api/expenses/1").status_code, 404)
        mine = self.client.get("/api/expenses").get_json()
        self.assertEqual(mine[0]["note"], "Lunch")

    def test_ids_are_global_but_data_is_not(self):
        r = self.add(self.bob, note="bob's")
        self.assertEqual(r.get_json()["id"], 2)
        self.assertEqual(len(self.client.get("/api/expenses").get_json()), 1)
        self.assertEqual(len(self.bob.get("/api/expenses").get_json()), 1)


class AccessControlTests(AppTestCase):
    def test_api_requires_login(self):
        c = self.client
        calls = [c.get("/api/me"), c.get("/api/expenses"), c.post("/api/expenses", json={}),
                 c.put("/api/expenses/1", json={}), c.delete("/api/expenses/1"), c.get("/api/summary")]
        for r in calls:
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.get_json()["error"], "Please log in.")

    def test_pages_redirect_when_logged_out(self):
        for path in ["/", "/dashboard"]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r.headers["Location"].endswith("/login"))

    def test_login_pages_redirect_when_logged_in(self):
        self.register()
        for path in ["/", "/login", "/register"]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r.headers["Location"].endswith("/dashboard"))

    def test_pages_render(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/register").status_code, 200)
        self.register()
        page = self.client.get("/dashboard")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"alice", page.data)
        self.assertIn(b"Entertainment", page.data)

    def test_deleted_account_session_stops_working(self):
        self.register()
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM users")
        conn.commit()
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_deleting_a_user_removes_their_expenses(self):
        self.register()
        self.add()
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM users")
        conn.commit()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0], 0)


class HeaderAndErrorTests(AppTestCase):
    def test_security_headers(self):
        r = self.client.get("/login")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_static_files_are_served_and_cacheable(self):
        r = self.client.get("/static/style.css")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("no-store", r.headers.get("Cache-Control", ""))
        r.close()

    def test_api_errors_are_json(self):
        r = self.client.get("/api/nope")
        self.assertEqual((r.status_code, r.get_json()["error"]), (404, "Not Found"))
        r = self.client.patch("/api/login")
        self.assertEqual(r.status_code, 405)
        self.assertIn("error", r.get_json())

    def test_session_cookie_flags(self):
        r = self.register()
        cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)


class ValidatorUnitTests(unittest.TestCase):
    def test_amount_returns_paise(self):
        self.assertEqual(clean_amount("120"), 12000)
        self.assertEqual(clean_amount(75.5), 7550)
        self.assertEqual(clean_amount(" 0.07 "), 7)

    def test_amount_float_noise_is_not_a_problem(self):
        self.assertEqual(clean_amount(0.1 + 0.2 - 0.2), 10)    # 0.10000000000000003 as a float

    def test_date_boundaries(self):
        self.assertEqual(clean_date("2000-01-01"), "2000-01-01")
        self.assertEqual(clean_date("2100-12-31"), "2100-12-31")
        with self.assertRaises(ValidationError):
            clean_date("2100-13-01")

    def test_note_unicode_is_allowed(self):
        self.assertEqual(clean_note("\u091a\u093e\u092f \u2615"), "\u091a\u093e\u092f \u2615")


if __name__ == "__main__":
    unittest.main()
