#!/usr/bin/env python3
# ==============================================================================
#  test_sanitizer_streaming_integration.py - Mock Backend Integration Tests
#  Tests SSE timing, fail-closed validation, status passthrough & error handling.
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
from typing import List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from request_sanitizer import SanitizerProxyServer


class MockBackendHandler(http.server.BaseHTTPRequestHandler):
    """Mock backend server simulating cli-proxy-api responses and SSE streaming."""

    def log_message(self, format: str, *args):
        pass

    def do_HEAD(self):
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "128")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

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
        elif self.path == "/test-204":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/test-304":
            self.send_response(304)
            self.end_headers()
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        self.server.total_post_requests += 1
        content_len = int(self.headers.get("Content-Length", 0))
        req_body = self.rfile.read(content_len) if content_len > 0 else b""

        self.server.last_received_body = req_body
        try:
            self.server.last_received_json = json.loads(req_body.decode("utf-8"))
        except Exception:
            self.server.last_received_json = None

        # Check for simulated error triggers
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

        # Check for timing test delay mode
        sse_delay = float(self.headers.get("X-SSE-Delay", "0.01"))
        notify_timing = (self.headers.get("X-Timing-Test") == "1")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        events = [
            'event: response.created\ndata: {"type":"response.created","id":"resp_1"}\n\n',
            'event: response.output_item.added\ndata: {"type":"item","id":"msg_1"}\n\n',
            'event: response.completed\ndata: {"type":"response.completed"}\n\n',
        ]

        try:
            for i, ev in enumerate(events):
                chunk = ev.encode("utf-8")
                self.wfile.write(f"{len(chunk):X}\r\n".encode("latin1") + chunk + b"\r\n")
                self.wfile.flush()

                if i == 0 and notify_timing:
                    self.server.first_event_sent_time = time.time()
                    self.server.first_event_sent.set()

                if i < len(events) - 1:
                    time.sleep(sse_delay)
                else:
                    if notify_timing:
                        self.server.final_event_sent_time = time.time()

            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, socket.error):
            pass


class MockBackendServer(http.server.ThreadingHTTPServer):
    def __init__(self, addr):
        super().__init__(addr, MockBackendHandler)
        self.last_received_body = None
        self.last_received_json = None
        self.total_post_requests = 0
        self.first_event_sent = threading.Event()
        self.first_event_sent_time = 0.0
        self.final_event_sent_time = 0.0

    def handle_error(self, request, client_address):
        # Suppress broken pipe noise during deliberate disconnect tests
        pass


class TestSanitizerStreamingIntegration(unittest.TestCase):
    backend_server: Optional[MockBackendServer] = None
    sanitizer_server: Optional[SanitizerProxyServer] = None
    backend_port: int = 0
    sanitizer_port: int = 0

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

    def setUp(self):
        self.backend_server.last_received_body = None
        self.backend_server.last_received_json = None
        self.backend_server.first_event_sent.clear()
        self.backend_server.first_event_sent_time = 0.0
        self.backend_server.final_event_sent_time = 0.0

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

    def test_02_sse_timing_unbuffered_first_event(self):
        """Verify client receives first SSE event BEFORE backend emits the final event.
        
        Backend emits 3 events spaced 150ms apart (total ~300ms stream duration).
        With unbuffered read1(), client receives event 1 in < 50ms (well before event 3 at ~300ms).
        With old 4096-byte buffered read, client was blocked until EOF at > 300ms.
        """
        payload = {"model": "gemini-3.8-flash", "stream": True}
        conn = http.client.HTTPConnection("127.0.0.1", self.sanitizer_port, timeout=10)
        conn.request(
            "POST",
            "/v1/responses",
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-SSE-Delay": "0.15",
                "X-Timing-Test": "1"
            }
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)

        # Read first SSE chunk
        first_chunk = resp.read1(1024)
        client_first_recv_time = time.time()
        self.assertTrue(len(first_chunk) > 0)
        self.assertIn(b"response.created", first_chunk)

        # Read remaining stream
        rest = resp.read()
        conn.close()

        # Assert: Client received event 1 BEFORE backend emitted event 3
        final_emit = self.backend_server.final_event_sent_time
        self.assertTrue(
            client_first_recv_time < final_emit,
            f"Expected client to receive event 1 ({client_first_recv_time}) BEFORE backend emitted event 3 ({final_emit})"
        )

    def test_03_fail_closed_malformed_json_returns_400_no_backend_forward(self):
        """Verify malformed JSON returns HTTP 400 and is NOT forwarded to backend."""
        prev_count = self.backend_server.total_post_requests
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses"
        bad_body = b'{"model": "gemini-3.8-flash", "instructions": "based on GPT-5", incomplete'

        req = urllib.request.Request(
            url,
            data=bad_body,
            headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)

        self.assertEqual(ctx.exception.code, 400)
        # Verify backend did NOT receive request
        self.assertEqual(self.backend_server.total_post_requests, prev_count)

    def test_04_fail_closed_array_root_json_returns_400(self):
        """Verify non-object JSON root returns HTTP 400 and is NOT forwarded."""
        prev_count = self.backend_server.total_post_requests
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses"
        bad_body = json.dumps(["array", "root", "not", "object"]).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=bad_body,
            headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)

        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.backend_server.total_post_requests, prev_count)

    def test_05_fail_closed_unsupported_content_encoding_returns_415(self):
        """Verify unsupported Content-Encoding returns HTTP 415 and is NOT forwarded."""
        prev_count = self.backend_server.total_post_requests
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses"
        body = json.dumps({"model": "gemini-3.8-flash"}).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)

        self.assertEqual(ctx.exception.code, 415)
        self.assertEqual(self.backend_server.total_post_requests, prev_count)

    def test_06_query_string_url_still_sanitized(self):
        """Verify /v1/responses?trace=1 is properly recognized and sanitized."""
        toxic_payload = {
            "model": "gemini-3.8-flash",
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [{"role": "user", "content": "hi"}]
        }
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses?trace=1&session=123"
        req = urllib.request.Request(
            url,
            data=json.dumps(toxic_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)

        # Verify backend received SANITIZED payload
        backend_json = self.backend_server.last_received_json
        self.assertIsNotNone(backend_json)
        self.assertEqual(backend_json["instructions"], "You are Codex, an expert coding agent.")

    def test_07_gpt_and_unknown_models_forwarded_byte_exact(self):
        """Verify GPT and unknown models containing 'claude' are forwarded byte-exact."""
        # 1. GPT-5.6 Sol
        gpt_payload = {
            "model": "gpt-5.6-sol",
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [{"role": "user", "content": "hi"}]
        }
        raw_gpt = json.dumps(gpt_payload).encode("utf-8")
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses"
        req = urllib.request.Request(url, data=raw_gpt, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(self.backend_server.last_received_body, raw_gpt)

        # 2. Fake Claude model
        fake_payload = {
            "model": "fake-claude-99",
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [{"role": "user", "content": "hi"}]
        }
        raw_fake = json.dumps(fake_payload).encode("utf-8")
        req = urllib.request.Request(url, data=raw_fake, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(self.backend_server.last_received_body, raw_fake)

    def test_08_head_204_and_304_responses_handled_properly(self):
        """Verify HEAD, 204, and 304 responses do not hang and contain zero body bytes."""
        conn = http.client.HTTPConnection("127.0.0.1", self.sanitizer_port, timeout=5)

        # 1. HEAD request
        conn.request("HEAD", "/v1/models")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = resp.read()
        self.assertEqual(body, b"")

        # 2. 204 No Content
        conn.request("GET", "/test-204")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 204)
        body = resp.read()
        self.assertEqual(body, b"")

        # 3. 304 Not Modified
        conn.request("GET", "/test-304")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 304)
        body = resp.read()
        self.assertEqual(body, b"")

        conn.close()

    def test_09_client_disconnect_handled_cleanly(self):
        """Verify client disconnect does not crash sanitizer or leak connections."""
        payload = {"model": "gemini-3.8-flash", "stream": True}
        conn = http.client.HTTPConnection("127.0.0.1", self.sanitizer_port, timeout=5)
        conn.request(
            "POST",
            "/v1/responses",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-SSE-Delay": "0.1"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        # Read 1 chunk and close connection abruptly
        _ = resp.read1(256)
        conn.close()
        time.sleep(0.15)

        # Subsequent request must succeed normally
        req2 = urllib.request.Request(f"http://127.0.0.1:{self.sanitizer_port}/v1/models")
        with urllib.request.urlopen(req2, timeout=5) as resp2:
            self.assertEqual(resp2.status, 200)

    def test_10_status_code_passthrough(self):
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

    def test_11_backend_offline_returns_502(self):
        """Verify 502 Bad Gateway is returned when backend is unreachable."""
        orphan_sanitizer = SanitizerProxyServer(
            ("127.0.0.1", 0),
            backend_host="127.0.0.1",
            backend_port=59998
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
