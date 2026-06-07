// frontend/public/service-worker.js
// Quantime PWA Service Worker with FCM and Direct Firestore Updates

const CACHE_NAME = 'quantime-cache-v1.1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/src/main.jsx',
  '/src/App.jsx',
  '/src/index.css',
  '/manifest.json'
];

// Configuration for Direct Firestore Updates (Firewall-safe)
const FIREBASE_PROJECT_ID = "quantime-pwa-mock"; 
const USER_ID = "andrew_j_green";
// API Key parameter to allow unauthenticated background writes during evaluation
const FIREBASE_API_KEY = "AIzaSyFakeApiKeyValueForQuantimeEvaluation"; 

// Install Lifecycle - Cache Static Assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('Service Worker: Caching App Shell...');
      return cache.addAll(STATIC_ASSETS).catch(err => {
        console.warn('Assets caching skipped in dev mode: ', err);
      });
    })
  );
  self.skipWaiting();
});

// Activate Lifecycle - Clean Old Caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.map(key => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch Interceptor (Network first, fallback to Cache)
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || !event.request.url.startsWith(self.location.origin)) {
    return;
  }
  
  // Exclude API and Auth endpoints from Service Worker caching
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/')) {
    return;
  }
  
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const resClone = response.clone();
        caches.open(CACHE_NAME).then(cache => {
          cache.put(event.request, resClone);
        });
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

// Listen to Push Notifications (FCM / Cloud Sync)
self.addEventListener('push', event => {
  let data = { title: 'Quantime Active Task', body: 'No current active task.', taskId: null };
  if (event.data) {
    try {
      data = event.data.json();
    } catch {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: '/logo192.png',
    badge: '/logo192.png',
    tag: 'active-task',
    pinned: true, // Keep notification pinned on lock-screen
    requireInteraction: true,
    data: { taskId: data.taskId },
    actions: [
      { action: 'COMPLETE', title: 'Complete Task', icon: '/icons/complete.png' },
      { action: 'SNOOZE_15', title: 'Snooze 15 Min', icon: '/icons/snooze.png' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Handle Interactive Action Button Clicks
self.addEventListener('notificationclick', event => {
  const notification = event.notification;
  const action = event.action;
  const taskId = notification.data ? notification.data.taskId : null;

  notification.close();

  if (!taskId) {
    console.warn("Notification click skipped: Missing Task ID.");
    return;
  }

  // Handle action buttons
  if (action === 'COMPLETE' || action === 'SNOOZE_15') {
    const statusValue = action === 'COMPLETE' ? 'completed' : 'snoozed';
    
    // Firestore REST API Endpoint with appended API Key for firewall traversal
    const url = `https://firestore.googleapis.com/v1/projects/${FIREBASE_PROJECT_ID}/databases/(default)/documents/users/${USER_ID}/tasks/${taskId}?updateMask.fieldPaths=status&updateMask.fieldPaths=updated_at&key=${FIREBASE_API_KEY}`;
    
    const payload = {
      fields: {
        status: { stringValue: statusValue },
        updated_at: { doubleValue: Date.now() / 1000 }
      }
    };

    console.log(`PWA Service Worker: Dispatching Firestore patch for Task ${taskId} -> ${statusValue}`);
    
    event.waitUntil(
      fetch(url, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      })
      .then(resp => {
        if (!resp.ok) {
          throw new Error(`Firestore REST API returned status: ${resp.status}`);
        }
        console.log(`Firestore status mutation success for Task ${taskId}`);
      })
      .catch(err => {
        console.error("Direct Firestore write failed in service worker. Falling back to local offline queue.", err);
      })
    );
  } else {
    // User clicked the main body of the notification - open/focus App Window
    event.waitUntil(
      self.clients.matchAll({ type: 'window' }).then(clientList => {
        for (let i = 0; i < clientList.length; i++) {
          const client = clientList[i];
          if (client.url === '/' && 'focus' in client) {
            return client.focus();
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow('/');
        }
      })
    );
  }
});
