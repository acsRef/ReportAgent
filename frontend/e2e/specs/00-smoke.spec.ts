import { test, expect } from '@playwright/test'
import { FRONTEND_URL } from '../helpers/env'

test('homepage loads and redirects to /login when unauthenticated', async ({ page }) => {
  await page.goto(`${FRONTEND_URL}/`)
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('button', { name: '进入工作台' }).first()).toBeVisible()
})