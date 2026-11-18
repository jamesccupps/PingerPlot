"""Make README.md's relative links absolute, for the package's page on PyPI.

PyPI shows README.md as the project's description, but it has no repository to
resolve `docs/screenshot.png` or `CHANGELOG.md` against, so on PyPI those are
broken images and dead links. .github/workflows/pypi.yml runs this on the
release's checkout, just before the build, so the published README points at
the same files on GitHub, at the release's tag. The README in the repository
is never changed.

    python pypi_readme.py README.md owner/repo vX.Y.Z

Images go to raw.githubusercontent.com (PyPI shows them through its image
proxy), other files to github.com/.../blob/<tag>/, folders to .../tree/<tag>/,
and in-page links (#section) to the README on GitHub, which resolves them the
way the README was written for. Fenced code blocks are left alone.
"""

import re
import sys

IMAGE = re.compile(r"\.(png|jpe?g|gif|svg|webp)$", re.IGNORECASE)
ABSOLUTE = re.compile(r"^([a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def absolute(url, repo, tag):
    if not url or ABSOLUTE.match(url):
        return url
    if url.startswith("#"):
        return f"https://github.com/{repo}/blob/{tag}/README.md{url}"
    path = url[2:] if url.startswith("./") else url.lstrip("/")
    if IMAGE.search(path.split("#")[0].split("?")[0]):
        return f"https://raw.githubusercontent.com/{repo}/{tag}/{path}"
    if path.endswith("/"):
        return f"https://github.com/{repo}/tree/{tag}/{path}"
    return f"https://github.com/{repo}/blob/{tag}/{path}"


def rewrite(text, repo, tag):
    def md(m):
        return m.group(1) + absolute(m.group(2), repo, tag) + m.group(3)

    def html(m):
        return m.group(1) + absolute(m.group(2), repo, tag) + m.group(3)

    out = []
    # Odd-numbered pieces are fenced code blocks: left as they are.
    for i, piece in enumerate(re.split(r"(^```.*?^```[ \t]*$)", text, flags=re.MULTILINE | re.DOTALL)):
        if i % 2 == 0:
            piece = re.sub(r"(\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))", md, piece)
            piece = re.sub(r"(\b(?:src|href)=\")([^\"]+)(\")", html, piece)
        out.append(piece)
    return "".join(out)


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    path, repo, tag = sys.argv[1:]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = rewrite(text, repo, tag)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    left = [u for u in re.findall(r"\]\(([^)\s]+)", new) if not ABSOLUTE.match(u)]
    print(f"{path}: links made absolute at {tag}; relative links left: {len(left)}")


if __name__ == "__main__":
    main()
