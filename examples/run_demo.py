"""Exercise the API with synthetic data and a temporary localhost server."""
import http.client
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.fixture import local_server


def request(port, method, path, data=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        body = None if data is None else json.dumps(data).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def main():
    with tempfile.TemporaryDirectory() as temp, local_server(Path(temp) / "demo.db") as port:
        status, asset = request(port, "POST", "/assets", {"name": "Projector", "quantity": 1})
        assert status == 201
        status, first = request(port, "POST", "/borrowers", {"name": "Student A"})
        assert status == 201
        status, second = request(port, "POST", "/borrowers", {"name": "Student B"})
        assert status == 201
        status, loan = request(port, "POST", "/loans", {"asset_id": asset["id"], "borrower_id": first["id"]})
        assert status == 201
        assert request(port, "POST", "/loans", {"asset_id": asset["id"], "borrower_id": first["id"]})[0] == 409
        assert request(port, "POST", "/loans", {"asset_id": asset["id"], "borrower_id": second["id"]})[0] == 409
        assert request(port, "GET", "/assets?available=false")[1][0]["available"] == 0
        assert request(port, "POST", f'/loans/{loan["id"]}/return', {})[0] == 200
        assert request(port, "POST", f'/loans/{loan["id"]}/return', {})[0] == 409
        assert request(port, "GET", "/assets?available=true")[1][0]["available"] == 1
        assert request(port, "GET", "/openapi.json")[1]["openapi"] == "3.0.3"
        print("Demo passed: created asset and borrowers; duplicate and unavailable loans rejected; return restored stock.")
        print("Local temporary database only; no external service contacted.")


if __name__ == "__main__":
    main()
