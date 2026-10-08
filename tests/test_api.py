import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from leanreview.api import validate_spec, check_api


class Handler(BaseHTTPRequestHandler):
    posted = 0
    def log_message(self, *_):
        pass
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "https://example.org/")
            self.end_headers()
            return
        if self.path == "/auth" and self.headers.get("Authorization") != "Bearer abc-token":
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": {"name": "demo"}, "password": "NEVER_RETURN"}).encode())
    def do_POST(self):
        Handler.posted += 1
        self.send_response(201)
        self.end_headers()


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(2)

    def test_healthy_contract_no_response_body(self):
        result = check_api(self.base, {"endpoints": [{"path": "/health", "required_keys": ["data.name"]}]})
        self.assertEqual(result["passed"], 1)
        self.assertNotIn("NEVER_RETURN", str(result))

    def test_bearer_token_never_logged(self):
        with patch.dict(os.environ, {"TEST_TOKEN": "abc-token"}):
            result = check_api(self.base, {"endpoints": [{"path": "/auth"}]}, bearer_env="TEST_TOKEN")
        self.assertEqual(result["passed"], 1)
        self.assertNotIn("abc-token", str(result))

    def test_redirect_is_blocked(self):
        result = check_api(self.base, {"endpoints": [{"path": "/redirect"}]})
        self.assertIn("redirect-blocked", result["results"][0]["issues"])

    def test_remote_rejected_default(self):
        with self.assertRaises(ValueError):
            check_api("https://example.com", {"endpoints": [{"path": "/"}]})

    def test_mutation_off_by_default(self):
        with self.assertRaises(ValueError):
            check_api(self.base, {"endpoints": [{"path": "/", "method": "POST"}]})

    def test_mutation_explicit(self):
        result = check_api(self.base, {"endpoints": [{"path": "/", "method": "POST", "expected_status": 201}]}, allow_mutation=True)
        self.assertEqual(result["passed"], 1)

    def test_contract_validation(self):
        for value in ("not-json", {"endpoints": [{"path": "//evil"}]},
                      {"endpoints": [{"path": "/x", "max_ms": "quick"}]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_spec(value, False)

if __name__ == '__main__':
    unittest.main()
