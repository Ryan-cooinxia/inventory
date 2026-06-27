from pathlib import Path
c = Path('content.js').read_text('utf-8')

helpers = """
  function walkOzonState(obj, visitor, depth) {
    depth = depth || 0;
    if (!obj || depth > 8) return;
    if (Array.isArray(obj)) { obj.slice(0, 500).forEach(function(x) { walkOzonState(x, visitor, depth + 1); }); return; }
    if (typeof obj !== 'object') return;
    visitor(obj);
    Object.keys(obj).slice(0, 500).forEach(function(k) { if (obj[k] && typeof obj[k] === 'object') walkOzonState(obj[k], visitor, depth + 1); });
  }

  function parseOzonRubPrice(text) {
    if (!text || !/(\\u20bd|\\u0440\\u0443\\u0431|RUB)/i.test(text)) return null;
    var m = text.match(/([0-9][0-9\\s.,]{1,14})\\s*(\\u20bd|\\u0440\\u0443\\u0431|RUB)/i);
    if (!m) return null;
    var raw = m[1].replace(/[\\s]/g, '').replace(',', '.');
    var n = parseFloat(raw);
    if (!isFinite(n) || n <= 1 || n > 10000000) return null;
    return Math.round(n * 100) / 100;
  }

  function normalizeOzonPriceNumber(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'string') {
      var wc = parseOzonRubPrice(v);
      if (wc) return wc;
      var raw = v.replace(/[\\s]/g, '').replace(',', '.').replace(/[^\\d.]/g, '');
      var n = parseFloat(raw);
      if (!isFinite(n) || n <= 1 || n > 10000000) return null;
      return Math.round(n * 100) / 100;
    }
    var n = Number(v);
    if (!isFinite(n) || n <= 1 || n > 10000000) return null;
    return Math.round(n * 100) / 100;
  }

  function extractOzonPricing(stateData) {
    var result = { reference_price_rub: null, currency: 'RUB', source: '', confidence: 'low', price_candidates: [] };
    function addPrice(price, source, confidence, text) {
      if (!price || result.reference_price_rub) return;
      result.reference_price_rub = price; result.source = source; result.confidence = confidence || 'medium';
      result.price_candidates.push({ price: price, currency: 'RUB', source: source, confidence: confidence || 'medium', text: (text||'').slice(0,200), note: 'OZON reference price, needs manual confirmation' });
    }
    document.querySelectorAll('[data-widget*="webPrice"],[data-widget*="webSaleBlock"],[class*="price"],[class*="Price"]').forEach(function(el) {
      if (typeof isInBadArea === 'function' && isInBadArea(el)) return;
      var text = (el.textContent||'').replace(/\\s+/g,' ').trim();
      var p = parseOzonRubPrice(text);
      addPrice(p, 'dom_price_area', 'medium', text);
    });
    var meta = document.querySelector('meta[property="product:price:amount"],meta[itemprop="price"]');
    if (meta && !result.reference_price_rub) {
      var mn = parseFloat((meta.getAttribute('content')||'').replace(/[^\\d.]/g,''));
      if (isFinite(mn) && mn > 1) addPrice(mn, 'meta', 'medium', meta.getAttribute('content'));
    }
    if (stateData && stateData.length && !result.reference_price_rub) {
      walkOzonState(stateData[0], function(obj) {
        if (result.reference_price_rub) return;
        var raw = obj.finalPrice || obj.salePrice || obj.cardPrice || obj.currentPrice || obj.price;
        var n = normalizeOzonPriceNumber(raw);
        if (n) addPrice(n, 'state_json', 'medium', String(raw));
      });
    }
    return result;
  }

  function extractOzonSkus(stateData, productInfo, pricing) {
    var skus = []; var seen = {};
    function addSku(sku) {
      var name = (sku.source_sku_name || '').trim();
      if (!name || name.length < 2 || name.length > 120) return;
      if (/(\\u043a\\u0443\\u043f\\u0438\\u0442\\u044c|\\u043a\\u043e\\u0440\\u0437\\u0438\\u043d|\\u0441\\u0440\\u0430\\u0432\\u043d\\u0438\\u0442\\u044c|\\u0438\\u0437\\u0431\\u0440\\u0430\\u043d|\\u043e\\u0442\\u0437\\u044b\\u0432|\\u0432\\u043e\\u043f\\u0440\\u043e\\u0441|\\u0434\\u043e\\u0441\\u0442\\u0430\\u0432\\u043a\\u0430|review|cart|favorite)/i.test(name)) return;
      var key = name.toLowerCase(); if (seen[key]) return; seen[key] = true;
      skus.push({ source_order: skus.length+1, source_sku_id: sku.source_sku_id||('sku-'+(skus.length+1)), source_sku_name: name, style_cn: sku.style_cn||name, bundle_quantity: sku.bundle_quantity||1, purchase_price_cny: sku.purchase_price_cny||pricing.reference_price_rub||null });
    }
    document.querySelectorAll('[data-widget*="webVariant"],[data-widget*="webAspects"],[class*="sku"],[class*="variant"],[class*="option"]').forEach(function(root) {
      if (typeof isInBadArea === 'function' && isInBadArea(root)) return;
      root.querySelectorAll('button,a,div,span,label,[role="button"],[aria-label],[title]').forEach(function(el) {
        var name = (el.getAttribute('aria-label')||el.getAttribute('title')||el.textContent||'').trim();
        addSku({ source_sku_name: name, style_cn: name });
      });
    });
    if (stateData && stateData.length) {
      walkOzonState(stateData[0], function(obj) {
        var name = obj.skuName || obj.name || obj.title || obj.label || obj.value;
        var id = obj.skuId || obj.offerId || obj.id;
        if (!name || !id) return;
        if (obj.skuId || obj.offerId || obj.available !== undefined || obj.isAvailable !== undefined) {
          addSku({ source_sku_id: String(id), source_sku_name: name.trim(), style_cn: name.trim(), purchase_price_cny: normalizeOzonPriceNumber(obj.finalPrice||obj.salePrice||obj.cardPrice||obj.currentPrice||obj.price) });
        }
      });
    }
    if (!skus.length) addSku({ source_sku_name: productInfo.title || 'default', style_cn: productInfo.title || 'default' });
    return skus;
  }

  function extractOzonRichText() {
    var best = null, bestScore = 0;
    document.querySelectorAll('[data-widget*="webDescription"],[data-widget*="rich"],[class*="description"],[class*="rich"],[class*="content"],article').forEach(function(root) {
      if (typeof isInBadArea === 'function' && isInBadArea(root)) return;
      var text = (root.innerText||root.textContent||'').trim();
      var imgs = root.querySelectorAll('img').length;
      var score = text.length + imgs * 300;
      if (/\\u043e\\u043f\\u0438\\u0441\\u0430\\u043d\\u0438\\u0435|\\u0445\\u0430\\u0440\\u0430\\u043a\\u0442\\u0435\\u0440\\u0438\\u0441\\u0442\\u0438\\u043a|description/i.test(text)) score += 500;
      if (score > bestScore) { bestScore = score; best = root; }
    });
    if (!best) return {html:'',plain_text:'',source_selector:'',image_count:0};
    var clone = best.cloneNode(true);
    clone.querySelectorAll('script,style,svg,button,nav,footer,header').forEach(function(x){x.remove();});
    return { html: clone.innerHTML.slice(0,200000), plain_text: (clone.innerText||clone.textContent||'').trim().slice(0,50000), source_selector: '', image_count: clone.querySelectorAll('img').length, captured_at: new Date().toISOString() };
  }

  function extractOzonAttributes(stateData) {
    var attrs = [], seen = {};
    function addAttr(name, value, source) {
      name = (name||'').trim(); value = (value||'').trim();
      if (!name || !value || name.length > 80 || value.length > 500) return;
      var key = name.toLowerCase()+'='+value.toLowerCase();
      if (seen[key]) return; seen[key] = true;
      attrs.push({name:name,value:value,source:source||'dom',source_text:name+': '+value});
    }
    document.querySelectorAll('[data-widget*="webCharacteristics"] *,[data-widget*="webShortCharacteristics"] *,[class*="characteristic"] *,[class*="spec"] *').forEach(function(el) {
      if (typeof isInBadArea === 'function' && isInBadArea(el)) return;
      var txt = (el.textContent||'').trim();
      if (!txt || txt.length > 300) return;
      var parts = txt.split(/:|\\uff1a|\\u2014|\\u2013/);
      if (parts.length >= 2) addAttr(parts[0], parts.slice(1).join(':'), 'dom_characteristics');
    });
    if (stateData && stateData.length) {
      walkOzonState(stateData[0], function(obj) {
        var name = obj.name || obj.key || obj.title;
        var value = obj.value || obj.text || obj.values;
        if (!name || !value) return;
        if (obj.value !== undefined || obj.characteristicId || obj.attributeId || obj.key) {
          addAttr(name, Array.isArray(value)?value.join(', '):String(value), 'state_json');
        }
      });
    }
    return attrs.slice(0, 120);
  }

  function extractOzonVideos(stateData) {
    var videos = [], seen = {};
    document.querySelectorAll('video, video source').forEach(function(el) {
      if (typeof isInBadArea === 'function' && isInBadArea(el)) return;
      var src = el.src || el.getAttribute('src') || el.getAttribute('data-src') || '';
      var poster = el.getAttribute('poster') || '';
      var key = src || poster;
      if (key && !seen[key]) { seen[key]=true; videos.push({src:src,poster:poster,role:'video',source:'dom_video'}); }
    });
    if (stateData && stateData.length) {
      walkOzonState(stateData[0], function(obj) {
        var url = obj.videoUrl || obj.video || obj.url || obj.src;
        if (url && /\\.(mp4|webm|mov|m3u8)/i.test(String(url)) && !seen[url]) { seen[url]=true; videos.push({src:String(url),poster:obj.poster||obj.preview||obj.cover||'',role:'video',source:'state_json'}); }
      });
    }
    return videos;
  }
"""

# Insert before extractOzonProduct
insert_at = c.find('  function extractOzonProduct() {')
c = c[:insert_at] + helpers + '\n' + c[insert_at:]

# Update extractOzonProduct return
old_ret = 'return {\n      title: title, category: category || desc.substring(0, 100), description: richText.plain_text || desc,\n      shop_name: shop, skus: skus, images: images, specs: specs, videos: videos,\n      rich_text: richText, attribute_candidates: specs,\n      rejected_images: rejectedImages,\n      detail_missing: images.filter(function(x){return x.role===\\\'detail\\\';}).length === 0,\n      debug: debug\n    };'

new_ret = 'var productInfo = {title:title, category:category||desc.substring(0,100), shop_name:shop, description:desc};\n    var v4RichText = extractOzonRichText();\n    var v4Pricing = extractOzonPricing(stateObjects);\n    var v4Attrs = extractOzonAttributes(stateObjects);\n    var v4Videos = extractOzonVideos(stateObjects);\n    var v4Skus = extractOzonSkus(stateObjects, productInfo, v4Pricing);\n    return {\n      title: title, category: category || desc.substring(0, 100), description: v4RichText.plain_text || desc,\n      shop_name: shop, skus: v4Skus.length ? v4Skus : skus, images: images,\n      specs: v4Attrs, attribute_candidates: v4Attrs,\n      videos: v4Videos.length ? v4Videos : videos, video_candidates: v4Videos,\n      rich_text: v4RichText, pricing: v4Pricing, price_candidates: v4Pricing.price_candidates || [],\n      rejected_images: rejectedImages,\n      detail_missing: !v4RichText.plain_text && !desc,\n      debug: debug\n    };'

if old_ret in c:
    c = c.replace(old_ret, new_ret)
    print('Return statement updated')
else:
    print('Return pattern not found - checking alternatives...')
    # The function might end with a simpler return
    for i in range(c.rfind('richText'), min(c.rfind('richText')+500, len(c))):
        if 'return {' in c[i:i+10]:
            print(f'Found return at offset {i}: {c[i:i+100]}')
            break

Path('content.js').write_text(c, 'utf-8')
print('V4 patch applied')
