/**
 * 商品采集器 — 弹窗（Token 配置 + 状态查看）
 */
(function () {
  'use strict';

  const contentEl = document.getElementById('content');

  // ── 渲染 ────────────────────────────────────────────
  function renderConfig() {
    contentEl.innerHTML =
      '<div style="padding:8px">' +
      '<div style="background:#d1e7dd;padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:13px;color:#0f5132;">' +
        '📌 <b>采集方式</b>：打开商品页面，右侧会出现绿色 <b>"采集"</b> 浮动按钮，直接点击即可采集，无需打开此弹窗。</div>' +
      '<h3 style="font-size:15px;margin-bottom:6px;">⚙️ 配置 Token</h3>' +
      '<p style="font-size:12px;color:#6c757d;margin-bottom:8px;">系统 → OZON运营 → 平台接口 → 复制插件Token：</p>' +
      '<input id="tokenInput" type="text" style="width:100%;padding:8px;border:1px solid #ced4da;border-radius:4px;font-size:13px;font-family:monospace;box-sizing:border-box;" placeholder="粘贴 Token...">' +
      '<div style="display:flex;gap:8px;margin-top:8px;">' +
        '<button id="btnSave" style="flex:1;padding:8px;background:#198754;color:#fff;border:none;border-radius:4px;font-size:13px;font-weight:600;cursor:pointer;">💾 保存</button>' +
        '<button id="btnTest" style="padding:8px 16px;background:#6c757d;color:#fff;border:none;border-radius:4px;font-size:13px;cursor:pointer;">🔍 测试</button>' +
      '</div>' +
      '<div id="cfgStatus" style="margin-top:8px;font-size:13px;text-align:center;"></div>' +
      '</div>';

    document.getElementById('btnSave').onclick = saveConfig;
    document.getElementById('btnTest').onclick = testConnection;
  }

  function renderStatus(token) {
    contentEl.innerHTML =
      '<div style="padding:8px">' +
      '<div style="background:#d1e7dd;padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:13px;color:#0f5132;text-align:center;">' +
        '✅ 已配置</div>' +
      '<div style="font-size:12px;color:#6c757d;margin-bottom:6px;">Token：</div>' +
      '<code style="display:block;padding:8px;background:#f8f9fa;border-radius:4px;font-size:11px;word-break:break-all;margin-bottom:8px;">' + token.substring(0, 20) + '...' + token.slice(-10) + '</code>' +
      '<p style="font-size:12px;color:#6c757d;text-align:center;margin-bottom:12px;">📌 打开商品页面，点击右侧绿色浮动按钮采集</p>' +
      '<button id="btnReset" style="width:100%;padding:8px;background:#fff;color:#dc3545;border:1px solid #dc3545;border-radius:4px;font-size:12px;cursor:pointer;">🔄 重新配置</button>' +
      '</div>';

    document.getElementById('btnReset').onclick = async function () {
      await chrome.storage.local.remove(['auth_token']);
      renderConfig();
    };
  }

  async function saveConfig() {
    var token = document.getElementById('tokenInput').value.trim();
    if (!token) {
      document.getElementById('cfgStatus').innerHTML = '<span style="color:#dc3545;">请输入 Token</span>';
      return;
    }
    await chrome.storage.local.set({ auth_token: token });
    renderStatus(token);
  }

  async function testConnection() {
    var token = document.getElementById('tokenInput').value.trim();
    if (!token) {
      document.getElementById('cfgStatus').innerHTML = '<span style="color:#dc3545;">请先输入 Token</span>';
      return;
    }
    var s = document.getElementById('cfgStatus');
    s.innerHTML = '<span style="color:#6c757d;">测试中...</span>';
    try {
      var resp = await fetch('http://127.0.0.1:5000/', { method: 'GET' });
      if (resp.status === 302 || resp.status === 200) {
        s.innerHTML = '<span style="color:#198754;">✅ 系统连接正常</span>';
      } else {
        s.innerHTML = '<span style="color:#dc3545;">❌ 系统返回异常状态 ' + resp.status + '</span>';
      }
    } catch (e) {
      s.innerHTML = '<span style="color:#dc3545;">❌ 无法连接，请确认系统运行在 127.0.0.1:5000</span>';
    }
  }

  // ── 入口 ────────────────────────────────────────────
  async function init() {
    var result = await chrome.storage.local.get(['auth_token']);
    var token = result.auth_token || '';
    if (token) {
      renderStatus(token);
    } else {
      renderConfig();
    }
  }
  init();

})();
