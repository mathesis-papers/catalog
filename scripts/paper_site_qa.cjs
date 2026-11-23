// Consume an already-built template site. No renderer or publication logic.
const { createRequire } = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function run(rootArg, outputArg, { timeoutMs = 20000 } = {}) {
  const root = path.resolve(rootArg), output = path.resolve(outputArg);
  if (output === root || output.startsWith(root + path.sep)) throw new Error('QA evidence must be outside the paper tree');
  const mapping = JSON.parse(fs.readFileSync(path.join(root, '.mathesis/book.json')));
  const { chromium } = createRequire(path.join(root, 'package.json'))('playwright-chromium');
  // Use the full browser supplied by the toolchain, including its media stack.
  // Playwright's default headless launch may select a separate headless shell.
  const chrome = process.env.PW_CHROMIUM_PATH || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(p => fs.existsSync(p)) || chromium.executablePath();
  fs.mkdirSync(output, { recursive: true });
  const results = [], operations = [], media = [];
  let browser, active, failure, browserVersion;
  const save = () => fs.writeFileSync(path.join(output, 'browser.json'), JSON.stringify({
    schema: 'mathesis.paper-browser-check.v1', results, operations, active, media,
    browser: { executable: chrome, version: browserVersion },
    failure, failures: results.filter(r => !r.main || r.katexErrors || r.rawDollars || r.pageErrors.length || r.bodyWidth > r.viewport + 1),
  }, null, 2) + '\n');
  async function check(name, action, limit = timeoutMs) {
    active = { name, started_at: new Date().toISOString() };
    save();
    console.log('Browser check started: ' + name);
    let timer;
    try {
      const value = await Promise.race([
        Promise.resolve().then(action),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(name + ' timed out after ' + limit + ' ms')), limit); }),
      ]);
      operations.push({ ...active, finished_at: new Date().toISOString() });
      active = undefined;
      save();
      return value;
    } catch (error) {
      throw new Error(name + ': ' + error.message, { cause: error });
    } finally { clearTimeout(timer); }
  }
  // Cleanup must also be bounded after a browser process or media promise stalls.
  async function close(target) {
    if (!target) return;
    let timer;
    try { await Promise.race([target.close(), new Promise(resolve => { timer = setTimeout(resolve, 3000); })]); }
    finally { clearTimeout(timer); }
  }
  try {
    browser = await check('Launch Chromium', () => chromium.launch({ headless: true, timeout: timeoutMs, executablePath: chrome }));
    browserVersion = browser.version();
    for (const lang of ['en', 'zh']) {
      const chapters = fs.readdirSync(path.join(root, 'books', mapping.book, lang))
        .filter(name => name.endsWith('.md') && name !== 'SUMMARY.md')
        .map(name => name === 'README.md' ? 'index.html' : name.replace(/\.md$/, '.html'));
      if (!chapters.length) throw new Error('missing reading edition: ' + lang);
      for (const theme of ['light', 'navy']) for (const width of [1440, 390]) {
        const context = await check('Reading context', () => browser.newContext({ viewport: { width, height: 1000 } }));
        try {
          context.setDefaultTimeout(timeoutMs);
          const page = await check('Reading page', () => context.newPage());
          await check('Reading theme', () => page.addInitScript(value => localStorage.setItem('mdbook-theme', value), theme));
          for (const chapter of chapters) {
            const label = `${lang}/${chapter} ${theme} ${width}`;
            const pageErrors = [];
            const onError = error => pageErrors.push(error.message);
            page.on('pageerror', onError);
            const url = pathToFileURL(path.join(root, '_site', mapping.book, lang, chapter)).href;
            await check(label + ' navigation', () => page.goto(url, { waitUntil: 'load', timeout: timeoutMs }));
            await check(label + ' fonts', () => page.evaluate(() => document.fonts.ready.then(() => true)));
            const facts = await check(label + ' layout', () => page.evaluate(() => ({
              main: !!document.querySelector('main'),
              katexErrors: document.querySelectorAll('.katex-error').length,
              rawDollars: document.querySelector('main')?.innerText.includes('$$'),
              bodyWidth: document.body.scrollWidth,
              viewport: innerWidth,
              title: document.querySelector('h1')?.innerText || '',
            })));
            results.push({ lang, theme, width, chapter, ...facts, pageErrors });
            page.off('pageerror', onError);
            if (chapter === 'index.html' && theme === 'light') {
              await check(label + ' screenshot', () => page.screenshot({ path: path.join(output, `reading-${lang}-${width}.png`), fullPage: true, timeout: timeoutMs }));
            }
          }
        } finally { await close(context); }
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
        const context = await check('Film context', () => browser.newContext({ viewport: { width, height: 1000 } }));
        try {
          context.setDefaultTimeout(timeoutMs);
          const page = await check('Film page', () => context.newPage()), pageErrors = [], requestFailures = [];
          page.on('pageerror', error => pageErrors.push(error.message));
          page.on('requestfailed', request => requestFailures.push({ url: request.url(), error: request.failure()?.errorText }));
          const label = `film ${number + 1} ${width}`;
          await check(label + ' navigation', () => page.goto(localUrl.href, { waitUntil: 'load', timeout: timeoutMs }));
          const video = page.locator('video').first();
          const inspectMedia = () => video.evaluate(element => ({
            currentSrc: element.currentSrc, readyState: element.readyState, networkState: element.networkState,
            paused: element.paused, currentTime: element.currentTime,
            error: element.error ? { code: element.error.code, message: element.error.message } : null,
            formats: Object.fromEntries(['video/mp4', 'video/mp4; codecs="avc1.64001F"', 'audio/mp4; codecs="mp4a.40.2"']
              .map(type => [type, element.canPlayType(type)])),
            sources: [...element.querySelectorAll('source')].map(source => ({ src: source.src, type: source.type })),
          }));
          const observation = { label, before: await check(label + ' media state', inspectMedia), requestFailures };
          media.push(observation);
          save();
          try {
            await check(label + ' play', () => video.evaluate(async element => { element.muted = true; await element.play(); }));
          } catch (error) {
            let timer;
            try {
              observation.after = await Promise.race([inspectMedia(), new Promise(resolve => {
                timer = setTimeout(() => resolve({ unavailable: 'browser media diagnostics timed out' }), 1000);
              })]);
            } catch (diagnosticError) { observation.after = { unavailable: diagnosticError.message }; }
            finally { clearTimeout(timer); }
            save();
            throw error;
          }
          await check(label + ' frames', () => page.waitForFunction(() => {
            const video = document.querySelector('video');
            return video && video.readyState >= 2 && video.currentTime > 0.1 && !video.paused;
          }, null, { timeout: timeoutMs }));
          const facts = await check(label + ' metadata', () => video.evaluate(element => {
            element.pause();
            return { videoWidth: element.videoWidth, videoHeight: element.videoHeight,
              duration: element.duration, currentTime: element.currentTime, mediaError: element.error?.message || null };
          }));
          if (!(facts.videoWidth > 0 && facts.videoHeight > 0 && facts.duration > 0) || facts.mediaError) throw new Error('film did not play');
          const layout = await check(label + ' layout', () => page.evaluate(() => ({ bodyWidth: document.body.scrollWidth, viewport: innerWidth })));
          results.push({ lang: 'en/zh subtitles', theme: 'light', width, chapter: relative,
            main: true, katexErrors: 0, rawDollars: false, ...layout, ...facts, pageErrors });
          await check(label + ' screenshot', () => page.screenshot({ path: path.join(output, `film-${number + 1}-${width}.png`), fullPage: true, timeout: timeoutMs }));
        } finally { await close(context); }
      }
    }
    save();
    const failures = results.filter(r => !r.main || r.katexErrors || r.rawDollars || r.pageErrors.length || r.bodyWidth > r.viewport + 1);
    console.log(`${results.length} reading/media configurations; ${failures.length} failures`);
    if (failures.length) throw new Error(failures.length + ' reading/media configurations failed');
  } catch (error) {
    failure = { name: active?.name || 'Browser inspection', message: error.message };
    save();
    throw error;
  } finally { await close(browser); }
}

module.exports = { run };
if (require.main === module) {
  const [root, output] = process.argv.slice(2);
  if (!root || !output) throw new Error('usage: node paper_site_qa.cjs PAPER_ROOT QA_DIR');
  run(root, output).catch(error => { console.error(error); process.exitCode = 1; });
}
