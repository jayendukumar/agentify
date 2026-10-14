"""Copy-paste (type-1) clone detection over token streams.

Whitespace and comments never reach this module (the analyzers drop them),
so two blocks that differ only in formatting or comments are clones. A
clone is a run of at least `min_tokens` identical tokens appearing in two
places -- the same idea as PMD CPD and jscpd. Windows of `min_tokens`
tokens are hashed; every pair of matching windows is then extended to its
maximal length, and pairs already covered by a longer match are dropped.
"""

from __future__ import annotations

import keyword
import re
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Clone:
    tokens: int
    a_file: str
    a_start: int
    a_end: int
    b_file: str
    b_start: int
    b_end: int

    @property
    def lines(self) -> int:
        return max(self.a_end - self.a_start + 1, self.b_end - self.b_start + 1)


def find_clones(
    files: dict[str, list[tuple[str, int]]], *, min_tokens: int = 70, min_lines: int = 6
) -> list[Clone]:
    streams = {name: [t for t, _ in toks] for name, toks in files.items()}
    lines = {name: [ln for _, ln in toks] for name, toks in files.items()}

    index: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for name, toks in streams.items():
        for i in range(len(toks) - min_tokens + 1):
            index[hash(tuple(toks[i : i + min_tokens]))].append((name, i))

    covered: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    clones: list[Clone] = []
    for locations in index.values():
        if len(locations) < 2 or len(locations) > 50:  # >50: boilerplate like import blocks
            continue
        for x in range(len(locations)):
            for y in range(x + 1, len(locations)):
                (fa, ia), (fb, ib) = locations[x], locations[y]
                if fa == fb and abs(ia - ib) < min_tokens:
                    continue  # overlapping self-match
                key = (fa, fb)
                if any(s <= ia <= e and s2 <= ib <= e2 for s, e, s2, e2 in covered[key]):
                    continue
                ta, tb = streams[fa], streams[fb]
                if ta[ia : ia + min_tokens] != tb[ib : ib + min_tokens]:
                    continue  # hash collision
                # Extend backwards then forwards to the maximal match.
                while ia > 0 and ib > 0 and ta[ia - 1] == tb[ib - 1] and (fa != fb or ia - 1 != ib):
                    ia -= 1
                    ib -= 1
                length = min_tokens
                while ia + length < len(ta) and ib + length < len(tb) and ta[ia + length] == tb[ib + length]:
                    length += 1
                covered[key].append((ia, ia + length - 1, ib, ib + length - 1))
                clone = Clone(
                    tokens=length,
                    a_file=fa, a_start=lines[fa][ia], a_end=lines[fa][ia + length - 1],
                    b_file=fb, b_start=lines[fb][ib], b_end=lines[fb][ib + length - 1],
                )
                if clone.lines >= min_lines:
                    clones.append(clone)
    clones.sort(key=lambda c: -c.tokens)
    return clones


_TS_KEYWORDS = set("""break case catch class const continue debugger default delete do else enum export extends
false finally for function if import in instanceof new null return super switch this throw true try typeof var
void while with as implements interface let package private protected public static yield async await of type
readonly declare keyof namespace abstract any boolean never number object string symbol undefined unknown""".split())
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_LITERAL = re.compile(r"^(\d|['\"`]|[rbfRBF]+['\"])")


def normalize(files: dict[str, list[tuple[str, int]]]) -> dict[str, list[tuple[str, int]]]:
    """Type-2 view: identifiers -> `ID`, literals -> `LIT`, keywords kept --
    finds copy-paste-then-rename clones, at the cost of some structural
    false positives (two similar-shaped dict literals, say)."""
    def norm(tok: str) -> str:
        if _LITERAL.match(tok):
            return "LIT"
        if _IDENT.match(tok) and not keyword.iskeyword(tok) and tok not in _TS_KEYWORDS:
            return "ID"
        return tok
    return {name: [(norm(t), ln) for t, ln in toks] for name, toks in files.items()}


def duplicated_lines(clones: list[Clone]) -> dict[str, set[int]]:
    """File -> set of line numbers that sit inside at least one clone."""
    out: dict[str, set[int]] = defaultdict(set)
    for c in clones:
        out[c.a_file].update(range(c.a_start, c.a_end + 1))
        out[c.b_file].update(range(c.b_start, c.b_end + 1))
    return out
