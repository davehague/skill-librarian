# Block conventions

A **block** is a skill that other skills, scripts, jobs or projects call. Most skills are not blocks: a skill a person invokes is an ordinary skill and none of this applies to it. A block is still a normal portable skill (config, state, `uv`, everything in `SKILL.md`), plus the rules below, which make it callable by a program with no agent present.

The test for making a skill a block is the **second-consumer rule**: once something other than a person calls it, or more than one thing does, it becomes a block. Phase 1c finds the callers; the human decides at the gate.

## The two layers

1. **`scripts/run.py` (or `run.sh`) is the real API.** It does the whole deterministic job on its own. Python, TypeScript, cron, Codex and any other runtime call it as a subprocess. No LLM is involved, so no vendor is.
2. **`SKILL.md` wraps it** for the parts that need judgment (choosing options, preparing input, calling another skill first). It tells the agent how to call the script; it never repeats the contract.

## The rules

1. **One entry point,** `scripts/run.py` or `scripts/run.sh`.
   - `--help` is the full contract: every flag, its environment variable, the JSON output shape, the exit codes, and a few examples.
   - Input as arguments, a file, or JSON on stdin.
   - On success: exit 0 and one JSON object on stdout. Nothing else on stdout.
   - On failure: nothing on stdout, and `{"ok": false, "error": {"code": "...", "message": "..."}}` on stderr. The message says what is wrong and what is valid, so the caller can fix exactly that.
2. **Exit codes.** `0` worked. `2` a bad call: arguments, config, input; fix the call. `1` a failed run: provider error, disk, network; safe to retry.
3. **No calls to other skills from inside the script.** Scripts may call scripts and programs. Anything that needs another skill or a judgment step lives in `SKILL.md`. Example: a text-to-speech block does not fetch URLs; its `SKILL.md` says to run the web-fetching skill first and pass the file.
4. **`--caller` is required.** It names the project, skill or job making the call. A call without it exits 2. When a person asks an agent directly, the agent passes its runtime's name (`claude-code`, `codex`).
5. **One line per call in the shared call log** (below), failed calls included.
6. **Settings, in this order, first value wins:** flag, environment variable, `config.json`, built-in default. Every `config.json` key has all three spellings:
   - flag `--out-dir`, environment variable `<PREFIX>_OUT_DIR`, config key `out_dir`.
   - `<PREFIX>` is short and unique to the skill (`TTS_`), because bare names like `FORMAT` collide across skills. State the prefix in `--help`.
   - Nothing about a particular consumer is stored in the skill. A consumer overrides from its own side, with flags or environment.
7. **Secrets.**
   - A provider key that more than one skill uses lives once in `~/.config/<provider>/credentials.env` (`OPENAI_API_KEY=...`). The skill's config has a `<provider>_credentials` key holding that file's path, so a consumer can point at a different file.
   - A secret only this skill uses stays in its git-ignored `config.json`, as for any skill.
   - Keys are never passed on the command line and never read from the environment by name, so they do not show up in process lists or logs.
   - When lifting a key out of a repo's `.env` into `~/.config/<provider>/`, **copy** it. Jobs may still read the old file.
8. **Paths.** As for every skill: absolute paths in `config.json` and `config.example.json` (`/Users/<username>/...`), `~` only in defaults inside the code.
9. **Outside effects.** A block that sends, publishes, deletes or spends beyond a few cents has `--dry-run`, which validates everything and does the work up to the outward step. A block that sends must not send twice when a caller retries: take a caller-supplied id (`--idempotency-key`) and record sent ids in the skill's state folder.
10. **How programs find it.** Programs call the script at its **library path**, `uv run <skill_library_path>/<name>/scripts/run.py`, not through a runtime's skills folder, which belongs to one agent harness. Agents use `$SKILL_DIR` as usual. No other machinery is needed for Codex, Hermes or other runtimes.
11. **Sandboxed runners.** A block run inside a write-restricted sandbox (for example an `agent-run task` with `--write-dir`) needs write access to its output folder and to the call log's folder. Each block's `SKILL.md` setup section names both. A refused log write is a warning, so the result is not lost.

## The call log

One JSON line per call, appended to `~/.local/state/skills/_calls.jsonl`, or to the file named by the `SKILLS_CALL_LOG` environment variable when it is set. Every block honours `SKILLS_CALL_LOG`; the verify check uses it to keep test calls out of the real log.

| Field | Required | Meaning |
|---|---|---|
| `ts` | yes | UTC ISO 8601 time the call started |
| `skill` | yes | the skill's name |
| `caller` | yes | the `--caller` value (`null` when it was missing) |
| `exit_status` | yes | the process exit code |
| `elapsed_ms` | yes | wall time of the call |
| `provider`, `model` | when relevant | who did the work |
| `units`, `unit` | when relevant | how much work, e.g. `1234`, `"characters"` |
| `estimated_cost_usd` | when known | an estimate, not a bill |
| `error` | on failure | the error `code` |

Anything else is the skill's own field (text-to-speech adds `duration_ms` for the audio's length, `voice` and `out`). Keep the core names exact: a person sums this file for cost per caller, and a tracing tool may read it later.

A failed log write prints a JSON warning on stderr and never changes the exit status. The work matters more than the log line.

There is no shared helper library. Each block carries the log write inline; the copy-paste version is recipe 6 in `migration-recipes.md`. `check_block.py` asserts the core fields on every verify run, which is what keeps the copies from drifting.

## The declared contract (README.md)

A skill is a block when its `README.md` has a `## Block contract` section. There is no frontmatter flag. The contract and the consumers live in the README, not `SKILL.md`: `SKILL.md` is loaded into an agent's context on every use, and the consumers list changes whenever a caller is added.

The form is fixed so `deploy.py` can read it:

```markdown
# <name>

One paragraph: what the block does, in plain words. This is the catalogue's one-line purpose.

## Block contract

- **Inputs:** what it takes, including the required `--caller`.
- **Outputs:** what it produces and the main JSON fields.
- **Entry point:** `scripts/run.py`

## Consumers

- **<consumer-name>** (`<path or repo>`): what it uses the block for.
```

- `## Consumers` is the only list of callers. Keep it current: a project that calls the block names it under "Depends on" in its own `AGENTS.md`, and the block lists the project here.
- The catalogue takes the bold name of each consumer item. An empty section (no items yet) is allowed and shows as "none yet".
- `--help` stays the full contract. The README section is the short form for the catalogue and for a person deciding whether this block fits.

## The catalogue (deploy.py)

A library README with `<!-- blocks:start -->` / `<!-- blocks:end -->` gets a generated Blocks section: one entry per block, name in backticks first so a search for the name finds it, with purpose, inputs, outputs, entry point and consumers. Blocks then leave the plain `<!-- skills:start -->` list, so each skill appears once. Put the Blocks section above the skills list: before building something that fetches, transcribes, speaks, sends or publishes, an agent searches this README first.

Everything between markers is generated and overwritten on deploy. Hand-kept sections (Services, Retired) go outside the markers.

## Building a block from scripts that are not a skill

Often the capability already exists as scripts in some repo (one script per host, a tool a job shells out to), and callers point at that repo by path. By the second-consumer rule it should be a block, even though there is no skill to migrate. The phases still apply, with five differences:

1. **Phase 0: the source is the repo of scripts.** There is no `SKILL.md` to copy; you write one (the judgment steps only) and the README contract. Search the library, the runtimes' skill dirs and any account-synced skills for one that already does the job under a similar name. A near-duplicate that triggers on the same words ("download this video") must be retired once the block is deployed, or agents pick the wrong one. Say so at the gate.
2. **Port, don't vendor.** Vendoring copies scripts as they are; a block needs one entry point with the fixed contract. Fold the scripts into `run.py` plus helper modules, for example one resolver function per host behind a router that picks by URL. Keep their behaviour; drop what the contract replaces: their own argument parsing, progress printed to stdout, `.env` loading, `sys.exit` inside library code.
3. **Keep consumer data with the consumer.** If a script carries one caller's specifics (a channel table, an account id, a folder layout), take the generic parameter instead (a channel id, not a body name) and leave the table where that caller already keeps it. The block holds no per-consumer data.
4. **Make a small real run possible.** If the job is long or large (a two-hour download, a thousand pages), give `run.py` a cap (`--max-seconds`, `--limit`) so verification, `check_block.py`'s sample and the fresh-agent test can be live without the full cost. Know where the cap does not apply and say so in the Limits.
5. **Phase 6 retires a repo, not a skill folder.**
   - Repoint every caller. Each is an edit in another repo, so list them and get a yes. Run the caller's tests, then one real call under the caller's own environment (a launchd job's short `PATH`, for example).
   - Let it run before retiring the old code: for a nightly job, at least one good night, confirmed from its log and from the call log's lines with that caller.
   - Then archive: a note at the top of the old README, the remote archived, the local copy moved to wherever the user keeps retired repos. Grep for the old path afterwards; only dated notes and the block's own provenance line should mention it.

## Verifying a block

Phase 3 runs the bundled check:

```bash
uv run "$LIBRARIAN/scripts/check_block.py" <skill_library_path>/<name> --sample '--text "One short line." --out {tmp}/check.mp3'
```

`$LIBRARIAN` is skill-librarian's own folder. `{tmp}` in the sample becomes a temporary folder that is deleted afterwards. It checks:

1. The README contract parses: inputs, outputs, an entry point that exists, and a consumers section.
2. `--help` exits 0 and prints something.
3. A call without `--caller` exits 2, prints nothing on stdout, and puts a JSON error on stderr.
4. With `--sample`: the sample call (with `--caller skill-verify` added) exits 0, stdout is one JSON object, and the log gains exactly one line with all the core fields and caller `skill-verify`.

Every call runs with a stripped environment (only `PATH`, `HOME`, `TMPDIR`, `USER`, `LANG`, plus `SKILLS_CALL_LOG` pointing at a temporary file). That is the proof that the script runs with no agent present, and no test line reaches the real log.

Choose the sample from the `--help` examples. It must have no outside effect: a paid call of a few cents is fine; anything that sends or publishes runs with `--dry-run`. Without `--sample` the check reports the sample as not run, and the report to the user says so.

## Worked examples

The first block built to these rules is a text-to-speech skill: text in, one MP3 and its exact duration out. Its shape:

- `scripts/run.py`, standard library only, with `--help` listing every setting and its `TTS_*` variable, the JSON output, and the exit codes.
- Keys read from `~/.config/openai/credentials.env` and `~/.config/deepgram/credentials.env`; the paths are config keys.
- URLs handled in `SKILL.md`, which calls the web-to-markdown skill first and passes the file.
- `SKILL.md` has a table of which steps are scripted and which are the agent's judgment (voice, delivery direction, splitting a script into segments).
- The README carries the `## Block contract` and `## Consumers` sections, and `deploy.py` lists it in the Blocks catalogue.

The second, a video-download skill, was built from scripts that were not a skill (the section above): a repo of six downloaders, one per host, that a nightly meetings job called by path. Its shape:

- `scripts/run.py` (settings, naming, ffmpeg or yt-dlp, JSON, call log) and `scripts/resolvers.py` (one function per host, returning what to fetch; nothing in it downloads). `route()` picks by host: Fathom, BoxCast, private Vimeo, Skool, StreamYard, any `.m3u8`, and yt-dlp for everything else.
- The old BoxCast script looked meetings up by a city body's name; the block takes a channel id and a date, and the channel table stayed in the meetings job.
- Downloads go to a hidden part file beside the output and move into place, so a caller never sees half a file. `--dry-run` resolves without downloading; `--max-seconds` makes a ten-second live test of a two-hour meeting.
- Phase 6 repointed the meetings job, waited for nine clean nights, archived the old repo, and retired an older generic downloader skill that triggered on the same words.
