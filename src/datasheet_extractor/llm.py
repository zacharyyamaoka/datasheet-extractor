"""The LLM step: ask a logged-in CLI, headless and without tools, to propose values. Nothing it says is kept until
the checker confirms it.

Copied from robotparts tools/ingestor/llm.py (2026-10-29) and widened in one place: a prompt may carry page images
(Option 2 on a PDF or an image).

WHY a CLI and not an SDK: the Claude Agent SDK needs a metered ANTHROPIC_API_KEY and cannot use Zach's
subscription (vault memory `reference_claude_agent_sdk_no_subscription_auth`), so the extractor drives the
logged-in binary and keeps its raw event stream, one directory per call:

- `claude`: `claude -p --output-format stream-json --verbose --safe-mode --tools "" --json-schema …` — no tools, no
  CLAUDE.md, no hooks or MCP servers, our own system prompt, structured output. Images go in as base64 image blocks
  of one `--input-format stream-json` user message.
- `codex`: `codex exec --json --sandbox read-only --output-schema … [--image …]`. Its session file (which records
  the model that actually ran) is copied into the call directory.

Every call directory keeps prompt.txt, schema.json, request.json (argv, CLI version), events.jsonl (the raw
stream), stderr.txt and output.json.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import mimetypes
import os
import pathlib
import re
import shutil
import subprocess
import time

CLAUDE_BIN = os.environ.get("DATASHEET_EXTRACTOR_CLAUDE", "/home/bam/.local/bin/claude")
CODEX_BIN = os.environ.get("DATASHEET_EXTRACTOR_CODEX", "/home/bam/.local/bin/codex")
DEFAULT_MODELS = {"claude": "claude-opus-5-5", "codex": "gpt-6.1-sol"}
CODEX_EFFORT = "high"
TIMEOUT_S = 1800
MAX_PROMPT_CHARS = 400_000
# Metered or third-party credentials are stripped so a run can only use the CLI's own subscription login.
_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
              "CLAUDE_CODE_USE_FOUNDRY", "OPENAI_API_KEY", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION",
              "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
              "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_ACCOUNT_UUID",
              "CLAUDE_CODE_OAUTH_SCOPES")


@dataclasses.dataclass
class LlmResult:
    backend: str
    model: str | None             # the model that actually ran, as the CLI reports it
    output: dict | None           # the structured reply
    error: str | None
    run_dir: pathlib.Path
    cli_version: str | None
    seconds: float


def clean_env() -> dict:
    env = dict(os.environ)
    for name in _STRIP_ENV:
        env.pop(name, None)
    return env


def _version(binary: str) -> str | None:
    try:
        run = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60, env=clean_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (run.stdout or run.stderr).strip()
    return text.splitlines()[0] if text else None


def _json_object(text: str) -> dict | None:
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _run(cmd: list[str], stdin: bytes, run_dir: pathlib.Path, cwd: pathlib.Path) -> tuple[int | None, float]:
    events = run_dir / "events.jsonl"
    start = time.time()
    with open(events, "wb") as out, open(run_dir / "stderr.txt", "wb") as err:
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=cwd, env=clean_env())
        try:
            process.communicate(stdin, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return None, time.time() - start
    return process.returncode, time.time() - start


def parse_claude_events(path: pathlib.Path) -> tuple[dict | None, str | None, str | None]:
    """(structured output, model, error) from a `claude -p --output-format stream-json` event log."""
    output, model, error, unknown = None, None, None, []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            model = model or event.get("model")
        elif kind == "assistant":
            message = event.get("message") or {}
            if message.get("model") and message.get("model") != "<synthetic>":
                model = message["model"]
            for block in message.get("content") or []:
                if block.get("type") == "tool_use" and block.get("name") == "StructuredOutput" \
                        and isinstance(block.get("input"), dict):
                    output = block["input"]
            if event.get("error"):
                error = f"{event['error']}: " + " ".join(b.get("text", "") for b in message.get("content") or []
                                                          if b.get("type") == "text")
        elif kind == "result":
            if isinstance(event.get("structured_output"), dict):
                output = event["structured_output"]
            elif output is None and isinstance(event.get("result"), str) and not event.get("is_error"):
                output = _json_object(event["result"])
            if event.get("is_error"):
                error = error or str(event.get("result"))
        elif kind not in ("system", "user", "stream_event", "rate_limit_event"):
            unknown.append(kind)
    if unknown:
        error = (error + "; " if error else "") + f"unknown event types {sorted(set(map(str, unknown)))}"
    return output, model, error


def parse_codex_events(path: pathlib.Path) -> tuple[dict | None, str | None, str | None]:
    """(structured output, thread id, error) from a `codex exec --json` event log."""
    output, thread, error = None, None, None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "thread.started":
            thread = event.get("thread_id")
        elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
            parsed = _json_object(event["item"].get("text", ""))
            if parsed is not None:
                output = parsed
        elif kind in ("error", "turn.failed"):
            error = json.dumps(event)[:500]
    return output, thread, error


def _codex_session_model(thread: str | None, run_dir: pathlib.Path) -> str | None:
    """Copy Codex's own session log for this thread into the run and read the model it records."""
    if not thread:
        return None
    home = pathlib.Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")))
    matches = sorted((home / "sessions").glob(f"**/*{thread}*.jsonl"))
    if not matches:
        return None
    shutil.copyfile(matches[-1], run_dir / "codex-session.jsonl")
    model = None
    for line in matches[-1].read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn_context":
            model = (event.get("payload") or {}).get("model") or model
    return model


def _claude_stdin(prompt: str, images: list[pathlib.Path]) -> bytes:
    """One stream-json user message: every page image, then the prompt."""
    content = []
    for image in images:
        media_type = mimetypes.guess_type(image.name)[0] or "image/png"
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                    "data": base64.b64encode(image.read_bytes()).decode("ascii")}})
    content.append({"type": "text", "text": prompt})
    message = {"type": "user", "message": {"role": "user", "content": content}}
    return (json.dumps(message) + "\n").encode("utf-8")


def extract(backend: str, system_prompt: str, prompt: str, schema: dict, run_dir: pathlib.Path,
            model: str | None = None, images: list[pathlib.Path] | None = None) -> LlmResult:
    """One call. `images` (Option 2 on a PDF or an image) are shown before the prompt, in order."""
    images = list(images or [])
    model = model or DEFAULT_MODELS[backend]
    run_dir.mkdir(parents=True, exist_ok=True)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"the prompt is {len(prompt)} characters, over {MAX_PROMPT_CHARS}")
    (run_dir / "system-prompt.txt").write_text(system_prompt, encoding="utf-8")
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (run_dir / "schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    work = run_dir / "cli-cwd"
    work.mkdir(exist_ok=True)
    if backend == "claude":
        cmd = [CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose", "--safe-mode", "--tools", "",
               "--strict-mcp-config", "--no-session-persistence", "--model", model,
               "--system-prompt", system_prompt, "--json-schema", json.dumps(schema)]
        if images:
            cmd[2:2] = ["--input-format", "stream-json"]
            stdin = _claude_stdin(prompt, images)
        else:
            stdin = prompt.encode("utf-8")
        version = _version(CLAUDE_BIN)
    elif backend == "codex":
        # WHY not --ephemeral: Codex's session file is the only record of the model that ran.
        cmd = [CODEX_BIN, "exec", "--json", "--skip-git-repo-check", "--ignore-user-config", "--sandbox", "read-only",
               "-C", str(work), "-m", model, "-c", f'model_reasoning_effort="{CODEX_EFFORT}"',
               "--output-schema", str(run_dir / "schema.json"), "-o", str(run_dir / "last-message.txt")]
        # WHY `--image=` and the prompt's `-` first: `-i <FILE>...` is greedy and would read `-` as an image.
        cmd += ["-"] + [f"--image={image}" for image in images]
        stdin = (system_prompt + "\n\n" + prompt).encode("utf-8")
        version = _version(CODEX_BIN)
    else:
        raise ValueError(f"unknown backend {backend!r}")
    (run_dir / "request.json").write_text(json.dumps({
        "backend": backend, "requested_model": model, "cli": cmd[0], "cli_version": version,
        "argv": [arg if len(arg) < 400 else f"<{len(arg)} chars>" for arg in cmd[1:]],
        "codex_reasoning_effort": CODEX_EFFORT if backend == "codex" else None,
        "images": [str(image) for image in images], "prompt_chars": len(prompt)}, indent=2) + "\n", encoding="utf-8")
    code, seconds = _run(cmd, stdin, run_dir, work)
    events = run_dir / "events.jsonl"
    if backend == "claude":
        output, ran, error = parse_claude_events(events)
    else:
        output, thread, error = parse_codex_events(events)
        ran = _codex_session_model(thread, run_dir) or None
    if code is None:
        error = f"timed out after {TIMEOUT_S} s"
    elif code != 0 and error is None:
        error = f"exit {code}: " + (run_dir / "stderr.txt").read_text(encoding="utf-8", errors="replace")[-500:]
    if output is None and error is None:
        error = "no structured reply in the event log"
    (run_dir / "output.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return LlmResult(backend=backend, model=ran, output=output, error=error, run_dir=run_dir,
                     cli_version=version, seconds=round(seconds, 1))
