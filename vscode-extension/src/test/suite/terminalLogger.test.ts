import * as assert from "assert";
import {
  TerminalLogger,
  terminalLogLines,
  terminalSeparatorLine,
} from "../../terminalLogger";

suite("terminal logger", () => {
  test("formats ANSI colored terminal lines", () => {
    assert.deepStrictEqual(terminalLogLines("success", "done"), [
      "\x1b[32m[FreeCM]\x1b[0m done",
    ]);
    assert.deepStrictEqual(terminalLogLines("warning", "first\nsecond"), [
      "\x1b[33m[FreeCM]\x1b[0m first",
      "\x1b[33m[FreeCM]\x1b[0m second",
    ]);
  });

  test("preserves shell-sensitive text as literal output", () => {
    assert.deepStrictEqual(terminalLogLines("error", "can't write $lock \\ path"), [
      "\x1b[31m[FreeCM]\x1b[0m can't write $lock \\ path",
    ]);
  });

  test("formats a muted separator line", () => {
    assert.strictEqual(
      terminalSeparatorLine(),
      `\x1b[90m${"-".repeat(72)}\x1b[0m`,
    );
  });

  test("prints a separator only after pending log output", () => {
    const logger = new TerminalLogger();
    const writes: string[] = [];
    logger.onDidWrite((value) => writes.push(value));

    logger.separator();
    assert.deepStrictEqual(writes, []);

    logger.log("info", "one");
    logger.log("success", "two");
    logger.separator();
    logger.separator();

    assert.deepStrictEqual(writes, [
      "\x1b[36m[FreeCM]\x1b[0m one\r\n",
      "\x1b[32m[FreeCM]\x1b[0m two\r\n",
      `${terminalSeparatorLine()}\r\n`,
    ]);
  });
});
