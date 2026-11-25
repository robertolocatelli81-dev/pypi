#!/usr/bin/env python3
"""PEP 503 index (robertolocatelli81-dev/pypi): adds to each package page the wheel and sdist of every release newer
than the newest one the page lists, as links to the GitHub release assets with the `#sha256=` fragment pip checks.
Run hourly by .github/workflows/index.yml (2026-11-25; until then the pages were written by hand). Standard library,
plus `gh` logged in (or GH_TOKEN set) for the GitHub API.

    python3 .github/scripts/update_index.py [--write] [INDEX_DIR]

For each new file the hash is taken from the release's SHA256SUMS and checked twice before the link is written: against
the bytes of the asset, downloaded, and against the digest GitHub states for the asset. Any difference, a release
without SHA256SUMS, without a wheel or without the sdist of the package (every version listed so far has both; a
release caught while its files are still being uploaded would otherwise stay half listed), with a file missing from
SHA256SUMS or for which GitHub states no digest, and any page that cannot be read, is exit 1 with the reason, and
nothing is written. A page is rewritten as its own lines plus the new ones: a page whose links do not each end with a
newline is refused, not read short. Closed lists: every directory of the index, and
every link of the root page, is a package in FOLLOWED or in FROZEN; anything else is exit 1 with its name (a new
package enters only by a line in FOLLOWED). Without --write nothing is written: the run says what would change.
With --write each changed file is printed as `changed: PATH<TAB>COMMIT MESSAGE`."""
import email.parser
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

OWNER = "robertolocatelli81-dev"
# directory of the index (the normalized project name) -> repository whose releases it follows
FOLLOWED = {
    "aee-verifier": "aee-verifier",
    "ap2-evidence-pack": "ap2-evidence-pack",
    "cryptovalid-opencore": "cryptovalid-opencore",
    "omega-cra-evol": "omega-cra-evol",
    "omega-evidence": "omega-evidence",
    "pq-scitt": "pq-scitt",
    "rigorous-levels": "rigorous-levels",
}
# directories of the index that no release updates any more: name -> reason
FROZEN = {
    "cra-evidence": "renamed omega-cra-evol in 0.10.0: its new releases are listed under omega-cra-evol",
}
TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)\Z")   # ASCII digits only
LINK = re.compile(r'<a href="https://github\.com/' + OWNER + r'/(?P<repo>[A-Za-z0-9_.-]+)/releases/download/'
                  r'v(?P<ver>[0-9]+\.[0-9]+\.[0-9]+)/(?P<file>[^"#/]+)#sha256=(?P<sha>[0-9a-f]{64})"'
                  r'(?: data-requires-python="(?P<rp>[^"<>]+)")?>(?P=file)</a><br>\Z')
PAGE_HEAD = '<!DOCTYPE html>\n<html><head><title>{0}</title></head>\n<body>\n<h1>{0}</h1>\n'
PAGE_TAIL = '</body></html>\n'
ROOT_LINK = re.compile(r'<a href="(?P<name>[a-z0-9-]+)/">(?P=name)</a><br>\Z')


class Refused(Exception):
    pass


def api(path):
    out = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise Refused("GitHub API %s: %s" % (path, out.stderr.strip()))
    pages = json.loads(out.stdout)
    return [x for p in pages for x in p]


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def read_page(path, name):
    """The links of a package page, in order; the page must be exactly head + links + tail."""
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    head = PAGE_HEAD.format(name)
    if not (text.startswith(head) and text.endswith(PAGE_TAIL)):
        raise Refused("%s: not in the form this script writes (head or tail differ)" % path)
    body = text[len(head):len(text) - len(PAGE_TAIL)]
    if body and not body.endswith("\n"):         # split("\n")[:-1] would drop the last line in silence
        raise Refused("%s: the last line before the tail does not end with a newline" % path)
    links = body.split("\n")[:-1]
    for line in links:
        m = LINK.match(line)
        if not m or m.group("repo") not in (FOLLOWED.get(name), name):
            raise Refused("%s: line not read: %r" % (path, line[:100]))
    return links


def requires_python(name, data):
    """Requires-Python of a wheel (*.dist-info/METADATA) or an sdist (<stem>/PKG-INFO); None if it declares none."""
    if name.endswith(".whl"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            meta = [n for n in z.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)]
            if len(meta) != 1:
                raise Refused("%s: %d METADATA files" % (name, len(meta)))
            text = z.read(meta[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            text = t.extractfile(name[:-len(".tar.gz")] + "/PKG-INFO").read()
    return email.parser.BytesParser().parsebytes(text).get("Requires-Python")


def sums(text):
    """SHA256SUMS -> {name: sha256}; a line that is not `<64 hex> <space><space or *><name>`, or a name twice, refuses."""
    out = {}
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *]([^/\s]+)", line)
        if not m or m.group(2) in out:
            raise Refused("SHA256SUMS: line not read or name repeated: %r" % line[:100])
        out[m.group(2)] = m.group(1)
    return out


def release_links(name, repo, rel, with_rp):
    ver = TAG.match(rel["tag_name"])
    ver = ".".join(ver.groups())
    stem = name.replace("-", "_") + "-" + ver
    assets = {a["name"]: a for a in rel.get("assets", [])}
    if "SHA256SUMS" not in assets:
        raise Refused("%s %s: the release has no SHA256SUMS" % (repo, rel["tag_name"]))
    listed = sums(fetch(assets["SHA256SUMS"]["browser_download_url"]).decode("ascii"))
    wheels = sorted(n for n in assets if n.startswith(stem + "-") and n.endswith(".whl"))
    files = wheels + [n for n in assets if n == stem + ".tar.gz"]
    if not wheels or len(files) == len(wheels):
        raise Refused("%s %s: needs a wheel %s-*.whl and the sdist %s.tar.gz, found %s" % (
            repo, rel["tag_name"], stem, stem, files))
    out = []
    for f in files:
        if f not in listed:
            raise Refused("%s %s: %s is not in SHA256SUMS" % (repo, rel["tag_name"], f))
        data = fetch(assets[f]["browser_download_url"])
        got = hashlib.sha256(data).hexdigest()
        stated = (assets[f].get("digest") or "absent").split(":", 1)
        if got != listed[f] or stated != ["sha256", got]:
            raise Refused("%s %s: %s: downloaded %s, SHA256SUMS %s, GitHub digest %s" % (
                repo, rel["tag_name"], f, got, listed[f], ":".join(stated)))
        url = "https://github.com/%s/%s/releases/download/%s/%s" % (OWNER, repo, rel["tag_name"], f)
        attr = ""
        if with_rp:                     # the page states Requires-Python on every link: so do the new ones
            rp = requires_python(f, data)
            if not rp:
                raise Refused("%s %s: %s declares no Requires-Python, and the page states it" % (repo, rel["tag_name"], f))
            attr = ' data-requires-python="%s"' % html.escape(rp, quote=True)
        line = '<a href="%s#sha256=%s"%s>%s</a><br>' % (url, got, attr, f)
        if not re.fullmatch(r"[A-Za-z0-9._+-]+", f) or not LINK.match(line):   # else the next run could not read it
            raise Refused("%s %s: %r would write a line this script does not read" % (repo, rel["tag_name"], f))
        out.append(line)
    return out


def plan(index):
    """[(path, new text, commit message)] for every file that changes; raises Refused."""
    dirs = sorted(d for d in os.listdir(index) if os.path.isdir(os.path.join(index, d)) and not d.startswith("."))
    unknown = sorted(set(dirs) - set(FOLLOWED) - set(FROZEN))
    if unknown or set(FOLLOWED) & set(FROZEN):
        raise Refused("directories in neither FOLLOWED nor FROZEN, or in both: %s" % (unknown or sorted(set(FOLLOWED) & set(FROZEN))))
    root_path = os.path.join(index, "index.html")
    with open(root_path, encoding="utf-8", newline="") as fh:
        root = fh.read()
    root_names = [m.group("name") for m in map(ROOT_LINK.match, root.split("\n")) if m]
    if sorted(root_names) != dirs or len(set(root_names)) != len(root_names):
        raise Refused("index.html links %s, directories are %s" % (sorted(root_names), dirs))
    changes, new_root = [], root
    for name, repo in sorted(FOLLOWED.items()):
        path = os.path.join(index, name, "index.html")
        links = read_page(path, name) if name in dirs else []
        known = [tuple(map(int, LINK.match(l).group("ver").split("."))) for l in links]
        rp = {LINK.match(l).group("rp") is not None for l in links}
        if len(rp) > 1:
            raise Refused("%s: data-requires-python on some links and not on others" % path)
        rels = [r for r in api("repos/%s/%s/releases?per_page=100" % (OWNER, repo))
                if not r.get("draft") and not r.get("prerelease")]
        bad = [r["tag_name"] for r in rels if not TAG.match(r["tag_name"])]
        if bad:
            raise Refused("%s: release tags that are not vX.Y.Z: %s" % (repo, bad))
        rels.sort(key=lambda r: tuple(map(int, TAG.match(r["tag_name"]).groups())))
        new = [r for r in rels if tuple(map(int, TAG.match(r["tag_name"]).groups())) > max(known, default=(-1,))]
        if not known:
            new = new[-1:]                      # a new package starts at its newest release, not at its whole history
        if not new:
            continue
        added = [l for r in new for l in release_links(name, repo, r, rp == {True})]
        text = PAGE_HEAD.format(name) + "".join(l + "\n" for l in links + added) + PAGE_TAIL
        msg = "%s %s (release assets, sha256 fragments)" % (name, ", ".join(r["tag_name"] for r in new))
        changes.append((path, text, msg))
        if name not in dirs:
            new_root = new_root[:-len(PAGE_TAIL)] + '<a href="%s/">%s</a><br>\n' % (name, name) + PAGE_TAIL
    if new_root != root:
        if not root.endswith(PAGE_TAIL):
            raise Refused("index.html: tail differs")
        changes.append((root_path, new_root, "index: " + ", ".join(sorted(set(FOLLOWED) - set(dirs)))))
    return changes


def main(argv):
    write = argv[:1] == ["--write"]
    rest = argv[1:] if write else argv
    if len(rest) > 1 or any(a.startswith("-") for a in rest):   # an argument that is not understood is an error
        print("usage: update_index.py [--write] [INDEX_DIR]", file=sys.stderr)
        return 2
    index = rest[0] if rest else "."
    try:
        changes = plan(index)
    except (Refused, OSError, UnicodeDecodeError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as e:
        print(json.dumps({"ok": False, "why": "%s: %s" % (type(e).__name__, e)}))
        return 1
    for path, text, msg in changes:
        if write:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        print(("changed: %s\t%s" if write else "would change: %s\t%s") % (os.path.relpath(path, index), msg))
    print("changed files:", len(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
