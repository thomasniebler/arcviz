// The product vision of a project (spec: docs/architecture/specs/project-vision.md, ADR-0203): the pure helpers of the
// editor on the Architektur page. Goals and non-goals are one entry per line in a textarea.

export const MAX_STATEMENT = 2000;
export const MAX_ENTRIES = 10;
export const MAX_ENTRY = 200;

/** The entries of a textarea: one per non-blank line, trimmed. */
export function parseLines(text) {
  return String(text ?? "").split("\n").map((line) => line.trim()).filter(Boolean);
}

/** The vision as the three form values (`{statement, goals, non_goals}`; textareas hold one entry per line). */
export function formValues(vision) {
  return { statement: vision?.statement ?? "", goals: (vision?.goals ?? []).join("\n"), non_goals: (vision?.non_goals ?? []).join("\n") };
}

/** The request body for the three form values. */
export function bodyFromForm({ statement, goals, non_goals }) {
  return { statement: String(statement ?? "").trim(), goals: parseLines(goals), non_goals: parseLines(non_goals) };
}

/** What is wrong with a request body (bounds of the server), as German-free codes the panel translates; [] when fine. */
export function problems(body) {
  const found = [];
  if (body.statement.length > MAX_STATEMENT) found.push("statement");
  if (body.goals.length > MAX_ENTRIES || body.non_goals.length > MAX_ENTRIES) found.push("count");
  if ([...body.goals, ...body.non_goals].some((e) => e.length > MAX_ENTRY)) found.push("length");
  return found;
}

/** Whether the form differs from the stored vision. */
export function changed(vision, values) {
  return JSON.stringify(bodyFromForm(formValues(vision))) !== JSON.stringify(bodyFromForm(values));
}
