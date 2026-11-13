import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [markedPath, ...chapterPaths] = process.argv.slice(2);
const { marked } = markedPath ? await import(pathToFileURL(markedPath)) : await import("marked");
const chapters = chapterPaths.map((path, index) => {
  let source = readFileSync(path, "utf8");
  if (index === 0) {
    source = source.split(/^## Передача и обновление\s*$/m)[0];
  }
  return marked.lexer(source, { gfm: true });
});
process.stdout.write(JSON.stringify(chapters));
