// Service worker do app do celular: só os avisos (nada de cache: o app sempre fala com o PC).

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

self.addEventListener("push", (e) => {
  let d = { titulo: "Studio Spurgeon", texto: "Há novidades no PC.", url: "/", marca: "spurgeon" };
  try { d = { ...d, ...e.data.json() }; } catch (_) { /* aviso sem corpo */ }
  e.waitUntil(self.registration.showNotification(d.titulo, {
    body: d.texto, icon: "/icone-192.png", badge: "/icone-192.png",
    tag: d.marca, renotify: true, data: { url: d.url },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
  e.waitUntil((async () => {
    const janelas = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const j of janelas) {
      if ("focus" in j) { await j.focus(); if ("navigate" in j) await j.navigate(url); return; }
    }
    await self.clients.openWindow(url);
  })());
});
