// Prints JSON {slug: {lint, stats, prompts}} from site/checker.js, for tests/test_site.py.
const fs = require("fs"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "docs", "index.html"), "utf8");
const m = html.match(/<script type="application\/json" id="ug-data">([\s\S]*?)<\/script>/);
const data = JSON.parse(m[1].replace(/<\\\//g, "</"));
const c = require("../site/checker.js");
c.setData(data);
const all = Object.assign({}, data.examples);
all[data.badSlug] = data.bad;
const out = {};
for (const [slug, s] of Object.entries(all)) {
  const r = c.lint(s);
  out[slug] = {
    lint: { errors: r.errors, warnings: r.warnings },
    stats: c.stats(s),
    lintHtml: c.lintHtml(r, s, "../../"),
    prompts: {}
  };
  for (const k of ["image", "kling", "veo3", "all"]) out[slug].prompts[k] = c.renderPrompts(s, k);
}
process.stdout.write(JSON.stringify(out));
