# skill-librarian

The framework for **portable agent skills** (Claude Code, Codex, and other runtimes): a convention for making a skill self-contained, the `skill-librarian` skill that migrates skills into that shape, and `deploy.py` to link skills into your runtimes.

This repo is shareable and carries no personal data. Your own skills live in a **separate library** — a private repo of skill folders — which `deploy.py` deploys alongside the ones here.

Every skill is **self-contained**: it carries the scripts, assets, and setup it needs, so it can be dropped onto any machine and work after a one-time config step.

## Requirements

- **Python 3** — used by `deploy.py` and by any skill that ships Python scripts.
- **[uv](https://docs.astral.sh/uv/)** — the default for isolating a skill's Python dependencies. Skills declare deps inline (PEP 723) and run via `uv run`, so nothing installs into your global/system Python. `brew install uv`.

## What "self-contained" means here

1. **No outside-the-folder dependencies.** Scripts and assets a skill needs are vendored into the skill's own folder (usually `scripts/`). The one exception: a skill may depend on *other skills* — those are referenced, not copied.
2. **Machine-specific values live in `config.json`.** Secrets (API tokens) and absolute paths (vaults, data dirs) never get hardcoded into `SKILL.md` or scripts. They go in `config.json`, which is git-ignored. A committed `config.example.json` shows the shape, and `README.md` tells a human how to fill it in.
3. **State is centralized.** Anything a skill writes to track itself across runs (logs, running lists, "last processed" markers) goes under `~/.local/state/skills/<skill-name>/`, never inside the skill folder or a random repo path.

## Per-skill layout

```
<skill-name>/
├── SKILL.md              # the skill (reads config.json for machine-specific values)
├── scripts/              # vendored scripts (deps declared inline via PEP 723, run with `uv run`)
├── references/           # docs the skill reads on demand; borrowed material in references/<source>/
├── config.example.json   # committed — the shape of config.json
├── config.json           # git-ignored — real secrets/paths for THIS machine
├── .gitignore            # ignores config.json
└── README.md             # one-time setup for a human
```

A skill with no secrets, no hardcoded paths, and no scripts is just `SKILL.md` — nothing else required.

## Conventions

- **`$SKILL_DIR`** in a `SKILL.md` means "the absolute path of the folder containing this `SKILL.md`." The running agent resolves it. Config is always read from `$SKILL_DIR/config.json`.
- **Two standard folders:** `scripts/` for code, `references/` for docs the skill reads (borrowed material in `references/<source>/`). Skills don't invent other top-level folders.
- **Python deps are isolated with `uv`** — inline PEP 723 deps + `uv run` for single-file scripts, a state-dir venv for projects. Never `pip install` into global Python.
- Scripts resolve their own location (`Path(__file__).parent`) and read `config.json` from the skill root — they never assume a working directory.
- Output files (things the user wants to keep, e.g. a report in `~/Downloads`) are *output*, not state — leave those where the user expects them.

## Deploying to your runtimes

The library is the single source of truth. `deploy.py` links each skill into every runtime's skills dir, so an edit here — or a `git pull` — shows up everywhere with no copying and nothing to keep in sync.

```bash
python3 deploy.py            # link all skills into all runtimes
python3 deploy.py --dry-run  # preview, change nothing
python3 deploy.py --skill X  # just one skill
```

It uses a symlink on macOS/Linux and a directory junction on Windows (auto-detected), so the same command works on any OS. It deploys the skills in this repo **plus** any skill libraries you list in a git-ignored `deploy.json` (copy `deploy.example.json`); targets default to `~/.claude/skills` and `~/.codex/skills`:

```json
{
  "libraries": ["/Users/you/source/skill-library"],
  "targets":   ["/Users/you/.claude/skills", "/Users/you/.codex/skills"]
}
```

Before deploying a skill, do its one-time setup from its own `README.md` (`cp config.example.json config.json`, fill it in; install `uv` if the skill ships Python — its deps are isolated, not global). If a runtime dir already has a real (drifted) copy of a skill, `deploy.py` asks before replacing it with a link.

**Skill list in your library's README.** Put `<!-- skills:start -->` and `<!-- skills:end -->` on their own lines in the library's `README.md`, and every `deploy.py` run rebuilds the list between them from the skill folders: one line per skill, taken from the first paragraph of the skill's `README.md` (or the first sentence of its `description`). Don't edit the list by hand. A README without the markers is left alone.

**Blocks catalogue.** A *block* is a skill other skills, scripts or projects call: it has one `scripts/run.py` entry point with a fixed contract (JSON out, a required `--caller`, a shared call log) and a `## Block contract` section in its `README.md`. The rules are in [`skill-librarian/references/block-conventions.md`](skill-librarian/references/block-conventions.md). Add `<!-- blocks:start -->` and `<!-- blocks:end -->` to the library README, above the skills list, and `deploy.py` fills it with each block's purpose, inputs, outputs, entry point and consumers. Blocks then leave the plain skills list. `skill-librarian/scripts/check_block.py <skill folder>` verifies a block against the contract.

**On a new machine:** clone this repo (and your private library), `cp deploy.example.json deploy.json` and set the paths, run `python3 deploy.py`, then fill in each skill's `config.json`.
