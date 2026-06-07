// frontend/src/App.jsx
import React, { useState, useEffect, useRef } from 'react';
import { 
  Calendar, 
  MessageSquare, 
  RefreshCw, 
  Sparkles, 
  Clock, 
  Zap, 
  Plus, 
  CheckCircle, 
  FileText, 
  User, 
  Send, 
  Terminal, 
  ChevronDown, 
  ChevronUp, 
  AlertTriangle,
  Trash2,
  Eraser
} from 'lucide-react';

// Optional: Import Firebase SDK components if initialized client-side
// We initialize a dynamic fallback wrapper if Firebase configs are omitted
import { initializeApp } from 'firebase/app';
import { getFirestore, doc, onSnapshot, collection, addDoc, serverTimestamp } from 'firebase/firestore';

const API_BASE = ""; // User details fetched dynamically from backend profile settings

// Firebase credentials placeholder
const firebaseConfig = {
  apiKey: "MOCK_API_KEY",
  authDomain: "quantime-pwa-mock.firebaseapp.com",
  projectId: "quantime-pwa-mock",
  storageBucket: "quantime-pwa-mock.appspot.com",
  messagingSenderId: "mock-sender-id",
  appId: "mock-app-id"
};

let db = null;
try {
  const app = initializeApp(firebaseConfig);
  db = getFirestore(app);
} catch (e) {
  console.warn("Firebase client SDK missing config. Operating in local polling fallback mode.");
}

export default function App() {
  const [userId, setUserId] = useState("user");
  const [userName, setUserName] = useState("User");
  const [tasks, setTasks] = useState([]);
  const [chats, setChats] = useState([
    {
      id: 'welcome',
      sender: 'agent',
      text: 'Hello! I am Quantime, your local scheduling assistant. How can I help you manage your timetable today?',
      thoughts: 'Initialized agent communication interface. Checking database...',
      timestamp: Date.now(),
      status: 'done'
    }
  ]);
  const [inputMessage, setInputMessage] = useState("");
  const [isThinkingOpen, setIsThinkingOpen] = useState({});
  const [isLoading, setIsLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [activeTab, setActiveTab] = useState("timeline");
  const [isChatExpanded, setIsChatExpanded] = useState(true);
  const [showSettings, setShowSettings] = useState(false);
  const [expandedTasks, setExpandedTasks] = useState({});
  
  // New task form state
  const [showAddForm, setShowAddForm] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newStart, setNewStart] = useState("");
  const [newEnd, setNewEnd] = useState("");
  const [newEnergy, setNewEnergy] = useState("none");
  const [newConstraint, setNewConstraint] = useState("soft");
  const [viewMode, setViewMode] = useState("timeline"); // timeline or calendar
  const [visibleDate, setVisibleDate] = useState(new Date("2026-06-07")); // reference visible month
  const [selectedDate, setSelectedDate] = useState(new Date("2026-06-07")); // highlighted day
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [showMobileGuide, setShowMobileGuide] = useState(false);
  const [publicIp, setPublicIp] = useState("Loading...");
  const [hasCredentials, setHasCredentials] = useState(true);
  const [setupClientId, setSetupClientId] = useState("");
  const [setupClientSecret, setSetupClientSecret] = useState("");
  const [setupProjectId, setSetupProjectId] = useState("");
  const [isSavingSetup, setIsSavingSetup] = useState(false);

  // Intercept browser PWA install triggers
  useEffect(() => {
    const handlePrompt = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
    };
    window.addEventListener('beforeinstallprompt', handlePrompt);
    return () => window.removeEventListener('beforeinstallprompt', handlePrompt);
  }, []);

  const chatEndRef = useRef(null);

  // Helper to get array of days for visible month grid (Sunday-Saturday)
  const getCalendarDays = () => {
    const year = visibleDate.getFullYear();
    const month = visibleDate.getMonth();
    
    const firstDayIndex = new Date(year, month, 1).getDay();
    const totalDays = new Date(year, month + 1, 0).getDate();
    const prevTotalDays = new Date(year, month, 0).getDate();
    
    const days = [];
    
    // Add padding days from previous month
    for (let i = firstDayIndex - 1; i >= 0; i--) {
      days.push({
        day: prevTotalDays - i,
        date: new Date(year, month - 1, prevTotalDays - i),
        isCurrentMonth: false
      });
    }
    
    // Add days of current month
    for (let i = 1; i <= totalDays; i++) {
      days.push({
        day: i,
        date: new Date(year, month, i),
        isCurrentMonth: true
      });
    }
    
    // Add padding days from next month to fill complete weeks
    const totalCells = days.length <= 35 ? 35 : 42;
    const remaining = totalCells - days.length;
    for (let i = 1; i <= remaining; i++) {
      days.push({
        day: i,
        date: new Date(year, month + 1, i),
        isCurrentMonth: false
      });
    }
    
    return days;
  };

  // Poll database tasks and chats with dynamic month bounds
  const fetchTasks = async (currentDate = visibleDate) => {
    try {
      const year = currentDate.getFullYear();
      const month = currentDate.getMonth();
      
      const startIso = new Date(Date.UTC(year, month, 1, 0, 0, 0)).toISOString();
      const endIso = new Date(Date.UTC(year, month + 1, 0, 23, 59, 59)).toISOString();
      
      const resp = await fetch(`${API_BASE}/api/tasks?start_date=${startIso}&end_date=${endIso}`);
      if (resp.ok) {
        const data = await resp.json();
        const sorted = data.tasks.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
        setTasks(sorted);
      }
    } catch (e) {
      console.error("Failed to fetch tasks", e);
    }
  };

  useEffect(() => {
    fetchTasks(visibleDate);
    const interval = setInterval(() => fetchTasks(visibleDate), 4000); 
    return () => clearInterval(interval);
  }, [visibleDate]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chats]);

  // Fetch user profile and chat history on startup
  useEffect(() => {
    const checkSetupStatus = async () => {
      try {
        const resp = await fetch(`/api/setup/status`);
        if (resp.ok) {
          const data = await resp.json();
          setHasCredentials(data.has_credentials);
        }
      } catch (e) {
        console.error("Failed to check credentials status", e);
      }
    };

    const fetchProfile = async () => {
      try {
        const resp = await fetch(`/api/profile`);
        if (resp.ok) {
          const data = await resp.json();
          setUserId(data.user_id);
          setUserName(data.user_name);
          setChats(prev => prev.map(c => {
            if (c.id === 'welcome') {
              return {
                ...c,
                text: `Hello ${data.user_name}! I am Quantime, your local scheduling assistant. How can I help you manage your timetable today?`
              };
            }
            return c;
          }));
        }
      } catch (e) {
        console.error("Failed to load user profile", e);
      }
    };
    
    const fetchInitialChats = async () => {
      try {
        const resp = await fetch(`/api/chats`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.chats && data.chats.length > 0) {
            setChats(data.chats);
          }
        }
      } catch (e) {
        console.error("Failed to load initial chats", e);
      }
    };

    checkSetupStatus();
    fetchProfile();
    fetchInitialChats();
  }, []);

  const fetchPublicIp = async () => {
    setPublicIp("Loading...");
    try {
      const resp = await fetch('/api/public-ip');
      if (resp.ok) {
        const data = await resp.json();
        setPublicIp(data.public_ip);
      }
    } catch (e) {
      console.error(e);
      setPublicIp("Error fetching IP");
    }
  };

  const handleInstallPWA = async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') {
      setDeferredPrompt(null);
    }
  };

  const handleOAuth = async () => {
    try {
      const currentOrigin = window.location.origin;
      const resp = await fetch(`${API_BASE}/auth/url?origin=${encodeURIComponent(currentOrigin)}`);
      const data = await resp.json();
      if (data.url) {
        window.location.href = data.url;
      }
    } catch (e) {
      alert("Failed to initiate Google OAuth flow.");
    }
  };

  const triggerSync = async () => {
    setIsSyncing(true);
    try {
      const resp = await fetch(`${API_BASE}/api/sync`, { method: 'POST' });
      if (resp.ok) {
        await fetchTasks();
      }
    } catch (e) {
      console.error("Sync failed", e);
    } finally {
      setIsSyncing(false);
    }
  };

  const handleAddTask = async (e) => {
    e.preventDefault();
    if (!newTitle || !newStart || !newEnd) return;

    const taskObj = {
      id: `task_${Date.now()}`,
      title: newTitle,
      description: newDesc,
      start_time: new Date(newStart).toISOString(),
      end_time: new Date(newEnd).toISOString(),
      energy_level: newEnergy,
      constraint_type: newConstraint,
      status: 'pending'
    };

    try {
      const resp = await fetch(`${API_BASE}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(taskObj)
      });
      if (resp.ok) {
        setNewTitle("");
        setNewDesc("");
        setNewStart("");
        setNewEnd("");
        setNewEnergy("none");
        setNewConstraint("soft");
        setShowAddForm(false);
        fetchTasks();
      }
    } catch (e) {
      console.error("Failed to add task", e);
    }
  };

  const handleDeleteTask = async (taskId) => {
    if (!window.confirm("Are you sure you want to delete this task?")) return;
    try {
      const resp = await fetch(`${API_BASE}/api/tasks/${taskId}`, {
        method: 'DELETE'
      });
      if (resp.ok) {
        fetchTasks();
      } else {
        alert("Failed to delete task.");
      }
    } catch (e) {
      console.error("Failed to delete task", e);
    }
  };

  const handleClearChat = async () => {
    if (!window.confirm("Are you sure you want to clear your chat history with Quantime?")) return;
    try {
      const resp = await fetch(`${API_BASE}/api/chats`, {
        method: 'DELETE'
      });
      if (resp.ok) {
        setChats([
          {
            id: 'welcome',
            sender: 'agent',
            text: `Hello ${userName}! I am Quantime, your local scheduling assistant. How can I help you manage your timetable today?`,
            thoughts: 'Cleared chat history. Reinitialized interface.',
            timestamp: Date.now(),
            status: 'done'
          }
        ]);
      } else {
        alert("Failed to clear chat history.");
      }
    } catch (e) {
      console.error("Failed to clear chat history", e);
    }
  };

  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!inputMessage.trim()) return;

    const chatId = `chat_${Date.now()}`;
    const userMsg = {
      id: `msg_user_${Date.now()}`,
      sender: 'user',
      text: inputMessage,
      timestamp: Date.now(),
      status: 'done'
    };

    const agentMsgId = `msg_agent_${Date.now()}`;
    const agentPlaceholder = {
      id: agentMsgId,
      sender: 'agent',
      text: "",
      thoughts: "Connecting to local reasoning loop...",
      timestamp: Date.now() + 10,
      status: 'pending'
    };

    setChats(prev => [...prev, userMsg, agentPlaceholder]);
    setInputMessage("");
    setIsLoading(true);

    // If live Firestore client is configured, push document to Cloud Firestore
    if (db && firebaseConfig.apiKey !== "MOCK_API_KEY") {
      try {
        const chatsCol = collection(db, "users", userId, "chats");
        await addDoc(chatsCol, {
          text: userMsg.text,
          sender: "user",
          status: "pending",
          timestamp: serverTimestamp()
        });
        
        // Listen dynamically for agent responses
        const unsub = onSnapshot(doc(db, "users", userId, "chats", chatId), (docSnap) => {
          if (docSnap.exists()) {
            const data = docSnap.data();
            setChats(prev => prev.map(c => {
              if (c.id === agentMsgId) {
                return {
                  ...c,
                  text: data.text || "",
                  thoughts: data.thoughts || "",
                  status: data.status || 'pending'
                };
              }
              return c;
            }));
            
            if (data.status === 'done') {
              unsub();
              setIsLoading(false);
              fetchTasks();
            }
          }
        });
      } catch (err) {
        console.error("Firestore write failed, falling back to REST API", err);
        fallbackRESTSync(userMsg, agentMsgId);
      }
    } else {
      // Offline fallback: Use SSE/HTTP polling mock response
      fallbackRESTSync(userMsg, agentMsgId);
    }
  };

  const fallbackRESTSync = async (userMsg, agentMsgId) => {
    try {
      const resp = await fetch(`${API_BASE}/api/chats?prompt=${encodeURIComponent(userMsg.text)}`, {
        method: 'POST'
      });
      
      if (resp.ok) {
        const data = await resp.json();
        const serverAgentMsgId = data.chat_id;
        
        // Start polling the chat logs
        const interval = setInterval(async () => {
          try {
            const chatsResp = await fetch(`${API_BASE}/api/chats`);
            if (chatsResp.ok) {
              const chatsData = await chatsResp.json();
              const currentChats = chatsData.chats;
              setChats(currentChats);
              
              const currentAgentMsg = currentChats.find(c => c.id === serverAgentMsgId);
              if (currentAgentMsg && (currentAgentMsg.status === 'done' || currentAgentMsg.status === 'failed')) {
                clearInterval(interval);
                setIsLoading(false);
                fetchTasks();
              }
            }
          } catch (pe) {
            console.error("Error polling chats from REST API:", pe);
          }
        }, 1500);
      } else {
        setIsLoading(false);
      }
    } catch (e) {
      console.error("Failed to post chat via REST", e);
      setIsLoading(false);
    }
  };

  const toggleThinking = (id) => {
    setIsThinkingOpen(prev => ({
      ...prev,
      [id]: !prev[id]
    }));
  };

  const toggleTaskExpand = (id) => {
    setExpandedTasks(prev => ({
      ...prev,
      [id]: !prev[id]
    }));
  };

  const formatTime = (isoString) => {
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return isoString;
    }
  };

  const handleSaveSetup = async (e) => {
    e.preventDefault();
    if (!setupClientId || !setupClientSecret || !setupProjectId) return;
    setIsSavingSetup(true);
    try {
      const resp = await fetch('/api/setup/credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: setupClientId.trim(),
          client_secret: setupClientSecret.trim(),
          project_id: setupProjectId.trim()
        })
      });
      if (resp.ok) {
        setHasCredentials(true);
        fetchTasks();
      } else {
        const errorData = await resp.json();
        alert(`Failed to save setup credentials: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (err) {
      console.error(err);
      alert("Failed to save credentials.");
    } finally {
      setIsSavingSetup(false);
    }
  };

  if (!hasCredentials) {
    return (
      <div className="flex items-center justify-center min-h-screen w-screen bg-gray-950 text-gray-100 p-6">
        <div className="w-full max-w-lg bg-gray-900 border border-gray-800 rounded-3xl p-6 md:p-8 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500"></div>
          
          <header className="text-center mb-6">
            <div className="h-12 w-12 rounded-2xl bg-indigo-650 flex items-center justify-center mx-auto mb-4 glow-indigo">
              <Sparkles className="h-6 w-6 text-white animate-pulse" />
            </div>
            <h1 className="text-2xl font-bold font-sans tracking-wide text-white">Welcome to Quantime</h1>
            <p className="text-xs text-indigo-400 mt-1.5 font-medium">Initial Google Workspace Integration Setup</p>
          </header>

          <div className="space-y-4 text-xs text-gray-300 leading-relaxed bg-black/40 border border-gray-850 p-4 rounded-2xl mb-6">
            <p className="font-semibold text-white flex items-center space-x-1">
              <AlertTriangle className="h-4 w-4 text-amber-500 mr-1" />
              <span>Step 1: Configure OAuth Web Application</span>
            </p>
            <ol className="list-decimal list-inside space-y-1.5 pl-1.5">
              <li>Open <a href="https://console.cloud.google.com" target="_blank" rel="noreferrer" className="text-indigo-400 hover:underline">Google Cloud Console</a> & create a project.</li>
              <li>Go to <strong>API & Services &gt; OAuth Consent Screen</strong>, setup as External.</li>
              <li>Under <strong>Credentials</strong>, click <strong>Create Credentials &gt; OAuth client ID</strong>.</li>
              <li>Select <strong>Web application</strong>. Add Authorized Redirect URI:</li>
              <li className="font-mono text-white bg-gray-900 p-1.5 rounded select-all text-center border border-gray-800 mt-1">http://localhost:8000/auth/callback</li>
            </ol>
          </div>

          <form onSubmit={handleSaveSetup} className="space-y-4">
            <div>
              <label className="block text-[10px] text-gray-400 mb-1 font-semibold uppercase tracking-wider">Project ID</label>
              <input 
                type="text" 
                placeholder="E.g., quantime-498716" 
                value={setupProjectId}
                onChange={(e) => setSetupProjectId(e.target.value)}
                className="w-full bg-gray-950 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                required
              />
            </div>

            <div>
              <label className="block text-[10px] text-gray-400 mb-1 font-semibold uppercase tracking-wider">OAuth Client ID</label>
              <input 
                type="text" 
                placeholder="E.g., 858447973962-xxx.apps.googleusercontent.com" 
                value={setupClientId}
                onChange={(e) => setSetupClientId(e.target.value)}
                className="w-full bg-gray-955 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                required
              />
            </div>

            <div>
              <label className="block text-[10px] text-gray-400 mb-1 font-semibold uppercase tracking-wider">OAuth Client Secret</label>
              <input 
                type="password" 
                placeholder="Enter client secret" 
                value={setupClientSecret}
                onChange={(e) => setSetupClientSecret(e.target.value)}
                className="w-full bg-gray-955 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                required
              />
            </div>

            <button 
              type="submit" 
              disabled={isSavingSetup}
              className="w-full mt-2 py-3 rounded-xl text-xs font-bold bg-indigo-650 hover:bg-indigo-500 text-white transition-all shadow-lg shadow-indigo-950 disabled:bg-gray-800 flex items-center justify-center space-x-2 focus:outline-none"
            >
              {isSavingSetup ? (
                <span>Saving Credentials...</span>
              ) : (
                <>
                  <CheckCircle className="h-4 w-4 text-white" />
                  <span>Save & Complete Setup</span>
                </>
              )}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col md:flex-row h-screen w-screen overflow-hidden pb-16 md:pb-0">
      
      {/* LEFT PANEL: TIMELINE & TASK INGESTION */}
      <div className={`w-full h-full flex flex-col overflow-y-auto p-4 md:p-6 border-r border-gray-800 transition-all duration-300 ${
        activeTab === 'timeline' ? 'flex' : 'hidden md:flex'
      } ${
        isChatExpanded ? 'md:w-3/5' : 'md:w-full'
      }`}>
        <header className="flex justify-between items-center mb-6 pb-4 border-b border-gray-800">
          <div className="flex items-center space-x-3">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-indigo-500 to-purple-600 flex items-center justify-center glow-indigo">
              <Sparkles className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold font-sans tracking-wide bg-gradient-to-r from-white via-gray-200 to-gray-400 bg-clip-text text-transparent">
                Quantime
              </h1>
              <p className="text-xs text-indigo-400 font-medium">Local-First Scheduling Engine</p>
            </div>
          </div>

          <div className="flex items-center space-x-2 relative">
            <button 
              onClick={() => setIsChatExpanded(!isChatExpanded)}
              className="hidden md:flex p-1.5 rounded-lg glass-panel text-gray-300 hover:text-white transition-all items-center justify-center focus:outline-none"
              title="Toggle Assistant Sidebar"
            >
              <MessageSquare className="h-4 w-4" />
            </button>
            
            {/* Settings & Profile Popover */}
            <div className="relative">
              <button 
                onClick={() => setShowSettings(!showSettings)}
                className="h-9 w-9 rounded-lg bg-gray-900 border border-gray-800 flex items-center justify-center text-indigo-400 hover:text-indigo-300 hover:border-gray-750 transition-all focus:outline-none"
                title="Settings & Integrations"
              >
                <User className="h-4 w-4" />
              </button>
              
              {showSettings && (
                <div className="absolute right-0 mt-2 w-56 rounded-xl bg-gray-950/95 border border-gray-800 p-3 shadow-2xl z-50 animate-slide">
                  <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2.5 px-1">Settings & Integrations</h3>
                  <div className="space-y-2">
                    <button 
                      onClick={() => { handleOAuth(); setShowSettings(false); }}
                      className="w-full text-left px-3 py-2 rounded-lg text-xs font-medium bg-gray-900 hover:bg-gray-800 text-gray-200 transition-all flex items-center space-x-2"
                    >
                      <User className="h-3.5 w-3.5 text-indigo-400" />
                      <span>Link Google OAuth</span>
                    </button>
                    <button 
                      onClick={() => { triggerSync(); setShowSettings(false); }}
                      disabled={isSyncing}
                      className="w-full text-left px-3 py-2 rounded-lg text-xs font-medium bg-indigo-950/50 hover:bg-indigo-900/40 text-indigo-300 border border-indigo-900/60 disabled:bg-gray-800 disabled:text-gray-400 transition-all flex items-center space-x-2"
                    >
                      <RefreshCw className={`h-3.5 w-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
                      <span>Sync Calendar</span>
                    </button>
                    
                    <button 
                      onClick={() => { fetchPublicIp(); setShowMobileGuide(true); setShowSettings(false); }}
                      className="w-full text-left px-3 py-2 rounded-lg text-xs font-medium bg-gray-900 hover:bg-gray-800 text-gray-200 transition-all flex items-center space-x-2"
                    >
                      <Clock className="h-3.5 w-3.5 text-indigo-400" />
                      <span>Connect Mobile Phone</span>
                    </button>
                    
                    {deferredPrompt && (
                      <button 
                        onClick={() => { handleInstallPWA(); setShowSettings(false); }}
                        className="w-full text-left px-3 py-2 rounded-lg text-xs font-bold bg-indigo-650 hover:bg-indigo-500 text-white transition-all flex items-center space-x-2"
                      >
                        <Plus className="h-3.5 w-3.5" />
                        <span>Install Quantime PWA</span>
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Dynamic Timeline */}
        <section className="flex-1">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6 pb-2">
            <div className="flex items-center space-x-4">
              <h2 className="text-lg font-semibold tracking-tight text-gray-100 flex items-center space-x-2">
                <Calendar className="h-5 w-5 text-indigo-400" />
                <span>Planner</span>
              </h2>
              <div className="flex items-center space-x-2 bg-gray-900/60 border border-gray-800 rounded-lg p-1">
                <button 
                  type="button"
                  onClick={() => {
                    const prev = new Date(visibleDate);
                    prev.setMonth(prev.getMonth() - 1);
                    setVisibleDate(prev);
                  }}
                  className="p-1 rounded text-gray-400 hover:text-white transition-all hover:bg-gray-800 focus:outline-none"
                  title="Previous Month"
                >
                  &lt;
                </button>
                <span className="text-xs font-semibold px-2 text-gray-200 min-w-[90px] text-center">
                  {visibleDate.toLocaleDateString([], { month: 'long', year: 'numeric' })}
                </span>
                <button 
                  type="button"
                  onClick={() => {
                    const next = new Date(visibleDate);
                    next.setMonth(next.getMonth() + 1);
                    setVisibleDate(next);
                  }}
                  className="p-1 rounded text-gray-400 hover:text-white transition-all hover:bg-gray-800 focus:outline-none"
                  title="Next Month"
                >
                  &gt;
                </button>
              </div>
            </div>

            <div className="flex items-center space-x-3">
              <div className="flex bg-gray-900 border border-gray-850 p-1 rounded-lg">
                <button 
                  type="button"
                  onClick={() => setViewMode("timeline")}
                  className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                    viewMode === 'timeline' 
                      ? 'bg-indigo-650 text-white shadow-md' 
                      : 'text-gray-400 hover:text-gray-200'
                  }`}
                >
                  Timeline
                </button>
                <button 
                  type="button"
                  onClick={() => setViewMode("calendar")}
                  className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                    viewMode === 'calendar' 
                      ? 'bg-indigo-650 text-white shadow-md' 
                      : 'text-gray-400 hover:text-gray-200'
                  }`}
                >
                  Calendar
                </button>
              </div>

              <button 
                onClick={() => setShowAddForm(true)}
                className="px-2.5 py-1.5 rounded-lg text-xs font-bold bg-indigo-950 text-indigo-300 border border-indigo-900 hover:bg-indigo-900 transition-all flex items-center space-x-1 shadow-inner focus:outline-none"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>Add Task</span>
              </button>
            </div>
          </div>

          {viewMode === 'calendar' && (
            <div className="mb-6 animate-slide">
              <div className="grid grid-cols-7 gap-1 text-center mb-1">
                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(day => (
                  <div key={day} className="text-[10px] font-bold text-gray-500 uppercase tracking-wider py-1">
                    {day}
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-1.5 bg-gray-950/40 p-2 rounded-2xl border border-gray-900">
                {getCalendarDays().map((cell, idx) => {
                  const isSelected = selectedDate.getDate() === cell.date.getDate() && 
                                     selectedDate.getMonth() === cell.date.getMonth() &&
                                     selectedDate.getFullYear() === cell.date.getFullYear();
                  const isToday = new Date().toDateString() === cell.date.toDateString();
                  
                  const cellTasks = tasks.filter(t => {
                    const tDate = new Date(t.start_time);
                    return tDate.getDate() === cell.date.getDate() && 
                           tDate.getMonth() === cell.date.getMonth() &&
                           tDate.getFullYear() === cell.date.getFullYear();
                  });
                  
                  return (
                    <div 
                      key={idx}
                      onClick={() => setSelectedDate(cell.date)}
                      className={`min-h-[60px] p-1.5 rounded-xl border flex flex-col justify-between cursor-pointer transition-all hover:scale-[1.03] select-none ${
                        !cell.isCurrentMonth ? 'bg-gray-950/20 border-transparent text-gray-805 opacity-40' :
                        isSelected ? 'bg-indigo-950/50 border-indigo-500 text-indigo-200' :
                        isToday ? 'bg-indigo-950/20 border-indigo-900/50 text-indigo-400' :
                        'bg-gray-900/30 border-gray-850 text-gray-300'
                      }`}
                    >
                      <span className="text-[10px] font-bold">{cell.day}</span>
                      <div className="flex flex-wrap gap-1 mt-1 justify-end">
                        {cellTasks.map(t => {
                          let dotClass = "bg-gray-500";
                          if (t.energy_level === 'crimson') dotClass = "bg-red-500";
                          else if (t.energy_level === 'teal') dotClass = "bg-teal-500";
                          else if (t.constraint_type === 'hard') dotClass = "bg-indigo-500";
                          
                          return (
                            <span 
                              key={t.id} 
                              className={`h-1.5 w-1.5 rounded-full ${dotClass}`} 
                              title={t.title}
                            />
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {viewMode === 'calendar' && (
            <div className="mb-4 text-xs font-semibold text-gray-400">
              Showing tasks for {selectedDate.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}
            </div>
          )}

          {/* Timeline Cards */}
          <div className="space-y-4 relative pl-4 border-l border-gray-800">
            {(() => {
              const filteredTasks = tasks.filter(t => {
                if (viewMode !== 'calendar') return true;
                const tDate = new Date(t.start_time);
                return tDate.getDate() === selectedDate.getDate() &&
                       tDate.getMonth() === selectedDate.getMonth() &&
                       tDate.getFullYear() === selectedDate.getFullYear();
              });

              if (filteredTasks.length === 0) {
                return (
                  <div className="text-center py-12 glass-panel rounded-xl">
                    <Clock className="h-8 w-8 text-gray-600 mx-auto mb-2" />
                    <p className="text-sm text-gray-400">
                      {viewMode === 'calendar' ? 'No events scheduled for this day.' : 'No events scheduled. Use Add Task or Sync Calendar.'}
                    </p>
                  </div>
                );
              }

              return filteredTasks.map((task) => {
                const isCrimson = task.energy_level === 'crimson';
                const isTeal = task.energy_level === 'teal';
                const isExpanded = expandedTasks[task.id];
                
                let cardClass = "bg-gray-900/40 border-gray-800 backdrop-blur-md text-gray-300 border-l-4 border-l-gray-500";
                
                if (isCrimson) {
                  cardClass = "bg-red-950/20 border-red-600/50 backdrop-blur-md shadow-sm shadow-red-950/20 text-red-200 border-l-4 border-l-red-600";
                } else if (isTeal) {
                  cardClass = "bg-teal-950/20 border-teal-600/50 backdrop-blur-md shadow-sm shadow-teal-950/20 text-teal-200 border-l-4 border-l-teal-600";
                }
                
                return (
                  <div 
                    key={task.id} 
                    className={`relative p-4 rounded-xl transition-all hover:scale-[1.01] flex items-start space-x-4 border ${cardClass} float-ui`}
                  >
                    <div className={`absolute -left-[22px] top-5 h-3.5 w-3.5 rounded-full bg-darkspace border-2 ${
                      isCrimson ? 'border-red-600' : isTeal ? 'border-teal-600' : 'border-gray-500'
                    }`}></div>
                    
                    <div className="flex-1">
                      <div className="flex justify-between items-start">
                        <div className="cursor-pointer select-text" onClick={() => toggleTaskExpand(task.id)}>
                          <h3 className="font-semibold text-base text-gray-100 hover:text-indigo-400 transition-all flex items-center space-x-1.5">
                            <span>{task.title}</span>
                            {task.description && (
                              <ChevronDown className={`h-3.5 w-3.5 text-gray-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                            )}
                          </h3>
                        </div>
                        <div className="flex items-center space-x-2">
                          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                            task.constraint_type === 'hard' 
                              ? 'bg-red-950/50 text-red-400 border border-red-900' 
                              : 'bg-indigo-950/50 text-indigo-400 border border-indigo-900'
                          }`}>
                            {task.constraint_type.toUpperCase()}
                          </span>
                          <button 
                            onClick={() => handleDeleteTask(task.id)}
                            className="p-1 rounded text-gray-505 hover:text-red-400 hover:bg-gray-800/60 transition-all focus:outline-none"
                            title="Delete Task"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                      
                      {task.description && isExpanded && (
                        <p className="text-xs text-gray-400 mt-2 bg-black/35 p-3 rounded-lg border border-gray-800 whitespace-pre-wrap leading-relaxed animate-slide max-h-48 overflow-y-auto">
                          {task.description}
                        </p>
                      )}
                      
                      <div className="flex items-center space-x-4 mt-3 text-xs text-gray-400">
                        <span className="flex items-center space-x-1">
                          <Clock className="h-3.5 w-3.5 text-gray-500" />
                          <span>{formatTime(task.start_time)} - {formatTime(task.end_time)}</span>
                        </span>
                        
                        {task.energy_level !== 'none' && (
                          <span className="flex items-center space-x-1">
                            <Zap className={`h-3.5 w-3.5 ${isCrimson ? 'text-red-400' : 'text-teal-400'}`} />
                            <span className={isCrimson ? 'text-red-400' : 'text-teal-400'}>
                              {isCrimson ? 'High Study' : 'Low Reading'}
                            </span>
                          </span>
                        )}

                        {task.source_event_id && (
                          <span className="text-[10px] text-gray-500 bg-gray-900/60 px-1.5 py-0.5 rounded border border-gray-800">
                            Google Synced
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              });
            })()}
          </div>
        </section>
      </div>

      {/* RIGHT PANEL: CHAT DRAWER */}
      <div className={`w-full flex flex-col h-full bg-gray-950/70 backdrop-blur-xl border-t md:border-t-0 border-gray-800 p-4 md:p-6 overflow-hidden transition-all duration-300 ${
        activeTab === 'chat' ? 'flex' : 'hidden md:flex'
      } ${
        isChatExpanded ? 'md:w-2/5' : 'md:w-0 md:p-0 md:opacity-0 md:pointer-events-none'
      }`}>
        <header className="mb-4 flex items-center justify-between pb-3 border-b border-gray-800">
          <div className="flex items-center space-x-2">
            <MessageSquare className="h-5 w-5 text-indigo-400" />
            <h2 className="font-semibold text-lg text-gray-200">Quantime Orchestrator</h2>
          </div>
          <div className="flex items-center space-x-2">
            <button 
              onClick={handleClearChat}
              className="p-1 rounded hover:bg-gray-900 text-gray-400 hover:text-red-400 transition-all focus:outline-none"
              title="Clear Chat Logs"
            >
              <Eraser className="h-4 w-4" />
            </button>
            <button 
              onClick={() => setIsChatExpanded(false)}
              className="hidden md:flex p-1 rounded hover:bg-gray-900 text-gray-400 hover:text-white transition-all focus:outline-none"
              title="Collapse Sidebar"
            >
              <ChevronDown className="h-4 w-4 rotate-90" />
            </button>
          </div>
        </header>

        {/* Messages Stream */}
        <div className="flex-1 overflow-y-auto space-y-4 pr-1 mb-4">
          {chats.map((chat) => {
            const isAgent = chat.sender === 'agent';
            return (
              <div 
                key={chat.id} 
                className={`flex flex-col ${isAgent ? 'items-start' : 'items-end'}`}
              >
                <div 
                  className={`p-3.5 rounded-2xl max-w-[85%] text-sm ${
                    isAgent 
                      ? 'glass-panel text-gray-100 rounded-tl-none border-l-2 border-l-indigo-500' 
                      : 'bg-indigo-600 text-white rounded-tr-none shadow-md shadow-indigo-950'
                  }`}
                >
                  <p className="leading-relaxed">{chat.text || "Generating schedule optimizations..."}</p>
                </div>

                {/* Agent Deep Thinking Accordion */}
                {isAgent && chat.thoughts && (
                  <div className="w-[85%] mt-1.5 pl-2">
                    <button 
                      onClick={() => toggleThinking(chat.id)}
                      className="flex items-center space-x-1 text-[10px] text-gray-500 hover:text-indigo-400 font-mono tracking-wider uppercase transition-all focus:outline-none"
                    >
                      <Terminal className="h-3 w-3" />
                      <span>Gemma 4 Thinking Logs</span>
                      {isThinkingOpen[chat.id] ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    </button>
                    
                    {isThinkingOpen[chat.id] && (
                      <div className="mt-1.5 p-3 rounded-lg bg-black/50 border border-gray-800 text-[11px] font-mono text-indigo-300/90 leading-normal max-h-40 overflow-y-auto glow-indigo animate-slide">
                        {chat.thoughts.split('\n').map((line, idx) => (
                          <div key={idx} className="flex items-start space-x-1.5">
                            <span className="text-gray-600 select-none">&gt;</span>
                            <span>{line}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          <div ref={chatEndRef} />
        </div>

        {/* Input box */}
        <form onSubmit={handleSendMessage} className="relative mt-auto">
          <input 
            type="text" 
            placeholder="Ask to reschedule, set dependencies, check Gmail..." 
            value={inputMessage}
            onChange={(e) => setInputMessage(e.target.value)}
            disabled={isLoading}
            className="w-full bg-gray-900 border border-gray-800 rounded-xl py-3 pl-4 pr-12 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-indigo-500 transition-all shadow-inner"
          />
          <button 
            type="submit" 
            disabled={isLoading || !inputMessage.trim()}
            className="absolute right-2 top-2 p-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 text-white transition-all shadow-md shadow-indigo-950 flex items-center justify-center"
          >
            <Send className="h-4 w-4" />
          </button>
        </form>
      </div>

      {/* MOBILE BOTTOM NAVIGATION BAR */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 h-16 bg-gray-950/90 backdrop-blur-lg border-t border-gray-800 flex justify-around items-center z-50 px-6">
        <button 
          onClick={() => setActiveTab('timeline')}
          className={`flex flex-col items-center justify-center space-y-1 transition-all ${
            activeTab === 'timeline' ? 'text-indigo-400 font-semibold scale-105' : 'text-gray-400 hover:text-gray-200'
          }`}
        >
          <Calendar className="h-5 w-5" />
          <span className="text-[10px]">Timeline</span>
        </button>
        <button 
          onClick={() => setActiveTab('chat')}
          className={`flex flex-col items-center justify-center space-y-1 transition-all ${
            activeTab === 'chat' ? 'text-indigo-400 font-semibold scale-105' : 'text-gray-400 hover:text-gray-200'
          }`}
        >
          <div className="relative">
            <MessageSquare className="h-5 w-5" />
            {isLoading && (
              <span className="absolute -top-1.5 -right-1.5 flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
              </span>
            )}
          </div>
          <span className="text-[10px]">Assistant</span>
        </button>
      </div>

      {/* BOTTOM SHEET ADD TASK MODAL (Mobile-first slide up bottom sheet) */}
      {showAddForm && (
        <div className="fixed inset-0 z-50 flex items-end justify-center">
          {/* Backdrop dim overlay */}
          <div 
            onClick={() => setShowAddForm(false)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
          ></div>
          
          {/* Slide up sheet */}
          <form 
            onSubmit={handleAddTask} 
            className="relative w-full max-w-xl bg-gray-950 border-t border-gray-800 rounded-t-3xl p-6 pb-8 space-y-4 shadow-2xl animate-slide z-10 max-h-[85vh] overflow-y-auto"
          >
            <div className="flex justify-between items-center pb-2 border-b border-gray-900">
              <h3 className="text-base font-bold text-gray-100 flex items-center space-x-2">
                <Plus className="h-5 w-5 text-indigo-400" />
                <span>Create Schedule Task</span>
              </h3>
              <button 
                type="button"
                onClick={() => setShowAddForm(false)}
                className="text-gray-500 hover:text-gray-300 text-xs font-semibold focus:outline-none"
              >
                Done
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-[10px] text-gray-400 mb-1 font-semibold">Task Title</label>
                <input 
                  type="text" 
                  placeholder="E.g., Read chapter 4" 
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  className="w-full bg-gray-900 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                  required
                />
              </div>
              
              <div>
                <label className="block text-[10px] text-gray-400 mb-1 font-semibold">Description (Optional)</label>
                <textarea 
                  placeholder="Invite links, requirements, notes..." 
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  rows="2"
                  className="w-full bg-gray-950 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1 font-semibold">Start Time</label>
                  <input 
                    type="datetime-local" 
                    value={newStart}
                    onChange={(e) => setNewStart(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                    required
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1 font-semibold">End Time</label>
                  <input 
                    type="datetime-local" 
                    value={newEnd}
                    onChange={(e) => setNewEnd(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                    required
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1 font-semibold">Energy Requirement</label>
                  <select 
                    value={newEnergy}
                    onChange={(e) => setNewEnergy(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none"
                  >
                    <option value="none">Neutral (None)</option>
                    <option value="crimson">Crimson (High Energy)</option>
                    <option value="teal">Teal (Low Energy)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] text-gray-400 mb-1 font-semibold">Constraint Priority</label>
                  <select 
                    value={newConstraint}
                    onChange={(e) => setNewConstraint(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none"
                  >
                    <option value="soft">Soft (Flexible)</option>
                    <option value="hard">Hard (Locked/Google)</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="flex space-x-3 pt-2">
              <button 
                type="button" 
                onClick={() => setShowAddForm(false)}
                className="flex-1 py-3 rounded-xl text-xs font-semibold glass-panel text-gray-300"
              >
                Cancel
              </button>
              <button 
                type="submit" 
                className="flex-1 py-3 rounded-xl text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white"
              >
                Save Task
              </button>
            </div>
          </form>
        </div>
      )}

      {showMobileGuide && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div 
            onClick={() => setShowMobileGuide(false)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
          ></div>
          
          <div className="relative w-full max-w-md bg-gray-950 border border-gray-800 rounded-3xl p-6 shadow-2xl z-10 animate-slide">
            <div className="flex justify-between items-center pb-3 border-b border-gray-900 mb-4">
              <h3 className="text-base font-bold text-gray-100 flex items-center space-x-2">
                <Sparkles className="h-5 w-5 text-indigo-400" />
                <span>Mobile Setup Guide</span>
              </h3>
              <button 
                type="button"
                onClick={() => setShowMobileGuide(false)}
                className="text-gray-500 hover:text-gray-300 text-xs font-semibold focus:outline-none"
              >
                Close
              </button>
            </div>
            
            <div className="space-y-4 text-xs text-gray-300 leading-relaxed">
              <p>
                To access Quantime from your mobile phone and keep your calendar synchronized:
              </p>
              
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 font-mono select-all text-center text-indigo-400 font-bold">
                https://quantime-scheduler-green.loca.lt
              </div>
              
              <div className="space-y-2">
                <div className="flex items-start space-x-2">
                  <span className="flex items-center justify-center h-5 w-5 rounded-full bg-indigo-950 text-indigo-300 font-bold text-[10px]">1</span>
                  <p>Open the link above in Chrome or Safari on your phone.</p>
                </div>
                
                <div className="flex items-start space-x-2">
                  <span className="flex items-center justify-center h-5 w-5 rounded-full bg-indigo-950 text-indigo-300 font-bold text-[10px]">2</span>
                  <div className="flex-1">
                    <p>When prompted with the Localtunnel reminder block, enter the host PC public IP address:</p>
                    <p className="font-mono text-white bg-black/40 px-2 py-1 rounded inline-block mt-1 font-bold select-all">{publicIp}</p>
                  </div>
                </div>
                
                <div className="flex items-start space-x-2">
                  <span className="flex items-center justify-center h-5 w-5 rounded-full bg-indigo-950 text-indigo-300 font-bold text-[10px]">3</span>
                  <p>In your mobile browser settings, select <strong>"Add to Home Screen"</strong> to install the standalone PWA app!</p>
                </div>
              </div>
            </div>
            
            <button 
              type="button" 
              onClick={() => setShowMobileGuide(false)}
              className="w-full mt-6 py-3 rounded-xl text-xs font-semibold bg-indigo-650 hover:bg-indigo-500 text-white transition-all focus:outline-none"
            >
              Got it!
            </button>
          </div>
        </div>
      )}

    </div>
  );
}
