#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
check_block.py -- verify a block skill against the block conventions.

    uv run check_block.py <skill folder> [--sample '<args>']

Checks, in order:
  1. README.md has a `## Block contract` (inputs, outputs, an entry point that
     exists) and a `## Consumers` section.
  2. `<entry> --help` exits 0 and prints something.
  3. A call without --caller exits 2, prints nothing on stdout, puts a JSON
     error on stderr that names the caller, and still writes one log line.
  4. With --sample: the sample call plus `--caller skill-verify` exits 0,
     prints one JSON object on stdout, and writes exactly one log line with
     the core fields (ts, skill, caller, exit_status, elapsed_ms).

Every call runs with a stripped environment (PATH, HOME, TMPDIR, USER, LANG
only) and SKILLS_CALL_LOG pointing at a temporary file: that proves the script
runs with no agent present, and no test line reaches the real call log.

In --sample, `{tmp}` is replaced with a temporary folder, so output can be
kept off the user's disk: --sample '--text "Hi." --out {tmp}/hi.mp3'.
The sample must have no outside effect: use --dry-run for anything that sends.

Prints a JSON result on stdout. Exit 0 if every check passed, 1 if any
failed, 2 for a bad call to this script. Standard library only.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CORE_FIELDS = ("ts", "skill", "caller", "exit_status", "elapsed_ms")
KEEP_ENV = ("PATH", "HOME", "TMPDIR", "USER", "LANG")
VERIFY_CALLER = "skill-verify"


def block_contract(readme_text):
    """The `## Block contract` and `## Consumers` sections, or None for an ordinary skill.

    Same parser as deploy.py's block_contract() -- keep the two in step.
    """
    sections, current = {}, None
    for line in readme_text.splitlines():
        m = re.match(r"## +(.+?)\s*$", line)
        if m:
            current = m.group(1).strip().lower()
            sections[current] = []
        elif current:
            sections[current].append(line)
    if "block contract" not in sections:
        return None
    fields, key = {}, None
    for line in sections["block contract"]:
        m = re.match(r"- \*\*([^*]+?):\*\*\s*(.*)", line)
        if m:
            key = m.group(1).strip().lower()
            fields[key] = m.group(2).strip()
        elif key and line.strip() and not line.startswith(("- ", "#")):
            fields[key] += " " + line.strip()
        else:
            key = None
    entry = fields.get("entry point", "")
    tick = re.search(r"`([^`]+)`", entry)
    return {"inputs": fields.get("inputs", ""), "outputs": fields.get("outputs", ""),
            "entry": tick.group(1) if tick else entry,
            "consumers": re.findall(r"^- \*\*([^*]+?)\*\*", "\n".join(sections.get("consumers", [])), re.M),
            "has_consumers": "consumers" in sections}


class Checker:
    def __init__(self, skill_dir, entry, log, timeout):
        self.skill_dir, self.entry, self.log, self.timeout = skill_dir, entry, log, timeout
        self.checks = []

    def record(self, name, ok, detail=""):
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    def command(self, args):
        if self.entry.suffix == ".py":
            return ["uv", "run", "--quiet", str(self.entry), *args]
        return ["bash", str(self.entry), *args]

    def call(self, args):
        """Run the entry point stripped of the agent's environment. Returns (code, out, err, new log lines)."""
        env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
        env["SKILLS_CALL_LOG"] = str(self.log)
        before = self.log.read_text().splitlines() if self.log.exists() else []
        try:
            done = subprocess.run(self.command(args), capture_output=True, text=True, env=env,
                                  cwd=tempfile.gettempdir(), timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return None, "", f"timed out after {self.timeout}s", []
        after = self.log.read_text().splitlines() if self.log.exists() else []
        return done.returncode, done.stdout, done.stderr, after[len(before):]

    def log_line(self, name, lines, caller, status):
        if len(lines) != 1:
            return self.record(name, False, f"expected exactly 1 new line in the call log, got {len(lines)}; "
                               "does the script honour SKILLS_CALL_LOG?")
        try:
            line = json.loads(lines[0])
        except json.JSONDecodeError as e:
            return self.record(name, False, f"log line is not JSON: {e}")
        missing = [f for f in CORE_FIELDS if f not in line]
        problems = []
        if missing:
            problems.append(f"missing core field(s) {', '.join(missing)}")
        if line.get("skill") != self.skill_dir.name:
            problems.append(f"skill is {line.get('skill')!r}, expected {self.skill_dir.name!r}")
        if line.get("caller") != caller:
            problems.append(f"caller is {line.get('caller')!r}, expected {caller!r}")
        if line.get("exit_status") != status:
            problems.append(f"exit_status is {line.get('exit_status')!r}, expected {status}")
        return self.record(name, not problems, "; ".join(problems) or "core fields present")


def stderr_error(err):
    """The JSON error object from stderr (the last JSON line holding `error`), or None."""
    for line in reversed(err.strip().splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            return data
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify a block skill against the block conventions.")
    ap.add_argument("skill_dir", help="the skill's folder")
    ap.add_argument("--sample", metavar="ARGS",
                    help="arguments for one real call, without --caller; {tmp} becomes a temp folder")
    ap.add_argument("--timeout", type=int, default=300, metavar="S", help="per call (default 300)")
    a = ap.parse_args(argv)

    skill_dir = Path(a.skill_dir).expanduser().resolve()
    if not (skill_dir / "SKILL.md").is_file():
        print(json.dumps({"ok": False, "error": f"{skill_dir} has no SKILL.md"}))
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="check_block-"))
    result = {"skill": skill_dir.name, "entry": None, "sample_run": False}
    try:
        readme = skill_dir / "README.md"
        contract = block_contract(readme.read_text()) if readme.is_file() else None
        checker = Checker(skill_dir, None, tmp / "calls.jsonl", a.timeout)
        if contract is None:
            checker.record("contract", False, "README.md has no '## Block contract' section")
        else:
            gaps = [f"no {k}" for k in ("inputs", "outputs", "entry point") if not contract[k.split()[0]]]
            entry = skill_dir / contract["entry"] if contract["entry"] else None
            if entry is not None and not entry.is_file():
                gaps.append(f"entry point {contract['entry']} does not exist")
            if not contract["has_consumers"]:
                gaps.append("no '## Consumers' section")
            checker.record("contract", not gaps, "; ".join(gaps) or
                           f"consumers: {', '.join(contract['consumers']) or 'none yet'}")
            if entry is not None and entry.is_file():
                checker.entry = entry
                result["entry"] = contract["entry"]

        if checker.entry is not None:
            code, out, err, _ = checker.call(["--help"])
            checker.record("help", code == 0 and out.strip(),
                           "exit 0" if code == 0 and out.strip() else f"exit {code}, stdout {len(out)} chars: {err[-300:]}")

            sample = shlex.split(a.sample.replace("{tmp}", str(tmp))) if a.sample else []
            code, out, err, lines = checker.call(sample)
            error = stderr_error(err)
            problems = []
            if code != 2:
                problems.append(f"exit {code}, expected 2")
            if out.strip():
                problems.append("printed on stdout")
            if error is None:
                problems.append("no JSON error on stderr")
            elif "caller" not in json.dumps(error["error"]).lower():
                problems.append(f"the error does not mention the caller: {error['error']}")
            checker.record("no_caller", not problems, "; ".join(problems) or f"exit 2, {error['error'].get('code')}")
            checker.log_line("no_caller_logged", lines, None, 2)

            if sample:
                result["sample_run"] = True
                code, out, err, lines = checker.call(sample + ["--caller", VERIFY_CALLER])
                try:
                    data = json.loads(out)
                    shape = isinstance(data, dict) and data.get("ok", True) is True
                except json.JSONDecodeError:
                    data, shape = None, False
                checker.record("sample", code == 0 and shape,
                               "exit 0, one JSON object on stdout" if code == 0 and shape
                               else f"exit {code}; stdout {'is not one JSON object' if not shape else 'ok'}; "
                                    f"stderr: {err.strip()[-400:]}")
                checker.log_line("sample_logged", lines, VERIFY_CALLER, 0)
            else:
                checker.record("sample", True, "not run: no --sample given")

        result["checks"] = checker.checks
        result["ok"] = all(c["ok"] for c in checker.checks)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({"ok": result.pop("ok"), **result}, indent=2))
    return 0 if all(c["ok"] for c in result["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
