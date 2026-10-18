const URL_RE = /\b(?:https?:\/\/|www\.)[^\s<]+/gi;

// Trailing punctuation usually ends the sentence, not the URL. A ")" belongs to
// the URL only if the match opened a "(" of its own.
function trimTrailing(url) {
  while (url.length > 1) {
    const tail = url[url.length - 1];
    if (".,;:!?".includes(tail)) url = url.slice(0, -1);
    else if (tail === ")" && !url.includes("(")) url = url.slice(0, -1);
    else break;
  }
  return url;
}

// Splits plain text into literal runs and links: [{text}, {text, href}, ...].
// Pure, so it is the part worth testing; setLinkifiedText just builds the nodes.
export function linkifyParts(text) {
  const value = text || "";
  const parts = [];
  let last = 0;
  for (const match of value.matchAll(URL_RE)) {
    const url = trimTrailing(match[0]);
    if (match.index > last) parts.push({ text: value.slice(last, match.index) });
    parts.push({ text: url, href: /^www\./i.test(url) ? `https://${url}` : url });
    last = match.index + url.length;
  }
  if (last < value.length) parts.push({ text: value.slice(last) });
  return parts;
}

// Ticket and comment bodies arrive as plain text, so bare URLs in them are dead
// text unless we turn them into anchors here. Still no innerHTML: nodes are built
// with createElement/createTextNode and href is always http(s), never a scheme
// the author picked.
export function setLinkifiedText(el, text) {
  while (el.firstChild) el.removeChild(el.firstChild);
  for (const part of linkifyParts(text)) {
    if (!part.href) {
      el.appendChild(document.createTextNode(part.text));
      continue;
    }
    const a = document.createElement("a");
    a.href = part.href;
    a.textContent = part.text;
    a.target = "_blank";
    a.rel = "noopener noreferrer nofollow";
    el.appendChild(a);
  }
}
