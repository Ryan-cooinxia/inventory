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

    var result = { rich_text_html: '', rich_text_plain: '', attributes: [], video_url: '', poster: '', video_data: null };

    // Deep recursive search for any key matching target patterns
    function findKeyInObject(obj, targetKey) {
      if (!obj || typeof obj !== 'object') return null;
      if (obj[targetKey] !== undefined) return obj[targetKey];
      if (Array.isArray(obj)) {
        for (var i=0;i<obj.length&&i<200;i++) {
          var r = findKeyInObject(obj[i], targetKey);
          if (r) return r;
        }
        return null;
      }
      for (var k in obj) {
        if (k === targetKey) return obj[k];
        var r = findKeyInObject(obj[k], targetKey);
        if (r) return r;
      }
      return null;
    }

    // Find rich content - try multiple key names
    var richKeys = ['richContent','richContentHtml','description','Description','htmlContent','content'];
    for (var rk=0;rk<richKeys.length;rk++) {
      var found = findKeyInObject(state, richKeys[rk]);
      if (found && typeof found === 'string' && found.length > 100) {
        result.rich_text_html = found
          .replace(/\\"/g, '"').replace(/\\u003C/g, '<')
          .replace(/\\u003E/g, '>').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
        var div = document.createElement('div');
        div.innerHTML = result.rich_text_html;
        result.rich_text_plain = (div.textContent || div.innerText || '').trim();
        break;
      }
    }

    // Find video data
    var videoKeys = ['video','videoUrl','mediaList','media','videos'];
    for (var vk=0;vk<videoKeys.length;vk++) {
      var v = findKeyInObject(state, videoKeys[vk]);
      if (v) {
        if (typeof v === 'string' && v.length > 10) {
          result.video_url = v;
        } else if (typeof v === 'object') {
          result.video_data = JSON.stringify(v);
          result.video_url = v.url || v.src || v.videoUrl || '';
          result.poster = v.poster || v.cover || v.preview || v.image || '';
        }
        if (result.video_url || result.poster) break;
      }
    }

    // Find characteristics/attributes
    try {
      var chars = findKeyInObject(state, 'characteristics');
      if (chars && Array.isArray(chars)) result.attributes = chars;
      if (!result.attributes.length) {
        var specs = findKeyInObject(state, 'specifications');
        if (specs && Array.isArray(specs)) result.attributes = specs;
      }
    } catch(e) {}

    // Fallback: if still no rich text, stringify entire state and extract long HTML strings
    if (!result.rich_text_html) {
      var stateStr = JSON.stringify(state);
      var m = stateStr.match(/"[^"]{500,20000}"/g);
      if (m) {
        for (var si=0;si<m.length;si++) {
          var candidate = m[si].slice(1,-1);
          if (candidate.indexOf('<div')>=0 || candidate.indexOf('<p')>=0 || candidate.indexOf('<span')>=0) {
            result.rich_text_html = candidate
              .replace(/\\"/g,'"').replace(/\\u003C/g,'<')
              .replace(/\\u003E/g,'>').replace(/\\n/g,'\n').replace(/\\\\/g,'\\');
            var d2 = document.createElement('div');
            d2.innerHTML = result.rich_text_html;
            result.rich_text_plain = (d2.textContent||d2.innerText||'').trim();
            break;
          }
        }
      }
    }

    return result;
  } catch(e) { return { error: e.message }; }
}
