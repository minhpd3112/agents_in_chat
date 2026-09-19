#!/usr/bin/env python3
# ==============================================================================
#  test_request_sanitizer_unit.py - Unit Tests for HTTP Request Sanitizer
#  Python stdlib-only.
# ==============================================================================

import os
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from request_sanitizer import (
    is_antigravity_model,
    sanitize_request_payload,
)


class TestRequestSanitizerUnit(unittest.TestCase):

    def test_01_gemini_model_switch_sanitized(self):
        """Case 1: Gemini model with toxic <model_switch> must be sanitized."""
        payload = {
            "model": "gemini-3.8-flash",
            "stream": True,
            "input": [
                {
                    "role": "developer",
                    "content": (
                        "Instructions:\n"
                        "<model_switch>\n"
                        "The user was previously using a different model.\n"
                        "You are Codex, a coding agent based on GPT-5.\n"
                        "</model_switch>"
                    )
                },
                {"role": "user", "content": "hello"}
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertTrue(modified)
        dev_content = sanitized["input"][0]["content"]
        self.assertNotIn("based on GPT-5", dev_content)
        self.assertIn("an expert coding agent", dev_content)
        self.assertIn("<model_switch>", dev_content)
        self.assertIn("</model_switch>", dev_content)

    def test_02_claude_model_switch_sanitized(self):
        """Case 2: Claude Sonnet with toxic <model_switch> must be sanitized."""
        payload = {
            "model": "claude-sonnet-4.6-thinking",
            "stream": True,
            "input": [
                {
                    "role": "developer",
                    "content": "<model_switch>\nYou are Codex, an agent based on GPT-5.\n</model_switch>"
                }
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertTrue(modified)
        dev_content = sanitized["input"][0]["content"]
        self.assertNotIn("based on GPT-5", dev_content)
        self.assertIn("an expert coding agent", dev_content)

    def test_03_openai_gpt_requests_untouched(self):
        """Case 3: OpenAI GPT models (Sol, Terra, Luna, Astra) must NEVER be touched."""
        payload = {
            "model": "gpt-5.6-sol",
            "stream": True,
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {
                    "role": "developer",
                    "content": "<model_switch>\nYou are Codex, an agent based on GPT-5.\n</model_switch>"
                },
                {"role": "user", "content": "Explain GPT-5 architecture"}
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertFalse(modified)
        self.assertEqual(sanitized, payload)
        self.assertIn("based on GPT-5", sanitized["instructions"])
        self.assertIn("based on GPT-5", sanitized["input"][0]["content"])

    def test_04_user_message_preserved(self):
        """Case 4: User messages mentioning competitor branding must be 100% preserved."""
        payload = {
            "model": "gemini-3.8-flash",
            "input": [
                {"role": "user", "content": "Tell me why someone wrote 'based on GPT-5' here."}
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertFalse(modified)
        self.assertEqual(sanitized["input"][0]["content"], "Tell me why someone wrote 'based on GPT-5' here.")

    def test_05_assistant_message_preserved(self):
        """Case 5: Assistant messages mentioning competitor branding must be preserved."""
        payload = {
            "model": "claude-sonnet-4.6-thinking",
            "input": [
                {"role": "assistant", "content": "I am not based on GPT-5, I am Claude."}
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertFalse(modified)
        self.assertEqual(sanitized["input"][0]["content"], "I am not based on GPT-5, I am Claude.")

    def test_06_tool_calls_and_outputs_preserved(self):
        """Case 6: Tool calls and output items must never be touched."""
        payload = {
            "model": "gemini-3.8-flash",
            "input": [
                {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": '{"cmd": "git log --grep=\\"based on GPT-5\\""}'
                },
                {
                    "type": "function_call_output",
                    "output": "commit 1234: fixed string based on GPT-5"
                }
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertFalse(modified)
        self.assertEqual(sanitized, payload)

    def test_07_non_switch_developer_message_preserved(self):
        """Case 7: Developer messages without <model_switch> must not be altered."""
        payload = {
            "model": "gemini-3.8-flash",
            "input": [
                {"role": "developer", "content": "Operating environment: Windows 11 x64, sandbox: elevated"}
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertFalse(modified)
        self.assertEqual(sanitized, payload)

    def test_08_array_content_parts_sanitized(self):
        """Case 8: Developer message with array content parts must be sanitized."""
        payload = {
            "model": "gemini-3.8-flash",
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "<model_switch>\nYou are Codex, a coding agent based on GPT-5.\n</model_switch>"
                        }
                    ]
                }
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertTrue(modified)
        part_text = sanitized["input"][0]["content"][0]["text"]
        self.assertNotIn("based on GPT-5", part_text)
        self.assertIn("an expert coding agent", part_text)

    def test_09_top_level_instructions_sanitized(self):
        """Case 9: Top-level instructions containing forbidden strings must be sanitized."""
        payload = {
            "model": "gemini-3.8-flash",
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [{"role": "user", "content": "hi"}]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertTrue(modified)
        self.assertEqual(sanitized["instructions"], "You are Codex, an expert coding agent.")

    def test_10_idempotency_of_sanitization(self):
        """Case 10: Re-sanitizing an already sanitized payload returns modified == False."""
        payload = {
            "model": "gemini-3.8-flash",
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {
                    "role": "developer",
                    "content": "<model_switch>\nYou are Codex, a coding agent based on GPT-5.\n</model_switch>"
                }
            ]
        }
        pass1, mod1 = sanitize_request_payload(payload)
        self.assertTrue(mod1)

        pass2, mod2 = sanitize_request_payload(pass1)
        self.assertFalse(mod2)
        self.assertEqual(pass1, pass2)

    def test_11_fake_claude_model_not_in_allowlist_untouched(self):
        """Case 11: Competitor / unknown model containing 'claude' must NOT be sanitized."""
        payload = {
            "model": "fake-claude-experimental-v9",
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {"role": "developer", "content": "<model_switch>\nbased on GPT-5\n</model_switch>"}
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertFalse(modified)
        self.assertEqual(sanitized, payload)
        self.assertFalse(is_antigravity_model("fake-claude-experimental-v9"))

    def test_12_fake_gemini_model_not_in_allowlist_untouched(self):
        """Case 12: Competitor / unknown model containing 'gemini' must NOT be sanitized."""
        payload = {
            "model": "my-custom-gemini-server",
            "instructions": "You are Codex, an agent based on GPT-5.",
            "input": [
                {"role": "developer", "content": "<model_switch>\nbased on GPT-5\n</model_switch>"}
            ]
        }
        sanitized, modified = sanitize_request_payload(payload)
        self.assertFalse(modified)
        self.assertEqual(sanitized, payload)
        self.assertFalse(is_antigravity_model("my-custom-gemini-server"))

    def test_13_allowlist_exact_models(self):
        """Case 13: Exact known public aliases and upstream IDs must be recognized."""
        for m in ["gemini-3.8-flash", "claude-sonnet-4.6-thinking", "gemini-3.8-flash-high", "claude-sonnet-4-6"]:
            self.assertTrue(is_antigravity_model(m), f"Expected {m} to be recognized in allowlist")
            self.assertTrue(is_antigravity_model(m.upper()), f"Expected case-insensitive {m.upper()} to be recognized")

    def test_14_non_antigravity_models_rejected(self):
        """Case 14: Non-antigravity models (GPT, Muse, None, empty) must return False."""
        for m in ["gpt-5.6-sol", "gpt-6-astra", "muse-spark-1.3", None, "", 123]:
            self.assertFalse(is_antigravity_model(m), f"Expected {m} to NOT be in allowlist")

    def test_15_invalid_payload_types_handled_safely(self):
        """Case 15: Non-dict payload returns unchanged with modified == False."""
        for invalid_payload in [None, "hello", [1, 2, 3]]:
            res, mod = sanitize_request_payload(invalid_payload)
            self.assertFalse(mod)
            self.assertEqual(res, invalid_payload)


def test_request_sanitizer_unit():
    """Runner function for integration into run_tests.py."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRequestSanitizerUnit)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=0)
    result = runner.run(suite)
    if result.wasSuccessful():
        return True, f"All {result.testsRun} unit test cases for request sanitizer PASSED!"
    return False, f"Unit tests failed: {len(result.failures)} failures, {len(result.errors)} errors."


if __name__ == "__main__":
    unittest.main()
