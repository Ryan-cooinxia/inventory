from pathlib import Path
c = Path('content.js').read_text('utf-8')

# 1. Add helper functions before extractOzonProduct
helpers = '''
  async function preloadOzonDetailSections() {
    var startY = window.scrollY;
    try {
      var btns = Array.from(document.querySelectorAll('button,a,div[role="button"],span'));
      var descBtn = btns.find(function(el) {
        var t = (el.innerText||el.textContent||'').trim();
        return /Перейти к описанию|Описание|Характеристики|О товаре/i.test(t) && t.length < 50;
      });
      if (descBtn && descBtn.click) { descBtn.click(); await sleep(300); await sleep(1200); }
      var maxY = Math.min(document.body.scrollHeight, startY + 6000);
      for (var y = window.scrollY; y < maxY; y += 700) {
        window.scrollTo(0, y); await sleep(400);
        if (findOzonTextAnchor(['Описание','Характеристики','О товаре'])) { await sleep(800); break; }
      }
    } catch(e) { console.warn('[OZON] preload failed',e); }
    finally { window.scrollTo(0, startY); await sleep(200); }
  }
  function sleep(ms) { return new Promise(function(r){setTimeout(r,ms);}); }
  function findOzonTextAnchor(words) {
    return Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,p')).find(function(el) {
      var t = (el.innerText||el.textContent||'').trim();
      return words.some(function(w){return t===w||t.indexOf(w)>=0;});
    });
  }
  function getUsefulTextLength(el) {
    if (!el) return 0;
    var t = (el.innerText||el.textContent||'').trim();
    if (/Отзывы|Фото и видео покупателей|Похожие|Рекомендуем/i.test(t.slice(0,500))) return 0;
    return t.length;
  }
  function findDescriptionRootByAnchor() {
    var anchors = ['Описание','Описание товара','О товаре'];
    var candidates = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,section'));
    for (var i=0;i<candidates.length;i++) {
      var el=candidates[i], t=(el.innerText||el.textContent||'').trim();
      if (!t||t.length>100) continue;
      var matched=anchors.some(function(a){return t===a||t.indexOf(a)>=0;});
      if (!matched) continue;
      var section=el.closest('section');
      if (section&&getUsefulTextLength(section)>200) return section;
      var block=document.createElement('div');
      var node=el.nextElementSibling,count=0;
      while (node&&count<12) {
        var txt=(node.innerText||node.textContent||'').trim();
        if (/Характеристики|Отзывы|Вопросы|Похожие|Рекомендуем/i.test(txt.slice(0,100))) break;
        if (txt.length>20||node.querySelector('img')) block.appendChild(node.cloneNode(true));
        node=node.nextElementSibling;count++;
      }
      if (getUsefulTextLength(block)>200) return block;
    }
    return null;
  }
  function isBuyerMediaArea(el) {
    var node=el;
    while (node&&node!==document.body) {
      var t=(node.innerText||node.textContent||'').slice(0,300);
      if (/Фото и видео покупателей|Отзывы|Вопросы|покупателей/i.test(t)) return true;
      node=node.parentElement;
    }
    return false;
  }
  function extractOzonMainProductVideo() {
    var videos=[],seen={};
    var gallery=document.querySelector('[data-widget*="webGallery"],[data-widget*="gallery"]');
    if (!gallery) return [{role:'main_video',url:'',poster:'',duration_text:'',source_area:'main_gallery',source:'gallery_not_found',need_manual_check:true}];
    gallery.querySelectorAll('video,video source').forEach(function(el){
      if (isBuyerMediaArea(el)) return;
      var src=el.currentSrc||el.src||el.getAttribute('src')||el.getAttribute('data-src')||'';
      var poster=el.getAttribute('poster')||'';
      var key=src||poster; if(!key||seen[key]) return; seen[key]=true;
      videos.push({role:'main_video',url:src||'',poster:poster||'',duration_text:'',source_area:'main_gallery',source:'video_tag',need_manual_check:!src});
    });
    gallery.querySelectorAll('img,button,div,a').forEach(function(el){
      if (isBuyerMediaArea(el)) return;
      var t=(el.innerText||el.textContent||'').trim();
      var cls=el.className||''; var aria=el.getAttribute('aria-label')||'';
      var hasPlay=/video|play|Видео|видео/i.test(t+' '+aria+' '+cls);
      var dur=findNearbyDuration(el);
      var img=el.tagName==='IMG'?el:el.querySelector&&el.querySelector('img');
      var poster=img?(img.src||img.getAttribute('data-src')||''):'';
      if (hasPlay||dur) {
        var key=poster||dur; if(!key||seen[key]) return; seen[key]=true;
        videos.push({role:'main_video',url:'',poster:poster,duration_text:dur,source_area:'main_gallery',source:'video_thumb',need_manual_check:true});
      }
    });
    return videos.length?videos:[{role:'main_video',url:'',poster:'',duration_text:'',source_area:'main_gallery',source:'no_video_found',need_manual_check:true}];
  }
  function findNearbyDuration(el) {
    var cur=el;
    for (var i=0;i<4&&cur;i++) { var txt=(cur.innerText||cur.textContent||'').trim(); var m=txt.match(/\\b\\d{1,2}:\\d{2}\\b/); if(m) return m[0]; cur=cur.parentElement; }
    return '';
  }
'''

insert_at = c.find('  function extractOzonProduct() {')
c = c[:insert_at] + helpers + chr(10) + c[insert_at:]

# 2. Rewrite extractOzonRichText
old_start = c.find('  function extractOzonRichText() {')
old_end = c.find('  function extractOzonAttributes', old_start)

new_rt = '''  function extractOzonRichText() {
    var result = {plain_text:'',html:'',sections:[],image_urls:[],image_count:0,source:'',captured_at:new Date().toISOString(),debug:{}};
    var root = findDescriptionRootByAnchor();
    if (!root) { result.debug={reason:'description_root_not_found'}; return result; }
    var clone=root.cloneNode(true);
    clone.querySelectorAll('script,style,button,nav,header,footer,svg').forEach(function(x){x.remove();});
    Array.from(clone.querySelectorAll('*')).forEach(function(el){
      var t=(el.innerText||el.textContent||'').slice(0,200);
      if (/Отзывы|Вопросы|Фото и видео покупателей|Похожие товары|Рекомендуем|Магазин/i.test(t)) el.remove();
    });
    var text=(clone.innerText||clone.textContent||'').replace(/\\n{3,}/g,'\\n\\n').trim();
    result.html=clone.innerHTML.slice(0,200000);
    result.plain_text=text.slice(0,50000);
    result.image_urls=Array.from(clone.querySelectorAll('img')).map(function(img){return img.src||img.getAttribute('data-src')||'';}).filter(Boolean);
    result.image_count=result.image_urls.length;
    result.source='description_anchor';
    return result;
  }

'''

c = c[:old_start] + new_rt + c[old_end:]

# 3. Update video call
c = c.replace(
    'var v4Videos = extractOzonVideos(stateObjects);',
    'var mainVideos = extractOzonMainProductVideo(); var stateVideos = extractOzonVideos(stateObjects); var v4Videos = mainVideos.length ? mainVideos : stateVideos;'
)

Path('content.js').write_text(c, 'utf-8')
print('V5 patch applied: preload + anchor RT + main video')
