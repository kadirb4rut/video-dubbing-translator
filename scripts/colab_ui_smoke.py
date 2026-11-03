"""Render the real repaired report and GUI preview on Colab, without inference."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
import urllib.request

OUT = Path('/content/regression-evidence')
ROOT = Path('/content/video-dubbing-translator-main')
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/content/playwright-browsers'
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'playwright', 'legacy-cgi'], check=True)
subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium-headless-shell'], check=True)
sys.path.insert(0, str(ROOT))
import web_gui
from playwright.sync_api import sync_playwright

validation = json.loads((OUT/'full-validation.json').read_text())
run = Path(validation['run_root'])
web_gui.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
preview = web_gui.OUTPUT_DIR / 'colab_repaired_preview.mp4'
shutil.copyfile(validation['final_video'], preview)
with web_gui.state_lock:
    web_gui.state['output_video'] = str(preview)
    web_gui.state['status'] = 'Done'
server = web_gui.ReusableThreadingHTTPServer(('127.0.0.1', 8765), web_gui.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
errors = []
try:
    status = json.loads(urllib.request.urlopen('http://127.0.0.1:8765/api/status').read())
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1280, 'height':1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8765/', wait_until='networkidle')
        page.locator('#source').select_option('en')
        page.locator('#target').select_option('tr')
        page.locator('#translationDevice').select_option('cuda')
        assert page.locator('#target').input_value() == 'tr'
        assert page.locator('#translationDevice').input_value() == 'cuda'
        page.locator('#finalVideo').wait_for(state='visible', timeout=60000)
        page.wait_for_timeout(1500)
        video_state = page.locator('#finalVideo').evaluate('''(video) => ({
            duration: Number.isFinite(video.duration) ? video.duration : null,
            readyState: video.readyState, src: video.currentSrc,
            error: video.error ? {code:video.error.code, message:video.error.message} : null,
            h264: video.canPlayType('video/mp4; codecs="avc1.64001f"'),
            aac: video.canPlayType('audio/mp4; codecs="mp4a.40.2"')
        })''')
        request = urllib.request.Request('http://127.0.0.1:8765' + status['outputVideoUrl'], headers={'Range':'bytes=0-1023'})
        with urllib.request.urlopen(request) as response:
            media_api = {'status':response.status, 'bytes':len(response.read()), 'content_range':response.headers.get('Content-Range')}
        assert media_api['status'] == 206 and media_api['bytes'] == 1024
        page.screenshot(path=str(OUT/'gui-smoke.png'), full_page=False)
        gui_title = page.title()
        page.goto((run/'quality_report.html').as_uri(), wait_until='load')
        assert page.locator('tbody tr').count() == 11
        assert page.locator('details').count() == 11
        assert 'voxcpm2-colab-native' in page.locator('table').inner_text()
        page.screenshot(path=str(OUT/'quality-report-top.png'), full_page=False)
        page.locator('details').nth(7).locator('summary').click()
        assert page.locator('details').nth(7).evaluate('(el) => el.open')
        page.screenshot(path=str(OUT/'quality-report-smoke.png'), full_page=False)
        report_title = page.title()
        browser.close()
    payload = {'passed': not errors, 'browser_route':'Browser plugin not available; Playwright Chromium on Colab',
        'gui_url':'http://127.0.0.1:8765/', 'gui_title':gui_title, 'status_api_ok':status['outputVideoUrl'].startswith('/media/'),
        'video_state':video_state, 'media_range_api':media_api, 'report_title':report_title, 'report_rows':11, 'report_details_interaction':True,
        'page_errors':errors, 'flow':'GUI loads → select Turkish/CUDA → actual final-video preview; report loads → expand Group 8 measurements'}
    (OUT/'ui-smoke.json').write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload))
finally:
    server.shutdown()
    server.server_close()
