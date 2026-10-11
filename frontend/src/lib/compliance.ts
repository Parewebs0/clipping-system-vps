type Check = { rule: string; status: string; expected?: unknown; actual?: unknown; how?: string; detail?: string; platform?: string }

/** Human-readable text of a review check for the publish checklist: the rule
 * text (manual checks, prohibitions, pinned comment) or its expected value. */
export function checklistText(c: Check): string {
  const exp = c.expected
  if (typeof exp === 'string' && exp.trim()) return exp
  if (Array.isArray(exp) && exp.length > 0) return exp.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join(' · ')
  return c.how ?? ''
}

export type { Check }