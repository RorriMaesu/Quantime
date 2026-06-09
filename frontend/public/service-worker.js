// frontend/public/service-worker.js
// Quantime PWA Service Worker with FCM and Direct Firestore Updates

const CACHE_NAME = 'quantime-cache-v1.5.2';
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

// Listen to Push Notifications (FCM / Cloud Sync / Agent Alerts)
self.addEventListener('push', event => {
  let data = { title: 'Quantime Active Task', body: 'No current active task.', taskId: null, silent: false, category: 'start' };
  if (event.data) {
    try {
      data = event.data.json();
    } catch {
      data.body = event.data.text();
    }
  }

  const isSilent = data.silent === true || data.silent === 'true';
  const category = data.category || 'start';

  // Customize options based on notification category
  let actions = [];
  if (category === 'clarification') {
    actions = [
      { action: 'OPEN_CHAT', title: 'Reply to AI 💬' },
      { action: 'DISMISS', title: 'Dismiss' }
    ];
  } else if (category === 'important_email') {
    actions = [
      { action: 'OPEN_INBOX', title: 'View Inbox ✉️' },
      { action: 'DISMISS', title: 'Dismiss' }
    ];
  } else if (!isSilent) {
    actions = [
      { action: 'COMPLETE', title: 'Complete Task' },
      { action: 'SNOOZE_15', title: 'Snooze 10 Min' }
    ];
  }

  const options = {
    body: data.body,
    icon: '/logo192.png',
    badge: '/logo192.png',
    sound: '/chime.wav',
    tag: data.taskId ? `${category}-${data.taskId}` : category,
    pinned: true, // Keep notification pinned on lock-screen
    requireInteraction: true, // Persist on screen until clicked or swiped away
    silent: isSilent,
    vibrate: isSilent ? [] : [200, 100, 200],
    data: { taskId: data.taskId, category: category },
    actions: actions
  };

  // Broadcast sound playback command to active client tabs
  const broadcastPromise = self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
    clients.forEach(client => {
      client.postMessage({
        type: 'PLAY_CHIME',
        silent: isSilent
      });
    });
  });

  event.waitUntil(
    Promise.all([
      self.registration.showNotification(data.title, options),
      broadcastPromise
    ])
  );
});

// Handle Interactive Action Button Clicks
self.addEventListener('notificationclick', event => {
  const notification = event.notification;
  const action = event.action;
  const taskId = notification.data ? notification.data.taskId : null;
  const category = notification.data ? notification.data.category : 'start';

  notification.close();

  // If dismiss or close button is clicked, do nothing
  if (action === 'DISMISS') {
    return;
  }

  // Handle agent-specific routing
  if (action === 'OPEN_CHAT' || category === 'clarification') {
    event.waitUntil(
      self.clients.matchAll({ type: 'window' }).then(clientList => {
        // Try focusing existing window and redirecting to the agent chat screen
        for (let i = 0; i < clientList.length; i++) {
          const client = clientList[i];
          if ('focus' in client) {
            client.focus();
            if (client.navigate) {
              return client.navigate('/');
            }
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow('/');
        }
      })
    );
    return;
  }

  if (action === 'OPEN_INBOX' || category === 'important_email') {
    event.waitUntil(
      self.clients.matchAll({ type: 'window' }).then(clientList => {
        for (let i = 0; i < clientList.length; i++) {
          const client = clientList[i];
          if ('focus' in client) {
            client.focus();
            if (client.navigate) {
              return client.navigate('/'); // Can be structured to open inbox tab or path
            }
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow('/');
        }
      })
    );
    return;
  }

  if (!taskId) {
    console.warn("Notification click skipped: Missing Task ID.");
    return;
  }

  // Handle action buttons
  if (action === 'COMPLETE' || action === 'SNOOZE_15') {
    const actionValue = action === 'COMPLETE' ? 'complete' : 'snooze';
    const statusValue = action === 'COMPLETE' ? 'completed' : 'snoozed';
    
    // Call our FastAPI notification action endpoint first
    const gatewayUrl = '/api/notifications/action';
    const gatewayPayload = {
      taskId: taskId,
      action: actionValue
    };

    console.log(`PWA Service Worker: Dispatching Gateway action for Task ${taskId} -> ${actionValue}`);
    
    event.waitUntil(
      fetch(gatewayUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(gatewayPayload)
      })
      .then(resp => {
        if (!resp.ok) {
          throw new Error(`Gateway API action returned status: ${resp.status}`);
        }
        console.log(`Gateway action '${actionValue}' success for Task ${taskId}`);
      })
      .catch(err => {
        console.error("Gateway action failed, falling back to direct Firestore patch.", err);
        
        // Firestore REST API Endpoint with appended API Key for firewall traversal
        const url = `https://firestore.googleapis.com/v1/projects/${FIREBASE_PROJECT_ID}/databases/(default)/documents/users/${USER_ID}/tasks/${taskId}?updateMask.fieldPaths=status&updateMask.fieldPaths=updated_at&key=${FIREBASE_API_KEY}`;
        
        const payload = {
          fields: {
            status: { stringValue: statusValue },
            updated_at: { doubleValue: Date.now() / 1000 }
          }
        };

        return fetch(url, {
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
        });
      })
    );
  } else {
    // User clicked the main body of the notification - open/focus App Window
    event.waitUntil(
      self.clients.matchAll({ type: 'window' }).then(clientList => {
        // Find if any window is open
        for (let i = 0; i < clientList.length; i++) {
          const client = clientList[i];
          if ('focus' in client) {
            client.focus();
            // Post a message to focus/expand chat panel if relevant
            if (category === 'clarification' || category === 'important_email') {
              client.postMessage({ type: 'EXPAND_CHAT_BOT' });
            }
            if (client.navigate) {
              return client.navigate('/');
            }
            return;
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow('/').then(windowClient => {
            if (windowClient && (category === 'clarification' || category === 'important_email')) {
              // Wait slightly for app to load then post expand chat message
              setTimeout(() => {
                windowClient.postMessage({ type: 'EXPAND_CHAT_BOT' });
              }, 1000);
            }
          });
        }
      })
    );
  }
});
