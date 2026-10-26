// ============================================================
// サービスワーカー（スマホのホーム画面に置いたとき、アプリとして扱ってもらうための部品）
//   データは常に最新を読みたいので、何も保存（キャッシュ）せず、そのままラズパイに取りに行く
// ============================================================
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => e.respondWith(fetch(e.request)));
