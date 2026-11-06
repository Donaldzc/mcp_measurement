// static_tools.js —— 从 JS 源码 AST 抽取工具名候选
// 用法: node static_tools.js <acorn_path> <pkg_dir>
// 输出: JSON {files, calls:[], nameProps:[]}
//   calls     : tool/registerTool/addTool 等注册调用的第一参数（字符串字面量）
//   nameProps : 任意 { name: "字面量" } 属性（覆盖数据驱动式注册）
const fs = require("fs");
const path = require("path");

const acornPath = process.argv[2];
const pkgDir = process.argv[3];
const acorn = require(acornPath);

const TOOL_METHODS = new Set([
  "tool", "registerTool", "addTool", "register_tool", "add_tool",
  "defineTool", "createTool", "registerTools", "toolFor",
]);

const files = [];
const calls = new Set();
const nameProps = new Set();
const literals = new Set();
const KEBAB = /^[a-zA-Z][a-zA-Z0-9]*(?:[_-][a-zA-Z0-9]+){1,8}$/;

function walkDir(dir, depth) {
  if (depth > 6) return;
  let ents;
  try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of ents) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === "node_modules" || e.name === ".git" || e.name === "test" ||
          e.name === "__tests__" || e.name === "coverage") continue;
      walkDir(p, depth + 1);
    } else if (/\.(js|cjs|mjs)$/.test(e.name) && !/\.min\.js$/.test(e.name)) {
      let sz = 0;
      try { sz = fs.statSync(p).size; } catch { continue; }
      if (sz > 8 * 1024 * 1024) continue;
      files.push(p);
    }
  }
}

function strVal(node) {
  if (!node) return null;
  if (node.type === "Literal" && typeof node.value === "string") return node.value;
  if (node.type === "TemplateLiteral" && node.expressions.length === 0)
    return node.quasis.map(q => q.value.cooked).join("");
  return null;
}

function visit(node) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) { for (const n of node) visit(n); return; }

  if (node.type === "CallExpression" && node.callee && node.callee.type === "MemberExpression") {
    const prop = node.callee.property;
    const mname = prop && (prop.name || (prop.type === "Literal" && prop.value));
    if (typeof mname === "string" && TOOL_METHODS.has(mname) && node.arguments.length) {
      const a0 = node.arguments[0];
      const s = strVal(a0);
      if (s) calls.add(s);
      else if (a0 && a0.type === "ObjectExpression") {
        for (const pr of a0.properties) {
          if (!pr.key) continue;
          const k = pr.key.name || pr.key.value;
          if (k === "name") { const v = strVal(pr.value); if (v) calls.add(v); }
        }
      }
    }
  }

  if (node.type === "ObjectExpression") {
    for (const pr of node.properties || []) {
      if (!pr.key) continue;
      const k = pr.key.name || pr.key.value;
      if (k === "name") { const v = strVal(pr.value); if (v) nameProps.add(v); }
    }
  }

  if (node.type === "Literal" && typeof node.value === "string") {
    const v = node.value;
    if (v.length >= 3 && v.length <= 64 && KEBAB.test(v)) literals.add(v);
  }

  // 命名常量: const TOOL_NAME = "x" / const xToolName = "x"
  if (node.type === "VariableDeclarator" && node.id && node.id.type === "Identifier") {
    if (/tool.*name|name.*tool/i.test(node.id.name)) {
      const v = strVal(node.init);
      if (v) nameProps.add(v);
    }
  }

  for (const k in node) {
    if (k === "type" || k === "start" || k === "end") continue;
    const v = node[k];
    if (v && typeof v === "object") visit(v);
  }
}

walkDir(pkgDir, 0);
let parsed = 0, failed = 0;
for (const f of files) {
  let src;
  try { src = fs.readFileSync(f, "utf8"); } catch { continue; }
  try {
    const ast = acorn.parse(src, {
      ecmaVersion: "latest", sourceType: "module", allowHashBang: true,
      allowReturnOutsideFunction: true, allowAwaitOutsideFunction: true,
    });
    visit(ast);
    parsed++;
  } catch {
    try {
      const ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "script", allowHashBang: true });
      visit(ast);
      parsed++;
    } catch { failed++; }
  }
}

process.stdout.write(JSON.stringify({
  files: parsed, parse_failed: failed,
  calls: [...calls].sort(), nameProps: [...nameProps].sort(),
  literals: [...literals].sort(),
}));
