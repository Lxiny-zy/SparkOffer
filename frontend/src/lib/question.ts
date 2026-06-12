// Question id display helpers.
// Question ids are usually numbers (1, 2, ...) but the LLM can emit string ids
// like "Q2". Rendering `Q{id}` blindly would produce "QQ2", and padStart on a
// non-numeric string is meaningless — so all display sites go through here.

/**
 * Format a question id for display: "Q01", "Q12", or the id verbatim if it
 * already carries a Q prefix (e.g. "Q2"). `pad` controls zero-padding width
 * for numeric ids (0 = no padding).
 */
export function formatQuestionLabel(id: number | string, pad: number = 0): string {
  const s = String(id);
  if (/^q/i.test(s)) return s.toUpperCase();
  return `Q${pad > 0 ? s.padStart(pad, "0") : s}`;
}
