/**
 * 后台 Service Worker — 代理 API 请求（绕过 HTTPS→HTTP 限制）
 */

// ── OZON 视频网络请求捕获会话 ──
var ozonVideoCaptureSessions = {};

// ── 猫抓式常驻缓存：独立于 session，插件启动后持续监听所有 tab ──
var ozonRecentVideoRequestsByTab = {};
var MAX_RECENT_VIDEOS_PER_TAB = 30;

// 高置信判定：猫抓已验证的 OZON PDP 主视频特征
// 格式: https://v-{数字}.ozone.ru/vod/video-{数字}/.../asset_{数字}_h264.mp4?type=pdp
function isHighConfidenceOzonPdpVideoUrl(url) {
  return /https:\/\/v-\d+\.ozone\.ru\/vod\/video-\d+\//i.test(url) &&
         /asset_\d+_h264\.mp4/i.test(url) &&
         /type=pdp/i.test(url);
}

// 判断是否为可能的 OZON 商品视频（含猫抓验证的 PDP 特征）
function isLikelyOzonPdpVideo(url) {
  var u = String(url || '').toLowerCase();
  if (!u) return false;

  // 排除图片
  if (/\.(jpg|jpeg|png|webp|avif|gif|svg)(\?|$)/i.test(u)) return false;
  if (u.indexOf('/multimedia-') >= 0) return false;
  // 排除买家秀 /s3/video-（与主视频 /vod/video- 不同）
  if (u.indexOf('/s3/video-') >= 0 || u.indexOf('/s3_') >= 0) return false;

  // ★ 猫抓验证：v-*.ozone.ru/vod/video-/...asset_*_h264.mp4?type=pdp
  if (isHighConfidenceOzonPdpVideoUrl(url)) return true;

  // OZON PDP 视频
  if (u.indexOf('ozone.ru/vod/') >= 0 && u.indexOf('.mp4') >= 0 && u.indexOf('type=pdp') >= 0) return true;

  // 其他视频候选
  return (
    u.indexOf('.mp4') >= 0 ||
    u.indexOf('.m3u8') >= 0 ||
    u.indexOf('.mpd') >= 0 ||
    u.indexOf('.m4s') >= 0 ||
    u.indexOf('.ts') >= 0 ||
    u.indexOf('dash') >= 0 ||
    u.indexOf('hls') >= 0 ||
    u.indexOf('manifest') >= 0 ||
    u.indexOf('video') >= 0 ||
    u.indexOf('stream') >= 0
  );
}

// 判断是否为 OZON 买家秀/评论视频
function isOzonReviewVideoUrl(url) {
  var u = String(url || '').toLowerCase();
  if (!u) return false;
  return (
    u.indexOf('/s3/video-') >= 0 ||
    u.indexOf('ir.ozone.ru/s3/video') >= 0 ||
    u.indexOf('review') >= 0 ||
    u.indexOf('comment') >= 0 ||
    u.indexOf('feedback') >= 0 ||
    u.indexOf('buyer') >= 0
  );
}

// 常驻缓存：记录最近视频请求（猫抓风格 — 长期监听）
function rememberOzonVideoRequest(tabId, details) {
  if (tabId < 0) return;
  var url = details.url || '';
  if (!isLikelyOzonPdpVideo(url)) return;

  if (!ozonRecentVideoRequestsByTab[tabId]) {
    ozonRecentVideoRequestsByTab[tabId] = [];
  }
  var list = ozonRecentVideoRequestsByTab[tabId];

  // 去重
  if (list.some(function(x) { return x.url === url; })) return;

  var videoKind = isHighConfidenceOzonPdpVideoUrl(url) ? 'pdp_main_video' :
                  isOzonReviewVideoUrl(url) ? 'review_video' :
                  'unknown_video';

  list.unshift({
    url: url,
    type: details.type || '',
    frameId: details.frameId,
    timeStamp: details.timeStamp,
    capturedAt: Date.now(),
    source: 'background_persistent_webrequest',
    video_kind: videoKind
  });

  // 每个 tab 最多保留 30 条
  ozonRecentVideoRequestsByTab[tabId] = list.slice(0, MAX_RECENT_VIDEOS_PER_TAB);
}

function isOzonVideoRequestUrl(url) {
  var u = String(url || '').toLowerCase();
  if (!u) return false;
  // 排除图片
  if (/\.(jpg|jpeg|png|webp|avif|gif|svg)(\?|$)/i.test(u)) return false;
  if (u.indexOf('/multimedia-') >= 0) return false;
  // 排除明显评论/推荐
  if (u.indexOf('review') >= 0 || u.indexOf('comment') >= 0) return false;
  // 视频相关 URL 特征（含 DASH/HLS 流媒体）
  return (
    u.indexOf('.mp4') >= 0 ||
    u.indexOf('.m3u8') >= 0 ||
    u.indexOf('.webm') >= 0 ||
    u.indexOf('.mpd') >= 0 ||
    u.indexOf('.m4s') >= 0 ||
    u.indexOf('.ts') >= 0 ||
    u.indexOf('.mov') >= 0 ||
    u.indexOf('dash') >= 0 ||
    u.indexOf('hls') >= 0 ||
    u.indexOf('manifest') >= 0 ||
    u.indexOf('init.mp4') >= 0 ||
    u.indexOf('video') >= 0 ||
    u.indexOf('stream') >= 0 ||
    u.indexOf('player') >= 0
  );
}

// ── 常驻监听 OZON 域名的所有网络请求 ──
// 限制每个 tab 最多缓存 500 条，超过则移除最旧的
var MAX_CACHED_REQUESTS = 500;

chrome.webRequest.onBeforeRequest.addListener(
  function(details) {
    // ★ 第 0 层（猫抓风格）：常驻缓存 — 无条件记录，不依赖 session
    rememberOzonVideoRequest(details.tabId, details);

    // 第 1 层：session 缓存（用户点击采集后的精确捕获）
    if (details.tabId < 0) return;
    var session = ozonVideoCaptureSessions[details.tabId];
    if (!session || !session.active) return;
    var url = details.url || '';
    if (!isOzonVideoRequestUrl(url)) return;

    // 去重
    if (!session._urlSeen) session._urlSeen = {};
    var normalizedUrl = url.split('?')[0];
    if (session._urlSeen[normalizedUrl]) return;
    session._urlSeen[normalizedUrl] = true;

    // 限制缓存数量
    while (session.requests.length >= MAX_CACHED_REQUESTS) {
      session.requests.shift();
    }

    session.requests.push({
      url: url,
      type: details.type || '',
      frameId: details.frameId,
      timeStamp: details.timeStamp,
      capturedAt: Date.now()
    });
  },
  {
    urls: [
      "*://*.ozon.ru/*",
      "*://*.ozone.ru/*",
      "*://*.cdn-ozon.ru/*",
      "*://*.ozonusercontent.com/*"
    ]
  }
);

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

  // OZON: React Fiber 主图 Gallery 视频探测（MAIN world）
  if (request.action === 'probeOzonMainGalleryVideo') {
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id },
      world: "MAIN",
      func: probeOzonMainGalleryVideoInMainWorld
    }).then(function(results) {
      var result = results && results[0] && results[0].result;
      sendResponse({
        ok: true,
        data: result || { ok: false, reason: 'empty_execute_result', videos: [] }
      });
    }).catch(function(e) {
      sendResponse({
        ok: false,
        error: e.message || String(e)
      });
    });
    return true;
  }

  // OZON: 开始常驻监听当前 tab 的网络请求（幂等：已活跃则复用现有缓存）
  if (request.action === 'startOzonVideoNetworkCapture') {
    var tabId = sender.tab && sender.tab.id;
    if (!tabId) {
      sendResponse({ ok: false, error: 'no_tab_id' });
      return true;
    }
    var existing = ozonVideoCaptureSessions[tabId];
    if (existing && existing.active) {
      // 已活跃：不重置缓存，返回已有请求数
      sendResponse({ ok: true, alreadyActive: true, requestCount: existing.requests.length });
      return true;
    }
    ozonVideoCaptureSessions[tabId] = {
      active: true,
      startedAt: Date.now(),
      requests: existing ? existing.requests : []  // 保留旧缓存（如有）
    };
    sendResponse({ ok: true, requestCount: 0 });
    return true;
  }

  // OZON: 获取已缓存的请求（常驻模式 — 不停止监听，不清除缓存）
  if (request.action === 'stopOzonVideoNetworkCapture' || request.action === 'getOzonVideoNetworkCapture') {
    var tabId2 = sender.tab && sender.tab.id;
    var session = ozonVideoCaptureSessions[tabId2];
    var reqs = session ? session.requests : [];
    // 常驻模式：不设 active=false，缓存持续保留
    sendResponse({
      ok: true,
      requests: reqs,
      requestCount: reqs.length,
      active: session ? session.active : false
    });
    return true;
  }

  // OZON: 重置当前 tab 的缓存（导航到新商品页时调用）
  if (request.action === 'resetOzonVideoNetworkCapture') {
    var tabId3 = sender.tab && sender.tab.id;
    if (tabId3 && ozonVideoCaptureSessions[tabId3]) {
      ozonVideoCaptureSessions[tabId3].requests = [];
      ozonVideoCaptureSessions[tabId3].startedAt = Date.now();
    }
    // 同时重置常驻缓存
    if (tabId3 && ozonRecentVideoRequestsByTab[tabId3]) {
      ozonRecentVideoRequestsByTab[tabId3] = [];
    }
    sendResponse({ ok: true });
    return true;
  }

  // OZON: 读取猫抓式常驻缓存（独立于 session，从页面加载就开始监听）
  if (request.action === 'getRecentOzonVideoRequests') {
    var tabId4 = sender.tab && sender.tab.id;
    var list = ozonRecentVideoRequestsByTab[tabId4] || [];
    // 高置信 PDP 视频优先排在前面
    var highConf = [];
    var others = [];
    list.forEach(function(r) {
      if (isHighConfidenceOzonPdpVideoUrl(r.url)) {
        highConf.push(r);
      } else {
        others.push(r);
      }
    });
    sendResponse({
      ok: true,
      requests: highConf.concat(others),
      totalCount: list.length,
      highConfidenceCount: highConf.length
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

// OZON React Fiber 主图 Gallery 视频探测（在 MAIN world 执行）
function probeOzonMainGalleryVideoInMainWorld() {
  function isVideoUrl(url) {
    var u = String(url || '').toLowerCase();
    return u.indexOf('.mp4') >= 0 || u.indexOf('.m3u8') >= 0 ||
           u.indexOf('.webm') >= 0 || u.indexOf('video.ozon') >= 0 ||
           u.indexOf('player') >= 0 || u.indexOf('stream') >= 0;
  }
  function isRejectedUrl(url) {
    var u = String(url || '').toLowerCase();
    return !u || u.indexOf('.jpg') >= 0 || u.indexOf('.jpeg') >= 0 ||
           u.indexOf('.png') >= 0 || u.indexOf('.webp') >= 0 ||
           u.indexOf('.avif') >= 0 || u.indexOf('/multimedia-') >= 0 ||
           u.indexOf('/s3/video-') >= 0;
  }
  function findGalleryRoot() {
    return document.querySelector('[data-widget="webGallery"]') ||
           document.querySelector('[data-widget*="webGallery"]') ||
           document.querySelector('[data-widget*="gallery"]') ||
           document.querySelector('[class*="gallery"]');
  }
  // 深度搜索 React Fiber：先查自己 → 查父节点 → 查子树
  function getReactFiber(el) {
    function fiberFromNode(node) {
      if (!node) return null;
      var keys = Object.keys(node);
      for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf('__reactFiber') === 0 || keys[i].indexOf('__reactContainer') === 0)
          return node[keys[i]];
      }
      return null;
    }
    // 1. 当前节点
    var f = fiberFromNode(el);
    if (f) return f;
    // 2. 向上查父节点
    var p = el.parentElement;
    for (var up = 0; up < 8 && p; up++, p = p.parentElement) {
      f = fiberFromNode(p);
      if (f) return f;
    }
    // 3. 向下查子树
    var all = el.querySelectorAll('*');
    for (var i = 0; i < all.length && i < 300; i++) {
      f = fiberFromNode(all[i]);
      if (f) return f;
    }
    return null;
  }
  function collectFromValue(value, out, path) {
    if (!value) return;
    if (typeof value === 'string') {
      if (isVideoUrl(value) && !isRejectedUrl(value)) {
        out.push({ role: 'main_video', url: value.replace(/\\/g, ''), poster: '', duration_text: '',
          source_area: 'main_gallery', source: 'react_fiber_gallery_string',
          evidence_path: path || '', need_manual_check: false });
      }
      return;
    }
    if (Array.isArray(value)) {
      for (var i = 0; i < value.length && i < 200; i++)
        collectFromValue(value[i], out, (path || '') + '[' + i + ']');
      return;
    }
    if (typeof value !== 'object') return;
    var typeText = String(value.type || value.mediaType || value.kind || '').toLowerCase();
    var mayBeVideo = typeText.indexOf('video') >= 0 || value.isCoverVideo || value.coverVideo ||
                     value.video || value.videoUrl || value.hdUrl || value.sdUrl || value.m3u8 || value.src;
    if (mayBeVideo) {
      var url = value.hdUrl || value.sdUrl || value.videoUrl || value.url || value.src || value.m3u8 || '';
      var poster = value.poster || value.cover || value.preview || value.image || value.thumbnail || '';
      if (url && isVideoUrl(url) && !isRejectedUrl(url)) {
        out.push({ role: 'main_video', url: String(url).replace(/\\/g, ''), poster: poster || '',
          duration_text: value.duration || value.durationText || '',
          source_area: 'main_gallery', source: 'react_fiber_gallery_object',
          evidence_path: path || '', need_manual_check: false });
      }
    }
    ['items','images','media','mediaList','slides','gallery','videos','video','coverVideo'].forEach(function(k) {
      if (value[k]) collectFromValue(value[k], out, (path || '') + '.' + k);
    });
  }
  function traverseFiber(node, out, depth) {
    if (!node || depth > 80) return;
    try { if (node.memoizedProps) collectFromValue(node.memoizedProps, out, 'memoizedProps'); } catch(e) {}
    try { if (node.pendingProps) collectFromValue(node.pendingProps, out, 'pendingProps'); } catch(e) {}
    if (node.child) traverseFiber(node.child, out, depth + 1);
    if (node.sibling) traverseFiber(node.sibling, out, depth + 1);
  }
  try {
    var gallery = findGalleryRoot();
    if (!gallery) return { ok: false, reason: 'gallery_not_found',
      hint: '请回到商品顶部主图区域后再点击探测主视频', videos: [] };
    var fiber = getReactFiber(gallery);
    if (!fiber) return { ok: false, reason: 'react_fiber_not_found',
      hint: '找到主图区域但没有拿到 React Fiber，OZON 页面结构可能已变化', videos: [] };
    var out = [];
    traverseFiber(fiber, out, 0);
    var seen = {};
    var videos = out.filter(function(v) {
      var key = v.url || v.poster || v.evidence_path;
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    }).slice(0, 3);
    return { ok: videos.length > 0,
      reason: videos.length > 0 ? 'found' : 'video_not_found_in_gallery_fiber', videos: videos };
  } catch(e) {
    return { ok: false, reason: 'probe_error', error: e.message || String(e), videos: [] };
  }
}
