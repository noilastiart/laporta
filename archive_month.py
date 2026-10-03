#!/usr/bin/env python3
"""
Compass monthly snapshot archiver.

Saves a complete, self-contained copy of Compass as it stood for one month
(days, events, per-event flags, general flags, amenities, diagrams, source
ledger, and the Month View set to that month) into archive/Compass_YYYY-MM.html,
then trims the live Compass_CURRENT.html + index.html down to what's still
current. The snapshot is a full copy of the app, so it keeps working and
looking the way it did that month no matter how the live app changes later.

Usage:
    python3 archive_month.py --month 2026-09 [--dry-run]

Everything dated before the first day of the month AFTER --month is archived.
General ("globalFlags") flags are sorted automatically:
  - stays in the snapshot : flag has no ISO date stamp, or is dated before the cutoff
  - stays in live         : flag is dated on/after the cutoff, OR mentions a BEO
                            number that still has a live event
  A flag can land in both (it describes the month AND still matters).
Personal Notes live in the browser (localStorage), keyed by date/room/event, so
they are not touched by this tool.
"""
import argparse, copy, json, os, re, shutil, sys
from datetime import datetime

COMPASS_DIR = os.path.dirname(os.path.abspath(__file__))
CURRENT_HTML = os.path.join(COMPASS_DIR, "Compass_CURRENT.html")
INDEX_HTML = os.path.join(COMPASS_DIR, "index.html")
ARCHIVE_DIR = os.path.join(COMPASS_DIR, "archive")
PASTBUILDS_DIR = os.path.join(COMPASS_DIR, "_PASTBUILDS")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def split_html(text):
    start = text.index("const DATA = ") + len("const DATA = ")
    depth, end = 0, None
    for i in range(start, len(text)):
        c = text[i]
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return json.loads(text[start:end]), text[:start], text[end:]


def dump(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def events(days):
    for d in days:
        for r in d["rooms"]:
            for e in r["events"]:
                yield d, r, e


def groups_present(days):
    return {e["group"] for _, _, e in events(days)}


def diagram_keys(days):
    return {e["diagramKey"] for _, _, e in events(days) if e.get("diagramKey")}


def set_month_consts(text, year, month):
    text = re.sub(r"const MONTH_VIEW_YEAR = \d+;", f"const MONTH_VIEW_YEAR = {year};", text)
    return re.sub(r"const MONTH_VIEW_MONTH = \d+;", f"const MONTH_VIEW_MONTH = {month};", text)


def classify_flags(flags, cutoff, live_beos):
    keep_snap, keep_live = [], []
    for i, f in enumerate(flags):
        m = re.match(r"\[([^\]]*)\]", f)
        tag = m.group(1) if m else ""
        iso = re.findall(r"\d{4}-\d{2}-\d{2}", tag)
        stamp = iso[0] if iso else None
        beos = set(re.findall(r"\b1[01]\d{4}\b", f))
        live_hit = sorted(beos & live_beos)
        to_snap = (stamp is None) or (stamp < cutoff)
        to_live = (stamp is not None and stamp >= cutoff) or bool(live_hit)
        if not to_snap and not to_live:
            to_snap = True
        if to_snap: keep_snap.append(f)
        if to_live: keep_live.append(f)
    return keep_snap, keep_live


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="Month to snapshot, YYYY-MM")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    y, mo = map(int, args.month.split("-"))
    ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    cutoff = f"{ny:04d}-{nmo:02d}-01"
    month_label = f"{MONTHS[mo - 1]} {y}"

    text = open(CURRENT_HTML, encoding="utf-8").read()
    data, head, tail = split_html(text)

    snap_days = [d for d in data["days"] if d["date"] < cutoff]
    keep_days = [d for d in data["days"] if d["date"] >= cutoff]
    if not snap_days:
        sys.exit(f"Nothing dated before {cutoff} in the live file -- nothing to archive.")
    snap_path = os.path.join(ARCHIVE_DIR, f"Compass_{args.month}.html")
    if os.path.exists(snap_path):
        sys.exit(f"Refusing to overwrite existing snapshot: {snap_path}")

    live_beos = {e["beoNumber"] for _, _, e in events(keep_days)}
    flags_snap, flags_live = classify_flags(data["globalFlags"], cutoff, live_beos)

    # ---------- snapshot data ----------
    snap = copy.deepcopy(data)
    snap["days"] = snap_days
    snap["amenities"] = [a for a in data.get("amenities", []) if a["date"] < cutoff]
    snap["crossGroupConflicts"] = [c for c in data.get("crossGroupConflicts", []) if c["date"] < cutoff]
    sg = groups_present(snap_days)
    snap["groups"] = [g for g in data["groups"] if g["name"] in sg]
    sk = diagram_keys(snap_days)
    snap["diagrams"] = {k: v for k, v in data["diagrams"].items() if k in sk}
    snap["globalFlags"] = flags_snap
    # sourceLedger deliberately copied in full: it is history, not rendered.

    # ---------- live data ----------
    live = copy.deepcopy(data)
    live["days"] = keep_days
    live["amenities"] = [a for a in data.get("amenities", []) if a["date"] >= cutoff]
    live["crossGroupConflicts"] = [c for c in data.get("crossGroupConflicts", []) if c["date"] >= cutoff]
    lg = groups_present(keep_days)
    live["groups"] = [g for g in data["groups"] if g["name"] in lg]
    lk = diagram_keys(keep_days)
    live["diagrams"] = {k: v for k, v in data["diagrams"].items() if k in lk}
    live["globalFlags"] = flags_live

    ev_flags_snap = sum(len(e.get("flags") or []) for _, _, e in events(snap_days))
    ev_flags_live = sum(len(e.get("flags") or []) for _, _, e in events(keep_days))

    print(f"=== Archive {month_label}  (cutoff: everything before {cutoff}) ===")
    print(f"Snapshot  : {len(snap_days)} days ({snap_days[0]['date']} .. {snap_days[-1]['date']}), "
          f"{sum(1 for _ in events(snap_days))} events, {len(snap['groups'])} groups, "
          f"{len(snap['amenities'])} amenity deliveries, {len(snap['diagrams'])} diagrams")
    print(f"            per-event flags {ev_flags_snap}, general flags {len(flags_snap)}, "
          f"source ledger {len(snap['sourceLedger'])} entries (full copy)")
    print(f"Live after: {len(keep_days)} days ({keep_days[0]['date']} .. {keep_days[-1]['date']}), "
          f"{sum(1 for _ in events(keep_days))} events, {len(live['groups'])} groups, "
          f"{len(live['amenities'])} amenity deliveries, {len(live['diagrams'])} diagrams")
    print(f"            per-event flags {ev_flags_live}, general flags {len(flags_live)} (was {len(data['globalFlags'])})")
    dropped = sorted({g['name'] for g in data['groups']} - lg)
    print(f"Groups leaving the live legend: {', '.join(dropped) if dropped else '(none)'}")
    print(f"Month View: snapshot -> {month_label}; live -> {MONTHS[nmo - 1]} {ny}")

    print("\n--- General flags STAYING in live (also copied to snapshot if dated before cutoff) ---")
    for f in flags_live:
        print("  •", f[:105].replace("\n", " "))
    print(f"\n--- General flags moving to the snapshot ONLY: {len(data['globalFlags']) - len(flags_live)} "
          f"(of {len(data['globalFlags'])}) ---")
    live_set = set(flags_live)
    only = [f for f in flags_snap if f not in live_set]
    tags = {}
    for f in only:
        m = re.match(r"\[([^\]]*)\]", f)
        t = m.group(1) if m else "(no tag)"
        tags[t] = tags.get(t, 0) + 1
    for t, n in tags.items():
        print(f"  {n:2d} × [{t}]")

    if args.dry_run:
        print("\nDry run -- nothing written.")
        return

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(PASTBUILDS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    shutil.copy(CURRENT_HTML, os.path.join(PASTBUILDS_DIR, f"Compass_ARCHIVED_{ts}.html"))

    # ----- snapshot page: lives one folder down, so asset paths get ../ -----
    snap_head = head.replace('<script src="password-gate.js"></script>', '<script src="../password-gate.js"></script>')
    snap_head = snap_head.replace("diagrams/${key}.png", "../diagrams/${key}.png")
    snap_head = snap_head.replace("<title>Ncompass — Floor Timeline</title>",
                                  f"<title>Ncompass — {month_label} (archive snapshot)</title>")
    banner = (f'<div class="no-print-hide" style="background:#3a4a5c; color:#e4ebf0; padding:8px 14px; '
              f'border-radius:6px; font-size:12px; margin-bottom:12px;">ARCHIVE SNAPSHOT &middot; {month_label} &middot; '
              f'Compass as it stood when this month was closed ({datetime.now().strftime("%Y-%m-%d")}). Not updated.</div>')
    snap_head = snap_head.replace('<h1 class="brand">Ncompass</h1>', banner + '\n<h1 class="brand">Ncompass</h1>', 1)
    snap_tail = set_month_consts(tail, y, mo)
    open(snap_path, "w", encoding="utf-8").write(snap_head + dump(snap) + snap_tail)

    # ----- live page -----
    live_tail = set_month_consts(tail, ny, nmo)
    new_live = head + dump(live) + live_tail
    open(CURRENT_HTML, "w", encoding="utf-8").write(new_live)
    open(INDEX_HTML, "w", encoding="utf-8").write(new_live)
    print(f"\nWrote {snap_path}")
    print(f"Live Compass_CURRENT.html + index.html now cover {keep_days[0]['date']} .. {keep_days[-1]['date']}")


if __name__ == "__main__":
    main()
