#!/usr/bin/env python3
"""
deploy.py — link skills into each agent runtime's skills dir.

A skill library is the single source of truth. This links each skill (a symlink
on macOS/Linux, a directory junction on Windows) from every runtime's skills
directory to the skill's folder, so an edit — or a `git pull` — shows up
everywhere with no copying and nothing to keep in sync.

It deploys skills from two places:
  1. This repo (the framework repo holding deploy.py + the skill-librarian skill).
  2. Any additional library roots you list in a git-ignored deploy.json — e.g. a
     private repo of your own skills, kept separate from this shareable framework.

    python3 deploy.py              # link all skills into all targets
    python3 deploy.py --dry-run    # show what it would do, change nothing
    python3 deploy.py --skill NAME # just one skill (used by skill-librarian)

Every run also refreshes each library's README skill list, if that README has
the markers below. The list is rebuilt from the skill folders, so it can't
drift; a README without the markers is left alone.

    <!-- skills:start -->
    <!-- skills:end -->

deploy.json (git-ignored; see deploy.example.json) — absolute paths only:

    {
      "libraries": ["/Users/you/source/skill-library"],
      "targets":   ["/Users/you/.claude/skills", "/Users/you/.codex/skills"]
    }

Targets default to ~/.claude/skills and ~/.codex/skills (whichever exist).
Requires Python 3. Standard library only.
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys

SELF_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TARGETS = ["~/.claude/skills", "~/.codex/skills"]


def config():
    cfg = os.path.join(SELF_DIR, "deploy.json")
    if os.path.isfile(cfg):
        try:
            return json.load(open(cfg))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def library_roots():
    """This repo, plus each library in deploy.json. Existing, de-duplicated."""
    roots = [SELF_DIR] + [os.path.expanduser(p) for p in config().get("libraries", [])]
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r in seen:
            continue
        seen.add(r)
        if os.path.isdir(r):
            out.append(r)
        else:
            print(f"  (configured library doesn't exist, skipping: {short(r)})")
    return out


def discover_skills():
    """Map skill-name -> source folder across all roots. First root wins on a name clash."""
    found = {}
    for root in library_roots():
        for name in sorted(os.listdir(root)):
            if name.startswith("."):
                continue
            d = os.path.join(root, name)
            if not (os.path.isdir(d) and os.path.isfile(os.path.join(d, "SKILL.md"))):
                continue
            if name in found:
                print(f"  (name clash: '{name}' in two libraries; keeping {short(found[name])})")
                continue
            found[name] = d
    return found


def targets():
    """Existing target skill dirs: deploy.json's list or the defaults, expanded."""
    seen, result = set(), []
    for t in config().get("targets", DEFAULT_TARGETS):
        p = os.path.expanduser(t)
        if p in seen:
            continue
        seen.add(p)
        if os.path.isdir(p):
            result.append(p)
        else:
            print(f"  (configured target doesn't exist, skipping: {short(p)})")
    return result


def link_target(path):
    """Resolved target if `path` is a symlink/junction, else None (real dir or missing)."""
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        return os.path.realpath(path)
    # Windows junctions aren't caught by islink(); detect the reparse point.
    if os.name == "nt" and os.path.isdir(path):
        try:
            if os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return os.path.realpath(path)
        except (AttributeError, OSError):
            pass
    return None


def remove_link(path):
    if os.name == "nt":
        try:
            os.rmdir(path)        # directory symlink / junction
        except OSError:
            os.unlink(path)
    else:
        os.unlink(path)


def make_link(src, link):
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(src, link)         # no admin needed
        except (ImportError, AttributeError, OSError):
            subprocess.run(["cmd", "/c", "mklink", "/J", link, src],
                           check=True, capture_output=True)
    else:
        os.symlink(src, link)


def short(path):
    return path.replace(os.path.expanduser("~"), "~")


CATALOG = re.compile(r"(<!-- skills:start -->\n).*?(<!-- skills:end -->)", re.S)


def skill_summary(d):
    """One line for the catalog: the README's first paragraph, else the
    first sentence of the SKILL.md frontmatter description."""
    readme = os.path.join(d, "README.md")
    if os.path.isfile(readme):
        for para in open(readme, encoding="utf-8").read().split("\n\n"):
            para = " ".join(para.split())
            if para and not para.startswith(("#", "```", "<!--")):
                return para
    text = open(os.path.join(d, "SKILL.md"), encoding="utf-8").read()
    fm = re.match(r"---\n(.*?)\n---", text, re.S)
    lines = fm.group(1).splitlines() if fm else []
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            val = line[len("description:"):].strip()
            if val in ("", ">", "|", ">-", "|-"):     # folded / block scalar
                rest = []
                for nxt in lines[i + 1:]:
                    if nxt and not nxt[0].isspace():
                        break
                    rest.append(nxt.strip())
                val = " ".join(rest)
            if len(val) > 1 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]            # YAML-quoted scalar
            return re.split(r"(?<=[.!?])\s", val, maxsplit=1)[0]
    return ""


def update_catalogs(dry_run=False):
    """Rebuild the marked skill list in each library's README.md."""
    for root in library_roots():
        readme = os.path.join(root, "README.md")
        if not os.path.isfile(readme):
            continue
        text = open(readme, encoding="utf-8").read()
        if not CATALOG.search(text):
            continue
        names = sorted(n for n in os.listdir(root)
                       if not n.startswith(".")
                       and os.path.isfile(os.path.join(root, n, "SKILL.md")))
        items = "".join(f"- `{n}` — {skill_summary(os.path.join(root, n))}\n" for n in names)
        new = CATALOG.sub(lambda m: m.group(1) + items + m.group(2), text)
        if new == text:
            continue
        if dry_run:
            print(f"  CATALOG   {short(readme)}  (would refresh {len(names)} skills)")
        else:
            open(readme, "w", encoding="utf-8").write(new)
            print(f"  catalog   {short(readme)}  ({len(names)} skills)")


def deploy(dry_run=False, only=None):
    skills = discover_skills()
    if only:
        if only not in skills:
            sys.exit(f"No skill named '{only}' in any configured library.")
        skills = {only: skills[only]}
    tgts = targets()
    if not tgts:
        sys.exit("No existing target dirs — nothing to deploy into.")

    print(f"Skills:    {', '.join(sorted(skills))}")
    print(f"Targets:   {', '.join(short(t) for t in tgts)}")
    print(f"Link type: {'junction (Windows)' if os.name == 'nt' else 'symlink'}"
          + ("   [DRY RUN — no changes]" if dry_run else ""))
    print()

    counts = {"linked": 0, "ok": 0, "repaired": 0, "replaced": 0, "skipped": 0}

    for skill, src in sorted(skills.items()):
        for tgt in tgts:
            link = os.path.join(tgt, skill)
            label = short(link)
            cur = link_target(link)

            if cur == os.path.realpath(src):
                counts["ok"] += 1
                continue

            if cur is not None:                       # a link, wrong/broken target
                if dry_run:
                    print(f"  REPAIR    {label}  (relink → {short(src)})")
                else:
                    remove_link(link)
                    make_link(src, link)
                    print(f"  repaired  {label}")
                counts["repaired"] += 1
                continue

            if os.path.lexists(link):                 # a REAL dir — never delete without consent
                if dry_run:
                    print(f"  REAL DIR  {label}  (would prompt to replace)")
                    counts["skipped"] += 1
                    continue
                ans = input(f"  {label} is a real directory (a drifted copy). "
                            f"Replace it with a link to {short(src)}? [y/N] ")
                if ans.strip().lower() == "y":
                    if os.path.isdir(link) and not os.path.islink(link):
                        shutil.rmtree(link)
                    else:
                        os.remove(link)
                    make_link(src, link)
                    print(f"  replaced  {label}")
                    counts["replaced"] += 1
                else:
                    print(f"  skipped   {label}")
                    counts["skipped"] += 1
                continue

            if dry_run:                               # nothing there → new link
                print(f"  LINK      {label}")
            else:
                make_link(src, link)
                print(f"  linked    {label}")
            counts["linked"] += 1

    update_catalogs(dry_run)
    print()
    print("Summary: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def main():
    ap = argparse.ArgumentParser(
        description="Link skills from this repo + configured libraries into runtime skill dirs.")
    ap.add_argument("--dry-run", action="store_true",
                    help="show actions, change nothing")
    ap.add_argument("--skill", metavar="NAME", help="deploy just one skill")
    args = ap.parse_args()
    deploy(dry_run=args.dry_run, only=args.skill)


if __name__ == "__main__":
    main()
