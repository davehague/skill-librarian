---
name: skill-librarian
description: Use when the user wants to make an agent skill self-contained and portable, or file a skill into a central skill library so it works on any machine and across agent runtimes (Claude Code, Codex, Hermes, OpenClaw, …). Migrates a skill out of wherever its runtime keeps skills (e.g. ~/.claude/skills or a project's .claude/skills) into the skill-library repo, vendoring in the scripts it depends on, extracting secrets and hardcoded paths into config.json, relocating state files, then VERIFYING it — static checks plus a full end-to-end run by a fresh agent given only the skill. Triggers on "skill-librarian", "add to skill library", "make this skill portable", "self-contain this skill", "vendor this skill", "package this skill", "migrate this skill". Reach for this whenever a skill needs to move and keep working — a plain copy silently breaks scripts, leaks secrets into git, or drags in a .venv.
---

# Skill Librarian

Migrate an agent skill into a central **skill-library** as a fully self-contained, portable unit — then prove it still works.

A skill is a folder of instructions (plus maybe some scripts) that an agent runtime loads. This came up around Claude Code skills in `~/.claude/skills/`, which is the running example throughout — but the problem and the fixes are runtime-agnostic. Migrating skills out of Codex, Hermes, OpenClaw, or anything else works the same way: point the librarian at the skill's folder, and treat that runtime's entry/manifest file the way these instructions treat `SKILL.md`.

## Why this exists

A skill that lives in `~/.claude/skills/<name>/SKILL.md` often isn't really self-contained. It calls a script three directories away, reads an API token from a repo's `.env`, hardcodes `/Users/you/...` paths, or scribbles a running log into the repo it happened to live in. Copy that folder to another machine — or hand it to a friend, or load it into a different agent — and it breaks in ways that only show up at runtime.

This skill fixes that, one skill at a time. The output is a folder that someone else can clone, fill in one `config.json`, and run.

The bar to clear: **after migration, the skill works from its new home with nothing outside its own folder except (a) other skills it explicitly depends on and (b) the values in `config.json`.**

## Configuration

The one input per run is **the path to the source skill folder** — the user points you at a folder; you don't hunt for it. `config.json` holds only the machine-level settings reused across every migration.

Read `config.json` from this skill's folder (`$SKILL_DIR/config.json`, where `$SKILL_DIR` is the directory containing this `SKILL.md`). If it's missing, copy `config.example.json` to `config.json` and ask the user to confirm the paths. See `README.md`.

| Key | Meaning |
|-----|---------|
| `skill_library_path` | Destination repo where migrated skills land, as an **absolute** path (e.g. `/Users/<username>/source/skill-library`) |
| `state_root` | Where relocated state goes, absolute (e.g. `/Users/<username>/.local/state/skills`) |

### Path style — one rule, no exceptions

Every path in any `config.json` is a full, literal, **absolute** path, used verbatim with no expansion (never `~`, never `$HOME`). `config.json` gets read by Python, bash, and argparse, and they expand `~`/`$HOME` differently — argparse not at all — so a stored tilde is a latent bug. `config.example.json` shows paths as `/Users/<username>/...`. Tilde / `$HOME` / XDG expansion is fine **only inside code** (a tool's own state dir, a default output location), which is a single known context.

### Notation

Two token styles appear below and mean different things: **`$NAME`** is a live shell variable the commands set and reuse (`$SKILL_DIR`, `$FRAMEWORK`, `$HOME`); **`<name>`** is a fill-in token — replace it with the real value (the skill's name, a `config.json` value, a sample arg) before running.

## The workflow

Work one skill at a time. Create a TodoWrite list with the seven phases below so nothing gets skipped. The easiest steps to drop — and the most expensive to skip — are the **gate** after the audit (don't change files before the human signs off), the two verify phases (static checks, then the end-to-end run), and the clean-up phase.

```
Phase 0  Locate & guard       → find the skill; bail if it already exists in the library
Phase 1  Audit dependencies   → classify everything the skill touches; is it a block?
─ GATE   Sign off with human  → report findings + plan; get the OK before changing any files
Phase 2  Migrate              → copy the body, vendor scripts, extract config, relocate state
Phase 3  Verify               → grep-clean, RUN the scripts, confirm secrets are git-ignored
Phase 4  Prove it end-to-end  → run the whole skill via a minimal-context subagent; fix what it flags
Phase 5  Report               → tell the user what moved, what config they must fill in
Phase 6  Deploy & clean up    → link the new skill into the runtimes, then tidy up with the user
```

### Phase 0 — Locate & guard

1. Take the source skill folder the user points you at. Read its entry file (`SKILL.md`, or the runtime's equivalent) and settle on the skill's name — default to the folder name unless the manifest says otherwise.
2. **Check for a `.librarian` opt-out marker in the source folder.** If it exists and declares `"status": "local-only"`, **stop and report its `reason` verbatim** — this skill was deliberately kept out of the library (its value is host- or runtime-specificity, so portability doesn't apply). Do not migrate, and do not re-run the audit to second-guess it. Proceed only on a **marker-aware override** — the user, *after you have shown them the marker's reason*, tells you to migrate anyway. A generic "yes, go ahead" given before they saw the marker does not count; surface the reason first, then let them decide. (See "Conventions" for the marker's shape and when to *write* one.)
3. Check `<skill_library_path>/<name>/`. **If it already exists, stop and report back** — do not overwrite. Migrations are not idempotent (config.json may hold real secrets; the body may have local edits). Let the user decide how to reconcile.
4. Confirm the destination name with the user if it's ambiguous.

### Phase 1 — Audit dependencies

Read the `SKILL.md` and list every file in the source folder. Then classify everything it touches. This audit drives the whole migration, so be thorough — grep the body and any scripts for path-like and secret-like strings.

| What you find | How to detect it | Action in Phase 2 |
|---------------|------------------|-------------------|
| **External script / project** | references to `scripts/...`, `../`, another repo path; a script the body calls | **Vendor** it into `<skill>/scripts/` (respect .gitignore — see below) |
| **Shared rules/docs from a workspace** (not a skill) | markdown/CSS/prompts the body *reads* that live in another repo or workspace | **Vendor** into `<skill>/references/<source>/` (provenance-named subfolder) |
| **Dependency on another skill** | "use the X skill", `Skill(...)`, `/some-skill` | **Reference only** — never copy. Note it as a prerequisite in the skill's README. |
| **User-provided secret** | an API key/token/password the user supplies and pastes in | **Extract** to `config.json` |
| **Shared provider key** | a provider's API key (OpenAI, Deepgram, Mailjet, Slack, …) that other skills on this machine also use | **Copy** it once to `~/.config/<provider>/credentials.env`; the skill's `config.json` holds that file's path |
| **Tool-obtained credential** | a token the tool fetches/refreshes itself (login, OAuth, device flow) and writes to disk | **Relocate** to `<state_root>/<name>/` and point the tool there — it's state the tool manages, not user config |
| **Hardcoded absolute path** | `/Users/...`, a vault path, a fixed data dir | **Extract** to `config.json` (absolute) |
| **Output / export destination** | where the skill writes the user's data or reports | Expose as a `config.json` key with a portable default; leave the *content* where the user expects it (don't move it into the state dir) |
| **State the skill writes about itself** | log, ledger, "running list", "last processed", a marker/`.json` it appends to | **Relocate** to `<state_root>/<name>/` |

Two judgment calls come up a lot:
- **State vs. output:** "if this file vanished, would the skill lose memory of what it's done?" Yes → state → relocate. Just a deliverable the user reads → output → leave the content, but make its destination a config key.
- **Secret vs. credential:** did the *user* hand you this secret (→ `config.json`), or did the *tool* obtain it through a login/OAuth flow and write it to disk (→ `state_root/<name>/`)? The Snipd-style `login` token is the latter.

#### Phase 1b — Determinism audit (classify the workflow, not just the dependencies)

While you're reading the body anyway, classify **each step of the skill's workflow**:

| Class | Test | Examples |
|---|---|---|
| **Mechanical** | A script could do it byte-for-byte: fixed paths, fixed commands, selection by rule, file reads/writes, sending, logging | "run `date`", "pick the newest unprocessed item vs a log", "email the file", "append a TSV row" |
| **Judgment** | The step's value IS the model's judgment: writing in a voice, summarizing, deciding, adapting to messy input | "write the newsletter", "draft posts in David's voice", "triage the incident" |

Then report the ratio and, when it's lopsided, propose **compiling** the mechanical steps into `scripts/` during migration: a runner does the plumbing, and each judgment step becomes ONE `claude -p` call with **all tools disabled** and its input on stdin. The full pattern, its payoff, and two worked examples live in `references/determinism-audit.md` — read it before proposing a compile.

When to push, when to leave alone:

- **Push hard for skills that run unattended** (a scheduled job invokes them headlessly). Every mechanical step an agent executes is a place it can stall on a prompt no one will answer — two real jobs burned 2h/night this way — and a toolless call is structurally immune. For these, the audit's compile proposal is the default, not an option.
- **Leave interactive, exploratory skills as prose.** If the workflow's value is adapting to what it finds (debugging, research, conversation), compiling removes the point. A 90%-judgment skill gets a "leave as prose" verdict — that is a finding, not a failure.
- **Never compile the voice.** Prose briefs stay in `SKILL.md` and the runner extracts them at runtime; duplicating prose into code is how the two drift.

#### Phase 1c — Block audit (who calls this skill?)

A **block** is a skill other skills, scripts, jobs or projects call; it gets a fixed `scripts/run.py` contract on top of the usual migration (`references/block-conventions.md`). Find the callers: grep the skill library, the runtime skills dirs, project `AGENTS.md`/`CLAUDE.md` files, and scheduled-job definitions for the skill's name and for paths into its scripts. By the **second-consumer rule**, anything that calls it besides a person, or more than one caller, makes it a block candidate. List each caller with how it calls today (by skill name, by hardcoded script path, by copied code). One person invoking it is an ordinary skill: say so and skip the block rules.

### Gate — sign off with the human (before touching files)

The audit produces a picture; **don't start changing files until the human has seen it.** This is the cheapest moment to catch a wrong call. Report back concisely, then wait for a go:

- **What you found** — the dependency classification (what gets vendored, what's referenced as a sibling skill, what becomes a `config.json` key, what's state vs. output), and especially the **non-obvious judgment calls**: a shared, read-only datastore → config, not a vendored copy; personal data (a bio, an audience profile) → config, kept out of the committed skill; a whole workspace's shared docs → vendored into `references/<source>/`.
- **The plan** — the config keys you'll create, the scripts you'll vendor and whether they need `uv`, where state/output will land, what you'll rewrite in the body.
- **Determinism verdict** (Phase 1b) — the mechanical/judgment ratio and your call: compile the mechanical steps into `scripts/` (say which, and what the one-or-few toolless LLM calls will be), or leave as prose (say why). For a skill that runs unattended, compiling is the default recommendation.
- **Block verdict** (Phase 1c) — the callers you found and your call: block or ordinary skill. For a block, name the entry point, the env-var prefix, any shared provider key moving to `~/.config/<provider>/`, and whether it needs `--dry-run` (it sends, publishes or deletes).
- **Name** — now that you understand what the skill actually does, assess its name. **Always show the current name and always make a recommendation** — even when you'd keep it. If the workflow points to something clearer or more accurate, propose it (e.g. `newsletter-setup` → `newsletter-profile-setup`, since it builds a profile, not sending infrastructure); if the current name is already the best fit, recommend keeping it and say why. The user picks; the chosen name becomes the library folder and the deployed skill name — re-check it doesn't collide in the library if you changed it.
- **Open questions** — only the genuinely ambiguous ones that would change the plan. Don't ask about what you can sensibly default; do ask when the right call depends on how the user works.

Then stop and let them confirm, adjust, or redirect — their answer can change what Phase 2 builds, which is the whole point of asking now instead of after. If the user has already told you to just proceed, state your plan in one pass and continue without waiting.

**If the outcome is "leave it local" — record it, don't just walk away.** When the human decides this skill should *not* be migrated because its value is host- or runtime-specificity (hardcoded to one machine's layout, an OS-bound mechanism, a deliberately Claude-only tool), **write a `.librarian` marker into the source folder** capturing that decision and its reason (see "Conventions" for the shape). This turns a one-time "nah, keep it local" into a durable signal so Phase 0 of the next run skips it instead of re-proposing the same migration. Then stop — writing the marker is the whole deliverable in that case.

### Phase 2 — Migrate

With the plan signed off at the gate above, build `<skill_library_path>/<name>/`.

**a. Copy the body.** Copy `SKILL.md` verbatim first; you'll edit the copy, never the original. The original keeps running until the user decides to switch over.

**b. Vendor scripts — but respect the source's `.gitignore`.** Before copying a script or project folder in, read the source repo's `.gitignore` (it's often at the repo root, not next to the script). Never carry across:
- `.venv/`, `venv/`, `__pycache__/`, `*.pyc`, `*.egg-info/`
- `node_modules/`, `dist/`, `build/`, `.next/`, `.turbo/`
- `.env` and anything else the repo ignores

For a Python tool, vendor the source files only — never the virtualenv, and **never depend on a globally `pip install`ed package** (that pollutes the machine's Python and collides across skills). Isolate dependencies with **`uv`** (the library default; a documented prerequisite):
- **Single-file script** → declare its deps inline with PEP 723 and run it via `uv run` (uv builds a cached, isolated env per dependency-set; nothing touches global Python):
  ```python
  #!/usr/bin/env python3
  # /// script
  # requires-python = ">=3.10"
  # dependencies = ["pypdf"]
  # ///
  ```
- **Multi-file project** → make a venv in the state dir and install into it: `uv venv <state_root>/<name>/.venv && uv pip install ...`, then invoke scripts with that interpreter. The venv is machine-specific, so it lives with state, not in the committed folder.

For a Node tool, copy `package.json` (and lockfile), not `node_modules`. The goal is "reproducible **and isolated** on a fresh machine," not "byte-for-byte copy."

**Where vendored things go — two standard folders, don't invent more:** runnable code → `<skill>/scripts/`. Reference material the body reads (rules, prompts, CSS, docs) → `<skill>/references/`; when it's borrowed from another skill or workspace, use a provenance-named subfolder `<skill>/references/<source>/` (e.g. `references/content-to-guide/`).

**c. Rewrite the vendored scripts to be location-independent.** A vendored script must resolve its own location and read config from the skill root, not from wherever it used to live:
```python
# was: load_dotenv(Path('/Users/me/some-repo/.env'))
config = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
token = config.get("some_token") or os.getenv("SOME_TOKEN")  # env fallback is a nice touch
```
Drop now-unneeded deps (e.g. `python-dotenv`) from the script's inline `dependencies` list.

**d. Extract user-provided secrets & paths into config.** For every user secret and hardcoded path from the audit:
- Add a key to `config.example.json` with a placeholder that shows the shape: paths as absolute `/Users/<username>/...`, secrets as `your_<thing>_here`.
- Add the real value to `config.json` (git-ignored), as an **absolute** path (expand any `~`/`$HOME` before writing). When the value already exists on this machine (a token in a repo `.env`, a known path), copy it in programmatically, **without printing the secret to the terminal**.
- In `SKILL.md`, replace the literal with a `config.json` read, used verbatim (no expansion). Establish the `$SKILL_DIR` convention near the top so every command can find config:
  ```bash
  vault_path=$(python3 -c "import json; print(json.load(open('$SKILL_DIR/config.json'))['vault_path'])")
  ```
  Tool-obtained credentials are *not* config — they go to state, see (e).

**e. Relocate state and tool-obtained credentials.** Point every state write — and any credential the tool fetches for itself (a `login`/OAuth token it writes to disk) — at `<state_root>/<name>/`. For a vendored tool that hardcodes something like `~/.foo_token`, repoint its token path to the standard state location and create the dir on write:
```python
_STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "skills", "<name>")
os.makedirs(_STATE, exist_ok=True)
TOKEN_FILE = os.path.join(_STATE, "token")
```
Migrate existing token/state by **copy** if the original location is still used elsewhere (e.g. a scheduled job that points at it), otherwise move it.

**f. Generate the support files** (the portable-skill pattern):
- `config.example.json` — committed, shows every key with placeholders (absolute `/Users/<username>/...` paths, `your_*_here` secrets), no real values
- `config.json` — git-ignored, real values for this machine
- `.gitignore` — contains `config.json` (a repo-root `.gitignore` with `**/config.json` also works; add a local one to be safe)
- `README.md` — human setup: copy config, fill keys, note `uv` as a prerequisite (deps are isolated via `uv run` / a state-dir venv, never installed globally), list any prerequisite skills and MCP servers

A skill with no secrets, no hardcoded paths, no scripts, and no state is just `SKILL.md` — a one-file copy. Don't manufacture config files it doesn't need.

**g. If it's a block,** build it to `references/block-conventions.md`: one `scripts/run.py` (recipe 6 in `migration-recipes.md` is the skeleton) with `--help` as the full contract, JSON out, JSON errors, exit 2 for a bad call and 1 for a failed run, a required `--caller`, one call-log line per call honouring `SKILLS_CALL_LOG`, and `<PREFIX>_<KEY>` environment variables. Move anything that needs another skill or judgment into `SKILL.md`. Give the `README.md` its `## Block contract` and `## Consumers` sections, listing every caller found in Phase 1c.

### Phase 3 — Verify (do not skip)

A migration that *looks* moved but silently broke a path is worse than no migration — it fails when the user is depending on it. Run the same three checks every time (four for a block), after the copy/edits are done:

1. **Grep clean.** No leftover source coupling in the migrated `SKILL.md` and scripts:
   ```bash
   grep -rnE "/Users/[a-z]+|\.env|load_dotenv|<source-repo-name>|os\.getenv\(['\"][A-Z_]+" <skill_library_path>/<name>
   ```
   Then **classify each hit** rather than reacting to the count. A literal `/Users/<you>/…` the code actually *uses* is real coupling: go back to Phase 2. But a skill that is *about* paths, or written in someone's voice, legitimately contains example paths and names in its prose — those are illustrative, not coupling. What must be gone is functional coupling: real machine paths the code reads, `.env`/`load_dotenv`, the source repo name in a code path. Fine to leave: `$SKILL_DIR`, `config.json` reads, `<state_root>`, and in-code `~/`/`$HOME` defaults.

2. **Run it — through the isolated env, not global Python.** Execute every vendored script from the new location so it exercises both the dependency isolation and the `config.json` read:
   ```bash
   SKILL_DIR=<skill_library_path>/<name>; uv run "$SKILL_DIR/scripts/<script>.py" <sample-args>
   ```
   Running with `uv run` proves the deps resolve from the script's own declaration (or the skill's state-dir venv) — so "it works" never means "it works because a package happens to be installed globally." (Quick sanity check: `uv run python -c "import <dep>"` *without* the script should fail — confirming nothing leaks from the ambient environment.) If a script can't fully run here (needs interactive auth, a device, live data), at minimum import-check it through `uv run` and confirm it finds and parses `config.json`. Note in the report what was fully run vs. smoke-tested. Read the raw output — a warning on stderr is fine, a traceback or empty stdout is not.

3. **Secret-safety.** Confirm git ignores the secret and tracks the example:
   ```bash
   git -C <skill_library_path> check-ignore <name>/config.json   # must print the path
   git -C <skill_library_path> status --short <name>/            # config.example.json staged, config.json absent
   ```

4. **Block contract (blocks only).** Run the bundled check with a sample call chosen from the block's `--help` examples. The sample must have no outside effect: a few cents of paid API is fine, anything that sends or publishes gets `--dry-run`.
   ```bash
   LIBRARIAN=<the folder holding this skill-librarian SKILL.md>   # not $SKILL_DIR, which step 2 set to the migrated skill
   uv run "$LIBRARIAN/scripts/check_block.py" <skill_library_path>/<name> --sample '<args from a --help example>'
   ```
   `{tmp}` in the sample becomes a temp folder, so output stays off the user's disk: `--sample '--text "Hi." --out {tmp}/hi.mp3'`.
   It checks the README contract parses, `--help` exits 0, a call without `--caller` exits 2 with a JSON error and empty stdout, and the sample prints one JSON object and writes exactly one log line with the core fields. Every call runs with a stripped environment (the no-agent proof) and the log pointed at a temp file, so nothing reaches the real log. Read its JSON result; `"ok": false` names each failed check.

If any check fails, fix it before moving on. Don't report success on an unverified migration.

### Phase 4 — Prove it end-to-end (minimal-context subagent)

Phase 3 confirms the parts; this confirms the whole — that a *fresh agent handed only the skill* can actually do the job on a real input. It catches what the static checks can't: a vendored doc still pointing at the old path, a step that silently relied on ambient context, a config key the body forgot to read, a dependency that only resolved because something happened to be installed globally. If the migration has a hole, this is where it shows — and it's the easiest phase to skip, so don't.

1. **Minimal context is the point.** Dispatch a subagent (the runtime's task/subagent tool) and give it almost nothing: the absolute path to the migrated `SKILL.md`, the value of `$SKILL_DIR` (so its `config.json` reads resolve), one realistic task/input, and the instruction to *follow that `SKILL.md` by path* — and explicitly NOT to use any installed skill of the same name (the original is often still loaded). Don't feed it the conventions, the audit, or hints. If it needs something you didn't put in the skill, that's a finding.
2. **Real but bounded input.** Representative and small (a few items, not hundreds) so the run is fast and the output is easy to check.
3. **Gate outward side-effects.** If the skill's happy path publishes, emails, pushes, or otherwise acts off the machine, confirm scope with the user first (full end-to-end vs. stop before the outward step). A local-output-only skill can run freely.
4. **Have it report friction precisely.** The subagent is a fresh pair of eyes — ask it to quote anything in the `SKILL.md` that was unclear, broken, missing, or worked-around. Apply those fixes now, while concrete; some turn out to be improvements to the *original* skill, not just the migration.
5. **A subagent usually can't spawn its own subagents.** If the skill's workflow fans out to subagents, the tester will run that step inline instead — a runtime limit, not a skill defect. Note it, and consider giving the skill a sequential fallback.

If a full run genuinely can't happen here (interactive auth, a device, live credentials it shouldn't use), say so in the report and fall back to the Phase 3 component run — don't silently skip this.

### Phase 5 — Report

Tell the user, concisely:
- **Moved/vendored:** which scripts came across, which were dropped (`.venv`, etc.)
- **Config keys created:** and which you auto-filled vs. which they must fill in
- **State relocated:** old path → `<state_root>/<name>/`
- **Prerequisite skills/MCPs:** anything referenced but not copied
- **Verification:** static checks (Phase 3) + the end-to-end run (Phase 4) — what passed, what the test subagent flagged and you fixed, what was only smoke-tested
- **Block (if it is one):** its consumers, the `check_block.py` result, and whether the sample call ran
- **Not committed** unless they asked — leave staging to them

### Phase 6 — Deploy, then clean up

The migration is verified and reported. Now **deploy the skill** so it actually loads, then tidy up. Deploying is something you do (safely — see below); the cleanup items are offered, and nothing that touches files the user already had goes ahead without a yes.

- **Deploy it — do this yourself, don't just hand over a command.** Deploying links the skill into every runtime's skills dir (symlink on macOS/Linux, directory junction on Windows) so it loads now, and future edits / `git pull`s propagate everywhere. `deploy.py` lives at the root of the skill-librarian framework repo, one level up from this skill's own folder. Run it:
  1. **Locate `deploy.py`** by resolving this skill's *real* path (it's usually symlinked into the runtime dir, so follow the link):
     ```bash
     FRAMEWORK=$(python3 -c "import os,sys; print(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1]))))" "$SKILL_DIR/SKILL.md")
     ```
  2. **Register the skill's library.** `deploy.py` only sees skills in the libraries listed under `libraries` in `$FRAMEWORK/deploy.json`. The skill you just migrated lives in `skill_library_path`; if that path isn't already in `deploy.json`'s `libraries`, add it (or ask the user to), or `--skill` won't find it.
  3. **Preview, then link:**
     ```bash
     python3 "$FRAMEWORK/deploy.py" --dry-run --skill <name>   # show the plan
     python3 "$FRAMEWORK/deploy.py" --skill <name>             # do it
     ```
     `deploy.py` prompts before replacing any real (drifted) directory, so running it is safe; nothing destructive happens without a yes.
- **A block's consumers.** For each caller found in Phase 1c, offer to repoint it at `uv run <skill_library_path>/<name>/scripts/run.py --caller <consumer> ...` (programs call the library path, not a runtime's skills dir), add the block to the consumer's `AGENTS.md` "Depends on" section, and run the consumer once. These are edits in other repos: list them and get a yes first.
- **The original skill.** Once deployed, the source copy is redundant and will **double-load** if it sits in a dir the same runtime also reads. Lay out the options:
  - **Delete it** (recommended once deploy succeeds) — the verified library copy now loads via the link, so the original is a stale duplicate. Confirm before deleting.
  - **Leave it** — only if it's in a dir the runtime doesn't also load; otherwise it collides with the deployed link.
- **Migration scratch.** The verify run may have left `__pycache__/` or `*.pyc` in the vendored scripts, or temp/test output. Offer to remove it. (`git -C <skill_library_path> status --short <name>/` shows anything stray.)
- **Now-orphaned source scripts** you vendored in (e.g. an external `scripts/foo.py`). They may be removable from the source repo, but only if nothing else uses them — the user knows that, you don't. Point them out.
- **Old state files** at the pre-migration location, if you relocated state and didn't move the history across.

## Conventions this skill enforces

- **`$SKILL_DIR`** = the folder holding `SKILL.md`. The running agent resolves it; config is always `$SKILL_DIR/config.json`.
- **Two standard folders:** `scripts/` for runnable code, `references/` for docs the skill reads on demand (borrowed material in `references/<source>/`). Don't invent new top-level folders.
- **Dependencies are isolated with `uv`** — PEP 723 inline deps + `uv run` for single-file scripts, a state-dir venv for projects. Never `pip install` into the global/system Python.
- **Scripts self-locate** via `Path(__file__)` and read config from the skill root — they never assume a working directory.
- **State** lives under `<state_root>/<name>/`, never inside the skill folder (keeps the folder a clean, shareable artifact) and never inside a random repo.
- **Secrets** never appear in committed files or terminal output. A provider key several skills share lives once in `~/.config/<provider>/credentials.env`, and the skill's config holds its path.
- **Blocks** — skills other programs call — follow `references/block-conventions.md`: one `scripts/run.py` contract, a required `--caller`, a shared call log at `~/.local/state/skills/_calls.jsonl` (or `$SKILLS_CALL_LOG`), and a `## Block contract` + `## Consumers` section in the README, which `deploy.py` turns into the library's Blocks catalogue.
- **`.librarian` opt-out marker.** A skill deliberately kept out of the library carries a `.librarian` JSON file in its *source* folder recording that call, so the decision survives instead of being re-litigated every pass. Shape:
  ```json
  {
    "status": "local-only",
    "reason": "Why this skill stays where it is — the host/runtime-specificity that makes portability pointless.",
    "date": "YYYY-MM-DD",
    "decided_by": "who signed off"
  }
  ```
  Phase 0 reads it and stops (reporting the `reason`); the Gate writes it when a human declines migration. `status: "local-only"` is the skip signal — the runtime ignores the file (it only loads the entry manifest), so it is inert to the skill itself and safe to commit if the folder is ever version-controlled.

## Golden reference

`references/case-study-daily-summary.md` walks through one full migration end to end — a skill with a vendored script, an extracted secret, and an extracted path — showing how the phases play out and what the finished folder looks like. Read it when you want a worked example to match. `references/migration-recipes.md` has the atomic before/after snippets for the common cases (dotenv→config, hardcoded path→config, state relocation, vendoring a Python CLI). `references/block-conventions.md` is the rule set for block skills (Phase 1c, 2g, 3.4), with a worked example. `references/determinism-audit.md` is the Phase 1b companion: the compile pattern (mechanical steps → `scripts/`, judgment steps → toolless `claude -p` calls), the failure modes it eliminates, and two worked examples.

## Dogfooding

This skill follows its own pattern: its machine-specific paths live in `config.json`, and it carries a `config.example.json`, `.gitignore`, and `README.md`. It belongs in the library too — migrating it is a good self-test.
