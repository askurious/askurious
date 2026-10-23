// Runs app/static/richtext.js for tests/test_richtext.py: cases as JSON on stdin, results as JSON on stdout.
// A case: {text, mode, gaps, op, arg}. \uE000 and \uE001 in the text mark the start and end of the selection.
const core = require("../app/static/richtext.js");

const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
const results = cases.map(function (k) {
  const opts = { gaps: !!k.gaps };
  const blocks = core.extractCarets(core.parse(k.text, k.mode, opts));
  if (k.op) core[k.op](blocks, k.arg, k.mode);
  const written = core.serialize(blocks, k.mode, opts);
  return {
    model: core.norm(blocks, k.mode),
    text: written.text,
    exact: written.exact,
    again: core.serialize(core.parse(written.text, k.mode, opts), k.mode, opts).text,
    shown: core.serialize(core.withCarets(blocks, k.mode, opts, written.level), k.mode, opts).text,
  };
});
process.stdout.write(JSON.stringify(results));
