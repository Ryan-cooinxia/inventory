/**
 * 后台 Service Worker — 代理 API 请求（绕过 HTTPS→HTTP 限制）
 */
chrome.runtime.onMessage.addListener(function (request, sender, sendResponse) {
  if (request.action === 'collect') {
    fetch(request.apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Auth-Token': request.token
      },
      body: JSON.stringify(request.payload)
    })
      .then(function (resp) { return resp.json(); })
      .then(function (result) { sendResponse({ ok: true, data: result }); })
      .catch(function (e) { sendResponse({ ok: false, error: e.message }); });
    return true; // 保持通道开放（异步 sendResponse）
  }

  if (request.action === 'check') {
    fetch('http://127.0.0.1:5000/')
      .then(function (resp) { sendResponse({ ok: true, status: resp.status }); })
      .catch(function (e) { sendResponse({ ok: false, error: e.message }); });
    return true;
  }

  // OZON: 穿透隔离沙箱，直接读取 window.__SPLIT_STATE__
  if (request.action === 'readSplitState') {
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id },
      world: "MAIN",
      func: extractSplitStateData
    }).then(function(results) {
      if (results && results[0] && results[0].result) {
        sendResponse({ ok: true, data: results[0].result });
      } else {
        sendResponse({ ok: false, error: 'SPLIT_STATE not found or empty' });
      }
    }).catch(function(e) {
      sendResponse({ ok: false, error: e.message });
    });
    return true;
  }
});

// 在 MAIN world 中执行的函数（可访问页面原生 window）
function extractSplitStateData() {
  try {
    var state = window.__SPLIT_STATE__;
    if (!state) {
      // try script tag fallback
      var scripts = document.querySelectorAll('script');
      for (var i = 0; i < scripts.length; i++) {
        var t = scripts[i].textContent || '';
        if (t.indexOf('__SPLIT_STATE__') >= 0) {
          var m = t.match(/__SPLIT_STATE__\s*=\s*(\{.*?\});?\s*\n/);
          if (!m) m = t.match(/=\s*(\{.*\});?\s*$/);
          if (m) try { state = JSON.parse(m[1]); } catch(e) {}
          break;
        }
      }
    }
    if (!state) return { error: '__SPLIT_STATE__ not found' };

    var result = { rich_text_html: '', rich_text_plain: '', attributes: [], video_url: '', poster: '' };
    var stateStr = JSON.stringify(state);

    // Extract rich content from state JSON
    var m1 = stateStr.match(/"richContent"\s*:\s*"([^"]{100,})"/);
    if (!m1) m1 = stateStr.match(/"richContentHtml"\s*:\s*"([^"]{100,})"/);
    if (!m1) m1 = stateStr.match(/"description"\s*:\s*"([^"]{100,})"/);
    if (m1) {
      result.rich_text_html = m1[1]
        .replace(/\\"/g, '"')
        .replace(/\\u003C/g, '<')
        .replace(/\\u003E/g, '>')
        .replace(/\\n/g, '\n')
        .replace(/\\\\/g, '\\');
      // plain text version
      var div = document.createElement('div');
      div.innerHTML = result.rich_text_html;
      result.rich_text_plain = (div.textContent || div.innerText || '').trim();
    }

    // Try deeper search for webDescription/widget data
    function deepSearch(obj, depth) {
      if (!obj || depth > 10) return;
      if (typeof obj === 'string' && obj.length > 500 && obj.indexOf('<') >= 0) {
        if (!result.rich_text_html) {
          result.rich_text_html = obj;
          var d = document.createElement('div'); d.innerHTML = obj;
          result.rich_text_plain = (d.textContent || d.innerText || '').trim();
        }
        return;
      }
      if (Array.isArray(obj)) { for (var j=0;j<obj.length&&j<100;j++) deepSearch(obj[j],depth+1); return; }
      if (typeof obj !== 'object') return;
      for (var k in obj) {
        if (!result.rich_text_html && (k.indexOf('richContent')>=0||k.indexOf('description')>=0||k.indexOf('Description')>=0))
          deepSearch(obj[k], depth+1);
        if (k.indexOf('characteristic')>=0||k.indexOf('widget')>=0||k.indexOf('layout')>=0)
          deepSearch(obj[k], depth+1);
      }
      // Scan top-level keys
      if (depth === 0) {
        for (var k2 in obj) { deepSearch(obj[k2], 1); }
      }
    }
    if (!result.rich_text_html) deepSearch(state, 0);

    // Extract attribute characteristics
    try {
      var chars = stateStr.match(/"characteristics"\s*:\s*(\[.*?\])/);
      if (chars) result.attributes = JSON.parse(chars[1]);
    } catch(e) {}

    // Extract main video poster
    try {
      var vid = stateStr.match(/"video"\s*:\s*\{[^}]*"url"\s*:\s*"([^"]+)"/);
      if (vid) result.video_url = vid[1];
      var poster = stateStr.match(/"poster"\s*:\s*"([^"]+)"/);
      if (poster) result.poster = poster[1];
    } catch(e) {}

    return result;
  } catch(e) { return { error: e.message }; }
}
