// Parse one JavaScript source into an ESTree tree and print it as JSON.
//
// Reads the source from stdin and writes to stdout, so no file path or shell
// quoting is involved and the source never touches a command line.
//
// Output is always a JSON object. On success it carries the tree, on failure
// the parse error with its position. The exit code stays 0 either way, since a
// file that does not parse is a fact about the project under audit rather than
// a failure of this script.

import { Parser } from "acorn";
import jsx from "acorn-jsx";

const JsxParser = Parser.extend(jsx());

function read() {
  return new Promise((resolve, reject) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => { buf += c; });
    process.stdin.on("end", () => resolve(buf));
    process.stdin.on("error", reject);
  });
}

// Acorn refuses import and export in script mode and refuses a bare return in
// module mode, and a file gives no reliable signal of which it is. Try module
// first, since that is what Vite projects use, then fall back to script for
// CommonJS. Whichever parses is the right reading.
function parse(src) {
  const base = {
    ecmaVersion: "latest",
    locations: true,
    allowHashBang: true,
    allowAwaitOutsideFunction: true,
    allowReturnOutsideFunction: true,
  };
  let firstError = null;
  for (const sourceType of ["module", "script"]) {
    try {
      return { ok: true, sourceType, ast: JsxParser.parse(src, { ...base, sourceType }) };
    } catch (err) {
      if (!firstError) firstError = err;
    }
  }
  return {
    ok: false,
    error: String(firstError && firstError.message || "parse failed"),
    line: (firstError && firstError.loc && firstError.loc.line) || null,
    column: (firstError && firstError.loc && firstError.loc.column) || null,
  };
}

read()
  .then((src) => {
    let out;
    try {
      out = parse(src);
    } catch (err) {
      out = { ok: false, error: String(err && err.message || err), line: null, column: null };
    }
    process.stdout.write(JSON.stringify(out));
  })
  .catch((err) => {
    process.stdout.write(JSON.stringify({
      ok: false, error: "could not read source: " + String(err), line: null, column: null,
    }));
  });
