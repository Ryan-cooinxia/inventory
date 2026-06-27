/**
 * 电商商品采集器 v2 — 店小秘式页面内采集
 * 支持: 1688 / 淘宝 / 天猫 / 拼多多 商品详情页
 */
(function () {
  'use strict';

  const API_URL = 'http://127.0.0.1:5000/ozon/api/sources/add';

  let authToken = null;
  let floatingBtn = null;
  let resultPanel = null;
  let toastTimer = null;

  // ── 初始化 ──────────────────────────────────────────
  async function init() {
    const platform = detectPlatform();
    if (platform === 'unknown') return;

    // 读取存储的 token
    try {
      const result = await chrome.storage.local.get(['auth_token']);
      authToken = result.auth_token || '';
    } catch (e) {
      authToken = '';
    }

    injectStyles();
    injectFloatingButton(platform);
  }

  function detectPlatform() {
    const host = location.hostname;
    if (host.includes('1688.com'))       return '1688';
    if (host.includes('taobao.com'))     return 'taobao';
    if (host.includes('tmall.com'))      return 'tmall';
    if (host.includes('yangkeduo.com'))  return 'pinduoduo';
    if (host.includes('pinduoduo.com'))  return 'pinduoduo';
    if (host.includes('ozon.ru') || host.includes('ozon.by') || host.includes('ozon.kz')) return 'ozon_product';
    return 'unknown';
  }

  // ── 注入样式 ────────────────────────────────────────
  function injectStyles() {
    const css = document.createElement('style');
    css.id = 'ozon-collector-style';
    css.textContent = `
      #ozon-collect-btn {
        position: fixed; right: 16px; top: 50%; transform: translateY(-50%);
        width: 52px; height: 52px; border-radius: 50%;
        background: linear-gradient(135deg, #198754, #20c997);
        color: #fff; border: none; font-size: 13px; font-weight: 700;
        box-shadow: 0 4px 16px rgba(25,135,84,.35);
        cursor: pointer; z-index: 2147483646;
        display: flex; align-items: center; justify-content: center;
        transition: all .2s; letter-spacing: 1px;
      }
      #ozon-collect-btn:hover { transform: translateY(-50%) scale(1.1); box-shadow: 0 6px 24px rgba(25,135,84,.45); }
      #ozon-collect-btn.loading { animation: ozon-pulse .8s infinite; }
      @keyframes ozon-pulse { 0%,100% { opacity:1; } 50% { opacity:.5; } }

      #ozon-result-panel {
        position: fixed; right: 16px; top: 50%; transform: translateY(-50%);
        width: 340px; max-height: 80vh; overflow-y: auto;
        background: #fff; border-radius: 12px;
        box-shadow: 0 8px 32px rgba(0,0,0,.2);
        z-index: 2147483647;
        font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
        font-size: 14px; color: #333;
      }
      #ozon-result-panel .panel-header {
        padding: 14px 16px; border-bottom: 1px solid #e5e7eb;
        display: flex; justify-content: space-between; align-items: center;
        font-weight: 700; font-size: 15px;
      }
      #ozon-result-panel .panel-close {
        background: none; border: none; font-size: 20px; cursor: pointer;
        color: #999; line-height: 1;
      }
      #ozon-result-panel .panel-body { padding: 14px 16px; }
      #ozon-result-panel .panel-field { margin-bottom: 10px; }
      #ozon-result-panel .panel-label { font-size: 12px; color: #999; margin-bottom: 2px; }
      #ozon-result-panel .panel-value { font-weight: 500; word-break: break-all; }
      #ozon-result-panel .panel-stats { display: flex; gap: 8px; margin: 12px 0; }
      #ozon-result-panel .panel-stat { flex: 1; background: #f8f9fa; border-radius: 8px; padding: 10px; text-align: center; }
      #ozon-result-panel .panel-stat-num { font-size: 22px; font-weight: 700; color: #198754; }
      #ozon-result-panel .panel-stat-label { font-size: 11px; color: #999; }

      #ozon-result-panel .btn-collect {
        display: block; width: 100%; padding: 12px; border: none; border-radius: 8px;
        background: #198754; color: #fff; font-size: 15px; font-weight: 700;
        cursor: pointer; transition: all .15s; margin-top: 12px;
      }
      #ozon-result-panel .btn-collect:hover { background: #146c43; }
      #ozon-result-panel .btn-collect:disabled { background: #a3cfbb; cursor: not-allowed; }
      #ozon-result-panel .btn-collect .spinner {
        display: inline-block; width: 16px; height: 16px;
        border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
        border-radius: 50%; animation: ozon-spin .6s linear infinite; vertical-align: middle; margin-right: 6px;
      }
      @keyframes ozon-spin { to { transform: rotate(360deg); } }

      #ozon-toast {
        position: fixed; top: 20px; left: 50%; transform: translateX(-50%);
        padding: 12px 24px; border-radius: 8px; font-size: 14px; font-weight: 600;
        z-index: 2147483647; box-shadow: 0 4px 16px rgba(0,0,0,.15);
        display: none;
        font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
      }
      #ozon-toast.success { background: #d1e7dd; color: #0f5132; }
      #ozon-toast.error { background: #f8d7da; color: #842029; }
      #ozon-toast.info { background: #cfe2ff; color: #084298; }
    `;
    (document.head || document.documentElement).appendChild(css);
  }

  // ── 浮动按钮 ────────────────────────────────────────
  function injectFloatingButton(platform) {
    if (document.getElementById('ozon-collect-btn')) return;

    floatingBtn = document.createElement('button');
    floatingBtn.id = 'ozon-collect-btn';
    floatingBtn.title = '采集到系统 (' + platform + ')';
    floatingBtn.textContent = '采集';
    floatingBtn.onclick = handleCollect;
    document.body.appendChild(floatingBtn);
  }

  function injectMobileSwitchButton() {
    if (document.getElementById('ozon-mobile-switch-btn')) return;

    // 提取商品 ID
    var itemId = getItemIdFromUrl(location.href);
    if (!itemId) return;

    var h5Url = 'https://h5.m.taobao.com/awp/core/detail.htm?id=' + itemId;

    var switchBtn = document.createElement('button');
    switchBtn.id = 'ozon-mobile-switch-btn';
    switchBtn.textContent = '📋 H5';
    switchBtn.title = '复制 H5 链接（备用）\n系统会自动在后端使用手机版页面补采详情图，无需手动打开。';
    switchBtn.style.cssText =
      'position:fixed;right:16px;top:calc(50% + 62px);transform:translateY(-50%);' +
      'width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,#6c757d,#495057);' +
      'color:#fff;border:none;font-size:11px;font-weight:700;' +
      'box-shadow:0 4px 16px rgba(108,117,125,.35);cursor:pointer;z-index:2147483646;' +
      'display:flex;align-items:center;justify-content:center;transition:all .2s;';
    switchBtn.onmouseenter = function() { this.style.transform = 'translateY(-50%) scale(1.1)'; };
    switchBtn.onmouseleave = function() { this.style.transform = 'translateY(-50%) scale(1)'; };
    switchBtn.onclick = function() {
      navigator.clipboard.writeText(h5Url).then(function() {
        showToast('✅ H5 链接已复制。系统会自动补采详情图，无需手动打开。', 'success');
      }).catch(function() {
        // fallback
        var ta = document.createElement('textarea');
        ta.value = h5Url; ta.style.position = 'fixed'; ta.style.left = '-9999px';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
        showToast('✅ H5 链接已复制。系统会自动补采详情图。', 'success');
      });
    };
    document.body.appendChild(switchBtn);
  }

  function getItemIdFromUrl(url) {
    // 从桌面淘宝/天猫 URL 提取商品 ID
    var m;
    // ?id=12345678
    m = url.match(/[?&]id=(\d+)/);
    if (m) return m[1];
    // ?itemId=12345678
    m = url.match(/[?&]itemId=(\d+)/);
    if (m) return m[1];
    // /item.htm?id=xxx 或其他 path 格式
    m = url.match(/[?&]item_id=(\d+)/);
    if (m) return m[1];
    // URL path: /offer/xxx.html
    return null;
  }

  // ── Toast ───────────────────────────────────────────
  function showToast(msg, type) {
    let toast = document.getElementById('ozon-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'ozon-toast';
      document.body.appendChild(toast);
    }
    if (toastTimer) clearTimeout(toastTimer);
    toast.className = type;
    toast.textContent = msg;
    toast.style.display = 'block';
    toastTimer = setTimeout(function () { toast.style.display = 'none'; }, 3000);
  }

  // ── 结果面板 ────────────────────────────────────────
  function showResultPanel(data) {
    if (resultPanel) resultPanel.remove();

    const platformNames = { '1688': '1688', 'taobao': '淘宝', 'tmall': '天猫', 'pinduoduo': '拼多多', 'ozon_product': 'OZON' };
    const platformColors = { '1688': '#e6002b', 'taobao': '#ff4400', 'tmall': '#e6002b', 'pinduoduo': '#e6004c', 'ozon_product': '#005bff' };

    resultPanel = document.createElement('div');
    resultPanel.id = 'ozon-result-panel';

    var skuListHtml = '';
    if (data.skus && data.skus.length) {
      skuListHtml = '<div style="max-height:120px;overflow-y:auto;background:#f8f9fa;border-radius:6px;padding:8px;margin-bottom:10px;font-size:12px;">' +
        data.skus.slice(0, 15).map(function(s) {
          return '<div style="padding:2px 0;border-bottom:1px solid #eee;">' +
            (s.purchase_price_cny ? '<b>¥' + s.purchase_price_cny + '</b> — ' : '') +
            escHtml(s.source_sku_name) + '</div>';
        }).join('') +
        (data.skus.length > 15 ? '<div style="color:#999;text-align:center;padding-top:4px;">...还有 ' + (data.skus.length - 15) + ' 个SKU</div>' : '') +
        '</div>';
    }

    resultPanel.innerHTML =
      '<div class="panel-header">' +
        '<span>📦 商品信息</span>' +
        '<button class="panel-close" id="ozon-panel-close">✕</button>' +
      '</div>' +
      '<div class="panel-body">' +
        '<span style="display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:700;color:#fff;background:' + (platformColors[data.platform] || '#666') + ';margin-bottom:8px;">' + (platformNames[data.platform] || data.platform) + '</span>' +
        '<div class="panel-field"><div class="panel-value" style="font-size:15px;line-height:1.5;">' + escHtml(data.title || '未识别') + '</div></div>' +
        (data.category ? '<div class="panel-field"><div class="panel-label">类目</div><div class="panel-value">' + escHtml(data.category) + '</div></div>' : '') +
        (data.shop_name ? '<div class="panel-field"><div class="panel-label">供应商</div><div class="panel-value">' + escHtml(data.shop_name) + '</div></div>' : '') +
        '<div class="panel-stats">' +
          '<div class="panel-stat"><div class="panel-stat-num">' + data.sku_count + '</div><div class="panel-stat-label">SKU</div></div>' +
          '<div class="panel-stat"><div class="panel-stat-num">' + data.image_count + '</div><div class="panel-stat-label">图片</div></div>' +
          (data.spec_count > 0 ? '<div class="panel-stat"><div class="panel-stat-num">' + data.spec_count + '</div><div class="panel-stat-label">参数</div></div>' : '') +
        '</div>' +
        '<div style="font-size:11px;color:#6c757d;margin-bottom:6px;padding:0 4px">' +
          '📷 主图: ' + (data.images ? data.images.filter(function(i){return i.role==='main'}).length : 0) +
          ' | 🎨 SKU: ' + (data.images ? data.images.filter(function(i){return i.role==='sku'}).length : 0) +
          ' | 📝 详情: ' + (data.images ? data.images.filter(function(i){return i.role==='detail'}).length : 0) +
        '</div>' +
        '<div style="background:#f0f0f0;border-radius:4px;padding:4px 8px;margin-bottom:8px;font-size:10px;color:#666;line-height:1.6">' +
          '选择器: ' + (_extractDebug.whitelistHits || 0) +
          ' | 原始资源: ' + (_extractDebug.rawCandidates || 0) +
          ' | 缩略图: ' + (_extractDebug.thumbHits || 0) +
          ' | SKU补充: ' + (_extractDebug.skuBgHits || 0) +
          ' | 详情深采: ' + (_extractDebug.deepDetailHits || 0) +
          ' | 兜底: ' + (_extractDebug.fallbackHits || 0) +
          ' | 过滤: ' + ((_extractDebug.keywordFiltered || 0) + (_extractDebug.sizeFiltered || 0) + (_extractDebug.cdnFiltered || 0)) +
        '</div>' +
        (_extractDebug.roleWarning ? '<div style="background:#f8d7da;color:#842029;padding:6px 10px;border-radius:4px;margin-bottom:8px;font-size:11px;border:1px solid #f5c2c7">⚠️ ' + _extractDebug.roleWarning + '</div>' : '') +
        skuListHtml +
        ((data.platform === 'taobao' || data.platform === 'tmall') ? '<div style="background:#f0f0f0;color:#6c757d;padding:6px 10px;border-radius:4px;margin-bottom:8px;font-size:11px">ℹ️ 淘宝/天猫详情图请通过 1688 同款商品采集或手动上传</div>' : '') +
        ((data.quality_warnings && data.quality_warnings.length > 0) ? data.quality_warnings.map(function(w) { return '<div style="background:#fff3cd;color:#856404;padding:6px 10px;border-radius:4px;margin-bottom:6px;font-size:11px;border:1px solid #ffc107;">⚠️ ' + escHtml(w) + '</div>'; }).join('') : '') +
        '<button class="btn-collect" id="ozon-btn-submit">📥 一键采集入库</button>' +
        '<div id="ozon-submit-status" style="text-align:center;margin-top:8px;font-size:13px;"></div>' +
      '</div>';

    document.body.appendChild(resultPanel);

    // 用 addEventListener 替代内联 onclick（CSP 兼容）
    document.getElementById('ozon-panel-close').addEventListener('click', function () {
      if (resultPanel) resultPanel.remove();
    });
    document.getElementById('ozon-btn-submit').addEventListener('click', submitCollect);

    // 存储数据供提交使用
    window.__ozonExtractedData = data;
  }

  function escHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
  }

  // ── 提交入库 ────────────────────────────────────────
  async function submitCollect() {
    const data = window.__ozonExtractedData;
    if (!data) return;

    const btn = document.getElementById('ozon-btn-submit');
    const status = document.getElementById('ozon-submit-status');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>采集中...';
    status.textContent = '';

    const payload = {
      platform: data.platform,
      url: data.url,
      item_id: data.item_id || '',
      title: data.title,
      category: data.category || '',
      description: data.description || '',
      shop_name: data.shop_name || '',
      skus: data.skus || [],
      images: data.images || [],
      specs: data.specs || [],
      videos: data.videos || [],
      detail_missing: data.detail_missing || false,
      quality_warnings: data.quality_warnings || [],
      collect_source: 'browser_extension_pc'
    };

    try {
      // 通过 background service worker 发请求（绕过 HTTPS→HTTP 限制）
      const bgResult = await chrome.runtime.sendMessage({
        action: 'collect',
        apiUrl: API_URL,
        token: authToken,
        payload: payload
      });

      if (!bgResult || !bgResult.ok) {
        var errMsg = (bgResult && bgResult.error) || '连接失败';
        status.innerHTML = '<span style="color:#dc3545;">❌ ' + escHtml(errMsg) + '</span>';
        btn.disabled = false;
        btn.textContent = '📥 重试';
        showToast('连接失败: ' + errMsg, 'error');
        return;
      }

      var apiResult = bgResult.data;
      if (apiResult.ok) {
        var successParts = ['✅ 采集成功！'];
        var toastMsg = '✅ 采集成功！' + (data.title || '').substring(0, 30);

        if (apiResult.sku_gap_warning) {
          successParts.push('<span style="color:#e67e22">⚠️ ' + escHtml(apiResult.sku_gap_warning) + '</span>');
        }
        if (apiResult.detail_missing_warning) {
          successParts.push('<span style="color:#e67e22">⚠️ ' + escHtml(apiResult.detail_missing_warning) + '</span>');
        }

        status.innerHTML = '<span style="color:#198754;">' + successParts.join('<br>') + '</span>';
        btn.innerHTML = '✅ 已入库';
        showToast(toastMsg, 'success');
        setTimeout(function () {
          if (resultPanel) resultPanel.remove();
        }, apiResult.detail_missing_warning ? 4000 : 1500);
      } else {
        status.innerHTML = '<span style="color:#dc3545;">❌ ' + escHtml(apiResult.error || '未知错误') + '</span>';
        btn.disabled = false;
        btn.textContent = '📥 重试';
        showToast('采集失败: ' + (apiResult.error || '请检查系统是否运行'), 'error');
      }
    } catch (e) {
      status.innerHTML = '<span style="color:#dc3545;">❌ 无法连接本地服务，请确认系统运行在 127.0.0.1:5000</span>';
      btn.disabled = false;
      btn.textContent = '📥 重试';
      showToast('连接失败，请检查系统是否运行', 'error');
    }
  }

  // ═══════════════════════════════════════════════════════
  // 提取逻辑
  // ═══════════════════════════════════════════════════════

  function cleanText(text) {
    if (!text) return '';
    return text.replace(/[\s ]+/g, ' ').trim();
  }

  function fixImageUrl(src) {
    if (!src) return '';
    if (src.startsWith('http')) return src;
    if (src.startsWith('//')) return location.protocol + src;
    // 尝试处理 1688 的 //cbu01.alicdn.com/... 格式
    if (src.includes('alicdn.com') || src.includes('taobao.com') || src.includes('alicdn')) {
      return src.startsWith('http') ? src : 'https:' + (src.startsWith('//') ? src : '//' + src);
    }
    return src;
  }

  // ═══════════════════════════════════════════════════
  // 图片区域定义（按平台区分）
  // ═══════════════════════════════════════════════════
  var AREA_SELECTORS = {
    // 1688
    '1688': {
      main_gallery: [
        '.od-pic img', '.detail-gallery-img', '.main-image img',
        '.pic-box img', '.gallery-img', '.product-img img',
        '[data-mod-id="mainPic"] img', '.mod-detail-gallery img',
        '#J_ImgBooth', '.J_ImgBooth', '.offer-pic img',
        '.tab-content-container img[src*="alicdn"]',
      ],
      sku_panel: [
        '.table-sku img', '.sku-item-row img', '.unit-list .unit-item img',
        '.offer-sku-item img', '[data-mod-id="sku"] img',
        '.prop-list img', '.sku-prop-wrapper img',
      ],
      detail_content: [
        '#J_DivItemDesc img', '.offer-desc img', '.desc-content img',
        '[data-mod-id="description"] img', '.mod-detail-description img',
        '.detail-main-content img', '.detail-content img',
        '.detail-desc img', '.alife-detail img', '#J_DescContent img',
        '.content-detail img',
      ],
      shop_header: [
        '.shop-header img', '.shop-info img', '.company-info img',
        '[data-mod-id="shop"] img', '.offer-head img',
      ],
      floating: [
        '#J_StaticTools img', '.toolbar-wrap img', '.sidebar-tool img',
        '.float-bar img', '.back-to-top img',
      ],
      sidebar: [
        '.aside-recommend img', '.hot-sale img', '.related-items img',
      ],
      footer: [
        'footer img', '.footer img', '#footer img',
      ],
      nav: [
        'header img', '.header img', '#header img',
        '.nav img', '.top-nav img', '.site-nav img',
      ],
      ad: [
        '.ad img', '.banner img', '[class*="ad-"] img:not(.detail-content *)',
      ],
      certification: [
        '.vip img', '.cert img', '.auth img',
        'img[src*="cert"]', 'img[src*="vip"]', 'img[src*="auth"]',
        'img[src*="verify"]', 'img[src*="trust"]',
      ],
    },
    // 淘宝/天猫共用
    'taobao': {
      main_gallery: [
        '#J_ImgBooth', '.J_ImgBooth', '.tb-gallery img',
        '.tm-m-img img', '.main-image img', '.gallery-img',
        '[data-role="main-image"]', '.pic-box img',
        'meta[property="og:image"]',
        // 天猫新版/React 版容器
        '[class*="Pic"] img[src*="alicdn"]',
        '[class*="pic"] img[src*="alicdn"]',
        '[class*="Gallery"] img[src*="alicdn"]',
        '[class*="gallery"] img[src*="alicdn"]',
        '[class*="Main"] img[src*="alicdn"]',
        '[class*="main-image"] img',
        '[class*="image-viewer"] img',
        '[class*="ImageViewer"] img',
        '[class*="PicGallery"] img',
        '[class*="BasicContent"] img[src*="alicdn"]',
      ],
      sku_panel: [
        '.J_TSaleProp img', '.tb-sku img', '.tm-sale-prop img',
        '[class*="sku-prop"] img', '[class*="SkuProp"] img',
        '.prop-list img', '.sku-wrap img',
        'ul[data-property] img', '.J_SkuProp img',
        // 天猫新版 SKU 选择器
        '[class*="skuItem"] img', '[class*="SkuItem"] img',
        '[class*="salesProp"] img', '[class*="SalesProp"] img',
        '[class*="sku-item"] img',
        // 宽泛 SKU 选择器
        '[class*="sku"] img', '[class*="Sku"] img',
        '[data-sku] img', '[data-property] img',
      ],
      detail_content: [
        '#J_DcTop img', '#description img', '.J_DetailSection img',
        '[class*="descContent"] img', '[class*="detailContent"] img',
        '.detail-img img', '.desc-img img',
        // H5 移动端页面详情图容器
        '.apm-wrap img', '.apm-item img',
        '[class*="desc"] img', '[id*="desc"] img',
        '.content-img img', '.detail-image img',
        '.rax-view img[src*="alicdn"]',
        '.detail-desc img', '.descV2 img',
      ],
      shop_header: [
        '.slogo-shopname img', '.shop-info img', '.tb-shop-name img',
        '.J_ShopHeader img', '.shop-header img',
      ],
      floating: [
        '#J_StaticTools img', '.toolbar img', '.float-bar img',
        '.J_NavToolbar img', '.quick-tools img',
      ],
      sidebar: [
        '.aside img', '.recommend img', '.hot-sale img',
        '.guess-like img', '.related img',
      ],
      footer: [
        '#footer img', '.footer img', '.tb-footer img',
      ],
      nav: [
        '#header img', '.header img', '.tb-header img',
        '.nav img', '.site-nav img', '#site-nav img',
      ],
      ad: [
        '.ad img', '.banner img', '[class*="ad-"] img',
        '.dacu img', '.tmall-ensure img',
      ],
      certification: [
        '.vip img', '.cert img', '.auth img', '.tmall-cc img',
        'img[src*="cert"]', 'img[src*="vip"]', 'img[src*="auth"]',
        'img[src*="verify"]', 'img[src*="trust"]',
        'img[src*="tmallcc"]', 'img[src*="ensure"]',
      ],
    },
    'tmall': null, // 与 taobao 共用
    // 拼多多
    'pinduoduo': {
      main_gallery: [
        '.goods-img img', '.product-img img', '.gallery-img img',
        '.swiper-slide img', '.carousel img',
      ],
      sku_panel: [
        '.sku-item img', '.sku-cell img', '.group-item img',
        '.spec-item img', '.prop-item img',
      ],
      detail_content: [
        '.goods-detail img', '.detail-img img', '.desc-img img',
        '[class*="detail"] img',
      ],
      shop_header: [
        '.shop-header img', '.mall-name img', '.store-info img',
      ],
      floating: [
        '.float-btn img', '.toolbar img', '.back-top img',
      ],
      sidebar: [
        '.recommend img', '.guess img',
      ],
      footer: [
        'footer img', '.footer img',
      ],
      nav: [
        'header img', '.header img', '.nav img',
      ],
      ad: [
        '.ad img', '.banner img', '.coupon img',
      ],
      certification: [
        '.cert img', '.auth img', '.verify img',
      ],
    },
  };

  // ═══════════════════════════════════════════════════
  // 获取元素在 DOM 中简略的路径
  // ═══════════════════════════════════════════════════
  function getDomPath(el) {
    if (!el || el === document.body || el === document.documentElement) return '';
    var parts = [];
    var current = el;
    while (current && current !== document.body && current !== document.documentElement && parts.length < 5) {
      var tag = current.tagName ? current.tagName.toLowerCase() : '';
      if (!tag) { current = current.parentElement; continue; }
      var id = current.id;
      var cls = '';
      if (current.className && typeof current.className === 'string') {
        cls = current.className.trim().split(/\s+/).slice(0, 2).join('.');
      }
      if (id) {
        parts.unshift(tag + '#' + id);
      } else if (cls) {
        parts.unshift(tag + '.' + cls);
      } else {
        parts.unshift(tag);
      }
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  // ═══════════════════════════════════════════════════
  // 获取图片附近的文本（向上找父元素，最多 200 字）
  // ═══════════════════════════════════════════════════
  function getNearbyText(el) {
    var parent = el.parentElement;
    if (!parent) return '';
    var text = (parent.textContent || '').replace(/[\s\n\r]+/g, ' ').trim();
    return text.substring(0, 200);
  }

  // ═══════════════════════════════════════════════════
  // 新版采图 — 白名单区域优先 + 商品主体受控兜底
  // ═══════════════════════════════════════════════════

  // 明确非商品关键词（只保留绝对不会出现在商品图上的词）
  var HARD_REJECT_KEYWORDS = [
    '电子营业执照', '举报中心', '适老化', '可信网站', '身份验证',
    '知识产权', '扫黄打非', '88vip', '88VIP', '采购优选', '去开通',
    '二维码', '客服热线', '返回顶部', '回到顶部', '下载app', '下载APP',
  ];

  // 商品图 CDN 白名单（URL 中包含这些域名才视为商品图）
  var PRODUCT_CDN_HOSTS = ['alicdn.com', 'alicdn.net', 'tbcdn.cn', 'taobaocdn.com'];

  function isProductCdn(url) {
    if (!url) return false;
    var lower = url.toLowerCase();
    for (var i = 0; i < PRODUCT_CDN_HOSTS.length; i++) {
      if (lower.indexOf(PRODUCT_CDN_HOSTS[i]) !== -1) return true;
    }
    return false;
  }

  function hitHardRejectKeyword(alt, nearbyText, domPath) {
    var combined = ((alt || '') + ' ' + (nearbyText || '') + ' ' + (domPath || '')).toLowerCase();
    for (var i = 0; i < HARD_REJECT_KEYWORDS.length; i++) {
      if (combined.indexOf(HARD_REJECT_KEYWORDS[i].toLowerCase()) !== -1) return HARD_REJECT_KEYWORDS[i];
    }
    return null;
  }

  // 排除区域选择器（兜底扫描时跳过这些容器内的图片）
  var EXCLUDE_CONTAINER_SELS = [
    'header', '.header', '#header', '[class*="Header"]',
    'footer', '.footer', '#footer', '[class*="Footer"]',
    'nav', '.nav', '#nav', '.site-nav', '[class*="Nav"]',
    '.sidebar', '.aside', '[class*="Sidebar"]',
    '.floating', '.toolbar', '[class*="float"]', '[class*="Float"]',
    '.shop-info', '.shop-header', '[class*="ShopHeader"]', '[class*="shopHeader"]',
    '.slogo', '[class*="slogo"]',
    '.vip', '.cert', '.auth', '[class*="cert"]', '[class*="trust"]',
  ];

  // 调试统计（全局变量，供面板读取）
  var _extractDebug = { whitelistHits: 0, fallbackHits: 0, keywordFiltered: 0, sizeFiltered: 0, cdnFiltered: 0, thumbHits: 0, skuBgHits: 0 };

  // 阿里 CDN URL 去除尺寸后缀，获取最大清晰度版本
  function normalizeAlicdnUrl(url) {
    if (!url) return url;
    return url
      .replace(/_\d+x\d+[a-z]*(\.\w+)(\?.*)?$/, '$1')     // xxx_60x60.jpg → xxx.jpg
      .replace(/(\.\w+)_\d+x\d+[a-z]*\.\w+(\?.*)?$/, '$1') // xxx.jpg_60x60q90.jpg → xxx.jpg
      .replace(/_q\d+(\.\w+)$/, '$1')                       // xxx_q50.jpg → xxx.jpg
      .replace(/\?.*$/, '');                                 // 去掉查询参数（去重用）
  }

  function cleanImageToken(value) {
    if (!value) return '';
    var v = String(value).trim();
    v = v.replace(/&amp;/g, '&').replace(/\\u002F/g, '/').replace(/\\\//g, '/');
    v = v.replace(/^url\(["']?/, '').replace(/["']?\)$/, '');
    v = v.replace(/^["']|["']$/g, '').trim();
    if (v.indexOf(',') !== -1 && !/^data:/i.test(v)) v = v.split(',')[0].trim();
    if (v.indexOf(' ') !== -1 && /(?:https?:)?\/\//i.test(v)) v = v.split(/\s+/)[0].trim();
    if (v.indexOf('//') === 0) v = location.protocol + v;
    return v;
  }

  function pushImageUrl(list, value) {
    var v = cleanImageToken(value);
    if (!v || /^data:/i.test(v) || /\.svg(?:$|\?)/i.test(v) || /\.ico(?:$|\?)/i.test(v)) return;
    if (!/(?:https?:)?\/\/|alicdn|tbcdn|taobaocdn/i.test(v)) return;
    if (v.indexOf('//') === 0) v = location.protocol + v;
    list.push(v);
  }

  function extractUrlsFromText(text) {
    var urls = [];
    if (!text) return urls;
    var normalized = String(text).replace(/&amp;/g, '&').replace(/\\u002F/g, '/').replace(/\\\//g, '/');
    normalized.split(',').forEach(function(part) {
      pushImageUrl(urls, part);
    });
    var re = /(?:https?:)?\/\/(?:[^"'()<>\s,]+?)(?:jpg|jpeg|png|webp)(?:[^"'()<>\s,]*)?/gi;
    var m;
    while ((m = re.exec(normalized)) !== null) {
      pushImageUrl(urls, m[0]);
    }
    return urls;
  }

  function getElementImageUrls(el) {
    var urls = [];
    if (!el) return urls;
    pushImageUrl(urls, el.currentSrc || el.src || el.href || '');
    [
      'src', 'href', 'data-src', 'data-lazy-src', 'lazy-src',
      'data-ks-lazyload', 'data-original', 'data-img', 'data-imgurl',
      'data-image', 'data-url', 'data-thumb', 'data-zoom', 'content'
    ].forEach(function(attr) {
      pushImageUrl(urls, el.getAttribute && el.getAttribute(attr));
    });
    ['srcset', 'data-srcset'].forEach(function(attr) {
      extractUrlsFromText(el.getAttribute && el.getAttribute(attr)).forEach(function(u) { pushImageUrl(urls, u); });
    });
    if (el.attributes) {
      for (var ai = 0; ai < el.attributes.length; ai++) {
        var attrVal = el.attributes[ai] && el.attributes[ai].value ? el.attributes[ai].value : '';
        if (!attrVal || !/(alicdn|tbcdn|taobaocdn|\.jpg|\.jpeg|\.png|\.webp|url\()/i.test(attrVal)) continue;
        extractUrlsFromText(attrVal).forEach(function(u) { pushImageUrl(urls, u); });
      }
    }
    extractUrlsFromText(el.getAttribute && el.getAttribute('style')).forEach(function(u) { pushImageUrl(urls, u); });
    try {
      extractUrlsFromText(getComputedStyle(el).backgroundImage || '').forEach(function(u) { pushImageUrl(urls, u); });
    } catch(e) {}
    return Array.from(new Set(urls));
  }

  function inferImageArea(el) {
    if (!el) return 'unknown';
    var path = (getDomPath(el) + ' ' + getNearbyText(el)).toLowerCase();
    if (/sku|saleprop|salesprop|property|props|variant|spec/.test(path)) return 'sku_panel';
    if (/desc|detail|description|rich|content/.test(path)) return 'detail_content';
    if (/thumb|gallery|mainpic|main-pic|piclist|imageviewer|viewer/.test(path)) return 'main_gallery';
    return 'unknown';
  }

  function extractImages() {
    var platform = detectPlatform();
    var selectors = AREA_SELECTORS[platform] || AREA_SELECTORS['taobao'] || AREA_SELECTORS['1688'];
    _extractDebug = { whitelistHits: 0, fallbackHits: 0, rawCandidates: 0, keywordFiltered: 0, sizeFiltered: 0, cdnFiltered: 0, thumbHits: 0, skuBgHits: 0 };

    // 优先级：sku_panel > main_gallery > detail_content > unknown
    var AREA_PRIORITY = { 'sku_panel': 0, 'main_gallery': 1, 'detail_content': 2, 'unknown': 3 };
    var AREA_ROLE = { 'sku_panel': 'sku', 'main_gallery': 'main', 'detail_content': 'detail', 'unknown': 'unknown' };

    // 候选图 Map：normalizedUrl → candidate
    var candidateMap = {};

    function getSrc(el) {
      var urls = getElementImageUrls(el);
      return urls.length ? urls[0] : '';
    }

    function inferCandidateRole(sourceArea, el, src, altText, nearbyText, domPath) {
      if (sourceArea === 'sku_panel' || sourceArea === 'main_gallery' || sourceArea === 'detail_content') {
        return AREA_ROLE[sourceArea];
      }
      var text = [src || '', altText || '', nearbyText || '', domPath || ''].join(' ').toLowerCase();
      if (/desc|detail|description|rich|aplus|module|content|itemdesc|item-desc/.test(text)) return 'detail';
      if (/sku|saleprop|salesprop|prop-item|tb-sku|tm-sale-prop|data-sku|variant|spec/.test(text)) return 'sku';
      if (/main|gallery|thumb|piclist|mainpic|imageviewer|viewer|slide|carousel/.test(text)) return 'main';
      return 'unknown';
    }

    function isUiOnlyCandidate(role, sourceArea, src, w, h, altText, nearbyText, domPath) {
      var isProductArea = (sourceArea === 'sku_panel' || sourceArea === 'main_gallery' || sourceArea === 'detail_content');
      var srcText = String(src || '').toLowerCase();
      var pageText = [altText || '', nearbyText || '', domPath || ''].join(' ').toLowerCase();
      if (/logo|favicon|sprite|avatar|wangwang|qrcode|qr_code|shop-logo|seller-logo|tmallcc|kefu|loading|placeholder|blank|empty|grey|gray|spacer|mmstat|tbpc-ext/.test(srcText)) return true;
      if (!isProductArea && /营业执照|电子营业执照|违法|不良信息|举报中心|适老化|无障碍|可信网站|扫黄打非|88vip|去开通|购物车|收藏|客服|会员|优惠券|二维码|淘宝首页|天猫首页/.test(pageText)) return true;
      if (role === 'unknown') return true;
      if (w > 0 && h > 0 && w < 40 && h < 40) return true;
      if (role === 'sku' && w > 0 && h > 0 && w < 60 && h < 60) return true;
      if (role === 'detail' && w > 0 && h > 0 && w < 120 && h < 120) return true;
      return false;
    }

    // 注册候选图。同一张图多次命中时，高优先级区域覆盖低优先级。
    function registerCandidate(rawSrc, sourceArea, el) {
      if (!rawSrc || rawSrc.startsWith('data:') || rawSrc.endsWith('.svg') || rawSrc.endsWith('.ico')) return false;
      var fixed = fixImageUrl(rawSrc);
      if (!fixed) return false;
      var norm = normalizeAlicdnUrl(fixed);
      if (!norm) return false;

      var w = el ? (el.naturalWidth || el.width || parseInt(el.getAttribute('width')) || 0) : 0;
      var h = el ? (el.naturalHeight || el.height || parseInt(el.getAttribute('height')) || 0) : 0;
      var altText = el ? cleanText(el.alt || '') : '';
      var domPath = el ? getDomPath(el) : '';
      var nearbyText = el ? getNearbyText(el) : '';

      // 尺寸过滤：< 30px 绝对图标
      if (w > 0 && h > 0 && w < 30 && h < 30) { _extractDebug.sizeFiltered++; return false; }

      // 非商品关键词过滤 — 仅对未知区域执行，商品区域（SKU/主图/详情）不过滤
      // 原因：天猫页面 "88VIP" 等文本常出现在 SKU 区域附近，会误杀商品图
      var isProductArea = (sourceArea === 'sku_panel' || sourceArea === 'main_gallery' || sourceArea === 'detail_content');
      if (!isProductArea && hitHardRejectKeyword(altText, nearbyText, domPath)) {
        _extractDebug.keywordFiltered++;
        return false;
      }

      var role = inferCandidateRole(sourceArea, el, fixed, altText, nearbyText, domPath);
      if (isUiOnlyCandidate(role, sourceArea, fixed, w, h, altText, nearbyText, domPath)) {
        _extractDebug.keywordFiltered++;
        return false;
      }

      var priority = AREA_PRIORITY[sourceArea] !== undefined ? AREA_PRIORITY[sourceArea] : 3;

      if (candidateMap[norm]) {
        var existing = candidateMap[norm];
        // 高优先级区域覆盖角色
        if (priority < existing._pri) {
          existing._pri = priority;
          existing.source_area = sourceArea;
          existing.role = role;
        }
        if (w > existing.width) { existing.width = w; existing.height = h; }
        if (altText && !existing.alt) existing.alt = altText;
        return false; // 非新增
      }

      candidateMap[norm] = {
        role: role,
        src: fixed, alt: altText,
        source_area: sourceArea,
        dom_path: domPath,
        width: w, height: h,
        nearby_text: nearbyText,
        _pri: priority
      };
      return true;
    }

    function scanSelectors(selectorList, area, debugKey) {
      if (!selectorList || !selectorList.length) return;
      selectorList.forEach(function(sel) {
        try {
          var els = document.querySelectorAll(sel);
          for (var j = 0; j < els.length; j++) {
            getElementImageUrls(els[j]).forEach(function(url) {
              if (registerCandidate(url, area, els[j])) {
                _extractDebug[debugKey] = (_extractDebug[debugKey] || 0) + 1;
              }
            });
          }
        } catch(e) {}
      });
    }

    function scanImageUrlsInElementTree(root, area, debugKey, maxNodes) {
      if (!root) return 0;
      var added = 0;
      var nodes = [root];
      try {
        var found = root.querySelectorAll([
          'img', 'source', 'picture source',
          '[style*="background"]', '[style*="url("]',
          '[data-src]', '[data-img]', '[data-imgurl]', '[data-original]',
          '[data-ks-lazyload]', '[data-lazy-src]', '[lazy-src]',
          '[srcset]', '[data-srcset]', 'li', 'button', 'a', 'div', 'span'
        ].join(','));
        var limit = Math.min(found.length, maxNodes || 220);
        for (var ni = 0; ni < limit; ni++) nodes.push(found[ni]);
      } catch(e) {}
      for (var xi = 0; xi < nodes.length; xi++) {
        getElementImageUrls(nodes[xi]).forEach(function(url) {
          if (!url || !isProductCdn(url)) return;
          if (registerCandidate(url, area, nodes[xi])) {
            added++;
            _extractDebug[debugKey] = (_extractDebug[debugKey] || 0) + 1;
          }
        });
      }
      return added;
    }

    function isReasonableSkuRegion(el) {
      if (!el || el === document.body || el === document.documentElement) return false;
      var tag = (el.tagName || '').toLowerCase();
      if (/^(script|style|nav|header|footer)$/.test(tag)) return false;
      var count = 0;
      try { count = el.querySelectorAll('*').length; } catch(e) {}
      if (count > 520) return false;
      try {
        var rect = el.getBoundingClientRect();
        if (rect.width < 80 || rect.height < 20) return false;
        if (rect.top > window.innerHeight * 3.5) return false;
      } catch(e2) {}
      return true;
    }

    function scanSkuRegionsByLabels() {
      var scanned = new Set();
      var labelRe = /(颜色分类|颜色|屏幕尺寸|尺寸|规格|套餐|版本|型号|容量|款式|配置|内存|存储|组合|分类|选择)/;
      var clickableSel = 'li,button,a,[role="button"],[class*="sku"],[class*="Sku"],[class*="prop"],[class*="Prop"],[class*="option"],[class*="Option"],[data-sku],[data-value]';
      var labelNodes = [];
      try {
        labelNodes = Array.from(document.querySelectorAll('div,span,dt,dd,label,p,strong,b'));
      } catch(e) {}
      for (var li = 0; li < labelNodes.length; li++) {
        var txt = cleanText(labelNodes[li].textContent || '');
        if (!txt || txt.length > 80 || !labelRe.test(txt)) continue;
        var node = labelNodes[li];
        for (var depth = 0; depth < 6 && node; depth++, node = node.parentElement) {
          if (!isReasonableSkuRegion(node) || scanned.has(node)) continue;
          var optionCount = 0;
          var imageHitCount = 0;
          try { optionCount = node.querySelectorAll(clickableSel).length; } catch(e2) {}
          try {
            var maybeImages = node.querySelectorAll('img,[style*="background"],[style*="url("],[data-src],[data-img],[data-imgurl],[data-original],[data-ks-lazyload],[data-lazy-src],[lazy-src]');
            for (var mi = 0; mi < maybeImages.length; mi++) {
              if (getElementImageUrls(maybeImages[mi]).some(function(u) { return isProductCdn(u); })) imageHitCount++;
            }
          } catch(e3) {}
          if (optionCount < 2 && imageHitCount < 1) continue;
          scanned.add(node);
          scanImageUrlsInElementTree(node, 'sku_panel', 'skuBgHits', 280);
          var next = node.nextElementSibling;
          if (next && isReasonableSkuRegion(next) && !scanned.has(next)) {
            scanned.add(next);
            scanImageUrlsInElementTree(next, 'sku_panel', 'skuBgHits', 180);
          }
          break;
        }
      }
    }

    // ══════════════════════════════════════════════════
    // Phase 1: SKU 图（最高优先级，先占坑）
    // ══════════════════════════════════════════════════
    scanSelectors((selectors && selectors['sku_panel']) || [], 'sku_panel', 'whitelistHits');

    // SKU 增强：容器内 img + background-image（淘宝/天猫）
    if (platform === 'taobao' || platform === 'tmall') {
      var skuContainerSels = [
        '.J_TSaleProp li', '.tb-sku li', '.tm-sale-prop li',
        '[class*="sku-prop"] li', '[class*="SkuProp"] li',
        '[class*="skuItem"]', '[class*="SkuItem"]',
        '[class*="salesProp"] li', '[class*="SalesProp"] li',
        '[class*="sku-item"]', 'ul[data-property] li',
        '[class*="sku"] li', '[class*="Sku"] li',
        '[data-sku] li', '[data-property] li',
      ];
      skuContainerSels.forEach(function(sel) {
        try {
          var skuEls = document.querySelectorAll(sel);
          for (var s = 0; s < skuEls.length; s++) {
            // 容器内 img
            var innerImgs = skuEls[s].querySelectorAll('img');
            for (var ii = 0; ii < innerImgs.length; ii++) {
              getElementImageUrls(innerImgs[ii]).forEach(function(url) {
                if (registerCandidate(url, 'sku_panel', innerImgs[ii])) {
                  _extractDebug.skuBgHits++;
                }
              });
            }
            // background-image
            var bg = '';
            try { bg = getComputedStyle(skuEls[s]).backgroundImage || ''; } catch(e2) {}
            if (!bg || bg === 'none') {
              var inner = skuEls[s].querySelector('[style*="background"]');
              if (inner) { try { bg = getComputedStyle(inner).backgroundImage || ''; } catch(e3) {} }
            }
            if (bg && bg !== 'none') {
              extractUrlsFromText(bg).forEach(function(bgUrl) {
                if (isProductCdn(bgUrl) && registerCandidate(bgUrl, 'sku_panel', skuEls[s])) {
                  _extractDebug.skuBgHits++;
                }
              });
            }
          }
        } catch(e) {}
      });
      scanSkuRegionsByLabels();
    }

    // ══════════════════════════════════════════════════
    // Phase 2: 主图（选择器 + 缩略图条，限 5 张）
    // ══════════════════════════════════════════════════
    scanSelectors((selectors && selectors['main_gallery']) || [], 'main_gallery', 'whitelistHits');

    // 主图缩略图条补充（淘宝/天猫）
    if (platform === 'taobao' || platform === 'tmall') {
      var thumbSelectors = [
        '#J_UlThumb li img', '.tb-thumb img', '.tm-m-photos li img',
        '[class*="thumbnail"] img', '[class*="Thumbnail"] img',
        '[class*="thumbItem"] img', '[class*="ThumbItem"] img',
        '[class*="PicList"] li img', '[class*="picList"] li img',
        '[class*="pic-list"] li img', '[class*="thumb"] li img',
      ];
      thumbSelectors.forEach(function(sel) {
        try {
          var thumbEls = document.querySelectorAll(sel);
          for (var t = 0; t < thumbEls.length; t++) {
            getElementImageUrls(thumbEls[t]).forEach(function(thumbSrc) {
              if (!thumbSrc || !isProductCdn(thumbSrc)) return;
              if (registerCandidate(thumbSrc, 'main_gallery', thumbEls[t])) {
                _extractDebug.thumbHits++;
              }
            });
          }
        } catch(e) {}
      });
    }

    // ══════════════════════════════════════════════════
    // Phase 3: 详情图（仅 1688 和拼多多采集详情图）
    // ══════════════════════════════════════════════════
    if (platform === '1688' || platform === 'pinduoduo') {
      scanSelectors((selectors && selectors['detail_content']) || [], 'detail_content', 'whitelistHits');
    }

    // ══════════════════════════════════════════════════
    // Phase 3.5: 深度详情图 —— 从页面脚本中提取详情图 URL（仅 1688）
    // ══════════════════════════════════════════════════
    if (platform === '1688') {
      // 构建已知 URL 集合（所有已注册图片的原始+归一化 URL，严格去重）
      var allKnownUrls = new Set();
      for (var ck in candidateMap) {
        allKnownUrls.add(ck);
        allKnownUrls.add(candidateMap[ck].src);
        // 也加入去掉查询参数的版本
        allKnownUrls.add(candidateMap[ck].src.split('?')[0]);
      }

      var deepDetailUrls = new Set();

      // A) 仅从包含 desc/detail/description 的脚本中提取
      try {
        var allScripts = document.querySelectorAll('script:not([src])');
        for (var si = 0; si < allScripts.length; si++) {
          var sText = allScripts[si].textContent || '';
          if (sText.length < 200 || sText.length > 500000) continue;
          // 必须包含 desc/detail 相关关键词才处理
          if (!/descUrl|descImgs|detailImages|detailDesc|descContent|desc_url|desc_images|imageModule.*desc/i.test(sText)) continue;
          var scriptText = sText.replace(/\\\//g, '/');
          var imgUrlRe = /(?:https?:)?\/\/(?:img|gw|g|cbu\d{1,2})\.alicdn\.com\/[^\s"'<>)\]},]+\.(?:jpg|jpeg|png|webp)/gi;
          var urlMatches = scriptText.match(imgUrlRe);
          if (urlMatches) {
            for (var ui = 0; ui < urlMatches.length; ui++) {
              var rawU = urlMatches[ui];
              if (rawU.startsWith('//')) rawU = 'https:' + rawU;
              deepDetailUrls.add(rawU);
            }
          }
        }
      } catch(e) {}

      // B) performance 资源不直接归为 detail（误判率太高）

      // C) 注册候选（严格去重 + 过滤）
      var deepCount = 0;
      deepDetailUrls.forEach(function(rawUrl) {
        var fixed = fixImageUrl(rawUrl);
        if (!fixed) return;
        var norm = normalizeAlicdnUrl(fixed);
        if (!norm) return;
        // 严格去重：归一化 URL、原始 URL、去参数 URL 都检查
        if (candidateMap[norm] || allKnownUrls.has(norm) || allKnownUrls.has(fixed) || allKnownUrls.has(fixed.split('?')[0])) return;

        // 小缩略图跳过（SKU 缩略图通常有 _60x60 等后缀）
        var szMatch = rawUrl.match(/_(\d+)x(\d+)/);
        if (szMatch && parseInt(szMatch[1]) < 200 && parseInt(szMatch[2]) < 200) return;

        // 非商品图路径跳过
        var lower = rawUrl.toLowerCase();
        if (/logo|icon|avatar|banner|kefu|sprite|favicon|simba|tfscom|tmallcc|wangwang/.test(lower)) return;

        candidateMap[norm] = {
          role: 'detail', src: fixed, alt: '',
          source_area: 'detail_content',
          dom_path: 'script_desc_context',
          width: 0, height: 0, nearby_text: '',
          _pri: 2
        };
        deepCount++;
      });
      if (deepCount > 0) _extractDebug.deepDetailHits = deepCount;
    }

    // ══════════════════════════════════════════════════
    // 转换候选为数组
    // ══════════════════════════════════════════════════
    function runRawImageProbe() {
      var added = 0;
      var rawSelector = [
        'img', 'source', 'picture source',
        'meta[property="og:image"]',
        '[style*="background"]',
        '[data-src]', '[data-img]', '[data-imgurl]', '[data-original]',
        '[data-ks-lazyload]', '[data-lazy-src]', '[lazy-src]',
        '[srcset]', '[data-srcset]'
      ].join(',');

      try {
        var rawEls = document.querySelectorAll(rawSelector);
        for (var ri = 0; ri < rawEls.length; ri++) {
          var area = inferImageArea(rawEls[ri]);
          getElementImageUrls(rawEls[ri]).forEach(function(url) {
            var fixed = fixImageUrl(url);
            if (!fixed || !isProductCdn(fixed)) return;
            if (registerCandidate(fixed, area, rawEls[ri])) added++;
          });
        }
      } catch(e) {}

      try {
        if (platform !== 'taobao' && platform !== 'tmall' && performance && performance.getEntriesByType) {
          performance.getEntriesByType('resource').forEach(function(entry) {
            var url = entry && entry.name ? entry.name : '';
            if (!url || !isProductCdn(url)) return;
            if (!/\.(jpg|jpeg|png|webp)(?:$|\?)/i.test(url)) return;
            if (/logo|icon|avatar|banner|kefu|sprite|favicon|simba|tfscom|tmallcc|wangwang|loading|placeholder|blank|empty|grey|spacer/i.test(url)) return;
            if (registerCandidate(url, 'unknown', null)) added++;
          });
        }
      } catch(e) {}

      _extractDebug.rawCandidates = added;
    }

    runRawImageProbe();

    var imgs = [];
    for (var key in candidateMap) {
      var c = candidateMap[key];
      delete c._pri;
      imgs.push(c);
    }
    imgs = imgs.filter(function(m) {
      return m.role === 'main' || m.role === 'sku' || m.role === 'detail';
    });

    // ══════════════════════════════════════════════════
    // Phase 4: 受控兜底（仅候选为 0 时触发）
    // ══════════════════════════════════════════════════
    if (imgs.length === 0) {
      var containerCandidates = [
        '#detail', '#J_DetailMeta', '.tb-detail-hd',
        '[class*="ItemInfo"]', '[class*="itemInfo"]',
        '[class*="ProductInfo"]', '[class*="productInfo"]',
        '[class*="MainPic"]', '[class*="mainPic"]',
        '[class*="Pic-box"]', '[class*="pic-box"]',
        '[class*="Gallery"]', '[class*="gallery"]',
        '[class*="sku-prop"]', '[class*="SkuProp"]',
        '[class*="detailContent"]', '[class*="descContent"]',
        '#J_DcTop', '#description',
        'main', '[role="main"]',
        '.content', '#content', '.main-content',
      ];

      var excludeEls = new Set();
      EXCLUDE_CONTAINER_SELS.forEach(function(sel) {
        try { document.querySelectorAll(sel).forEach(function(el) { excludeEls.add(el); }); } catch(e) {}
      });
      function isInExcluded(el) {
        var node = el;
        while (node) { if (excludeEls.has(node)) return true; node = node.parentElement; }
        return false;
      }

      var fallbackSeen = new Set();
      containerCandidates.forEach(function(containerSel) {
        try {
          document.querySelectorAll(containerSel).forEach(function(container) {
            if (isInExcluded(container)) return;
            var containerImgs = container.querySelectorAll('img');
            for (var k = 0; k < containerImgs.length; k++) {
              var el = containerImgs[k];
              if (isInExcluded(el)) continue;
              var src = getSrc(el);
              var fixed = fixImageUrl(src);
              if (!fixed || fallbackSeen.has(fixed)) continue;
              if (!isProductCdn(fixed)) { _extractDebug.cdnFiltered++; continue; }
              fallbackSeen.add(fixed);

              var w = el.naturalWidth || el.width || parseInt(el.getAttribute('width')) || 0;
              var h = el.naturalHeight || el.height || parseInt(el.getAttribute('height')) || 0;
              if (w > 0 && h > 0 && w < 40 && h < 40) { _extractDebug.sizeFiltered++; continue; }

              var altText = cleanText(el.alt || '');
              if (hitHardRejectKeyword(altText, getNearbyText(el), getDomPath(el))) { _extractDebug.keywordFiltered++; continue; }

              var fbRole = (w >= 250 && h >= 250) ? 'main' : 'sku';
              var fbArea = (w >= 250 && h >= 250) ? 'main_gallery' : 'sku_panel';
              imgs.push({
                role: fbRole, src: fixed, alt: altText,
                source_area: fbArea, dom_path: getDomPath(el),
                width: w, height: h, nearby_text: getNearbyText(el)
              });
              _extractDebug.fallbackHits++;
            }
          });
        } catch(e) {}
      });
    }

    // ── 限制主图最多 8 张，不再把多出的主图降级成 SKU ─────────────────────────
    var mainKept = 0;
    imgs = imgs.filter(function(m) {
      if (m.role !== 'main') return true;
      mainKept++;
      return mainKept <= 8;
    });

    // ── 确保至少有一张主图 ──────────────────────────
    if (imgs.length > 0 && !imgs.some(function (m) { return m.role === 'main'; })) {
      var firstMainCandidate = imgs.find(function(m) { return m.role === 'sku'; }) || imgs[0];
      firstMainCandidate.role = 'main';
      firstMainCandidate.source_area = 'main_gallery';
    }

    // ── 质量校验：检测 SKU 图被误归类 ────────────────
    var finalSkuCount = imgs.filter(function(m) { return m.role === 'sku'; }).length;
    var finalDetailCount = imgs.filter(function(m) { return m.role === 'detail'; }).length;
    var finalMainCount = imgs.filter(function(m) { return m.role === 'main'; }).length;
    _extractDebug.finalRoles = { main: finalMainCount, sku: finalSkuCount, detail: finalDetailCount };
    if (finalSkuCount === 0 && finalDetailCount > 10) {
      _extractDebug.roleWarning = '疑似 SKU 图被误归类为详情图（SKU图=0, 详情图=' + finalDetailCount + '）';
    }

    return imgs.slice(0, 50);
  }

  // ══════════════════════════════════════════════════════════
  // 1688 专用采集函数（按区域精确采集，不用全页面扫描）
  // ══════════════════════════════════════════════════════════

  // ── 1688 图片过滤器 ─────────────────────────────────────
  var _1688_URL_REJECT_KEYWORDS = [
    'logo', 'icon', 'sprite', 'avatar', 'wangwang', 'kefu', 'favicon',
    'qrcode', 'qr_code', 'placeholder', 'loading', 'blank', 'empty',
    'grey', 'gray', 'spacer', 'mmstat', 'tbpc-ext', 'simba', 'tfscom',
    'tmallcc', 'shop-logo', 'seller-logo', 'shop_avatar', 'banner',
    'customer-service', 'cert', 'trust', 'verify', 'vip', 'auth',
    'coupon', 'dacu', 'service', 'guarantee', 'license', 'footer',
    'sidebar', 'nav', 'weixin', 'wechat', '_icon', '_logo'
  ];
  var _1688_TEXT_REJECT_KEYWORDS = [
    '质', '7天退', '电子营业执照', '可信网站', '举报中心', '营业执照',
    '身份验证', '知识产权', '扫黄打非', '客服热线', '采购优选', '品质保障',
    '正品保证', '退换无忧', '安全交易', '诚信通', '实力商家', '金牌卖家',
    '适老化', '无障碍', '违法', '不良信息', '88vip', '88VIP',
    '返回顶部', '回到顶部', '下载app', '下载APP', '二维码',
    '客服', '购物车', '收藏', '会员', '优惠券'
  ];

  function classify1688Image(url, el, context) {
    var area = (context && context.area) || 'unknown';

    // 1. 空 URL / data: / .svg / .ico
    if (!url || url.startsWith('data:') || url.endsWith('.svg') || url.endsWith('.ico')) {
      return { status: 'reject', reason: '无效URL或图标格式' };
    }

    // 2. URL 路径关键词
    var urlLower = url.toLowerCase();
    for (var ki = 0; ki < _1688_URL_REJECT_KEYWORDS.length; ki++) {
      if (urlLower.indexOf(_1688_URL_REJECT_KEYWORDS[ki]) !== -1) {
        return { status: 'reject', reason: 'URL含过滤词: ' + _1688_URL_REJECT_KEYWORDS[ki] };
      }
    }

    // 3. CDN 域名白名单
    if (!isProductCdn(url)) {
      return { status: 'reject', reason: '非商品CDN域名' };
    }

    // 4. 尺寸过滤
    if (el) {
      var w = el.naturalWidth || el.width || parseInt(el.getAttribute('width')) || 0;
      var h = el.naturalHeight || el.height || parseInt(el.getAttribute('height')) || 0;
      if (w > 0 && h > 0) {
        if (w < 30 && h < 30) return { status: 'reject', reason: '尺寸过小(<30x30)' };
        if (area === 'sku_panel' && w < 40 && h < 40) return { status: 'reject', reason: 'SKU图过小(<40x40)' };
        if (area === 'detail_content' && w < 100 && h < 100) return { status: 'reject', reason: '详情图过小(<100x100)' };
      }
    }

    // 5. 文本关键词过滤
    if (el) {
      var combined = ((el.alt || '') + ' ' + getNearbyText(el) + ' ' + getDomPath(el)).toLowerCase();
      for (var ti = 0; ti < _1688_TEXT_REJECT_KEYWORDS.length; ti++) {
        if (combined.indexOf(_1688_TEXT_REJECT_KEYWORDS[ti].toLowerCase()) !== -1) {
          // 产品区域命中 → review（可能误判）；其他区域 → reject
          if (area === 'main_gallery' || area === 'sku_panel' || area === 'detail_content') {
            return { status: 'review', reason: '产品区域文本含: ' + _1688_TEXT_REJECT_KEYWORDS[ti] };
          }
          return { status: 'reject', reason: '文本含过滤词: ' + _1688_TEXT_REJECT_KEYWORDS[ti] };
        }
      }
    }

    // 6. 缩略图后缀过滤
    var szMatch = url.match(/_(\d+)x(\d+)/);
    if (szMatch && parseInt(szMatch[1]) < 200 && parseInt(szMatch[2]) < 200) {
      return { status: 'reject', reason: '缩略图尺寸过小' };
    }

    return { status: 'accept', reason: '' };
  }

  // ── 1688 主图采集 ───────────────────────────────────────
  var _1688_MAIN_CONTAINERS = [
    // 新版 1688（Ant Design / React）
    '.module-od-picture-gallery', '.od-picture-gallery-section',
    '.od-gallery-preview', '.od-picture-gallery-list', '.od-gallery-list',
    // 旧版 1688
    '.od-pic', '#J_ImgBooth', '.detail-gallery', '.mod-detail-gallery',
    '[data-mod-id="mainPic"]', '.offer-pic', '.tab-content-container',
    '.pic-box', '.main-image', '.product-img', '.gallery-wrapper'
  ];
  var _LAZY_ATTRS = ['data-src', 'data-lazy-src', 'data-original', 'data-ks-lazyload', 'data-lazyload', 'data-zoom', 'data-img'];

  function extract1688MainImages() {
    var images = [];
    var seen = {};

    function addMain(rawUrl, el, selector, reason) {
      if (!rawUrl) return;
      var fixed = fixImageUrl(rawUrl);
      if (!fixed) return;
      var norm = normalizeAlicdnUrl(fixed);
      if (seen[norm]) return;

      var cls = classify1688Image(fixed, el, { area: 'main_gallery' });
      if (cls.status === 'reject') return;

      seen[norm] = true;
      images.push({
        src: fixed, role: 'main', source_area: 'main_gallery',
        source_selector: selector, reason: reason || '主图轮播区',
        linked_sku_name: null,
        width: el ? (el.naturalWidth || el.width || 0) : 0,
        height: el ? (el.naturalHeight || el.height || 0) : 0,
        alt: el ? (el.alt || '') : '',
        _classify: cls
      });
    }

    // 找到主图容器
    var container = null;
    var containerSel = '';
    for (var ci = 0; ci < _1688_MAIN_CONTAINERS.length; ci++) {
      container = document.querySelector(_1688_MAIN_CONTAINERS[ci]);
      if (container) { containerSel = _1688_MAIN_CONTAINERS[ci]; break; }
    }

    if (container) {
      // 1. 容器内 <img> src + lazyload
      container.querySelectorAll('img').forEach(function(img) {
        var src = img.src || '';
        if (src && src.length > 10) addMain(src, img, containerSel + ' img', '主图大图');
        for (var li = 0; li < _LAZY_ATTRS.length; li++) {
          var v = img.getAttribute(_LAZY_ATTRS[li]);
          if (v && v.length > 10) addMain(v, img, containerSel + ' img[' + _LAZY_ATTRS[li] + ']', '主图懒加载');
        }
      });

      // 2. 容器内 background-image
      container.querySelectorAll('li, a, div, span').forEach(function(el) {
        try {
          var bg = getComputedStyle(el).backgroundImage;
          var m = bg && bg.match(/url\(["']?(https?:\/\/[^"')]+)["']?\)/);
          if (m && m[1] && isProductCdn(m[1])) addMain(m[1], el, containerSel + ' [bg]', '主图CSS背景');
        } catch(e) {}
      });
    }

    // 3. 兜底：og:image
    if (images.length === 0) {
      var ogImg = document.querySelector('meta[property="og:image"]');
      if (ogImg) {
        var ogSrc = ogImg.getAttribute('content');
        if (ogSrc) addMain(ogSrc, null, 'meta[og:image]', '主图兜底(meta)');
      }
    }

    return images.slice(0, 10);
  }

  // ── 1688 SKU 图绑定辅助 ─────────────────────────────────
  function _getSkuItemImage(itemEl) {
    if (!itemEl) return null;
    // 1. 直接子 <img>
    var imgs = itemEl.querySelectorAll('img');
    for (var i = 0; i < imgs.length; i++) {
      var src = imgs[i].src || imgs[i].getAttribute('data-src') || imgs[i].getAttribute('data-lazy-src') || '';
      if (src && src.length > 10 && !src.startsWith('data:') && isProductCdn(src)) {
        return { url: fixImageUrl(src), el: imgs[i] };
      }
    }
    // 2. background-image
    var bgEls = [itemEl].concat(Array.from(itemEl.querySelectorAll('a, span, div')));
    for (var bi = 0; bi < bgEls.length; bi++) {
      try {
        var bg = getComputedStyle(bgEls[bi]).backgroundImage;
        var m = bg && bg.match(/url\(["']?(https?:\/\/[^"')]+)["']?\)/);
        if (m && m[1] && isProductCdn(m[1])) {
          return { url: fixImageUrl(m[1]), el: bgEls[bi] };
        }
      } catch(e) {}
    }
    return null;
  }

  // ── 1688 SKU 数据+图片采集 ──────────────────────────────
  var _1688_SKU_WRAPPERS = [
    // 新版 1688（Ant Design / React）
    '.module-od-sku-selection',
    // 旧版 1688
    '[data-mod-id="sku"]', '.mod-detail-sku', '.sku-prop-wrapper',
    '.prop-list-wrapper', '.table-sku', '[class*="sku-prop"]',
    '[data-mod-id="skuProps"]', '.sku-content'
  ];

  function extract1688SkuData() {
    var skus = [];
    var skuImages = [];
    var seenSkuNames = new Set();
    var seenImgUrls = {};
    var skuOrder = 0;

    function addSku(name, price, color, style, imgUrl, selector) {
      name = cleanText(name || '');
      if (!name || name.length < 1 || name.length > 100) return;
      if (/^(更多|全部|收起|展开)/.test(name)) return;
      if (seenSkuNames.has(name)) return;
      seenSkuNames.add(name);
      skuOrder++;
      skus.push({
        source_order: skuOrder,
        source_sku_name: name,
        purchase_price_cny: price || null,
        color_cn: color || name,
        style_cn: style || null,
        image_url: imgUrl || null,
        source_selector: selector || ''
      });
    }

    function addSkuImage(url, el, skuName, selector) {
      if (!url) return;
      var fixed = fixImageUrl(url);
      if (!fixed) return;
      var norm = normalizeAlicdnUrl(fixed);
      if (seenImgUrls[norm]) return;

      var cls = classify1688Image(fixed, el, { area: 'sku_panel' });
      if (cls.status === 'reject') return;

      seenImgUrls[norm] = true;
      skuImages.push({
        src: fixed, role: 'sku', source_area: 'sku_panel',
        source_selector: selector || '', reason: 'SKU "' + (skuName || '').substring(0, 20) + '" 绑定图',
        linked_sku_name: skuName || '',
        width: el ? (el.naturalWidth || el.width || 0) : 0,
        height: el ? (el.naturalHeight || el.height || 0) : 0,
        alt: el ? (el.alt || '') : '',
        _classify: cls
      });
    }

    // 策略 0: 新版 1688 feature-item 结构（Ant Design）
    var skuSelectionEl = document.querySelector('.module-od-sku-selection');
    if (skuSelectionEl) {
      var featureItems = skuSelectionEl.querySelectorAll('.feature-item');
      var featureGroups = [];    // Type A: 按钮型（颜色/款式选择）
      var tableSkuData = [];     // Type B: 表格型（规格/配置，含价格库存）

      featureItems.forEach(function(featureEl) {
        var labelEl = featureEl.querySelector('.feature-item-label');
        var dimName = labelEl ? cleanText(labelEl.textContent) : '';

        // ── Type A: 查找按钮型 SKU 选项 ──
        var buttons = featureEl.querySelectorAll('.sku-filter-button, [class*="sku-item"], [class*="prop-item"], button[class*="filter"], a[class*="filter"]');
        if (buttons.length === 0) {
          buttons = featureEl.querySelectorAll('button:not([class*="gallery"]), a[data-value], li[data-value]');
        }
        var values = [];
        var valueImages = {};
        buttons.forEach(function(btn) {
          if (btn === labelEl) return;
          var text = cleanText(btn.textContent);
          if (!text || text.length < 1 || text.length > 80) return;
          if (/^(更多|全部|收起|展开|请选择|颜色|规格|尺码|型号|款式)$/.test(text)) return;
          if (text === dimName) return;
          // 跳过含价格/库存的行（这些是 Type B 表格数据）
          if (/¥/.test(text)) return;
          values.push(text);
          var img = _getSkuItemImage(btn);
          if (img) valueImages[text] = img;
        });
        if (values.length > 0) {
          featureGroups.push({ name: dimName, values: values, images: valueImages });
        }

        // ── Type B: 查找表格型 SKU（在当前 feature-item 内部） ──
        if (values.length === 0) {
          // 方法 1: 真实 <table>
          var tableEl = featureEl.querySelector('table');
          if (tableEl) {
            tableEl.querySelectorAll('tr').forEach(function(row) {
              var cells = row.querySelectorAll('td, th');
              if (cells.length < 2) return;
              var rowName = cleanText(cells[0].textContent);
              if (!rowName || rowName.length < 2) return;
              if (/^(规格|价格|库存|起批|小计|合计|数量)$/.test(rowName)) return;
              var rowText = cleanText(row.textContent);
              var priceMatch = rowText.match(/¥\s*([\d.]+)/);
              var rowPrice = priceMatch ? parseFloat(priceMatch[1]) : null;
              var stockMatch = rowText.match(/(\d+)\s*(盒|件|个|包|套|箱|组|只)/);
              var rowStock = stockMatch ? parseInt(stockMatch[1]) : null;
              tableSkuData.push({ name: rowName, price: rowPrice, stock: rowStock });
            });
          }
          // 方法 2: div 模拟表格行（Ant Design 的 div-based table）
          if (tableSkuData.length === 0) {
            featureEl.querySelectorAll('[class*="sku-row"], [class*="spec-row"], [class*="sku-item-row"], [class*="field-row"]').forEach(function(row) {
              var rowText = cleanText(row.textContent);
              if (!rowText || rowText.length < 3) return;
              var priceMatch = rowText.match(/¥\s*([\d.]+)/);
              if (!priceMatch) return; // 必须含价格才算 SKU 行
              var nameMatch = rowText.match(/^([^¥]+)/);
              var rowName = nameMatch ? cleanText(nameMatch[1]) : '';
              if (!rowName || rowName.length < 2) return;
              var rowPrice = parseFloat(priceMatch[1]) || null;
              var stockMatch = rowText.match(/(\d+)\s*(盒|件|个|包|套|箱|组|只)/);
              var rowStock = stockMatch ? parseInt(stockMatch[1]) : null;
              tableSkuData.push({ name: rowName, price: rowPrice, stock: rowStock });
            });
          }
        }
      });

      console.log('[1688-DEBUG] Strategy0 featureGroups:', featureGroups.length, 'tableSkuData:', tableSkuData.length, JSON.stringify(tableSkuData));

      if (featureGroups.length > 0 || tableSkuData.length > 0) {
        skus = []; seenSkuNames.clear(); skuOrder = 0;

        if (tableSkuData.length > 1 && featureGroups.length > 0) {
          // 混合型：颜色按钮 + 规格表格 → 笛卡尔积，价格从表格取
          var colorGroup = featureGroups[0];
          tableSkuData.forEach(function(td) {
            colorGroup.values.forEach(function(colorVal) {
              var comboName = colorVal + ' / ' + td.name;
              var img = colorGroup.images[colorVal];
              var imgUrl = img ? img.url : null;
              addSku(comboName, td.price, colorVal, td.name, imgUrl, '.module-od-sku-selection combo');
              if (img) addSkuImage(img.url, img.el, comboName, '.module-od-sku-selection img');
            });
          });
        } else if (tableSkuData.length > 1) {
          // 纯表格型
          tableSkuData.forEach(function(td) {
            addSku(td.name, td.price, td.name, null, null, '.module-od-sku-selection table');
          });
          if (featureGroups.length > 0) {
            var colorGroup = featureGroups[0];
            for (var cv in colorGroup.images) {
              addSkuImage(colorGroup.images[cv].url, colorGroup.images[cv].el, cv, '.module-od-sku-selection img');
            }
          }
        } else if (featureGroups.length >= 2) {
          var combos = cartesianProduct(featureGroups.map(function(g) { return g.values; }));
          combos.forEach(function(combo) {
            var comboName = combo.join(' / ');
            var color = combo[0];
            var style = combo.length > 1 ? combo[1] : null;
            var img = featureGroups[0].images[color];
            var imgUrl = img ? img.url : null;
            addSku(comboName, null, color, style, imgUrl, '.module-od-sku-selection combo');
            if (img) addSkuImage(img.url, img.el, comboName, '.module-od-sku-selection img');
          });
        } else if (featureGroups.length === 1) {
          featureGroups[0].values.forEach(function(val) {
            var img = featureGroups[0].images[val];
            var imgUrl = img ? img.url : null;
            addSku(val, null, val, null, imgUrl, '.module-od-sku-selection single');
            if (img) addSkuImage(img.url, img.el, val, '.module-od-sku-selection img');
          });
        } else if (tableSkuData.length === 1) {
          addSku(tableSkuData[0].name, tableSkuData[0].price, tableSkuData[0].name, null, null, '.module-od-sku-selection table-single');
        }
      }
    }

    // 策略 1: 表格型 SKU（旧版，仅在策略 0 未命中时执行）
    if (skus.length === 0) {
    var tableRows = document.querySelectorAll('.table-sku tr, .sku-item-row, .unit-list .unit-item, .offer-sku-item, .mod-detail-sku tr, [data-mod-id="sku"] tr');
    tableRows.forEach(function(row) {
      var cells = row.querySelectorAll('td, .cell, .col, .prop-value');
      if (cells.length < 2) return;
      var name = cleanText(cells[0].textContent);
      var priceText = cleanText(cells[cells.length - 1].textContent);
      var price = parseFloat(priceText.replace(/[^0-9.]/g, '')) || null;
      if (!name || name.length < 1) return;

      var img = _getSkuItemImage(row);
      var imgUrl = img ? img.url : null;
      addSku(name, price, name, null, imgUrl, '.table-sku tr');
      if (img) addSkuImage(img.url, img.el, name, '.table-sku tr img');
    });
    } // end 策略 1 if

    // 策略 2: 属性组型（颜色/规格/尺码选择器）
    if (skus.length <= 1) {
      var groups = [];
      var wrapperEl = null;
      for (var wi = 0; wi < _1688_SKU_WRAPPERS.length; wi++) {
        wrapperEl = document.querySelector(_1688_SKU_WRAPPERS[wi]);
        if (wrapperEl) break;
      }

      if (wrapperEl) {
        var groupEls = wrapperEl.querySelectorAll('.prop-group, .prop-list, .prop-items, .sku-prop, dl');
        if (groupEls.length === 0) groupEls = [wrapperEl]; // 单组

        groupEls.forEach(function(groupEl) {
          var items = groupEl.querySelectorAll('li, .prop-item, span[data-value], a[data-value], dd a, dd span');
          var values = [];
          var valueImages = {};
          items.forEach(function(item) {
            var text = cleanText(item.textContent);
            if (!text || text.length < 1 || text.length > 40) return;
            if (/^(更多|全部|收起|展开|请选择)/.test(text)) return;
            // 去除导航/功能文字
            if (/^(首页|搜索|登录|注册|加入|关注|分享|举报|投诉)$/.test(text)) return;
            values.push(text);
            // 尝试绑定图片
            var img = _getSkuItemImage(item);
            if (img) valueImages[text] = img;
          });
          if (values.length > 0) groups.push({ values: values, images: valueImages });
        });
      }

      if (groups.length > 0) {
        // 清除策略 1 的单一结果
        if (skus.length === 1 && groups[0].values.length > 1) {
          skus = []; seenSkuNames.clear(); skuOrder = 0;
        }

        if (groups.length >= 2) {
          // 笛卡尔积
          var combos = cartesianProduct(groups.map(function(g) { return g.values; }));
          combos.forEach(function(combo) {
            var comboName = combo.join(' / ');
            var color = combo[0];
            var style = combo.length > 1 ? combo[1] : null;
            var img = groups[0].images[color]; // 图片绑定第一组（颜色）
            var imgUrl = img ? img.url : null;
            addSku(comboName, null, color, style, imgUrl, 'prop-group combo');
            if (img) addSkuImage(img.url, img.el, comboName, 'prop-group img');
          });
        } else {
          // 单组
          groups[0].values.forEach(function(val) {
            var img = groups[0].images[val];
            var imgUrl = img ? img.url : null;
            addSku(val, null, val, null, imgUrl, 'prop-group single');
            if (img) addSkuImage(img.url, img.el, val, 'prop-group img');
          });
        }
      }
    }

    // 策略 3: 价格兜底
    if (skus.length === 0) {
      var price = null;
      // 新版价格容器优先
      var priceEl = document.querySelector('.module-od-main-price, .od-price-container, .price-original, .mod-price .value, .offer-price, .price-range, .mod-detail-price .value');
      if (priceEl) {
        var pt = cleanText(priceEl.textContent);
        // 从文本中提取 ¥ 后面的价格数字
        var priceMatch = pt.match(/¥\s*([\d.]+)/);
        if (priceMatch) {
          price = parseFloat(priceMatch[1]) || null;
        } else {
          price = parseFloat(pt.replace(/[^0-9.\-~～]/g, '')) || null;
        }
      }
      if (!price) {
        var metaPrice = document.querySelector('meta[property="product:price:amount"], meta[itemprop="price"]');
        if (metaPrice) price = parseFloat(metaPrice.getAttribute('content')) || null;
      }
      // 不用 title 做 SKU name（避免公司名），用 "默认规格"
      addSku('默认规格', price, null, '标配', null, 'price-fallback');
    }

    return { skus: skus, skuImages: skuImages };
  }

  // ── 1688 详情图采集 ─────────────────────────────────────
  var _1688_DETAIL_CONTAINERS = [
    '#J_DivItemDesc', '.offer-desc', '[data-mod-id="description"]',
    '.desc-content', '.mod-detail-description', '.detail-main-content',
    '.detail-content', '.detail-desc', '.alife-detail', '#J_DescContent',
    '.content-detail'
  ];
  var _DETAIL_TEXT_REJECTS = [
    '质', '7天退', '退换', '保障', '认证', '可信', '举报',
    '实力商家', '诚信通', '安全交易', '正品保证', '品质保障',
    '退换无忧', '电子营业执照', '知识产权', '扫黄打非', '营业执照'
  ];

  function extract1688DetailImages() {
    var images = [];
    var seen = {};

    function addDetail(rawUrl, el, selector, reason) {
      if (!rawUrl) return;
      var fixed = fixImageUrl(rawUrl);
      if (!fixed) return;
      var norm = normalizeAlicdnUrl(fixed);
      if (seen[norm]) return;

      var cls = classify1688Image(fixed, el, { area: 'detail_content' });
      if (cls.status === 'reject') return;

      // 额外文本过滤（详情区服务图标）
      if (el) {
        var elText = ((el.alt || '') + ' ' + getNearbyText(el)).toLowerCase();
        for (var di = 0; di < _DETAIL_TEXT_REJECTS.length; di++) {
          if (elText.indexOf(_DETAIL_TEXT_REJECTS[di].toLowerCase()) !== -1) {
            return; // 静默过滤服务保障图标
          }
        }
      }

      seen[norm] = true;
      images.push({
        src: fixed, role: 'detail', source_area: 'detail_content',
        source_selector: selector || '', reason: reason || '详情区图片',
        linked_sku_name: null,
        width: el ? (el.naturalWidth || el.width || 0) : 0,
        height: el ? (el.naturalHeight || el.height || 0) : 0,
        alt: el ? (el.alt || '') : '',
        _classify: cls
      });
    }

    // 找详情容器
    var descEl = null;
    var descSel = '';
    for (var di = 0; di < _1688_DETAIL_CONTAINERS.length; di++) {
      descEl = document.querySelector(_1688_DETAIL_CONTAINERS[di]);
      if (descEl) { descSel = _1688_DETAIL_CONTAINERS[di]; break; }
    }

    if (descEl) {
      // 层 1: img + lazyload
      descEl.querySelectorAll('img').forEach(function(img) {
        var src = img.src || '';
        if (src && src.length > 10) addDetail(src, img, descSel + ' img', '详情img');
        for (var li = 0; li < _LAZY_ATTRS.length; li++) {
          var v = img.getAttribute(_LAZY_ATTRS[li]);
          if (v && v.length > 10) addDetail(v, img, descSel + ' img[' + _LAZY_ATTRS[li] + ']', '详情懒加载');
        }
      });

      // 层 2: CSS background-image
      descEl.querySelectorAll('[style*="background"]').forEach(function(el) {
        try {
          var bg = getComputedStyle(el).backgroundImage;
          var m = bg && bg.match(/url\(["']?(https?:\/\/[^"')]+)["']?\)/);
          if (m && m[1] && isProductCdn(m[1])) addDetail(m[1], el, descSel + ' [bg]', '详情CSS背景');
        } catch(e) {}
      });

      // 层 3: iframe 内容
      descEl.querySelectorAll('iframe').forEach(function(iframe) {
        try {
          var iDoc = iframe.contentDocument || (iframe.contentWindow && iframe.contentWindow.document);
          if (iDoc) {
            iDoc.querySelectorAll('img').forEach(function(img) {
              var src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
              addDetail(src, img, descSel + ' iframe img', '详情iframe');
            });
          }
        } catch(e) {} // 跨域静默
      });
    }

    // 层 4: 脚本嵌入 URL
    try {
      var allScripts = document.querySelectorAll('script:not([src])');
      for (var si = 0; si < allScripts.length; si++) {
        var sText = allScripts[si].textContent || '';
        if (sText.length < 200 || sText.length > 500000) continue;
        if (!/descUrl|descImgs|detailImages|detailDesc|descContent|desc_url|desc_images|imageModule/i.test(sText)) continue;
        var scriptText = sText.replace(/\\\//g, '/');
        var imgUrlRe = /(?:https?:)?\/\/(?:img|gw|g|cbu\d{1,2})\.alicdn\.com\/[^\s"'<>)\]},]+\.(?:jpg|jpeg|png|webp)/gi;
        var urlMatches = scriptText.match(imgUrlRe);
        if (urlMatches) {
          for (var ui = 0; ui < urlMatches.length; ui++) {
            var rawU = urlMatches[ui];
            if (rawU.startsWith('//')) rawU = 'https:' + rawU;
            addDetail(rawU, null, 'script[desc]', '脚本详情URL');
          }
        }
      }
    } catch(e) {}

    return images.slice(0, 40);
  }

  // ── 1688 总调度器 ───────────────────────────────────────
  function extract1688Product() {
    // ═══ 调试：自动扫描页面 DOM 结构 ═══
    try {
      console.log('%c[1688-DEBUG] ═══ 页面 DOM 结构扫描 ═══', 'color:#ff6600;font-weight:bold');

      // 1. 扫描所有已知容器选择器
      var debugSels = {
        '主图': _1688_MAIN_CONTAINERS,
        'SKU': _1688_SKU_WRAPPERS,
        '详情': _1688_DETAIL_CONTAINERS
      };
      for (var area in debugSels) {
        debugSels[area].forEach(function(s) {
          var el = document.querySelector(s);
          if (el) console.log('[1688-DEBUG] ✅ ' + area + ' 命中: ' + s, '→', el.className.substring(0,80));
          else console.log('[1688-DEBUG] ❌ ' + area + ' 未命中: ' + s);
        });
      }

      // 2. 扫描页面大图（前 10 个 >200px 的 img）
      var allImgs = document.querySelectorAll('img');
      var bigImgs = [];
      allImgs.forEach(function(img) {
        var w = img.naturalWidth || img.width || 0;
        var h = img.naturalHeight || img.height || 0;
        if ((w > 200 || h > 200) && bigImgs.length < 10) {
          bigImgs.push({ w:w, h:h, src:(img.src||'').substring(0,80), parent: (img.parentElement ? img.parentElement.className : '').substring(0,60) });
        }
      });
      console.log('[1688-DEBUG] 页面大图(>200px)前10个:', JSON.stringify(bigImgs, null, 1));

      // 3. 找含 SKU 相关文字的容器
      var skuTexts = ['颜色', '规格', '尺码', '尺寸', '型号', '款式', '套餐', '版本', '配置'];
      skuTexts.forEach(function(kw) {
        var found = [];
        document.querySelectorAll('dt, label, th, .label, [class*="label"], [class*="prop"]').forEach(function(el) {
          if (el.textContent.indexOf(kw) >= 0 && el.textContent.length < 30) {
            var parent = el.parentElement;
            var grandparent = parent ? parent.parentElement : null;
            found.push({
              text: el.textContent.trim().substring(0,20),
              tag: el.tagName,
              class: el.className.substring(0,50),
              parentClass: parent ? parent.className.substring(0,50) : '',
              grandparentClass: grandparent ? grandparent.className.substring(0,50) : ''
            });
          }
        });
        if (found.length) console.log('[1688-DEBUG] SKU关键词"' + kw + '"命中:', JSON.stringify(found, null, 1));
      });

      // 4. 找含价格的元素
      var priceEls = [];
      document.querySelectorAll('[class*="price"], [class*="Price"], .price, .mod-price').forEach(function(el) {
        if (priceEls.length < 5) {
          priceEls.push({ tag:el.tagName, class:el.className.substring(0,60), text:el.textContent.trim().substring(0,30) });
        }
      });
      console.log('[1688-DEBUG] 价格元素:', JSON.stringify(priceEls, null, 1));

      // 5. 页面所有顶层容器 class（辅助定位主图/SKU）
      var topContainers = [];
      document.querySelectorAll('[class*="gallery"], [class*="Gallery"], [class*="sku"], [class*="Sku"], [class*="SKU"], [class*="detail-gallery"], [class*="main-image"], [class*="MainImage"], [class*="ImageView"], [class*="image-view"], [class*="thumb"], [class*="Thumb"]').forEach(function(el) {
        topContainers.push({ tag:el.tagName, class:el.className.substring(0,80), children: el.children.length });
      });
      console.log('[1688-DEBUG] gallery/sku/thumb容器:', JSON.stringify(topContainers, null, 1));

      console.log('%c[1688-DEBUG] ═══ 扫描结束 ═══', 'color:#ff6600;font-weight:bold');
    } catch(e) {
      console.log('[1688-DEBUG] 调试出错:', e.message);
    }

    // ═══════════════════════════════════════════════════════
    // 优先策略：从页面全局 JS 变量提取结构化数据
    // ═══════════════════════════════════════════════════════
    var _jsonData = null;
    try {
      // 注入脚本到页面上下文读取全局变量（content script 无法直接访问 window.viewData）
      var _dataEl = document.getElementById('__1688_extracted__');
      if (_dataEl) _dataEl.remove();
      _dataEl = document.createElement('div');
      _dataEl.id = '__1688_extracted__';
      _dataEl.style.display = 'none';
      document.documentElement.appendChild(_dataEl);

      var _injScript = document.createElement('script');
      _injScript.textContent = '(function(){' +
        'var KEYS=["viewData","__INIT_DATA__","__INIT_STATE__","iDetailConfig","iDetailData","detailData","__data__","pageData","offerDetailData","globalData","__detailData__","_w_detailData"];' +
        'var found=null,foundKey="";' +
        'for(var i=0;i<KEYS.length;i++){try{var v=window[KEYS[i]];if(v&&typeof v==="object"){found=v;foundKey=KEYS[i];break;}}catch(e){}}' +
        // 如果没找到命名变量，搜索 script 标签中的 JSON 数据
        'if(!found){' +
        '  var scripts=document.querySelectorAll("script:not([src])");' +
        '  for(var s=0;s<scripts.length;s++){' +
        '    var t=scripts[s].textContent||"";' +
        '    if(t.length<500||t.length>500000)continue;' +
        // 搜索 window.viewData = {...} 或 var detailData = {...} 模式
        '    var m=t.match(/(?:window\\.)?(viewData|detailData|iDetailData|offerDetail|__INIT_DATA__)\\s*=\\s*(\\{[\\s\\S]{100,}?\\});/);' +
        '    if(m){try{found=JSON.parse(m[2]);foundKey="script:"+m[1];break;}catch(e){}}' +
        '    m=t.match(/(?:window\\.)?(viewData|detailData|iDetailData|offerDetail|__INIT_DATA__)\\s*=\\s*(\\{[\\s\\S]{100,})$/m);' +
        '    if(m){try{var chunk=m[2].replace(/;\\s*$/,"");found=JSON.parse(chunk);foundKey="script:"+m[1];break;}catch(e){}}' +
        '  }' +
        '}' +
        // 递归搜索产品数据
        'var result={_source:foundKey,title:"",images:[],skuProps:[],skuMap:null,priceRange:null};' +
        'if(found){' +
        '  function dig(obj,depth){' +
        '    if(!obj||depth>6||typeof obj!=="object")return;' +
        '    for(var k in obj){' +
        '      if(!obj.hasOwnProperty(k))continue;' +
        '      var v=obj[k];' +
        // 标题
        '      if(!result.title&&(k==="subject"||k==="title"||k==="offerTitle")&&typeof v==="string"&&v.length>5&&v.length<200){result.title=v;}' +
        // 主图数组
        '      if(result.images.length===0&&(k==="images"||k==="imageList"||k==="imgList"||k==="mainImages"||k==="productImage")&&Array.isArray(v)&&v.length>=2){' +
        '        for(var ii=0;ii<v.length;ii++){' +
        '          var imgUrl=typeof v[ii]==="string"?v[ii]:(v[ii]&&(v[ii].fullPathImageURI||v[ii].imageUrl||v[ii].originalImageURI||v[ii].url||v[ii].src||""));' +
        '          if(imgUrl&&imgUrl.indexOf("alicdn")!==-1)result.images.push(imgUrl);' +
        '        }' +
        '      }' +
        // SKU 属性组
        '      if((k==="skuProps"||k==="skuProperties"||k==="productFeature")&&Array.isArray(v)&&v.length>0){result.skuProps=v;}' +
        // SKU 映射（价格/库存）
        '      if((k==="skuMap"||k==="skuPriceMap"||k==="skuInfoMap")&&typeof v==="object"&&!Array.isArray(v)){result.skuMap=v;}' +
        // 价格范围
        '      if((k==="priceRange"||k==="priceRangeOriginal")&&typeof v==="object"&&v!==null){result.priceRange=v;}' +
        // 递归
        '      if(typeof v==="object")dig(v,depth+1);' +
        '    }' +
        '  }' +
        '  dig(found,0);' +
        '}' +
        'try{document.getElementById("__1688_extracted__").textContent=JSON.stringify(result);}catch(e){document.getElementById("__1688_extracted__").textContent="{}"}' +
      '})();';
      document.documentElement.appendChild(_injScript);
      _injScript.remove();

      var _rawJson = _dataEl.textContent;
      _dataEl.remove();
      if (_rawJson && _rawJson !== '{}') {
        _jsonData = JSON.parse(_rawJson);
        console.log('%c[1688-JSON] 全局变量提取结果:', 'color:#0099ff;font-weight:bold', _jsonData._source,
          '标题:', (_jsonData.title || '').substring(0, 30),
          '主图:', _jsonData.images.length,
          'skuProps:', _jsonData.skuProps.length,
          'skuMap:', _jsonData.skuMap ? Object.keys(_jsonData.skuMap).length : 0,
          'priceRange:', JSON.stringify(_jsonData.priceRange));
      } else {
        console.log('[1688-JSON] 未找到全局变量数据，将使用 DOM 提取');
      }
    } catch(e) {
      console.log('[1688-JSON] 全局变量提取出错:', e.message);
    }

    // ═══════════════════════════════════════════════════════
    // 如果 JSON 提取到了完整数据，直接使用（跳过 DOM）
    // ═══════════════════════════════════════════════════════
    if (_jsonData && _jsonData.title && _jsonData.images.length >= 2) {
      console.log('%c[1688-JSON] ✅ 使用 JSON 数据路径', 'color:#00cc00;font-weight:bold');

      var title = cleanText(_jsonData.title);
      var category = '';
      var breadcrumb = document.querySelector('.offer-breadcrumb, .breadcrumb, .crumbs, [data-mod-id="breadcrumb"]');
      if (breadcrumb) category = cleanText(breadcrumb.textContent).replace(/[\n\r]+/g, ' > ').replace(/\s+/g, ' ').substring(0, 200);

      var shop = '';
      var shopEl = document.querySelector('.shop-name, .company-name, .offer-shop-name, [data-mod-id="shop"] .name, .supplier-name');
      if (shopEl) shop = cleanText(shopEl.textContent).substring(0, 100);

      // ── 主图（从 JSON） ──
      var mainImages = [];
      var mainSeen = {};
      _jsonData.images.forEach(function(rawUrl, idx) {
        if (!rawUrl) return;
        var url = rawUrl.startsWith('//') ? 'https:' + rawUrl : rawUrl;
        url = fixImageUrl(url);
        if (!url) return;
        var norm = normalizeAlicdnUrl(url);
        if (mainSeen[norm]) return;
        mainSeen[norm] = true;
        mainImages.push({
          src: url, role: 'main', source_area: 'main_gallery',
          source_selector: 'JSON:images[' + idx + ']', reason: 'JSON主图',
          linked_sku_name: null, width: 800, height: 800, alt: ''
        });
      });

      // ── SKU（从 JSON skuProps + skuMap） ──
      var skus = [];
      var skuImages = [];
      var seenSkuNames = new Set();
      var skuOrder = 0;

      if (_jsonData.skuProps && _jsonData.skuProps.length > 0) {
        // 提取每个维度的值和图片
        var skuGroups = [];
        _jsonData.skuProps.forEach(function(prop) {
          var propName = prop.propName || prop.fid || prop.name || '';
          var values = prop.value || prop.values || prop.features || [];
          var group = { name: propName, values: [], images: {}, ids: {} };
          values.forEach(function(v) {
            var vName = v.name || v.value || v.showName || '';
            var vImg = v.imageUrl || v.imgUrl || v.image || v.fullPathImageURI || '';
            var vId = v.propValueId || v.vid || v.id || '';
            if (vName) {
              group.values.push(vName);
              if (vImg) {
                var imgUrl = vImg.startsWith('//') ? 'https:' + vImg : vImg;
                group.images[vName] = { url: fixImageUrl(imgUrl), el: null };
              }
              if (vId) group.ids[vName] = String(vId);
            }
          });
          if (group.values.length > 0) skuGroups.push(group);
        });

        // 从 skuMap 获取价格/库存
        var skuMapData = _jsonData.skuMap || {};

        if (skuGroups.length >= 2) {
          // 多维度：笛卡尔积
          var combos = cartesianProduct(skuGroups.map(function(g) { return g.values; }));
          combos.forEach(function(combo) {
            skuOrder++;
            var comboName = combo.join(' / ');
            if (seenSkuNames.has(comboName)) return;
            seenSkuNames.add(comboName);
            // 尝试从 skuMap 获取价格
            var price = null;
            var comboIds = combo.map(function(val, gi) { return skuGroups[gi].ids[val] || ''; }).join(';');
            var mapEntry = skuMapData[comboIds] || skuMapData[comboName];
            if (mapEntry) {
              price = mapEntry.price || mapEntry.discountPrice || mapEntry.salePrice || null;
            }
            var img = skuGroups[0].images[combo[0]];
            skus.push({
              source_order: skuOrder, source_sku_name: comboName,
              purchase_price_cny: price, color_cn: combo[0],
              style_cn: combo.length > 1 ? combo[1] : null,
              image_url: img ? img.url : null, source_selector: 'JSON:skuProps'
            });
            if (img) skuImages.push({
              src: img.url, role: 'sku', source_area: 'sku_panel',
              source_selector: 'JSON:skuProps.imageUrl', reason: 'SKU "' + combo[0].substring(0, 20) + '" JSON绑定图',
              linked_sku_name: comboName, width: 0, height: 0, alt: ''
            });
          });
        } else if (skuGroups.length === 1) {
          // 单维度
          skuGroups[0].values.forEach(function(val) {
            skuOrder++;
            if (seenSkuNames.has(val)) return;
            seenSkuNames.add(val);
            var mapEntry = skuMapData[skuGroups[0].ids[val] || ''] || skuMapData[val];
            var price = mapEntry ? (mapEntry.price || mapEntry.discountPrice || null) : null;
            var img = skuGroups[0].images[val];
            skus.push({
              source_order: skuOrder, source_sku_name: val,
              purchase_price_cny: price, color_cn: val, style_cn: null,
              image_url: img ? img.url : null, source_selector: 'JSON:skuProps'
            });
            if (img) skuImages.push({
              src: img.url, role: 'sku', source_area: 'sku_panel',
              source_selector: 'JSON:skuProps.imageUrl', reason: 'SKU "' + val.substring(0, 20) + '" JSON绑定图',
              linked_sku_name: val, width: 0, height: 0, alt: ''
            });
          });
        }
      }

      // SKU 兜底：如果 JSON 没有 skuProps，尝试 DOM
      if (skus.length === 0) {
        var domSkuResult = extract1688SkuData();
        skus = domSkuResult.skus;
        skuImages = domSkuResult.skuImages;
      }

      // 价格兜底：如果 SKU 都没有价格，从 priceRange 取
      if (_jsonData.priceRange && skus.length > 0) {
        var hasPrice = skus.some(function(s) { return s.purchase_price_cny; });
        if (!hasPrice) {
          var rangePrice = _jsonData.priceRange.price || _jsonData.priceRange.min || _jsonData.priceRange.beginAmount || null;
          if (rangePrice) {
            skus.forEach(function(s) { if (!s.purchase_price_cny) s.purchase_price_cny = parseFloat(rangePrice); });
          }
        }
      }

      // 如果完全没有 SKU，用价格兜底
      if (skus.length === 0) {
        var fbPrice = null;
        if (_jsonData.priceRange) {
          fbPrice = parseFloat(_jsonData.priceRange.price || _jsonData.priceRange.min || _jsonData.priceRange.beginAmount) || null;
        }
        if (!fbPrice) {
          var priceEl = document.querySelector('.module-od-main-price, .od-price-container');
          if (priceEl) {
            var pm = cleanText(priceEl.textContent).match(/¥\s*([\d.]+)/);
            if (pm) fbPrice = parseFloat(pm[1]);
          }
        }
        skus.push({ source_order: 1, source_sku_name: '默认规格', purchase_price_cny: fbPrice,
                     color_cn: null, style_cn: '标配', image_url: null, source_selector: 'JSON:fallback' });
      }

      // ── 规格参数（仍用 DOM） ──
      var specs = [];
      document.querySelectorAll('.mod-detail-attributes tr, .attribute-list li, [data-mod-id="attributes"] .attr-item, .offer-attr tr, .product-features li, .detail-attributes .attr-row, .ant-descriptions-row').forEach(function(row) {
        var cells = row.querySelectorAll('td, th, .attr-name, .attr-value, .label, .value, .name, .val, .ant-descriptions-item-label, .ant-descriptions-item-content');
        if (cells.length >= 2) {
          var name = cleanText(cells[0].textContent);
          var value = cleanText(cells[1].textContent);
          if (name && value && name.length < 30 && value.length < 200) {
            if (!specs.some(function(s) { return s.name === name; })) {
              specs.push({ name: name, value: value });
            }
          }
        }
      });

      // ── 描述 ──
      var desc = '';
      var descEl = document.querySelector('.offer-desc, [data-mod-id="description"], #J_DivItemDesc, .desc-content, .mod-detail-description, .detail-main-content');
      if (descEl) desc = cleanText(descEl.textContent).substring(0, 3000);

      // ── 详情图（仍用 DOM + 脚本提取） ──
      var detailImages = extract1688DetailImages();

      // ── 合并图片 ──
      var allSeen = {};
      var allImages = [];
      function mergeImgs(arr) {
        for (var i = 0; i < arr.length; i++) {
          var norm = normalizeAlicdnUrl(arr[i].src);
          if (!allSeen[norm]) { allSeen[norm] = true; allImages.push(arr[i]); }
        }
      }
      mergeImgs(mainImages);
      mergeImgs(skuImages);
      mergeImgs(detailImages);

      // ── 标题验证 ──
      var _titleWarning = '';
      if (shop && title && (title === shop || title.indexOf(shop) >= 0)) {
        _titleWarning = '标题疑似供应商名称';
      }
      if (/有限公司|官方旗舰店|实力商家|金牌卖家/.test(title)) {
        _titleWarning = '标题含公司名特征';
      }

      // ── 质量检查 ──
      var warnings = [];
      if (skus.length > 0 && skuImages.length === 0) warnings.push('SKU 图未绑定');
      if (detailImages.length === 0) warnings.push('未采集到详情图');
      if (mainImages.length === 0) warnings.push('未采集到主图');
      if (_titleWarning) warnings.push(_titleWarning);

      console.log('%c[1688-RESULT] JSON路径采集结果:', 'color:#00cc00;font-weight:bold');
      console.log('[1688-RESULT] 主图:', mainImages.length, '张');
      console.log('[1688-RESULT] SKU:', skus.length, '个', skus.map(function(s) { return s.source_sku_name + ' ¥' + s.purchase_price_cny; }));
      console.log('[1688-RESULT] SKU图:', skuImages.length, '张');
      console.log('[1688-RESULT] 详情图:', detailImages.length, '张');

      return {
        title: title, category: category, shopName: shop, description: desc,
        images: allImages, skus: skus, specs: specs,
        _qualityWarnings: warnings,
        _debug: { mainImageCount: mainImages.length, skuImageCount: skuImages.length,
                  detailImageCount: detailImages.length, skuCount: skus.length, dataSource: 'JSON:' + (_jsonData._source || '') }
      };
    }

    // ═══════════════════════════════════════════════════════
    // 兜底策略：JSON 提取失败，使用 DOM 选择器
    // ═══════════════════════════════════════════════════════
    console.log('[1688-JSON] ⚠️ JSON 数据不完整，回退到 DOM 提取');

    // ── 标题（改进：去掉裸 h1，避免取到公司名） ──────
    var title = '';
    var titleEl = document.querySelector('.offer-title-text, [data-mod-id="title"] h1, .title-text, h1[data-till]');
    if (titleEl) title = cleanText(titleEl.textContent);
    if (!title) {
      // 兜底：document.title 去后缀
      title = cleanText(document.title)
        .replace(/\s*[-_|–—]\s*(1688\.com|阿里巴巴|Alibaba).*$/i, '')
        .replace(/\s*-\s*[^-]*1688[^-]*$/, '');
    }

    // ── 类目 ─────────────────────────────────────────
    var category = '';
    var breadcrumb = document.querySelector('.offer-breadcrumb, .breadcrumb, .crumbs, [data-mod-id="breadcrumb"]');
    if (breadcrumb) category = cleanText(breadcrumb.textContent).replace(/[\n\r]+/g, ' > ').replace(/\s+/g, ' ').substring(0, 200);

    // ── 店铺 ─────────────────────────────────────────
    var shop = '';
    var shopEl = document.querySelector('.shop-name, .company-name, .offer-shop-name, [data-mod-id="shop"] .name, .supplier-name');
    if (shopEl) shop = cleanText(shopEl.textContent).substring(0, 100);

    // ── 标题验证：不能等于店铺名 ─────────────────────
    var _titleWarning = '';
    if (shop && title && (title === shop || title.indexOf(shop) >= 0)) {
      // 尝试 og:title 兜底
      var ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogTitle) {
        var ogText = cleanText(ogTitle.getAttribute('content') || '');
        ogText = ogText.replace(/\s*[-_|–—]\s*(1688|阿里巴巴).*$/i, '');
        if (ogText && ogText.length > 2 && ogText !== shop) {
          title = ogText;
        }
      }
      if (title === shop || title.indexOf(shop) >= 0) {
        _titleWarning = '标题疑似供应商名称，请人工检查';
      }
    }
    if (/有限公司|官方旗舰店|实力商家|金牌卖家/.test(title)) {
      _titleWarning = '标题含公司名特征，请人工检查';
    }

    // ── 规格参数 ─────────────────────────────────────
    var specs = [];
    // 方法1: 属性表
    document.querySelectorAll('.mod-detail-attributes tr, .attribute-list li, [data-mod-id="attributes"] .attr-item, .offer-attr tr, .product-features li, .detail-attributes .attr-row').forEach(function(row) {
      var cells = row.querySelectorAll('td, th, .attr-name, .attr-value, .label, .value, .name, .val');
      if (cells.length >= 2) {
        var name = cleanText(cells[0].textContent);
        var value = cleanText(cells[1].textContent);
        if (name && value && name.length < 30 && value.length < 200) {
          if (!specs.some(function(s) { return s.name === name; })) {
            specs.push({ name: name, value: value });
          }
        }
      }
    });
    // 方法2: table-form
    if (!specs.length) {
      document.querySelectorAll('.attr-table tr, .spec-table tr, table[class*="attr"] tr, table[class*="param"] tr, .mod-detail-params tr').forEach(function(row) {
        var cells = row.querySelectorAll('td, th');
        if (cells.length >= 2) {
          var name = cleanText(cells[0].textContent);
          var content = cleanText(cells[1].textContent);
          if (name && content && name.length < 30 && content.length < 200) {
            if (/(价格|售价|批发|库存|数量|起批|发货|物流|运费|评价)/i.test(name)) return;
            if (!specs.some(function(s) { return s.value === content; })) {
              specs.push({ name: name, value: content });
            }
          }
        }
      });
    }
    // 方法3: meta
    if (!specs.length) {
      document.querySelectorAll('meta[property^="product:"], meta[itemprop]').forEach(function(meta) {
        var prop = meta.getAttribute('property') || meta.getAttribute('itemprop') || '';
        var content = meta.getAttribute('content') || '';
        if (!content || !prop) return;
        if (/^(og:|twitter:|image|url|title|description|price)/i.test(prop)) return;
        var cleanName = prop.replace(/^product:/, '').replace(/_/g, ' ');
        if (cleanName && content && !specs.some(function(s) { return s.value === content; })) {
          specs.push({ name: cleanName, value: content });
        }
      });
    }

    // ── 描述 ─────────────────────────────────────────
    var desc = '';
    var descEl = document.querySelector('.offer-desc, [data-mod-id="description"], #J_DivItemDesc, .desc-content, .mod-detail-description, [class*="detail-description"], .detail-main-content');
    if (descEl) desc = cleanText(descEl.textContent).substring(0, 3000);

    // ── 调三个采集函数 ───────────────────────────────
    var mainImages = extract1688MainImages();
    var skuResult = extract1688SkuData();
    var detailImages = extract1688DetailImages();

    // ── 调试：输出各函数结果 ─────────────────────────
    console.log('%c[1688-RESULT] 采集结果:', 'color:#00cc00;font-weight:bold');
    console.log('[1688-RESULT] 主图:', mainImages.length, '张', mainImages.map(function(m){ return m.src.substring(0,60); }));
    console.log('[1688-RESULT] SKU:', skuResult.skus.length, '个', skuResult.skus.map(function(s){ return s.source_sku_name + ' ¥' + s.purchase_price_cny; }));
    console.log('[1688-RESULT] SKU图:', skuResult.skuImages.length, '张', skuResult.skuImages.map(function(m){ return m.linked_sku_name + ':' + m.src.substring(0,50); }));
    console.log('[1688-RESULT] 详情图:', detailImages.length, '张');

    // ── 合并图片（全局去重） ─────────────────────────
    var allSeen = {};
    var allImages = [];
    function mergeImages(arr) {
      for (var i = 0; i < arr.length; i++) {
        var norm = normalizeAlicdnUrl(arr[i].src);
        if (!allSeen[norm]) {
          allSeen[norm] = true;
          // 移除内部 _classify 字段
          var img = {};
          for (var k in arr[i]) { if (k !== '_classify') img[k] = arr[i][k]; }
          allImages.push(img);
        }
      }
    }
    mergeImages(mainImages);
    mergeImages(skuResult.skuImages);
    mergeImages(detailImages);

    // ── 质量检查 ─────────────────────────────────────
    var warnings = [];
    if (skuResult.skus.length > 0 && skuResult.skuImages.length === 0) {
      warnings.push('SKU 图未绑定到规格项，请检查 SKU 区域选择器');
    }
    if (detailImages.length === 0) {
      warnings.push('未采集到详情图');
    }
    if (mainImages.length === 0) {
      warnings.push('未采集到主图');
    }
    if (_titleWarning) {
      warnings.push(_titleWarning);
    }

    return {
      title: title,
      category: category,
      shopName: shop,
      description: desc,
      images: allImages,
      skus: skuResult.skus,
      specs: specs,
      _qualityWarnings: warnings,
      _debug: {
        mainImageCount: mainImages.length,
        skuImageCount: skuResult.skuImages.length,
        detailImageCount: detailImages.length,
        skuCount: skuResult.skus.length
      }
    };
  }

  // ── 1688 旧版（保留供回归对比，不再调用） ──────────
  function _extract1688_legacy() {
    // 标题
    let title = '';
    const titleEl = document.querySelector('.offer-title-text, h1[data-till], .title-text, h1, [data-mod-id="title"] h1');
    if (titleEl) title = cleanText(titleEl.textContent);
    if (!title) title = cleanText(document.title);

    // 类目 — 面包屑
    let category = '';
    const breadEl = document.querySelector('.offer-breadcrumb, .breadcrumb, .crumbs, [data-mod-id="breadcrumb"]');
    if (breadEl) category = cleanText(breadEl.textContent);

    // 店铺
    let shop = '';
    const shopEl = document.querySelector('.shop-name, .company-name, .offer-shop-name, [data-mod-id="shop"] .name, .supplier-name');
    if (shopEl) shop = cleanText(shopEl.textContent);

    // ── SKU 表格 — 多种策略 ──────────────────────────
    const skus = [];
    const seenSkuNames = new Set();

    function addSku(name, priceVal, color, style) {
      if (!name || name.length < 1 || seenSkuNames.has(name)) return;
      seenSkuNames.add(name);
      skus.push({
        source_order: skus.length + 1,
        source_sku_name: name,
        purchase_price_cny: priceVal || null,
        color_cn: color || name,
        style_cn: style || null
      });
    }

    // 方式1: 标准 SKU 表格 (新版 1688)
    document.querySelectorAll('.table-sku tr, .sku-item-row, .unit-list .unit-item, .offer-sku-item, .mod-detail-sku tr, [data-mod-id="sku"] tr, .prop-list li').forEach(function (row) {
      var cells = row.querySelectorAll('td, .cell, .col, .prop-value');
      if (cells.length >= 2) {
        var name = cleanText(cells[0].textContent);
        var priceText = cleanText(cells[cells.length - 1].textContent);
        var price = parseFloat(priceText.replace(/[^\d.]/g, ''));
        if (name && name.length > 1) {
          addSku(name, price || null, name, null);
        }
      }
    });

    // 方式2: SKU 属性选择器 (颜色/规格分类)
    if (!skus.length || skus.length <= 1) {
      // 找 SKU 属性块
      document.querySelectorAll('.prop-list-wrapper, .sku-prop-wrapper, [class*="sku-prop"], [data-mod-id="skuProps"]').forEach(function (wrapper) {
        var groups = [];
        wrapper.querySelectorAll('.prop-group, .prop-list, .prop-items').forEach(function (group) {
          var values = [];
          group.querySelectorAll('li, .prop-item, span[data-value], a').forEach(function (item) {
            var text = cleanText(item.textContent);
            if (text && text.length >= 1 && text.length <= 40 &&
                !/^(更多|全部|收起|展开)/i.test(text)) {
              if (values.indexOf(text) < 0) values.push(text);
            }
          });
          if (values.length >= 2) groups.push(values);
        });
        // 生成组合
        if (groups.length >= 2) {
          var combos = cartesianProduct(groups);
          combos.forEach(function (combo) {
            addSku(combo.join(' / '), null, combo[0], combo[1] || null);
          });
        } else if (groups.length === 1) {
          groups[0].forEach(function (v) { addSku(v, null, v, null); });
        }
      });
    }

    // 方式3: 价格区间回退
    if (!skus.length) {
      var price = null;
      var priceEl = document.querySelector('.price-original, .price, .mod-price .value, .offer-price, .price-range, [class*="Price"] [class*="price"], .mod-detail-price .value');
      if (priceEl) price = parseFloat(cleanText(priceEl.textContent).replace(/[^\d.]/g, ''));
      if (price && isNaN(price)) price = null;
      // 也尝试从 meta 标签提取
      if (!price) {
        var metaPrice = document.querySelector('meta[property="product:price:amount"], meta[itemprop="price"]');
        if (metaPrice) price = parseFloat(metaPrice.getAttribute('content'));
      }
      addSku(title || '默认规格', price, null, '标配');
    }

    // ── 商品规格参数 (specs) ──────────────────────────
    var specs = [];
    // 方式1: 1688 标准属性表
    document.querySelectorAll('.mod-detail-attributes tr, .attribute-list li, [data-mod-id="attributes"] .attr-item, .offer-attr tr, .product-features li, .detail-attributes .attr-row').forEach(function (row) {
      var cells = row.querySelectorAll('td, th, .attr-name, .attr-value, .label, .value, .name, .val');
      if (cells.length >= 2) {
        var name = cleanText(cells[0].textContent).replace(/[：:]\s*$/, '');
        var value = cleanText(cells[1].textContent);
        if (name && value && name.length > 0 && value.length > 0 &&
            name.length < 30 && value.length < 200) {
          specs.push({ name: name, value: value });
        }
      }
    });

    // 方式2: 表格形式的属性 (class="attr-table" 等)
    if (!specs.length) {
      document.querySelectorAll('.attr-table tr, .spec-table tr, table[class*="attr"] tr, table[class*="param"] tr, .mod-detail-params tr').forEach(function (row) {
        var cells = row.querySelectorAll('td, th');
        if (cells.length >= 2) {
          var name = cleanText(cells[0].textContent).replace(/[：:]\s*$/, '');
          var value = cleanText(cells[1].textContent);
          if (name && value && name.length > 0 && value.length > 0 &&
              name.length < 30 && value.length < 200 &&
              !/(价格|售价|批发|库存|数量|起批|发货|物流|运费|评价)/i.test(name)) {
            specs.push({ name: name, value: value });
          }
        }
      });
    }

    // 方式3: 从 meta 标签提取
    if (!specs.length) {
      document.querySelectorAll('meta[property^="product:"], meta[itemprop]').forEach(function (meta) {
        var prop = meta.getAttribute('property') || meta.getAttribute('itemprop') || '';
        var content = meta.getAttribute('content') || '';
        if (prop && content && content.length < 200) {
          // 过滤非规格的 meta
          if (!/^(og:|twitter:|image|url|title|description|price)/i.test(prop)) {
            var cleanName = prop.replace(/^(product:|itemprop\.)/, '').replace(/_/g, ' ');
            if (cleanName && content && !specs.some(function(s) { return s.value === content; })) {
              specs.push({ name: cleanName, value: content });
            }
          }
        }
      });
    }

    // ── 描述 ──────────────────────────────────────────
    var desc = '';
    var descEl = document.querySelector('.offer-desc, [data-mod-id="description"], #J_DivItemDesc, .desc-content, .mod-detail-description, [class*="detail-description"], .detail-main-content');
    if (descEl) desc = cleanText(descEl.textContent).substring(0, 3000);

    // ── 详情图片（从描述区域提取，含 lazyload / CSS bg / iframe） ──
    var detailImages = [];
    var _detailSeen = {};
    function _addDetailImg(src, alt) {
      if (!src || src.startsWith('data:') || src.endsWith('.svg') || src.endsWith('.ico')) return;
      var fixed = fixImageUrl(src);
      if (!fixed) return;
      var norm = normalizeAlicdnUrl(fixed);
      if (_detailSeen[norm]) return;
      _detailSeen[norm] = true;
      detailImages.push({ role: 'detail', src: fixed, alt: cleanText(alt || ''), source_area: 'detail_content' });
    }
    if (descEl) {
      // 1. img src + lazyload 属性
      var lazyAttrs = ['src', 'data-src', 'data-lazy-src', 'data-original', 'data-ks-lazyload', 'data-lazyload'];
      descEl.querySelectorAll('img').forEach(function (img) {
        for (var li = 0; li < lazyAttrs.length; li++) {
          var v = (lazyAttrs[li] === 'src') ? (img.src || '') : (img.getAttribute(lazyAttrs[li]) || '');
          if (v && v.length > 10) _addDetailImg(v, img.alt);
        }
      });

      // 2. CSS background-image
      descEl.querySelectorAll('[style*="background"]').forEach(function (el) {
        try {
          var bg = getComputedStyle(el).backgroundImage;
          var m = bg && bg.match(/url\(["']?(https?:\/\/[^"')]+)["']?\)/);
          if (m && m[1] && m[1].includes('alicdn.com')) _addDetailImg(m[1], '');
        } catch(e) {}
      });

      // 3. iframe 内图片（1688 常用 iframe 加载详情描述）
      descEl.querySelectorAll('iframe').forEach(function (iframe) {
        try {
          var iDoc = iframe.contentDocument || (iframe.contentWindow && iframe.contentWindow.document);
          if (iDoc) {
            iDoc.querySelectorAll('img').forEach(function (img) {
              var iSrc = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
              _addDetailImg(iSrc, img.alt);
            });
          }
        } catch(e) {} // 跨域 iframe 静默忽略
      });
    }

    // ── 合并图片并去重 ────────────────────────────────
    var baseImages = extractImages();
    var mergedSeen = {};
    var mergedImages = [];
    baseImages.forEach(function(img) {
      var k = normalizeAlicdnUrl(img.src);
      if (!mergedSeen[k]) { mergedSeen[k] = true; mergedImages.push(img); }
    });
    detailImages.slice(0, 30).forEach(function(img) {
      var k = normalizeAlicdnUrl(img.src);
      if (!mergedSeen[k]) { mergedSeen[k] = true; mergedImages.push(img); }
    });

    return {
      title: title,
      category: category,
      shopName: shop,
      description: desc,
      images: mergedImages,
      skus: skus,
      specs: specs
    };
  }

  // ── 淘宝/天猫 ───────────────────────────────────────
  function extractTaobaoTmall() {
    // 标题 — meta 优先（最可靠）
    let title = '';
    const metaTitle = document.querySelector('meta[property="og:title"], meta[name="title"], meta[itemprop="name"]');
    if (metaTitle) {
      title = metaTitle.getAttribute('content') || '';
    }

    // 然后尝试 DOM 选择器
    if (!title || title.length < 3) {
      const titleEls = document.querySelectorAll('h1, .tb-main-title, .ItemTitle--mainTitle--, .J_ItemTitle');
      for (const el of titleEls) {
        const text = cleanText(el.textContent);
        // 过滤：标题通常 > 5 字符，不含评价/好评/包邮等噪音
        if (text.length > 5 && text.length < 100 &&
            !/评价|好评|包邮|正品|投诉|举报|客服|运费|快递|收藏|分享|关注|首页/i.test(text)) {
          title = text;
          break;
        }
      }
    }

    // 回退：document.title（清理后缀）
    if (!title || title.length < 3) {
      title = cleanText(document.title);
      // 去除淘宝/天猫/旗舰店等后缀
      title = title.replace(/[\s\-—–|]*(天猫|淘宝|天猫商城|正品保证|包邮|旗舰店|官方店|-tmall|-taobao).*$/i, '');
    }

    // 再次清理异常情况
    if (/^(用户评价|商品评价|好评率|客服|同行|关注|收藏)/i.test(title)) title = '';

    // 终极回退
    if (!title || title.length < 3) {
      // 找页面上最可能是商品名的文字：通常在顶部区域、字体较大的元素
      const candidates = document.querySelectorAll('h1, h2, [class*="title"], [class*="Title"]');
      for (const el of candidates) {
        const text = cleanText(el.textContent);
        if (text.length > 6 && text.length < 80 && !/[评价好评包邮投诉举报客服运费收藏]/i.test(text)) {
          title = text;
          break;
        }
      }
    }

    if (!title || title.length < 3) title = cleanText(document.title) || '(未能识别标题)';

    // 类目 — 面包屑
    let category = '';
    const breadSelectors = [
      '#J_BreadCrumb', '.breadcrumb', '.crumbs',
      '[class*="breadcrumb"]', '[class*="Breadcrumb"]',
      '[data-spm="bread"]', '.tb-breadcrumb'
    ];
    for (const sel of breadSelectors) {
      const el = document.querySelector(sel);
      if (el) { category = cleanText(el.textContent); if (category) break; }
    }

    // 店铺 — 只取店名，过滤评分/评价等噪音
    let shop = '';
    const shopSelectors = [
      '.slogo-shopname', '.shop-name', '.tb-shop-name',
      '[class*="shopname"]', '[class*="shopName"]',
      'a[href*="shop"] strong', '.J_ShopName',
      '.seller-name', '[class*="seller"]',
      '.shop-info .name', '.slogo-shopname strong'
    ];
    for (const sel of shopSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        const text = cleanText(el.textContent);
        // 只取第一行或截断到数字/评分前
        const short = text.split(/[\d.]+%|[4-5]\.\d{2,}|好评|VIP|同行|客服|满意度|次日达/)[0].trim();
        if (short && short.length >= 2) { shop = short; break; }
      }
    }
    // 如果店铺名还很长，尽量截取前 20 个字符
    if (shop && shop.length > 30) shop = shop.substring(0, 30);

    // 价格 — 多策略
    let price = null;
    const priceSelectors = [
      '.tm-price', '.tm-promo-price .tm-price',
      '#J_PromoPriceNum', '#J_StrPriceModBox',
      '[class*="Price"]', '[class*="price"]',
      '.tb-rmb-num', 'meta[itemprop="price"]'
    ];
    for (const sel of priceSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        const raw = el.getAttribute('content') || cleanText(el.textContent);
        const parsed = parseFloat(raw.replace(/[^\d.]/g, ''));
        if (parsed && parsed > 0.01) { price = parsed; break; }
      }
    }

    // SKU — 多策略
    const skus = [];
    const seenSkuNames = new Set();

    function addSku(name, priceVal, color, style) {
      if (!name || name.length < 1 || seenSkuNames.has(name)) return;
      seenSkuNames.add(name);
      skus.push({
        source_order: skus.length + 1,
        source_sku_name: name,
        purchase_price_cny: priceVal || null,
        color_cn: color || name,
        style_cn: style || null
      });
    }

    // 策略1: 扫描 window 下所有全局变量中的 SKU 数据
    try {
      const keysToCheck = [
        'g_config', '__data', '__pageData', '_tb_config',
        '__INITIAL_STATE__', '__PRELOADED_STATE__', '__NEXT_DATA__',
        'skuModel', 'ItemData', 'pageData', '__tmData',
        'detailData', 'itemData', 'appData', '__initialState'
      ];
      for (const key of keysToCheck) {
        try {
          const obj = window[key];
          if (!obj) continue;
          // 递归查找 sku 相关数据
          const found = findSkuInObject(obj, 0);
          if (found && found.length) {
            found.forEach(function (s) { addSku(s.name, s.price, s.name, null); });
            if (skus.length) break;
          }
        } catch (e) { /* continue */ }
      }
    } catch (e) { /* ignore */ }

    function findSkuInObject(obj, depth) {
      if (!obj || depth > 5) return null;
      if (Array.isArray(obj)) {
        // 检查是否是 SKU 数组
        if (obj.length > 0 && obj.length <= 50) {
          const sample = obj[0];
          if (typeof sample === 'object' && sample) {
            const nameKey = sample.name || sample.label || sample.value || sample.skuName || sample.propName;
            if (nameKey) {
              return obj.map(function (item) {
                return {
                  name: cleanText(item.name || item.label || item.value || item.skuName || item.propName || item.key || ''),
                  price: parseFloat(item.price || item.priceCent || item.salePrice) || null
                };
              }).filter(function (s) { return s.name; });
            }
          }
        }
        // 递归搜索数组元素
        for (const item of obj.slice(0, 10)) {
          const found = findSkuInObject(item, depth + 1);
          if (found) return found;
        }
      } else if (typeof obj === 'object') {
        // 查找 sku/skus/skuMap 等关键属性
        for (const k of ['sku', 'skus', 'skuMap', 'skuList', 'skuProps', 'saleProps', 'props', 'variants', 'sizes']) {
          if (obj[k]) {
            const found = findSkuInObject(obj[k], depth + 1);
            if (found) return found;
          }
        }
      }
      return null;
    }

    // 策略2: 查找 SKU 属性容器（带标签如"颜色分类""套餐名称"）
    function findSkuByLabels() {
      var labels = ['颜色分类', '颜色', '规格', '套餐名称', '套餐', '版本', '尺码', '型号', '容量', '款式', '大小', '配置', '内存', '存储', '组合', '分类', '口味', '香型'];
      var foundGroups = [];

      // 遍历所有元素，查找文本匹配上述标签的 dt/label/span
      var allEls = document.querySelectorAll('dt, .label, .prop-label, .attr-label, .sku-label, .tm-prop-label, label, .name, .key, [class*="label"], [class*="Label"], [class*="prop-name"], [class*="attr-name"]');
      allEls.forEach(function (labelEl) {
        var labelText = cleanText(labelEl.textContent);
        var matchedLabel = null;
        for (var li = 0; li < labels.length; li++) {
          if (labelText.indexOf(labels[li]) === 0 || labelText === labels[li]) {
            matchedLabel = labels[li];
            break;
          }
        }
        if (!matchedLabel) return;

        // 找到这个 label 对应的值容器
        var valueContainer = labelEl.nextElementSibling;
        if (!valueContainer) valueContainer = labelEl.parentElement;
        if (!valueContainer) return;

        // 在容器内找所有可能的 SKU 值元素
        var values = [];
        valueContainer.querySelectorAll('li, .item, [data-value], span, a, dd, .prop-value, .attr-value, [class*="sku-val"], [class*="prop-item"]').forEach(function (valEl) {
          var text = cleanText(valEl.textContent);
          // 过滤：2-60 字符，不含导航/功能词，不含促销标签
          if (text && text.length >= 2 && text.length <= 60 &&
              !/^(首页|我的|购物车|收藏|客服|举报|投诉|评价|关注|开店|免费|已买到|足迹|卡券|退出|登录)/i.test(text) &&
              !/^(立即购买|加入购物车|取消|确定|查看更多)/i.test(text) &&
              !/(加购|疯抢|热卖|爆款|新品|促销|已售\d|人气|好评|推荐|首发|预售|限量|万人|千件|万件|元红包|优惠券|满减|包邮|免邮|减\d)/i.test(text)) {
            // 如果这个元素是 label 本身，跳过
            if (valEl === labelEl) return;
            // 去重
            if (values.indexOf(text) < 0) values.push(text);
          }
        });

        if (values.length >= 2 && values.length <= 30) {
          foundGroups.push(values);
        }
      });

      return foundGroups;
    }

    var propGroups = findSkuByLabels();

    // 如果找到了多组，生成笛卡尔积
    if (propGroups.length >= 2) {
      var combinations = cartesianProduct(propGroups);
      combinations.forEach(function (combo) {
        addSku(combo.join(' / '), null, combo[0], combo[1] || null);
      });
    } else if (propGroups.length === 1) {
      // 只有一个维度，直接列出
      propGroups[0].forEach(function (val) { addSku(val, null, val, null); });
    }

    // 策略2.5: 从 SKU 容器中提取（回退方案，更严格的过滤）
    if (!skus.length) {
      // 尝试在商品详情区域（非导航/侧栏）找 SKU
      var detailArea = document.querySelector(
        '.detail-content, .product-detail, .item-detail, ' +
        '#detail, .tb-detail, .tm-detail, [class*="detail-wrap"], ' +
        '.sku-wrap, .J_Panel, #J_DetailMeta'
      ) || document.body;

      var skuContainerSelectors = [
        '.J_TSaleProp', '.tb-sku', '.tm-sale-prop',
        '[class*="sku-prop"]', '[class*="SkuProp"]',
        '[class*="sku-wrap"]', '[class*="SkuWrap"]',
        '.prop-list', '[class*="propList"]'
      ];
      var skuContainer = null;
      for (var si = 0; si < skuContainerSelectors.length; si++) {
        var el = detailArea.querySelector(skuContainerSelectors[si]);
        if (el) { skuContainer = el; break; }
      }
      if (skuContainer) {
        skuContainer.querySelectorAll('li, .item, [data-value]').forEach(function (item) {
          var text = cleanText(item.textContent);
          if (text && text.length >= 2 && text.length <= 40 &&
              !/^(首页|我的|购物车|收藏|客服|举报|投诉|评价|关注|开店|免费|立即购买|加入购物车)/i.test(text) &&
              !/(加购|疯抢|热卖|爆款|新品|促销|已售\d|人气|好评|推荐|首发|预售|限量|万人|千件|万件|元红包|优惠券|满减|包邮|免邮|减\d)/i.test(text)) {
            addSku(text, null, text, null);
          }
        });
      }
    }

    // 策略3: 单SKU回退
    if (!skus.length) {
      addSku(title || '默认规格', price, null, '标配');
    }

    // 描述
    // ── 规格参数 ────────────────────────────────────
    var specs = [];
    // 淘宝/天猫属性列表
    document.querySelectorAll('.J_AttrSection tr, .attributes-list li, .tm-ind-panel .tm-ind-item, [data-mod-id="attributes"] .attr-item, #J_AttrUL li, .tb-prop li').forEach(function (row) {
      var cells = row.querySelectorAll('td, th, .attr-name, .attr-value, .label, .value, .name, .val');
      if (cells.length >= 2) {
        var name = cleanText(cells[0].textContent).replace(/[：:]\s*$/, '');
        var value = cleanText(cells[1].textContent);
        if (name && value && name.length > 0 && value.length > 0 &&
            name.length < 30 && value.length < 200) {
          specs.push({ name: name, value: value });
        }
      }
    });
    // 从 meta 标签提取
    if (!specs.length) {
      document.querySelectorAll('meta[property^="product:"], meta[itemprop]').forEach(function (meta) {
        var prop = meta.getAttribute('property') || meta.getAttribute('itemprop') || '';
        var content = meta.getAttribute('content') || '';
        if (prop && content && content.length < 200) {
          if (!/^(og:|twitter:|image|url|title|description|price)/i.test(prop)) {
            var cleanName = prop.replace(/^(product:|itemprop\.)/, '').replace(/_/g, ' ');
            if (cleanName && content && !specs.some(function(s) { return s.value === content; })) {
              specs.push({ name: cleanName, value: content });
            }
          }
        }
      });
    }

    // ── 描述 ────────────────────────────────────────
    var desc = '';
    var descEl = document.querySelector(
      '.J_DetailSection, #J_DcTop, [class*="descContent"], ' +
      '[class*="detailContent"], [class*="desc"]'
    );
    if (descEl) desc = cleanText(descEl.textContent).substring(0, 2000);

    return {
      title: title,
      category: category,
      shopName: shop,
      description: desc,
      images: extractImages(),
      skus: skus,
      specs: specs
    };
  }

  // ── 拼多多 ──────────────────────────────────────────
  function extractPinduoduo() {
    let title = '';
    const titleEl = document.querySelector('.goods-name, .product-title, .goods-title, h1');
    if (titleEl) title = cleanText(titleEl.textContent);
    if (!title) title = cleanText(document.title);

    let category = '';
    const breadEl = document.querySelector('.breadcrumb, .crumbs');
    if (breadEl) category = cleanText(breadEl.textContent);

    let shop = '';
    const shopEl = document.querySelector('.mall-name, .shop-name, .store-name');
    if (shopEl) shop = cleanText(shopEl.textContent);

    const skus = [];
    document.querySelectorAll('.sku-item, .sku-cell, .group-item, .spec-item').forEach(function (item) {
      const name = cleanText(item.textContent);
      if (name && name.length > 1 && !skus.find(function(s){return s.source_sku_name === name})) {
        skus.push({
          source_order: skus.length + 1,
          source_sku_name: name,
          purchase_price_cny: null,
          color_cn: name,
          style_cn: null
        });
      }
    });

    if (!skus.length) {
      var price2 = null;
      var priceEl2 = document.querySelector('.price, .current-price, .goods-price, .price-text');
      if (priceEl2) price2 = parseFloat(cleanText(priceEl2.textContent).replace(/[^\d.]/g, ''));
      if (!price2 || isNaN(price2)) price2 = null;
      skus.push({
        source_order: 1,
        source_sku_name: title || '默认规格',
        purchase_price_cny: price2,
        color_cn: null,
        style_cn: '标配'
      });
    }

    // ── 规格参数 ────────────────────────────────────
    var specs3 = [];
    document.querySelectorAll('.goods-attribute tr, .attr-list li, .param-item, [class*="attr"] tr').forEach(function (row) {
      var cells = row.querySelectorAll('td, th, .attr-name, .attr-value, .label, .value');
      if (cells.length >= 2) {
        var name = cleanText(cells[0].textContent).replace(/[：:]\s*$/, '');
        var value = cleanText(cells[1].textContent);
        if (name && value && name.length > 0 && value.length > 0 && name.length < 30 && value.length < 200) {
          specs3.push({ name: name, value: value });
        }
      }
    });

    return {
      title: title,
      category: category,
      shopName: shop,
      description: '',
      images: extractImages(),
      skus: skus,
      specs: specs3
    };
  }

  // ── 主提取 ──────────────────────────────────────────
  function extract() {
    const platform = detectPlatform();

    let data;
    if (platform === '1688')           data = extract1688Product();
    else if (platform === 'taobao')    data = extractTaobaoTmall();
    else if (platform === 'tmall')     data = extractTaobaoTmall();
    else if (platform === 'pinduoduo') data = extractPinduoduo();
    else if (platform === 'ozon_product') data = extractOzonProduct();
    else return { error: 'unsupported_platform', message: '当前页面不支持' };

    if (!data.title || data.title.length < 2) {
      return { error: 'no_title', message: '未能识别商品标题，请确认当前页面为商品详情页' };
    }

    return {
      platform: platform,
      url: location.href,
      item_id: getItemIdFromUrl(location.href) || '',
      title: data.title,
      category: data.category,
      shop_name: data.shopName,
      description: data.description,
      skus: data.skus,
      images: data.images.map(function (img, i) {
        return {
          index: i,
          role: img.role,
          src: img.src,
          alt: img.alt || '',
          source_area: img.source_area || 'unknown',
          dom_path: img.dom_path || '',
          width: img.width || 0,
          height: img.height || 0,
          nearby_text: img.nearby_text || '',
          source_selector: img.source_selector || '',
          reason: img.reason || '',
          linked_sku_name: img.linked_sku_name || null
        };
      }),
      specs: data.specs || [],
      sku_count: data.skus.length,
      image_count: data.images.length,
      main_count: data.images.filter(function(i) { return i.role === 'main'; }).length,
      sku_img_count: data.images.filter(function(i) { return i.role === 'sku'; }).length,
      spec_count: (data.specs || []).length,
      quality_warnings: data._qualityWarnings || []
    };
  }

  // ── 保留 popup 消息通信 ────────────────────────────
  chrome.runtime.onMessage.addListener(function (request, sender, sendResponse) {
    if (request.action === 'extract') {
      try {
        sendResponse(extract());
      } catch (e) {
        sendResponse({ error: 'extract_failed', message: e.message });
      }
      return true;
    }
  });

  // ── 采集前自动滚动，触发懒加载 ─────────────────
  async function scrollToBottom() {
    var maxIterations = 15;
    var scrollStep = window.innerHeight || 800;
    var waitMs = 700;
    var prevHeight = 0;

    for (var i = 0; i < maxIterations; i++) {
      var scrollHeight = document.body.scrollHeight || document.documentElement.scrollHeight;
      var targetY = (i + 1) * scrollStep;

      // 已到底且页面高度不再增长 → 结束
      if (targetY >= scrollHeight && scrollHeight === prevHeight) {
        break;
      }

      window.scrollTo({ top: targetY, behavior: 'smooth' });
      await sleep(waitMs);

      prevHeight = scrollHeight;
    }

    // 底部额外等待，确保最后一批懒加载图片加载完成
    await sleep(1500);

    // Keep lazy-loaded images in the DOM/resource cache until extraction finishes.
    await sleep(300);
  }

  // ── 重试提取（等待动态内容加载） ─────────────────
  async function handleCollect() {
    if (!authToken) {
      showToast('请先在插件弹窗中配置 Token', 'error');
      return;
    }

    if (resultPanel) { resultPanel.remove(); resultPanel = null; }

    floatingBtn.classList.add('loading');
    floatingBtn.textContent = '...';

    var platform = detectPlatform();

    // 自动滚动到底部，触发懒加载图片
    await scrollToBottom();

    // 尝试提取，如果结果不理想则等待后重试
    let data = extractOnce();
    if (!data.title || data.title.length < 3 || (data.sku_count === 0 && data.image_count === 0)) {
      // 等待 1.5 秒让动态内容加载
      await sleep(1500);
      data = extractOnce();
    }

    // 检测详情长图是否缺失（仅 1688 需要检测）
    if (data && !data.error) {
      if (data.platform === '1688') {
        var hasDetailImages = data.images && data.images.some(function(img) {
          return img.source_area === 'detail_content';
        });
        if (!hasDetailImages) {
          data.detail_missing = true;
        }
      } else {
        data.detail_missing = false;
      }
    }

    floatingBtn.classList.remove('loading');
    floatingBtn.textContent = '采集';

    if (data.error) {
      showToast(data.message, 'error');
      return;
    }
    showResultPanel(data);
  }

  function extractOnce() {
    try {
      return extract();
    } catch (e) {
      return { error: 'extract_failed', message: e.message };
    }
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function cartesianProduct(groups) {
    // 生成多维度组合的笛卡尔积: [[a,b],[1,2]] → [["a / 1"],["a / 2"],["b / 1"],["b / 2"]]
    if (!groups.length) return [];
    var result = groups[0].map(function (v) { return [v]; });
    for (var i = 1; i < groups.length; i++) {
      var next = [];
      result.forEach(function (combo) {
        groups[i].forEach(function (val) {
          next.push(combo.concat([val]));
        });
      });
      result = next;
    }
    return result;
  }

  // ── 启动 ────────────────────────────────────────────
  // 延迟初始化，确保页面动态内容已渲染
  setTimeout(init, 800);

})();

  function extractOzonProduct() {
    // ── 1. 尝试从页面JSON状态提取 ──
    var stateData = null;
    var scripts = document.querySelectorAll('script');
    for (var si = 0; si < scripts.length; si++) {
      var text = scripts[si].textContent || scripts[si].innerHTML || '';
      if (text.indexOf('window.__INITIAL_STATE__') >= 0 || text.indexOf('window.__NUXT__') >= 0) {
        try {
          var match = text.match(/(?:window\.__INITIAL_STATE__|window\.__NUXT__)\s*=\s*(\{.*?\});?\s*\n/);
          if (!match) match = text.match(/(?:window\.__INITIAL_STATE__|window\.__NUXT__)\s*=\s*(\{.*?\});?\s*$/);
          if (!match) match = text.match(/=\s*(\{.*?"product".*?\});/);
          if (match) stateData = JSON.parse(match[1]);
        } catch(e) {}
      }
      if (!stateData && (text.indexOf('"@type":"Product"') >= 0 || text.indexOf('"product"') >= 0)) {
        try {
          if (scripts[si].type === 'application/ld+json' || scripts[si].type === 'application/json') {
            stateData = JSON.parse(text);
          }
        } catch(e) {}
      }
    }

    var title = '', desc = '', shop = '', category = '';
    var images = [], skus = [], specs = [], videos = [];

    // ── 2. 从JSON状态提取 ──
    if (stateData) {
      var pd = stateData.product || stateData.state || stateData;

      // 从JSON提取图集
      try {
        var js = JSON.stringify(stateData);
        // 提取所有OZON CDN图片URL
        var imgUrls = js.match(/https?:\/\/[^"'\s,]*ozon[^"'\s,]*\.(jpg|jpeg|png|webp|avif)[^"'\s,]*/gi);
        if (imgUrls) {
          for (var u2 = 0; u2 < imgUrls.length && images.length < 30; u2++) {
            var cleanUrl = imgUrls[u2].replace(/\/wc\d{1,4}(\/|$)/, '/wc1000/').replace(/[?&](size|w|h|q)=\d+/gi, '').replace(/\?$/, '');
            var k2 = cleanUrl.substring(0, 100);
            if (!processedUrls[k2] && cleanUrl.indexOf('/icon') < 0 && cleanUrl.indexOf('/logo') < 0) {
              processedUrls[k2] = true;
              images.push({ role: images.length === 0 ? 'main' : 'detail', src: cleanUrl });
            }
          }
        }
        // 提取视频URL
        var vidUrls = js.match(/https?:\/\/[^"'\s,]*(?:video|player|vkvideo|vk\.com)[^"'\s,]*/gi);
        if (vidUrls) {
          for (var v2 = 0; v2 < vidUrls.length; v2++) {
            if (videos.indexOf(vidUrls[v2]) < 0) videos.push(vidUrls[v2]);
          }
        }
      } catch(e) {}

      // 递归查找product
      function findProduct(obj, depth) {
        if (!obj || depth > 5) return null;
        if (obj.title || obj.name || obj.offerData) return obj;
        for (var key in obj) {
          if (key === 'product' || key === 'currentProduct' || key === 'skuInfo') {
            var r = obj[key];
            if (r && (r.title || r.name)) return r;
          }
          if (typeof obj[key] === 'object') {
            var r2 = findProduct(obj[key], depth + 1);
            if (r2) return r2;
          }
        }
        return null;
      }
      var prod = findProduct(stateData, 0) || stateData;
      title = prod.title || prod.name || '';
      desc = prod.description || prod.richDescription || '';
      category = prod.category || prod.categoryName || '';
      shop = prod.sellerName || prod.seller || '';
    }

    // ── 3. DOM兜底 ──
    if (!title) {
      var ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogTitle) title = ogTitle.getAttribute('content') || '';
      if (!title) title = (document.querySelector('h1') || {}).textContent || '';
      if (!title) title = document.title || '';
    }
    if (!desc) {
      var ogDesc = document.querySelector('meta[property="og:description"]');
      if (ogDesc) desc = ogDesc.getAttribute('content') || '';
    }
    if (!shop) {
      var sellerEl = document.querySelector('[data-widget="webCurrentSeller"], [class*="seller"], a[href*="seller"] span');
      if (sellerEl) shop = sellerEl.textContent.trim();
    }

    // ── 4. 图片提取（区分主图/SKU图/详情图） ──
    var mainImgs = document.querySelectorAll('[data-widget="webGallery"] img, [class*="gallery"] img[src*="ozon"], [data-widget="webPhoto"] img');
    // SKU变体图片
    var skuImgs = document.querySelectorAll('[data-widget="webVariant"] img, [class*="variant"] img[src*="ozon"], [class*="sku"] img[src*="ozon"]');
    // 详情富文本图片
    var detailImgs = document.querySelectorAll('[data-widget="webDescription"] img, [class*="description"] img, [class*="ra"] img, article img, [data-widget="webDetail"] img, [class*="detail"] img');
    var allImgs = document.querySelectorAll('img[src*="ozon"], img[src*="ir-2.ozone.ru"], img[src*="cdn1.ozone.ru"], img[src*="woody"], img[src*="product"]');
    var processedUrls = {};

    // 预计算：找到评论区/推荐区的Y坐标边界
  var reviewBoundaryY = 999999;
  var markers = ['Отзывы', 'Вопросы', 'Похожие товары', 'С этим товаром покупают', 'Рекомендуем', 'Смотрите также'];
  var allH = document.querySelectorAll('h2,h3');
  for (var hi = 0; hi < allH.length; hi++) {
    var txt = (allH[hi].textContent || '').trim();
    for (var mi = 0; mi < markers.length; mi++) {
      if (txt.indexOf(markers[mi]) >= 0) {
        var y = window.scrollY + allH[hi].getBoundingClientRect().top;
        if (y < reviewBoundaryY) reviewBoundaryY = y;
      }
    }
  }

  function isInBadArea(el) {
    if (!el) return false;
    // 位置检查：在评论区下方
    if (reviewBoundaryY < 999999) {
      var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {top:0};
      var elY = window.scrollY + rect.top;
      if (elY > reviewBoundaryY) return true;
    }
    // DOM祖先检查
    var node = el;
    for (var depth = 0; depth < 6 && node; depth++) {
      var cls = (node.className || '').toString().toLowerCase();
      var id = (node.id || '').toLowerCase();
      var dw = (node.getAttribute && node.getAttribute('data-widget')) || '';
      if (cls.indexOf('review') >= 0 || cls.indexOf('comment') >= 0) return true;
      if (id.indexOf('review') >= 0 || id.indexOf('comment') >= 0) return true;
      if (dw.indexOf('webReview') >= 0 || dw.indexOf('webComment') >= 0) return true;
      if (cls.indexOf('recommend') >= 0 || cls.indexOf('similar') >= 0 || cls.indexOf('related') >= 0) return true;
      if (dw.indexOf('webRecommend') >= 0 || dw.indexOf('webSimilar') >= 0) return true;
      if (cls.indexOf('carousel') >= 0 && dw.indexOf('webGallery') < 0 && cls.indexOf('gallery') < 0) return true;
      if (cls.indexOf('nav') >= 0 || cls.indexOf('footer') >= 0 || cls.indexOf('sidebar') >= 0) return true;
      node = node.parentElement;
    }
    return false;
  }

  function addImage(src, role, el) {
      if (!src || !src.startsWith('http')) return;
      if (el && isInBadArea(el)) return;
      // 图标/Logo/支付/银行过滤
      var u = src.toLowerCase();
      if (/\/icon|\/logo|\/avatar|\/favicon|\/sprite|\/badge/.test(u)) return;
      if (u.indexOf('bank') >= 0 || u.indexOf('payment') >= 0 || u.indexOf('static') >= 0) return;
      // 其他产品链接过滤(关键!)
      if (el && el.closest) {
        var a = el.closest('a[href*=\"/product/\"]');
        if (a) {
          var aPath = (a.getAttribute('href')||'').split('?')[0];
          var curPath = location.pathname.split('?')[0];
          if (aPath !== curPath && aPath.indexOf('/product/') >= 0) return;
        }
      }
      // 去水印/缩略图处理
      src = src.replace(/\/wc\d+(\/|$)/, '/wc1000/').replace(/\/\d{1,4}x\d{1,4}(\/|$)/, '/').replace(/[?&](size|w|h|quality|q)=\d+/gi, '').replace(/[?&]ts=\d+/gi, '').replace(/\?$/, '');
      var key = src.substring(0, 80);
      if (processedUrls[key]) return;
      processedUrls[key] = true;
      images.push({ role: role, src: src });
    }

    // 主图
    for (var mi = 0; mi < mainImgs.length; mi++) { addImage(mainImgs[mi].src || mainImgs[mi].getAttribute('data-src'), 'main', mainImgs[mi]); }
    // SKU图
    for (var si2 = 0; si2 < skuImgs.length; si2++) { addImage(skuImgs[si2].src || skuImgs[si2].getAttribute('data-src'), 'sku', skuImgs[si2]); }
    // 详情图
    for (var di = 0; di < detailImgs.length; di++) { addImage(detailImgs[di].src || detailImgs[di].getAttribute('data-src'), 'detail', detailImgs[di]); }
    // 兜底：仅在白名单没抓到图时才从allImgs补充(且必须在评论/推荐区之上)
    var mainCount = images.filter(function(x){return x.role==='main';}).length;
    var detailCount = images.filter(function(x){return x.role==='detail';}).length;
    if (mainCount === 0 || detailCount < 3) {
      for (var ai = 0; ai < Math.min(allImgs.length, 30); ai++) {
        var fallbackRole = (mainCount === 0 && ai < 10) ? 'main' : 'detail';
        addImage(allImgs[ai].src || allImgs[ai].getAttribute('data-src'), fallbackRole, allImgs[ai]);
      }
    }

    // ── 5. 视频提取 ──
    var videoEls = document.querySelectorAll('video, video source, video[src], [data-widget="webVideo"] video, [class*="video"] video, [class*="player"] video, [class*="media"] video');
    for (var vi = 0; vi < videoEls.length; vi++) {
      var vSrc = videoEls[vi].src || videoEls[vi].getAttribute('data-src') || videoEls[vi].getAttribute('src') || videoEls[vi].currentSrc || '';
      if (vSrc && vSrc.startsWith('http') && videos.indexOf(vSrc) < 0) videos.push(vSrc);
      var sources = videoEls[vi].querySelectorAll('source');
      for (var si = 0; si < sources.length; si++) {
        var sSrc = sources[si].src || sources[si].getAttribute('data-src') || '';
        if (sSrc && sSrc.startsWith('http') && videos.indexOf(sSrc) < 0) videos.push(sSrc);
      }
    }
    var iframes = document.querySelectorAll('iframe[src*="youtube"], iframe[src*="vk.com"], iframe[src*="rutube"], iframe[src*="yandex"], iframe[src*="vkvideo"], iframe[src*="player"]');
    for (var fi = 0; fi < iframes.length; fi++) {
      var iSrc = iframes[fi].src || iframes[fi].getAttribute('data-src') || '';
      if (iSrc && iSrc.startsWith('http') && videos.indexOf(iSrc) < 0) videos.push(iSrc);
    }
    // 查找JSON中的视频URL
    if (stateData) {
      try {
        var jsonStr = JSON.stringify(stateData);
        var vMatches = jsonStr.match(/https?:\/\/[^"'\s]*\.(mp4|webm|mov)[^"'\s]*/gi);
        if (vMatches) { for (var vm = 0; vm < vMatches.length; vm++) { if (videos.indexOf(vMatches[vm]) < 0) videos.push(vMatches[vm]); } }
      } catch(e) {}
    }

    // ── 6. SKU提取 ──
    // OZON变体选择器
    var variantBtns = document.querySelectorAll('[data-widget="webVariant"] button, [class*="purchasing"] button, [class*="options"] button, [class*="sku"] button, [class*="variant"] button, [role="button"]');
    for (var vk = 0; vk < variantBtns.length; vk++) {
      var vName = (variantBtns[vk].textContent || '').trim();
      if (vName && vName.length > 1 && vName.length < 80 && vName.indexOf('В корзину') < 0 && vName.indexOf('Купить') < 0) {
        skus.push({ source_order: skus.length + 1, source_sku_name: vName });
      }
    }
    // 从JSON提取SKU
    if (skus.length === 0 && stateData) {
      try {
        var jStr = JSON.stringify(stateData);
        var skuMatch = jStr.match(/"skuList"\s*:\s*(\[.*?\])/);
        if (!skuMatch) skuMatch = jStr.match(/"variants"\s*:\s*(\[.*?\])/);
        if (!skuMatch) skuMatch = jStr.match(/"offers"\s*:\s*(\[.*?\])/);
        if (!skuMatch) skuMatch = jStr.match(/"skuMap"\s*:\s*(\{.*?\})/);
        if (skuMatch) {
          var skuData = JSON.parse(skuMatch[1]);
          for (var sk = 0; sk < skuData.length; sk++) {
            skus.push({ source_order: sk + 1, source_sku_name: (skuData[sk].name || skuData[sk].skuName || skuData[sk].id || '') + '' });
          }
        }
      } catch(e) {}
    }

    // ── 7. 规格/属性提取 ──
    var specEls = document.querySelectorAll('[data-widget="webCharacteristics"] div, [class*="characteristics"] div, [class*="tsSpec"] div, [class*="specs"] div, [class*="props"] div, [data-widget="webProductInfo"] div');
    var specSeen = {};
    for (var sp = 0; sp < specEls.length && specs.length < 80; sp++) {
      var text = specEls[sp].textContent.trim();
      if (text.length < 3 || text.length > 300) continue;
      var sep = text.indexOf(':') > 0 ? ':' : (text.indexOf('：') > 0 ? '：' : '');
      if (sep <= 0) {
        // 尝试dt/dd结构
        var dt = specEls[sp].querySelector('dt, .tsSpecTitle, [class*="title"]');
        var dd = specEls[sp].querySelector('dd, .tsSpecValue, [class*="value"]');
        if (dt && dd) {
          var n2 = dt.textContent.trim();
          var v2 = dd.textContent.trim();
          if (n2.length > 1 && !specSeen[n2]) { specSeen[n2] = true; specs.push({ name: n2, value: v2 }); }
        }
        continue;
      }
      var n = text.substring(0, sep).trim();
      var v = text.substring(sep + 1).trim();
      if (n.length > 1 && n.length < 60 && v.length > 0 && v.length < 300 && !specSeen[n]) {
        specSeen[n] = true;
        specs.push({ name: n, value: v });
      }
    }
    // 从JSON提取属性
    if (specs.length === 0 && stateData) {
      try {
        var j2 = JSON.stringify(stateData);
        var charMatch = j2.match(/"characteristics"\s*:\s*(\[.*?\])/);
        if (charMatch) {
          var chars = JSON.parse(charMatch[1]);
          for (var cc = 0; cc < chars.length; cc++) {
            specs.push({ name: chars[cc].name || chars[cc].key || '', value: chars[cc].value || chars[cc].text || '' });
          }
        }
      } catch(e) {}
    }

    // ── 8. 富文本描述采集 ──
    var richDesc = '';
    var descWidget = document.querySelector('[data-widget="webDescription"], [data-widget="webDetail"], [class*="description"], [class*="ra"]');
    if (descWidget) richDesc = descWidget.innerHTML || descWidget.textContent || '';
    if (!richDesc) {
      var articleEl = document.querySelector('article, [class*="content"], [class*="text"]');
      if (articleEl) richDesc = articleEl.innerHTML || articleEl.textContent || '';
    }
    if (richDesc && richDesc.length > desc.length) desc = richDesc.substring(0, 50000);

    return {
      title: title, category: category || desc.substring(0, 100), description: desc,
      shop_name: shop, skus: skus, images: images, specs: specs, videos: videos,
      detail_missing: (desc.length < 100)
    };
  }
