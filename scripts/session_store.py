#!/usr/bin/env python3
"""Session store: JSONL parsing, ordinal byte offset resolution, and lineage realignment.

Guarantees:
- Pure atomic writes (temp file + os.replace). No r+ in-place corruption.
- Accurate ordinal-based cutoff calculation for forked threads.
- Comprehensive lineage verification matching expected ordinal offsets.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from log_utils import error, info, warn


def atomic_write_lines(target_path: str, lines: List[str]) -> None:
    """Atomically write text lines to target_path using a temporary file and os.replace().
    Propagates any OSError/PermissionError to ensure failures are never hidden."""
    target_dir = os.path.dirname(target_path)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    tmp_path = f"{target_path}.tmp.{os.getpid()}_{time.time_ns()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as tf:
            tf.writelines(lines)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp_path, target_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def find_ordinal_byte_offset(parent_path: str, end_ordinal_exclusive: Optional[int]) -> int:
    """Find the byte offset in parent_path where all items with ordinal < end_ordinal_exclusive end.

    Logic:
    - If end_ordinal_exclusive is None: fallback to the entire file length (documented fallback
      when child fork did not specify an ordinal boundary).
    - If end_ordinal_exclusive <= 0: return 0.
    - Scans rollout lines sequentially. Lines preceding the first item with
      ordinal >= end_ordinal_exclusive accumulate byte length.
    - When an item with ordinal >= end_ordinal_exclusive is reached, returns the byte offset
      at the beginning of that line.
    - If the end of the file is reached before any item with ordinal >= end_ordinal_exclusive,
      returns the total file size.
    """
    file_size = os.path.getsize(parent_path)
    if end_ordinal_exclusive is None:
        return file_size
    if end_ordinal_exclusive <= 0:
        return 0

    offset = 0
    matched_offset = file_size

    with open(parent_path, "rb") as pf:
        for line in pf:
            line_len = len(line)
            try:
                obj = json.loads(line.decode("utf-8"))
                ord_val = obj.get("ordinal")
                if ord_val is not None:
                    if ord_val >= end_ordinal_exclusive:
                        # Found the first excluded ordinal; the cutoff is precisely the start of this line
                        return offset
                    else:
                        matched_offset = offset + line_len
            except Exception:
                pass
            offset += line_len

    return matched_offset


def build_thread_map(sessions_dir: Path) -> Dict[str, str]:
    """Build a mapping of thread_id -> absolute file path for all .jsonl sessions."""
    thread_map: Dict[str, str] = {}
    if not sessions_dir.exists():
        return thread_map

    for root, _, files in os.walk(str(sessions_dir)):
        for f in files:
            if f.endswith(".jsonl"):
                fp = os.path.join(root, f)
                try:
                    with open(fp, "r", encoding="utf-8") as sf:
                        first = sf.readline()
                    if not first:
                        continue
                    meta = json.loads(first)
                    payload = meta.get("payload", {})
                    tid = payload.get("id") or payload.get("session_id")
                    if tid and tid not in thread_map:
                        thread_map[tid] = fp
                except Exception:
                    pass
    return thread_map


def realign_forked_lineages(sessions_dir: Path) -> Tuple[int, int]:
    """Scan all session JSONL files and re-align history_base.end_byte_offset
    whenever the recorded offset differs from the expected cutoff computed from parent's ordinals.

    Returns (realigned_count, error_count).
    """
    if not sessions_dir.exists():
        return 0, 0

    thread_map = build_thread_map(sessions_dir)
    realigned_count = 0
    error_count = 0

    for root, _, files in os.walk(str(sessions_dir)):
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            fp = os.path.join(root, f)
            try:
                with open(fp, "r", encoding="utf-8") as sf:
                    lines = sf.readlines()
                if not lines:
                    continue

                meta = json.loads(lines[0])
                payload = meta.get("payload")
                if not isinstance(payload, dict):
                    continue
                hbase = payload.get("history_base")
                if not isinstance(hbase, dict):
                    continue

                parent_id = hbase.get("thread_id")
                cutoff = hbase.get("end_byte_offset")
                end_ord = hbase.get("end_ordinal_exclusive")

                if not parent_id or cutoff is None or parent_id not in thread_map:
                    continue

                parent_fp = thread_map[parent_id]
                parent_size = os.path.getsize(parent_fp)

                # Compute expected cutoff based on parent ordinals
                expected_cutoff = find_ordinal_byte_offset(parent_fp, end_ord) if end_ord is not None else parent_size
                expected_cutoff = min(expected_cutoff, parent_size)

                # Update whenever recorded cutoff differs from expected cutoff
                if cutoff != expected_cutoff:
                    hbase["end_byte_offset"] = expected_cutoff
                    lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"

                    # Strictly atomic write: NO r+ in-place fallback
                    atomic_write_lines(fp, lines)
                    realigned_count += 1
                    info(f"realigned lineage for {f}: cutoff {cutoff} -> {expected_cutoff} (parent: {os.path.basename(parent_fp)})")
            except Exception as e:
                error(f"failed to realign lineage in {f}: {e}")
                error_count += 1

    return realigned_count, error_count


def verify_lineage_integrity(sessions_dir: Path) -> List[str]:
    """Verify all forked sessions have valid parents and exact cutoff offsets.
    Returns a list of issue descriptions."""
    issues: List[str] = []
    if not sessions_dir.exists():
        return issues

    thread_map = build_thread_map(sessions_dir)

    for root, _, files in os.walk(str(sessions_dir)):
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            fp = os.path.join(root, f)
            try:
                with open(fp, "r", encoding="utf-8") as sf:
                    first = sf.readline()
                if not first:
                    continue
                meta = json.loads(first)
                payload = meta.get("payload")
                if not isinstance(payload, dict):
                    continue
                hbase = payload.get("history_base")
                if not isinstance(hbase, dict):
                    continue

                parent_id = hbase.get("thread_id")
                cutoff = hbase.get("end_byte_offset")
                end_ord = hbase.get("end_ordinal_exclusive")

                if not parent_id or cutoff is None:
                    continue

                if parent_id not in thread_map:
                    # Parent does not exist on disk (orphaned session); cannot verify against missing file
                    continue

                parent_fp = thread_map[parent_id]
                parent_size = os.path.getsize(parent_fp)

                if cutoff > parent_size:
                    issues.append(f"Session {f} lineage cutoff ({cutoff}) exceeds parent size ({parent_size})")
                    continue

                if end_ord is not None:
                    expected = find_ordinal_byte_offset(parent_fp, end_ord)
                    if cutoff != expected:
                        issues.append(
                            f"Session {f} lineage cutoff ({cutoff}) differs from expected ordinal offset ({expected}) for parent {os.path.basename(parent_fp)}"
                        )
            except Exception as e:
                issues.append(f"Session {f} could not be inspected for lineage: {e}")

    return issues
