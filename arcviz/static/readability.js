// Reading helpers of the two-pane editors (spec: docs/architecture/specs/decision-ui-readability.md): the snippet of a
// list card and the height of an auto-growing text area. Pure module: no DOM.

/** `text` on one line, cut at a word boundary after at most `max` characters with an ellipsis; "" for no text. */
export function snippet(text, max = 140) {
  const flat = String(text ?? "").replace(/[#>*_`]+/g, "").replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:.]+$/, "")}…`;
}

/** The height in pixels of a text area whose content is `scrollHeight` tall: the content, at least `min`, at most `max`;
 * `overflow` says whether the cap was reached (the area then scrolls). */
export function fitHeight(scrollHeight, min, max) {
  const wanted = Math.max(Number(scrollHeight) || 0, min);
  return { height: Math.min(wanted, max), overflow: wanted > max };
}
