// Code-health analyzer for the TypeScript/React frontend.
//
// Uses the frontend's own `typescript` package (no extra dependency) to parse
// every .ts/.tsx file under the given root and print one JSON document on
// stdout with, per file:
//   - functions: cyclomatic + cognitive complexity per named function
//   - imports:   module specifiers (relative ones resolved to repo paths)
//   - exports:   exported symbol names
//   - idents:    every identifier referenced (for dead-export detection)
//   - tokens:    [text, line] pairs, comments/whitespace excluded (duplication)
//
// Usage: node ts_analyze.mjs <frontend-dir> <src-subdir>
// The Python side (tools/code_health/tsbridge.py) owns the interpretation.

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const [, , frontendDir, srcSub = "src"] = process.argv;
const require = createRequire(path.join(path.resolve(frontendDir), "package.json"));
const ts = require("typescript");

const root = path.resolve(frontendDir);
const srcRoot = path.join(root, srcSub);

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) out.push(full);
  }
  return out;
}

const K = ts.SyntaxKind;
const LOOP_KINDS = new Set([K.ForStatement, K.ForInStatement, K.ForOfStatement, K.WhileStatement, K.DoStatement]);
const FUNC_KINDS = new Set([
  K.FunctionDeclaration, K.MethodDeclaration, K.ArrowFunction, K.FunctionExpression,
  K.Constructor, K.GetAccessor, K.SetAccessor,
]);
const LOGICAL = new Set([K.AmpersandAmpersandToken, K.BarBarToken, K.QuestionQuestionToken]);

function functionName(node) {
  if (node.name && ts.isIdentifier(node.name)) return node.name.text;
  if (node.kind === K.Constructor) return "constructor";
  const parent = node.parent;
  if (parent && ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) return parent.name.text;
  if (parent && ts.isPropertyAssignment(parent) && ts.isIdentifier(parent.name)) return parent.name.text;
  if (parent && ts.isPropertyDeclaration(parent) && ts.isIdentifier(parent.name)) return parent.name.text;
  return null; // anonymous: its complexity is attributed to the enclosing function
}

// Flattens a chain of &&/||/?? into its operator sequence, so `a && b && c`
// counts as one sequence (cognitive +1) and `a && b || c` as two.
function logicalOperators(node, ops = []) {
  if (ts.isParenthesizedExpression(node)) return logicalOperators(node.expression, ops);
  if (ts.isBinaryExpression(node) && LOGICAL.has(node.operatorToken.kind)) {
    logicalOperators(node.left, ops);
    ops.push(node.operatorToken.kind);
    logicalOperators(node.right, ops);
  }
  return ops;
}

function isLogical(node) {
  return ts.isBinaryExpression(node) && LOGICAL.has(node.operatorToken.kind);
}

function analyzeFunction(fn, sf) {
  let cyclomatic = 1;
  let cognitive = 0;

  function visit(node, nesting) {
    // A nested named function is reported on its own; anonymous callbacks
    // (JSX handlers, .map callbacks) add to this function with extra nesting.
    if (node !== fn && FUNC_KINDS.has(node.kind)) {
      if (functionName(node) !== null) return;
      ts.forEachChild(node, (c) => visit(c, nesting + 1));
      return;
    }

    if (ts.isIfStatement(node)) {
      cyclomatic += 1;
      const isElseIf = node.parent && ts.isIfStatement(node.parent) && node.parent.elseStatement === node;
      cognitive += isElseIf ? 1 : 1 + nesting;
      visit(node.expression, nesting);
      visit(node.thenStatement, nesting + 1);
      if (node.elseStatement) {
        if (ts.isIfStatement(node.elseStatement)) visit(node.elseStatement, nesting);
        else {
          cognitive += 1;
          visit(node.elseStatement, nesting + 1);
        }
      }
      return;
    }
    if (LOOP_KINDS.has(node.kind) || ts.isCatchClause(node) || ts.isSwitchStatement(node)) {
      if (!ts.isSwitchStatement(node)) cyclomatic += 1;
      cognitive += 1 + nesting;
      ts.forEachChild(node, (c) => visit(c, nesting + 1));
      return;
    }
    if (ts.isCaseClause(node)) cyclomatic += 1;
    if (ts.isConditionalExpression(node)) {
      cyclomatic += 1;
      cognitive += 1 + nesting;
      ts.forEachChild(node, (c) => visit(c, nesting + 1));
      return;
    }
    if (isLogical(node) && !(node.parent && isLogical(node.parent))) {
      const ops = logicalOperators(node);
      cyclomatic += ops.length;
      let sequences = 0;
      ops.forEach((op, i) => { if (i === 0 || ops[i - 1] !== op) sequences += 1; });
      cognitive += sequences;
    }
    ts.forEachChild(node, (c) => visit(c, nesting));
  }

  ts.forEachChild(fn, (c) => visit(c, 0));
  const start = sf.getLineAndCharacterOfPosition(fn.getStart(sf)).line + 1;
  const end = sf.getLineAndCharacterOfPosition(fn.getEnd()).line + 1;
  return { name: functionName(fn), line: start, end_line: end, cyclomatic, cognitive };
}

function resolveImport(fromFile, spec) {
  if (!spec.startsWith(".")) return { external: spec };
  const base = path.resolve(path.dirname(fromFile), spec);
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")]) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return { internal: path.relative(root, candidate).split(path.sep).join("/") };
    }
  }
  return { unresolved: spec };
}

function tokens(text, isTsx) {
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, true, isTsx ? ts.LanguageVariant.JSX : ts.LanguageVariant.Standard, text);
  const out = [];
  let lineStarts = ts.computeLineStarts(text);
  for (let kind = scanner.scan(); kind !== K.EndOfFileToken; kind = scanner.scan()) {
    const pos = scanner.getTokenStart ? scanner.getTokenStart() : scanner.getTokenPos();
    const line = ts.computeLineAndCharacterOfPosition(lineStarts, pos).line + 1;
    out.push([scanner.getTokenText(), line]);
  }
  return out;
}

const files = {};
for (const file of walk(srcRoot)) {
  const text = fs.readFileSync(file, "utf8");
  const isTsx = file.endsWith(".tsx");
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, isTsx ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const rel = path.relative(root, file).split(path.sep).join("/");
  const functions = [];
  const imports = [];
  const exports = [];
  const idents = new Set();
  const identCounts = {};

  function visit(node) {
    if (FUNC_KINDS.has(node.kind) && functionName(node) !== null) functions.push(analyzeFunction(node, sf));
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const names = [];
      const clause = node.importClause;
      if (clause?.name) names.push("default");
      if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
        for (const el of clause.namedBindings.elements) names.push((el.propertyName ?? el.name).text);
      }
      if (clause?.namedBindings && ts.isNamespaceImport(clause.namedBindings)) names.push("*");
      imports.push({ ...resolveImport(file, node.moduleSpecifier.text), names, type_only: Boolean(clause?.isTypeOnly) });
    }
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      imports.push({ ...resolveImport(file, node.moduleSpecifier.text), names: ["*"], type_only: false });
    }
    const mods = ts.canHaveModifiers(node) ? ts.getModifiers(node) : undefined;
    if (mods?.some((m) => m.kind === K.ExportKeyword)) {
      const isDefault = mods.some((m) => m.kind === K.DefaultKeyword);
      if (isDefault) exports.push({ name: "default", line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1 });
      else if (ts.isVariableStatement(node)) {
        for (const d of node.declarationList.declarations) {
          if (ts.isIdentifier(d.name)) exports.push({ name: d.name.text, line: sf.getLineAndCharacterOfPosition(d.getStart(sf)).line + 1 });
        }
      } else if (node.name && ts.isIdentifier(node.name)) {
        exports.push({ name: node.name.text, line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1 });
      }
    }
    if (ts.isExportAssignment(node)) exports.push({ name: "default", line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1 });
    if (ts.isIdentifier(node)) {
      idents.add(node.text);
      identCounts[node.text] = (identCounts[node.text] ?? 0) + 1;
    }
    ts.forEachChild(node, visit);
  }
  visit(sf);

  const nonBlank = text.split(/\r?\n/).filter((l) => l.trim() !== "").length;
  files[rel] = { loc: nonBlank, functions, imports, exports, idents: [...idents], ident_counts: identCounts, tokens: tokens(text, isTsx) };
}

process.stdout.write(JSON.stringify({ root: path.basename(root), files }));
