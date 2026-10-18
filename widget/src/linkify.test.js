import assert from "node:assert/strict";
import test from "node:test";

import { linkifyParts } from "./linkify.js";

const links = (t) => linkifyParts(t).filter((p) => p.href);

test("links http(s) urls and leaves the rest as text", () => {
  assert.deepEqual(linkifyParts("See https://ex.com/a now"), [
    { text: "See " },
    { text: "https://ex.com/a", href: "https://ex.com/a" },
    { text: " now" },
  ]);
});

test("bare www. gets an https scheme but keeps its display text", () => {
  assert.deepEqual(links("go to www.ex.com"), [{ text: "www.ex.com", href: "https://www.ex.com" }]);
});

test("sentence punctuation stays out of the url", () => {
  assert.deepEqual(links("try https://ex.com/a."), [{ text: "https://ex.com/a", href: "https://ex.com/a" }]);
  assert.deepEqual(links("(see https://ex.com/a), ok"), [{ text: "https://ex.com/a", href: "https://ex.com/a" }]);
});

test("a paren the url opened is kept", () => {
  assert.deepEqual(links("https://ex.com/a_(b)"), [{ text: "https://ex.com/a_(b)", href: "https://ex.com/a_(b)" }]);
});

test("no scheme is ever taken from the text", () => {
  assert.deepEqual(links("javascript:alert(1) data:text/html,x"), []);
});

test("plain and empty bodies are left alone", () => {
  assert.deepEqual(linkifyParts("no links here"), [{ text: "no links here" }]);
  assert.deepEqual(linkifyParts(""), []);
  assert.deepEqual(linkifyParts(null), []);
});
