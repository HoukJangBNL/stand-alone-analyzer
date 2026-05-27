import { test, expect, type Route, type Page } from '@playwright/test'

/**
 * Phase 4 P4.5 — Playwright e2e for the pipeline UX flow (mocked SSE).
 *
 * Scope (per the P4.5 plan task in
 * docs/superpowers/plans/2026-05-25-segmentation-web-integration.md L1846-1853):
 *   "Playwright: 업로드 → SAM 실행 → progress 패널 → 완료 확인"
 *
 * Real-GPU live-fire of the pipeline is tracked separately as #190 backlog
 * (requires AWS IAM grants). This spec validates the multi-step UX over the
 * canonical 5-event SSE vocabulary
 * (step_started / step_progress / step_completed / pipeline_done /
 * pipeline_error) without depending on a live GPU/AWS pipeline — the route
 * is intercepted with `page.route` and synthetic SSE bodies are streamed.
 *
 * Implementation references:
 *   - Endpoint:    POST /api/v1/projects/{pid}/scans/{sid}/run/pipeline (SSE)
 *   - Hook:        web/src/hooks/usePipelineProgress.ts
 *   - Form:        web/src/components/run/PipelineParamsForm.tsx
 *   - Timeline:    web/src/components/run/PipelineTimeline.tsx
 *   - Tab:         web/src/pages/ComputeTab.tsx
 *
 * Upload coverage: the upload flow is exercised end-to-end by the sibling
 * upload.spec.ts. Re-running it here would only add backend coupling without
 * additional test value, so this spec navigates straight to the compute tab
 * for a stub scan and exercises the per-pipeline-step UX over fully mocked
 * HTTP. The frontend uses fetch() with a manual SSE parser
 * (web/src/lib/sse.ts), so route.fulfill() with a text/event-stream body
 * works directly — no EventSource monkey-patching needed.
 */

const PID = 'p1'
const SID = 42

const STEPS = [
  'thumbnails',
  'background',
  'sam',
  'domain_stats',
  'domain_proximity',
] as const

interface SSELine {
  event: string
  data: unknown
}

function sseBody(lines: SSELine[]): string {
  return lines
    .map((l) => `event: ${l.event}\ndata: ${JSON.stringify(l.data)}\n\n`)
    .join('')
}

async function fulfillSSE(route: Route, lines: SSELine[]): Promise<void> {
  await route.fulfill({
    status: 200,
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
    },
    body: sseBody(lines),
  })
}

/**
 * Stub the auth-me + project list + scans list endpoints so the SPA's auth
 * gate, sidebar, and scan picker render without a backend. Anything not
 * essential to the compute-tab pipeline UI is returned as the smallest
 * valid envelope.
 */
async function mockAuthAndShell(page: Page): Promise<void> {
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'u-e2e',
        email: 'e2e@example.com',
        role: 'admin',
        email_verified: true,
      }),
    })
  )

  await page.route('**/api/v1/projects', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          projects: [
            {
              project_id: PID,
              name: 'E2E project',
              description: null,
              created_at: '2026-05-27T00:00:00Z',
              scan_count: 1,
            },
          ],
        }),
      })
    }
    return route.continue()
  })

  await page.route(`**/api/v1/projects/${PID}/scans`, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scans: [
            {
              scan_id: SID,
              name: 'e2e-scan',
              material: 'graphene',
              image_count: 2,
              uploaded_count: 2,
              status: 'ready',
              created_at: '2026-05-27T00:00:00Z',
            },
          ],
        }),
      })
    }
    return route.continue()
  })
}

/**
 * Navigate to a guarded SPA route after the auth slice has hydrated.
 *
 * A naive `page.goto(targetUrl)` would trigger a fresh page load, the auth
 * slice would re-initialise to `currentUser: null`, RequireAuth would
 * redirect to /login, and LoginPage would later replace the URL with "/" —
 * dropping the original target. Instead we land on the route table at
 * /login first; once auth/me resolves, LoginPage navigates to "/" and the
 * sidebar shows the user's email (our load-bearing readiness signal). With
 * the auth slice live, an in-SPA history.pushState + popstate transitions
 * React Router to the target route without remounting the app shell.
 *
 * Why click the scan row instead of pushState alone? React Router v6's
 * BrowserRouter does respond to popstate, but useParams in deeply nested
 * routes can be flaky to update via raw popstate in dev. Clicking the
 * actual scan row in the ScanTable is the canonical user gesture and uses
 * react-router-dom's `navigate()` internally — guaranteed to update
 * useParams. The scan row is rendered on /projects/{pid} (and on every
 * tab route), so we land there first, then click into the desired tab.
 */
async function gotoScanCompute(page: Page): Promise<void> {
  await page.goto('/login')
  await expect(page.getByText('e2e@example.com')).toBeVisible({
    timeout: 10_000,
  })

  // Push to /projects/{pid} via the sidebar — it's a real <button> using
  // react-router's navigate(), so useParams will pick up :projectId.
  await page.getByTestId(`sidebar-project-select-${PID}`).click()

  // ScanTable renders rows clickable, each onClick wires up to navigate(
  // `/projects/${pid}/scans/${sid}/${tabSlug}`). Default tabSlug is
  // 'compute' (ScanTable.tsx). One click takes us to the compute tab for
  // the stub scan — useParams sees scanId, ComputeTab renders the pipeline
  // form + timeline + SAM panel.
  await page.getByTestId(`scan-table-row-${SID}`).click()
  await expect(page).toHaveURL(
    new RegExp(`/projects/${PID}/scans/${SID}/compute$`)
  )
  await expect(page.getByTestId('pipeline-form')).toBeVisible()
  await expect(page.getByTestId('pipeline-timeline')).toBeVisible()
}

test.describe('compute pipeline UX (mocked SSE)', () => {
  test('happy path: full 5-step pipeline transitions to done', async ({
    page,
  }) => {
    await mockAuthAndShell(page)

    // Mock the unified pipeline SSE stream. Vocabulary is the 5-event set
    // documented in src/flake_analysis/api/sse.py::PipelineProgressBridge
    // and consumed by web/src/hooks/usePipelineProgress.ts:
    //   step_started → step_progress → step_completed → ... → pipeline_done
    //
    // The hook also expects the parsed JSON payloads to carry their own
    // `type` field (it discriminates on payload.type, not the SSE `event:`
    // line). We populate both to match what the backend bridge emits.
    await page.route(
      new RegExp(`/api/v1/projects/${PID}/scans/${SID}/run/pipeline$`),
      (route) => {
        const lines: SSELine[] = []
        for (let i = 0; i < STEPS.length; i++) {
          const step = STEPS[i]
          lines.push({
            event: 'step_started',
            data: { type: 'step_started', step, index: i, total: STEPS.length },
          })
          lines.push({
            event: 'step_progress',
            data: {
              type: 'step_progress',
              step,
              pct: 0.5,
              msg: `${step} halfway`,
            },
          })
          lines.push({
            event: 'step_completed',
            data: {
              type: 'step_completed',
              step,
              result: { step },
            },
          })
        }
        lines.push({
          event: 'pipeline_done',
          data: {
            type: 'pipeline_done',
            cascade: { reran: STEPS },
          },
        })
        return fulfillSSE(route, lines)
      }
    )

    await gotoScanCompute(page)

    // Click the Run pipeline button on PipelineParamsForm (P5.3). The
    // default form values include a non-empty SAM weights_path, so the
    // button is enabled. No background param edits = no cascade dialog.
    await page.getByTestId('pipeline-form-run').click()

    // After pipeline_done, every timeline row's status badge transitions to
    // 'done' (data-status="done", glyph "✓"). The hook reduces all
    // step_completed events into the steps map before processing the
    // terminal pipeline_done event.
    for (const step of STEPS) {
      const slug = step.replace(/_/g, '-')
      const status = page.getByTestId(`pipeline-timeline-row-${slug}-status`)
      await expect(status).toHaveAttribute('data-status', 'done', {
        timeout: 10_000,
      })
      await expect(status).toContainText('✓')
    }

    // Run button re-enables when the pipeline reaches a terminal phase
    // (isRunning is bound to phase === 'running'); seeing it interactive
    // again is the cleanest UX-level signal that the pipeline finished.
    await expect(page.getByTestId('pipeline-form-run')).toBeEnabled()
  })

  test('error path: pipeline_error after SAM start surfaces ✗ on the failing step', async ({
    page,
  }) => {
    await mockAuthAndShell(page)

    // Sequence: thumbnails done → background done → sam started → SAM
    // emits pipeline_error. The error row should flip to data-status="error"
    // with a glyph "✗" and the envelope message, the row's message slot
    // should render the envelope's message text, and the global form
    // re-enables once the terminal event is consumed.
    await page.route(
      new RegExp(`/api/v1/projects/${PID}/scans/${SID}/run/pipeline$`),
      (route) =>
        fulfillSSE(route, [
          {
            event: 'step_started',
            data: { type: 'step_started', step: 'thumbnails', index: 0, total: 5 },
          },
          {
            event: 'step_completed',
            data: { type: 'step_completed', step: 'thumbnails', result: {} },
          },
          {
            event: 'step_started',
            data: { type: 'step_started', step: 'background', index: 1, total: 5 },
          },
          {
            event: 'step_completed',
            data: { type: 'step_completed', step: 'background', result: {} },
          },
          {
            event: 'step_started',
            data: { type: 'step_started', step: 'sam', index: 2, total: 5 },
          },
          {
            event: 'pipeline_error',
            data: {
              type: 'pipeline_error',
              step: 'sam',
              error: {
                code: 'pipeline_failed',
                message: 'SAM weights load failed (mocked)',
                details: { exc_type: 'RuntimeError' },
                request_id: 'req-e2e-error',
              },
            },
          },
        ])
    )

    await gotoScanCompute(page)
    await page.getByTestId('pipeline-form-run').click()

    // The earlier two steps reached step_completed.
    await expect(
      page.getByTestId('pipeline-timeline-row-thumbnails-status')
    ).toHaveAttribute('data-status', 'done', { timeout: 10_000 })
    await expect(
      page.getByTestId('pipeline-timeline-row-background-status')
    ).toHaveAttribute('data-status', 'done', { timeout: 10_000 })

    // SAM is the failing step.
    const samStatus = page.getByTestId('pipeline-timeline-row-sam-status')
    await expect(samStatus).toHaveAttribute('data-status', 'error', {
      timeout: 10_000,
    })
    await expect(samStatus).toContainText('✗')
    await expect(
      page.getByTestId('pipeline-timeline-row-sam-msg')
    ).toContainText('SAM weights load failed (mocked)')

    // The two never-started steps remain idle.
    for (const step of ['domain_stats', 'domain_proximity'] as const) {
      const slug = step.replace(/_/g, '-')
      await expect(
        page.getByTestId(`pipeline-timeline-row-${slug}-status`)
      ).toHaveAttribute('data-status', 'idle')
    }

    // Form re-enables on terminal phase (isRunning false again).
    await expect(page.getByTestId('pipeline-form-run')).toBeEnabled()
  })
})
