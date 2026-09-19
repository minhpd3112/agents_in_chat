#!/usr/bin/env python3
# ==============================================================================
#  test_runtime_model_switch_regression.py - Regression Test for Runtime Switches
#  Simulates multi-turn session switching across Sol -> Claude -> Gemini -> Sol.
#  Verifies Antigravity requests are sanitized and GPT requests remain untouched.
#  Python stdlib-only.
# ==============================================================================

import http.server
import json
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from request_sanitizer import SanitizerProxyServer


class MockMultiTurnBackendHandler(http.server.BaseHTTPRequestHandler):
    """Mock backend that captures each turn's request and validates competitor branding rules."""

    def log_message(self, format: str, *args):
        pass

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)
        data = json.loads(body.decode("utf-8"))

        model = data.get("model", "")
        self.server.received_turns.append({
            "model": model,
            "raw_payload": data
        })

        # Simulate Upstream Content Filter:
        # If model is Antigravity and payload contains "based on GPT-5" in developer messages or instructions -> 429!
        is_antigravity = "gemini" in model.lower() or "claude" in model.lower()
        if is_antigravity:
            body_str = body.decode("utf-8")
            if "based on GPT-5" in body_str:
                # Content filter triggered!
                err = json.dumps({
                    "error": {
                        "code": "429",
                        "message": "Resource has been exhausted (content filter triggered by competitor branding)."
                    }
                }).encode("utf-8")
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return

        # Return successful SSE response
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        resp_text = f"event: response.text.delta\ndata: {{\"text\": \"Response from {model}\"}}\n\n"
        chunk = resp_text.encode("utf-8")
        self.wfile.write(f"{len(chunk):X}\r\n".encode("latin1") + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class MockMultiTurnServer(http.server.ThreadingHTTPServer):
    def __init__(self, addr):
        super().__init__(addr, MockMultiTurnBackendHandler)
        self.received_turns: List[Dict] = []


class TestRuntimeModelSwitchRegression(unittest.TestCase):
    backend_server = None
    sanitizer_server = None
    backend_port = 0
    sanitizer_port = 0

    @classmethod
    def setUpClass(cls):
        cls.backend_server = MockMultiTurnServer(("127.0.0.1", 0))
        cls.backend_port = cls.backend_server.server_address[1]
        cls.backend_thread = threading.Thread(target=cls.backend_server.serve_forever, daemon=True)
        cls.backend_thread.start()

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

    def _send_turn(self, payload: dict) -> Tuple[int, str]:
        url = f"http://127.0.0.1:{self.sanitizer_port}/v1/responses"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode("utf-8")
            return resp.status, content

    def test_multi_turn_switching_sequence(self):
        """Execute a 4-turn sequence: Sol -> Claude -> Gemini -> Sol.
        Verify:
        - Claude & Gemini succeed without triggering 429 content filter.
        - Sol remains 100% untouched.
        """
        # Turn 1: Started on GPT-5.6 Sol
        turn1_payload = {
            "model": "gpt-5.6-sol",
            "stream": True,
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {"role": "user", "content": "Hello Sol"}
            ]
        }
        status1, body1 = self._send_turn(turn1_payload)
        self.assertEqual(status1, 200)
        self.assertIn("Response from gpt-5.6-sol", body1)

        # Verify backend received untouched payload for Sol
        t1_rec = self.backend_server.received_turns[0]["raw_payload"]
        self.assertEqual(t1_rec["instructions"], "You are Codex, an agent based on GPT-5.")

        # Turn 2: User switches to Claude Sonnet 4.6
        turn2_payload = {
            "model": "claude-sonnet-4.6-thinking",
            "stream": True,
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {"role": "user", "content": "Hello Sol"},
                {"role": "assistant", "content": "Hello! How can I help?"},
                {
                    "role": "developer",
                    "content": (
                        "<model_switch>\n"
                        "The user was previously using a different model.\n"
                        "You are Codex, a coding agent based on GPT-5.\n"
                        "</model_switch>"
                    )
                },
                {"role": "user", "content": "Now answer as Claude"}
            ]
        }
        status2, body2 = self._send_turn(turn2_payload)
        self.assertEqual(status2, 200, "Claude must not fail with 429")
        self.assertIn("Response from claude-sonnet-4.6-thinking", body2)

        # Verify backend received SANITIZED instructions & developer message
        t2_rec = self.backend_server.received_turns[1]["raw_payload"]
        self.assertEqual(t2_rec["instructions"], "You are Codex, an expert coding agent.")
        self.assertNotIn("based on GPT-5", t2_rec["input"][2]["content"])
        self.assertIn("an expert coding agent", t2_rec["input"][2]["content"])

        # Turn 3: User switches to Gemini 3.8 Flash
        turn3_payload = {
            "model": "gemini-3.8-flash",
            "stream": True,
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {"role": "user", "content": "Hello Sol"},
                {"role": "assistant", "content": "Hello! How can I help?"},
                {
                    "role": "developer",
                    "content": "<model_switch>You are Codex, a coding agent based on GPT-5.</model_switch>"
                },
                {"role": "assistant", "content": "Claude here."},
                {
                    "role": "developer",
                    "content": "<model_switch>You are Codex, a coding agent based on GPT-5.</model_switch>"
                },
                {"role": "user", "content": "Now answer as Gemini"}
            ]
        }
        status3, body3 = self._send_turn(turn3_payload)
        self.assertEqual(status3, 200, "Gemini must not fail with 429")
        self.assertIn("Response from gemini-3.8-flash", body3)

        t3_rec = self.backend_server.received_turns[2]["raw_payload"]
        self.assertEqual(t3_rec["instructions"], "You are Codex, an expert coding agent.")
        self.assertNotIn("based on GPT-5", t3_rec["input"][2]["content"])
        self.assertNotIn("based on GPT-5", t3_rec["input"][4]["content"])

        # Turn 4: User switches back to GPT-5.6 Sol
        turn4_payload = {
            "model": "gpt-5.6-sol",
            "stream": True,
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {
                    "role": "developer",
                    "content": "<model_switch>You are Codex, a coding agent based on GPT-5.</model_switch>"
                },
                {"role": "user", "content": "Back to Sol"}
            ]
        }
        status4, body4 = self._send_turn(turn4_payload)
        self.assertEqual(status4, 200)
        self.assertIn("Response from gpt-5.6-sol", body4)

        t4_rec = self.backend_server.received_turns[3]["raw_payload"]
        self.assertEqual(t4_rec["instructions"], "You are Codex, an agent based on GPT-5.")
        self.assertIn("based on GPT-5", t4_rec["input"][0]["content"])


def test_runtime_model_switch_regression():
    """Runner function for integration into run_tests.py."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRuntimeModelSwitchRegression)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=0)
    result = runner.run(suite)
    if result.wasSuccessful():
        return True, "Runtime multi-turn model switching sequence (Sol -> Claude -> Gemini -> Sol) verified 100%!"
    return False, f"Regression tests failed: {len(result.failures)} failures, {len(result.errors)} errors."


if __name__ == "__main__":
    unittest.main()
