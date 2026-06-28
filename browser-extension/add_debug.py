from pathlib import Path
c = Path('content.js').read_text('utf-8')

# Add debug logging
c = c.replace(
    'var rich = extractOzonRichTextAtViewport();',
    "var rich = extractOzonRichTextAtViewport(); console.log('[OZON DEBUG] scrollY='+window.scrollY+' richText chars='+(rich&&rich.plain_text?rich.plain_text.length:0)+' debug='+JSON.stringify(rich&&rich.debug||{}));"
)

c = c.replace(
    'var mainVideos = extractOzonMainProductVideo();',
    "var mainVideos = extractOzonMainProductVideo(); console.log('[OZON DEBUG] mainVideos count='+mainVideos.length+' hasPoster='+!!(mainVideos[0]&&mainVideos[0].poster));"
)

c = c.replace(
    'window.__ozonCollectedRichText = null;',
    "window.__ozonCollectedRichText = null; console.log('[OZON DEBUG] preloadOzonDetailSections START scrollY='+window.scrollY);"
)

Path('content.js').write_text(c, 'utf-8')
print('Debug added')
