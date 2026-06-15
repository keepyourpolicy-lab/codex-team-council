#!/usr/bin/env python3
"""Run a multi-model /team council with SOT guardrails and adversarial pass."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import textwrap
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX fallback
    msvcrt = None  # type: ignore[assignment]


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_DIR / "references" / "roster.example.json"
USER_CONFIG = Path.home() / ".codex" / "team" / "roster.json"
DEFAULT_RUN_ROOT = Path("/tmp/team-council-runs")
OPENCODE_LOCK_PATH = Path("/tmp/team-council-opencode.lock")
OPENCODE_THREAD_LOCK = threading.Lock()
DEFAULT_KNOWLEDGE_TRANSFER = {
    "capsules": True,
    "capsule_model_id": "deepseek-v4-pro",
    "allow_archivist_outside_selected": False,
    "capsule_input_max_chars": 160000,
    "capsule_chunk_overlap_chars": 2000,
    "capsule_max_output_chars": 90000,
    "pack_raw_excerpt_chars": 70000,
    "pack_max_chars": 320000,
    "route_peer_knowledge_to_round2": True,
    "route_round_knowledge_to_final": True,
}
KIMI_NATIVE_ARG_MAX_CHARS = 120000
KIMI_COUNCIL_DENY_TOOLS = [
    "Agent",
    "AgentSwarm",
    "Write",
    "Edit",
    "Bash",
    "WebSearch",
    "FetchURL",
    "WriteFile",
    "StrReplaceFile",
    "Shell",
    "SearchWeb",
    "EnterPlanMode",
    "ExitPlanMode",
]


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s'\"`]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"),
    re.compile(r"\b([A-Za-z0-9_=-]{32,}\.[A-Za-z0-9_=-]{16,}\.[A-Za-z0-9_=-]{16,})\b"),
]


def redact(text: str) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            result = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(text), encoding="utf-8")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(json.dumps(payload, indent=2, sort_keys=True)), encoding="utf-8")


def run_cmd(
    args: List[str],
    cwd: Path,
    timeout: Optional[float] = None,
    input_text: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_text,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return
        if msvcrt is not None:
            if handle.tell() == 0:
                handle.write("\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        yield


@contextlib.contextmanager
def opencode_lock() -> Iterator[None]:
    with OPENCODE_THREAD_LOCK:
        with file_lock(OPENCODE_LOCK_PATH):
            yield


def is_opencode_db_locked(proc: subprocess.CompletedProcess[str]) -> bool:
    if proc.returncode == 0:
        return False
    text = "\n".join(part for part in [proc.stdout or "", proc.stderr or ""] if part)
    return "database is locked" in text.lower()


def run_opencode_cmd(args: List[str], cwd: Path, input_text: str) -> subprocess.CompletedProcess[str]:
    last_proc: Optional[subprocess.CompletedProcess[str]] = None
    for wait_seconds in [0.0, 1.0, 2.0, 4.0]:
        if wait_seconds:
            time.sleep(wait_seconds)
        with opencode_lock():
            proc = run_cmd(args, cwd=cwd, input_text=input_text)
        last_proc = proc
        if not is_opencode_db_locked(proc):
            return proc

    assert last_proc is not None
    retry_note = "\nOpenCode database remained locked after serialized retries."
    return subprocess.CompletedProcess(
        last_proc.args,
        last_proc.returncode,
        stdout=last_proc.stdout,
        stderr=(last_proc.stderr or "") + retry_note,
    )


def git_value(cwd: Path, args: List[str]) -> str:
    try:
        proc = run_cmd(["git", *args], cwd=cwd, timeout=10)
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def git_multiline(cwd: Path, args: List[str], limit: int = 16000) -> str:
    value = git_value(cwd, args)
    if len(value) > limit:
        return value[:limit] + "\n[truncated]\n"
    return value


def detect_sot(cwd: Path, explicit_path: Optional[str], explicit_label: Optional[str]) -> Dict[str, Any]:
    sot_path = Path(explicit_path).expanduser().resolve() if explicit_path else cwd.resolve()
    git_root_text = git_value(sot_path, ["rev-parse", "--show-toplevel"])
    git_root = Path(git_root_text).resolve() if git_root_text else sot_path
    branch = git_value(git_root, ["branch", "--show-current"])
    commit = git_value(git_root, ["rev-parse", "HEAD"])
    status = git_multiline(git_root, ["status", "--short"], limit=20000)
    worktrees_raw = git_multiline(git_root, ["worktree", "list", "--porcelain"], limit=30000)
    forbidden: List[str] = []
    current = str(git_root)
    current_sot = str(sot_path)
    for line in worktrees_raw.splitlines():
        if not line.startswith("worktree "):
            continue
        path = line.removeprefix("worktree ").strip()
        try:
            resolved = str(Path(path).resolve())
        except Exception:
            resolved = path
        if resolved not in {current, current_sot}:
            forbidden.append(resolved)

    return {
        "label": explicit_label or "current declared SOT worktree",
        "sot_path": str(sot_path),
        "git_root": str(git_root),
        "branch": branch,
        "commit": commit,
        "status_short": status,
        "forbidden_worktrees": forbidden,
    }


def load_config(path: Optional[str]) -> Tuple[Dict[str, Any], Path]:
    selected = Path(path).expanduser() if path else (USER_CONFIG if USER_CONFIG.exists() else DEFAULT_CONFIG)
    return read_json(selected), selected


def init_user_config() -> Path:
    if USER_CONFIG.exists():
        raise SystemExit(f"User config already exists, refusing to overwrite: {USER_CONFIG}")
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG.write_text(DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    return USER_CONFIG


def normalize_ids(raw: Optional[str]) -> Optional[set[str]]:
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def validate_binary(model: Dict[str, Any]) -> Optional[str]:
    if model.get("adapter") == "kimi-openai":
        if has_api_key_source(model):
            return None
        return "missing API key env/file"
    binary = model.get("binary")
    if not binary:
        return "missing binary path"
    expanded = os.path.expanduser(str(binary))
    if not Path(expanded).exists():
        found = shutil.which(str(binary))
        if not found:
            return f"binary not found: {binary}"
    if model.get("api_key_target_env"):
        if has_api_key_source(model):
            return None
        return f"missing API key source for {model['api_key_target_env']}"
    return None


def truncate_output(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return "[omitted by zero-character limit]"
    if len(text) <= max_chars:
        return text
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    return (
        text[:head_chars]
        + f"\n\n[omitted middle; preserved head and tail within {max_chars} chars]\n\n"
        + text[-tail_chars:]
    )


def block(text: str) -> str:
    return textwrap.dedent(text).strip()


def indent(text: str, prefix: str = "  ") -> str:
    if not text:
        return ""
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def command_preview(args: List[str]) -> List[str]:
    preview: List[str] = []
    for item in args:
        if "\n" in item or len(item) > 220:
            preview.append("[long prompt/input omitted]")
        else:
            preview.append(item)
    return preview[:16]


def knowledge_settings(run_settings: Dict[str, Any]) -> Dict[str, Any]:
    configured = run_settings.get("knowledge_transfer") or {}
    if not isinstance(configured, dict):
        configured = {}
    return {**DEFAULT_KNOWLEDGE_TRANSFER, **configured}


def truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-") or "model"


def kimi_native_config(model: Dict[str, Any]) -> str:
    alias = str(model.get("model") or "kimi-code/kimi-for-coding")
    provider_model = str(model.get("provider_model") or alias.split("/")[-1] or "kimi-for-coding")
    context_size = int(model.get("max_context_size", 262144))
    deny_rules = "\n".join(
        block(
            f"""
            [[permission.rules]]
            decision = "deny"
            pattern = "{tool}"
            reason = "Codex Team Council Kimi worker runs as one read-only model; this tool is disabled for council mode."
            """
        )
        for tool in KIMI_COUNCIL_DENY_TOOLS
    )
    return block(
        f"""
        default_model = "{alias}"
        default_thinking = true
        default_permission_mode = "auto"
        default_plan_mode = false
        merge_all_available_skills = false
        telemetry = false

        [providers."managed:kimi-code"]
        type = "kimi"
        api_key = ""
        base_url = "https://api.kimi.com/coding/v1"

        [providers."managed:kimi-code".oauth]
        storage = "file"
        key = "oauth/kimi-code"

        [models."{alias}"]
        provider = "managed:kimi-code"
        model = "{provider_model}"
        max_context_size = {context_size}
        capabilities = [ "thinking", "always_thinking", "image_in", "video_in", "tool_use" ]
        display_name = "K2.7 Code"

        [services.moonshot_search]
        base_url = "https://api.kimi.com/coding/v1/search"
        api_key = ""

        [services.moonshot_search.oauth]
        storage = "file"
        key = "oauth/kimi-code"

        [services.moonshot_fetch]
        base_url = "https://api.kimi.com/coding/v1/fetch"
        api_key = ""

        [services.moonshot_fetch.oauth]
        storage = "file"
        key = "oauth/kimi-code"

        [loop_control]
        max_retries_per_step = 2
        reserved_context_size = 50000

        [background]
        max_running_tasks = 1
        keep_alive_on_exit = false

        {deny_rules}
        """
    ) + "\n"


def prepare_kimi_code_home(model: Dict[str, Any], run_dir: Path, adapter_id: str) -> Path:
    source_home = Path(os.environ.get("KIMI_CODE_HOME") or (Path.home() / ".kimi-code")).expanduser()
    source_credentials = source_home / "credentials"
    if not source_credentials.exists():
        raise RuntimeError(f"Kimi credentials directory not found at {source_credentials}")

    home = run_dir / "runtime" / "kimi-code-home" / safe_name(adapter_id)
    home.mkdir(parents=True, exist_ok=True)
    write_text(home / "config.toml", kimi_native_config(model))
    (home / "empty-skills").mkdir(parents=True, exist_ok=True)

    credentials_link = home / "credentials"
    if credentials_link.exists() or credentials_link.is_symlink():
        return home
    os.symlink(source_credentials, credentials_link, target_is_directory=True)
    return home


def kimi_council_prompt(prompt: str) -> str:
    return block(
        """
        Kimi council runtime policy for this invocation:
        - You are exactly one floor worker in a larger council. Do not spawn or simulate an internal council.
        - Do not call Agent or AgentSwarm. Subagents are disabled because they independently consume Kimi quota and duplicate the council architecture.
        - Do not use Bash, WebSearch, FetchURL, Write, or Edit. This council pass is read-only and SOT-only.
        - Use direct Read, Grep, and Glob tools when you need evidence from the declared Source of Truth.
        - If you feel tempted to delegate, instead do the smallest targeted search yourself and report any uncertainty.
        """
    ) + "\n\n" + prompt


def adapter_knowledge_settings(adapter: "Adapter", settings: Dict[str, Any], round_name: str) -> Dict[str, Any]:
    selected = dict(settings)
    prefix = "round2_" if round_name == "round2" else ""
    if adapter.model.get(f"{prefix}pack_raw_excerpt_chars") is not None:
        selected["pack_raw_excerpt_chars"] = int(adapter.model[f"{prefix}pack_raw_excerpt_chars"])
    if adapter.model.get(f"{prefix}pack_max_chars") is not None:
        selected["pack_max_chars"] = int(adapter.model[f"{prefix}pack_max_chars"])
    return selected


def build_round2_peer_pack(
    adapter: "Adapter",
    mission: str,
    sot: Dict[str, Any],
    first: Dict[str, Any],
    round1_synthesis: str,
    red_flags: List[str],
    round1: List[Dict[str, Any]],
    round1_capsules: Dict[str, Dict[str, Any]],
    settings: Dict[str, Any],
    persistent: bool,
) -> str:
    if not settings.get("route_peer_knowledge_to_round2", True):
        return "Peer knowledge routing disabled for this run."

    pack_settings = adapter_knowledge_settings(adapter, settings, "round2")
    max_prompt_chars = int(adapter.model.get("max_prompt_chars") or 0)
    if max_prompt_chars:
        empty_prompt = adversarial_prompt(
            mission,
            sot,
            first.get("output", ""),
            round1_synthesis,
            "[peer knowledge budget placeholder]",
            red_flags,
            persistent=persistent,
        )
        peer_budget = max_prompt_chars - len(empty_prompt) - 2048
        if peer_budget < 4000:
            return (
                "Peer knowledge omitted for this adapter because its native prompt transport "
                f"has a {max_prompt_chars}-character safety ceiling. Raw peer artifacts remain authoritative."
            )
        pack_settings["pack_max_chars"] = min(int(pack_settings.get("pack_max_chars", 320000)), peer_budget)

    return build_knowledge_pack(
        f"Round-one peer knowledge for {adapter.id}",
        round1,
        round1_capsules,
        pack_settings,
        exclude_id=adapter.id,
    )


def extract_footer(text: str, max_chars: int = 12000) -> str:
    patterns = [
        r"(?is)(#+\s*Extraction footer\b.*)$",
        r"(?is)(\*\*Extraction footer\*\*.*)$",
        r"(?is)(Extraction footer.*)$",
        r"(?is)(- strongest recommendation\s*:?.*)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return truncate_output(match.group(1).strip(), max_chars)
    return ""


def split_with_overlap(text: str, max_chars: int, overlap: int) -> List[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    start = 0
    step = max(1, max_chars - max(0, overlap))
    while start < len(text):
        chunks.append(text[start : start + max_chars])
        if start + max_chars >= len(text):
            break
        start += step
    return chunks


def read_artifact_text(result: Dict[str, Any]) -> str:
    artifact = result.get("artifact_path")
    if artifact:
        path = Path(str(artifact))
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return str(result.get("output") or "")


def result_status(result: Dict[str, Any]) -> str:
    if result.get("ok"):
        return "OK"
    return f"FAILED: {result.get('reason') or 'unknown'}"


def format_result_header(result: Dict[str, Any], capsule: Optional[Dict[str, Any]] = None) -> str:
    lines = [
        f"- worker id: {result.get('id')}",
        f"- status: {result_status(result)}",
        f"- raw artifact: {result.get('artifact_path') or 'unavailable'}",
    ]
    if result.get("full_output_chars") is not None:
        lines.append(f"- raw chars: {result.get('full_output_chars')}")
    if capsule:
        lines.append(f"- capsule status: {result_status(capsule)}")
        lines.append(f"- capsule artifact: {capsule.get('artifact_path') or 'unavailable'}")
    return "\n".join(lines)


def build_result_pack(
    result: Dict[str, Any],
    capsule: Optional[Dict[str, Any]],
    raw_excerpt_chars: int,
) -> str:
    raw_text = read_artifact_text(result)
    footer = extract_footer(raw_text)
    capsule_text = ""
    if capsule and capsule.get("ok"):
        capsule_text = read_artifact_text(capsule)
    raw_excerpt = truncate_output(raw_text, raw_excerpt_chars)
    parts = [
        f"## {result.get('id')} knowledge pack",
        "",
        format_result_header(result, capsule),
    ]
    if capsule_text:
        parts.extend(
            [
                "",
                "### Recall capsule",
                "This capsule is a lossy recall index, not authority. Prefer raw text if the two conflict.",
                "",
                capsule_text,
            ]
        )
    if footer:
        parts.extend(["", "### Extraction footer from raw output", "", footer])
    parts.extend(
        [
            "",
            "### Raw output excerpt",
            "The raw artifact remains authoritative. This excerpt preserves head and tail if shortened.",
            "",
            raw_excerpt,
        ]
    )
    return "\n".join(parts).strip()


def build_knowledge_pack(
    title: str,
    results: List[Dict[str, Any]],
    capsules: Dict[str, Dict[str, Any]],
    settings: Dict[str, Any],
    exclude_id: Optional[str] = None,
) -> str:
    raw_excerpt_chars = int(settings.get("pack_raw_excerpt_chars", 70000))
    max_chars = int(settings.get("pack_max_chars", 320000))
    blocks: List[str] = [f"# {title}"]
    for result in results:
        if exclude_id and result.get("id") == exclude_id:
            continue
        blocks.append(build_result_pack(result, capsules.get(str(result.get("id"))), raw_excerpt_chars))
    packed = "\n\n".join(blocks).strip()
    if len(packed) <= max_chars:
        return packed
    return truncate_output(packed, max_chars)


def append_missing_red_flags(report: str, red_flags: List[str]) -> str:
    missing = [flag for flag in red_flags if flag and flag not in report]
    if not missing:
        return report
    return (
        report.rstrip()
        + "\n\n## Mechanically Preserved Red Flags\n\n"
        + "\n".join(f"- {flag}" for flag in missing)
        + "\n"
    )


def exact_carry_strings(text: str) -> List[str]:
    seen: set[str] = set()
    strings: List[str] = []
    for line in text.splitlines():
        red_flag_match = re.match(r"^\s*(?:>\s*)?(?:[-*+]\s*)?(RED FLAG:\s*.+?)\s*$", line)
        if red_flag_match:
            value = red_flag_match.group(1).strip()
            if value and value not in seen:
                seen.add(value)
                strings.append(value)
    for match in re.findall(r"\b[A-Z0-9_]*SENTINEL[A-Z0-9_]*\b", text):
        if match not in seen:
            seen.add(match)
            strings.append(match)
    return strings


def missing_exact_carry_strings(raw_text: str, capsule_text: str) -> List[str]:
    return [value for value in exact_carry_strings(raw_text) if value not in capsule_text]


def extract_kimi_session_id(text: str) -> Optional[str]:
    uuid_sessions = re.findall(r"(session_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", text)
    if uuid_sessions:
        return uuid_sessions[-1]
    sessions = [item for item in re.findall(r"(session_[A-Za-z0-9-]+)", text) if item != "session_id"]
    return sessions[-1] if sessions else None


def compact_failure_reason(proc: subprocess.CompletedProcess[str]) -> str:
    error_pattern = re.compile(
        r"(?i)\b(error|failed|rate[_ -]?limit|429|quota|usage limit|insufficient|unauthorized|forbidden|permission)\b"
    )
    for source in [proc.stderr or "", proc.stdout or ""]:
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        for line in lines:
            if error_pattern.search(line):
                return line[:500]
    for source in [proc.stdout or "", proc.stderr or ""]:
        try:
            payload = json.loads(source)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                return "; ".join(str(item) for item in errors[:3])
            if payload.get("api_error_status"):
                return str(payload["api_error_status"])
            if payload.get("subtype") and "error" in str(payload["subtype"]):
                return str(payload["subtype"])
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        if lines:
            return lines[0][:500]
    return f"exit {proc.returncode}"


def api_key_env_names(model: Dict[str, Any]) -> List[str]:
    names = []
    primary = str(model.get("api_key_env") or "").strip()
    if primary:
        names.append(primary)
    fallbacks = model.get("api_key_fallback_envs")
    if fallbacks is None and (model.get("adapter") == "kimi-openai" or str(model.get("id", "")).startswith("kimi-")):
        fallbacks = ["KIMI_API_KEY", "MOONSHOT_API_KEY"]
    if isinstance(fallbacks, str):
        names.extend(part.strip() for part in fallbacks.split(",") if part.strip())
    elif isinstance(fallbacks, list):
        names.extend(str(part).strip() for part in fallbacks if str(part).strip())
    return list(dict.fromkeys(names))


def has_api_key_source(model: Dict[str, Any]) -> bool:
    for env_name in api_key_env_names(model):
        if os.getenv(env_name):
            return True
    key_file = model.get("api_key_file")
    return bool(key_file and Path(str(key_file)).expanduser().exists())


def normalize_api_key_candidate(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("export "):
        candidate = candidate[len("export "):].strip()
    if "=" in candidate and not candidate.startswith(("sk-", "sk_")):
        candidate = candidate.split("=", 1)[1].strip()
    return candidate.strip().strip('"').strip("'")


def load_api_key(model: Dict[str, Any]) -> str:
    for env_name in api_key_env_names(model):
        if os.getenv(env_name):
            return str(os.environ[env_name]).strip()
    key_file = model.get("api_key_file")
    if key_file:
        text = Path(str(key_file)).expanduser().read_text(encoding="utf-8", errors="ignore")
        pattern = str(model.get("api_key_pattern") or r"(sk-[A-Za-z0-9_-]{16,})")
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
        for line in text.splitlines():
            candidate = normalize_api_key_candidate(line)
            if candidate and not candidate.startswith("#") and len(candidate) >= 16:
                return candidate
    raise RuntimeError("missing API key")


def build_process_env(model: Dict[str, Any]) -> Optional[Dict[str, str]]:
    merged: Dict[str, str] = dict(os.environ)
    configured = model.get("env") or {}
    if not isinstance(configured, dict):
        raise RuntimeError("model env must be an object")
    for key, value in configured.items():
        merged[str(key)] = str(value)
    key_target = model.get("api_key_target_env")
    if key_target:
        merged[str(key_target)] = load_api_key(model)
    return merged if configured or key_target else None


def parse_json_error(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text.strip()[:500] if text.strip() else "empty error response"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or error
        return str(message)[:500]
    return str(payload)[:500]


def base_worker_prompt(mission: str, sot: Dict[str, Any], context_files: List[Tuple[str, str]], preflight_red_flags: List[str]) -> str:
    context_sections = []
    for label, content in context_files:
        context_sections.append(f"## Context File: {label}\n\n{content}")
    context_blob = "\n\n".join(context_sections) if context_sections else "No extra context files were attached."
    forbidden = "\n".join(f"- {item}" for item in sot["forbidden_worktrees"]) or "- none detected"
    status = sot.get("status_short") or "clean or unavailable"
    flags = "\n".join(f"- {flag}" for flag in preflight_red_flags) if preflight_red_flags else "- none"
    return "\n".join(
        [
            "You are one member of a multi-model council. You are not assigned a specialty.",
            "Solve the user's whole request independently and completely.",
            "",
            "Critical Source of Truth rules:",
            f"- LIVE SOT label: {sot['label']}",
            f"- LIVE SOT path: {sot['sot_path']}",
            f"- Git root: {sot['git_root']}",
            f"- Branch: {sot.get('branch') or 'unknown'}",
            f"- Commit: {sot.get('commit') or 'unknown'}",
            "- Git status summary from SOT:",
            indent(status),
            "- Forbidden/non-SOT worktrees:",
            indent(forbidden),
            "",
            "Use only the provided SOT context as factual authority.",
            "Do not infer from dirty launchers, sibling worktrees, or old worktrees.",
            "If you discuss another worktree or past implementation as inspiration, explicitly name it,",
            "explain why it is relevant, and state that it is not the source of truth.",
            "Read-only research is allowed inside the LIVE SOT only.",
            "You may read files, list directories, grep/search, and inspect git status/diff/log/show.",
            "Treat READ ONLY literally: do not edit, create, delete, move, or format files.",
            "Do not run tests, builds, package managers, migrations, service restarts, deploys,",
            "network/web/browser tools, or any command that mutates repo, runtime, database, or live state.",
            "",
            "Research hygiene for large repos:",
            "- Prefer targeted `rg`, focused `find`, and direct file reads over broad tree dumps.",
            "- Avoid enumerating dependency, cache, build, virtualenv, coverage, or VCS internals unless directly relevant.",
            "- If a search/listing returns a huge result set, narrow it before continuing instead of copying the whole thing into context.",
            "If your client exposes a sub-agent/delegation-only tool and it is enabled,",
            "you may use it only for internal critique of the provided context. The child",
            "must receive the same SOT and READ ONLY constraints.",
            "",
            "Intelligence posture:",
            "- Write naturally and fully. Do not compress yourself into a tiny form.",
            "- Be humble. Assume your first answer may be wrong.",
            "- Prefer reality, evidence, and falsifiable tests over confident claims.",
            "- Include counterarguments and ways to prove or disprove your answer.",
            "- A minority insight can matter more than consensus; do not optimize for agreement.",
            "",
            "Council methodology:",
            "- Your value is disciplined coverage, not variety for its own sake.",
            "- Treat the user's framing and your first interpretation as hypotheses, not facts.",
            "- Before settling, consider at least two materially different explanations, designs, or failure modes.",
            "- Separate what you directly observed in the SOT from what you inferred, assumed, or could not verify.",
            "- For bugs, failures, architecture, or implementation advice, name the proof path: the smallest read-only evidence, test, log, build, runtime check, or reproduction that would prove or falsify your claim.",
            "- Do not let confidence, consensus, model reputation, or elegant reasoning substitute for evidence.",
            "- Actively look for blind spots: hidden state, wrong worktree, stale assumptions, missing wiring, unhappy paths, user-visible gaps, data loss, security/privacy risk, deploy/runtime risk, and integration boundaries.",
            "- Preserve minority insights, contradictions, and weird-but-plausible observations. A one-model claim with a proof path may be the uplift.",
            "- If evidence is missing, say what is missing and how to get it. Do not invent certainty.",
            "",
            "User mission:",
            mission,
            "",
            "Provided context:",
            context_blob,
            "",
            "Run red flags:",
            flags,
            "",
            "At the end, include a short extraction footer with:",
            "- strongest recommendation",
            "- key evidence observed",
            "- key inference or assumption",
            "- biggest blind spot or uncertainty",
            "- best proof/test",
            "- minority or weird insight worth preserving",
        ]
    ).strip()


def synthesis_round1_prompt(
    mission: str,
    sot: Dict[str, Any],
    results: List[Dict[str, Any]],
    red_flags: List[str],
    round1_knowledge_pack: str,
) -> str:
    blocks = []
    for result in results:
        status = "OK" if result["ok"] else f"FAILED: {result.get('reason', 'unknown')}"
        blocks.append(f"- {result['id']}: {status}; raw artifact: {result.get('artifact_path') or 'unavailable'}")
    flags = "\n".join(f"- {flag}" for flag in red_flags) or "- none"
    worker_statuses = "\n".join(blocks)
    return block(
        f"""
        You are the first synthesis layer for a multi-model council.

        Do not merely summarize. Dedupe, denoise, and preserve intelligence.
        You must preserve contradictions, minority insights, unsupported leaps, and proof paths.
        Do not smooth over disagreement. Do not majority-vote blindly.

        Synthesis methodology:
        - Optimize for intelligence preservation, not neatness.
        - Distinguish observed evidence, inference, assumption, and unproven claims.
        - Preserve minority claims when they have a plausible proof path.
        - Do not let agreement wash away uncertainty.
        - Identify the most important blind spot and the smallest verification that would reduce it.

        Source of Truth:
        - Label: {sot['label']}
        - Path: {sot['sot_path']}
        - Branch: {sot.get('branch') or 'unknown'}
        - Commit: {sot.get('commit') or 'unknown'}

        User mission:
        {mission}

        Red flags:
        {flags}

        Worker knowledge pack:
        Capsules are recall indexes, not authority. Raw artifacts remain authoritative.
        {round1_knowledge_pack}

        Worker statuses:
        {worker_statuses}

        Produce a denoised claim map with:
        - Best candidate answer so far
        - Consensus claims
        - Unique one-model claims that might be the intelligence uplift
        - Contradictions and how to resolve them
        - Unsupported or arrogant-sounding leaps
        - What should be attacked in the adversarial pass
        - What must be proven before implementation
        - Red flags, verbatim
        """
    ).strip()


def adversarial_prompt(
    mission: str,
    sot: Dict[str, Any],
    first_answer: str,
    synthesis: str,
    peer_knowledge_pack: str,
    red_flags: List[str],
    persistent: bool,
) -> str:
    flags = "\n".join(f"- {flag}" for flag in red_flags) or "- none"
    session_note = (
        "You are in the same model session as your first pass; use your prior context and the synthesis below. Your first answer is not replayed to avoid duplicating context."
        if persistent
        else "Your first answer is replayed below because this adapter does not support reliable session resume."
    )
    first_answer_section = (
        "Your first answer is available in this persistent session. Re-check it from session context."
        if persistent
        else f"Your first answer:\n{first_answer}"
    )
    return block(
        f"""
        This is the adversarial second pass for the same multi-model council.

        {session_note}

        Your job is adversarial truth-seeking, not defending yourself:
        - Attack the synthesis.
        - Re-check your own first answer.
        - Evaluate one-model minority claims as possible uplift or possible nonsense.
        - Identify consensus claims that may still be wrong.
        - Find confident/arrogant assumptions.
        - Say what would prove or disprove the recommendation.
        - Revise your answer if needed. It is good to say "I was wrong" or "I missed this."

        Adversarial methodology:
        - Attack the reasoning, not the author.
        - Look for false consensus, unsupported confidence, missing evidence, wrong-SOT assumptions, and claims that sound right but have no proof path.
        - Re-evaluate minority claims: either kill them with evidence or preserve them as possible uplift.
        - Identify the single most consequential disagreement, uncertainty, or load-bearing claim that is checkable from the read-only Source of Truth.
        - Run the smallest read-only SOT check that can settle or materially reduce uncertainty about that claim. Use only allowed read/list/grep/git inspection tools.
        - Report the exact check, the raw result in brief, what it settles, what it does not settle, and how the result could still mislead.
        - If no useful read-only SOT check is available, say why and name the smallest evidence that would be needed.
        - Change your answer only when SOT evidence or a peer proof path warrants it, not because the synthesis sounds confident.
        - State clearly what changed in your view and why.

        Source of Truth remains unchanged:
        - Label: {sot['label']}
        - Path: {sot['sot_path']}
        - Branch: {sot.get('branch') or 'unknown'}
        - Commit: {sot.get('commit') or 'unknown'}

        User mission:
        {mission}

        Red flags:
        {flags}

        {first_answer_section}

        Peer knowledge pack:
        These are recall aids from other workers, not authority. Use them to attack the synthesis,
        inspect minority claims, and avoid losing important details. Do not blindly converge.
        {peer_knowledge_pack}

        First synthesis to critique:
        {synthesis}

        Produce your revised answer freely in prose. End with:
        - what survived critique
        - what changed in your view
        - the evidence check you ran, or why no useful check was possible
        - what still must be proven
        """
    ).strip()


def final_synthesis_prompt(
    mission: str,
    sot: Dict[str, Any],
    round1_synthesis: str,
    round2_results: List[Dict[str, Any]],
    all_results: List[Dict[str, Any]],
    round1_knowledge_pack: str,
    round2_knowledge_pack: str,
    red_flags: List[str],
) -> str:
    round2_blocks = []
    for result in round2_results:
        status = "OK" if result["ok"] else f"FAILED: {result.get('reason', 'unknown')}"
        round2_blocks.append(f"- {result['id']} adversarial pass: {status}; raw artifact: {result.get('artifact_path') or 'unavailable'}")
    failed_first = [result for result in all_results if not result["ok"]]
    failed_blocks = "\n".join(f"- {item['id']}: {item.get('reason', 'unknown')}" for item in failed_first) or "- none"
    flags = "\n".join(f"- {flag}" for flag in red_flags) or "- none"
    round2_reports = "\n".join(round2_blocks) if round2_blocks else "No adversarial second pass was run."
    return block(
        f"""
        You are the final synthesis layer for a multi-model council.

        Your job is to produce one fortified answer for the human, not a transcript.
        Use both rounds. Preserve the humility of the adversarial pass.
        Rank claims by evidence, falsifiability, and explanatory power. Do not defer to model prestige.
        A minority claim can win if it survived critique. Consensus can lose if it is unsupported.

        Final methodology:
        - The final answer should represent disciplined convergence, not majority vote.
        - Prefer claims that survived adversarial review and have evidence or a concrete proof path.
        - Mark claims as proven, likely, plausible-but-unproven, or rejected only when that distinction changes what Codex should do next.
        - Preserve the most important minority insight if it still has a plausible path to being true.
        - Before finalizing, audit yourself for dropped dissent, unsupported consensus, emergent claims no worker actually made, and minority insights that were smoothed away.
        - If a final recommendation depends on an emergent synthesis claim, mark it unverified and give the proof path instead of presenting it as settled.
        - If the audit changes the answer, surface that change under disagreements, verification, or remaining uncertainty. Do not add a ceremonial audit section when nothing material changed.

        Source of Truth:
        - Label: {sot['label']}
        - Path: {sot['sot_path']}
        - Branch: {sot.get('branch') or 'unknown'}
        - Commit: {sot.get('commit') or 'unknown'}

        User mission:
        {mission}

        Required red flags, verbatim:
        {flags}

        First synthesis:
        {round1_synthesis}

        Round-one knowledge pack:
        Capsules are recall indexes, not authority. Raw artifacts remain authoritative.
        {round1_knowledge_pack}

        Round-two knowledge pack:
        {round2_knowledge_pack}

        Adversarial second-pass statuses:
        {round2_reports}

        First-round failures:
        {failed_blocks}

        Final report format:
        - Best answer
        - Why this is probably true
        - What changed after adversarial review
        - What to verify before touching code or live systems
        - What to implement or do next
        - Council disagreements
        - Red flags for missing or failed models
        - Remaining uncertainty

        Keep it decisive and human. Do not include raw transcripts unless needed.
        """
    ).strip()


class Adapter:
    supports_resume = False

    def __init__(self, model: Dict[str, Any], run_dir: Path, max_output_chars: int):
        self.model = model
        self.run_dir = run_dir
        self.max_output_chars = max_output_chars

    @property
    def id(self) -> str:
        return str(self.model["id"])

    def run(self, prompt: str, round_name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def _record(
        self,
        round_name: str,
        proc: subprocess.CompletedProcess[str],
        output: str,
        started: float,
        session_id: Optional[str],
        command: List[str],
        reason_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        elapsed = time.time() - started
        folder = self.run_dir / round_name / self.id
        write_text(folder / "stdout.txt", proc.stdout or "")
        write_text(folder / "stderr.txt", proc.stderr or "")
        write_text(folder / "output.md", output)
        meta = {
            "id": self.id,
            "round": round_name,
            "adapter": self.model.get("adapter"),
            "model": self.model.get("model"),
            "returncode": proc.returncode,
            "elapsed_seconds": round(elapsed, 3),
            "session_id": session_id,
            "command_preview": command_preview(command),
        }
        write_json(folder / "meta.json", meta)
        ok = proc.returncode == 0 and bool(output.strip())
        if ok:
            reason = ""
        elif reason_override:
            reason = reason_override
        elif proc.returncode == 0:
            reason = "exit 0; empty output"
        else:
            reason = compact_failure_reason(proc)
        return {
            "id": self.id,
            "ok": ok,
            "reason": reason,
            "output": truncate_output(output, self.max_output_chars),
            "artifact_path": str(folder / "output.md"),
            "full_output_chars": len(output),
            "session_id": session_id,
            "elapsed_seconds": elapsed,
            "round": round_name,
        }


class OpenCodeAdapter(Adapter):
    supports_resume = True

    def run(self, prompt: str, round_name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        binary = os.path.expanduser(str(self.model["binary"]))
        args = [
            binary,
            "run",
            "--pure",
            "--dir",
            str(Path(self.model.get("work_dir", "/tmp"))),
            "--model",
            str(self.model["model"]),
            "--format",
            "json",
            "--title",
            f"team {self.id} {round_name}",
        ]
        if session_id:
            args.extend(["--session", session_id])
        started = time.time()
        proc = run_opencode_cmd(args, cwd=Path(self.model.get("work_dir", "/tmp")), input_text=prompt)
        texts: List[str] = []
        seen_session = session_id
        for line in (proc.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen_session = seen_session or event.get("sessionID")
            part = event.get("part") or {}
            if part.get("type") == "text":
                texts.append(str(part.get("text", "")))
        output = "\n".join(texts).strip() or (proc.stdout or "").strip()
        return self._record(round_name, proc, output, started, seen_session, args)


class KimiOpenAIAdapter(Adapter):
    supports_resume = True

    def _state_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
        return self.run_dir / "state" / f"{safe}.json"

    def _load_messages(self, session_id: Optional[str]) -> Tuple[str, List[Dict[str, Any]]]:
        selected = session_id or f"kimi-openai-{uuid.uuid4()}"
        path = self._state_path(selected)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                messages = payload.get("messages")
                if isinstance(messages, list):
                    return selected, messages
            except json.JSONDecodeError:
                pass
        return selected, []

    def _save_messages(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        write_json(
            self._state_path(session_id),
            {
                "id": session_id,
                "adapter": self.model.get("adapter"),
                "model": self.model.get("model"),
                "messages": messages,
            },
        )

    def run(self, prompt: str, round_name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        started = time.time()
        selected_session, messages = self._load_messages(session_id)
        request_messages = [*messages, {"role": "user", "content": prompt}]
        base_url = str(self.model.get("base_url", "https://api.kimi.com/coding/v1")).rstrip("/")
        endpoint = base_url + "/chat/completions"
        payload: Dict[str, Any] = {
            "model": str(self.model.get("model", "kimi-for-coding")),
            "messages": request_messages,
            "stream": True,
        }
        if self.model.get("max_tokens") is not None:
            payload["max_tokens"] = int(self.model["max_tokens"])
        if self.model.get("tools"):
            payload["tools"] = self.model["tools"]
            payload["tool_choice"] = self.model.get("tool_choice", "auto")

        try:
            api_key = load_api_key(self.model)
        except Exception as exc:
            return failure_result(self, round_name, f"missing Kimi API key: {exc}")

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": "Bearer " + api_key,
            "User-Agent": str(self.model.get("user_agent", "Codex-Team-Council/0.1")),
        }
        args = [
            "kimi-openai",
            endpoint,
            "--model",
            str(payload["model"]),
            "--stream",
            "--max-tokens",
            str(payload.get("max_tokens", "default")),
        ]
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        stderr = ""
        returncode = 0

        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        returncode = 1
                        stderr = parse_json_error(json.dumps(event))
                        break
                    choice = (event.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(str(delta["reasoning_content"]))
                    if delta.get("content"):
                        content_parts.append(str(delta["content"]))
        except urllib.error.HTTPError as exc:
            returncode = 1
            stderr = parse_json_error(exc.read().decode("utf-8", "replace"))
        except Exception as exc:
            returncode = 1
            stderr = f"{type(exc).__name__}: {exc}"

        content = "".join(content_parts).strip()
        reasoning = "".join(reasoning_parts)
        if returncode == 0:
            assistant_message: Dict[str, Any] = {"role": "assistant", "content": content}
            if reasoning:
                assistant_message["reasoning_content"] = reasoning
            self._save_messages(selected_session, [*request_messages, assistant_message])

        stdout = json.dumps(
            {
                "content": content,
                "reasoning_content_chars": len(reasoning),
                "session_id": selected_session,
                "model": payload["model"],
            },
            ensure_ascii=True,
        )
        proc = subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)
        reason_override = stderr if returncode != 0 else None
        return self._record(round_name, proc, content, started, selected_session, args, reason_override=reason_override)


class KimiAdapter(Adapter):
    supports_resume = True

    def run(self, prompt: str, round_name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        binary = os.path.expanduser(str(self.model["binary"]))
        process_env = dict(os.environ)
        configured_env = self.model.get("env") or {}
        if not isinstance(configured_env, dict):
            return failure_result(self, round_name, "model env must be an object")
        for key, value in configured_env.items():
            process_env[str(key)] = str(value)
        process_env["KIMI_CODE_NO_AUTO_UPDATE"] = "1"
        process_env["KIMI_DISABLE_TELEMETRY"] = "1"
        process_env["KIMI_CODE_BACKGROUND_KEEP_ALIVE_ON_EXIT"] = "0"

        skills_dir: Optional[Path] = None
        if truthy(self.model.get("council_mode"), True):
            try:
                kimi_home = prepare_kimi_code_home(self.model, self.run_dir, self.id)
            except Exception as exc:
                return failure_result(self, round_name, f"Kimi council runtime setup failed: {exc}")
            process_env["KIMI_CODE_HOME"] = str(kimi_home)
            skills_dir = kimi_home / "empty-skills"
            prompt = kimi_council_prompt(prompt)

        max_prompt_chars = int(self.model.get("max_prompt_chars") or KIMI_NATIVE_ARG_MAX_CHARS)
        if max_prompt_chars and len(prompt) > max_prompt_chars:
            return failure_result(
                self,
                round_name,
                f"Kimi prompt too large for native CLI -p single-argument transport ({len(prompt)} chars > {max_prompt_chars}); reduce adapter peer pack or use an API/ACP adapter.",
            )

        args = [binary]
        if self.model.get("auto", False):
            args.append("--auto")
        if self.model.get("yolo", False):
            args.append("--yolo")
        if session_id:
            args.extend(["-r", session_id])
        if skills_dir:
            args.extend(["--skills-dir", str(skills_dir)])
        args.extend(["-m", str(self.model["model"]), "-p", prompt, "--output-format", "stream-json"])
        started = time.time()
        proc = run_cmd(args, cwd=Path(self.model.get("work_dir", "/tmp")), env=process_env)
        output_parts: List[str] = []
        seen_session = session_id
        saw_tool_activity = False
        for line in (proc.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("role") == "assistant" and event.get("content"):
                output_parts.append(str(event["content"]))
            if event.get("role") == "assistant" and event.get("tool_calls"):
                saw_tool_activity = True
            if event.get("role") == "tool":
                saw_tool_activity = True
            if event.get("session_id"):
                seen_session = str(event["session_id"])
        output = "\n".join(part.strip() for part in output_parts if part.strip()).strip()
        if not output:
            session_text = "\n".join(part for part in [proc.stdout or "", proc.stderr or ""] if part)
            parsed_session = extract_kimi_session_id(session_text)
            if parsed_session:
                seen_session = parsed_session
            if proc.returncode == 0 and not saw_tool_activity:
                output = (proc.stdout or "").strip()
                output = re.sub(r"\n*To resume this session:.*$", "", output, flags=re.S).strip()
        reason_override = compact_failure_reason(proc) if proc.returncode != 0 else None
        if proc.returncode == 0 and not output and saw_tool_activity:
            reason_override = "Kimi produced tool calls/tool results but no final assistant content"
        return self._record(round_name, proc, output, started, seen_session, args, reason_override=reason_override)


class ClaudeAdapter(Adapter):
    supports_resume = True

    def run(self, prompt: str, round_name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        binary = os.path.expanduser(str(self.model["binary"]))
        tools = self.model.get("tools", "Read,Grep,Glob,LS,Agent")
        args = [
            binary,
        ]
        if self.model.get("safe_mode", False):
            args.append("--safe-mode")
        if self.model.get("bare", False):
            args.append("--bare")
        args.extend(
            [
                "-p",
                "--model",
                str(self.model["model"]),
                "--effort",
                str(self.model.get("effort", "max")),
                "--output-format",
                "json",
            ]
        )
        if self.model.get("no_chrome", True):
            args.append("--no-chrome")
        if tools is not None:
            args.extend(["--tools", str(tools)])
        if session_id:
            args.extend(["-r", session_id])
        try:
            process_env = build_process_env(self.model)
        except Exception as exc:
            return failure_result(self, round_name, f"environment setup failed: {exc}")
        started = time.time()
        proc = run_cmd(
            args,
            cwd=Path(self.model.get("work_dir", "/tmp")),
            input_text=prompt,
            env=process_env,
        )
        output = ""
        seen_session = session_id
        try:
            payload = json.loads(proc.stdout or "{}")
            output = str(payload.get("result") or payload.get("message") or "")
            seen_session = seen_session or payload.get("session_id") or payload.get("sessionId")
        except json.JSONDecodeError:
            output = (proc.stdout or "").strip()
        reason_override = compact_failure_reason(proc) if proc.returncode != 0 else None
        return self._record(round_name, proc, output, started, seen_session, args, reason_override=reason_override)


class CodexAdapter(Adapter):
    supports_resume = True

    def run(self, prompt: str, round_name: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        binary = os.path.expanduser(str(self.model["binary"]))
        folder = self.run_dir / round_name / self.id
        folder.mkdir(parents=True, exist_ok=True)
        last_message = folder / "last_message.md"
        base_args = [
            binary,
            "exec",
        ]
        if session_id:
            base_args.append("resume")
        base_args.extend(
            [
                "--ignore-user-config",
                "--skip-git-repo-check",
                "-m",
                str(self.model["model"]),
                "-c",
                f"model_reasoning_effort=\"{self.model.get('reasoning_effort', 'xhigh')}\"",
                "-c",
                f"service_tier=\"{self.model.get('service_tier', 'fast')}\"",
                "--json",
                "-o",
                str(last_message),
            ]
        )
        if session_id:
            args = [*base_args, session_id, "-"]
        else:
            args = [
                *base_args,
                "--sandbox",
                "read-only",
                "-C",
                str(Path(self.model.get("work_dir", "/tmp"))),
                "-",
            ]
        started = time.time()
        proc = run_cmd(args, cwd=Path(self.model.get("work_dir", "/tmp")), input_text=prompt)
        output = last_message.read_text(encoding="utf-8").strip() if last_message.exists() else (proc.stdout or "").strip()
        seen_session = session_id
        for line in (proc.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("thread_id"):
                seen_session = str(event["thread_id"])
                break
        return self._record(round_name, proc, output, started, seen_session, args)


ADAPTERS = {
    "opencode": OpenCodeAdapter,
    "kimi-openai": KimiOpenAIAdapter,
    "kimi": KimiAdapter,
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
}


def failure_result(adapter: Adapter, round_name: str, reason: str) -> Dict[str, Any]:
    folder = adapter.run_dir / round_name / adapter.id
    write_text(folder / "output.md", "")
    write_json(
        folder / "meta.json",
        {
            "id": adapter.id,
            "round": round_name,
            "adapter": adapter.model.get("adapter"),
            "model": adapter.model.get("model"),
            "ok": False,
            "reason": reason,
        },
    )
    return {
        "id": adapter.id,
        "ok": False,
        "reason": reason,
        "output": "",
        "artifact_path": str(folder / "output.md"),
        "full_output_chars": 0,
        "session_id": None,
        "elapsed_seconds": 0,
        "round": round_name,
    }


def make_adapter(model: Dict[str, Any], run_dir: Path, max_output_chars: int) -> Adapter:
    adapter_name = str(model.get("adapter", ""))
    adapter_cls = ADAPTERS.get(adapter_name)
    if not adapter_cls:
        raise ValueError(f"unknown adapter: {adapter_name}")
    return adapter_cls(model, run_dir, max_output_chars)


def apply_worker_defaults(model: Dict[str, Any], run_settings: Dict[str, Any], sot: Dict[str, Any]) -> Dict[str, Any]:
    selected = dict(model)
    configured_work_dir = selected.get("work_dir", run_settings.get("work_dir", "sot"))
    if str(configured_work_dir).lower() == "sot":
        selected["work_dir"] = sot["git_root"]
    else:
        selected["work_dir"] = str(configured_work_dir)
    return selected


def resolve_archivist_config(
    config: Dict[str, Any],
    run_settings: Dict[str, Any],
    sot: Dict[str, Any],
    selected_ids: set[str],
    include: Optional[set[str]],
    exclude: set[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    settings = knowledge_settings(run_settings)
    if not settings.get("capsules", True):
        return None, "capsules disabled in run settings"
    configured = config.get("archivist")
    if isinstance(configured, dict):
        candidate = configured
    else:
        capsule_model_id = str(settings.get("capsule_model_id", "deepseek-v4-pro"))
        candidate = next((model for model in config.get("models", []) if str(model.get("id")) == capsule_model_id), None)
        if candidate is None:
            return None, f"archivist model not found: {capsule_model_id}"
    model_id = str(candidate.get("id"))
    if model_id in exclude:
        return None, f"archivist model {model_id} excluded for this run"
    if include and model_id not in include and not settings.get("allow_archivist_outside_selected", False):
        return None, f"archivist model {model_id} not selected for this run"
    if not candidate.get("enabled", True):
        return None, f"archivist model {model_id} disabled in roster"
    reason = validate_binary(candidate)
    if reason:
        return None, reason
    selected = apply_worker_defaults(candidate, run_settings, sot)
    selected["id"] = f"archivist-{model_id}"
    return selected, None


def capsule_prompt(mission: str, result: Dict[str, Any], report_text: str, chunk_label: str = "") -> str:
    chunk_note = f"\nChunk: {chunk_label}\n" if chunk_label else ""
    return block(
        f"""
        You are a non-council recall archivist for a multi-model council.

        You are not solving the user's problem. You are preserving one worker report for later synthesis.
        Read the entire worker report below and produce a loss-preserving technical recall capsule.

        Preserve every important or possibly important:
        - claim, recommendation, implementation detail, caveat, uncertainty, contradiction, minority insight
        - proof path, file/path reference, command, test idea, number, constraint, and red flag

        Do not decide correctness. Do not smooth disagreement. Do not dedupe against other workers.
        Do not make the worker sound more certain than it was. If unsure whether something matters, include it.
        Quote exact strings for paths, commands, numbers, and RED FLAG lines.
        Mark anything you could not fully preserve.

        User mission:
        {mission}

        Worker id: {result.get('id')}
        Worker status: {result_status(result)}
        Raw artifact path: {result.get('artifact_path') or 'unavailable'}
        {chunk_note}
        Worker report:
        {report_text}

        Output a recall capsule in prose with clear sections:
        - Worker id and source artifact
        - Strongest recommendation
        - All important or possibly important claims
        - Minority/unique insights
        - Contradictions, caveats, and doubts
        - Proof paths and exact references
        - Implementation suggestions
        - Red flags copied exactly and literally; do not paraphrase, reword, or summarize any RED FLAG line
        - Things you were unsure whether to preserve
        """
    ).strip()


def capsule_merge_prompt(mission: str, result: Dict[str, Any], chunk_capsules: List[str]) -> str:
    return block(
        f"""
        You are merging recall capsules for one worker report.

        Do not solve the user's problem. Do not decide correctness.
        Preserve all important or possibly important information from every chunk capsule.
        If chunks disagree or overlap, preserve the disagreement instead of smoothing it away.

        User mission:
        {mission}

        Worker id: {result.get('id')}
        Raw artifact path: {result.get('artifact_path') or 'unavailable'}

        Chunk capsules:
        {chr(10).join(f"## Chunk capsule {index + 1}{chr(10)}{capsule}" for index, capsule in enumerate(chunk_capsules))}
        """
    ).strip()


def generate_one_capsule(
    archivist_config: Dict[str, Any],
    result: Dict[str, Any],
    mission: str,
    run_dir: Path,
    round_name: str,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    worker_id = str(result.get("id"))
    output_folder = run_dir / round_name / worker_id
    if not result.get("ok"):
        return {
            "id": worker_id,
            "ok": False,
            "reason": "worker did not participate",
            "output": "",
            "artifact_path": str(output_folder / "capsule.md"),
            "full_output_chars": 0,
            "round": f"{round_name}-capsule",
        }
    report_text = read_artifact_text(result)
    max_input_chars = int(settings.get("capsule_input_max_chars", 160000))
    overlap = int(settings.get("capsule_chunk_overlap_chars", 2000))
    max_output_chars = int(settings.get("capsule_max_output_chars", 90000))
    chunks = split_with_overlap(report_text, max_input_chars, overlap)
    chunk_outputs: List[str] = []
    chunk_results: List[Dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        adapter_config = dict(archivist_config)
        adapter_config["id"] = f"{archivist_config['id']}-{round_name}-{worker_id}-chunk{index + 1}"
        adapter = make_adapter(adapter_config, run_dir, max_output_chars)
        prompt = capsule_prompt(mission, result, chunk, chunk_label=f"{index + 1} of {len(chunks)}")
        chunk_result = adapter.run(prompt, f"capsules/{round_name}")
        chunk_results.append(chunk_result)
        if chunk_result.get("ok"):
            chunk_outputs.append(str(chunk_result.get("output") or ""))
        else:
            reason = chunk_result.get("reason") or "unknown capsule error"
            write_text(output_folder / "capsule.md", "")
            return {
                "id": worker_id,
                "ok": False,
                "reason": f"chunk {index + 1} failed: {reason}",
                "output": "",
                "artifact_path": str(output_folder / "capsule.md"),
                "full_output_chars": 0,
                "round": f"{round_name}-capsule",
                "chunks": chunk_results,
            }
    if len(chunk_outputs) == 1:
        capsule_text = chunk_outputs[0]
    else:
        adapter_config = dict(archivist_config)
        adapter_config["id"] = f"{archivist_config['id']}-{round_name}-{worker_id}-merge"
        adapter = make_adapter(adapter_config, run_dir, max_output_chars)
        merge_result = adapter.run(capsule_merge_prompt(mission, result, chunk_outputs), f"capsules/{round_name}")
        chunk_results.append(merge_result)
        if not merge_result.get("ok"):
            reason = merge_result.get("reason") or "unknown capsule merge error"
            write_text(output_folder / "capsule.md", "")
            return {
                "id": worker_id,
                "ok": False,
                "reason": f"merge failed: {reason}",
                "output": "",
                "artifact_path": str(output_folder / "capsule.md"),
                "full_output_chars": 0,
                "round": f"{round_name}-capsule",
                "chunks": chunk_results,
            }
        capsule_text = str(merge_result.get("output") or "")
    capsule_path = output_folder / "capsule.md"
    write_text(capsule_path, capsule_text)
    missing_carry = missing_exact_carry_strings(report_text, capsule_text)
    write_json(
        output_folder / "capsule_meta.json",
        {
            "id": worker_id,
            "ok": not missing_carry,
            "round": f"{round_name}-capsule",
            "archivist": archivist_config.get("id"),
            "chunks": len(chunks),
            "artifact_path": str(capsule_path),
            "raw_artifact_path": result.get("artifact_path"),
            "full_output_chars": len(capsule_text),
            "missing_exact_carry_strings": missing_carry,
        },
    )
    if missing_carry:
        return {
            "id": worker_id,
            "ok": False,
            "reason": "capsule missing exact carry strings: " + "; ".join(missing_carry[:5]),
            "output": truncate_output(capsule_text, max_output_chars),
            "artifact_path": str(capsule_path),
            "full_output_chars": len(capsule_text),
            "round": f"{round_name}-capsule",
            "chunks": chunk_results,
            "missing_exact_carry_strings": missing_carry,
        }
    return {
        "id": worker_id,
        "ok": True,
        "reason": "",
        "output": truncate_output(capsule_text, max_output_chars),
        "artifact_path": str(capsule_path),
        "full_output_chars": len(capsule_text),
        "round": f"{round_name}-capsule",
        "chunks": chunk_results,
    }


def generate_capsules(
    archivist_config: Optional[Dict[str, Any]],
    results: List[Dict[str, Any]],
    mission: str,
    run_dir: Path,
    round_name: str,
    settings: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    if not archivist_config or not settings.get("capsules", True):
        return {}
    ok_results = [result for result in results if result.get("ok")]
    capsules: Dict[str, Dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(ok_results))) as pool:
        future_map = {
            pool.submit(generate_one_capsule, archivist_config, result, mission, run_dir, round_name, settings): str(result.get("id"))
            for result in ok_results
        }
        for future in concurrent.futures.as_completed(future_map):
            model_id = future_map[future]
            try:
                capsules[model_id] = future.result()
            except Exception as exc:
                capsules[model_id] = {
                    "id": model_id,
                    "ok": False,
                    "reason": f"runner exception: {exc}",
                    "output": "",
                    "artifact_path": str(run_dir / round_name / model_id / "capsule.md"),
                    "full_output_chars": 0,
                    "round": f"{round_name}-capsule",
                }
    return capsules


def run_parallel(adapters: List[Adapter], prompt_by_id: Dict[str, str], round_name: str, session_by_id: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(adapters))) as pool:
        future_map = {}
        for adapter in adapters:
            session_id = (session_by_id or {}).get(adapter.id)
            future = pool.submit(adapter.run, prompt_by_id[adapter.id], round_name, session_id)
            future_map[future] = adapter.id
        for future in concurrent.futures.as_completed(future_map):
            try:
                results.append(future.result())
            except Exception as exc:
                model_id = future_map[future]
                results.append({"id": model_id, "ok": False, "reason": f"runner exception: {exc}", "output": "", "session_id": None, "round": round_name})
    return sorted(results, key=lambda item: item["id"])


def read_context_files(files: Iterable[str], max_chars: int) -> List[Tuple[str, str]]:
    contexts: List[Tuple[str, str]] = []
    for raw in files:
        path = Path(raw).expanduser()
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[context file truncated to {max_chars} chars]\n"
        contexts.append((str(path), content))
    return contexts


def red_flags_from_results(results: List[Dict[str, Any]], disabled: List[Tuple[str, str]], preflight_red_flags: List[str]) -> List[str]:
    flags = list(preflight_red_flags)
    flags.extend(f"RED FLAG: {model_id} did not participate because {reason}." for model_id, reason in disabled)
    for result in results:
        if not result.get("ok"):
            flags.append(f"RED FLAG: {result['id']} did not participate because {result.get('reason') or 'unknown error'}.")
    return flags


def fallback_round1(results: List[Dict[str, Any]], red_flags: List[str]) -> str:
    ok = [item["id"] for item in results if item.get("ok")]
    flags = "\n".join(f"- {flag}" for flag in red_flags) if red_flags else "- none"
    return "\n".join(
        [
            "First synthesis was generated by fallback logic because LLM synthesis was skipped or unavailable.",
            "",
            f"Participating models: {', '.join(ok) if ok else 'none'}",
            "",
            "Red flags:",
            flags,
            "",
            "Read the raw model outputs in the round1 artifact folders before making implementation decisions.",
        ]
    ).strip()


def fallback_final(round1_synthesis: str, round2_results: List[Dict[str, Any]], red_flags: List[str], run_dir: Path) -> str:
    ok2 = [item["id"] for item in round2_results if item.get("ok")]
    flags = "\n".join(f"- {flag}" for flag in red_flags) if red_flags else "- none"
    return "\n".join(
        [
            "Final report was generated by fallback logic because LLM synthesis was skipped or unavailable.",
            "",
            "Best answer:",
            "Review the first synthesis and raw worker outputs before acting.",
            "",
            "First synthesis:",
            round1_synthesis,
            "",
            f"Adversarial pass participants: {', '.join(ok2) if ok2 else 'none'}",
            "",
            "Red flags:",
            flags,
            "",
            "Artifact path:",
            str(run_dir),
        ]
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a /team multi-model council.")
    parser.add_argument("mission", nargs="*", help="User prompt after /team")
    parser.add_argument("--config", help="Roster config JSON. Defaults to ~/.codex/team/roster.json or the skill example.")
    parser.add_argument("--init-config", action="store_true", help="Copy the example roster to ~/.codex/team/roster.json if absent.")
    parser.add_argument("--models", help="Comma-separated model ids to include.")
    parser.add_argument("--exclude", help="Comma-separated model ids to exclude.")
    parser.add_argument("--sot-path", help="Authoritative SOT path. Defaults to current working directory.")
    parser.add_argument("--sot-label", help="Human SOT label/ref/release.")
    parser.add_argument("--context-file", action="append", default=[], help="Attach a specific context file. Repeatable.")
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT), help="Directory for run artifacts.")
    parser.add_argument("--dry-run", action="store_true", help="Write prompts/artifacts without calling model CLIs.")
    parser.add_argument("--skip-second-pass", action="store_true", help="Only run first pass and first synthesis.")
    parser.add_argument("--skip-synthesis", action="store_true", help="Collect worker outputs without LLM synthesis.")
    parser.add_argument("--allow-empty-context", action="store_true", help="Do not red-flag a run with no attached context files.")
    parser.add_argument("--print-config", action="store_true", help="Print the selected config and exit.")
    args = parser.parse_args()

    if args.init_config:
        path = init_user_config()
        print(f"created={path}")
        return 0

    config, config_path = load_config(args.config)
    if args.print_config:
        print(json.dumps(config, indent=2, sort_keys=True))
        return 0

    mission = " ".join(args.mission).strip()
    if not mission:
        raise SystemExit("Missing mission. Usage: team_council.py \"prompt after /team\"")

    cwd = Path.cwd()
    sot = detect_sot(cwd, args.sot_path, args.sot_label)
    run_settings = config.get("run", {})
    kt_settings = knowledge_settings(run_settings)
    capsules_active = bool(kt_settings.get("capsules", True)) and not args.skip_synthesis and bool(run_settings.get("synthesis", True))
    max_output_chars = int(run_settings.get("max_output_chars", 240000))
    run_dir = Path(args.run_root) / f"team-{now_stamp()}-pid{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    include = normalize_ids(args.models)
    exclude = normalize_ids(args.exclude) or set()
    disabled: List[Tuple[str, str]] = []
    selected_models: List[Dict[str, Any]] = []

    for model in config.get("models", []):
        model_id = str(model.get("id"))
        if include and model_id not in include:
            disabled.append((model_id, "not selected for this run"))
            continue
        if model_id in exclude:
            disabled.append((model_id, "excluded for this run"))
            continue
        if not model.get("enabled", True):
            disabled.append((model_id, "disabled in roster"))
            continue
        reason = validate_binary(model)
        if reason:
            disabled.append((model_id, reason))
            continue
        selected_models.append(apply_worker_defaults(model, run_settings, sot))

    selected_ids = {str(model.get("id")) for model in selected_models}
    archivist_config, archivist_reason = resolve_archivist_config(config, run_settings, sot, selected_ids, include, exclude)

    context_files = read_context_files(args.context_file, max_chars=120000)
    preflight_red_flags: List[str] = []
    if not context_files and not args.allow_empty_context:
        preflight_red_flags.append(
            "RED FLAG: no context files were attached; workers were SOT-constrained but may lack the evidence needed for repo-specific claims."
        )
    if capsules_active and archivist_reason:
        preflight_red_flags.append(f"RED FLAG: recall capsule layer is unavailable because {archivist_reason}.")
    worker_prompt = base_worker_prompt(mission, sot, context_files, preflight_red_flags)
    write_text(run_dir / "mission.md", worker_prompt)
    write_json(
        run_dir / "run_meta.json",
        {
            "config_path": str(config_path),
            "sot": sot,
            "selected_models": [model["id"] for model in selected_models],
            "disabled": disabled,
            "preflight_red_flags": preflight_red_flags,
            "knowledge_transfer": kt_settings,
            "archivist": archivist_config.get("id") if archivist_config else None,
            "archivist_unavailable_reason": archivist_reason,
            "dry_run": args.dry_run,
        },
    )

    adapters = [make_adapter(model, run_dir, max_output_chars) for model in selected_models]
    prompt_by_id = {adapter.id: worker_prompt for adapter in adapters}

    if args.dry_run:
        summary = {
            "run_dir": str(run_dir),
            "dry_run": True,
            "selected_models": [adapter.id for adapter in adapters],
            "disabled": disabled,
            "red_flags": preflight_red_flags,
            "sot": sot,
        }
        write_json(run_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    round1 = run_parallel(adapters, prompt_by_id, "round1")
    red_flags = red_flags_from_results(round1, disabled, preflight_red_flags)
    round1_capsules = generate_capsules(archivist_config, round1, mission, run_dir, "round1", kt_settings) if capsules_active else {}
    for model_id, capsule in round1_capsules.items():
        if not capsule.get("ok"):
            red_flags.append(f"RED FLAG: recall capsule for round1/{model_id} failed because {capsule.get('reason') or 'unknown error'}.")
    round1_knowledge_pack = build_knowledge_pack("Round-one worker knowledge", round1, round1_capsules, kt_settings)
    write_text(run_dir / "knowledge" / "round1_knowledge_pack.md", round1_knowledge_pack)

    synthesizer_config = config.get("synthesizer")
    round1_synthesis = ""
    if args.skip_synthesis or not run_settings.get("synthesis", True) or not synthesizer_config:
        round1_synthesis = fallback_round1(round1, red_flags)
    else:
        try:
            synth_adapter = make_adapter({**synthesizer_config, "id": str(synthesizer_config.get("id", "synthesizer"))}, run_dir, max_output_chars)
            synth_prompt = synthesis_round1_prompt(mission, sot, round1, red_flags, round1_knowledge_pack)
            synth_result = synth_adapter.run(synth_prompt, "synthesis/round1")
            if synth_result.get("ok"):
                round1_synthesis = synth_result["output"]
            else:
                red_flags.append(f"RED FLAG: {synth_adapter.id} did not participate because {synth_result.get('reason') or 'synthesis error'}.")
                round1_synthesis = fallback_round1(round1, red_flags)
        except Exception as exc:
            red_flags.append(f"RED FLAG: synthesizer did not participate because runner exception: {exc}.")
            round1_synthesis = fallback_round1(round1, red_flags)
    write_text(run_dir / "synthesis" / "round1_synthesis.md", round1_synthesis)

    round2: List[Dict[str, Any]] = []
    second_pass_enabled = bool(run_settings.get("second_pass", True)) and not args.skip_second_pass
    if second_pass_enabled:
        ok_by_id = {item["id"]: item for item in round1 if item.get("ok")}
        round2_adapters = [adapter for adapter in adapters if adapter.id in ok_by_id]
        session_by_id: Dict[str, str] = {}
        round2_prompts: Dict[str, str] = {}
        for adapter in round2_adapters:
            first = ok_by_id[adapter.id]
            resume_enabled = truthy(adapter.model.get("round2_resume"), True)
            session_id = first.get("session_id") if adapter.supports_resume and resume_enabled else None
            persistent = bool(session_id and adapter.supports_resume)
            if session_id:
                session_by_id[adapter.id] = str(session_id)
            peer_knowledge_pack = build_round2_peer_pack(
                adapter,
                mission,
                sot,
                first,
                round1_synthesis,
                red_flags,
                round1,
                round1_capsules,
                kt_settings,
                persistent,
            )
            round2_prompts[adapter.id] = adversarial_prompt(
                mission,
                sot,
                first.get("output", ""),
                round1_synthesis,
                peer_knowledge_pack,
                red_flags,
                persistent=persistent,
            )
        round2 = run_parallel(round2_adapters, round2_prompts, "round2", session_by_id=session_by_id)
        for result in round2:
            if not result.get("ok"):
                red_flags.append(f"RED FLAG: {result['id']} did not complete adversarial pass because {result.get('reason') or 'unknown error'}.")

    round2_capsules: Dict[str, Dict[str, Any]] = {}
    if round2 and capsules_active:
        round2_capsules = generate_capsules(archivist_config, round2, mission, run_dir, "round2", kt_settings)
        for model_id, capsule in round2_capsules.items():
            if not capsule.get("ok"):
                red_flags.append(f"RED FLAG: recall capsule for round2/{model_id} failed because {capsule.get('reason') or 'unknown error'}.")
    round2_knowledge_pack = (
        build_knowledge_pack("Round-two adversarial knowledge", round2, round2_capsules, kt_settings)
        if round2 and kt_settings.get("route_round_knowledge_to_final", True)
        else "No adversarial second-pass knowledge pack was produced."
    )
    write_text(run_dir / "knowledge" / "round2_knowledge_pack.md", round2_knowledge_pack)

    final_report = ""
    if args.skip_synthesis or not run_settings.get("synthesis", True) or not synthesizer_config:
        final_report = fallback_final(round1_synthesis, round2, red_flags, run_dir)
    else:
        try:
            final_adapter = make_adapter({**synthesizer_config, "id": str(synthesizer_config.get("id", "synthesizer-final"))}, run_dir, max_output_chars)
            final_prompt = final_synthesis_prompt(
                mission,
                sot,
                round1_synthesis,
                round2,
                round1,
                round1_knowledge_pack,
                round2_knowledge_pack,
                red_flags,
            )
            final_result = final_adapter.run(final_prompt, "synthesis/final")
            if final_result.get("ok"):
                final_report = final_result["output"]
            else:
                red_flags.append(f"RED FLAG: {final_adapter.id} did not participate because {final_result.get('reason') or 'final synthesis error'}.")
                final_report = fallback_final(round1_synthesis, round2, red_flags, run_dir)
        except Exception as exc:
            red_flags.append(f"RED FLAG: final synthesizer did not participate because runner exception: {exc}.")
            final_report = fallback_final(round1_synthesis, round2, red_flags, run_dir)
    final_report = append_missing_red_flags(final_report, red_flags)
    write_text(run_dir / "synthesis" / "final_report.md", final_report)

    summary = {
        "run_dir": str(run_dir),
        "selected_models": [adapter.id for adapter in adapters],
        "round1": [{"id": item["id"], "ok": item["ok"], "reason": item.get("reason", "")} for item in round1],
        "round2": [{"id": item["id"], "ok": item["ok"], "reason": item.get("reason", "")} for item in round2],
        "round1_capsules": [{"id": item["id"], "ok": item["ok"], "reason": item.get("reason", "")} for item in round1_capsules.values()],
        "round2_capsules": [{"id": item["id"], "ok": item["ok"], "reason": item.get("reason", "")} for item in round2_capsules.values()],
        "red_flags": red_flags,
        "final_report": str(run_dir / "synthesis" / "final_report.md"),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
