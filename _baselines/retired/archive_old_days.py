#!/usr/bin/env python3
"""
Compass archiving tool.

Moves days older than a cutoff date (and their per-event flags, which travel
with each event automatically) out of the live Compass_CURRENT.html into a
separate Compass_ARCHIVE.html, so the live file stays lean without deleting
anything. Safe to re-run: repeated runs accumulate into the same archive file
rather than overwrite it, and re-running with the same cutoff is a no-op.

Usage:
    python3 archive_old_days.py [--cutoff YYYY-MM-DD] [--dry-run]

Defaults to archiving everything strictly before today.
"""
import argparse
import base64
import json
import os
import re
import shutil
import sys
from datetime import date

COMPASS_DIR = os.path.dirname(os.path.abspath(__file__))
CURRENT_HTML = os.path.join(COMPASS_DIR, "Compass_CURRENT.html")
INDEX_HTML = os.path.join(COMPASS_DIR, "index.html")
ARCHIVE_DIR = os.path.join(COMPASS_DIR, "weekly archive ")
ARCHIVE_HTML = os.path.join(ARCHIVE_DIR, "Compass_ARCHIVE.html")
PASTBUILDS_DIR = os.path.join(COMPASS_DIR, "_PASTBUILDS")


def extract_data_and_shell(html_text):
    start = html_text.index("const DATA = ") + len("const DATA = ")
    depth, end = 0, None
    for i in range(start, len(html_text)):
        c = html_text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    data = json.loads(html_text[start:end])
    shell = html_text[:start] + "__DATA_JSON__" + html_text[end:]
    return data, shell


def rebuild_html(shell, data):
    return shell.replace("__DATA_JSON__", json.dumps(data, separators=(",", ":")))


def merge_days(base_days, new_days):
    by_date = {d["date"]: d for d in base_days}
    for day in new_days:
        by_date[day["date"]] = day  # archive run with the same cutoff overwrites cleanly, not duplicates
    return sorted(by_date.values(), key=lambda d: d["date"])


def groups_present(days):
    """Group names that actually have at least one event across these days."""
    names = set()
    for day in days:
        for room in day["rooms"]:
            for ev in room["events"]:
                names.add(ev["group"])
    return names


def prune_groups(all_groups, present_names):
    """Drop color-dot-legend entries for groups with no events left in this
    file, so archiving a group's last remaining day doesn't leave a stale
    dot/name behind with nothing to back it up."""
    return [g for g in all_groups if g["name"] in present_names]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default=date.today().isoformat(),
                     help="Archive every day strictly before this date (YYYY-MM-DD). Defaults to today.")
    ap.add_argument("--dry-run", action="store_true", help="Report what would move without writing anything.")
    args = ap.parse_args()

    if not os.path.exists(CURRENT_HTML):
        sys.exit(f"Not found: {CURRENT_HTML}")

    current_html = open(CURRENT_HTML, encoding="utf-8").read()
    data, shell = extract_data_and_shell(current_html)

    old_days = [d for d in data["days"] if d["date"] < args.cutoff]
    keep_days = [d for d in data["days"] if d["date"] >= args.cutoff]

    if not old_days:
        print(f"Nothing older than {args.cutoff} -- live file already lean. No changes made.")
        return

    old_conflicts = [c for c in data.get("crossGroupConflicts", []) if c["date"] < args.cutoff]
    keep_conflicts = [c for c in data.get("crossGroupConflicts", []) if c["date"] >= args.cutoff]

    print(f"Archiving {len(old_days)} day(s) older than {args.cutoff}: "
          f"{', '.join(d['date'] for d in old_days)}")
    print(f"Live file keeps {len(keep_days)} day(s): {', '.join(d['date'] for d in keep_days)}")

    n_flags = sum(len(ev.get("flags") or []) for d in old_days for r in d["rooms"] for ev in r["events"])
    print(f"({n_flags} per-event flag(s) travel with their day automatically -- no separate step needed.)")

    if args.dry_run:
        print("Dry run -- nothing written.")
        return

    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    # --- Load or start the archive dataset ---
    if os.path.exists(ARCHIVE_HTML):
        archive_html = open(ARCHIVE_HTML, encoding="utf-8").read()
        archive_data, archive_shell = extract_data_and_shell(archive_html)
    else:
        archive_data = {"days": [], "groups": [], "globalFlags": [], "crossGroupConflicts": [], "diagrams": {}}
        # Same rendering shell as the live file, but the archive lives one
        # directory down (in ARCHIVE_DIR), so its password-gate and diagram
        # references need a ../ prefix to still resolve.
        archive_shell = shell.replace(
            '<script src="password-gate.js"></script>',
            '<script src="../password-gate.js"></script>'
        ).replace(
            "diagrams/${key}.png",
            "../diagrams/${key}.png"
        )

    archive_data["days"] = merge_days(archive_data["days"], old_days)
    archive_data["crossGroupConflicts"] = sorted(
        {c["date"] + c["kind"] + c["detail"]: c for c in archive_data["crossGroupConflicts"] + old_conflicts}.values(),
        key=lambda c: c["date"]
    )
    # groups/diagrams: keep the archive's copy current so colors/legend and any
    # diagram lookups for archived events keep working standalone. The legend
    # itself is pruned to groups that actually appear somewhere in the archive
    # (cumulative across every run), so it never lists a color dot with
    # nothing behind it.
    archive_data["groups"] = prune_groups(data["groups"], groups_present(archive_data["days"]))
    archive_data["diagrams"] = data["diagrams"]
    # globalFlags are process notes, not tied to a single day -- left in the
    # live file rather than split, per the archiving instructions.

    # --- Archive the pre-archive Compass_CURRENT.html first, per convention ---
    ts = __import__("datetime").datetime.now().strftime("%Y-%m-%d_%H%M")
    os.makedirs(PASTBUILDS_DIR, exist_ok=True)
    shutil.copy(CURRENT_HTML, os.path.join(PASTBUILDS_DIR, f"Compass_ARCHIVED_{ts}.html"))

    # --- Write the slimmed live file ---
    data["days"] = keep_days
    data["crossGroupConflicts"] = keep_conflicts
    # Drop legend entries (color dot + name) for any group whose every event
    # just got archived away -- otherwise it lingers in the live file's
    # group-legend row with nothing left on the timeline to back it up.
    dropped = {g["name"] for g in data["groups"]} - groups_present(keep_days)
    data["groups"] = prune_groups(data["groups"], groups_present(keep_days))
    if dropped:
        print(f"Dropped from the live legend (no events left in the live file): {', '.join(sorted(dropped))}")
    new_current_html = rebuild_html(shell, data)
    open(CURRENT_HTML, "w", encoding="utf-8").write(new_current_html)
    open(INDEX_HTML, "w", encoding="utf-8").write(new_current_html)

    # --- Write the archive file ---
    new_archive_html = rebuild_html(archive_shell, archive_data)
    open(ARCHIVE_HTML, "w", encoding="utf-8").write(new_archive_html)

    if keep_days:
        print(f"\nDone. Compass_CURRENT.html + index.html now cover {keep_days[0]['date']} - {keep_days[-1]['date']}.")
    else:
        print("\nDone. Compass_CURRENT.html + index.html now cover 0 days (everything archived) -- ready for a new batch.")
    print(f"Compass_ARCHIVE.html now covers {archive_data['days'][0]['date']} - {archive_data['days'][-1]['date']} "
          f"({len(archive_data['days'])} day(s) total).")


if __name__ == "__main__":
    main()
