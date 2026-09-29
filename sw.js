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
        vibrate: [200, 100, 200],
        data: { url: data.url || "/" } // per il click
    };
    // Il server manda un tag per partita: notifiche di partite diverse
    // restano tutte visibili, quella nuova della stessa partita sostituisce
    // la vecchia (e fa comunque suonare il telefono grazie a renotify).
    // Senza tag ogni notifica resta a sé.
    if (data.tag) {
        options.tag = data.tag;
        options.renotify = true;
    }

    event.waitUntil(
        self.registration.showNotification(title, options)
            .catch(err => console.error("Errore mostrando la notifica:", err))
    );
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    const targetUrl = new URL(event.notification.data?.url || "/", self.location.origin).href;

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
            // Se l'app e' gia' aperta la si riusa, portandola sulla pagina giusta
            for (const client of clientList) {
                if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
                    const naviga = client.url !== targetUrl && 'navigate' in client
                        ? client.navigate(targetUrl) : Promise.resolve(client);
                    // navigate() puo' fallire su finestre non controllate
                    // dal service worker: in quel caso basta portarla davanti
                    return naviga.then(c => (c || client).focus())
                                 .catch(() => client.focus());
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
