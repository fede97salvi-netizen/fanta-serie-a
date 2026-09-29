// Una nuova versione del service worker entra in funzione subito, senza
// aspettare che l'utente chiuda tutte le schede dell'app.
self.addEventListener('install', function () {
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function (event) {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { title: "FantaSerieA", body: event.data ? event.data.text() : "Hai una nuova notifica!" };
    }

    const title = data.title || "FantaSerieA";
    const options = {
        body: data.body || "Hai una nuova notifica!",
        icon: data.icon || "/static/icons/icon-192.png",
        badge: "/static/icons/badge-96.png",
        tag: data.tag || "fantaseriea-notification",
        renotify: true,
        vibrate: [200, 100, 200],
        data: { url: data.url || "/" } // per il click
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
            .catch(err => console.error("Errore mostrando la notifica:", err))
    );
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    const targetUrl = event.notification.data?.url || "/";

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
            for (const client of clientList) {
                if (client.url === targetUrl && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
