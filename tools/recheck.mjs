// Re-check items that came back with no PDF link, using a full page render
// instead of a raw HTML fetch. If PIDE renders the download button with
// JavaScript, the raw fetch would have missed it.
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const slugs = process.argv.slice(2);
const recs = fs.readFileSync(path.join(ROOT, 'data/research_index.jsonl'), 'utf8')
  .split('\n').filter(Boolean).map(JSON.parse);
const targets = slugs.length
  ? recs.filter((r) => slugs.includes(r.slug))
  : recs.filter((r) => (r.pdf_urls || []).length === 0);

const BOILERPLATE = new Set(['pide-profile.pdf','admission-guidelines-for-passing-criteria.pdf','pide-initiatives.pdf']);
const ctx = await chromium.launchPersistentContext(path.join(ROOT, '.chrome-profile'), {
  channel: 'chrome', headless: false, viewport: { width: 1280, height: 900 },
  args: ['--disable-blink-features=AutomationControlled'],
});
const page = ctx.pages()[0] || (await ctx.newPage());
await page.goto('https://pide.org.pk/', { waitUntil: 'domcontentloaded', timeout: 90000 });

console.log(`rechecking ${targets.length} items with full render`);
const found = [];
for (let i = 0; i < targets.length; i++) {
  const it = targets[i];
  try {
    await page.goto(it.link, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await new Promise((r) => setTimeout(r, 900));
    const urls = await page.evaluate(() =>
      [...document.querySelectorAll('a[href]')].map((a) => a.href)
    );
    const pdfs = [...new Set(urls
      .map((u) => u.split('?')[0].replace(/\s+/g, ''))
      .filter((u) => /\.pdf$/i.test(u))
      .filter((u) => !BOILERPLATE.has(u.split('/').pop().toLowerCase())))];
    if (pdfs.length) {
      found.push({ ...it, pdf_urls: pdfs });
      console.log(`  FOUND ${it.slug.slice(0, 55)} -> ${pdfs[0].split('/').pop()}`);
    }
  } catch (e) { console.log(`  ! ${it.slug}: ${e.message.slice(0, 60)}`); }
  if ((i + 1) % 25 === 0) console.log(`  ${i + 1}/${targets.length} (${found.length} found)`);
}
if (found.length) {
  fs.appendFileSync(path.join(ROOT, 'data/research_index.jsonl'),
    found.map((r) => JSON.stringify(r)).join('\n') + '\n');
}
console.log(`done: recovered ${found.length} PDF links`);
await ctx.close();
