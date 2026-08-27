// Stage 1 - Harvest the PIDE research index using a real, headed Chrome.
//
// pide.org.pk sits behind a Cloudflare WAF rule (Error 1020) that blocks curl,
// python-requests and headless Chrome alike. A real headed browser passes, so we
// drive the installed Google Chrome and issue every request as a same-origin
// fetch() from inside a live pide.org.pk page.
//
// Output: data/research_index.jsonl, one JSON object per publication, appended
// as we go so an interrupted run resumes instead of starting over.

import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA = path.join(ROOT, 'data');
const OUT = path.join(DATA, 'research_index.jsonl');
const TAX_OUT = path.join(DATA, 'taxonomies.json');
const PROFILE = path.join(ROOT, '.chrome-profile');

const SINCE = '2022-01-01';
const ORIGIN = 'https://pide.org.pk';
const DELAY_MS = 1000;          // ~1 request/sec against pide.org.pk
const MAX_CONSECUTIVE_403 = 3;  // back off rather than hammer the WAF
// Each detail fetch spends ~2.3s waiting on the network. Running a few in
// flight overlaps that wait; the global rate limiter below still caps us at
// one request per DELAY_MS overall, so the site sees the same 1 req/sec.
const DETAIL_CONCURRENCY = 3;

// PDFs linked from the site-wide header/footer on every single page.
const BOILERPLATE = new Set([
  'pide-profile.pdf',
  'admission-guidelines-for-passing-criteria.pdf',
  'pide-initiatives.pdf',
]);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

fs.mkdirSync(DATA, { recursive: true });

// ---------------------------------------------------------------- in-page fetch

// Runs inside the browser: same-origin fetch, so it carries the real session.
const IN_PAGE_FETCH = async (url) => {
  try {
    const res = await fetch(url, { credentials: 'include' });
    const body = await res.text();
    return {
      status: res.status,
      totalPages: Number(res.headers.get('x-wp-totalpages') || 0),
      total: Number(res.headers.get('x-wp-total') || 0),
      body,
    };
  } catch (e) {
    return { status: 0, error: String(e), body: '' };
  }
};

class Blocked extends Error {}

// Global rate limiter: hands out one slot per DELAY_MS no matter how many
// callers are in flight, so concurrency overlaps latency without raising the
// request rate the site actually sees.
let nextSlot = 0;
async function takeSlot() {
  const now = Date.now();
  const slot = Math.max(now, nextSlot);
  nextSlot = slot + DELAY_MS;
  const wait = slot - now;
  if (wait > 0) await sleep(wait);
}

function makeFetcher(page) {
  let consecutive403 = 0;
  return async function get(url) {
    await takeSlot();
    const r = await page.evaluate(IN_PAGE_FETCH, url);
    const blocked =
      r.status === 403 || (r.body || '').includes('Attention Required');
    if (blocked) {
      consecutive403 += 1;
      if (consecutive403 >= MAX_CONSECUTIVE_403) {
        throw new Blocked(
          `Cloudflare blocked ${consecutive403} requests in a row (last: ${url}). ` +
            `Stopping. Re-run to resume, or use the DevTools-console fallback.`
        );
      }
      log(`  ! blocked (${consecutive403}/${MAX_CONSECUTIVE_403}), backing off 20s`);
      await sleep(20000);
      return get(url);
    }
    consecutive403 = 0;
    return r;
  };
}

// ---------------------------------------------------------------- taxonomies

async function fetchTaxonomy(get, name) {
  const map = {};
  let page = 1;
  let totalPages = 1;
  do {
    const url = `${ORIGIN}/wp-json/wp/v2/${name}?per_page=100&page=${page}&_fields=id,name`;
    const r = await get(url);
    if (r.status !== 200) {
      log(`  ! ${name} page ${page} -> HTTP ${r.status}, stopping this taxonomy`);
      break;
    }
    let terms;
    try {
      terms = JSON.parse(r.body);
    } catch {
      log(`  ! ${name} page ${page} -> unparseable, stopping`);
      break;
    }
    if (!Array.isArray(terms) || terms.length === 0) break;
    for (const t of terms) map[t.id] = t.name;
    totalPages = r.totalPages || 1;
    page += 1;
  } while (page <= totalPages);
  log(`  ${name}: ${Object.keys(map).length} terms`);
  return map;
}

// ---------------------------------------------------------------- PDF extraction

function extractPdfs(html) {
  const found = new Set();
  const re = /href\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    // Hrefs carry HTML entities and sometimes stray newlines inside the URL.
    let url = m[1]
      .replace(/&amp;/g, '&')
      .replace(/&#0?39;/g, "'")
      .replace(/&quot;/g, '"')
      .replace(/\s+/g, '')
      .trim();
    if (url.startsWith('//')) url = 'https:' + url;
    else if (url.startsWith('/')) url = ORIGIN + url;
    if (!/^https?:\/\//i.test(url)) continue;
    const base = url.split('?')[0].split('/').pop().toLowerCase();
    if (BOILERPLATE.has(base)) continue;
    found.add(url.split('?')[0]); // drop tracking query strings
  }
  return [...found];
}

// ---------------------------------------------------------------- main

async function main() {
  const done = new Set();
  if (fs.existsSync(OUT)) {
    for (const line of fs.readFileSync(OUT, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      try {
        done.add(JSON.parse(line).slug);
      } catch {}
    }
    log(`resuming: ${done.size} items already harvested`);
  }

  log('launching Chrome (a window will open - leave it alone while it works)');
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: 'chrome',
    headless: false,
    viewport: { width: 1280, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());

  try {
    log('opening pide.org.pk to establish a session...');
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded', timeout: 90000 });
    await sleep(3000);

    const title = await page.title();
    if (/Attention Required/i.test(title)) {
      throw new Blocked(
        'Cloudflare blocked the very first page load. If a challenge or captcha is ' +
          'showing in the window, solve it by hand and re-run - the profile persists.'
      );
    }
    log(`session ok (page title: "${title}")`);

    const get = makeFetcher(page);

    // ---- taxonomies
    log('fetching taxonomies...');
    const taxNames = [
      'research-year',
      'research-category',
      'research-sub-category',
      'research-author',
      'research-keywords',
      'research-jel',
    ];
    const tax = {};
    for (const n of taxNames) tax[n] = await fetchTaxonomy(get, n);
    fs.writeFileSync(TAX_OUT, JSON.stringify(tax, null, 2));

    // ---- item list from the REST API
    //
    // Two definitions of "2022 onwards" exist and they disagree slightly:
    // the WP publish date, and the research-year taxonomy (the real publication
    // year). We take the union of both so nothing is missed either way.
    const FIELDS =
      'id,slug,link,date,title,content,research-year,research-category,' +
      'research-sub-category,research-author,research-keywords,research-jel';

    const byId = new Map();

    async function drain(label, buildUrl) {
      let p = 1;
      let totalPages = 1;
      let added = 0;
      do {
        const r = await get(buildUrl(p));
        if (r.status !== 200) {
          log(`  ! ${label} page ${p} -> HTTP ${r.status}, stopping`);
          break;
        }
        let batch;
        try {
          batch = JSON.parse(r.body);
        } catch {
          log(`  ! ${label} page ${p} -> unparseable, stopping`);
          break;
        }
        if (!Array.isArray(batch) || batch.length === 0) break;
        for (const it of batch) {
          if (!byId.has(it.id)) {
            byId.set(it.id, it);
            added += 1;
          }
        }
        totalPages = r.totalPages || 1;
        p += 1;
      } while (p <= totalPages);
      log(`  ${label}: +${added} new (running total ${byId.size})`);
    }

    log('fetching research list...');

    // (a) everything posted since the cutoff
    await drain(
      `posted >= ${SINCE}`,
      (p) =>
        `${ORIGIN}/wp-json/wp/v2/research?per_page=100&page=${p}` +
        `&after=${SINCE}T00:00:00&orderby=date&order=desc&_fields=${FIELDS}`
    );

    // (b) everything tagged with a publication year of 2022..2026
    const yearMap = tax['research-year'] || {};
    const wantedYears = Object.entries(yearMap)
      .filter(([, name]) => Number(name) >= 2022 && Number(name) <= 2026)
      .sort((a, b) => Number(a[1]) - Number(b[1]));
    for (const [termId, name] of wantedYears) {
      await drain(
        `year ${name}`,
        (p) =>
          `${ORIGIN}/wp-json/wp/v2/research?per_page=100&page=${p}` +
          `&research-year=${termId}&orderby=date&order=desc&_fields=${FIELDS}`
      );
    }

    const items = [...byId.values()];
    log(`${items.length} distinct items in scope`);
    fs.writeFileSync(path.join(DATA, 'items_raw_count.txt'), `${items.length}\n`);

    // ---- detail pages for the PDF links
    const todo = items.filter((it) => !done.has(it.slug));
    log(`visiting ${todo.length} detail pages for PDF links (${done.size} skipped)`);

    const out = fs.createWriteStream(OUT, { flags: 'a' });
    let n = 0;
    let noPdf = 0;
    const started = Date.now();
    let cursor = 0;
    let fatal = null;

    async function handle(it) {
      let pdfs = [];
      let detailStatus = 0;
      try {
        const r = await get(it.link);
        detailStatus = r.status;
        if (r.status === 200) pdfs = extractPdfs(r.body);
      } catch (e) {
        if (e instanceof Blocked) throw e;
        log(`  ! ${it.slug}: ${e.message}`);
      }
      if (pdfs.length === 0) noPdf += 1;

      const rec = {
        id: it.id,
        slug: it.slug,
        title: it.title?.rendered ?? '',
        date: it.date ?? '',
        link: it.link ?? '',
        detail_status: detailStatus,
        pdf_urls: pdfs,
        tax_ids: {
          year: it['research-year'] ?? [],
          category: it['research-category'] ?? [],
          sub_category: it['research-sub-category'] ?? [],
          author: it['research-author'] ?? [],
          keywords: it['research-keywords'] ?? [],
          jel: it['research-jel'] ?? [],
        },
        content_html: it.content?.rendered ?? '',
      };
      out.write(JSON.stringify(rec) + '\n');

      n += 1;
      if (n % 25 === 0 || n === todo.length) {
        const pct = ((n / todo.length) * 100).toFixed(1);
        // ETA from the rate actually observed, not from DELAY_MS
        const perItem = (Date.now() - started) / n;
        const eta = (((todo.length - n) * perItem) / 60000).toFixed(1);
        log(`  ${n}/${todo.length} (${pct}%) - ${noPdf} without a PDF - ~${eta} min left`);
      }
    }

    async function worker() {
      while (!fatal) {
        const i = cursor++;
        if (i >= todo.length) return;
        try {
          await handle(todo[i]);
        } catch (e) {
          fatal = e;
          return;
        }
      }
    }

    await Promise.all(
      Array.from({ length: DETAIL_CONCURRENCY }, () => worker())
    );
    out.end();
    await new Promise((r) => out.on('finish', r));
    if (fatal) throw fatal;
    log(`done: ${n} harvested this run, ${noPdf} had no PDF link`);
  } catch (e) {
    if (e instanceof Blocked) {
      log('\nHARVEST STOPPED');
      log(e.message);
      process.exitCode = 2;
    } else {
      throw e;
    }
  } finally {
    await ctx.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
