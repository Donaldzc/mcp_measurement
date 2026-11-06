// spec_aware.js — spec/table-aware tool enumerator
// usage: node spec_aware.js <acorn_path> <pkg_dir> [name_prefix]
// Finds, in order of preference:
//   1. bundled OpenAPI/Swagger documents  -> one candidate per operation
//   2. compiled tool/operation tables in JS bundles -> one candidate per table entry
// Unlike static_tools.js this does not skip large bundles and does not require a
// registration call: it reads the *inputs* of a generator, not the call sites.
const fs = require("fs");
const path = require("path");
const acornPath = process.argv[2];
const pkgDir = process.argv[3];
const prefix = process.argv[4] || "";
const acorn = require(acornPath);

const SKIP_DIRS = new Set(["node_modules", ".git", "test", "__tests__", "coverage"]);
const METHODS = new Set(["get", "put", "post", "delete", "options", "head", "patch", "trace"]);

function walk(dir, depth, out) {
  if (depth > 6) return;
  let ents;
  try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of ents) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name)) walk(p, depth + 1, out); }
    else out.push(p);
  }
}

const files = [];
walk(pkgDir, 0, files);

// ---------- 1. OpenAPI documents ----------
function openapiTools() {
  const hits = [];
  for (const f of files) {
    if (!/\.json$/i.test(f)) continue;
    let sz = 0;
    try { sz = fs.statSync(f).size; } catch { continue; }
    if (sz > 64 * 1024 * 1024) continue;
    let doc;
    try { doc = JSON.parse(fs.readFileSync(f, "utf8")); } catch { continue; }
    if (!doc || typeof doc !== "object" || !doc.paths) continue;
    if (!("openapi" in doc) && !("swagger" in doc)) continue;
    const ops = [];
    for (const [p, item] of Object.entries(doc.paths)) {
      if (!item || typeof item !== "object") continue;
      for (const [m, op] of Object.entries(item)) {
        if (!METHODS.has(m.toLowerCase())) continue;
        const nm = (op && (op.operationId || op["x-mcp-tool-name"])) || null;
        ops.push({
          name: nm ? prefix + nm : null,
          raw: nm,
          method: m.toUpperCase(), path: p,
          summary: (op && op.summary) || null,
          deprecated: !!(op && op.deprecated),
        });
      }
    }
    if (ops.length) hits.push({ file: path.relative(pkgDir, f), n_ops: ops.length, ops });
  }
  return hits;
}

// ---------- 2. compiled tables ----------
function tableTools() {
  const hits = [];
  for (const f of files) {
    if (!/\.(js|cjs|mjs)$/i.test(f) || /\.min\.js$/i.test(f)) continue;
    let src;
    try { src = fs.readFileSync(f, "utf8"); } catch { continue; }
    let ast = null;
    try {
      ast = acorn.parse(src, {
        ecmaVersion: "latest", sourceType: "module", allowHashBang: true,
        allowReturnOutsideFunction: true, allowAwaitOutsideFunction: true,
      });
    } catch { continue; }
    const entries = [];
    const domains = [];
    (function visit(node) {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) { for (const n of node) visit(n); return; }
      if (node.type === "ObjectExpression") {
        const props = {};
        let readOnlyHint = null;
        for (const pr of node.properties || []) {
          if (!pr.key) continue;
          const k = pr.key.name || pr.key.value;
          if (pr.value && pr.value.type === "Literal") props[k] = pr.value.value;
          else if (pr.value && pr.value.type === "ObjectExpression") {
            props[k] = "{obj}";
            // annotations / toolAnnotations: keep the read-only hint, which is
            // what separates the reader from the updater tool in a domain table
            for (const inner of pr.value.properties || []) {
              const ik = inner.key && (inner.key.name || inner.key.value);
              if ((ik === "readOnlyHint" || ik === "readOnly") && inner.value &&
                  inner.value.type === "Literal") {
                readOnlyHint = inner.value.value === true;
              }
            }
          }
        }
        // tool-table entry: a literal name plus the fields a tool definition
        // carries. A bare {name, description} pair is common in unrelated data
        // (schema names, library config), so a *schema-shaped* field must be
        // present as well.
        const toolShaped = (props.inputSchema === "{obj}") ||
                           (props.annotations === "{obj}") ||
                           (props.parameters === "{obj}");
        // a legal tool identifier: no whitespace, and either lowercase-initial
        // or carrying a separator. This drops prose strings that happen to sit
        // in objects with a schema-shaped key.
        const legalName = typeof props.name === "string" &&
                          !/\s/.test(props.name) && props.name.length <= 64 &&
                          (/^[a-z]/.test(props.name) || /[-_.]/.test(props.name));
        if (legalName && toolShaped) {
          entries.push({ name: prefix + props.name, raw: props.name,
                         readOnlyHint,
                         description: typeof props.description === "string"
                           ? props.description.slice(0, 120) : null });
        }
        // domain-operation entry: domain + operation (+ read-only hint)
        if (typeof props.domain === "string" && typeof props.operation === "string") {
          domains.push({ domain: props.domain, operation: props.operation, readOnlyHint });
        }
      }
      for (const k in node) {
        if (k === "type" || k === "start" || k === "end") continue;
        const v = node[k];
        if (v && typeof v === "object") visit(v);
      }
    })(ast);
    if (entries.length || domains.length)
      hits.push({ file: path.relative(pkgDir, f), bytes: src.length,
                  entries, domains });
  }
  return hits;
}

const spec = openapiTools();
const tables = tableTools();
process.stdout.write(JSON.stringify({ pkg_dir: pkgDir, prefix, spec, tables }));
