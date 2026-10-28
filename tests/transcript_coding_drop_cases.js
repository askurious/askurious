// Runs app/static/transcript_coding_drop.js for tests/interview/test_transcript_coding_map.py: {data, calls} as JSON on
// stdin ([name, a id, b id]), the results as JSON on stdout.
const drop = require("../app/static/transcript_coding_drop.js");

const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const cats = input.data.cats;
const results = input.calls.map(function ([name, a, b]) {
  return name === "siblings" ? drop.siblings(cats[a], cats[b]) : drop[name](input.data, cats[a], cats[b]);
});
process.stdout.write(JSON.stringify(results));
