import { test, expect } from '@playwright/test'
import path from 'path'

test('upload modal: create scan → drop 2 files → finalize', async ({ page }) => {
  // Stub S3 PUT (matches presigned URL host)
  await page.route(/\/qpress-uploads.*/, (route) => {
    if (route.request().method() === 'PUT') {
      return route.fulfill({ status: 200, body: '' })
    }
    return route.continue()
  })

  await page.goto('/projects/local/compute')

  // Open modal
  await page.getByTestId('compute-tab-new-scan').click()
  await expect(page.getByTestId('upload-modal')).toBeVisible()

  // Fill metadata
  await page.getByTestId('scan-form-name').fill('e2e-scan-1')
  await page.getByTestId('material-combobox-input').click()
  await page.getByTestId('material-combobox-option-graphene').click()
  await page.getByTestId('scan-form-image-count').fill('2')
  await page.getByTestId('scan-form-submit').click()

  // Drop 2 files via the hidden input
  const fixturesDir = path.join(__dirname, 'fixtures')
  await page.getByTestId('file-dropzone-input').setInputFiles([
    path.join(fixturesDir, 'tile_0_0.tif'),
    path.join(fixturesDir, 'tile_0_1.tif'),
  ])

  await page.getByTestId('upload-modal-start').click()

  // Wait for both rows to reach 'done'
  const rows = page.getByTestId(/file-row-.*-status/)
  await expect(rows.first()).toContainText('done', { timeout: 30_000 })
  await expect(rows.last()).toContainText('done', { timeout: 30_000 })

  await page.getByTestId('upload-modal-finalize').click()
  await expect(page.getByText(/finalized/i)).toBeVisible({ timeout: 10_000 })
})
