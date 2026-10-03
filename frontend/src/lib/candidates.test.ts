import { describe, expect, it } from 'vitest'
import openapi from '../../openapi.json'
import { approveOutcome, canApprove, canReject, fmtTimecode, previewEmbedUrl, rejectSchema, youtubeId } from './candidates'
import { CANDIDATE_STATUS_ORDER } from './status'

describe('candidate helpers', () => {
  it('formats timecodes', () => {
    expect(fmtTimecode(0)).toBe('0:00.0')
    expect(fmtTimecode(65.44)).toBe('1:05.4')
    expect(fmtTimecode(-3)).toBe('0:00.0')
  })

  it('builds a YouTube preview limited to the window', () => {
    expect(youtubeId('https://youtu.be/abcdefghijk')).toBe('abcdefghijk')
    expect(youtubeId('https://www.youtube.com/shorts/abcdefghijk')).toBe('abcdefghijk')
    expect(youtubeId('https://drive.google.com/file/d/x/view')).toBeNull()
    expect(previewEmbedUrl('https://www.youtube.com/watch?v=abcdefghijk&t=3', 10.6, 40.2)).toBe(
      'https://www.youtube-nocookie.com/embed/abcdefghijk?start=10&end=41&rel=0',
    )
    expect(previewEmbedUrl(null, 0, 10)).toBeNull()
  })

  it('mirrors the backend approve/reject guards', () => {
    expect(canApprove({ status: 'pending', render_job_id: null })).toBe(true)
    expect(canApprove({ status: 'approved', render_job_id: null })).toBe(true)
    expect(canApprove({ status: 'approved', render_job_id: 'j' })).toBe(false)
    for (const s of ['rejected', 'rendered', 'superseded'] as const) {
      expect(canApprove({ status: s, render_job_id: null })).toBe(false)
      expect(canReject({ status: s, render_job_id: null })).toBe(false)
    }
    expect(canReject({ status: 'pending', render_job_id: null })).toBe(true)
    expect(canReject({ status: 'approved', render_job_id: 'j' })).toBe(false)
  })

  it('validates the reject reason', () => {
    expect(rejectSchema.parse({ reason: '  ' })).toEqual({ reason: undefined })
    expect(rejectSchema.parse({ reason: ' malo ' })).toEqual({ reason: 'malo' })
    expect(rejectSchema.safeParse({ reason: 'x'.repeat(501) }).success).toBe(false)
  })

  it('describes approve outcomes', () => {
    expect(approveOutcome({ candidate_id: 'c', status: 'approved', render_job_id: '12345678-aaaa', idempotent: false })).toMatchObject({
      ok: true,
      title: 'Candidato aprobado',
      description: 'Job de render 12345678 en cola del worker',
    })
    expect(approveOutcome({ candidate_id: 'c', status: 'approved', render_job_id: 'x', idempotent: true }).title).toBe('Ya estaba aprobado')
    expect(approveOutcome({ candidate_id: 'c', status: 'rejected', reason: 'duration', idempotent: false })).toMatchObject({ ok: false, description: 'duration' })
    expect(approveOutcome({ candidate_id: 'c', status: 'error', reason: 'boom', idempotent: false }).ok).toBe(false)
  })

  it('covers exactly the candidate statuses published in OpenAPI', () => {
    const fromApi = (openapi as any).components.schemas.CandidateItem.properties.status.enum as string[]
    expect([...CANDIDATE_STATUS_ORDER].sort()).toEqual([...fromApi].sort())
  })
})
