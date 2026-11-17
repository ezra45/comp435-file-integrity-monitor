"""
Usage:
  python3 monitor.py baseline <dir> <baseline.json>
  python3 monitor.py scan <dir> <baseline.json> [--mode full|fast]
  python3 monitor.py selftest

Keep baseline.json outside the monitored directory
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(root):
    """Yield (relative_path, absolute_path) for every regular file."""
    for dirpath, _, names in os.walk(root):
        for name in names:
            full = os.path.join(dirpath, name)
            if os.path.isfile(full) and not os.path.islink(full):
                yield os.path.relpath(full, root), full


def take_baseline(root):
    snap = {}
    for rel, full in walk(root):
        st = os.stat(full)
        snap[rel] = {
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "ctime_ns": st.st_ctime_ns,
            "sha256": sha256(full),
        }
    return snap


def scan(root, baseline, mode="full"):
    """Compare directory to baseline. Returns dict of added/deleted/renamed/modified."""
    current = {} 
    for rel, full in walk(root):
        old = baseline.get(rel)
        if mode == "fast" and old is not None:
            st = os.stat(full)
            if st.st_size == old["size"] and st.st_mtime_ns == old["mtime_ns"]:
                current[rel] = old["sha256"]
                continue
        current[rel] = sha256(full)

    modified = sorted(p for p in current if p in baseline and current[p] != baseline[p]["sha256"])
    added = {p for p in current if p not in baseline}
    deleted = {p for p in baseline if p not in current}

    # A rename = a deleted path and an added path with identical content hash.
    renamed = []
    for d in sorted(deleted):
        for a in sorted(added):
            if current[a] == baseline[d]["sha256"]:
                renamed.append((d, a))
                added.discard(a)
                deleted.discard(d)
                break

    return {
        "modified": modified,
        "added": sorted(added),
        "deleted": sorted(deleted),
        "renamed": renamed,
    }


def has_changes(report):
    return any(report.values())


# ---------------------------------------------------------------- self-test

def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def _t_edit(d):
    _write(os.path.join(d, "a.txt"), b"hello world, this is changed and longer")


def _t_edit_same_size(d):
    _write(os.path.join(d, "a.txt"), b"HELLO WORLD") 


def _t_edit_same_size_restore_mtime(d):
    p = os.path.join(d, "a.txt")
    st = os.stat(p)
    _write(p, b"HELLO WORLD")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))


def _t_rename(d):
    os.rename(os.path.join(d, "a.txt"), os.path.join(d, "renamed.txt"))


def _t_delete(d):
    os.remove(os.path.join(d, "a.txt"))


def _t_add(d):
    _write(os.path.join(d, "new.txt"), b"new file")


def _t_touch(d):
    os.utime(os.path.join(d, "a.txt"))


TESTS = [
    ("edit contents", _t_edit, True),
    ("edit, same size", _t_edit_same_size, True),
    ("edit, same size, restore mtime", _t_edit_same_size_restore_mtime, True),
    ("rename", _t_rename, True),
    ("delete", _t_delete, True),
    ("add", _t_add, True),
    ("touch", _t_touch, False),
]


def selftest():
    print(f"{'test':34} {'full':>8} {'fast':>8}")
    print("-" * 52)
    for name, action, is_attack in TESTS:
        row = []
        for mode in ("full", "fast"):
            workdir = tempfile.mkdtemp()
            target = os.path.join(workdir, "target")
            os.mkdir(target)
            _write(os.path.join(target, "a.txt"), b"hello world")
            _write(os.path.join(target, "b.txt"), b"untouched")
            base = take_baseline(target)
            action(target)
            changed = has_changes(scan(target, base, mode))
            if is_attack:
                row.append("detected" if changed else "MISSED")
            else:
                row.append("FALSE ALARM" if changed else "ok")
            shutil.rmtree(workdir)
        print(f"{name:34} {row[0]:>8} {row[1]:>8}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline")
    b.add_argument("dir")
    b.add_argument("baseline")

    s = sub.add_parser("scan")
    s.add_argument("dir")
    s.add_argument("baseline")
    s.add_argument("--mode", choices=["full", "fast"], default="full")

    sub.add_parser("selftest")
    args = ap.parse_args()

    if args.cmd == "baseline":
        snap = take_baseline(args.dir)
        with open(args.baseline, "w") as f:
            json.dump(snap, f, indent=2)
        print(f"Baseline of {len(snap)} files written to {args.baseline}")
    elif args.cmd == "scan":
        with open(args.baseline) as f:
            base = json.load(f)
        report = scan(args.dir, base, args.mode)
        for kind in ("modified", "added", "deleted", "renamed"):
            for item in report[kind]:
                print(f"{kind.upper():9} {item}")
        if not has_changes(report):
            print("No changes detected.")
        sys.exit(1 if has_changes(report) else 0)
    elif args.cmd == "selftest":
        selftest()


if __name__ == "__main__":
    main()