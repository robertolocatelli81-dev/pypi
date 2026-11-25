#!/usr/bin/env python3
"""Positive control of the tests themselves: each mutant below is update_index.py with one rule switched off; the unit
tests must then fail. A mutant that survives is a rule no test protects. Offline, standard library; works on temporary
copies, writes nothing here.

    python3 .github/scripts/controllo_positivo.py   # `mutanti=N uccisi=N sopravvissuti=0`, exit 0; exit 1 if one survives

The unmutated copy is run first: if the tests do not pass on it, «killed» would mean nothing, and the run stops (exit 2)."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.dirname(os.path.dirname(HERE))
T = "update_index.py"
MUTANTS = [
 (T, 'if got != listed[f] or stated != ["sha256", got]:', 'if stated != ["sha256", got]:'),
 (T, 'if got != listed[f] or stated != ["sha256", got]:', 'if got != listed[f]:'),
 (T, 'if "SHA256SUMS" not in assets:', 'if False:'),
 (T, 'if f not in listed:', 'if False:'),
 (T, 'if not wheels or len(files) == len(wheels):', 'if False:'),
 (T, 'not r.get("draft") and not r.get("prerelease")', 'not r.get("draft")'),
 (T, 'if not r.get("draft") and', 'if'),
 (T, '> max(known, default=(-1,))', '>= max(known, default=(-1,))'),
 (T, 'new = new[-1:]', 'pass'),
 (T, 'unknown = sorted(set(dirs) - set(FOLLOWED) - set(FROZEN))', 'unknown = []'),
 (T, 'if unknown or set(FOLLOWED) & set(FROZEN):', 'if unknown:'),
 (T, 'if sorted(root_names) != dirs', 'if False'),
 (T, 'if not m or m.group(2) in out:', 'if not m:'),
 (T, 'if len(rp) > 1:', 'if False:'),
 (T, 'rp == {True})]', 'True)]'),
 (T, 'if not rp:\n                raise', 'if False:\n                raise'),
 (T, 'if not (text.startswith(head) and text.endswith(PAGE_TAIL)):', 'if not text.endswith(PAGE_TAIL):'),
 (T, 'if not m or m.group("repo") not in (FOLLOWED.get(name), name):', 'if not m:'),
 (T, '        if write:\n            os.makedirs', '        if True:\n            os.makedirs'),
 (T, 'bad = [r["tag_name"] for r in rels if not TAG.match(r["tag_name"])]', 'bad = []'),
 (T, r'TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)\Z")', r'TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")'),
 (T, 'rels.sort(key=lambda r: tuple(map(int, TAG.match(r["tag_name"]).groups())))', 'rels.sort(key=lambda r: r["tag_name"])'),
 (T, '        return 1\n    for path', '        return 0\n    for path'),
 (T, "' data-requires-python=\"%s\"' % html.escape(rp, quote=True)", "' data-requires-python=\"%s\"' % rp"),
 (T, 'wheels = sorted(n for n in assets if n.startswith(stem + "-") and n.endswith(".whl"))', 'wheels = []'),
 (T, 'new_root = new_root[:-len(PAGE_TAIL)]', 'new_root = new_root[:0]'),
 (T, 'if body and not body.endswith("\\n"):', 'if False:'),
 (T, 'if not wheels or len(files) == len(wheels):', 'if not files:'),
 (T, '(assets[f].get("digest") or "absent")', '(assets[f].get("digest") or "sha256:" + got)'),
 (T, 'if not re.fullmatch(r"[A-Za-z0-9._+-]+", f) or not LINK.match(line):', 'if False:'),
 (T, 'if not (text.startswith(head) and text.endswith(PAGE_TAIL)):', 'if not text.startswith(head):'),
 (T, 'n.startswith(stem + "-") and n.endswith(".whl")', 'n.endswith(".whl")'),
 (T, '(?P<sha>[0-9a-f]{64})"', '(?P<sha>[0-9a-f]+)"'),
 (T, '.decode("ascii"))', '.decode("latin-1"))'),
 (T, 'if len(meta) != 1:', 'if not meta:'),
 (T, 'if len(rest) > 1 or any(a.startswith("-") for a in rest):', 'if len(rest) > 1:'),
 (T, 'raise Refused("%s: release tags', 'print("%s: release tags'),
]


def run(target=None, old=None, new=None):
    d = tempfile.mkdtemp()
    try:
        shutil.copytree(INDEX, os.path.join(d, "idx"), ignore=shutil.ignore_patterns(".git", "__pycache__"))
        scripts = os.path.join(d, "idx", ".github", "scripts")
        if target:
            p = os.path.join(scripts, target)
            with open(p, encoding="utf-8") as fh:
                s = fh.read()
            if old not in s:
                return None                      # the mutant no longer applies: counted as a failure of this script
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(s.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-B", os.path.join(scripts, "test_update_index.py")], capture_output=True, text=True)
        return r.returncode != 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    if run():
        print("the tests fail on the unmutated script: nothing measured")
        return 2
    bad = []
    for target, old, new in MUTANTS:
        k = run(target, old, new)
        if not k:
            bad.append((target, new[:70], "not applicable" if k is None else "survived"))
    for b in bad:
        print("NOT KILLED:", b)
    print("mutanti=%d uccisi=%d sopravvissuti=%d" % (len(MUTANTS), len(MUTANTS) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
