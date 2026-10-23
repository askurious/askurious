// Runs app/static/samplesize.js for tests/test_samplesize.py: calls as JSON on stdin ([name, args...]), results as
// JSON on stdout.
const core = require("../app/static/samplesize.js");

const calls = JSON.parse(require("fs").readFileSync(0, "utf8"));
const nan = v => (v === null ? NaN : v);
const results = calls.map(function ([name, ...args]) {
  if (name === "plan") {
    const fields = {};
    for (const [k, v] of Object.entries(args[1])) fields[k] = typeof v === "boolean" ? v : nan(v);
    return core.plan(args[0], fields);
  }
  return core[name](...args);
});
process.stdout.write(JSON.stringify(results));
