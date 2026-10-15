"""The wiki in a browser: docs/wiki rendered as HTML with a sidebar of
every wiki page, the planning docs and the epics, an on-page table of
contents, previous/next links and Mermaid diagrams.

    python -m tools.docs serve [--port 8090] [--no-open]   # live: each request renders the file as it is now
    python -m tools.docs build [--out docs/site]           # static copy, every link rewritten to .html

Pages are served at their path in the repository (docs/wiki/Home.md,
planning/epics/...), so the relative links the wiki already uses work
unchanged, including the ones into planning/. Only Markdown files (and
images under docs/ and planning/) are served, and only on 127.0.0.1.
"""

from __future__ import annotations

import html
import http.server
import os
import re
import shutil
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import markdown

from .generators import REPO

WIKI = REPO / "docs" / "wiki"
HOME = WIKI / "Home.md"
# Mermaid 11.4.1, an exact, long-published version.
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "site"}
_ASSETS = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
_FENCE = re.compile(r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_MD_LINK = re.compile(r"\]\(([^)#\s]+\.md)(#[^)]*)?\)")
_HREF_MD = re.compile(r'href="(?![a-z]+:)([^"#]+)\.md(#[^"]*)?"')


@dataclass
class NavItem:
    title: str
    path: Path


def _title(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ")


def navigation() -> list[tuple[str, list[NavItem]]]:
    """Wiki pages in the order Home lists them, then planning, then epics."""
    wiki = [NavItem("Home", HOME)]
    for text, target in re.findall(r"^\| \[([^\]]+)\]\(([^)]+\.md)\)", HOME.read_text(encoding="utf-8"), re.MULTILINE):
        path = (WIKI / target).resolve()
        if path.exists() and all(item.path != path for item in wiki):
            wiki.append(NavItem(text, path))
    for page in sorted(WIKI.glob("*.md")):  # anything Home forgot to list
        if all(item.path != page.resolve() for item in wiki):
            wiki.append(NavItem(_title(page), page.resolve()))

    planning_dir = REPO / "planning"
    planning = [
        NavItem(label, planning_dir / name)
        for label, name in (("Planning index", "00-index.md"), ("Decision log", "decision-log.md"), ("Backlog", "backlog.md"))
        if (planning_dir / name).exists()
    ]
    epics = []
    for path in sorted((planning_dir / "epics").glob("*.md")):
        title = re.sub(r"^Epic\s+(\d+)\s*--\s*", r"\1 · ", _title(path))
        epics.append(NavItem(title, path))
    return [("Wiki", wiki), ("Planning", planning), ("Epics", epics)]


def _rel(path: Path) -> str:
    return path.resolve().relative_to(REPO).as_posix()


def _url(path: Path, static: bool, current: Path | None = None) -> str:
    """Served: an absolute URL. Static: relative to the current page, so the
    copy also works opened straight from disk."""
    rel = _rel(path)
    if not static:
        return "/" + rel
    rel = rel[:-3] + ".html" if rel.endswith(".md") else rel
    if current is None:
        return rel
    return os.path.relpath(REPO / rel, (REPO / _rel(current)).parent).replace(os.sep, "/")


def render(path: Path, static: bool = False) -> str:
    """One Markdown file as a full HTML page."""
    text = path.read_text(encoding="utf-8")
    diagrams: list[str] = []

    def stash(match: re.Match) -> str:
        diagrams.append(match.group(1))
        return f"\n\nMERMAIDDIAGRAM{len(diagrams) - 1}\n\n"

    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "md_in_html", "sane_lists", "attr_list"],
        extension_configs={"toc": {"permalink": "#", "toc_depth": "2-3"}},
    )
    body = md.convert(_FENCE.sub(stash, text))
    for i, source in enumerate(diagrams):
        body = body.replace(f"<p>MERMAIDDIAGRAM{i}</p>", f'<pre class="mermaid">{html.escape(source)}</pre>')
    if static:
        body = _HREF_MD.sub(lambda m: f'href="{m.group(1)}.html{m.group(2) or ""}"', body)

    nav = navigation()
    current = path.resolve()
    sections, flat = [], []
    for heading, items in nav:
        links = []
        for item in items:
            flat.append(item)
            here = ' aria-current="page"' if item.path.resolve() == current else ""
            links.append(f'<li><a href="{_url(item.path, static, current)}"{here}>{html.escape(item.title)}</a></li>')
        open_attr = " open" if heading != "Epics" or any(i.path.resolve() == current for i in items) else ""
        sections.append(
            f'<details{open_attr}><summary>{heading}</summary><ul>{"".join(links)}</ul></details>'
        )

    position = next((i for i, item in enumerate(flat) if item.path.resolve() == current), None)
    pager = ""
    if position is not None:
        prev_item = flat[position - 1] if position > 0 else None
        next_item = flat[position + 1] if position + 1 < len(flat) else None
        pager = '<nav class="pager" aria-label="Previous and next page">'
        pager += f'<a href="{_url(prev_item.path, static, current)}">&larr; {html.escape(prev_item.title)}</a>' if prev_item else "<span></span>"
        pager += f'<a href="{_url(next_item.path, static, current)}">{html.escape(next_item.title)} &rarr;</a>' if next_item else "<span></span>"
        pager += "</nav>"

    toc_items = [
        f'<li class="toc-{t["level"]}"><a href="#{t["id"]}">{html.escape(t["name"])}</a></li>'
        for top in md.toc_tokens for t in [top, *top.get("children", [])]
    ]
    toc = f'<aside class="toc" aria-label="On this page"><p>On this page</p><ul>{"".join(toc_items)}</ul></aside>' if len(toc_items) > 1 else ""
    title = _title(path)
    return _PAGE.format(
        title=html.escape(title),
        home=_url(HOME, static, current),
        nav="".join(sections),
        body=body,
        toc=toc,
        pager=pager,
        source=html.escape(_rel(path)),
        mermaid=MERMAID,
    )


def _resolve(url_path: str) -> Path | None:
    """A request path to a file inside the repository, or None."""
    path = (REPO / unquote(url_path).lstrip("/")).resolve()
    try:
        rel = path.relative_to(REPO)
    except ValueError:
        return None
    if any(part.startswith(".") or part in _SKIP_DIRS for part in rel.parts) or not path.is_file():
        return None
    return path


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
        url = urlsplit(self.path).path
        if url in ("/", "/index.html"):
            self._send(302, b"", "text/plain", {"Location": _url(HOME, static=False)})
            return
        path = _resolve(url)
        if path is not None and path.suffix == ".md":
            self._send(200, render(path).encode("utf-8"), "text/html; charset=utf-8")
        elif path is not None and path.suffix.lower() in _ASSETS and _rel(path).startswith(("docs/", "planning/")):
            self._send(200, path.read_bytes(), _ASSETS[path.suffix.lower()])
        else:
            page = _PAGE_404.format(home=_url(HOME, static=False), path=html.escape(url))
            self._send(404, page.encode("utf-8"), "text/html; charset=utf-8")

    def _send(self, code: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass  # quiet: the console only says where the wiki is


def serve(port: int = 8090, open_browser: bool = True) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Wiki at {url}  (Ctrl+C to stop; edits show on refresh)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build(out: Path) -> list[Path]:
    """Every nav page plus every Markdown file they link to, as static HTML."""
    if out.exists():
        shutil.rmtree(out)
    queue = [item.path.resolve() for _, items in navigation() for item in items]
    seen: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        target = out / (_rel(path)[:-3] + ".html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(path, static=True), encoding="utf-8")
        for link, _ in _MD_LINK.findall(path.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+:", link):
                continue
            linked = (path.parent / link).resolve()
            if linked.is_relative_to(REPO) and _resolve("/" + _rel(linked)) == linked:
                queue.append(linked)
    home = _url(HOME, static=True).lstrip("/")
    (out / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={home}">'
        f'<title>Axyntro wiki</title><a href="{home}">Axyntro wiki</a>',
        encoding="utf-8",
    )
    return sorted(seen)


_STYLE = """
:root { --bg: #ffffff; --fg: #1d2330; --muted: #5b6475; --border: #dfe3ea; --side: #f5f7fa; --accent: #0b6bcb;
  --code-bg: #f1f3f6; --mark: #e7f0fb; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg: #14171c; --fg: #e3e7ee; --muted: #9aa3b2; --border: #2c323c;
  --side: #1a1e24; --accent: #6cb2ff; --code-bg: #20252d; --mark: #1f2d40; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
a { color: var(--accent); }
header.top { position: sticky; top: 0; z-index: 2; display: flex; gap: 1rem; align-items: center; padding: .6rem 1rem;
  background: var(--side); border-bottom: 1px solid var(--border); }
header.top a.brand { font-weight: 700; color: var(--fg); text-decoration: none; }
header.top input { flex: 0 1 18rem; margin-left: auto; padding: .35rem .6rem; border: 1px solid var(--border);
  border-radius: 6px; background: var(--bg); color: var(--fg); font: inherit; }
#menu { display: none; font: inherit; background: none; border: 1px solid var(--border); border-radius: 6px; color: var(--fg); }
.layout { display: grid; grid-template-columns: 17rem minmax(0, 1fr) 14rem; }
nav.side { position: sticky; top: 3.1rem; height: calc(100vh - 3.1rem); overflow-y: auto; padding: .5rem .75rem 2rem;
  border-right: 1px solid var(--border); background: var(--side); font-size: .92rem; }
nav.side summary { cursor: pointer; font-weight: 600; padding: .45rem .25rem; color: var(--muted); text-transform: uppercase;
  font-size: .75rem; letter-spacing: .05em; }
nav.side ul { list-style: none; margin: 0 0 .5rem; padding: 0; }
nav.side a { display: block; padding: .25rem .5rem; border-radius: 5px; color: var(--fg); text-decoration: none; }
nav.side a:hover { background: var(--mark); }
nav.side a[aria-current="page"] { background: var(--mark); color: var(--accent); font-weight: 600; }
main { min-width: 0; padding: 1.5rem 2.5rem 4rem; max-width: 62rem; }
main h1 { margin-top: 0; }
main h2 { border-bottom: 1px solid var(--border); padding-bottom: .25rem; margin-top: 2.2rem; }
.headerlink { opacity: 0; margin-left: .35rem; text-decoration: none; }
h2:hover .headerlink, h3:hover .headerlink { opacity: .6; }
table { border-collapse: collapse; display: block; overflow-x: auto; margin: 1rem 0; font-size: .92rem; }
th, td { border: 1px solid var(--border); padding: .4rem .6rem; text-align: left; vertical-align: top; }
th { background: var(--side); }
code { background: var(--code-bg); padding: .1rem .3rem; border-radius: 4px; font-size: .88em; }
pre { background: var(--code-bg); padding: .8rem 1rem; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
pre.mermaid { background: none; text-align: center; }
details > summary { cursor: pointer; }
aside.toc { position: sticky; top: 3.1rem; height: calc(100vh - 3.1rem); overflow-y: auto; padding: 1.5rem 1rem; font-size: .85rem; }
aside.toc p { margin: 0 0 .4rem; color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: .72rem; letter-spacing: .05em; }
aside.toc ul { list-style: none; margin: 0; padding: 0; }
aside.toc li { margin: .2rem 0; }
aside.toc .toc-3 { padding-left: .8rem; }
aside.toc a { color: var(--muted); text-decoration: none; }
aside.toc a:hover { color: var(--accent); }
.pager { display: flex; justify-content: space-between; gap: 1rem; margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border); }
.source { color: var(--muted); font-size: .8rem; margin-top: 1.5rem; }
@media (max-width: 1100px) { .layout { grid-template-columns: 15rem minmax(0, 1fr); } aside.toc { display: none; } }
@media (max-width: 720px) {
  .layout { display: block; }
  #menu { display: inline-block; }
  nav.side { display: none; position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--border); }
  body.menu-open nav.side { display: block; }
  main { padding: 1rem 1rem 3rem; }
  header.top input { flex-basis: 9rem; }
}
"""

_SCRIPT = """
<script type="module">
import mermaid from "{mermaid}";
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
mermaid.initialize({{ startOnLoad: true, theme: dark ? "dark" : "default", securityLevel: "strict" }});
</script>
<script>
document.getElementById("menu").addEventListener("click", () => document.body.classList.toggle("menu-open"));
const filter = document.getElementById("filter");
filter.addEventListener("input", () => {{
  const q = filter.value.trim().toLowerCase();
  document.querySelectorAll("nav.side li").forEach((li) => {{
    li.hidden = q !== "" && !li.textContent.toLowerCase().includes(q);
  }});
  document.querySelectorAll("nav.side details").forEach((d) => {{
    if (q) d.open = [...d.querySelectorAll("li")].some((li) => !li.hidden);
  }});
}});
document.querySelector('nav.side a[aria-current="page"]')?.scrollIntoView({{ block: "center" }});
</script>
"""

_PAGE = (
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Axyntro wiki</title><style>"""
    + _STYLE.replace("{", "{{").replace("}", "}}")
    + """</style></head>
<body>
<header class="top"><button id="menu" type="button" aria-label="Show pages">&#9776;</button>
<a class="brand" href="{home}">Axyntro wiki</a>
<input id="filter" type="search" placeholder="Filter pages..." aria-label="Filter pages"></header>
<div class="layout">
<nav class="side" aria-label="Pages">{nav}</nav>
<main>{body}{pager}<p class="source">Source: <code>{source}</code></p></main>
{toc}
</div>"""
    + _SCRIPT
    + "</body></html>"
)

_PAGE_404 = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Not in the wiki</title>
<style>body {{ font: 16px/1.6 system-ui, sans-serif; margin: 3rem; }} @media (prefers-color-scheme: dark) {{ body {{ background: #14171c; color: #e3e7ee; }} a {{ color: #6cb2ff; }} }}</style></head>
<body><h1>Not in the wiki</h1><p><code>{path}</code> isn't a Markdown page the wiki can show.
Links to source code point into the repository; open those in your editor.</p><p><a href="{home}">Back to the wiki home</a></p></body></html>"""
