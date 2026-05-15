// ==UserScript==
// @name         ccburn auto-sync
// @namespace    https://github.com/lalosevich/claude-test
// @version      0.1.0
// @description  Push claude.ai usage to your local ccburn dashboard automatically
// @match        https://claude.ai/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const CCBURN_URL    = 'http://127.0.0.1:8765/api/snapshot';
  const INTERVAL_MS   = 10 * 60 * 1000;    // 10 minutes
  const USAGE_PATH    = '/settings/usage';
  const IFRAME_WAIT   = 5000;              // ms to wait for the SPA to render
  const FIRST_RUN_MS  = 4000;              // initial scrape delay after page load

  function log(...args) { console.log('[ccburn]', ...args); }

  function parseUsage(text) {
    if (!text) return null;
    text = text.replace(/ /g, ' ').replace(/ /g, ' ');
    const sPct = text.match(/Current session[\s\S]*?(\d+(?:\.\d+)?)\s*%/i);
    const wPct = text.match(/All models[\s\S]*?(\d+(?:\.\d+)?)\s*%/i);
    const sRes = text.match(/Current session[\s\S]*?Resets?\s+in\s*(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?/i);
    if (!sPct || !wPct) return null;
    const data = {
      session_pct: parseFloat(sPct[1]),
      weekly_pct:  parseFloat(wPct[1]),
    };
    if (sRes) {
      data.session_reset_seconds =
        (parseInt(sRes[1] || 0)) * 3600 + (parseInt(sRes[2] || 0)) * 60;
    }
    return data;
  }

  function push(data) {
    if (!data) return;
    GM_xmlhttpRequest({
      method:  'POST',
      url:     CCBURN_URL,
      data:    JSON.stringify(data),
      headers: { 'Content-Type': 'application/json' },
      onload:  (r) => log('pushed', r.status, data),
      onerror: (e) => log('push failed (is ccburn_web.py running?)', e),
      ontimeout: () => log('push timed out'),
      timeout: 8000,
    });
  }

  function scrapeCurrentPage() {
    const data = parseUsage(document.body.innerText);
    if (data) push(data);
    else log('parse failed on current page; will retry via iframe');
  }

  let frameBusy = false;
  function scrapeViaIframe() {
    if (frameBusy) return;
    frameBusy = true;
    const old = document.getElementById('ccburn-frame');
    if (old) old.remove();
    const f = document.createElement('iframe');
    f.id = 'ccburn-frame';
    f.src = USAGE_PATH;
    f.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1280px;height:800px;border:0;visibility:hidden';
    f.addEventListener('load', () => {
      setTimeout(() => {
        try {
          const text = (f.contentDocument && f.contentDocument.body && f.contentDocument.body.innerText) || '';
          const data = parseUsage(text);
          if (data) push(data);
          else log('iframe parse failed (first 200 chars):', text.slice(0, 200));
        } catch (e) {
          log('iframe access denied:', e && e.message);
        } finally {
          f.remove();
          frameBusy = false;
        }
      }, IFRAME_WAIT);
    });
    document.body.appendChild(f);
  }

  // If user is already on /settings/usage, scrape it directly.
  if (location.pathname.indexOf(USAGE_PATH) !== -1) {
    setTimeout(scrapeCurrentPage, FIRST_RUN_MS);
  } else {
    setTimeout(scrapeViaIframe, FIRST_RUN_MS);
  }
  setInterval(scrapeViaIframe, INTERVAL_MS);
})();
