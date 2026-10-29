// Runs the computations of app/static/transcript_player.js for tests/interview/test_transcript_player.py: [[name,
// ...arguments]] as JSON on stdin, the results as JSON on stdout.
const tp = require("../app/static/transcript_player.js");

const calls = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(calls.map(([name, ...args]) => tp[name](...args))));
