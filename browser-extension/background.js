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
});
