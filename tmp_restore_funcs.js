function openSourceGallery() {
  var overlay = document.getElementById('sourceGalleryOverlay');
  var grid = document.getElementById('galleryGrid');
  var stats = document.getElementById('galleryStats');

  if (!allSourceMedia || !allSourceMedia.length) {
    grid.innerHTML = '<div class="col-12 text-center text-white py-5">暂无图片数据</div>';
    overlay.style.display = 'block';
    return;
  }

  // 按角色统计（5 分组）
  var notRejected = function(m) { return m.compliance_status !== 'rejected'; };
  var mainCount    = allSourceMedia.filter(function(m) { return m.role === 'main' && notRejected(m) && m.compliance_status !== 'needs_review'; }).length;
  var skuCount     = allSourceMedia.filter(function(m) { return m.role === 'sku' && notRejected(m) && m.compliance_status !== 'needs_review'; }).length;
  var detailCount  = allSourceMedia.filter(function(m) { return m.role === 'detail' && notRejected(m) && m.compliance_status !== 'needs_review'; }).length;
  var reviewCount  = allSourceMedia.filter(function(m) { return m.compliance_status === 'needs_review'; }).length;
  var rejected     = allSourceMedia.filter(function(m) { return m.compliance_status === 'rejected'; }).length;

  var statsText = '主图 ' + mainCount +
    ' | SKU图 ' + skuCount +
    ' | 详情图 ' + detailCount +
    (reviewCount > 0 ? ' | 待审核 ' + reviewCount : '') +
    ' | 已过滤 ' + rejected;
  var warnings = [];
  var sourcePlatform = '{{ source.platform }}';
  if (sourcePlatform === 'taobao' || sourcePlatform === 'tmall') {
    if (detailCount === 0) {
      warnings.push('<span class="text-muted">ℹ️ 淘宝/天猫详情图请通过 1688 同款或手动补图</span>');
    }
  } else if (sourcePlatform === '1688') {
    if (detailCount === 0) {
      warnings.push('<span style="color:#ffc107">⚠️ 未采集到详情图，建议重新采集或手动补图</span>');
    }
    if (skuCount === 0 && allSourceMedia.filter(function(m) { return m.role === 'sku'; }).length === 0) {
      var skuTotal = {{ source.sku_count or 0 }};
      if (skuTotal > 0) {
        warnings.push('<span style="color:#ffc107">⚠️ SKU 图未绑定到规格项，请检查 SKU 区域选择器</span>');
      }
    }
  }
  stats.innerHTML = statsText + (warnings.length ? '<br>' + warnings.join('<br>') : '');

  // 按角色分组排序（5 分组）
  function getGroupKey(m) {
    if (m.compliance_status === 'rejected') return 'reject';
    if (m.compliance_status === 'needs_review') return 'review';
    if (m.role === 'main') return 'main';
    if (m.role === 'sku') return 'sku';
    if (m.role === 'detail') return 'detail';
    return 'reject';
  }
  var groupOrder = {'main': 0, 'sku': 1, 'detail': 2, 'review': 3, 'reject': 4};
  var groupLabels = {
    'main': '📷 主图', 'sku': '🎨 SKU 图',
    'detail': '📝 详情图', 'review': '⚠️ 待审核', 'reject': '🚫 已过滤'
  };
  var sorted = allSourceMedia.slice().sort(function(a, b) {
    return (groupOrder[getGroupKey(a)] || 4) - (groupOrder[getGroupKey(b)] || 4);
  });

  // 渲染网格（按角色分组）
  var html = '';
  var lastGroup = '';
  sorted.forEach(function(m, i) {
    var compStatus = m.compliance_status || 'usable';
    var groupKey = getGroupKey(m);
    if (groupKey !== lastGroup) {
      html += '<div class="col-12"><h6 class="text-white mt-2 mb-1" style="font-size:13px;border-bottom:1px solid #444;padding-bottom:4px">' + (groupLabels[groupKey] || groupKey) + '</h6></div>';
      lastGroup = groupKey;
    }
    var statusLabel = STATUS_LABELS[compStatus] || compStatus;
    var statusColor = STATUS_COLORS[compStatus] || '#6c757d';
    var isRejected = compStatus === 'rejected';
    var isReview = compStatus === 'needs_review';
    var reason = m.reject_reason || '';
    var sourceArea = m.source_area || 'unknown';
    var areaLabel = AREA_LABELS[sourceArea] || sourceArea;
    var dimensions = (m.width > 0 && m.height > 0) ? (m.width + '×' + m.height) : '';
    var altText = m.alt || '';
    var srcLabel = getSourceLabel(m);
    // 找到在原始数组中的索引（用于大图查看）
    var origIdx = allSourceMedia.indexOf(m);

    html += '<div class="col-6 col-md-4 col-lg-3">' +
      '<div class="card bg-dark text-white h-100" style="border-color:' + statusColor + ';border-width:' + (isRejected ? '1px' : '2px') + '">' +
        // 缩略图
        '<div style="height:160px;background:#1a1a2e;display:flex;align-items:center;justify-content:center;cursor:pointer" ' +
             'onclick="galleryViewFullByIndex(' + origIdx + ')">' +
          '<img src="' + m.source_url + '" loading="lazy" referrerpolicy="no-referrer" ' +
               'style="max-width:100%;max-height:160px;object-fit:contain' + (isRejected ? ';opacity:.4' : '') + '" ' +
               'onerror="this.parentElement.innerHTML=\'<div style=color:#999;font-size:12px>图片加载失败</div>\'">' +
        '</div>' +
        // 信息区
        '<div class="card-body p-2" style="font-size:11px">' +
          '<div class="d-flex justify-content-between align-items-start mb-1">' +
            srcLabel +
            '<span class="badge" style="font-size:9px;background:' + statusColor + '">' + statusLabel + '</span>' +
          '</div>' +
          '<div class="text-muted mb-1" style="font-size:9px">' + areaLabel +
            (dimensions ? ' <span style="color:#6c757d">' + dimensions + '</span>' : '') +
          '</div>' +
          (m.linked_sku_name ? '<div class="mb-1"><span class="badge bg-info" style="font-size:8px">SKU: ' + m.linked_sku_name.replace(/&/g,'&amp;').replace(/</g,'&lt;').substring(0,25) + '</span></div>' : '') +
          (m.collect_reason ? '<div class="text-muted mb-1" style="font-size:8px">' + m.collect_reason.replace(/&/g,'&amp;').replace(/</g,'&lt;').substring(0,40) + '</div>' : '') +
          (altText ? '<div class="text-muted mb-1" style="font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="' + altText.replace(/"/g, '&quot;') + '">alt: ' + altText + '</div>' : '') +
          (reason ? '<div class="mb-1" style="font-size:9px;color:' + statusColor + ';word-break:break-all">' + reason.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') + '</div>' : '') +
          '<div class="d-flex gap-1 mt-1">' +
            // 查看大图（使用索引避免 URL 特殊字符问题）
            '<button class="btn btn-sm btn-outline-light flex-grow-1" style="font-size:9px;padding:2px 6px" onclick="galleryViewFullByIndex(' + origIdx + ')">🔍 大图</button>' +
            // 恢复按钮（仅被过滤的显示）
            (isRejected || isReview ?
              '<button class="btn btn-sm btn-outline-success" style="font-size:9px;padding:2px 6px" onclick="restoreMedia(' + m.id + ', this)">↩ 恢复</button>' : '') +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>';
  });

  grid.innerHTML = html;
  overlay.style.display = 'block';
  document.body.style.overflow = 'hidden';
}

function closeSourceGallery() {
  document.getElementById('sourceGalleryOverlay').style.display = 'none';
  document.getElementById('galleryFullView').style.display = 'none';
  document.body.style.overflow = '';
}

function galleryViewFull(url) {
  var view = document.getElementById('galleryFullView');
  var img = document.getElementById('galleryFullImg');
  img.src = url;
  view.style.display = 'block';
}

function galleryViewFullByIndex(index) {
  if (allSourceMedia && allSourceMedia[index]) {
    galleryViewFull(allSourceMedia[index].source_url);
  }
}

// ESC 关闭
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    var gallery = document.getElementById('sourceGalleryOverlay');
    var fullView = document.getElementById('galleryFullView');
    if (fullView.style.display === 'block') {
      fullView.style.display = 'none';
    } else if (gallery.style.display === 'block') {
      closeSourceGallery();
    }
  }
});

// 恢复被过滤的图片
function restoreMedia(mediaId, btn) {
  if (!confirm('确认将此图片恢复为可用？')) return;
  btn.disabled = true;
  btn.textContent = '⏳';
  fetch('/ozon/api/source-media/' + mediaId + '/restore', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        // 更新本地数据
        var found = allSourceMedia.find(function(m) { return m.id === mediaId; });
        if (found) {
          found.compliance_status = 'usable';
          found.reject_reason = '人工恢复';
          found.review_status = 'approved';
        }
        // 刷新图库
        openSourceGallery();
      } else {
        alert('恢复失败: ' + (d.error || ''));
        btn.disabled = false;
        btn.textContent = '↩ 恢复';
      }
    })
    .catch(function(e) {
      alert('请求失败: ' + e.message);
      btn.disabled = false;
      btn.textContent = '↩ 恢复';
    });
}

// ── 手动补图 ──────────────────────────────────────
function toggleManualUpload() {
  var panel = document.getElementById('manualUploadPanel');
  var toggle = document.getElementById('manualUploadToggle');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    toggle.textContent = '▼';
  } else {
    panel.style.display = 'none';
    toggle.textContent = '▶';
  }
}

