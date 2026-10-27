from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import inventory
from examples.fixture import local_server
from examples.run_demo import request


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / "inventory.db"
        cls.fixture = local_server(cls.db)
        cls.port = cls.fixture.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.__exit__(None, None, None)
        cls.temp.cleanup()

    def post(self, path, data):
        return request(self.port, "POST", path, data)

    def get(self, path):
        return request(self.port, "GET", path)

    def setup_loan(self, qty=1):
        status, asset = self.post("/assets", {"name": "Projector", "quantity": qty})
        self.assertEqual(status, 201)
        status, borrower = self.post("/borrowers", {"name": "Alice"})
        self.assertEqual(status, 201)
        return asset["id"], borrower["id"]

    def test_end_to_end_return_and_filters(self):
        aid, bid = self.setup_loan()
        status, loan = self.post("/loans", {"asset_id": aid, "borrower_id": bid})
        self.assertEqual(status, 201)
        self.assertIsNone(loan["returned_at"])
        self.assertTrue(loan["borrowed_at"].endswith("+00:00"))
        self.assertIn(aid, [x["id"] for x in self.get("/assets?available=false")[1]])
        self.assertIn(loan["id"], [x["id"] for x in self.get("/loans?status=active")[1]])
        self.assertEqual(self.post(f'/loans/{loan["id"]}/return', {})[0], 200)
        self.assertIn(aid, [x["id"] for x in self.get("/assets?available=true")[1]])
        self.assertIn(loan["id"], [x["id"] for x in self.get("/loans?status=returned")[1]])
        self.assertEqual(self.post(f'/loans/{loan["id"]}/return', {})[0], 409)

    def test_double_loan_and_unavailable(self):
        aid, first = self.setup_loan()
        second = self.post("/borrowers", {"name": "Bob"})[1]["id"]
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": first})[0], 201)
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": first})[0], 409)
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": second})[0], 409)
        with closing(inventory.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM loans WHERE asset_id=?", (aid,)).fetchone()[0], 1)

    def test_concurrent_requests_cannot_overbook(self):
        aid, first = self.setup_loan()
        second = self.post("/borrowers", {"name": "Carol"})[1]["id"]
        def send(bid):
            return self.post("/loans", {"asset_id": aid, "borrower_id": bid})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(send, [first, second]))
        self.assertEqual(sorted(statuses), [201, 409])
        with closing(inventory.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM loans WHERE asset_id=? AND returned_at IS NULL", (aid,)).fetchone()[0], 1)

    def test_more_than_one_unit(self):
        aid, first = self.setup_loan(2)
        second = self.post("/borrowers", {"name": "Dave"})[1]["id"]
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": first})[0], 201)
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": first})[0], 409)
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": second})[0], 201)
        item = next(x for x in self.get("/assets")[1] if x["id"] == aid)
        self.assertEqual(item["available"], 0)

    def test_missing_references_and_invalid_return(self):
        aid, bid = self.setup_loan()
        self.assertEqual(self.post("/loans", {"asset_id": 2147483647, "borrower_id": bid})[0], 404)
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": 2147483647})[0], 404)
        self.assertEqual(self.post("/loans/2147483647/return", {})[0], 404)
        self.assertEqual(self.post("/loans/0/return", {})[0], 404)

    def test_bad_fields(self):
        cases = [("/assets", {"name": "", "quantity": 1}), ("/assets", {"name": "x", "quantity": 0}), ("/assets", {"name": "x", "quantity": True}), ("/assets", {"name": "x", "quantity": 100001}), ("/assets", {"name": "x", "quantity": 1, "extra": 1}), ("/borrowers", {"name": "bad\nname"}), ("/borrowers", {"name": "x"*101}), ("/loans", {"asset_id": True, "borrower_id": 1}), ("/loans", []), ("/loans/1/return", {"anything": True})]
        for path, data in cases:
            with self.subTest(path=path, data=str(data)[:20]):
                self.assertEqual(self.post(path, data)[0], 400)

    def test_bad_filters(self):
        for path in ["/assets?available=maybe", "/assets?available=true&available=false", "/assets?other=x", "/loans?status=unknown", "/borrowers?status=active"]:
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 400)

    def test_openapi_and_unknown_route(self):
        status, doc = self.get("/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(doc["openapi"], "3.0.3")
        self.assertEqual(set(doc["paths"]), {"/assets", "/borrowers", "/loans", "/loans/{id}/return"})
        self.assertEqual(self.get("/missing")[0], 404)
        self.assertEqual(self.post("/missing", {})[0], 404)

    def test_database_insert_failure_rolls_back(self):
        aid, bid = self.setup_loan()
        with closing(inventory.connect(self.db)) as c, c:
            c.execute("CREATE TRIGGER reject_test BEFORE INSERT ON loans WHEN NEW.asset_id = %d BEGIN SELECT RAISE(ABORT, 'fail'); END" % aid)
        self.assertEqual(self.post("/loans", {"asset_id": aid, "borrower_id": bid})[0], 503)
        with closing(inventory.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM loans WHERE asset_id=?", (aid,)).fetchone()[0], 0)
        item = next(x for x in self.get("/assets")[1] if x["id"] == aid)
        self.assertEqual(item["available"], 1)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "db.sqlite"

    def test_foreign_database_not_modified(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TABLE unrelated(x)")
            c.execute("INSERT INTO unrelated VALUES ('keep')")
        original = self.db.read_bytes()
        with self.assertRaises(ValueError):
            inventory.connect(self.db, create=True)
        self.assertEqual(self.db.read_bytes(), original)

    def test_invalid_json(self):
        for raw in [b"\xff", b"{", b'{"a":1,"a":2}', b'{"n":NaN}', b'{"n":' + b"9"*5000 + b'}']:
            with self.subTest(prefix=raw[:20]), self.assertRaises(inventory.ApiError):
                inventory.parse_body(raw)

    def test_blank_name_unicode_surrogate_and_boolean_id(self):
        for name in [" ", "bad\x00", "\ud800", None]:
            with self.assertRaises(inventory.ApiError):
                inventory.valid_name(name)
        with self.assertRaises(inventory.ApiError):
            inventory.positive(True)

    def test_symlink_database_rejected(self):
        target = Path(self.temp.name) / "target.db"
        with closing(inventory.connect(target, create=True)):
            pass
        try:
            self.db.symlink_to(target)
        except OSError:
            self.skipTest("OS does not allow creating symlinks")
        with self.assertRaises(ValueError):
            inventory.connect(self.db)

    def test_invalid_schema_is_rejected(self):
        with closing(inventory.connect(self.db, create=True)) as c:
            c.execute("PRAGMA user_version=2")
        with self.assertRaises(ValueError):
            inventory.connect(self.db)


if __name__ == "__main__":
    unittest.main()
