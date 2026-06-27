from pathlib import Path
c = Path('content.js').read_text('utf-8')

# Fix 1: extractOzonRichText - more fallbacks, review boundary removal
old = c.find('function extractOzonRichText()')
end = c.find('function extractOzonAttributes', old)
c = c[:old] + '''function extractOzonRichText() {
    var best = null, bestScore = 0;
    var candidates = document.querySelectorAll('[data-widget*="webDescription"],[data-widget*="webDetail"],[data-widget*="rich"],[class*="description"],[class*="rich"],article,main,[class*="content"]');
    if (!candidates.length) candidates = document.querySelectorAll('body');
    candidates.forEach(function(root) {
      if (typeof isInBadArea === 'function' && isInBadArea(root)) return;
      var text = (root.innerText||root.textContent||'').trim();
      if (text.length < 100) return;
      var imgs = root.querySelectorAll('img').length;
      var score = text.length + imgs * 300;
      if (/описание|характеристик|description|about/i.test(text)) score += 500;
      if (score > bestScore) { bestScore = score; best = root; }
    });
    if (!best) return {html:'',plain_text:'',source_selector:'',image_count:0};
    var clone = best.cloneNode(true);
    clone.querySelectorAll('script,style,svg,button,nav,footer,header,[class*="review"],[class*="comment"],[class*="recommend"]').forEach(function(x){x.remove();});
    return { html: clone.innerHTML.slice(0,200000), plain_text: (clone.innerText||clone.textContent||'').trim().slice(0,50000), source_selector: '', image_count: clone.querySelectorAll('img').length, captured_at: new Date().toISOString() };
  }

''' + c[end:]

# Fix 2: extractOzonAttributes - add dl/dt+dd, table rows
old2 = c.find('function extractOzonAttributes(')
end2 = c.find('function extractOzonVideos', old2)
c = c[:old2] + '''function extractOzonAttributes(stateData) {
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
      var parts = txt.split(/:|：|—|–/);
      if (parts.length >= 2) addAttr(parts[0], parts.slice(1).join(':'), 'dom');
    });
    document.querySelectorAll('dl,[class*="props"],[class*="params"]').forEach(function(dl) {
      var dts = dl.querySelectorAll('dt,[class*="name"],[class*="label"]');
      var dds = dl.querySelectorAll('dd,[class*="value"]');
      for (var i=0;i<Math.min(dts.length,dds.length);i++) addAttr(dts[i].textContent.trim(),dds[i].textContent.trim(),'dl');
    });
    document.querySelectorAll('table tr,[class*="row"]').forEach(function(tr) {
      var cells = tr.querySelectorAll('td,th');
      if (cells.length>=2) addAttr(cells[0].textContent.trim(),cells[1].textContent.trim(),'table');
    });
    if (stateData && stateData.length) {
      walkOzonState(stateData[0], function(obj) {
        var name = obj.name || obj.key || obj.title;
        var value = obj.value || obj.text || obj.values;
        if (!name || !value) return;
        if (obj.value!==undefined||obj.characteristicId||obj.attributeId||obj.key)
          addAttr(name, Array.isArray(value)?value.join(', '):String(value), 'json');
      });
    }
    return attrs.slice(0,120);
  }

''' + c[end2:]

# Fix 3: extractOzonVideos - add iframes
old3 = c.find('function extractOzonVideos(')
end3 = c.find('function extractOzonSkus', old3)
c = c[:old3] + '''function extractOzonVideos(stateData) {
    var videos = [], seen = {};
    document.querySelectorAll('video,video source,[class*="player"] video,[class*="media"] video').forEach(function(el) {
      if (typeof isInBadArea==='function'&&isInBadArea(el)) return;
      var src = el.src||el.currentSrc||el.getAttribute('src')||el.getAttribute('data-src')||'';
      if (src&&!seen[src]){seen[src]=true;videos.push({src:src,poster:el.getAttribute('poster')||'',role:'video',source:'dom_video'});}
      el.querySelectorAll('source').forEach(function(s){var ss=s.src||'';if(ss&&!seen[ss]){seen[ss]=true;videos.push({src:ss,role:'video',source:'dom_source'});}});
    });
    document.querySelectorAll('iframe[src*="youtube"],iframe[src*="vk.com"],iframe[src*="rutube"],iframe[src*="yandex"],iframe[src*="vkvideo"],iframe[src*="player"]').forEach(function(el){
      var s=el.src||el.getAttribute('data-src')||'';if(s&&!seen[s]){seen[s]=true;videos.push({src:s,role:'video',source:'iframe'});}
    });
    if(stateData&&stateData.length) walkOzonState(stateData[0],function(obj){
      var url=obj.videoUrl||obj.video||obj.url||obj.src;
      if(url&&/\\.(mp4|webm|mov|m3u8)/i.test(String(url))&&!seen[url]){seen[url]=true;videos.push({src:String(url),poster:obj.poster||obj.preview||obj.cover||'',role:'video',source:'json'});}
    });
    return videos;
  }

''' + c[end3:]

# Fix 4: extractOzonSkus - simplify to just 1 default
old4 = c.find('function extractOzonSkus(')
end4 = c.find('  function extractOzonPricing', old4)
c = c[:old4] + '''function extractOzonSkus(stateData, productInfo, pricing) {
    var name = productInfo.title || '默认规格';
    return [{source_order:1,source_sku_id:'default',source_sku_name:name,style_cn:name,bundle_quantity:1,purchase_price_cny:pricing.reference_price_rub||null}];
  }

''' + c[end4:]

Path('content.js').write_text(c, 'utf-8')
print('V4 fix applied')
