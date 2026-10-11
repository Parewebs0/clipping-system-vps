import { describe, expect, it } from 'vitest'
import { checklistText } from './compliance'

describe('checklistText', () => {
  it('shows the rule text for manual checks and prohibitions', () => {
    expect(checklistText({ rule: 'manual_check', status: 'review', expected: 'Vary your clips — don\'t repost the same moment repeatedly.' })).toBe(
      'Vary your clips — don\'t repost the same moment repeatedly.',
    )
    expect(checklistText({ rule: 'prohibitions.topic', status: 'review', expected: 'story boosting' })).toBe('story boosting')
  })

  it('joins allowed edits and falls back to the how text for numeric rules', () => {
    expect(checklistText({ rule: 'edit', status: 'review', expected: ['cuts', 'b-roll', 'overlays'] })).toBe('cuts · b-roll · overlays')
    expect(checklistText({ rule: 'hook', status: 'review', expected: 2.0, how: 'humano al aprobar el publish' })).toBe('humano al aprobar el publish')
    expect(checklistText({ rule: 'hook', status: 'review', expected: null, how: undefined })).toBe('')
  })
})