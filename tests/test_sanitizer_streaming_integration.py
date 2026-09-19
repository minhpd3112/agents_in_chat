#!/usr/bin/env python3
# ==============================================================================
#  test_sanitizer_streaming_integration.py - Mock Backend Integration Tests
#  Tests SSE streaming, zero-buffering, status passthrough & error handling.
#  Python stdlib-only.
# ==============================================================================

import http.client
import http.server
import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from request_sanitizer import SanitizerProxyServer


class MockBackendHandler(http.server.BaseHTTPRequestHandler):
    """Mock backend server simulating cli-proxy-api responses and SSE streaming."""

    def log_message(self, format: str, *args):
        # Suppress logging during tests
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({
                "object": "list",
                "data": [
                    {"id": "gemini-3.8-flash"},
                    {"id": "claude-sonnet-4.6-thinking"},
                    {"id": "gpt-5.6-sol"},
                ]
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        req_body = self.rfile.read(content_len) if content_len > 0 else b""

        # Record received payload on the server instance
        self.server.last_received_body = req_body
        try:
            self.server.last_received_json = json.loads(req_body.decode("utf-8"))
        except Exception:
            self.server.last_received_json = None

        # Check for simulated error triggers in headers
        simulated_status = self.headers.get("X-Simulate-Status")
        if simulated_status:
            status_code = int(simulated_status)
            err_body = json.dumps({"error": f"simulated {status_code}"}).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            return

        # Simulate SSE chunked streaming
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        events = [
            'event: response.created\ndata: {"type":"response.created","id":"resp_1"}\n\n',
            'event: response.in_progress\ndata: {"type":"response.in_progress","id":"resp_1"}\n\n',
            'event: response.output_item.added\ndata: {"type":"item","id":"msg_1"}\n\n',
            'event: response.text.delta\ndata: {"type":"delta","text":"Hello world"}\n\n',
            'event: response.completed\ndata: {"type":"response.completed"}\n\n',
        ]

        for ev in events:
            chunk = ev.encode("utf-8")
            # Write chunk in standard HTTP chunked framing
            self.wfile.write(f"{len(chunk):X}\r\n".encode("latin1") + chunk + b"\r\n")
            self.wfile.flush()
            time.sleep(0.01)

        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class MockBackendServer(http.server.ThreadingHTTPServer):
    def __init__(self, addr):
        super().__init__(addr, MockBackendHandler)
        self.last_received_body = None
        self.last_received_json = None


class TestSanitizerStreamingIntegration(unittest.TestCase):
    backend_server = None
    sanitizer_server = None
    backend_port = 0
    sanitizer_port = 0

    @classmethod
    def setUpClass(cls):
        # 1. Start mock backend on an ephemeral port
        cls.backend_server = MockBackendServer(("127.0.0.1", 0))
        cls.backend_port = cls.backend_server.server_address[1]
        cls.backend_thread = threading.Thread(target=cls.backend_server.serve_forever, daemon=True)
        cls.backend_thread.start()

        # 2. Start sanitizer proxy on an ephemeral port forwarding to backend
        cls.sanitizer_server = SanitizerProxyServer(
            ("127.0.0.1", 0),
            backend_host="127.0.0.1",
            backend_port=cls.backend_port
        )
        cls.sanitizer_port = cls.sanitizer_server.server_address[1]
        cls.sanitizer_thread = threading.Thread(target=cls.sanitizer_server.serve_forever, daemon=True)
        cls.sanitizer_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        if cls.sanitizer_server:
            cls.sanitizer_server.shutdown()
            cls.sanitizer_server.server_close()
        if cls.backend_server:
            cls.backend_server.shutdown()
            cls.backend_server.server_close()

    def test_01_models_endpoint_passthrough(self):
        """Verify GET /v1/models passes through cleanly."""
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/models"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            model_ids = [m["id"] for m in data.get("data", [])]
            self.assertIn("gemini-3.8-flash", model_ids)
            self.assertIn("claude-sonnet-4.6-thinking", model_ids)

    def test_02_sse_streaming_and_sanitization(self):
        """Verify POST /v1/responses sanitizes toxic payload and streams SSE without buffering."""
        toxic_payload = {
            "model": "gemini-3.8-flash",
            "stream": True,
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {
                    "role": "developer",
                    "content": "<model_switch>\nYou are Codex, a coding agent based on GPT-5.\n</model_switch>"
                },
                {"role": "user", "content": "hello"}
            ]
        }
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses"
        req = urllib.request.Request(
            url,
            data=json.dumps(toxic_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        received_events = []
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            for line in resp:
                l = line.decode("utf-8").strip()
                if l.startswith("event:"):
                    received_events.append(l)

        # 1. Verify client received all 5 SSE events in real-time
        self.assertEqual(len(received_events), 5)
        self.assertEqual(received_events[0], "event: response.created")
        self.assertEqual(received_events[-1], "event: response.completed")

        # 2. Verify backend received SANITIZED payload
        backend_json = self.backend_server.last_received_json
        self.assertIsNotNone(backend_json)
        self.assertEqual(backend_json["instructions"], "You are Codex, an expert coding agent.")
        dev_content = backend_json["input"][0]["content"]
        self.assertNotIn("based on GPT-5", dev_content)
        self.assertIn("an expert coding agent", dev_content)

    def test_03_status_code_passthrough(self):
        """Verify status codes (400, 429, 500, 503) are passed through verbatim."""
        for code in [400, 429, 500, 503]:
            url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses"
            req = urllib.request.Request(
                url,
                data=json.dumps({"model": "gemini-3.8-flash"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Simulate-Status": str(code)}
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, code)

    def test_04_backend_offline_returns_502(self):
        """Verify 502 Bad Gateway is returned when backend is unreachable."""
        # Create a sanitizer pointing to an unused port
        orphan_sanitizer = SanitizerProxyServer(
            ("127.0.0.1", 0),
            backend_host="127.0.0.1",
            backend_port=59999  # Unused port
        )
        orphan_port = orphan_sanitizer.server_address[1]
        t = threading.Thread(target=orphan_sanitizer.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        try:
            url = f"http://127.0.0.1:{orphan_port}/v1/models"
            req = urllib.request.Request(url)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 502)
        finally:
            orphan_sanitizer.shutdown()
            orphan_sanitizer.server_close()


def test_sanitizer_streaming_integration():
    """Runner function for integration into run_tests.py."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSanitizerStreamingIntegration)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=0)
    result = runner.run(suite)
    if result.wasSuccessful():
        return True, f"All {result.testsRun} streaming integration test cases PASSED!"
    return False, f"Streaming integration tests failed: {len(result.failures)} failures, {len(result.errors)} errors."


if __name__ == "__main__":
    unittest.main()
