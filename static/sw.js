// 缓存名称（更新版本可强制刷新）
const CACHE_NAME = 'inventory-v1';

// 需要预缓存的资源列表（可根据实际需要调整）
const PRE_CACHE_URLS = [
    '/',
    '/static/manifest.json',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// 安装事件：预缓存关键资源
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('Opened cache');
            return cache.addAll(PRE_CACHE_URLS);
        })
    );
});

// 激活事件：清理旧缓存
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
});

// 拦截请求：优先从缓存读取，缓存未命中时网络请求并更新缓存
self.addEventListener('fetch', (event) => {
    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
            // 如果缓存命中，直接返回；同时发起网络请求更新缓存（后台）
            const fetchPromise = fetch(event.request).then((networkResponse) => {
                // 仅缓存成功响应的 GET 请求
                if (networkResponse && networkResponse.status === 200 && event.request.method === 'GET') {
                    const responseClone = networkResponse.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseClone);
                    });
                }
                return networkResponse;
            }).catch(() => {
                // 网络请求失败（如离线），不处理
            });
            return cachedResponse || fetchPromise;
        })
    );
});