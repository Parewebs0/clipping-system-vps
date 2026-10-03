import { describe, expect, it } from 'vitest'
import type { CampaignDetailCampaign } from '@/api/types'
import { buildPatch, campaignFormSchema, formDefaults } from './campaignForm'

const base: CampaignDetailCampaign = {
  id: 1,
  name: 'TEST',
  status: 'discovered',
  source_provider: 'manual',
  source_url: null,
  source_id: null,
  source_metadata: {},
  source_instructions: null,
  spec: { duration_min: 10, duration_max: 40, keywords: ['a'], extra: { score: 1 } },
  assets_count: 0,
  clips_approved: 0,
  clips_published: 0,
}

describe('campaignFormSchema', () => {
  it('accepts defaults built from a campaign', () => {
    expect(campaignFormSchema.safeParse(formDefaults(base)).success).toBe(true)
  })
  it('rejects min > max, bad URLs and empty name', () => {
    const d = formDefaults(base)
    const r = campaignFormSchema.safeParse({ ...d, duration_min: '50', duration_max: '10' })
    expect(r.success).toBe(false)
    expect(r.error?.issues[0].path).toEqual(['duration_max'])
    expect(campaignFormSchema.safeParse({ ...d, source_url: 'ftp://x' }).success).toBe(false)
    expect(campaignFormSchema.safeParse({ ...d, name: '  ' }).success).toBe(false)
    expect(campaignFormSchema.safeParse({ ...d, duration_min: '-1' }).success).toBe(false)
  })
  it('converts numeric strings and empty to null in the patch', () => {
    const v = campaignFormSchema.parse({ ...formDefaults(base), duration_min: '12', duration_max: '' })
    expect(buildPatch(base, v).spec).toEqual({ duration_min: 12, duration_max: null })
    expect(campaignFormSchema.safeParse({ ...formDefaults(base), duration_min: 'abc' }).success).toBe(false)
  })
})

describe('buildPatch', () => {
  it('is empty when nothing changed', () => {
    expect(buildPatch(base, campaignFormSchema.parse(formDefaults(base)))).toEqual({})
  })
  it('sends only changed fields and never spec.extra', () => {
    const v = campaignFormSchema.parse({ ...formDefaults(base), name: 'TEST 2', duration_max: '60', keywords: 'a, b', source_url: 'https://x.y' })
    expect(buildPatch(base, v)).toEqual({ name: 'TEST 2', source_url: 'https://x.y', spec: { duration_max: 60, keywords: ['a', 'b'] } })
  })
  it('does not send source_url/source_id for whop campaigns', () => {
    const whop = { ...base, source_provider: 'whop' as const, source_url: 'https://whop.com/a' }
    const v = campaignFormSchema.parse({ ...formDefaults(whop), source_url: 'https://whop.com/b' })
    expect(buildPatch(whop, v).source_url).toBeUndefined()
  })
})
