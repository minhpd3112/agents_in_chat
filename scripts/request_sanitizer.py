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
import urllib.parse
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


ANTIGRAVITY_ALLOWLIST: Set[str] = {
    # Public aliases in Codex CLI models_cache
    "gemini-3.8-flash",
    "claude-sonnet-4.6-thinking",
    # Upstream model IDs in cli-proxy-api & Google Antigravity
    "gemini-3.8-flash-high",
    "claude-sonnet-4-6",
    # Additional Antigravity Gemini/Claude upstream IDs if configured
    "gemini-3.7-flash",
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-thinking",
    "gemini-3.7-pro",
    "claude-opus-4-6",
    "claude-opus-4-6-thinking",
    "claude-3-7-sonnet",
}

# Backward compatibility alias
ANTIGRAVITY_KNOWN_MODELS = ANTIGRAVITY_ALLOWLIST

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
    """Return True strictly if the model is in the explicit Antigravity allowlist.

    Strict Contract:
    - ONLY returns True for exact allowed public aliases or upstream IDs.
    - Never uses substring matching (no 'gemini in ...' or 'claude in ...').
    - OpenAI / GPT models and unknown / competitor models containing 'claude'
      MUST return False and be forwarded byte-exact without modification.
    """
    if not model_name or not isinstance(model_name, str):
        return False
    return model_name.strip().lower() in ANTIGRAVITY_ALLOWLIST


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

    def _send_error_response(self, code: int, message: str, error_type: str = "invalid_request_error") -> None:
        err_body = json.dumps({
            "error": {
                "type": error_type,
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

    def _handle_forward(self, method: str) -> None:
        parsed_url = urllib.parse.urlsplit(self.path)
        normalized_path = parsed_url.path

        content_len_hdr = self.headers.get("Content-Length")
        body: bytes = b""

        # Intercept and sanitize POST /v1/responses (exact path, query params allowed)
        if method == "POST" and normalized_path == "/v1/responses":
            # Fail-closed check 1: Content-Encoding
            content_encoding = self.headers.get("Content-Encoding")
            if content_encoding and content_encoding.strip().lower() not in ("", "identity"):
                self._send_error_response(415, "Unsupported Content-Encoding")
                return

            # Fail-closed check 2: Content-Length
            if content_len_hdr is None or not content_len_hdr.strip().isdigit():
                self._send_error_response(400, "Missing or invalid Content-Length")
                return

            expected_len = int(content_len_hdr.strip())
            if expected_len <= 0:
                self._send_error_response(400, "Empty request body")
                return

            body = self.rfile.read(expected_len)
            if len(body) < expected_len:
                self._send_error_response(400, "Incomplete request body")
                return

            # Fail-closed check 3: JSON parsing & structure
            try:
                decoded_str = body.decode("utf-8")
                payload = json.loads(decoded_str)
            except UnicodeDecodeError:
                self._send_error_response(400, "Invalid UTF-8 encoding in request body")
                return
            except json.JSONDecodeError:
                self._send_error_response(400, "Malformed JSON in request body")
                return

            if not isinstance(payload, dict):
                self._send_error_response(400, "JSON payload root must be an object")
                return

            # Strict Model Matching: Only sanitize if model is in ANTIGRAVITY_ALLOWLIST
            model = payload.get("model")
            if is_antigravity_model(model):
                sanitized_payload, modified = sanitize_request_payload(payload)
                if modified:
                    body = json.dumps(sanitized_payload, ensure_ascii=False).encode("utf-8")
            # If not an Antigravity model (e.g. GPT/OpenAI, unknown, competitor), keep original raw bytes!
        else:
            # For all other paths / methods: pass through raw body if present
            if content_len_hdr and content_len_hdr.strip().isdigit():
                content_len = int(content_len_hdr.strip())
                if content_len > 0:
                    body = self.rfile.read(content_len)

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
            self._send_error_response(502, f"Backend proxy unavailable on {backend_host}:{backend_port}: {e}", error_type="proxy_error")
            return
        except socket.timeout:
            self._send_error_response(504, "Backend proxy timed out", error_type="proxy_error")
            return

        # Forward response status and headers
        try:
            self.send_response_only(resp.status, resp.reason)

            # HEAD, 204, and 304 responses MUST NOT include a message body
            no_body_status = (method == "HEAD" or resp.status in (204, 304))

            has_content_length = False
            for header_name, header_value in resp.getheaders():
                hl = header_name.lower()
                if hl in HOP_BY_HOP_HEADERS:
                    continue
                if hl == "content-length":
                    has_content_length = True
                self.send_header(header_name, header_value)

            is_chunked = False
            if not no_body_status and not has_content_length:
                is_chunked = True
                self.send_header("Transfer-Encoding", "chunked")

            self.end_headers()

            if no_body_status:
                return

            # Real-Time Streaming: Use read1() to read immediately available bytes without waiting for 4096 bytes
            try:
                while True:
                    chunk = resp.read1(4096)
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
            except (BrokenPipeError, ConnectionResetError, socket.error):
                # Client closed connection early (e.g. user canceled/interrupted in Codex CLI)
                pass
            except Exception as e:
                warn(f"streaming error during proxy forward: {e}")
        finally:
            try:
                conn.close()
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

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Suppress socket errors (ConnectionResetError, BrokenPipeError) when client disconnects early."""
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        # For other unexpected errors, log concise message without traceback spam
        if exc_val:
            warn(f"server handler error from {client_address}: {exc_val}")


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
