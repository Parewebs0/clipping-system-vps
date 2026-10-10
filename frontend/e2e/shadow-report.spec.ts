import { expect, test } from '@playwright/test'

const list = {
  count: 1,
  items: [
    {
      id: 'run',
      filename: 'run.jsonl',
      generated_at: '2026-10-10T09:00:00+00:00',
      n_assets: 2,
      match_pct: 75.0,
      mean_iou: 0.75,
      cost_usd_total: 0.03,
      latency_ms_total: 49,
      n_would_request_approval: 1,
    },
  ],
}

const detail = {
  ...list.items[0],
  assets: [
    {
      asset_id: '00000001-4a7e-41d0-8000-000000000001',
      campaign_id: 101,
      label: 'forge-speech',
      generated_at: '2026-10-10T09:00:00+00:00',
      current: {
        n_clips: 2,
        windows: [[0, 22], [22, 48]],
        titles: ['Watch this ForgeGUI trick'],
        captions: ['cap a'],
        cost_usd: 0.01,
        latency_ms: 12,
      },
      graph: {
        n_clips: 2,
        windows: [[0, 22], [22, 48]],
        titles: ['Watch this ForgeGUI trick'],
        captions: ['cap a'],
        cost_usd: 0.02,
        latency_ms: 20,
        decision: 'clips',
        decision_reason: 'elegidos 2',
        confidence: 0.8,
        would_request_approval: false,
        asked_more_context: false,
        context_iteration: 0,
        graph_path: ['load_context', 'extract_rules', 'propose_clips', 'score_clips', 'decide', 'human_approval', 'report'],
      },
      diff: { iou: 1, title_similarity: 1, n_clips_delta: 0 },
    },
    {
      asset_id: '00000003-4a7e-41d0-8000-000000000003',
      campaign_id: 102,
      label: 'casita-banned-term',
      generated_at: '2026-10-10T09:00:01+00:00',
      current: {
        n_clips: 2,
        windows: [[0, 14], [14, 28]],
        titles: ['Welcome back', 'The fold-out house'],
        captions: ['old', 'house'],
        cost_usd: 0,
        latency_ms: 8,
      },
      graph: {
        n_clips: 1,
        windows: [[14, 28]],
        titles: ['The fold-out house'],
        captions: ['house'],
        cost_usd: 0,
        latency_ms: 9,
        decision: 'clips',
        decision_reason: 'elegidos 1',
        confidence: 0.4,
        would_request_approval: true,
        approval_note: 'habría pedido aprobación',
        approval_reason: 'habría pedido aprobación · stake $80.00 · umbral $50.00',
        stake_usd: 80,
        asked_more_context: true,
        context_iteration: 1,
        graph_path: [
          'load_context',
          'extract_rules',
          'propose_clips',
          'score_clips',
          'need_more_context',
          'score_clips',
          'decide',
          'human_approval',
          'report',
        ],
      },
      diff: { iou: 0.5, title_similarity: 0.11, n_clips_delta: -1 },
    },
  ],
}

test('lista y detalle de informes de sombra (solo lectura)', async ({ page }) => {
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
    if (path === '/mission-control/shadow/runs' && route.request().method() === 'GET') return json(list)
    if (path === '/mission-control/shadow/runs/run' && route.request().method() === 'GET') return json(detail)
    return json({})
  })

  await page.goto('/mission-control/#/shadow')
  await expect(page.getByRole('heading', { name: 'Clip-decider LangGraph' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Sombra' })).toBeVisible()
  await expect(page.getByText('75.0%')).toBeVisible()
  await expect(page.getByText('49 ms')).toBeVisible()

  await page.getByRole('link', { name: 'run', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'run' })).toBeVisible()
  await expect(page.getByText('Decider actual').first()).toBeVisible()
  await expect(page.getByText('LangGraph').first()).toBeVisible()
  await expect(page.getByText('casita-banned-term')).toBeVisible()
  await expect(page.getByText('pidió más contexto')).toBeVisible()
  await expect(page.getByText('habría pedido aprobación').first()).toBeVisible()
  await expect(page.getByText('need_more_context')).toBeVisible()
  await expect(page.getByText('IoU 0.50')).toBeVisible()
})
