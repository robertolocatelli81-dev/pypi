#!/usr/bin/env python3
"""Tests of update_index.py: offline, standard library only (`python3 .github/scripts/test_update_index.py -v`). The
GitHub API and the downloads are replaced by a fake release; each refusal has its positive control (the same input
with one thing wrong must be refused, and must leave every file as it was)."""
import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_index as ui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.dirname(os.path.dirname(HERE))
O = ui.OWNER


def wheel(stem, rp=">=3.10"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(stem.split("-")[0] + "-" + stem.split("-")[1] + ".dist-info/METADATA",
                   "Metadata-Version: 2.1\nName: x\n" + ("Requires-Python: %s\n" % rp if rp else ""))
    return b.getvalue()


def sdist(stem, rp=">=3.10"):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        data = ("Metadata-Version: 2.1\nName: x\n" + ("Requires-Python: %s\n" % rp if rp else "")).encode()
        info = tarfile.TarInfo(stem + "/PKG-INFO")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return b.getvalue()


def page(name, links):
    return ui.PAGE_HEAD.format(name) + "".join(l + "\n" for l in links) + ui.PAGE_TAIL


def link(repo, ver, f, data, rp=None):
    attr = ' data-requires-python="%s"' % rp if rp else ""
    return '<a href="https://github.com/%s/%s/releases/download/v%s/%s#sha256=%s"%s>%s</a><br>' % (
        O, repo, ver, f, hashlib.sha256(data).hexdigest(), attr, f)


class Fake:
    """Releases {repo: [release]}, files {url: bytes}."""

    def __init__(self):
        self.releases, self.files = {}, {}

    def add(self, repo, tag, files, sums=None, digest=None, **flags):
        assets = []
        for n, data in files.items():
            url = "https://github.com/%s/%s/releases/download/%s/%s" % (O, repo, tag, n)
            self.files[url] = data
            assets.append({"name": n, "browser_download_url": url,
                           "digest": (digest or {}).get(n, "sha256:" + hashlib.sha256(data).hexdigest())})
        if sums is not False:
            text = sums if isinstance(sums, str) else "".join(
                "%s  %s\n" % (hashlib.sha256(d).hexdigest(), n) for n, d in files.items())
            url = "https://github.com/%s/%s/releases/download/%s/SHA256SUMS" % (O, repo, tag)
            self.files[url] = text.encode()
            assets.append({"name": "SHA256SUMS", "browser_download_url": url, "digest": None})
        self.releases.setdefault(repo, []).append(dict({"tag_name": tag, "assets": assets}, **flags))

    def release(self, repo, ver, rp=">=3.10", **kw):
        stem = repo.replace("-", "_") + "-" + ver
        files = {stem + "-py3-none-any.whl": wheel(stem, rp), stem + ".tar.gz": sdist(stem, rp)}
        self.add(repo, "v" + ver, files, **kw)
        return files


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.fake = Fake()
        self.saved = ui.api, ui.fetch, ui.FOLLOWED, ui.FROZEN
        ui.api = lambda path: list(self.fake.releases.get(path.split("/")[2], []))
        ui.fetch = lambda url: self.fake.files[url]
        ui.FOLLOWED, ui.FROZEN = {"pkg-a": "pkg-a"}, {"old-pkg": "a reason"}
        self.old = self.fake.release("pkg-a", "0.1.0")
        self.write("index.html", page("Simple", []).replace("<title>Simple</title></head>\n<body>\n<h1>Simple</h1>\n",
                   "<title>Simple</title></head>\n<body>\n<h1>Simple index</h1>\n"
                   '<a href="pkg-a/">pkg-a</a><br>\n<a href="old-pkg/">old-pkg</a><br>\n'))
        self.write("pkg-a/index.html", page("pkg-a", [link("pkg-a", "0.1.0", n, d) for n, d in self.old.items()]))
        self.write("old-pkg/index.html", page("old-pkg", []))

    def tearDown(self):
        ui.api, ui.fetch, ui.FOLLOWED, ui.FROZEN = self.saved
        shutil.rmtree(self.d)

    def write(self, rel, text):
        os.makedirs(os.path.dirname(os.path.join(self.d, rel)), exist_ok=True)
        with open(os.path.join(self.d, rel), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def snapshot(self):
        out = {}
        for root, _, files in os.walk(self.d):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    out[os.path.relpath(os.path.join(root, f), self.d)] = fh.read()
        return out

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            return ui.main(list(args) + [self.d]), out.getvalue()

    def refused(self, why):
        before = self.snapshot()
        code, out = self.run_main("--write")
        self.assertEqual(code, 1, out)
        self.assertIn(why, out)
        self.assertEqual(self.snapshot(), before)          # nothing written


class Adds(Base):
    def test_a_new_release_adds_its_wheel_and_sdist_in_the_existing_format(self):
        new = self.fake.release("pkg-a", "0.2.0")
        code, out = self.run_main("--write")
        self.assertEqual(code, 0, out)
        self.assertIn("changed: pkg-a/index.html\tpkg-a v0.2.0 (release assets, sha256 fragments)", out)
        with open(os.path.join(self.d, "pkg-a/index.html"), encoding="utf-8") as fh:
            got = fh.read()
        names = sorted(self.old) + sorted(new)            # wheel before sdist, as on the hand-written pages
        want = page("pkg-a", [link("pkg-a", n.split("-")[1].replace(".tar.gz", ""), n, {**self.old, **new}[n]) for n in names])
        self.assertEqual(got, want)
        self.assertEqual(self.run_main("--write")[1].splitlines()[-1], "changed files: 0")   # a second run: nothing

    def test_without_write_nothing_is_written(self):
        self.fake.release("pkg-a", "0.2.0")
        before = self.snapshot()
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("would change: pkg-a/index.html", out)
        self.assertEqual(self.snapshot(), before)

    def test_every_release_newer_than_the_page_in_order_and_none_older(self):
        self.fake.release("pkg-a", "0.0.9")
        self.fake.release("pkg-a", "0.3.0")
        self.fake.release("pkg-a", "0.10.0")
        self.fake.release("pkg-a", "0.2.10")
        self.fake.release("pkg-a", "0.4.0", prerelease=True)
        self.fake.release("pkg-a", "0.5.0", draft=True)
        code, out = self.run_main("--write")
        self.assertEqual(code, 0, out)
        self.assertIn("pkg-a v0.2.10, v0.3.0, v0.10.0 (release", out)
        with open(os.path.join(self.d, "pkg-a/index.html"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual([ui.LINK.match(l).group("ver") for l in text.splitlines() if l.startswith("<a ")],
                         ["0.1.0", "0.1.0", "0.2.10", "0.2.10", "0.3.0", "0.3.0", "0.10.0", "0.10.0"])

    def test_requires_python_is_carried_when_the_page_states_it(self):
        self.write("pkg-a/index.html", page("pkg-a", [link("pkg-a", "0.1.0", n, d, "&gt;=3.10") for n, d in self.old.items()]))
        self.fake.release("pkg-a", "0.2.0", rp=">=3.11")
        self.assertEqual(self.run_main("--write")[0], 0)
        with open(os.path.join(self.d, "pkg-a/index.html"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count('data-requires-python="&gt;=3.11"'), 2)

    def test_a_new_followed_package_starts_at_its_newest_release_and_enters_the_root_page(self):
        ui.FOLLOWED["pkg-b"] = "pkg-b"
        self.fake.release("pkg-b", "1.0.0")
        newest = self.fake.release("pkg-b", "1.1.0")
        code, out = self.run_main("--write")
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.d, "pkg-b/index.html"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), page("pkg-b", [link("pkg-b", "1.1.0", n, newest[n]) for n in sorted(newest)]))
        with open(os.path.join(self.d, "index.html"), encoding="utf-8") as fh:
            self.assertTrue(fh.read().endswith('<a href="pkg-b/">pkg-b</a><br>\n</body></html>\n'))
        self.assertEqual(self.run_main("--write")[1].splitlines()[-1], "changed files: 0")


class Refuses(Base):
    def test_a_wrong_hash_in_sha256sums(self):
        stem = "pkg_a-0.2.0"
        w, s = wheel(stem), sdist(stem)
        self.fake.add("pkg-a", "v0.2.0", {stem + "-py3-none-any.whl": w, stem + ".tar.gz": s},
                      sums="%s  %s\n%s  %s\n" % ("0" * 64, stem + "-py3-none-any.whl", hashlib.sha256(s).hexdigest(), stem + ".tar.gz"))
        self.refused("downloaded %s, SHA256SUMS %s" % (hashlib.sha256(w).hexdigest(), "0" * 64))

    def test_bytes_that_differ_from_the_github_digest(self):
        self.fake.release("pkg-a", "0.2.0", digest={"pkg_a-0.2.0.tar.gz": "sha256:" + "1" * 64})
        self.refused("GitHub digest sha256:" + "1" * 64)

    def test_a_digest_that_is_not_sha256(self):
        files = self.fake.release("pkg-a", "0.2.0")
        h = hashlib.sha256(files["pkg_a-0.2.0.tar.gz"]).hexdigest()
        self.fake.releases["pkg-a"][-1]["assets"][1]["digest"] = "sha512:" + h
        self.refused("GitHub digest sha512:" + h)

    def test_a_file_that_sha256sums_does_not_list(self):
        stem = "pkg_a-0.2.0"
        w = wheel(stem)
        self.fake.add("pkg-a", "v0.2.0", {stem + "-py3-none-any.whl": w, stem + ".tar.gz": sdist(stem)},
                      sums="%s  %s\n" % (hashlib.sha256(w).hexdigest(), stem + "-py3-none-any.whl"))
        self.refused("pkg_a-0.2.0.tar.gz is not in SHA256SUMS")

    def test_sha256sums_lines_that_cannot_be_read(self):
        stem = "pkg_a-0.2.0"
        w = wheel(stem)
        h = hashlib.sha256(w).hexdigest()
        for bad in ("%s %s\n" % (h, stem + "-py3-none-any.whl"), "%s  %s\n%s  %s\n" % (h, "x", h, "x"),
                    "%s  %s\n" % (h.upper(), stem + "-py3-none-any.whl"), "%s  ../%s\n" % (h, "x")):
            self.tearDown()
            self.setUp()
            self.fake.add("pkg-a", "v0.2.0", {stem + "-py3-none-any.whl": w}, sums=bad)
            self.refused("SHA256SUMS: line not read or name repeated")

    def test_a_release_without_sha256sums(self):
        self.fake.release("pkg-a", "0.2.0", sums=False)
        self.refused("the release has no SHA256SUMS")

    def test_a_release_without_a_wheel_or_sdist_of_the_package(self):
        self.fake.add("pkg-a", "v0.2.0", {"pkg-a-0.2.0.zip": b"z", "other_pkg-0.2.0.tar.gz": b"t"})
        self.refused("no wheel or sdist named pkg_a-0.2.0-*.whl or pkg_a-0.2.0.tar.gz")

    def test_a_tag_that_is_not_vxyz(self):
        for tag in ("0.2.0", "v0.2", "v١.2.3", "v1.2.3 "):
            self.tearDown()
            self.setUp()
            self.fake.add("pkg-a", tag, {})
            self.refused("release tags that are not vX.Y.Z")

    def test_a_directory_in_neither_list(self):
        self.write("stray/index.html", page("stray", []))
        self.refused("directories in neither FOLLOWED nor FROZEN, or in both: ['stray']")

    def test_a_package_in_both_lists(self):
        ui.FROZEN["pkg-a"] = "x"
        self.refused("or in both: ['pkg-a']")

    def test_root_page_and_directories_differ(self):
        with open(os.path.join(self.d, "index.html"), encoding="utf-8") as fh:
            root = fh.read()
        self.write("index.html", root.replace('<a href="old-pkg/">old-pkg</a><br>\n', ""))
        self.refused("index.html links ['pkg-a'], directories are ['old-pkg', 'pkg-a']")

    def test_a_page_line_that_cannot_be_read(self):
        with open(os.path.join(self.d, "pkg-a/index.html"), encoding="utf-8") as fh:
            text = fh.read()
        self.fake.release("pkg-a", "0.2.0")
        for bad in (text.replace("#sha256=", "#md5="), text.replace("</a><br>\n</body>", "</a>\n</body>"),
                    text.replace("/pkg-a/releases", "/other/releases", 1), text.replace("<h1>pkg-a</h1>", "<h1>pkg-b</h1>")):
            self.write("pkg-a/index.html", bad)
            self.refused("pkg-a/index.html")

    def test_requires_python_on_some_links_only(self):
        a, b = sorted(self.old)
        self.write("pkg-a/index.html", page("pkg-a", [link("pkg-a", "0.1.0", a, self.old[a], "&gt;=3.10"),
                                                     link("pkg-a", "0.1.0", b, self.old[b])]))
        self.refused("data-requires-python on some links and not on others")

    def test_requires_python_stated_by_the_page_but_not_by_the_wheel(self):
        self.write("pkg-a/index.html", page("pkg-a", [link("pkg-a", "0.1.0", n, d, "&gt;=3.10") for n, d in self.old.items()]))
        self.fake.release("pkg-a", "0.2.0", rp=None)
        self.refused("declares no Requires-Python, and the page states it")


class TheIndexAsItIs(unittest.TestCase):
    def test_the_real_index_is_read_and_with_no_new_release_nothing_changes(self):
        saved = ui.api
        pages = {}
        for name, repo in ui.FOLLOWED.items():
            links = ui.read_page(os.path.join(INDEX, name, "index.html"), name)
            pages[repo] = ui.LINK.match(links[-1]).group("ver")
        ui.api = lambda path: [{"tag_name": "v" + pages[path.split("/")[2]], "assets": []}]
        try:
            self.assertEqual(ui.plan(INDEX), [])
        finally:
            ui.api = saved

    def test_command_line_refuses_arguments_it_does_not_understand(self):
        for bad in (("--wrte", "."), (".", "--write"), ("--write", ".", ".")):
            r = subprocess.run([sys.executable, os.path.join(HERE, "update_index.py"), *bad], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("usage:", r.stderr)


if __name__ == "__main__":
    unittest.main()
