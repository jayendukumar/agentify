"""Python side of the code-health analyzer: one pass over each file's AST
collecting complexity per function, imports (for coupling/cycles),
definitions and references (for dead code) and tokens (for duplication).

Complexity rules:

- Cyclomatic (McCabe): 1 + one per if/elif, loop, except handler, ternary,
  match case, comprehension `for`/`if`, and each extra operand of a boolean
  operator (`a and b and c` adds 2).
- Cognitive (after SonarSource's definition): +1 for each if, ternary,
  loop, except, match, plus the current nesting depth for the ones that
  nest; `elif`/`else` add a flat +1; each run of like boolean operators
  adds +1. Nested functions and lambdas raise nesting for their contents
  but are otherwise reported on their own.
"""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionMetrics:
    name: str
    qualname: str
    line: int
    end_line: int
    cyclomatic: int
    cognitive: int


@dataclass
class ImportEdge:
    target: str          # dotted module name as written (resolved for relative imports)
    names: list[str]
    deferred: bool       # inside a function body (a lazy import, often used to dodge a cycle)


@dataclass
class Definition:
    name: str
    kind: str            # function | class | method
    line: int
    decorated_by: list[str]


@dataclass
class PyFileAnalysis:
    path: str            # repo-relative, forward slashes
    module: str          # dotted module name
    loc: int
    functions: list[FunctionMetrics] = field(default_factory=list)
    imports: list[ImportEdge] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    references: set[str] = field(default_factory=set)
    string_literals: set[str] = field(default_factory=set)
    tokens: list[tuple[str, int]] = field(default_factory=list)
    syntax_error: str | None = None


_NESTING_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)


class _ComplexityVisitor:
    """Computes both metrics for one function body, without descending into
    nested named functions/classes (those are measured separately)."""

    def __init__(self) -> None:
        self.cyclomatic = 1
        self.cognitive = 0

    def run(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for stmt in fn.body:
            self._visit(stmt, 0)

    def _visit_all(self, nodes, nesting: int) -> None:
        for node in nodes:
            self._visit(node, nesting)

    def _bool_ops(self, node: ast.BoolOp) -> None:
        # Each BoolOp node is one run of a single operator; a differently
        # operated child BoolOp is a new run (+1), visited normally.
        self.cyclomatic += len(node.values) - 1
        self.cognitive += 1

    def _visit_if(self, node: ast.If, nesting: int, is_elif: bool) -> None:
        self.cyclomatic += 1
        self.cognitive += 1 if is_elif else 1 + nesting
        self._visit(node.test, nesting)
        self._visit_all(node.body, nesting + 1)
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self._visit_if(node.orelse[0], nesting, is_elif=True)
        elif node.orelse:
            self.cognitive += 1
            self._visit_all(node.orelse, nesting + 1)

    def _visit(self, node: ast.AST, nesting: int) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return  # measured on its own
        if isinstance(node, ast.Lambda):
            self._visit(node.body, nesting + 1)
            return
        if isinstance(node, ast.If):
            self._visit_if(node, nesting, is_elif=False)
            return
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self.cyclomatic += 1
            self.cognitive += 1 + nesting
            self._visit(node.iter if not isinstance(node, ast.While) else node.test, nesting)
            self._visit_all(node.body, nesting + 1)
            if node.orelse:
                self.cognitive += 1
                self._visit_all(node.orelse, nesting + 1)
            return
        if isinstance(node, ast.Try) or (hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)):
            self._visit_all(node.body, nesting)
            for handler in node.handlers:
                self.cyclomatic += 1
                self.cognitive += 1 + nesting
                self._visit_all(handler.body, nesting + 1)
            self._visit_all(node.orelse, nesting)
            self._visit_all(node.finalbody, nesting)
            return
        if isinstance(node, ast.Match):
            self.cognitive += 1 + nesting
            self._visit(node.subject, nesting)
            for case in node.cases:
                self.cyclomatic += 1
                self._visit_all(case.body, nesting + 1)
            return
        if isinstance(node, ast.IfExp):
            self.cyclomatic += 1
            self.cognitive += 1 + nesting
            for child in (node.test, node.body, node.orelse):
                self._visit(child, nesting + 1)
            return
        if isinstance(node, ast.BoolOp):
            self._bool_ops(node)
        if isinstance(node, ast.comprehension):
            self.cyclomatic += 1 + len(node.ifs)
            self.cognitive += 1 + len(node.ifs)
        for child in ast.iter_child_nodes(node):
            self._visit(child, nesting)


def _decorator_name(dec: ast.expr) -> str:
    target = dec.func if isinstance(dec, ast.Call) else dec
    return ast.unparse(target)


def module_name_for(path: Path, package_root: Path) -> str:
    rel = path.relative_to(package_root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join([*base, target] if target else base)


def _tokens(source: str) -> list[tuple[str, int]]:
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENCODING, tokenize.ENDMARKER}
    out: list[tuple[str, int]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type not in skip:
                out.append((tok.string, tok.start[0]))
    except (tokenize.TokenError, SyntaxError):
        pass
    return out


def analyze_file(path: Path, repo_root: Path, package_root: Path) -> PyFileAnalysis:
    source = path.read_text(encoding="utf-8")
    module = module_name_for(path, package_root)
    is_package = path.name == "__init__.py"
    result = PyFileAnalysis(
        path=path.relative_to(repo_root).as_posix(),
        module=module,
        loc=sum(1 for line in source.splitlines() if line.strip()),
    )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        result.syntax_error = str(exc)
        return result
    result.tokens = _tokens(source)

    def visit(node: ast.AST, scope: list[str], in_function: bool, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visitor = _ComplexityVisitor()
                visitor.run(child)
                qual = ".".join([*scope, child.name])
                result.functions.append(FunctionMetrics(
                    name=child.name, qualname=qual, line=child.lineno,
                    end_line=child.end_lineno or child.lineno,
                    cyclomatic=visitor.cyclomatic, cognitive=visitor.cognitive,
                ))
                if not in_function:
                    result.definitions.append(Definition(
                        name=child.name, kind="method" if in_class else "function", line=child.lineno,
                        decorated_by=[_decorator_name(d) for d in child.decorator_list],
                    ))
                visit(child, [*scope, child.name], True, False)
            elif isinstance(child, ast.ClassDef):
                if not in_function:
                    result.definitions.append(Definition(
                        name=child.name, kind="class", line=child.lineno,
                        decorated_by=[_decorator_name(d) for d in child.decorator_list],
                    ))
                visit(child, [*scope, child.name], in_function, True)
            else:
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        result.imports.append(ImportEdge(alias.name, [], in_function))
                elif isinstance(child, ast.ImportFrom):
                    names = [a.name for a in child.names]
                    if child.level:
                        target = _resolve_relative(module, is_package, child.level, child.module)
                    else:
                        target = child.module or ""
                    result.imports.append(ImportEdge(target, names, in_function))
                    # `from x import y` references y, as far as dead code goes.
                    result.references.update(names)
                elif isinstance(child, ast.Name):
                    result.references.add(child.id)
                elif isinstance(child, ast.Attribute):
                    result.references.add(child.attr)
                elif isinstance(child, ast.Constant) and isinstance(child.value, str) and len(child.value) < 200:
                    result.string_literals.add(child.value)
                visit(child, scope, in_function, in_class)

    visit(tree, [], False, False)
    return result


def analyze_tree(repo_root: Path, package_root: Path, globs: list[str], exclude: tuple[str, ...] = ()) -> list[PyFileAnalysis]:
    files: list[Path] = []
    for pattern in globs:
        files.extend(package_root.glob(pattern))
    seen: set[Path] = set()
    out: list[PyFileAnalysis] = []
    for path in sorted(files):
        if path in seen or any(part in exclude for part in path.parts):
            continue
        seen.add(path)
        out.append(analyze_file(path, repo_root, package_root))
    return out
