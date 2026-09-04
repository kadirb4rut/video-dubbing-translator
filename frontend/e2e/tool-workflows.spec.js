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
  const file = path.join(os.tmpdir(), 'lingowave-playwright-tool-fixture.wav');
  execFileSync('ffmpeg', [
    '-y', '-hide_banner', '-loglevel', 'error',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3.5',
    '-ac', '1', '-ar', '16000', file,
  ]);
  return file;
}

async function createAccount(page) {
  const email = `tools-${crypto.randomUUID()}@example.test`;
  await page.getByRole('button', { name: /Start creating/i }).first().click();
  await page.getByRole('button', { name: /Need an account/i }).click();
  await page.getByPlaceholder('Your name').fill('Tool Tester');
  await page.getByPlaceholder('Email address').fill(email);
  await page.getByPlaceholder(/Password · 12 characters minimum/).fill(password);
  await page.getByRole('button', { name: /Create account/i }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}

async function uploadAudio(page) {
  await page.locator('input[type="file"]').setInputFiles(fixturePath());
  await expect(page.getByText(/FFprobe inspected/)).toBeVisible();
}

async function waitForCompletion(page, artifactName) {
  await expect(page.locator('.job-status')).toBeVisible();
  await expect(page.locator('.job-status')).toContainText('completed', { timeout: 180_000 });
  await expect(page.locator('.artifact-list')).toContainText(artifactName);
}

test('stem splitter produces downloadable two/four stem artifacts', async ({ page }) => {
  requireGpuAcceptance();
  await page.goto('/');
  await createAccount(page);
  await page.getByRole('button', { name: 'Stem Splitter' }).click();
  await uploadAudio(page);
  await page.getByRole('button', { name: 'Split stems' }).click();
  await waitForCompletion(page, 'stems.zip');
  await expect(page.locator('.artifact-list')).toContainText('vocals.wav');
  await expect(page.locator('.artifact-list')).toContainText('drums.wav');
  await expect(page.locator('.artifact-list')).toContainText('bass.wav');
  await expect(page.locator('.artifact-list')).toContainText('other.wav');
});

test('noise remover produces an enhanced preview artifact', async ({ page }) => {
  requireGpuAcceptance();
  await page.goto('/');
  await createAccount(page);
  await page.getByRole('button', { name: 'Noise Remover' }).click();
  await uploadAudio(page);
  await page.getByRole('button', { name: 'Remove noise' }).click();
  await waitForCompletion(page, 'enhanced.wav');
  await expect(page.locator('.noise-comparison')).toBeVisible();
});

test('voice studio stores consent, synthesizes speech, and deletes the reference', async ({ page }) => {
  requireGpuAcceptance();
  await page.goto('/');
  await createAccount(page);
  await page.getByRole('button', { name: 'Voice Studio' }).click();
  await page.getByPlaceholder('Voice name').fill('Browser consented voice');
  await page.getByPlaceholder('I own or am authorized to use this voice.').fill('I own or am authorized to use this voice.');
  await page.getByLabel('I confirm authorization to use this voice.').check();
  await page.locator('input[type="file"]').setInputFiles(fixturePath());
  await page.getByRole('button', { name: /Store reference securely/i }).click();
  await expect(page.getByText('Browser consented voice')).toBeVisible();
  await page.getByPlaceholder('Enter text to synthesize').fill('This is a real browser voice test.');
  await page.getByRole('button', { name: /Generate with Chatterbox/i }).click();
  await waitForCompletion(page, 'speech.wav');
  await page.getByRole('button', { name: 'Delete reference' }).click();
  await expect(page.getByText('Browser consented voice')).toHaveCount(0);
});

test('completed jobs persist in history and credits update', async ({ page }) => {
  requireGpuAcceptance();
  await page.goto('/');
  await createAccount(page);
  await page.getByRole('button', { name: 'Transcription' }).click();
  await uploadAudio(page);
  await page.getByRole('button', { name: 'Transcribe' }).click();
  await waitForCompletion(page, 'transcript.txt');
  await expect(page.locator('.credit-pill')).toContainText('29 credits');
  await page.getByRole('button', { name: 'Projects' }).click();
  await expect(page.locator('.project-history')).toContainText('transcription');
  await page.getByRole('button', { name: 'Open' }).click();
  await expect(page.locator('.history-detail')).toContainText('transcript.txt');
});
