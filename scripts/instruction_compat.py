#!/usr/bin/env python3
"""Instruction compatibility and schema-aware sanitization for Codex.

Provides:
- Exact forbidden competitor fingerprint definitions.
- Schema-aware sanitization (only harness fields & <model_switch> developer blocks).
- Safe-guarding of user/assistant/tool messages and non-harness developer text.
- Template structural integrity verification.
"""

import re
from typing import Any, Dict, List, Tuple

# Exact competitor branding fingerprints and their neutral replacements
EXACT_FORBIDDEN_FINGERPRINTS: List[Tuple[str, str]] = [
    ("You are Codex, a coding agent based on GPT-5.", "You are Codex, an expert coding agent."),
    ("You are Codex, a coding agent based on GPT-5", "You are Codex, an expert coding agent"),
    ("You are Codex, an agent based on GPT-5.", "You are Codex, an expert coding agent."),
    ("You are Codex, an agent based on GPT-5", "You are Codex, an expert coding agent"),
]

# Structural markers required in full Codex instruction templates
REQUIRED_TEMPLATE_MARKERS = [
    "# Personality",
    "## Writing style",
    "# Rules for getting work done",
    "## Final answer",
]


class SanitizedText(str):
    """String subclass that also behaves as a 2-tuple (text, was_modified)
    when unpacked, supporting both str and tuple call contracts."""

    def __new__(cls, val: str, was_modified: bool = False):
        obj = str.__new__(cls, val)
        obj.was_modified = was_modified
        return obj

    def __iter__(self):
        return iter((str(self), self.was_modified))


def sanitize_instruction_text(text: str) -> SanitizedText:
    """Sanitize only exact forbidden fingerprints in a text string.
    Does NOT perform indiscriminate replacement of partial words."""
    if not isinstance(text, str) or not text:
        return SanitizedText(text, False)

    modified = False
    result = text
    for forbidden, replacement in EXACT_FORBIDDEN_FINGERPRINTS:
        if forbidden in result:
            result = result.replace(forbidden, replacement)
            modified = True

    if "based on GPT-5" in result:
        result = result.replace("based on GPT-5", "an expert coding agent")
        modified = True

    return SanitizedText(result, modified)


def sanitize_model_switch(text: str) -> str:
    """Sanitize instructions inside <model_switch>...</model_switch> blocks,
    leaving any surrounding content untouched."""
    if not isinstance(text, str) or "<model_switch>" not in text:
        return text

    def _replace_block(m: re.Match) -> str:
        content = m.group(1)
        sanitized_content, _ = sanitize_instruction_text(content)
        return f"<model_switch>{sanitized_content}</model_switch>"

    return re.sub(r"<model_switch>([\s\S]*?)</model_switch>", _replace_block, text)


def sanitize_session_item(item: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Sanitize an item from a rollout session JSONL file with strict schema awareness.

    ONLY sanitizes:
    1. session_meta: payload.base_instructions (str or dict with "text"), payload.instructions (str)
    2. turn_context: payload.instructions (str), payload.base_instructions (str or dict with "text")
    3. response_item or message: developer messages containing <model_switch> blocks

    STRICTLY PRESERVES:
    - User messages (role == 'user')
    - Assistant messages (role == 'assistant')
    - Tool call arguments & outputs (custom_tool_call, function_call, etc.)
    - Developer messages outside <model_switch> blocks
    - Any quotes/citations of forbidden strings in user/assistant content

    Returns (item, was_modified).
    """
    if not isinstance(item, dict):
        return item, False

    item_type = item.get("type")
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}

    was_modified = False

    # 1. session_meta
    if item_type == "session_meta" and payload:
        base_inst = payload.get("base_instructions")
        if isinstance(base_inst, str):
            sanitized, changed = sanitize_instruction_text(base_inst)
            if changed:
                payload["base_instructions"] = str(sanitized)
                was_modified = True
        elif isinstance(base_inst, dict):
            text_val = base_inst.get("text")
            if isinstance(text_val, str):
                sanitized, changed = sanitize_instruction_text(text_val)
                if changed:
                    base_inst["text"] = str(sanitized)
                    was_modified = True

        inst = payload.get("instructions")
        if isinstance(inst, str):
            sanitized, changed = sanitize_instruction_text(inst)
            if changed:
                payload["instructions"] = str(sanitized)
                was_modified = True

    # 2. turn_context
    elif item_type == "turn_context" and payload:
        inst = payload.get("instructions")
        if isinstance(inst, str):
            sanitized, changed = sanitize_instruction_text(inst)
            if changed:
                payload["instructions"] = str(sanitized)
                was_modified = True

        base_inst = payload.get("base_instructions")
        if isinstance(base_inst, str):
            sanitized, changed = sanitize_instruction_text(base_inst)
            if changed:
                payload["base_instructions"] = str(sanitized)
                was_modified = True
        elif isinstance(base_inst, dict):
            text_val = base_inst.get("text")
            if isinstance(text_val, str):
                sanitized, changed = sanitize_instruction_text(text_val)
                if changed:
                    base_inst["text"] = str(sanitized)
                    was_modified = True

    # 3. developer message with <model_switch> (either wrapped in response_item or direct)
    msg = None
    if item_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "developer":
        msg = payload
    elif (item_type == "message" or item.get("role") == "developer") and item.get("role") == "developer":
        msg = item

    if msg is not None:
        content = msg.get("content")
        if isinstance(content, str):
            if "<model_switch>" in content:
                sanitized = sanitize_model_switch(content)
                if sanitized != content:
                    msg["content"] = sanitized
                    was_modified = True
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part_text = part["text"]
                    if "<model_switch>" in part_text:
                        sanitized = sanitize_model_switch(part_text)
                        if sanitized != part_text:
                            part["text"] = sanitized
                            was_modified = True

    return item, was_modified


def has_unsanitized_fingerprint(item: Dict[str, Any]) -> bool:
    """Schema-aware check for un-sanitized competitor fingerprints in harness fields.

    Uses the exact same schema boundary as sanitize_session_item.
    Returns True ONLY if an un-sanitized fingerprint is found in:
    - session_meta instructions / base_instructions
    - turn_context instructions / base_instructions
    - developer message <model_switch> blocks

    Returns False for user/assistant messages and non-switch developer messages.
    """
    if not isinstance(item, dict):
        return False

    item_type = item.get("type")
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}

    def _check_string(s: str) -> bool:
        if not isinstance(s, str):
            return False
        for forbidden, _ in EXACT_FORBIDDEN_FINGERPRINTS:
            if forbidden in s:
                return True
        return "based on GPT-5" in s

    if item_type == "session_meta":
        base_inst = payload.get("base_instructions")
        if isinstance(base_inst, str) and _check_string(base_inst):
            return True
        elif isinstance(base_inst, dict) and _check_string(base_inst.get("text")):
            return True
        if isinstance(payload.get("instructions"), str) and _check_string(payload.get("instructions")):
            return True

    elif item_type == "turn_context":
        if isinstance(payload.get("instructions"), str) and _check_string(payload.get("instructions")):
            return True
        base_inst = payload.get("base_instructions")
        if isinstance(base_inst, str) and _check_string(base_inst):
            return True
        elif isinstance(base_inst, dict) and _check_string(base_inst.get("text")):
            return True

    # Developer messages
    msg = None
    if item_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "developer":
        msg = payload
    elif (item_type == "message" or item.get("role") == "developer") and item.get("role") == "developer":
        msg = item

    if msg is not None:
        content = msg.get("content")
        if isinstance(content, str) and "<model_switch>" in content:
            m = re.search(r"<model_switch>([\s\S]*?)</model_switch>", content)
            if m and _check_string(m.group(1)):
                return True
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part_text = part["text"]
                    if "<model_switch>" in part_text:
                        m = re.search(r"<model_switch>([\s\S]*?)</model_switch>", part_text)
                        if m and _check_string(m.group(1)):
                            return True

    return False


def verify_instruction_template(template_str: str) -> Tuple[bool, str]:
    """Verify that a model instruction template is valid, non-truncated,
    possesses structural markers, and has no forbidden competitor fingerprints."""
    if not template_str or not isinstance(template_str, str):
        return False, "template is missing or empty"

    if len(template_str) < 1000:
        return False, f"template is suspiciously short ({len(template_str)} chars)"

    matched_markers = [m for m in REQUIRED_TEMPLATE_MARKERS if m in template_str]
    if len(matched_markers) < 2:
        return False, f"template is missing structural markers (found {len(matched_markers)}/4)"

    for forbidden, _ in EXACT_FORBIDDEN_FINGERPRINTS:
        if forbidden in template_str:
            return False, f"template contains forbidden fingerprint: '{forbidden}'"

    if "based on GPT-5" in template_str:
        return False, "template contains 'based on GPT-5'"

    return True, "template is valid and structurally intact"
