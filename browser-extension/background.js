/**
 * 后台 Service Worker
 * 1. 代理 API 请求（绕过 HTTPS→HTTP 限制）
 * 2. chrome.debugger 拦截 OZON PDP API 响应，提取富文本/视频
 */
const API_URL = 'http://127.0.0.1:5000';

// ── 网络代理 ──
chrome.runtime.onMessage.addListener(function (request, sender, sendResponse) {
  if (request.action === 'collect') {
    fetch(request.apiUrl || (API_URL + '/ozon/api/sources/add'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Auth-Token': request.token },
      body: JSON.stringify(request.payload)
    })
      .then(function (resp) { return resp.json(); })
      .then(function (result) { sendResponse({ ok: true, data: result }); })
      .catch(function (e) { sendResponse({ ok: false, error: e.message }); });
    return true;
  }

  if (request.action === 'check') {
    fetch(API_URL + '/')
      .then(function (resp) { sendResponse({ ok: true, status: resp.status }); })
      .catch(function (e) { sendResponse({ ok: false, error: e.message }); });
    return true;
  }

  // ── Debugger: 启动 OZON API 拦截 ──
  if (request.action === 'startDebugCapture') {
    startOzonNetworkCapture(sender.tab.id).then(function(result) {
      sendResponse(result);
    }).catch(function(e) {
      sendResponse({ ok: false, error: e.message });
    });
    return true;
  }

  // ── Debugger: 停止并获取结果 ──
  if (request.action === 'stopDebugCapture') {
    stopOzonNetworkCapture(sender.tab.id).then(function(result) {
      sendResponse(result);
    });
    return true;
  }
});

// ═══════════════════════════════════════════════════
// chrome.debugger OZON API 拦截
// ═══════════════════════════════════════════════════

const captureSessions = {}; // { tabId: { captured: [], debuggee: {...} } }

function findOzonKey(obj, target) {
  if (!obj || typeof obj !== 'object') return null;
  if (obj[target] !== undefined) return obj[target];
  if (Array.isArray(obj)) {
    for (let i = 0; i < obj.length && i < 200; i++) {
      const r = findOzonKey(obj[i], target);
      if (r) return r;
    }
    return null;
  }
  for (const key in obj) {
    if (key === target) return obj[key];
    const r = findOzonKey(obj[key], target);
    if (r) return r;
  }
  return null;
}

function decodeOzonHtml(str) {
  if (typeof str !== 'string') return str;
  return str
    .replace(/\\"/g, '"')
    .replace(/\\u003C/g, '<')
    .replace(/\\u003E/g, '>')
    .replace(/\\n/g, '\n')
    .replace(/\\\\/g, '\\');
}

async function startOzonNetworkCapture(tabId) {
  // Detach any existing debugger on this tab
  try {
    if (captureSessions[tabId]) {
      await chrome.debugger.detach({ tabId: tabId });
    }
  } catch(e) {}

  return new Promise((resolve) => {
    chrome.debugger.attach({ tabId: tabId }, "1.3", () => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }

      captureSessions[tabId] = { captured: [], debuggee: { tabId: tabId } };

      chrome.debugger.sendCommand({ tabId: tabId }, "Network.enable", {}, () => {
        resolve({ ok: true, message: 'Network capture started' });
      });
    });
  });
}

function stopOzonNetworkCapture(tabId) {
  return new Promise((resolve) => {
    const session = captureSessions[tabId];
    if (!session) {
      resolve({ ok: false, error: 'No active capture session' });
      return;
    }

    // Wait a bit for pending responses, then detach
    setTimeout(() => {
      try {
        chrome.debugger.detach({ tabId: tabId });
      } catch(e) {}
      delete captureSessions[tabId];

      resolve({
        ok: true,
        captured: session.captured,
        summary: `${session.captured.length} responses captured`
      });
    }, 2000);
  });
}

// Global debugger event listener for network responses
chrome.debugger.onEvent.addListener((source, method, params) => {
  if (method !== "Network.responseReceived") return;
  const tabId = source.tabId;
  const session = captureSessions[tabId];
  if (!session) return;

  const response = params.response;
  const url = response.url || '';

  // Only capture OZON API responses that might contain product data
  if (!url.includes('ozon.ru')) return;
  if (url.match(/\.(jpg|jpeg|png|webp|gif|svg|css|js|woff|ico)(\?|$)/i)) return;

  const requestId = params.requestId;
  const mimeType = (response.mimeType || '').toLowerCase();
  if (!mimeType.includes('json') && !mimeType.includes('html') && !mimeType.includes('javascript')) return;

  // Get response body
  setTimeout(() => {
    chrome.debugger.sendCommand({ tabId: tabId }, "Network.getResponseBody", { requestId: requestId }, (result) => {
      if (!result || !result.body) return;
      const body = result.body;
      // Check if this response contains OZON product data
      if (body.includes('richContent') || body.includes('rich_text') || body.includes('description') ||
          body.includes('video') || body.includes('webProductDescription') ||
          body.includes('characteristics') || url.includes('/pdp/') || url.includes('/product/')) {

        try {
          const data = JSON.parse(body);
          const richContent = findOzonKey(data, 'richContent') || findOzonKey(data, 'description') || findOzonKey(data, 'rich_text');
          const videoData = findOzonKey(data, 'video') || findOzonKey(data, 'mediaList');
          const characteristics = findOzonKey(data, 'characteristics');

          let richHtml = '', richPlain = '';
          if (richContent) {
            if (typeof richContent === 'string') {
              richHtml = decodeOzonHtml(richContent);
            } else if (typeof richContent === 'object') {
              richHtml = JSON.stringify(richContent);
            }
            richPlain = richHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
          }

          let videoUrl = '', poster = '';
          if (videoData) {
            if (typeof videoData === 'string') {
              videoUrl = videoData;
            } else if (videoData.url || videoData.src || videoData.hdUrl) {
              videoUrl = videoData.hdUrl || videoData.url || videoData.src || '';
              poster = videoData.poster || videoData.cover || videoData.preview || '';
            } else if (Array.isArray(videoData) && videoData.length) {
              videoUrl = videoData[0].url || videoData[0].src || '';
              poster = videoData[0].poster || videoData[0].cover || '';
            }
          }

          session.captured.push({
            url: url,
            rich_text_html: richHtml.substring(0, 200000),
            rich_text_plain: richPlain.substring(0, 50000),
            video_url: videoUrl,
            video_poster: poster,
            attributes: characteristics || [],
            captured_at: new Date().toISOString()
          });

          // Send to content script
          chrome.tabs.sendMessage(tabId, {
            action: 'debugCaptureResult',
            data: session.captured[session.captured.length - 1]
          }).catch(() => {}); // content script may not be listening
        } catch(e) {}
      }
    });
  }, 100);
});

// ── SPLIT_STATE fallback (keep previous approach) ──
chrome.runtime.onMessage.addListener(function (request, sender, sendResponse) {
  if (request.action === 'readSplitState') {
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id },
      world: "MAIN",
      func: extractSplitStateData
    }).then(function(results) {
      if (results && results[0] && results[0].result) {
        sendResponse({ ok: true, data: results[0].result });
      } else {
        sendResponse({ ok: false, error: 'SPLIT_STATE not found' });
      }
    }).catch(function(e) {
      sendResponse({ ok: false, error: e.message });
    });
    return true;
  }
});

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

    var result = { rich_text_html: '', rich_text_plain: '', attributes: [], video_url: '', poster: '' };

    function findKey(obj, target) {
      if (!obj || typeof obj !== 'object') return null;
      if (obj[target] !== undefined) return obj[target];
      if (Array.isArray(obj)) { for (var i=0;i<obj.length&&i<200;i++) { var r=findKey(obj[i],target); if(r)return r; } return null; }
      for (var k in obj) { if (k===target) return obj[k]; var r=findKey(obj[k],target); if(r)return r; }
      return null;
    }

    var richKeys = ['richContent','richContentHtml','description','Description','htmlContent','content'];
    for (var rk=0;rk<richKeys.length;rk++) {
      var found = findKey(state, richKeys[rk]);
      if (found && typeof found === 'string' && found.length > 100) {
        result.rich_text_html = found.replace(/\\\"/g,'"').replace(/\\u003C/g,'<').replace(/\\u003E/g,'>').replace(/\\n/g,'\n').replace(/\\\\/g,'\\');
        result.rich_text_plain = result.rich_text_html.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
        break;
      }
    }

    var vidKeys = ['video','videoUrl','mediaList','media','videos'];
    for (var vk=0;vk<vidKeys.length;vk++) {
      var v = findKey(state, vidKeys[vk]);
      if (v) {
        if (typeof v === 'string') result.video_url = v;
        else if (typeof v === 'object') { result.video_url = v.url||v.src||v.videoUrl||''; result.poster = v.poster||v.cover||v.preview||''; }
        if (result.video_url||result.poster) break;
      }
    }

    try { var chars = findKey(state, 'characteristics'); if (chars&&Array.isArray(chars)) result.attributes=chars; } catch(e) {}
    return result;
  } catch(e) { return { error: e.message }; }
}
