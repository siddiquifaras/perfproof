const test = require("node:test");
const assert = require("node:assert");
const { checksum } = require("../work.js");

test("checksum", () => {
  assert.ok(checksum(30000000) > 0);
});
