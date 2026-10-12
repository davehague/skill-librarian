# Migration recipes

Worked snippets for the cases that come up most. All assume `$SKILL_DIR` is the
migrated skill's folder and `config.json` lives at its root.

## 1. dotenv / env-var secret → config.json

A script that loaded a secret from a repo `.env`:
```python
# BEFORE — couples the script to one repo's .env
from dotenv import load_dotenv
load_dotenv(Path("/Users/me/some-repo/.env"))
token = os.getenv("SOME_TOKEN")
```
```python
# AFTER — reads config.json from the skill root, with an env fallback
import json, os
from pathlib import Path

def load_config():
    p = Path(__file__).resolve().parent.parent / "config.json"  # adjust ../ to depth
    return json.loads(p.read_text()) if p.exists() else {}

token = load_config().get("some_token") or os.getenv("SOME_TOKEN")
```
Then drop `python-dotenv` from the script's inline `dependencies` if nothing else needs it.

## 2. Hardcoded path in SKILL.md → config.json

```bash
# BEFORE
vault_path="/Users/me/Library/Mobile Documents/.../Vault"

# AFTER — read it from config at runtime
vault_path=$(python3 -c "import json; print(json.load(open('$SKILL_DIR/config.json'))['vault_path'])")
```
Add `"vault_path"` to both `config.json` (real value) and `config.example.json` (placeholder). For shell-only skills without python, `jq -r .vault_path "$SKILL_DIR/config.json"` works too.

## 3. State file → ~/.local/state/skills/<name>/

A skill that appended to a log/ledger inside its old repo:
```bash
# BEFORE
echo "$entry" >> /Users/me/some-repo/processed.log

# AFTER — central, per-skill state dir
state_dir="$(python3 -c "import json,os;print(os.path.expanduser(json.load(open('$SKILL_DIR/config.json'))['state_root']))")/<name>"
mkdir -p "$state_dir"
echo "$entry" >> "$state_dir/processed.log"
```
If the old state file has history worth keeping, move it once during setup:
`mkdir -p "$state_dir" && mv /old/path/processed.log "$state_dir/" 2>/dev/null || true`.

## 4. Vendoring a Python CLI (without the venv, isolated with uv)

Source tool at `~/source/some-repo/scripts/mytool/` with a `.venv`, `__pycache__`, etc.

1. Copy only the source — let the source repo's `.gitignore` tell you what to skip:
   ```bash
   rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
         --exclude='.env' --exclude='node_modules' \
         ~/source/some-repo/scripts/mytool/ "$SKILL_DIR/scripts/mytool/"
   ```
2. Isolate the deps with `uv` — never a global `pip install`:
   - **Single entry-point script** → add a PEP 723 header so `uv run` provisions a cached, isolated env:
     ```python
     # /// script
     # requires-python = ">=3.10"
     # dependencies = ["requests", "pypdf"]
     # ///
     ```
   - **Multi-file project** → keep a `requirements.txt`, but build a venv in the state dir and install into it: `uv venv "$state_dir/.venv" && uv pip install --python "$state_dir/.venv/bin/python" -r requirements.txt`. Invoke with that interpreter. Don't carry the source venv — it's machine- and path-specific.
3. Repoint any `.env`/secret reads at `config.json` (recipe 1).
4. Verify with `uv run` (proves isolation): `uv run "$SKILL_DIR/scripts/mytool/<entry>.py" <sample-args>`. A bare `uv run python -c "import <dep>"` should fail — confirming nothing leaks from the ambient environment.

## 5. Verification commands (Phase 3)

```bash
DEST=<skill_library_path>; NAME=<name>

# 1. grep clean — only $SKILL_DIR / config.json / state_root / ~ should remain
grep -rnE "/Users/[a-z]+|\.env|load_dotenv|os\.getenv\(['\"][A-Z_]+" "$DEST/$NAME" \
  && echo "↑ leftover coupling — go back to Phase 2" || echo "grep clean ✓"

# 2. run every vendored script from the new home — through uv (isolated env)
SKILL_DIR="$DEST/$NAME"; uv run "$SKILL_DIR/scripts/<script>.py" <sample-args>

# 3. secret-safety
git -C "$DEST" check-ignore "$NAME/config.json"      # must print the path
git -C "$DEST" status --short "$NAME/"               # example tracked, config.json absent
```

## 6. Block skill: run.py skeleton with the call log

For a block (see `block-conventions.md`). The parts every block shares: JSON errors, exit codes 2 and 1, the required `--caller`, and one call-log line per call, failed calls included. Fill in `build_parser()` and `run()`; keep the rest as is.

```python
SKILL_NAME = "<name>"
CALL_LOG = "~/.local/state/skills/_calls.jsonl"


class BlockError(Exception):
    """A failure the caller can act on. exit_status 2 = bad call, 1 = failed run."""

    def __init__(self, code, message, exit_status=1):
        super().__init__(message)
        self.code, self.message, self.exit_status = code, message, exit_status


class Parser(argparse.ArgumentParser):
    def error(self, message):   # argparse would print usage and exit 2; answer in JSON instead
        raise BlockError("bad_arguments", f"{message}. Run with --help for the full contract.", 2)


def append_call_log(entry):
    path = Path(os.environ.get("SKILLS_CALL_LOG") or CALL_LOG).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        # The work matters more than the log line; say so without failing the call.
        print(json.dumps({"warning": f"could not write call log {path}: {e.strerror}"}), file=sys.stderr)


def main(argv=None):
    started = time.time()
    entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "skill": SKILL_NAME, "caller": None, "exit_status": 0, "elapsed_ms": 0}
    try:
        args = build_parser().parse_args(argv)   # --help exits 0 here and logs nothing
        entry["caller"] = args.caller
        if not (args.caller or "").strip():
            raise BlockError("no_caller", "--caller is required: name the project, skill or job "
                             "making this call. It goes in the shared call log.", 2)
        result = run(args, entry)                # run() may add provider, model, units, unit, cost
        print(json.dumps({"ok": True, **result}, indent=2, ensure_ascii=False))
    except BlockError as e:
        entry.update(exit_status=e.exit_status, error=e.code)
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message}}), file=sys.stderr)
    except Exception as e:   # a bug in this script; still answer in JSON and still log the call
        entry.update(exit_status=1, error="internal")
        print(json.dumps({"ok": False, "error": {"code": "internal", "message": f"{type(e).__name__}: {e}"}}),
              file=sys.stderr)
    entry["elapsed_ms"] = int((time.time() - started) * 1000)
    append_call_log(entry)
    return entry["exit_status"]


if __name__ == "__main__":
    sys.exit(main())
```
