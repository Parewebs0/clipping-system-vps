import { expect, test } from '@playwright/test'

const campaign = {
  campaign: {
    id: 18,
    name: 'Boxabl',
    status: 'needs_review',
    source_provider: 'whop',
    source_metadata: {},
    spec: {},
    assets_count: 0,
    clips_approved: 0,
    clips_published: 0,
    created_at: '2026-10-03T00:00:00Z',
    updated_at: '2026-10-03T00:00:00Z',
  },
  assets: [],
  active_jobs: [],
  clips: [],
  llm_usage: { calls: 0, errors: 0, total_tokens: 0, cost_usd: 0, by_stage: [] },
  worker_file_base_url: null,
}

const rules = {
  campaign_id: 18,
  campaign_name: 'Boxabl',
  status: 'needs_review',
  source_provider: 'whop',
  spec: {},
  spec_is_empty: true,
  rules: {},
  card_text: '',
  discovered: {},
  asset_links_brief: [],
  asset_links_raw: [],
  asset_links_count: 0,
  drive_ids: [],
  priority_components: {},
}

test('confirma requisitos humanos y dispensa con nota contra un API mock', async ({ page }) => {
  const blockers = [
    {
      key: 'human:pre_approval',
      kind: 'human',
      rule: 'pre_approval',
      text: 'Pre-aprobación del cliente',
      confirmed: false,
      evidence: [],
    },
    {
      key: 'unsupported:frame',
      kind: 'unsupported',
      rule: 'unsupported',
      text: 'Host no soportado',
      reason: 'frame.io',
      confirmed: false,
      evidence: [],
    },
  ]
  let ruleset = {
    campaign_id: 18,
    status: 'needs_review',
    ruleset: { version: 2, coverage: {} },
    blockers,
    pending_count: 2,
    gate: null,
    compliance: null,
    logo_url: null,
  }

  await page.addInitScript(() => sessionStorage.setItem('mc_token', 'e2e-token'))
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (route.request().resourceType() !== 'fetch' && route.request().resourceType() !== 'xhr') {
      await route.continue()
      return
    }
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/mission-control/campaigns/18' && route.request().method() === 'GET') return json(campaign)
    if (path === '/mission-control/campaigns/18/rules') return json(rules)
    if (path === '/mission-control/pipeline/18') return json({ stages: [] })
    if (path === '/campaigns/status-machine') return json({ statuses: [], transitions: {} })
    if (path === '/campaigns/18/ruleset' && route.request().method() === 'GET') return json(ruleset)
    if (path === '/campaigns/18/rules/confirm' && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { keys: string[]; note?: string | null; waive_unsupported?: boolean }
      const blockers = ruleset.blockers.map((b) =>
        body.keys.includes(b.key)
          ? { ...b, confirmed: true, confirmation: { by: 'e2e', at: '2026-10-03T12:00:00Z', type: body.waive_unsupported ? 'waiver' : 'confirmation', note: body.note } }
          : b,
      )
      ruleset = { ...ruleset, blockers, pending_count: blockers.filter((b) => !b.confirmed).length }
      return json(ruleset)
    }
    return json({})
  })

  await page.goto('/mission-control/#/campaigns/18')
  await page.getByRole('tab', { name: 'Reglas y score' }).click()
  await expect(page.getByText('Trabajabilidad (gate de reglas)')).toBeVisible()

  const dispense = page.getByRole('button', { name: /Dispensar no soportadas/ })
  await page.getByLabel('confirmar unsupported:frame').check()
  await expect(dispense).toBeDisabled()
  await page.getByPlaceholder(/Nota/).fill('se cumple por contrato')
  await expect(dispense).toBeEnabled()

  const waive = page.waitForRequest((r) => r.url().includes('/rules/confirm') && r.method() === 'POST')
  await dispense.click()
  const waiveReq = await waive
  const waiveBody = waiveReq.postDataJSON()
  expect(waiveBody.waive_unsupported).toBe(true)
  expect(waiveBody.keys).toEqual(['unsupported:frame'])
  expect(waiveBody.note).toBe('se cumple por contrato')
  await expect(page.getByText('dispensada')).toBeVisible()

  await page.getByLabel('confirmar human:pre_approval').check()
  const confirm = page.waitForRequest((r) => r.url().includes('/rules/confirm') && r.method() === 'POST')
  await page.getByRole('button', { name: /Confirmar seleccionados/ }).click()
  const confirmReq = await confirm
  const confirmBody = confirmReq.postDataJSON()
  expect(confirmBody.waive_unsupported).toBe(false)
  expect(confirmBody.keys).toEqual(['human:pre_approval'])
  await expect(page.getByText('confirmado', { exact: true })).toBeVisible()
  await expect(page.getByText('Reglas confirmadas')).toBeVisible()
})
