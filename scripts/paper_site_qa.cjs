// Consume an already-built template site. No renderer or publication logic.
const { createRequire } = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const [rootArg, outputArg] = process.argv.slice(2);
if (!rootArg || !outputArg) throw new Error('usage: node paper_site_qa.cjs PAPER_ROOT QA_DIR');
const root = path.resolve(rootArg), output = path.resolve(outputArg);
if (output.startsWith(root + path.sep)) throw new Error('QA evidence must be outside the paper tree');
const mapping = JSON.parse(fs.readFileSync(path.join(root, '.mathesis/book.json')));
const { chromium } = createRequire(path.join(root, 'package.json'))('playwright-chromium');
const chrome = process.env.PW_CHROMIUM_PATH || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(p => fs.existsSync(p));
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(chrome ? { executablePath: chrome } : {}) });
  const results = [];
  try {
    for (const lang of ['en', 'zh']) {
      const chapters = fs.readdirSync(path.join(root, 'books', mapping.book, lang))
        .filter(name => name.endsWith('.md') && name !== 'SUMMARY.md')
        .map(name => name === 'README.md' ? 'index.html' : name.replace(/\.md$/, '.html'));
      if (!chapters.length) throw new Error('missing reading edition: ' + lang);
      for (const theme of ['light', 'navy']) for (const width of [1440, 390]) {
        const context = await browser.newContext({ viewport: { width, height: 1000 } });
        const page = await context.newPage();
        await page.addInitScript(value => localStorage.setItem('mdbook-theme', value), theme);
        for (const chapter of chapters) {
          const pageErrors = [];
          const onError = error => pageErrors.push(error.message);
          page.on('pageerror', onError);
          const url = pathToFileURL(path.join(root, '_site', mapping.book, lang, chapter)).href;
          await page.goto(url, { waitUntil: 'load' });
          await page.evaluate(() => document.fonts.ready);
          const facts = await page.evaluate(() => ({
            main: !!document.querySelector('main'),
            katexErrors: document.querySelectorAll('.katex-error').length,
            rawDollars: document.querySelector('main')?.innerText.includes('$$'),
            bodyWidth: document.body.scrollWidth,
            viewport: innerWidth,
            title: document.querySelector('h1')?.innerText || '',
          }));
          results.push({ lang, theme, width, chapter, ...facts, pageErrors });
          page.off('pageerror', onError);
          if (chapter === 'index.html' && theme === 'light') {
            await page.screenshot({ path: path.join(output, `reading-${lang}-${width}.png`), fullPage: true });
          }
        }
        await context.close();
      }
    }
    const material = JSON.parse(fs.readFileSync(path.join(root, '_site/mathesis-materials.json')));
    for (const [number, film] of (material.videos || []).entries()) {
      if (!film.url.startsWith(material.site_url)) throw new Error('film page leaves this paper');
      const relative = film.url.slice(material.site_url.length);
      const relativePath = relative.split('#', 1)[0];
      const file = path.resolve(root, '_site', relativePath, relativePath.endsWith('/') ? 'index.html' : '');
      if (!file.startsWith(path.join(root, '_site') + path.sep)) throw new Error('unsafe film page');
      const localUrl = pathToFileURL(file);
      localUrl.hash = new URL(film.url).hash;
      for (const width of [1440, 390]) {
        const context = await browser.newContext({ viewport: { width, height: 1000 } });
        try {
          const page = await context.newPage(), pageErrors = [];
          page.on('pageerror', error => pageErrors.push(error.message));
          await page.goto(localUrl.href, { waitUntil: 'load' });
          const video = page.locator('video').first();
          await video.evaluate(async element => { element.muted = true; await element.play(); });
          await page.waitForFunction(() => {
            const video = document.querySelector('video');
            return video && video.readyState >= 2 && video.currentTime > 0.1 && !video.paused;
          });
          const facts = await video.evaluate(element => {
            element.pause();
            return { videoWidth: element.videoWidth, videoHeight: element.videoHeight,
              duration: element.duration, currentTime: element.currentTime, mediaError: element.error?.message || null };
          });
          if (!(facts.videoWidth > 0 && facts.videoHeight > 0 && facts.duration > 0) || facts.mediaError) throw new Error('film did not play');
          const layout = await page.evaluate(() => ({ bodyWidth: document.body.scrollWidth, viewport: innerWidth }));
          results.push({ lang: 'en/zh subtitles', theme: 'light', width, chapter: relative,
            main: true, katexErrors: 0, rawDollars: false, ...layout, ...facts, pageErrors });
          await page.screenshot({ path: path.join(output, `film-${number + 1}-${width}.png`), fullPage: true });
        } finally { await context.close(); }
      }
    }
  } finally { await browser.close(); }
  const failures = results.filter(r => !r.main || r.katexErrors || r.rawDollars || r.pageErrors.length || r.bodyWidth > r.viewport + 1);
  fs.writeFileSync(path.join(output, 'browser.json'), JSON.stringify({ schema: 'mathesis.paper-browser-check.v1', results, failures }, null, 2) + '\n');
  console.log(`${results.length} reading/media configurations; ${failures.length} failures`);
  if (failures.length) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
