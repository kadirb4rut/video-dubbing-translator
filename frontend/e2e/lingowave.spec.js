import { test, expect } from 'playwright/test';
import { execFileSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';

const password = 'e2e-strong-password-123';
const gpuAcceptance = process.env.E2E_GPU_ENABLED === 'true';

function requireGpuAcceptance() {
  test.skip(!gpuAcceptance, 'Requires an authorized GPU worker and real provider model cache.');
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => fetch('/api/account', { method: 'DELETE', credentials: 'include' })).catch(() => {});
});

function fixturePath() {
  const file = path.join(os.tmpdir(), 'lingowave-playwright-fixture.wav');
  execFileSync('ffmpeg', [
    '-y', '-hide_banner', '-loglevel', 'error',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3.5',
    '-ac', '1', '-ar', '16000', file,
  ]);
  return file;
}

function videoFixturePath() {
  const file = path.join(os.tmpdir(), 'lingowave-playwright-fixture.mp4');
  execFileSync('ffmpeg', [
    '-y', '-hide_banner', '-loglevel', 'error',
    '-f', 'lavfi', '-i', 'color=c=0x28446e:s=320x180:d=1',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
    '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
    '-movflags', '+faststart', file,
  ]);
  return file;
}

async function createAccount(page) {
  const email = `e2e-${crypto.randomUUID()}@example.test`;
  await page.getByRole('button', { name: /Start creating/i }).first().click();
  await page.getByRole('button', { name: /Need an account/i }).click();
  await page.getByPlaceholder('Your name').fill('Browser Tester');
  await page.getByPlaceholder('Email address').fill(email);
  await page.getByPlaceholder(/Password · 12 characters minimum/).fill(password);
  await page.getByRole('button', { name: /Create account/i }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
  return email;
}

test('signup, upload, server inspection, and real transcription artifact flow', async ({ page }) => {
  requireGpuAcceptance();
  await page.goto('/');
  await createAccount(page);

  await page.getByRole('button', { name: 'Transcription' }).click();
  await page.locator('input[type="file"]').setInputFiles(fixturePath());
  await expect(page.getByText(/FFprobe inspected/)).toBeVisible();
  await page.getByRole('button', { name: 'Transcribe' }).click();
  await expect(page.locator('.job-status')).toBeVisible();
  await expect(page.locator('.job-status')).toContainText('completed', { timeout: 150_000 });
  await expect(page.locator('.artifact-list')).toContainText('transcript.txt');
  await expect(page.locator('.artifact-list')).toContainText('transcript.srt');
  await expect(page.locator('.artifact-list')).toContainText('transcript.vtt');
});

test('protected dashboard persists across a browser reload', async ({ page }) => {
  await page.goto('/');
  const email = await createAccount(page);
  await page.getByLabel('Log out').click();
  await page.getByRole('button', { name: /Start creating/i }).first().click();
  await page.getByPlaceholder('Email address').fill(email);
  await page.getByPlaceholder(/Password · 12 characters minimum/).fill(password);
  await page.getByRole('button', { name: /^Log in/ }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
  await page.reload();
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(page.locator('.credit-pill')).toContainText('credits');
});

test('mobile workspace keeps navigation and content within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await createAccount(page);
  await expect(page.locator('.app-shell')).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport);

  await page.getByRole('button', { name: 'Projects' }).click();
  await expect(page.locator('.project-history')).toBeVisible();
});

test('selected video controls stay outside the file-picker label', async ({ page }) => {
  await page.goto('/');
  await createAccount(page);
  await page.locator('input[type="file"]').setInputFiles(videoFixturePath());
  await expect(page.getByText(/FFprobe inspected/)).toBeVisible();

  const mediaControlIsPickerIndependent = await page.locator('video[aria-label="Selected source video"]').evaluate(video => !video.closest('label'));
  expect(mediaControlIsPickerIndependent).toBe(true);
});
