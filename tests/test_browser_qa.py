"""Real-browser recovery checks; opt into an existing template's Node toolchain."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

MODULES = os.environ.get('MATHESIS_BROWSER_QA_MODULES')
SCRIPT = Path(__file__).resolve().parents[1]/'scripts/paper_site_qa.cjs'


@unittest.skipUnless(MODULES and shutil.which('node'), 'existing Playwright toolchain not selected')
class BrowserQA(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'paper'
        self.output = Path(self.temp.name)/'qa'
        (self.root/'.mathesis').mkdir(parents=True)
        (self.root/'.mathesis/book.json').write_text('{"book":"example"}')
        (self.root/'package.json').write_text('{}')
        (self.root/'node_modules').symlink_to(Path(MODULES).resolve(), target_is_directory=True)
        for lang in ('en','zh'):
            source=self.root/'books/example'/lang;source.mkdir(parents=True)
            (source/'README.md').write_text('A readable example')
            site=self.root/'_site/example'/lang;site.mkdir(parents=True)
            (site/'index.html').write_text('<html><main><h1>A theorem</h1><p>A readable argument.</p></main></html>')
        self.manifest={'site_url':'https://example.test/','videos':[]}

    def execute(self, timeout=5000):
        (self.root/'_site/mathesis-materials.json').write_text(json.dumps(self.manifest))
        code='require(process.argv[1]).run(process.argv[2],process.argv[3],{timeoutMs:Number(process.argv[4])}).catch(e=>{console.error(e.message);process.exitCode=1})'
        result=subprocess.run(['node','-e',code,str(SCRIPT),str(self.root),str(self.output),str(timeout)],
                              text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=45)
        return result,json.loads((self.output/'browser.json').read_text())

    def test_bilingual_reading_really_renders_in_both_themes_and_widths(self):
        result,report=self.execute()
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertEqual(len(report['results']),8)
        self.assertEqual(report['failures'],[])
        self.assertNotIn('failure',report)

    def test_unresolved_font_readiness_fails_with_the_precise_page(self):
        page=self.root/'_site/example/en/index.html'
        page.write_text(page.read_text()+'<script>Object.defineProperty(document.fonts,"ready",{get:()=>new Promise(()=>{})})</script>')
        result,report=self.execute(timeout=1500)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(report['failure']['name'],'en/index.html light 1440 fonts')
        self.assertIn('timed out',report['failure']['message'])
        self.assertEqual(report['results'],[])

    def test_unresolved_video_playback_keeps_the_completed_reading_checks(self):
        film=self.root/'_site/example/films/intro';film.mkdir(parents=True)
        (film/'index.html').write_text('<html><main><video></video></main><script>HTMLMediaElement.prototype.play=()=>new Promise(()=>{})</script></html>')
        self.manifest['videos']=[{'url':'https://example.test/example/films/intro/'}]
        result,report=self.execute(timeout=1500)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(report['failure']['name'],'film 1 1440 play')
        self.assertIn('timed out',report['failure']['message'])
        self.assertEqual(len(report['results']),8)
