// Helpers over the diagram JSON the server sends (nodes by id, edges, Mermaid id map).
// Pure module: no DOM access, unit-tested with node --test.

export function isContainer(node, containerKinds) { return containerKinds.includes(node.kind); }

/** Ids of all nodes below `id`. */
export function descendantIds(nodes, id) {
  const out = new Set();
  const walk = (p) => { for (const n of Object.values(nodes)) if (n.parent === p && !out.has(n.id)) { out.add(n.id); walk(n.id); } };
  walk(id);
  return out;
}

/** Containers as `{id, label}` depth-first, sorted by name, the label indented two spaces per level.
 * Every node of a container kind counts (empty ones too), and so does any node that has children. */
export function containerOptions(nodes, containerKinds) {
  const byParent = {};
  for (const n of Object.values(nodes)) (byParent[n.parent ?? ""] ??= []).push(n);
  const out = [];
  const walk = (parent, depth) => {
    for (const n of (byParent[parent] ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      if (isContainer(n, containerKinds) || byParent[n.id]) { out.push({ id: n.id, label: `${"  ".repeat(depth)}${n.name}` }); walk(n.id, depth + 1); }
    }
  };
  walk("", 0);
  return out;
}

/** The configured kinds plus `current` if it is no longer configured, so a form never silently changes it. */
export function withCurrent(kinds, current) {
  return current && !kinds.includes(current) ? [...kinds, current] : kinds;
}

/** The focus one level above `focus` (null = whole diagram). */
export function parentFocus(nodes, focus) {
  return focus ? (nodes[focus]?.parent ?? null) : null;
}

/** Whether an answer that renders `answerFocus` was requested for another focus than `currentFocus`, the one the user
 * looks at now (#216): asked before a focus change, answered after it. The server shows a focus only when asked for it
 * and falls back to the whole diagram only when the asked one is not in the diagram or the type cannot focus
 * (`check_focus`), so a different answer is stale unless the current focus cannot be shown in the answer's `nodes`. */
export function staleFocus(answerFocus, currentFocus, nodes, canFocus) {
  const answer = answerFocus ?? null, current = currentFocus ?? null;
  if (answer === current) return false;
  if (answer !== null) return true;
  return Boolean(canFocus && nodes && Object.hasOwn(nodes, current));
}

/** Map the id of a rendered `g.cluster` / `g.node` back to the node id, via the server's node id -> Mermaid id map.
 * Mermaid prefixes every id with the render id ("arcviz-<seq>-"), then uses the plain id for clusters
 * and "flowchart-<id>-<n>" for nodes. Our Mermaid ids never contain "-" themselves. */
export function nodeIdForSvgId(svgId, mermaidIds) {
  const reverse = Object.fromEntries(Object.entries(mermaidIds).map(([k, v]) => [v, k]));
  let key = (svgId || "").replace(/^arcviz-\d+-/, "");
  if (key.startsWith("flowchart-")) key = key.replace(/^flowchart-/, "").replace(/-\d+$/, "");
  return reverse[key] ?? null;
}

/** Turn edited `[key, value]` rows into {properties, remove_properties} relative to `current`, or null if
 * nothing changed. Keys are trimmed; rows with an empty key are ignored. Keys in `typed` (the typed fields of the
 * diagram type, ADR-0177) are not properties for the server: their changed values are returned as `fields`
 * (`""` removes one), never in `properties` or `remove_properties`. */
export function propertyChanges(rows, current, typed = []) {
  if (typed.length) {
    const isTyped = (k) => typed.includes(k.trim());
    const plain = propertyChanges(rows.filter(([k]) => !isTyped(k)), Object.fromEntries(Object.entries(current || {}).filter(([k]) => !typed.includes(k))));
    const fields = {};
    const shown = new Set();
    for (const [rawKey, value] of rows) {
      const key = rawKey.trim();
      if (!key || !typed.includes(key)) continue;
      shown.add(key);
      if ((current?.[key] ?? "") !== value) fields[key] = value;
    }
    for (const key of Object.keys(current || {})) if (typed.includes(key) && !shown.has(key)) fields[key] = "";
    if (!Object.keys(fields).length) return plain;
    return { properties: plain?.properties || {}, remove_properties: plain?.remove_properties || [], fields };
  }
  const properties = {};
  for (const [rawKey, value] of rows) {
    const key = rawKey.trim();
    if (key) properties[key] = value;
  }
  const removed = Object.keys(current || {}).filter((k) => !(k in properties));
  const changed = removed.length || Object.entries(properties).some(([k, v]) => current?.[k] !== v);
  return changed ? { properties, remove_properties: removed } : null;
}
