#!/usr/bin/env python3
# ==============================================================================
#  request_sanitizer.py - Real-Time HTTP Request Sanitizer Reverse Proxy
#  Python stdlib-only.
#
#  Sits in front of CLIProxyAPI (e.g. listening on 127.0.0.1:8090 and forwarding
#  to 127.0.0.1:8095). Intercepts POST /v1/responses to sanitize <model_switch>
#  blocks in developer messages for Antigravity models (gemini, claude),
#  permanently preventing HTTP 429 content-filter triggers at runtime.
# ==============================================================================

import argparse
import copy
import http.client
import http.server
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

try:
    from instruction_compat import sanitize_instruction_text, sanitize_model_switch
    from log_utils import error, info, warn
except ImportError:
    # Fallback when running standalone or in test harness
    def info(msg: str) -> None:
        sys.stderr.write(f"[INFO] {msg}\n")

    def warn(msg: str) -> None:
        sys.stderr.write(f"[WARN] {msg}\n")

    def error(msg: str) -> None:
        sys.stderr.write(f"[ERROR] {msg}\n")

    from instruction_compat import sanitize_instruction_text, sanitize_model_switch


ANTIGRAVITY_KNOWN_MODELS: Set[str] = {
    "gemini-3.8-flash",
    "gemini-3.8-flash-high",
    "gemini-3.7-flash",
    "claude-sonnet-4.6-thinking",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-opus-4-6-thinking",
    "claude-3-7-sonnet",
}

HOP_BY_HOP_HEADERS: Set[str] = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def is_antigravity_model(model_name: Optional[str]) -> bool:
    """Return True if the model is an Antigravity upstream model (Gemini or Claude).

    Only Antigravity upstream models enforce the competitor branding content filter
    that triggers HTTP 429 when receiving 'based on GPT-5'.
    OpenAI / GPT models (Sol, Terra, Luna, Astra) must never be sanitized.
    """
    if not model_name or not isinstance(model_name, str):
        return False
    normalized = model_name.lower().strip()
    if normalized in ANTIGRAVITY_KNOWN_MODELS:
        return True
    return "gemini" in normalized or "claude" in normalized


def sanitize_request_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Sanitize <model_switch> in developer messages for Antigravity models.

    Strict Contract:
    - ONLY sanitizes if model is an Antigravity model (Gemini/Claude).
    - ONLY sanitizes <model_switch> blocks inside messages where role == 'developer'
      and top-level 'instructions' (if present and contains competitor branding).
    - STRICTLY PRESERVES:
      - All OpenAI / GPT requests (unmodified, 100% byte-exact passthrough).
      - User messages (role == 'user').
      - Assistant messages (role == 'assistant').
      - Tool calls & tool outputs (exec_command, functions, etc.).
      - Non-switch developer messages.
      - Any quotation or mention of GPT-5 by user or assistant.

    Returns (payload, was_modified).
    """
    if not isinstance(payload, dict):
        return payload, False

    model = payload.get("model")
    if not is_antigravity_model(model):
        return payload, False

    was_modified = False
    new_payload = copy.deepcopy(payload)

    # 1. Top-level 'instructions' field if injected at root of Responses API request
    root_instructions = new_payload.get("instructions")
    if isinstance(root_instructions, str):
        sanitized_inst, changed = sanitize_instruction_text(root_instructions)
        if changed:
            new_payload["instructions"] = str(sanitized_inst)
            was_modified = True

    # 2. Input conversation items
    inputs = new_payload.get("input")
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                continue
            # Strictly developer role
            if item.get("role") == "developer":
                content = item.get("content")
                if isinstance(content, str):
                    if "<model_switch>" in content:
                        sanitized = sanitize_model_switch(content)
                        if sanitized != content:
                            item["content"] = sanitized
                            was_modified = True
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            text_val = part.get("text")
                            if isinstance(text_val, str) and "<model_switch>" in text_val:
                                sanitized = sanitize_model_switch(text_val)
                                if sanitized != text_val:
                                    part["text"] = sanitized
                                    was_modified = True

    return new_payload, was_modified


class SanitizerProxyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Reverse Proxy Handler with Real-Time SSE Streaming and Request Sanitization."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard access logs to keep stdout/stderr clean.
        Only log errors or significant warnings on stderr."""
        # Non-200 responses can be logged to stderr if debugging
        if args and len(args) >= 2:
            try:
                status_code = int(args[1])
                if status_code >= 400:
                    warn(f"[{status_code}] {args[0]}")
            except (ValueError, IndexError):
                pass

    def _handle_forward(self, method: str) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b""

        # Intercept and sanitize POST /v1/responses
        path_lower = self.path.lower()
        if method in ("POST", "PUT", "PATCH") and (
            path_lower == "/v1/responses" or path_lower.endswith("/responses")
        ):
            if body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    sanitized_payload, modified = sanitize_request_payload(payload)
                    if modified:
                        body = json.dumps(sanitized_payload, ensure_ascii=False).encode("utf-8")
                except Exception as ex:
                    # If JSON parsing fails, pass through original body untouched
                    warn(f"failed to parse/sanitize request payload: {ex}")

        # Prepare backend connection
        backend_host: str = getattr(self.server, "backend_host", "127.0.0.1")
        backend_port: int = getattr(self.server, "backend_port", 8095)

        # Build clean forward headers
        forward_headers: Dict[str, str] = {}
        for key, val in self.headers.items():
            kl = key.lower()
            if kl in HOP_BY_HOP_HEADERS or kl in ("host", "content-length"):
                continue
            forward_headers[key] = val

        forward_headers["Host"] = f"{backend_host}:{backend_port}"
        if body:
            forward_headers["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPConnection(backend_host, backend_port, timeout=120)
            conn.request(method, self.path, body=body if body else None, headers=forward_headers)
            resp = conn.getresponse()
        except (ConnectionRefusedError, socket.error, OSError) as e:
            self._send_error_response(502, f"Backend proxy unavailable on {backend_host}:{backend_port}: {e}")
            return
        except socket.timeout:
            self._send_error_response(504, "Backend proxy timed out")
            return

        # Forward response status and headers
        self.send_response_only(resp.status, resp.reason)

        has_content_length = False
        is_chunked = False
        for header_name, header_value in resp.getheaders():
            hl = header_name.lower()
            if hl in HOP_BY_HOP_HEADERS:
                continue
            if hl == "content-length":
                has_content_length = True
            self.send_header(header_name, header_value)

        # For streaming responses without explicit Content-Length (like SSE / chunked streams)
        if not has_content_length:
            is_chunked = True
            self.send_header("Transfer-Encoding", "chunked")

        self.end_headers()

        # Stream response body chunk-by-chunk in real-time (ZERO buffering)
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                if is_chunked:
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("latin1") + chunk + b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()

            if is_chunked:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Client closed connection early (e.g. user canceled/interrupted in Codex CLI)
            pass
        except Exception as e:
            warn(f"streaming error during proxy forward: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _send_error_response(self, code: int, message: str) -> None:
        err_body = json.dumps({
            "error": {
                "type": "proxy_error",
                "code": code,
                "message": message,
            }
        }).encode("utf-8")
        self.send_response_only(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(err_body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(err_body)
            self.wfile.flush()
        except Exception:
            pass

    def do_GET(self) -> None:
        self._handle_forward("GET")

    def do_POST(self) -> None:
        self._handle_forward("POST")

    def do_PUT(self) -> None:
        self._handle_forward("PUT")

    def do_DELETE(self) -> None:
        self._handle_forward("DELETE")

    def do_OPTIONS(self) -> None:
        self._handle_forward("OPTIONS")

    def do_HEAD(self) -> None:
        self._handle_forward("HEAD")

    def do_PATCH(self) -> None:
        self._handle_forward("PATCH")


class SanitizerProxyServer(http.server.ThreadingHTTPServer):
    """Threaded HTTP Server for Sanitizer Reverse Proxy with backend binding."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        backend_host: str = "127.0.0.1",
        backend_port: int = 8095,
    ) -> None:
        super().__init__(server_address, SanitizerProxyHandler)
        self.backend_host = backend_host
        self.backend_port = backend_port


def run_sanitizer_server(
    listen_host: str = "127.0.0.1",
    listen_port: int = 8090,
    backend_host: str = "127.0.0.1",
    backend_port: int = 8095,
) -> None:
    server = SanitizerProxyServer((listen_host, listen_port), backend_host, backend_port)

    def _signal_handler(sig: int, frame: Any) -> None:
        info(f"sanitizer proxy received signal {sig}; shutting down...")
        threading.Thread(target=server.shutdown).start()

    try:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
    except (ValueError, AttributeError):
        pass

    info(f"sanitizer proxy listening on {listen_host}:{listen_port} -> backend {backend_host}:{backend_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        info("sanitizer proxy terminated cleanly")


def main() -> int:
    parser = argparse.ArgumentParser(description="AIC Real-Time HTTP Request Sanitizer Reverse Proxy")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to listen on")
    parser.add_argument("--port", type=int, default=8090, help="Public port to listen on (default: 8090)")
    parser.add_argument("--backend-host", default="127.0.0.1", help="Backend host interface")
    parser.add_argument("--backend-port", type=int, default=8095, help="Backend internal port (default: 8095)")

    args = parser.parse_args()
    run_sanitizer_server(
        listen_host=args.host,
        listen_port=args.port,
        backend_host=args.backend_host,
        backend_port=args.backend_port,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
