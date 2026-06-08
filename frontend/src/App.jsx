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
  ChevronLeft,
  ChevronRight,
  AlertTriangle,
  Trash2,
  Eraser,
  Settings,
  Edit,
  Mic,
  MicOff,
  Volume2,
  Disc,
  Square
} from 'lucide-react';

// Optional: Import Firebase SDK components if initialized client-side
// We initialize a dynamic fallback wrapper if Firebase configs are omitted
import { initializeApp } from 'firebase/app';
import { getFirestore, doc, onSnapshot, collection, addDoc, serverTimestamp } from 'firebase/firestore';

const API_BASE = ""; // User details fetched dynamically from backend profile settings

// Global HTTP Fetch interceptor to automatically attach API key headers
const originalFetch = window.fetch;
window.fetch = async (url, options = {}) => {
  const apiKey = localStorage.getItem("quantime_api_key");
  if (apiKey && (typeof url === 'string' && (url.includes('/api/') || url.includes('/auth/')))) {
    options.headers = options.headers || {};
    if (options.headers instanceof Headers) {
      options.headers.set('X-API-Key', apiKey);
    } else {
      options.headers['X-API-Key'] = apiKey;
    }
  }
  return originalFetch(url, options);
};

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
  const [notificationsEnabled, setNotificationsEnabled] = useState(true);
  const [notificationLeadMinutes, setNotificationLeadMinutes] = useState(15);
  const [notificationOnStart, setNotificationOnStart] = useState(true);
  const [notificationDndFocus, setNotificationDndFocus] = useState(true);
  const [showNotificationSettings, setShowNotificationSettings] = useState(false);
  const [isSubscribingPush, setIsSubscribingPush] = useState(false);
  const [showMobileInstallPrompt, setShowMobileInstallPrompt] = useState(false);
  const [isAppInstalledSuccessfully, setIsAppInstalledSuccessfully] = useState(false);
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
  const [recurrencePattern, setRecurrencePattern] = useState("none");
  const [isEditing, setIsEditing] = useState(false);
  const [editingTaskId, setEditingTaskId] = useState(null);
  const [editScope, setEditScope] = useState('single');
  const [recurringConfirm, setRecurringConfirm] = useState({ isOpen: false, taskId: null, actionType: 'delete', payload: null });
  const [recurrenceCount, setRecurrenceCount] = useState(10);
  const [recurrenceDays, setRecurrenceDays] = useState([]); // Array of integers 0 = Monday, 6 = Sunday
  const [apiKey, setApiKey] = useState(() => localStorage.getItem("quantime_api_key") || "");
  const [viewMode, setViewMode] = useState("timeline"); // timeline or calendar
  const [visibleDate, setVisibleDate] = useState(new Date()); // reference visible month
  const [selectedDate, setSelectedDate] = useState(new Date()); // highlighted day
  const [weekOffset, setWeekOffset] = useState(0); // offset in weeks from current week
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [stagedProposal, setStagedProposal] = useState(null); // { transaction_id, options: [...] }
  const [activeProposalOption, setActiveProposalOption] = useState(null); // { option_id, description, proposed_changes: [...] }
  const [proposalsMap, setProposalsMap] = useState({}); // tx_id -> proposal details

  // Voice Chat S2S states and refs
  const [isVoiceActive, setIsVoiceActive] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState("idle"); // 'idle', 'recording', 'thinking', 'speaking'
  const [voiceChoice, setVoiceChoice] = useState("af_heart");
  const [llmModel, setLlmModel] = useState("gemma4-agent-mtp");
  const [availableModels, setAvailableModels] = useState(["gemma4-agent-mtp"]);
  const [activeVoiceText, setActiveVoiceText] = useState("");
  const [activeVoiceThoughts, setActiveVoiceThoughts] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const [ollamaStatus, setOllamaStatus] = useState("loading");
  const [kokoroStatus, setKokoroStatus] = useState("loading");
  const [showStatusBadge, setShowStatusBadge] = useState(true);
  const wsRef = useRef(null);
  const audioContextRef = useRef(null);
  const audioQueueRef = useRef([]);
  const isPlayingRef = useRef(false);
  const activeAudioSourceRef = useRef(null);
  const recognitionRef = useRef(null);

  const voiceMediaRecorderRef = useRef(null);

  const startVoiceChat = async () => {
    try {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      let wsUrl;
      if (window.location.port === "5173") {
        wsUrl = `${protocol}//${window.location.hostname}:8000/api/voice-chat`;
      } else {
        wsUrl = `${protocol}//${window.location.host}/api/voice-chat`;
      }
      const key = localStorage.getItem("quantime_api_key");
      const wsUrlWithKey = key ? `${wsUrl}?key=${key}` : wsUrl;
      const ws = new WebSocket(wsUrlWithKey);
      wsRef.current = ws;
      
      ws.onopen = async () => {
        console.log("Voice WebSocket connected.");
        setIsVoiceActive(true);
        setVoiceStatus("recording");
        setActiveVoiceText("");
        setActiveVoiceThoughts("");
        setVoiceError("");
        
        // Start offline audio recorder streaming binary PCM data to backend
        try {
          const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const audioCtx = audioContextRef.current || new (window.AudioContext || window.webkitAudioContext)();
          if (!audioContextRef.current) {
            audioContextRef.current = audioCtx;
          }
          
          const source = audioCtx.createMediaStreamSource(micStream);
          // Process audio in 4096-sample blocks (16kHz mono)
          const processor = audioCtx.createScriptProcessor(4096, 1, 1);
          
          source.connect(processor);
          processor.connect(audioCtx.destination);
          
          voiceMediaRecorderRef.current = {
            stream: micStream,
            source: source,
            processor: processor
          };
          
          // Re-sample mic input (usually 44.1kHz or 48kHz) to 16kHz mono PCM for faster-whisper/speech_recognition
          processor.onaudioprocess = (e) => {
            if (ws.readyState !== WebSocket.OPEN || isPlayingRef.current) return;
            
            const inputData = e.inputBuffer.getChannelData(0);
            
            // Re-sample to 16kHz
            const originalSampleRate = audioCtx.sampleRate;
            const targetSampleRate = 16000;
            const ratio = originalSampleRate / targetSampleRate;
            const targetLength = Math.round(inputData.length / ratio);
            
            const int16Buffer = new Int16Array(targetLength);
            for (let i = 0; i < targetLength; i++) {
              const originalIndex = Math.round(i * ratio);
              if (originalIndex < inputData.length) {
                // Scale Float32 [-1, 1] to Int16 [-32768, 32767]
                const sample = Math.max(-1, Math.min(1, inputData[originalIndex]));
                int16Buffer[i] = sample < 0 ? sample * 32768 : sample * 32767;
              }
            }
            
            // Send binary PCM frames to WebSocket
            if (ws.readyState === WebSocket.OPEN && !isPlayingRef.current) {
              ws.send(int16Buffer.buffer);
            }
          };
        } catch (recorderErr) {
          console.warn("Local mic recording streaming initialization failed:", recorderErr);
        }
        
        if (!voiceMediaRecorderRef.current) {
          console.log("SpeechRecognition: Local mic streaming not active. Falling back to browser SpeechRecognition.");
          startSpeechRecognition();
        } else {
          console.log("SpeechRecognition: Local mic streaming is active. Disabling browser SpeechRecognition to prevent device conflicts.");
        }
      };
      
      ws.onmessage = async (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "status") {
          setVoiceStatus(msg.status);
          if (msg.status === "thinking") {
            setActiveVoiceText("");
            setActiveVoiceThoughts("");
          }
          
          if (msg.status === "speaking") {
            pauseSpeechRecognition();
          } else if (msg.status === "idle" || msg.status === "recording") {
            resumeSpeechRecognition();
          }
        } else if (msg.type === "text") {
          setActiveVoiceText(prev => prev + msg.text);
        } else if (msg.type === "thought") {
          setActiveVoiceThoughts(prev => prev + msg.thought);
        } else if (msg.type === "audio") {
          const audioBytes = Uint8Array.from(atob(msg.audio), c => c.charCodeAt(0));
          audioQueueRef.current.push(audioBytes);
          playNextInQueue();
        }
      };
      
      ws.onclose = () => {
        stopVoiceChat();
      };
      
    } catch (err) {
      console.error("Failed to start voice chat:", err);
      alert("Microphone permission denied or speech recognition not supported.");
      stopVoiceChat();
    }
  };

  const startSpeechRecognition = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn("Speech Recognition not supported in this browser.");
      return;
    }
    
    const recognition = new SpeechRecognition();
    recognitionRef.current = recognition;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';
    
    let lastErrorType = null;
    
    recognition.onstart = () => {
      setVoiceError(""); // Clear any active connection errors
    };
    
    recognition.onresult = (event) => {
      if (isPlayingRef.current) {
        return;
      }
      
      let interimTranscript = '';
      let finalTranscript = '';
      
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          finalTranscript += event.results[i][0].transcript;
        } else {
          interimTranscript += event.results[i][0].transcript;
        }
      }
      
      if (finalTranscript || interimTranscript) {
        setActiveVoiceText(finalTranscript || interimTranscript);
      }
      
      if (finalTranscript.trim()) {
        console.log("Speech recognition final result:", finalTranscript);
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "prompt", prompt: finalTranscript.trim() }));
        }
        recognition.stop();
      }
    };
    
    recognition.onerror = (event) => {
      console.error("Speech recognition error:", event.error);
      lastErrorType = event.error;
      
      if (event.error === "network") {
        setVoiceError("Network error. Retrying connection...");
      } else if (event.error === "no-speech") {
        setVoiceError(""); // Transient timeout
      }
    };
    
    recognition.onend = () => {
      // Restart recognition if voice is active and we are not speaking/thinking
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && !isPlayingRef.current && !activeAudioSourceRef.current) {
        let delay = 100;
        if (lastErrorType === "network") {
          console.log("SpeechRecognition: Network error. Falling back entirely to backend PCM audio streaming STT.");
          setVoiceError("");
          lastErrorType = null;
          return;
        } else if (lastErrorType === "no-speech") {
          delay = 500;
        }
        
        lastErrorType = null;
        
        setTimeout(() => {
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && !isPlayingRef.current && !activeAudioSourceRef.current) {
            try {
              recognition.start();
            } catch (e) {}
          }
        }, delay);
      }
    };
    
    try {
      recognition.start();
    } catch (e) {
      console.error("Error starting speech recognition:", e);
    }
  };

  const pauseSpeechRecognition = () => {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch (e) {}
    }
  };

  const resumeSpeechRecognition = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && recognitionRef.current) {
      try {
        recognitionRef.current.start();
      } catch (e) {}
    }
  };

  const playNextInQueue = async () => {
    if (isPlayingRef.current || audioQueueRef.current.length === 0) return;
    
    isPlayingRef.current = true;
    setVoiceStatus("speaking");
    const pcmData = audioQueueRef.current.shift();
    
    try {
      const audioCtx = audioContextRef.current || new (window.AudioContext || window.webkitAudioContext)();
      if (!audioContextRef.current) {
        audioContextRef.current = audioCtx;
      }
      const int16Array = new Int16Array(pcmData.buffer, pcmData.byteOffset, pcmData.byteLength / 2);
      
      const float32Array = new Float32Array(int16Array.length);
      for (let i = 0; i < int16Array.length; i++) {
        float32Array[i] = int16Array[i] / 32768.0;
      }
      
      const audioBuffer = audioCtx.createBuffer(1, float32Array.length, 24000);
      audioBuffer.getChannelData(0).set(float32Array);
      
      const source = audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioCtx.destination);
      activeAudioSourceRef.current = source;
      
      source.onended = () => {
        activeAudioSourceRef.current = null;
        isPlayingRef.current = false;
        if (audioQueueRef.current.length > 0) {
          playNextInQueue();
        } else {
          setVoiceStatus("recording");
          resumeSpeechRecognition();
        }
      };
      source.start(0);
    } catch (err) {
      console.error("Audio playback error:", err);
      activeAudioSourceRef.current = null;
      isPlayingRef.current = false;
      playNextInQueue();
    }
  };

  const stopAudioPlayback = () => {
    if (activeAudioSourceRef.current) {
      try {
        activeAudioSourceRef.current.stop();
      } catch (e) {}
      activeAudioSourceRef.current = null;
    }
    audioQueueRef.current = [];
    isPlayingRef.current = false;
    setVoiceStatus("recording");
  };

  const stopVoiceChat = () => {
    setIsVoiceActive(false);
    setVoiceStatus("idle");
    stopAudioPlayback();
    
    if (voiceMediaRecorderRef.current) {
      const { stream, source, processor } = voiceMediaRecorderRef.current;
      try {
        if (source) source.disconnect();
        if (processor) processor.disconnect();
        if (stream) stream.getTracks().forEach(track => track.stop());
      } catch (e) {
        console.error("Error cleaning up microphone streams:", e);
      }
      voiceMediaRecorderRef.current = null;
    }
    
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch(e){}
      recognitionRef.current = null;
    }
    if (wsRef.current) {
      try { wsRef.current.close(); } catch(e){}
      wsRef.current = null;
    }
  };

  // Clean up voice objects on unmount
  useEffect(() => {
    return () => {
      stopVoiceChat();
    };
  }, []);

  // Hook to fetch proposal options when a proposal tag is detected in chat history
  useEffect(() => {
    const fetchProposals = async () => {
      for (const chat of chats) {
        if (chat.text && chat.text.includes("<schedule-proposal")) {
          const match = chat.text.match(/<schedule-proposal tx="([^"]+)">/);
          if (match && match[1]) {
            const txId = match[1];
            if (proposalsMap[txId] === undefined) {
              try {
                // Instantly set to null to prevent parallel trigger loops before fetch returns
                setProposalsMap(prev => ({ ...prev, [txId]: null }));
                
                const resp = await fetch(`${API_BASE}/api/proposals/${txId}`);
                if (resp.ok) {
                  const data = await resp.json();
                  setProposalsMap(prev => ({ ...prev, [txId]: data }));
                }
              } catch (e) {
                console.error("Failed to fetch proposal options", e);
              }
            }
          }
        }
      }
    };
    fetchProposals();
  }, [chats]);

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 60000);
    return () => clearInterval(timer);
  }, []);

  // Helper to calculate week offset for a given date relative to today
  const getWeekOffsetForDate = (date) => {
    const today = new Date();
    const todaySunday = new Date(today);
    todaySunday.setDate(today.getDate() - today.getDay());
    todaySunday.setHours(0, 0, 0, 0);
    
    const targetSunday = new Date(date);
    targetSunday.setDate(date.getDate() - date.getDay());
    targetSunday.setHours(0, 0, 0, 0);
    
    const diffTime = targetSunday.getTime() - todaySunday.getTime();
    return Math.round(diffTime / (7 * 24 * 60 * 60 * 1000));
  };

  useEffect(() => {
    const offset = getWeekOffsetForDate(selectedDate);
    setWeekOffset(offset);
  }, [selectedDate]);
  const [showMobileGuide, setShowMobileGuide] = useState(false);
  const [publicIp, setPublicIp] = useState("Loading...");
  const [tunnelUrl, setTunnelUrl] = useState(null);
  const [hasCredentials, setHasCredentials] = useState(true);

  const [isGoogleLinked, setIsGoogleLinked] = useState(false);
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
  const [hasModel, setHasModel] = useState(true);
  const [gpuName, setGpuName] = useState("Scanning...");
  const [gpuVram, setGpuVram] = useState(0);
  const [selectedModel, setSelectedModel] = useState("gemma2:2b");
  const [setupStep, setSetupStep] = useState("select"); // "select" or "progress"
  const [pullProgress, setPullProgress] = useState({ status: "idle", percent: 0, detail: "" });
  const [setupClientId, setSetupClientId] = useState("");
  const [setupClientSecret, setSetupClientSecret] = useState("");
  const [setupProjectId, setSetupProjectId] = useState("");
  const [isSavingSetup, setIsSavingSetup] = useState(false);
  const [showSetupModal, setShowSetupModal] = useState(false);

  // Intercept browser PWA install triggers
  useEffect(() => {
    const handlePrompt = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
      
      const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent) || window.innerWidth < 768;
      const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
      const dismissed = sessionStorage.getItem('pwa_prompt_dismissed');
      if (isMobile && !isStandalone && !dismissed) {
        setShowMobileInstallPrompt(true);
      }
    };
    window.addEventListener('beforeinstallprompt', handlePrompt);
    return () => window.removeEventListener('beforeinstallprompt', handlePrompt);
  }, []);

  useEffect(() => {
    const handleAppInstalled = () => {
      setIsAppInstalledSuccessfully(true);
      setShowMobileInstallPrompt(false);
    };
    window.addEventListener('appinstalled', handleAppInstalled);
    return () => window.removeEventListener('appinstalled', handleAppInstalled);
  }, []);

  useEffect(() => {
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent) || window.innerWidth < 768;
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
    const dismissed = sessionStorage.getItem('pwa_prompt_dismissed');
    
    if (isMobile && !isStandalone && !dismissed) {
      const isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);
      if (isIOS) {
        setShowMobileInstallPrompt(true);
      }
    }
  }, []);

  const chatEndRef = useRef(null);
  const fetchTasksAbortControllerRef = useRef(null);

  const getDaysForOffsetWeek = (offset) => {
    const today = new Date();
    const dayOfWeek = today.getDay();
    const sunday = new Date(today);
    sunday.setDate(today.getDate() - dayOfWeek + (offset * 7));
    
    const days = [];
    for (let i = 0; i < 7; i++) {
      const day = new Date(sunday);
      day.setDate(sunday.getDate() + i);
      days.push(day);
    }
    return days;
  };

  const parseTaskDate = (dateStr) => {
    if (!dateStr) return new Date();
    if (typeof dateStr === 'string' && dateStr.length === 10 && dateStr.includes('-')) {
      const [year, month, day] = dateStr.split('-').map(Number);
      return new Date(year, month - 1, day);
    }
    return new Date(dateStr);
  };

  const isSameDay = (d1, d2) => {
    return d1.getFullYear() === d2.getFullYear() &&
           d1.getMonth() === d2.getMonth() &&
           d1.getDate() === d2.getDate();
  };

  const getDailyWorkload = (date) => {
    return tasks.filter(t => {
      const tDate = parseTaskDate(t.start_time);
      return isSameDay(tDate, date);
    });
  };

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
    if (fetchTasksAbortControllerRef.current) {
      fetchTasksAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    fetchTasksAbortControllerRef.current = controller;

    try {
      const year = currentDate.getFullYear();
      const month = currentDate.getMonth();
      
      // Pad by 2 days on either side to capture tasks offset by timezone differences
      const startIso = new Date(Date.UTC(year, month, 1 - 2, 0, 0, 0)).toISOString();
      const endIso = new Date(Date.UTC(year, month + 1, 0 + 2, 23, 59, 59)).toISOString();
      
      const resp = await fetch(`${API_BASE}/api/tasks?start_date=${startIso}&end_date=${endIso}&_t=${Date.now()}`, {
        signal: controller.signal
      });
      if (resp.ok) {
        const data = await resp.json();
        const sorted = data.tasks.sort((a, b) => parseTaskDate(a.start_time) - parseTaskDate(b.start_time));
        setTasks(sorted);
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        return; // Ignore clean client-side aborts
      }
      console.error("Failed to fetch tasks", e);
    } finally {
      if (fetchTasksAbortControllerRef.current === controller) {
        fetchTasksAbortControllerRef.current = null;
      }
    }
  };

  useEffect(() => {
    fetchTasks(visibleDate);
    const interval = setInterval(() => fetchTasks(visibleDate), 4000); 
    return () => {
      clearInterval(interval);
      if (fetchTasksAbortControllerRef.current) {
        fetchTasksAbortControllerRef.current.abort();
      }
    };
  }, [visibleDate]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chats]);

  // Intercept API key in URL parameters on startup
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const keyParam = params.get("key");
    if (keyParam) {
      localStorage.setItem("quantime_api_key", keyParam);
      setApiKey(keyParam);
      params.delete("key");
      const cleanSearch = params.toString();
      const newUrl = window.location.pathname + (cleanSearch ? "?" + cleanSearch : "");
      window.history.replaceState({}, document.title, newUrl);
    }
  }, []);

  // Fetch user profile and chat history on startup
  useEffect(() => {
    const checkSetupStatus = async () => {
      try {
        const resp = await fetch(`/api/setup/status`);
        if (resp.ok) {
          const data = await resp.json();
          setHasCredentials(data.has_credentials);
          setHasModel(data.has_model);
          if (data.tunnel_url) {
            setTunnelUrl(data.tunnel_url);
          }
          if (data.api_key) {
            localStorage.setItem("quantime_api_key", data.api_key);
            setApiKey(data.api_key);
          }
          
          if (!data.has_model) {
            // Fetch hardware information
            const hwResp = await fetch(`/api/setup/hardware`);
            if (hwResp.ok) {
              const hwData = await hwResp.json();
              setGpuName(hwData.name);
              setGpuVram(hwData.vram);
              if (hwData.vram >= 12.0) {
                setSelectedModel("gemma4");
              } else if (hwData.vram >= 8.0) {
                setSelectedModel("gemma2:9b");
              } else {
                setSelectedModel("gemma2:2b");
              }
            }
          }
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
          setIsGoogleLinked(!!data.is_google_linked);
          setNotificationsEnabled(data.notifications_enabled === 'true');
          setNotificationLeadMinutes(parseInt(data.notification_lead_minutes) || 15);
          setNotificationOnStart(data.notification_on_start === 'true');
          setNotificationDndFocus(data.notification_dnd_focus === 'true');
          setVoiceChoice(data.voice_choice || "af_heart");
          setLlmModel(data.llm_model || "gemma4-agent-mtp");
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

    const fetchModels = async () => {
      try {
        const resp = await fetch(`/api/models`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.models && data.models.length > 0) {
            setAvailableModels(data.models);
          }
        }
      } catch (e) {
        console.error("Failed to load Ollama models", e);
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
    fetchModels();
    fetchInitialChats();
  }, []);

  // Poll model preheating status on startup
  useEffect(() => {
    let active = true;
    let interval;
    const pollPreheat = async () => {
      try {
        const resp = await fetch(`/api/setup/status`);
        if (resp.ok && active) {
          const data = await resp.json();
          if (data.ollama_status) setOllamaStatus(data.ollama_status);
          if (data.kokoro_status) setKokoroStatus(data.kokoro_status);
          
          if (data.ollama_status === 'ready' && data.kokoro_status === 'ready') {
            setTimeout(() => {
              if (active) setShowStatusBadge(false);
            }, 3000);
            if (interval) clearInterval(interval);
          }
        }
      } catch (e) {
        console.error("Failed to poll preheat status:", e);
      }
    };
    
    pollPreheat();
    interval = setInterval(pollPreheat, 2500);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!showMobileGuide || tunnelUrl) return;

    let active = true;
    const pollStatus = async () => {
      try {
        const resp = await fetch(`/api/setup/status`);
        if (resp.ok && active) {
          const data = await resp.json();
          if (data.tunnel_url) {
            setTunnelUrl(data.tunnel_url);
          }
        }
      } catch (e) {
        console.error("Failed to poll tunnel status", e);
      }
    };

    pollStatus();
    const interval = setInterval(pollStatus, 2000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [showMobileGuide, tunnelUrl]);

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
      } else {
        throw new Error("No URL returned from backend");
      }
    } catch (e) {
      alert("Failed to initiate Google OAuth flow. Please ensure the Quantime background engine is running (check your system tray / hidden icons in the taskbar).");
    }
  };

  const saveNotificationSettings = async (enabled, leadMins, onStart, dndFocus, voice = voiceChoice, model = llmModel) => {
    try {
      await fetch(`/api/profile`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          user_id: userId,
          user_name: userName,
          notifications_enabled: enabled ? 'true' : 'false',
          notification_lead_minutes: String(leadMins),
          notification_on_start: onStart ? 'true' : 'false',
          notification_dnd_focus: dndFocus ? 'true' : 'false',
          voice_choice: voice,
          llm_model: model
        })
      });
    } catch (e) {
      console.error("Failed to save notification settings", e);
    }
  };

  const subscribeToPushNotifications = async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      console.warn("Web Push is not supported by this browser.");
      return false;
    }
    
    setIsSubscribingPush(true);
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        throw new Error('Notification permission denied');
      }
      
      const reg = await navigator.serviceWorker.ready;
      
      try {
        const registrations = await navigator.serviceWorker.getRegistrations();
        for (const r of registrations) {
          const sub = await r.pushManager.getSubscription();
          if (sub) {
            await sub.unsubscribe();
            console.log("Unsubscribed from previous push subscription keys to avoid mismatch.");
          }
        }
        const oldSub = await reg.pushManager.getSubscription();
        if (oldSub) {
          await oldSub.unsubscribe();
        }
      } catch (e) {
        console.warn("Error checking or unsubscribing from old subscription:", e);
      }
      
      const keyResp = await fetch(`/api/notifications/vapid-public-key`);
      if (!keyResp.ok) throw new Error("Failed to get public VAPID key");
      const { publicKey } = await keyResp.json();
      
      const padding = '='.repeat((4 - publicKey.length % 4) % 4);
      const base64 = (publicKey + padding).replace(/\-/g, '+').replace(/_/g, '/');
      const rawData = window.atob(base64);
      const outputArray = new Uint8Array(rawData.length);
      for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
      }
      
      const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: outputArray
      });
      
      const subResp = await fetch(`/api/notifications/subscribe`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ subscription })
      });
      if (!subResp.ok) throw new Error("Failed to send subscription to server");
      
      console.log("Successfully subscribed to Push Notifications!");
      return true;
    } catch (err) {
      console.error("Failed to subscribe to push notifications", err);
      return false;
    } finally {
      setIsSubscribingPush(false);
    }
  };

  const lastChimeRef = useRef(0);

  const playNotificationChime = () => {
    try {
      const nowMs = Date.now();
      if (nowMs - lastChimeRef.current < 15000) {
        return; // Prevent duplicate chime playback within 15 seconds
      }
      lastChimeRef.current = nowMs;

      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const now = ctx.currentTime;
      
      const osc1 = ctx.createOscillator();
      const gain1 = ctx.createGain();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(880, now);
      gain1.gain.setValueAtTime(0, now);
      gain1.gain.linearRampToValueAtTime(0.3, now + 0.05);
      gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
      osc1.connect(gain1);
      gain1.connect(ctx.destination);
      osc1.start(now);
      osc1.stop(now + 0.5);
      
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(1200, now + 0.15);
      gain2.gain.setValueAtTime(0, now + 0.15);
      gain2.gain.linearRampToValueAtTime(0.3, now + 0.2);
      gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.7);
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.start(now + 0.15);
      osc2.stop(now + 0.7);
    } catch (e) {
      console.warn("Failed to play synthesized notification sound:", e);
    }
  };

  const localTimersRef = useRef([]);

  useEffect(() => {
    localTimersRef.current.forEach(timerId => clearTimeout(timerId));
    localTimersRef.current = [];

    if (!notificationsEnabled) return;
    if (Notification.permission !== 'granted') return;

    const now = new Date();
    tasks.forEach(task => {
      if (task.status === 'completed') return;

      const startTime = new Date(task.start_time);
      if (isNaN(startTime.getTime())) return;

      const leadTimeMs = startTime.getTime() - now.getTime() - (notificationLeadMinutes * 60 * 1000);
      if (leadTimeMs > 0) {
        const timerId = setTimeout(() => {
          playNotificationChime();
          if (navigator.onLine === false) {
            new Notification(`Upcoming: ${task.title}`, {
              body: `Starts in ${notificationLeadMinutes} minutes.`,
              icon: '/logo192.png',
              tag: `lead-${task.id}`
            });
          }
        }, leadTimeMs);
        localTimersRef.current.push(timerId);
      }

      if (notificationOnStart) {
        const startTimeMs = startTime.getTime() - now.getTime();
        if (startTimeMs > 0) {
          const timerId = setTimeout(() => {
            playNotificationChime();
            if (navigator.onLine === false) {
              new Notification(`Starting now: ${task.title}`, {
                body: "It's time to begin!",
                icon: '/logo192.png',
                tag: `start-${task.id}`
              });
            }
          }, startTimeMs);
          localTimersRef.current.push(timerId);
        }
      }
    });

    return () => {
      localTimersRef.current.forEach(timerId => clearTimeout(timerId));
    };
  }, [tasks, notificationsEnabled, notificationLeadMinutes, notificationOnStart]);

  useEffect(() => {
    if (notificationsEnabled && 'Notification' in window && Notification.permission === 'granted') {
      subscribeToPushNotifications();
    }
  }, [notificationsEnabled]);

  // Listen to messages from the Service Worker (e.g. to play chime sound on push notification receipt)
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      const handleServiceWorkerMessage = (event) => {
        if (event.data && event.data.type === 'PLAY_CHIME') {
          if (!event.data.silent) {
            playNotificationChime();
          }
        }
      };
      navigator.serviceWorker.addEventListener('message', handleServiceWorkerMessage);
      return () => {
        navigator.serviceWorker.removeEventListener('message', handleServiceWorkerMessage);
      };
    }
  }, []);


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

    if (isEditing && editingTaskId) {
      const editObj = {
        title: newTitle,
        description: newDesc,
        start_time: new Date(newStart).toISOString(),
        end_time: new Date(newEnd).toISOString(),
        energy_level: newEnergy,
        constraint_type: newConstraint
      };
      
      const currentTask = tasks.find(t => t.id === editingTaskId);
      const isRecurring = currentTask && (currentTask.recurrence_group_id || currentTask.source_event_id);
      
      await executeEditTask(editingTaskId, editObj, isRecurring ? editScope : 'single');
      return;
    }

    const taskObj = {
      id: `task_${Date.now()}`,
      title: newTitle,
      description: newDesc,
      start_time: new Date(newStart).toISOString(),
      end_time: new Date(newEnd).toISOString(),
      energy_level: newEnergy,
      constraint_type: newConstraint,
      status: 'pending',
      recurrence_pattern: recurrencePattern,
      recurrence_count: recurrencePattern !== 'none' ? parseInt(recurrenceCount) || 10 : null,
      recurrence_days: recurrencePattern === 'weekly' && recurrenceDays.length > 0 ? recurrenceDays : null
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
        setRecurrencePattern("none");
        setRecurrenceCount(10);
        setRecurrenceDays([]);
        setShowAddForm(false);
        fetchTasks();
      }
    } catch (e) {
      console.error("Failed to add task", e);
    }
  };

  const executeEditTask = async (taskId, editObj, target) => {
    try {
      const resp = await fetch(`${API_BASE}/api/tasks/${taskId}?target=${target}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editObj)
      });
      if (resp.ok) {
        setNewTitle("");
        setNewDesc("");
        setNewStart("");
        setNewEnd("");
        setNewEnergy("none");
        setNewConstraint("soft");
        setIsEditing(false);
        setEditingTaskId(null);
        setShowAddForm(false);
        fetchTasks();
      }
    } catch (e) {
      console.error("Failed to edit task", e);
    }
  };

  const toLocalDatetimeString = (isoString) => {
    try {
      if (!isoString) return "";
      const date = new Date(isoString);
      const tzOffset = date.getTimezoneOffset() * 60000;
      const localISOTime = (new Date(date.getTime() - tzOffset)).toISOString().slice(0, 16);
      return localISOTime;
    } catch {
      return "";
    }
  };

  const handleEditTaskClick = (task) => {
    setIsEditing(true);
    setEditingTaskId(task.id);
    setNewTitle(task.title || "");
    setNewDesc(task.description || "");
    setNewStart(toLocalDatetimeString(task.start_time));
    setNewEnd(toLocalDatetimeString(task.end_time));
    setNewEnergy(task.energy_level || "none");
    setNewConstraint(task.constraint_type || "soft");
    setRecurrencePattern("none");
    setEditScope('single');
    setShowAddForm(true);
  };
  

  const executeDeleteTask = async (taskId, target) => {
    try {
      const resp = await fetch(`${API_BASE}/api/tasks/${taskId}?target=${target}`, {
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

  const handleDeleteTask = async (taskId) => {
    const task = tasks.find(t => t.id === taskId);
    if (!task) return;

    if (task.recurrence_group_id || task.source_event_id) {
      setRecurringConfirm({
        isOpen: true,
        taskId: taskId,
        actionType: 'delete',
        payload: null
      });
      return;
    }

    if (!window.confirm(`Are you sure you want to delete "${task.title}"?`)) return;
    await executeDeleteTask(taskId, 'single');
  };

  const handleCompleteTask = async (taskId, currentStatus) => {
    const nextStatus = currentStatus === 'completed' ? 'pending' : 'completed';
    try {
      const resp = await fetch(`${API_BASE}/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: nextStatus })
      });
      if (resp.ok) {
        fetchTasks();
      } else {
        alert("Failed to update task status.");
      }
    } catch (e) {
      console.error("Failed to update task status", e);
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

  const handleStartModelSetup = async () => {
    setSetupStep("progress");
    try {
      const resp = await fetch('/api/setup/pull-model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: selectedModel })
      });
      if (resp.ok) {
        // Start polling
        const pollInterval = setInterval(async () => {
          try {
            const statusResp = await fetch('/api/setup/pull-status');
            if (statusResp.ok) {
              const statusData = await statusResp.json();
              setPullProgress(statusData);
              if (statusData.status === 'completed') {
                clearInterval(pollInterval);
                setHasModel(true);
              } else if (statusData.status === 'failed') {
                clearInterval(pollInterval);
                alert(`Setup failed: ${statusData.detail}`);
                setSetupStep("select");
              }
            }
          } catch (e) {
            console.error("Error checking setup progress", e);
          }
        }, 800);
      } else {
        alert("Failed to start model setup task.");
        setSetupStep("select");
      }
    } catch (e) {
      alert("Error starting model setup task.");
      setSetupStep("select");
    }
  };

  const handleSendMessage = async (e, customText = null) => {
    if (e) e.preventDefault();
    const messageToSend = customText || inputMessage;
    if (!messageToSend.trim()) return;

    const chatId = `chat_${Date.now()}`;
    const userMsg = {
      id: `msg_user_${Date.now()}`,
      sender: 'user',
      text: messageToSend,
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
    if (!customText) setInputMessage("");
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
      const resp = await fetch(`${API_BASE}/api/chats?prompt=${encodeURIComponent(userMsg.text)}&selected_date=${encodeURIComponent(selectedDate.toISOString())}&current_time=${encodeURIComponent(new Date().toISOString())}`, {
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

  const handleCommitProposal = async (txId, optionId) => {
    try {
      const resp = await fetch(`${API_BASE}/api/proposals/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transaction_id: txId, option_id: optionId })
      });
      if (resp.ok) {
        setStagedProposal(null);
        setActiveProposalOption(null);
        
        // Post confirmation message to chat local DB
        const confirmMsg = {
          sender: "agent",
          text: "✓ Rescheduling Plan Applied! The schedule has been successfully re-organized.",
          thoughts: "User approved the speculative rescheduling workaround options. Changes committed.",
          timestamp: Date.now() / 1000
        };
        
        await fetch(`${API_BASE}/api/chats`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(confirmMsg)
        });
        
        fetchTasks();
        const chatResp = await fetch(`${API_BASE}/api/chats`);
        if (chatResp.ok) {
          const chatsData = await chatResp.json();
          setChats(chatsData.chats);
        }
      }
    } catch (e) {
      console.error("Failed to commit proposal option", e);
    }
  };

  const handleRejectProposal = async (txId) => {
    try {
      const resp = await fetch(`${API_BASE}/api/proposals/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transaction_id: txId })
      });
      if (resp.ok) {
        setStagedProposal(null);
        setActiveProposalOption(null);
        
        // Post reject and trigger next plan search
        handleSendMessage(null, "I decline this schedule proposal. Please try to find another alternative workaround.");
      }
    } catch (e) {
      console.error("Failed to reject proposal", e);
    }
  };

  const handleRollbackProposal = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/proposals/rollback`, {
        method: "POST"
      });
      if (resp.ok) {
        const rollbackMsg = {
          sender: "agent",
          text: "↩ Rescheduling changes undone! The schedule has been restored to the previous snapshot state.",
          thoughts: "User requested rollback. Snapshot state restored.",
          timestamp: Date.now() / 1000
        };
        
        await fetch(`${API_BASE}/api/chats`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(rollbackMsg)
        });
        
        fetchTasks();
        const chatResp = await fetch(`${API_BASE}/api/chats`);
        if (chatResp.ok) {
          const chatsData = await chatResp.json();
          setChats(chatsData.chats);
        }
      }
    } catch (e) {
      console.error("Failed to rollback schedule", e);
    }
  };

  const renderMessageContent = (text) => {
    if (!text) return null;

    const lines = text.split('\n');
    const elements = [];
    let currentList = null; // { type: 'ul' | 'ol', items: [] }

    const flushList = (key) => {
      if (!currentList) return null;
      const listKey = `list_${key}`;
      const listContent = currentList.items.map((item, idx) => {
        const parts = item.split(/(\*\*[^*]+\*\*)/g);
        return (
          <li key={idx} className="text-gray-100 mb-1 last:mb-0">
            {parts.map((part, partIdx) => {
              if (part.startsWith('**') && part.endsWith('**')) {
                return <strong key={partIdx} className="font-extrabold text-indigo-300">{part.slice(2, -2)}</strong>;
              }
              return part;
            })}
          </li>
        );
      });

      const listElement = currentList.type === 'ul' ? (
        <ul key={listKey} className="list-disc pl-5 space-y-1 my-2 text-left">
          {listContent}
        </ul>
      ) : (
        <ol key={listKey} className="list-decimal pl-5 space-y-1 my-2 text-left">
          {listContent}
        </ol>
      );
      
      currentList = null;
      return listElement;
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();

      // Handle empty lines
      if (trimmed === "") {
        const listEl = flushList(i);
        if (listEl) elements.push(listEl);
        elements.push(<div key={`spacer_${i}`} className="h-2" />);
        continue;
      }

      // Handle headers (###, ##, #)
      const headerMatch = line.match(/^(#{1,6})\s+(.+)$/);
      if (headerMatch) {
        const listEl = flushList(i);
        if (listEl) elements.push(listEl);
        
        const headerText = headerMatch[2];
        const parts = headerText.split(/(\*\*[^*]+\*\*)/g);
        const headerInline = parts.map((part, partIdx) => {
          if (part.startsWith('**') && part.endsWith('**')) {
            return <strong key={partIdx} className="font-extrabold text-indigo-300">{part.slice(2, -2)}</strong>;
          }
          return part;
        });

        elements.push(
          <h4 key={`header_${i}`} className="text-sm font-extrabold text-indigo-400 mt-4 mb-2 text-left">
            {headerInline}
          </h4>
        );
        continue;
      }

      // Handle Unordered Lists (* or -)
      const ulMatch = line.match(/^[\s]*[-*]\s+(.+)$/);
      if (ulMatch) {
        if (currentList && currentList.type !== 'ul') {
          elements.push(flushList(i));
        }
        if (!currentList) {
          currentList = { type: 'ul', items: [] };
        }
        currentList.items.push(ulMatch[1]);
        continue;
      }

      // Handle Ordered Lists (1., 2., etc.)
      const olMatch = line.match(/^[\s]*\d+\.\s+(.+)$/);
      if (olMatch) {
        if (currentList && currentList.type !== 'ol') {
          elements.push(flushList(i));
        }
        if (!currentList) {
          currentList = { type: 'ol', items: [] };
        }
        currentList.items.push(olMatch[1]);
        continue;
      }

      // If it's a regular text line, flush any active list first
      const listEl = flushList(i);
      if (listEl) elements.push(listEl);

      // Render regular text line
      const parts = line.split(/(\*\*[^*]+\*\*)/g);
      elements.push(
        <p key={`p_${i}`} className="leading-relaxed mb-1.5 last:mb-0 text-left">
          {parts.map((part, partIdx) => {
            if (part.startsWith('**') && part.endsWith('**')) {
              return <strong key={partIdx} className="font-extrabold text-indigo-300">{part.slice(2, -2)}</strong>;
            }
            return part;
          })}
        </p>
      );
    }

    // Flush any remaining list at the end
    const lastListEl = flushList(lines.length);
    if (lastListEl) elements.push(lastListEl);

    return elements;
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
      if (typeof isoString === 'string' && isoString.length === 10 && isoString.includes('-')) {
        return "All Day";
      }
      const date = parseTaskDate(isoString);
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



  if (!hasModel) {
    return (
      <div className="flex items-center justify-center min-h-screen w-screen bg-gray-950 text-gray-100 p-4 font-sans select-none overflow-y-auto">
        <div className="w-full max-w-2xl bg-gray-900/60 backdrop-blur-2xl border border-gray-800 rounded-3xl p-6 md:p-8 shadow-2xl relative overflow-hidden glow-indigo">
          <div className="absolute top-0 right-0 w-80 h-80 bg-indigo-600/10 rounded-full blur-[100px] pointer-events-none"></div>
          <div className="absolute bottom-0 left-0 w-80 h-80 bg-purple-600/10 rounded-full blur-[100px] pointer-events-none"></div>
          
          <div className="flex items-center space-x-3 mb-6">
            <div className="h-12 w-12 rounded-2xl bg-gradient-to-tr from-indigo-500 to-purple-600 flex items-center justify-center glow-indigo">
              <Sparkles className="h-6 w-6 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-extrabold tracking-wide bg-gradient-to-r from-white via-gray-200 to-gray-400 bg-clip-text text-transparent">
                Quantime Onboarding
              </h1>
              <p className="text-xs text-indigo-400 font-semibold uppercase tracking-wider">Local AI Setup Wizard</p>
            </div>
          </div>

          {setupStep === "select" ? (
            <div className="space-y-6 animate-slide">
              <div className="glass-panel p-4 rounded-2xl border border-gray-800 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <h3 className="text-sm font-bold text-gray-300">System Hardware Detected:</h3>
                  <p className="text-xs text-indigo-300 font-mono mt-1">{gpuName}</p>
                </div>
                <div className="bg-indigo-950/60 border border-indigo-900/60 rounded-xl px-4 py-2 text-center md:text-right shrink-0">
                  <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-widest block">Available VRAM</span>
                  <span className="text-lg font-extrabold text-white font-mono">{gpuVram} GB</span>
                </div>
              </div>

              <div>
                <h2 className="text-base font-bold text-gray-200 mb-3 flex items-center space-x-1.5">
                  <Zap className="h-4 w-4 text-indigo-400" />
                  <span>Choose Your Scheduling Agent Model:</span>
                </h2>
                
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {[
                    {
                      id: "gemma4",
                      name: "Gemma 4 12B",
                      size: "Full Agent",
                      vramReq: 12.0,
                      desc: "Top-tier agent reasoning & Speculative decoding. Ideal for RTX 5060 Ti / 16GB VRAM."
                    },
                    {
                      id: "gemma2:9b",
                      name: "Gemma 2 9B",
                      size: "Balanced Agent",
                      vramReq: 8.0,
                      desc: "Excellent capability-to-size ratio. Recommended for >= 8GB VRAM."
                    },
                    {
                      id: "llama3:8b",
                      name: "Llama 3 8B",
                      size: "Llama Alternative",
                      vramReq: 8.0,
                      desc: "Popular alternative open-source model. Recommended for >= 8GB VRAM."
                    },
                    {
                      id: "gemma2:2b",
                      name: "Gemma 2 2B",
                      size: "Lightweight CPU",
                      vramReq: 0.0,
                      desc: "Extremely fast execution, works on low-end systems & CPU-only modes."
                    }
                  ].map((m) => {
                    const isRecommended = (gpuVram >= m.vramReq && (m.id === "gemma4" && gpuVram >= 12.0 || m.id === "gemma2:9b" && gpuVram >= 8.0 && gpuVram < 12.0 || m.id === "gemma2:2b" && gpuVram < 8.0));
                    const isSelected = selectedModel === m.id;
                    return (
                      <div 
                        key={m.id}
                        onClick={() => setSelectedModel(m.id)}
                        className={`cursor-pointer group relative rounded-2xl p-4 border transition-all duration-300 ${
                          isSelected 
                            ? 'bg-indigo-950/40 border-indigo-500/80 shadow-md shadow-indigo-950/50' 
                            : 'bg-gray-900/40 border-gray-800 hover:border-gray-750 hover:bg-gray-900/60'
                        }`}
                      >
                        {isRecommended && (
                          <span className="absolute -top-2 -right-2 bg-gradient-to-r from-emerald-500 to-teal-500 text-[9px] font-extrabold text-black uppercase tracking-widest px-2 py-0.5 rounded-full shadow-md animate-pulse">
                            Recommended
                          </span>
                        )}
                        <div className="flex justify-between items-start mb-2">
                          <h4 className="font-bold text-sm text-gray-200 group-hover:text-indigo-400 transition-colors">{m.name}</h4>
                          <span className="text-[10px] bg-gray-950 border border-gray-800 text-indigo-400 font-bold px-2 py-0.5 rounded-md">{m.size}</span>
                        </div>
                        <p className="text-xs text-gray-400 leading-normal">{m.desc}</p>
                      </div>
                    );
                  })}
                </div>
              </div>

              <button 
                onClick={handleStartModelSetup}
                className="w-full bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-bold py-4 rounded-2xl shadow-lg shadow-indigo-950/60 transition-all transform hover:-translate-y-0.5 active:translate-y-0 text-sm flex items-center justify-center space-x-2"
              >
                <span>Initialize Agent & Download Weights</span>
                <Plus className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <div className="space-y-6 text-center py-6 animate-slide">
              <div className="relative inline-flex items-center justify-center mb-4">
                <div className="w-24 h-24 rounded-full border-4 border-gray-800 flex items-center justify-center">
                  <span className="text-xl font-extrabold font-mono text-white">{pullProgress.percent}%</span>
                </div>
                <div className="absolute inset-0 rounded-full border-4 border-indigo-500 animate-spin border-t-transparent border-r-transparent"></div>
              </div>

              <div className="space-y-2">
                <h3 className="text-base font-bold text-gray-200 capitalize">
                  {pullProgress.status} Model Agent...
                </h3>
                <div className="w-full bg-gray-950 rounded-full h-2.5 overflow-hidden border border-gray-800 max-w-md mx-auto">
                  <div 
                    className="bg-gradient-to-r from-indigo-500 to-purple-500 h-full rounded-full transition-all duration-300 shadow-glow"
                    style={{ width: `${pullProgress.percent}%` }}
                  ></div>
                </div>
                <p className="text-xs text-gray-400 max-w-sm mx-auto font-mono mt-4 leading-relaxed bg-black/45 p-3 rounded-xl border border-gray-800 max-h-24 overflow-y-auto">
                  {pullProgress.detail || "Waiting for download start..."}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  const handleMicClickInOverlay = () => {
    if (voiceStatus === "speaking" || voiceStatus === "thinking") {
      stopAudioPlayback();
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "interrupt" }));
      }
    } else if (voiceStatus === "recording") {
      stopVoiceChat();
    }
  };

  const isMobileDevice = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent) || window.innerWidth < 768;

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
              <div className="flex items-center space-x-2">
                <p className="text-xs text-indigo-400 font-medium">Local-First Scheduling Engine</p>
                <span className="text-[10px] text-gray-500 font-mono bg-gray-900/60 px-1.5 py-0.5 rounded border border-gray-800">v1.2.0</span>
                {showStatusBadge && (
                  <div 
                    className={`flex items-center space-x-1 px-1.5 py-0.5 rounded text-[9px] font-medium border transition-all duration-500 ${
                      ollamaStatus === 'loading' || kokoroStatus === 'loading'
                        ? 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20 animate-pulse'
                        : ollamaStatus === 'error' || kokoroStatus === 'error'
                        ? 'bg-red-500/10 text-red-400 border-red-500/20'
                        : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                    }`}
                    title={`LLM status: ${ollamaStatus}, Voice status: ${kokoroStatus}`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${
                      ollamaStatus === 'loading' || kokoroStatus === 'loading'
                        ? 'bg-yellow-400'
                        : ollamaStatus === 'error' || kokoroStatus === 'error'
                        ? 'bg-red-400'
                        : 'bg-emerald-400'
                    }`} />
                    <span>
                      {ollamaStatus === 'loading' || kokoroStatus === 'loading'
                        ? 'System Warming Up...'
                        : ollamaStatus === 'error' || kokoroStatus === 'error'
                        ? 'Preheat Failed'
                        : 'AI Engine Ready'}
                    </span>
                  </div>
                )}
              </div>
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
                <Settings className="h-4 w-4" />
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
                      <span>Link Google Account</span>
                    </button>
                    <button 
                      onClick={() => { triggerSync(); setShowSettings(false); }}
                      disabled={isSyncing}
                      className="w-full text-left px-3 py-2 rounded-lg text-xs font-medium bg-indigo-950/50 hover:bg-indigo-900/40 text-indigo-300 border border-indigo-900/60 disabled:bg-gray-800 disabled:text-gray-400 transition-all flex items-center space-x-2"
                    >
                      <RefreshCw className={`h-3.5 w-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
                      <span>Sync Calendar</span>
                    </button>
                    
                    {!isMobileDevice && (
                      <button 
                        onClick={() => { fetchPublicIp(); setShowMobileGuide(true); setShowSettings(false); }}
                        className="w-full text-left px-3 py-2 rounded-lg text-xs font-medium bg-gray-900 hover:bg-gray-800 text-gray-200 transition-all flex items-center space-x-2"
                      >
                        <Clock className="h-3.5 w-3.5 text-indigo-400" />
                        <span>Connect Mobile Phone</span>
                      </button>
                    )}

                    <div className="border-t border-gray-800/80 pt-2 mt-2">
                      <button
                        onClick={() => setShowNotificationSettings(!showNotificationSettings)}
                        className="w-full text-left px-2 py-1 text-[10px] font-semibold text-gray-500 hover:text-gray-400 transition-all uppercase tracking-wider flex justify-between items-center focus:outline-none"
                      >
                        <span>Alerts & Notifications</span>
                        <ChevronDown className={`h-3 w-3 transform transition-transform ${showNotificationSettings ? 'rotate-180' : ''}`} />
                      </button>
                      
                      {showNotificationSettings && (
                        <div className="mt-2 space-y-2.5 pl-1 pr-1 text-gray-300 animate-slide">
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] font-medium">Enable Notifications</span>
                            <input 
                              type="checkbox" 
                              checked={notificationsEnabled} 
                              onChange={async (e) => {
                                const val = e.target.checked;
                                setNotificationsEnabled(val);
                                await saveNotificationSettings(val, notificationLeadMinutes, notificationOnStart, notificationDndFocus);
                                if (val) {
                                  subscribeToPushNotifications();
                                }
                              }} 
                              className="accent-indigo-550 h-3.5 w-3.5 rounded border-gray-800 bg-gray-900"
                            />
                          </div>

                          {notificationsEnabled && (
                            <>
                              <div className="space-y-1">
                                <label className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider block">Lead Time Reminder</label>
                                <select 
                                  value={notificationLeadMinutes} 
                                  onChange={async (e) => {
                                    const val = parseInt(e.target.value);
                                    setNotificationLeadMinutes(val);
                                    await saveNotificationSettings(notificationsEnabled, val, notificationOnStart, notificationDndFocus);
                                  }}
                                  className="w-full bg-gray-900 border border-gray-800 text-xs rounded-lg px-2 py-1 focus:outline-none focus:border-indigo-550 text-gray-200"
                                >
                                  <option value={0}>None (Disabled)</option>
                                  <option value={5}>5 Minutes</option>
                                  <option value={15}>15 Minutes</option>
                                  <option value={30}>30 Minutes</option>
                                  <option value={60}>1 Hour</option>
                                </select>
                              </div>

                              <div className="flex items-center justify-between">
                                <span className="text-[11px] font-medium">Alert on Start Time</span>
                                <input 
                                  type="checkbox" 
                                  checked={notificationOnStart} 
                                  onChange={async (e) => {
                                    const val = e.target.checked;
                                    setNotificationOnStart(val);
                                    await saveNotificationSettings(notificationsEnabled, notificationLeadMinutes, val, notificationDndFocus);
                                  }} 
                                  className="accent-indigo-550 h-3.5 w-3.5 rounded border-gray-800 bg-gray-900"
                                />
                              </div>

                              <div className="flex items-center justify-between">
                                <span className="text-[11px] font-medium">DND in Focus blocks</span>
                                <input 
                                  type="checkbox" 
                                  checked={notificationDndFocus} 
                                  onChange={async (e) => {
                                    const val = e.target.checked;
                                    setNotificationDndFocus(val);
                                    await saveNotificationSettings(notificationsEnabled, notificationLeadMinutes, notificationOnStart, val);
                                  }} 
                                  className="accent-indigo-550 h-3.5 w-3.5 rounded border-gray-800 bg-gray-900"
                                />
                              </div>

                              <button
                                onClick={async () => {
                                    try {
                                      let isSubscribed = false;
                                      if ('serviceWorker' in navigator && 'PushManager' in window) {
                                        const reg = await navigator.serviceWorker.ready;
                                        const sub = await reg.pushManager.getSubscription();
                                        if (sub) {
                                          isSubscribed = true;
                                        }
                                      }

                                      if (!isSubscribed) {
                                        const success = await subscribeToPushNotifications();
                                        if (!success) {
                                          alert("Failed to test notification: Notification subscription could not be registered.");
                                          return;
                                        }
                                      }

                                      const res = await fetch('/api/notifications/test', { method: 'POST' });
                                      if (res.ok) {
                                        alert("Test notification dispatched!");
                                      } else {
                                        let errMsg = "Unknown error";
                                        try {
                                          const err = await res.json();
                                          errMsg = err.detail || err.message || errMsg;
                                        } catch (_) {
                                          try {
                                            errMsg = await res.text();
                                          } catch (__) {}
                                        }
                                        alert("Failed: " + errMsg);
                                      }
                                    } catch (err) {
                                      alert("Error: " + err.message);
                                    }
                                }}
                                className="w-full py-1 text-center bg-indigo-950/45 text-indigo-300 border border-indigo-900/40 rounded-lg text-[10px] font-bold hover:bg-indigo-900/35 transition-all"
                              >
                                Test Push Notification
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>

                    <div className="border-t border-gray-800/80 pt-2 mt-2">
                      <div className="px-2 py-1 text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                        Voice Profile
                      </div>
                      <div className="mt-1 space-y-2 px-1">
                        <select 
                          value={voiceChoice}
                          onChange={async (e) => {
                            const val = e.target.value;
                            setVoiceChoice(val);
                            await saveNotificationSettings(notificationsEnabled, notificationLeadMinutes, notificationOnStart, notificationDndFocus, val);
                          }}
                          className="w-full bg-gray-900 border border-gray-805 text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:border-indigo-550 text-gray-250 font-medium"
                        >
                          <option value="af_heart">❤️ Kokoro Heart</option>
                          <option value="af_bella">Bella (Female)</option>
                          <option value="af_nicole">Nicole (Female)</option>
                          <option value="am_adam">Adam (Male)</option>
                          <option value="am_michael">Michael (Male)</option>
                        </select>

                      </div>
                    </div>

                    <div className="border-t border-gray-800/80 pt-2 mt-2">
                      <div className="px-2 py-1 text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                        LLM Model
                      </div>
                      <div className="mt-1 px-1">
                        <select 
                          value={llmModel}
                          onChange={async (e) => {
                            const val = e.target.value;
                            setLlmModel(val);
                            await saveNotificationSettings(notificationsEnabled, notificationLeadMinutes, notificationOnStart, notificationDndFocus, voiceChoice, val);
                          }}
                          className="w-full bg-gray-900 border border-gray-805 text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:border-indigo-550 text-gray-250 font-medium"
                        >
                          {availableModels.map(model => (
                            <option key={model} value={model}>🤖 {model}</option>
                          ))}
                        </select>
                      </div>
                    </div>

                    <div className="border-t border-gray-800/80 pt-2 mt-2">
                      <button
                        onClick={() => setShowAdvancedSettings(!showAdvancedSettings)}
                        className="w-full text-left px-2 py-1 text-[10px] font-semibold text-gray-500 hover:text-gray-400 transition-all uppercase tracking-wider flex justify-between items-center focus:outline-none"
                      >
                        <span>Developer Settings</span>
                        <ChevronDown className={`h-3 w-3 transform transition-transform ${showAdvancedSettings ? 'rotate-180' : ''}`} />
                      </button>
                      
                      {showAdvancedSettings && (
                        <div className="mt-2 space-y-2 pl-0.5 animate-slide">
                          <button 
                            onClick={() => { setShowSetupModal(true); setShowSettings(false); }}
                            className="w-full text-left px-3 py-2 rounded-lg text-xs font-medium bg-gray-900 hover:bg-gray-800 text-amber-400/90 transition-all flex items-center space-x-2 border border-amber-950/20"
                          >
                            <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                            <span>Custom OAuth Secrets</span>
                          </button>
                          <button 
                            onClick={async () => {
                              if (window.confirm("ARE YOU ABSOLUTELY SURE? This will permanently delete all tasks, routines, and task dependencies from the local database, Firestore, and synced Google Calendar events!")) {
                                if (window.confirm("FINAL CONFIRMATION: Double check if you really want to clear the entire calendar?")) {
                                  try {
                                    const resp = await fetch(`${API_BASE}/api/tasks/clear`, {
                                      method: 'DELETE'
                                    });
                                    if (resp.ok) {
                                      alert("Calendar cleared successfully!");
                                      fetchTasks();
                                    } else {
                                      alert("Failed to clear calendar.");
                                    }
                                  } catch (e) {
                                    console.error("Failed to clear calendar", e);
                                    alert("Error clearing calendar.");
                                  }
                                  setShowSettings(false);
                                }
                              }
                            }}
                            className="w-full text-left px-3 py-2 rounded-lg text-xs font-medium bg-red-950/45 hover:bg-red-900/40 text-red-300 border border-red-900/40 transition-all flex items-center space-x-2"
                          >
                            <Trash2 className="h-3.5 w-3.5 text-red-500" />
                            <span>Clear Calendar</span>
                          </button>
                        </div>
                      )}
                    </div>

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

        {/* Google Sync Connection Card */}
        {!isGoogleLinked ? (
          <div className="mb-6 bg-gradient-to-r from-indigo-950/40 via-purple-950/35 to-indigo-950/40 border border-indigo-500/25 p-4 rounded-2xl flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 animate-pulse-subtle">
            <div>
              <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-indigo-400" />
                Connect Google Calendar & Gmail
              </h3>
              <p className="text-xs text-gray-400 mt-1">
                Link your Google account to automatically schedule meetings, sync your tasks, and summarize your emails.
              </p>
            </div>
            <button
              onClick={handleOAuth}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold shadow-indigo transition-all shrink-0 cursor-pointer"
            >
              Connect Now
            </button>
          </div>
        ) : (
          <div className="mb-6 bg-gray-900/40 border border-gray-800 p-3.5 rounded-2xl flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="h-8 w-8 rounded-lg bg-green-950/40 border border-green-900/50 flex items-center justify-center">
                <CheckCircle className="h-4 w-4 text-green-400" />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-200">Google Sync Active</p>
                <p className="text-[10px] text-gray-400 mt-0.5">Connected as {userName}</p>
              </div>
            </div>
            <div className="flex space-x-1.5">
              <button
                onClick={triggerSync}
                disabled={isSyncing}
                className="p-1.5 rounded-lg bg-gray-955 hover:bg-gray-800 border border-gray-800 text-gray-300 transition-all cursor-pointer flex items-center justify-center"
                title="Sync Google Account"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>
        )}

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
                    
                    const currentDay = selectedDate.getDate();
                    const newDate = new Date(prev.getFullYear(), prev.getMonth(), currentDay);
                    if (newDate.getMonth() !== (prev.getMonth() + 12) % 12) {
                      setSelectedDate(new Date(prev.getFullYear(), prev.getMonth() + 1, 0));
                    } else {
                      setSelectedDate(newDate);
                    }
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
                    
                    const currentDay = selectedDate.getDate();
                    const newDate = new Date(next.getFullYear(), next.getMonth(), currentDay);
                    if (newDate.getMonth() !== (next.getMonth() + 12) % 12) {
                      setSelectedDate(new Date(next.getFullYear(), next.getMonth() + 1, 0));
                    } else {
                      setSelectedDate(newDate);
                    }
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
                onClick={() => {
                  setIsEditing(false);
                  setEditingTaskId(null);
                  setNewTitle("");
                  setNewDesc("");
                  setNewStart("");
                  setNewEnd("");
                  setNewEnergy("none");
                  setNewConstraint("soft");
                  setRecurrencePattern("none");
                  setShowAddForm(true);
                }}
                className="px-2.5 py-1.5 rounded-lg text-xs font-bold bg-indigo-950 text-indigo-300 border border-indigo-900 hover:bg-indigo-900 transition-all flex items-center space-x-1 shadow-inner focus:outline-none"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>Add Task</span>
              </button>
            </div>
          </div>

          {viewMode === 'timeline' && (
            <div className="bg-gray-950/40 p-3 rounded-2xl border border-gray-900 mb-6 shadow-inner animate-slide">
              <div className="flex justify-between items-center mb-3">
                <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Weekly Overview</h3>
                <div className="flex items-center space-x-1">
                  <button 
                    type="button"
                    onClick={() => {
                      setWeekOffset(prev => {
                        const nextOffset = prev - 1;
                        const currentDayIdx = selectedDate.getDay();
                        const days = getDaysForOffsetWeek(nextOffset);
                        const targetDay = days[currentDayIdx];
                        setSelectedDate(targetDay);
                        setVisibleDate(targetDay);
                        return nextOffset;
                      });
                    }}
                    className="p-1.5 rounded-lg bg-gray-900 border border-gray-800 hover:bg-gray-800 text-gray-400 hover:text-white transition-all focus:outline-none"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </button>
                  <button 
                    type="button"
                    onClick={() => {
                      setWeekOffset(0);
                      const today = new Date();
                      setSelectedDate(today);
                      setVisibleDate(today);
                    }}
                    className="px-2 py-1 rounded-lg bg-gray-900 border border-gray-800 text-[10px] font-bold text-gray-400 hover:text-white transition-all focus:outline-none"
                  >
                    Today
                  </button>
                  <button 
                    type="button"
                    onClick={() => {
                      setWeekOffset(prev => {
                        const nextOffset = prev + 1;
                        const currentDayIdx = selectedDate.getDay();
                        const days = getDaysForOffsetWeek(nextOffset);
                        const targetDay = days[currentDayIdx];
                        setSelectedDate(targetDay);
                        setVisibleDate(targetDay);
                        return nextOffset;
                      });
                    }}
                    className="p-1.5 rounded-lg bg-gray-900 border border-gray-800 hover:bg-gray-800 text-gray-400 hover:text-white transition-all focus:outline-none"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              
              <div className="flex justify-between items-center gap-1.5 overflow-x-auto">
                {getDaysForOffsetWeek(weekOffset).map((day, idx) => {
                  const isSelected = isSameDay(day, selectedDate);
                  const isToday = isSameDay(day, new Date());
                  const dailyTasks = getDailyWorkload(day);
                  
                  return (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => {
                        setSelectedDate(day);
                        setVisibleDate(day);
                      }}
                      className={`flex-1 min-w-[44px] py-2.5 px-1 rounded-xl flex flex-col items-center transition-all ${
                        isSelected ? 'bg-indigo-650 text-white shadow-lg shadow-indigo-650/30 scale-[1.04]' :
                        isToday ? 'bg-indigo-950/30 border border-indigo-900/50 text-indigo-300' :
                        'bg-gray-900/20 border border-transparent text-gray-400 hover:bg-gray-900/50 hover:text-gray-200'
                      }`}
                    >
                      <span className="text-[9px] uppercase font-bold tracking-wider">{day.toLocaleDateString([], { weekday: 'short' })}</span>
                      <span className="text-sm font-extrabold mt-0.5">{day.getDate()}</span>
                      
                      {/* Workload Indicator Dots */}
                      <div className="flex gap-0.5 mt-1.5 h-1 justify-center items-center">
                        {dailyTasks.slice(0, 3).map((t, tIdx) => {
                          let dotColor = "bg-gray-500/60";
                          if (t.energy_level === 'crimson') dotColor = "bg-red-400";
                          else if (t.energy_level === 'teal') dotColor = "bg-teal-400";
                          else if (t.constraint_type === 'hard') dotColor = "bg-indigo-400";
                          
                          return <span key={tIdx} className={`h-1 w-1 rounded-full ${dotColor}`} />;
                        })}
                        {dailyTasks.length > 3 && <span className="text-[7px] font-bold opacity-70">+</span>}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

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
                  
                  const cellTasks = tasks.filter(t => isSameDay(parseTaskDate(t.start_time), cell.date));
                  
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

          <div className="mb-4 text-xs font-semibold text-gray-400">
            Showing tasks for {selectedDate.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}
          </div>

          {/* Timeline Cards */}
          <div className="space-y-4 relative pl-4 border-l border-gray-800">
            {(() => {
              // Apply staged changes preview to the list if active
              const previewTasks = tasks.map(t => {
                if (activeProposalOption) {
                  const change = activeProposalOption.proposed_changes.find(c => c.task_id === t.id);
                  if (change) {
                    return {
                      ...t,
                      start_time: change.new_start,
                      end_time: change.new_end,
                      isPreviewGhost: true
                    };
                  }
                }
                return t;
              });

              const filteredTasks = previewTasks.filter(t => {
                const tDate = parseTaskDate(t.start_time);
                return isSameDay(tDate, selectedDate);
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

              const sortedTasks = [...filteredTasks].sort((a, b) => parseTaskDate(a.start_time) - parseTaskDate(b.start_time));
              const now = currentTime;
              const isSelectedToday = isSameDay(selectedDate, now);
              const nowMinutes = now.getHours() * 60 + now.getMinutes();
              let timeIndicatorRendered = false;

              const elements = [];

              sortedTasks.forEach((task) => {
                const taskStart = parseTaskDate(task.start_time);
                const startMinutes = taskStart.getHours() * 60 + taskStart.getMinutes();

                if (isSelectedToday && !timeIndicatorRendered && nowMinutes < startMinutes) {
                  elements.push(
                    <div key="time-indicator" className="relative my-2 py-1.5 flex items-center justify-center">
                      <div className="absolute left-[-21px] h-3 w-3 rounded-full bg-red-500 ring-4 ring-red-500/20 animate-pulse"></div>
                      <div className="w-full h-[1px] bg-gradient-to-r from-red-500/50 to-transparent"></div>
                      <span className="absolute right-4 text-[9px] bg-red-950/80 border border-red-900 text-red-400 px-1.5 py-0.5 rounded font-bold uppercase tracking-wider">
                        Current Time: {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                  );
                  timeIndicatorRendered = true;
                }

                const isCrimson = task.energy_level === 'crimson';
                const isTeal = task.energy_level === 'teal';
                const isCompleted = task.status === 'completed';
                const isExpanded = expandedTasks[task.id];

                let cardClass = "bg-gray-900/40 border-gray-800 backdrop-blur-md text-gray-300 border-l-4 border-l-gray-500";
                if (task.isPreviewGhost) {
                  cardClass = "bg-indigo-950/15 border-indigo-500/50 border-dashed text-indigo-300 border-l-4 border-l-indigo-400 opacity-80 animate-pulse";
                } else if (isCompleted) {
                  cardClass = "bg-gray-950/20 border-gray-950 text-gray-500 border-l-4 border-l-gray-700 opacity-60";
                } else if (isCrimson) {
                  cardClass = "bg-red-950/20 border-red-600/50 backdrop-blur-md shadow-sm shadow-red-950/20 text-red-200 border-l-4 border-l-red-600";
                } else if (isTeal) {
                  cardClass = "bg-teal-950/20 border-teal-600/50 backdrop-blur-md shadow-sm shadow-teal-950/20 text-teal-200 border-l-4 border-l-teal-600";
                }

                elements.push(
                  <div 
                    key={task.id} 
                    className={`relative p-4 rounded-xl transition-all hover:scale-[1.01] flex items-start space-x-4 border ${cardClass} float-ui`}
                  >
                    <div className={`absolute -left-[22px] top-5 h-3.5 w-3.5 rounded-full bg-darkspace border-2 ${
                      task.isPreviewGhost ? 'border-indigo-400 bg-indigo-950' : isCompleted ? 'border-green-500 bg-green-500' : isCrimson ? 'border-red-600' : isTeal ? 'border-teal-600' : 'border-gray-500'
                    }`}></div>
                    
                    <button
                      type="button"
                      onClick={() => handleCompleteTask(task.id, task.status)}
                      className="mt-0.5 focus:outline-none flex-shrink-0 animate-pulse"
                      title={isCompleted ? "Mark Pending" : "Mark Completed"}
                    >
                      <CheckCircle className={`h-5 w-5 transition-all ${
                        isCompleted ? 'text-green-500 fill-green-500/20' : 'text-gray-600 hover:text-green-400'
                      }`} />
                    </button>
                    
                    <div className="flex-1">
                      <div className="flex justify-between items-start">
                        <div className="cursor-pointer select-text flex-1" onClick={() => toggleTaskExpand(task.id)}>
                          <h3 className={`font-semibold text-base hover:text-indigo-400 transition-all flex items-center space-x-1.5 ${
                            isCompleted ? 'text-gray-500 line-through' : 'text-gray-100'
                          }`}>
                            <span>{task.title}</span>
                            {task.isPreviewGhost && (
                              <span className="text-[9px] bg-indigo-950/80 border border-indigo-850 text-indigo-400 px-1.5 py-0.5 rounded font-bold uppercase tracking-wider">
                                Preview Slot
                              </span>
                            )}
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
                            type="button"
                            onClick={() => handleEditTaskClick(task)}
                            className="p-1 rounded text-gray-500 hover:text-indigo-400 hover:bg-gray-800/60 transition-all focus:outline-none"
                            title="Edit Task"
                          >
                            <Edit className="h-3.5 w-3.5" />
                          </button>
                          <button 
                            type="button"
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
                            <Zap className={`h-3.5 w-3.5 ${isCompleted ? 'text-gray-600' : isCrimson ? 'text-red-400' : 'text-teal-400'}`} />
                            <span className={isCompleted ? 'text-gray-500' : isCrimson ? 'text-red-400' : 'text-teal-400'}>
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

              if (isSelectedToday && !timeIndicatorRendered && sortedTasks.length > 0) {
                elements.push(
                  <div key="time-indicator" className="relative my-2 py-1.5 flex items-center justify-center">
                    <div className="absolute left-[-21px] h-3 w-3 rounded-full bg-red-500 ring-4 ring-red-500/20 animate-pulse"></div>
                    <div className="w-full h-[1px] bg-gradient-to-r from-red-500/50 to-transparent"></div>
                    <span className="absolute right-4 text-[9px] bg-red-950/80 border border-red-900 text-red-400 px-1.5 py-0.5 rounded font-bold uppercase tracking-wider">
                      Current Time: {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                );
              }

              return elements;
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
            {showStatusBadge && (
              <span 
                className={`inline-block h-2.5 w-2.5 rounded-full cursor-help transition-all duration-500 ${
                  ollamaStatus === 'loading' || kokoroStatus === 'loading'
                    ? 'bg-yellow-400 animate-pulse'
                    : ollamaStatus === 'error' || kokoroStatus === 'error'
                    ? 'bg-red-400'
                    : 'bg-emerald-400'
                }`}
                title={`LLM status: ${ollamaStatus}\nVoice status: ${kokoroStatus}`}
              />
            )}
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
            const proposalMatch = chat.text ? chat.text.match(/<schedule-proposal tx="([^"]+)">/) : null;
            const txId = proposalMatch ? proposalMatch[1] : null;
            const proposalData = txId ? proposalsMap[txId] : null;
            let cleanText = chat.text ? chat.text.replace(/<schedule-proposal tx="[^"]+">/, "") : "";
            cleanText = cleanText.replace(/^<tool_(?:call\s+name="[^"]+")?>/, "");
            cleanText = cleanText.replace(/^<tool_/, "");
            cleanText = cleanText.replace(/<tool_call\s+name="[^"]+">.*?<\/tool_call>/g, "");
            cleanText = cleanText.replace(/<tool_call\s+name="[^"]+">/g, "");
            cleanText = cleanText.replace(/<\/tool_call>/g, "");
            cleanText = cleanText.trim();
            
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
                  {cleanText ? renderMessageContent(cleanText) : isAgent ? <p className="leading-relaxed">Generating schedule optimizations...</p> : null}
                  
                  {isAgent && proposalData && (
                    <div className="mt-4 p-4 rounded-xl bg-gray-900 border border-gray-800 space-y-3 shadow-lg max-w-full text-left">
                      <div className="flex items-center space-x-1.5 text-xs text-indigo-400 font-bold uppercase tracking-wider">
                        <Calendar className="h-4 w-4" />
                        <span>Workaround Alternatives</span>
                      </div>
                      
                      {/* Options Tabs */}
                      <div className="flex space-x-1 bg-gray-950 p-0.5 rounded-lg border border-gray-850">
                        {proposalData.options.map((opt) => {
                          const isActive = stagedProposal && stagedProposal.transaction_id === txId && activeProposalOption && activeProposalOption.option_id === opt.option_id;
                          return (
                            <button
                              key={opt.option_id}
                              type="button"
                              onClick={() => {
                                setStagedProposal(proposalData);
                                setActiveProposalOption(opt);
                              }}
                              className={`flex-1 text-[9px] font-bold py-1.5 rounded transition-all ${
                                isActive 
                                  ? 'bg-indigo-600 text-white shadow' 
                                  : 'text-gray-400 hover:text-gray-200'
                              }`}
                            >
                              {opt.option_id.toUpperCase()}
                            </button>
                          );
                        })}
                      </div>
                      
                      {/* Option details */}
                      {(() => {
                        const currentOpt = (stagedProposal && stagedProposal.transaction_id === txId) ? activeProposalOption : null;
                        if (!currentOpt) {
                          return <p className="text-[11px] text-gray-500 italic">Select a workaround strategy tab above to preview shifts on the timeline.</p>;
                        }
                        
                        return (
                          <div className="space-y-3">
                            <p className="text-[11px] text-gray-300 bg-gray-950/40 p-2 rounded-lg border border-gray-850 leading-relaxed">
                              {currentOpt.description}
                            </p>
                            
                            {/* Proposed shifts preview */}
                            <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                              {currentOpt.proposed_changes.map((change, cIdx) => {
                                const taskObj = tasks.find(t => t.id === change.task_id) || {};
                                return (
                                  <div key={cIdx} className="text-[10px] p-2 rounded-lg bg-gray-950/80 border border-gray-850 flex justify-between items-center">
                                    <span className="font-medium text-gray-300 truncate max-w-[100px]">{taskObj.title || change.task_id}</span>
                                    <span className="font-mono text-[9px] text-indigo-400 bg-indigo-950/40 border border-indigo-900/30 px-1 rounded">{formatTime(change.new_start)}</span>
                                  </div>
                                );
                              })}
                            </div>

                            {/* Action Buttons */}
                            <div className="flex space-x-2 pt-1.5">
                              <button
                                type="button"
                                onClick={() => handleCommitProposal(txId, currentOpt.option_id)}
                                className="flex-1 py-1.5 rounded-lg text-[10px] font-bold bg-gradient-to-r from-emerald-600 to-teal-500 text-white shadow hover:opacity-90 active:scale-95 transition-all"
                              >
                                Approve
                              </button>
                              <button
                                type="button"
                                onClick={() => handleRejectProposal(txId)}
                                className="px-2.5 py-1.5 rounded-lg text-[10px] font-bold bg-red-950/20 border border-red-900/50 text-red-400 hover:bg-red-900/40 transition-all"
                              >
                                Decline
                              </button>
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                  )}

                  {isAgent && chat.text && chat.text.includes("✓ Rescheduling Plan Applied!") && (
                    <div className="mt-3 p-2.5 rounded-xl bg-emerald-950/20 border border-emerald-900/40 flex justify-between items-center text-left">
                      <span className="text-[10px] text-emerald-400 font-semibold">Changes applied.</span>
                      <button
                        type="button"
                        onClick={handleRollbackProposal}
                        className="px-2 py-1 bg-indigo-950 border border-indigo-900 text-indigo-300 hover:bg-indigo-900 rounded text-[9px] font-bold uppercase transition-all"
                      >
                        Undo
                      </button>
                    </div>
                  )}
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
          <button
            type="button"
            onClick={isVoiceActive ? stopVoiceChat : startVoiceChat}
            className={`absolute left-2 top-2 p-1.5 rounded-lg flex items-center justify-center transition-all ${
              isVoiceActive
                ? 'bg-red-600 text-white animate-pulse shadow-lg shadow-red-950'
                : 'bg-gray-850 hover:bg-gray-800 text-indigo-400 border border-gray-800'
            }`}
            title={isVoiceActive ? `Voice chat active: ${voiceStatus}. Click to stop.` : "Start Real-Time Voice Chat"}
          >
            {isVoiceActive ? <Mic className="h-4 w-4" /> : <MicOff className="h-4 w-4" />}
          </button>
          
          <input 
            type="text" 
            placeholder={isVoiceActive ? `Voice Chatting (${voiceStatus})...` : "Ask to reschedule, set dependencies, check Gmail..."} 
            value={inputMessage}
            onChange={(e) => setInputMessage(e.target.value)}
            disabled={isLoading || isVoiceActive}
            className="w-full bg-gray-900 border border-gray-800 rounded-xl py-3 pl-12 pr-12 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-indigo-500 transition-all shadow-inner"
          />
          <button 
            type="submit" 
            disabled={isLoading || isVoiceActive || !inputMessage.trim()}
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

      {/* DIALOG ADD/EDIT TASK MODAL */}
      {showAddForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop dim overlay */}
          <div 
            onClick={() => setShowAddForm(false)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
          ></div>
          
          {/* Float-centered dialog */}
          <form 
            onSubmit={handleAddTask} 
            className="relative w-full max-w-xl bg-gray-950 border border-gray-800 rounded-3xl p-6 md:p-8 space-y-4 shadow-2xl z-10 max-h-[90vh] overflow-y-auto"
          >
            <div className="flex justify-between items-center pb-2 border-b border-gray-900">
              <h3 className="text-base font-bold text-gray-100 flex items-center space-x-2">
                {isEditing ? <Edit className="h-5 w-5 text-indigo-400" /> : <Plus className="h-5 w-5 text-indigo-400" />}
                <span>{isEditing ? "Edit Schedule Task" : "Create Schedule Task"}</span>
              </h3>
              <button 
                type="button"
                onClick={() => setShowAddForm(false)}
                className="text-gray-500 hover:text-gray-300 text-xs font-semibold focus:outline-none"
              >
                Done
              </button>
            </div>

            {!isEditing && (
              <div className="space-y-1.5 pb-2 border-b border-gray-900/60">
                <label className="block text-[10px] text-gray-405 font-bold uppercase tracking-wider mb-1">Quick Templates</label>
                <div className="flex flex-wrap gap-2">
                  {[
                    { title: "Cook Dinner", duration: 60, energy: "none", desc: "Prep ingredients and cook dinner" },
                    { title: "Deep Work / Study", duration: 120, energy: "crimson", desc: "Focused study or coding session" },
                    { title: "Gym Workout", duration: 60, energy: "crimson", desc: "Strength training or cardio session" },
                    { title: "Email & Admin", duration: 30, energy: "teal", desc: "Process inbox and administrative chores" },
                    { title: "Morning Meditation", duration: 15, energy: "teal", desc: "Mindfulness breathing and reflection" }
                  ].map((tpl, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => {
                        setNewTitle(tpl.title);
                        setNewDesc(tpl.desc);
                        setNewEnergy(tpl.energy);
                        
                        // Set start time to today/selected date at nearest hour, end time to start + duration
                        const start = new Date(selectedDate);
                        const now = new Date();
                        start.setHours(now.getHours() + 1, 0, 0, 0);
                        const end = new Date(start.getTime() + tpl.duration * 60000);
                        setNewStart(toLocalDatetimeString(start.toISOString()));
                        setNewEnd(toLocalDatetimeString(end.toISOString()));
                      }}
                      className="bg-gray-900 border border-gray-800 hover:border-indigo-500 hover:bg-gray-850/80 text-[10px] text-gray-300 font-medium px-2.5 py-1.5 rounded-full transition-all"
                    >
                      {tpl.title} ({tpl.duration}m)
                    </button>
                  ))}
                </div>
              </div>
            )}

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

              {!isEditing && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1 font-semibold">Recurrence Frequency</label>
                    <select 
                      value={recurrencePattern}
                      onChange={(e) => setRecurrencePattern(e.target.value)}
                      className="w-full bg-gray-900 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none"
                    >
                      <option value="none">Does not repeat</option>
                      <option value="daily">Daily</option>
                      <option value="weekly">Weekly</option>
                      <option value="monthly">Monthly</option>
                    </select>
                  </div>
                  {recurrencePattern !== 'none' && (
                    <div>
                      <label className="block text-[10px] text-gray-400 mb-1 font-semibold">Occurrences Count</label>
                      <input 
                        type="number"
                        min="1"
                        max="100"
                        value={recurrenceCount}
                        onChange={(e) => setRecurrenceCount(e.target.value)}
                        className="w-full bg-gray-900 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                        required
                      />
                    </div>
                  )}
                </div>
              )}
              {!isEditing && recurrencePattern === 'weekly' && (
                <div className="bg-gray-900 border border-gray-800 rounded-xl p-3.5 space-y-2">
                  <label className="block text-[10px] text-gray-400 font-bold uppercase tracking-wider">Recur on these Weekdays</label>
                  <div className="flex justify-between gap-1">
                    {[
                      { label: "M", value: 0 },
                      { label: "T", value: 1 },
                      { label: "W", value: 2 },
                      { label: "T", value: 3 },
                      { label: "F", value: 4 },
                      { label: "S", value: 5 },
                      { label: "S", value: 6 }
                    ].map((day) => {
                      const isSelected = recurrenceDays.includes(day.value);
                      return (
                        <button
                          key={day.value}
                          type="button"
                          onClick={() => {
                            if (isSelected) {
                              setRecurrenceDays(recurrenceDays.filter(d => d !== day.value));
                            } else {
                              setRecurrenceDays([...recurrenceDays, day.value].sort());
                            }
                          }}
                          className={`w-9 h-9 rounded-lg flex items-center justify-center text-xs font-semibold border transition-all ${
                            isSelected
                              ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300 font-bold'
                              : 'bg-gray-950 border-gray-800 text-gray-400 hover:text-gray-250'
                          }`}
                        >
                          {day.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
              {isEditing && (
                (() => {
                  const currentTask = tasks.find(t => t.id === editingTaskId);
                  const isRecurring = currentTask && (currentTask.recurrence_group_id || currentTask.source_event_id);
                  if (!isRecurring) return null;
                  return (
                    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-2.5">
                      <label className="block text-[10px] text-gray-400 font-bold uppercase tracking-wider">Scope of Changes</label>
                      <div className="flex space-x-3">
                        <button
                          type="button"
                          onClick={() => setEditScope('single')}
                          className={`flex-1 py-2 px-3 rounded-lg text-xs font-semibold border transition-all ${
                            editScope === 'single'
                              ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300'
                              : 'bg-gray-950 border-gray-800 text-gray-400 hover:text-gray-250'
                          }`}
                        >
                          This Occurrence Only
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditScope('series')}
                          className={`flex-1 py-2 px-3 rounded-lg text-xs font-semibold border transition-all ${
                            editScope === 'series'
                              ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300'
                              : 'bg-gray-950 border-gray-800 text-gray-400 hover:text-gray-250'
                          }`}
                        >
                          Entire Routine Series
                        </button>
                      </div>
                    </div>
                  );
                })()
              )}
            </div>

            <div className="flex space-x-3 pt-2">
              <button 
                type="button" 
                onClick={() => setShowAddForm(false)}
                className="flex-1 py-3 rounded-xl text-xs font-semibold glass-panel text-gray-300"
              >
                Cancel
              </button>
              {isEditing && (
                <button 
                  type="button" 
                  onClick={async () => {
                    const currentTask = tasks.find(t => t.id === editingTaskId);
                    if (!currentTask) return;
                    const isRecurring = currentTask.recurrence_group_id || currentTask.source_event_id;
                    if (isRecurring) {
                      if (window.confirm(`Are you sure you want to delete this ${editScope === 'series' ? 'entire recurring series' : 'occurrence'}?`)) {
                        setShowAddForm(false);
                        await executeDeleteTask(editingTaskId, editScope);
                      }
                    } else {
                      if (window.confirm(`Are you sure you want to delete "${currentTask.title}"?`)) {
                        setShowAddForm(false);
                        await executeDeleteTask(editingTaskId, 'single');
                      }
                    }
                  }}
                  className="flex-1 py-3 rounded-xl text-xs font-semibold bg-red-650 hover:bg-red-600 text-white border border-red-800/40"
                >
                  Delete Task
                </button>
              )}
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

      {/* RECURRING TASK ACTION CONFIRM MODAL */}
      {recurringConfirm.isOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div 
            onClick={() => setRecurringConfirm({ isOpen: false, taskId: null, actionType: 'delete', payload: null })}
            className="absolute inset-0 bg-black/70 backdrop-blur-sm transition-opacity"
          ></div>
          
          <div className="relative w-full max-w-md bg-gray-950 border border-gray-800 rounded-3xl p-6 space-y-5 shadow-2xl z-10 text-center float-ui">
            <div className="mx-auto h-12 w-12 rounded-full bg-indigo-950/60 border border-indigo-900 flex items-center justify-center glow-indigo mb-2">
              <RefreshCw className="h-5 w-5 text-indigo-400" />
            </div>
            
            <div className="space-y-2">
              <h3 className="text-lg font-bold text-gray-100">
                {recurringConfirm.actionType === 'delete' ? "Delete Recurring Task" : "Edit Recurring Task"}
              </h3>
              <p className="text-xs text-gray-400 leading-relaxed">
                This is a recurring task series. Would you like to apply this {recurringConfirm.actionType === 'delete' ? "deletion" : "modification"} to this occurrence only, or to all scheduled instances?
              </p>
            </div>
            
            <div className="flex flex-col space-y-2 pt-2">
              <button
                type="button"
                onClick={async () => {
                  const { taskId, actionType, payload } = recurringConfirm;
                  setRecurringConfirm({ isOpen: false, taskId: null, actionType: 'delete', payload: null });
                  if (actionType === 'delete') {
                    await executeDeleteTask(taskId, 'single');
                  } else {
                    await executeEditTask(taskId, payload, 'single');
                  }
                }}
                className="w-full py-3 rounded-xl text-xs font-semibold bg-gray-900 border border-gray-800 hover:border-indigo-500 hover:bg-gray-850 text-indigo-300 transition-all focus:outline-none"
              >
                Apply to This Occurrence Only
              </button>
              
              <button
                type="button"
                onClick={async () => {
                  const { taskId, actionType, payload } = recurringConfirm;
                  setRecurringConfirm({ isOpen: false, taskId: null, actionType: 'delete', payload: null });
                  if (actionType === 'delete') {
                    await executeDeleteTask(taskId, 'series');
                  } else {
                    await executeEditTask(taskId, payload, 'series');
                  }
                }}
                className="w-full py-3 rounded-xl text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-950 transition-all focus:outline-none"
              >
                Apply to Entire Recurring Series
              </button>
              
              <button
                type="button"
                onClick={() => setRecurringConfirm({ isOpen: false, taskId: null, actionType: 'delete', payload: null })}
                className="w-full py-2.5 text-xs text-gray-500 hover:text-gray-350 transition-all focus:outline-none"
              >
                Cancel
              </button>
            </div>
          </div>
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
              
              <div className="flex flex-col items-center justify-center p-4 bg-gray-900 border border-gray-800 rounded-2xl min-h-[200px]">
                {!tunnelUrl ? (
                  <div className="flex flex-col items-center justify-center space-y-3 py-6">
                    <RefreshCw className="w-8 h-8 text-indigo-500 animate-spin" />
                    <p className="text-xs text-gray-400 font-medium">Generating secure connection link...</p>
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center space-y-3 w-full">
                    <div className="p-2.5 bg-white rounded-xl shadow-glow transition-transform duration-300 hover:scale-105">
                      <img 
                        src={`https://api.qrserver.com/v1/create-qr-code/?size=140x140&data=${encodeURIComponent(apiKey ? `${tunnelUrl}?key=${apiKey}` : tunnelUrl)}`} 
                        alt="Quantime Mobile Link QR Code"
                        className="w-[140px] h-[140px] block"
                      />
                    </div>
                    <div className="text-center w-full px-2">
                      <p className="font-mono text-xs select-all text-indigo-400 font-bold break-all">{tunnelUrl}</p>
                      {apiKey && (
                        <div className="mt-2 p-2 bg-gray-950/80 border border-gray-800/60 rounded-xl max-w-xs mx-auto">
                          <p className="text-[9px] text-gray-400 font-bold uppercase tracking-wider">Access Token</p>
                          <p className="font-mono text-[10px] text-indigo-300 font-bold select-all break-all">{apiKey}</p>
                        </div>
                      )}
                      <p className="text-[10px] text-gray-500 mt-2 font-medium">Scan with your phone camera to open instantly</p>
                    </div>
                  </div>
                )}
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
      {showSetupModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div 
            onClick={() => setShowSetupModal(false)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
          ></div>
          
          <div className="relative w-full max-w-md bg-gray-900 border border-gray-800 rounded-3xl p-6 md:p-8 shadow-2xl z-10 animate-slide">
            <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500"></div>
            
            <div className="flex justify-between items-center pb-3 border-b border-gray-800 mb-4">
              <h3 className="text-base font-bold text-gray-100 flex items-center space-x-2">
                <Sparkles className="h-5 w-5 text-indigo-400" />
                <span>Custom OAuth Setup</span>
              </h3>
              <button 
                type="button"
                onClick={() => setShowSetupModal(false)}
                className="text-gray-500 hover:text-gray-300 text-xs font-semibold focus:outline-none"
              >
                Close
              </button>
            </div>

            <div className="space-y-4 text-xs text-gray-300 leading-relaxed bg-black/40 border border-gray-850 p-4 rounded-2xl mb-4">
              <p className="font-semibold text-white flex items-center space-x-1">
                <AlertTriangle className="h-4 w-4 text-amber-500 mr-1" />
                <span>Configure Custom OAuth Web Credentials</span>
              </p>
              <ol className="list-decimal list-inside space-y-1.5 pl-1.5">
                <li>Create an OAuth Web Client in Google Cloud Console.</li>
                <li>Add this Authorized Redirect URI:</li>
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
                  className="w-full bg-gray-955 border border-gray-800 rounded-xl p-3 text-xs text-gray-100 focus:outline-none focus:border-indigo-500"
                  required
                />
              </div>

              <div>
                <label className="block text-[10px] text-gray-400 mb-1 font-semibold uppercase tracking-wider">OAuth Client ID</label>
                <input 
                  type="text" 
                  placeholder="Enter client ID" 
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

              <div className="flex space-x-3 pt-2">
                <button 
                  type="button" 
                  onClick={() => setShowSetupModal(false)}
                  className="flex-1 py-3 rounded-xl text-xs font-semibold glass-panel text-gray-300 focus:outline-none"
                >
                  Cancel
                </button>
                <button 
                  type="submit" 
                  disabled={isSavingSetup}
                  className="flex-1 py-3 rounded-xl text-xs font-bold bg-indigo-650 hover:bg-indigo-500 text-white transition-all disabled:bg-gray-800 flex items-center justify-center space-x-2 focus:outline-none"
                >
                  {isSavingSetup ? <span>Saving...</span> : <span>Save Details</span>}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      {showMobileInstallPrompt && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-gray-955/80 backdrop-blur-md animate-fade-in">
          <div className="relative w-full max-w-sm bg-gray-900 border border-gray-800 rounded-3xl p-6 shadow-glow flex flex-col items-center text-center space-y-5 animate-slide">
            
            {/* Logo Icon */}
            <div className="h-16 w-16 rounded-2xl bg-gradient-to-tr from-indigo-500 to-purple-600 flex items-center justify-center glow-indigo mb-2 animate-bounce-subtle">
              <Sparkles className="h-8 w-8 text-white" />
            </div>

            {/* Title & Desc */}
            <div className="space-y-2">
              <h2 className="text-xl font-extrabold text-white">Install Quantime App</h2>
              <p className="text-xs text-gray-400 leading-relaxed">
                Add Quantime to your Home Screen for push notifications, offline calendar access, and a native app experience.
              </p>
            </div>

            {/* Action buttons based on OS */}
            <div className="w-full space-y-3">
              {deferredPrompt ? (
                /* Android / Chrome prompt available */
                <button 
                  onClick={async () => {
                    await handleInstallPWA();
                    setShowMobileInstallPrompt(false);
                  }}
                  className="w-full bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-bold py-3 px-4 rounded-xl text-xs shadow-lg transition-all"
                >
                  Install App
                </button>
              ) : (
                /* iOS Safari / Fallback steps */
                <div className="bg-gray-955 border border-gray-805/80 rounded-2xl p-4 text-left space-y-3">
                  <h4 className="text-[10px] font-bold text-indigo-400 uppercase tracking-widest">iOS / Safari Instructions</h4>
                  <div className="space-y-2 text-[11px] text-gray-300">
                    <div className="flex items-center space-x-2">
                      <span className="flex items-center justify-center h-4.5 w-4.5 rounded-full bg-indigo-950 text-indigo-300 font-bold text-[9px]">1</span>
                      <p>Tap the <strong>Share</strong> button in Safari (bottom navigation bar).</p>
                    </div>
                    <div className="flex items-center space-x-2">
                      <span className="flex items-center justify-center h-4.5 w-4.5 rounded-full bg-indigo-950 text-indigo-300 font-bold text-[9px]">2</span>
                      <p>Scroll down and select <strong>Add to Home Screen</strong>.</p>
                    </div>
                  </div>
                </div>
              )}

              <button 
                onClick={() => {
                  sessionStorage.setItem('pwa_prompt_dismissed', 'true');
                  setShowMobileInstallPrompt(false);
                }}
                className="w-full py-2.5 text-xs text-gray-500 hover:text-gray-300 transition-all font-semibold"
              >
                Continue in Browser
              </button>
            </div>

          </div>
        </div>
      )}

      {isAppInstalledSuccessfully && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-gray-955/90 backdrop-blur-md animate-fade-in">
          <div className="relative w-full max-w-sm bg-gray-900 border border-gray-800 rounded-3xl p-6 shadow-glow flex flex-col items-center text-center space-y-5 animate-slide">
            
            {/* Celebration Icon */}
            <div className="h-16 w-16 rounded-2xl bg-gradient-to-tr from-emerald-500 to-teal-500 flex items-center justify-center glow-emerald mb-2">
              <CheckCircle className="h-8 w-8 text-black" />
            </div>

            {/* Title & Desc */}
            <div className="space-y-2">
              <h2 className="text-xl font-extrabold text-white">Ecosystem Connected!</h2>
              <p className="text-xs text-gray-400 leading-relaxed">
                Quantime is now installed on your device.
              </p>
            </div>

            <div className="w-full bg-gray-955 border border-gray-805/80 rounded-2xl p-4 space-y-2.5 text-left">
              <h4 className="text-[10px] font-bold text-emerald-400 uppercase tracking-widest">How to Launch</h4>
              <ol className="space-y-1.5 text-[11px] text-gray-300 list-decimal pl-4">
                <li>Close this browser tab.</li>
                <li>Go to your phone **App Drawer** (swipe up on your home screen) to locate the new **Quantime** app icon.</li>
                <li>Press and hold the icon, then drag it directly onto your Home Screen.</li>
                <li>Tap the icon to start managing your schedule.</li>
              </ol>
            </div>

            <button 
              onClick={() => setIsAppInstalledSuccessfully(false)}
              className="w-full bg-gray-850 hover:bg-gray-800 text-gray-350 font-semibold py-3 px-4 rounded-xl text-xs transition-all"
            >
              Dismiss
            </button>

          </div>
        </div>
      )}
      {isVoiceActive && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-955/80 backdrop-blur-md animate-fade-in">
          <div className="relative w-full max-w-lg bg-gray-900 border border-gray-800 rounded-3xl p-6 md:p-8 shadow-2xl z-10 flex flex-col space-y-6">
            <div className="flex justify-between items-center pb-3 border-b border-gray-800">
              <div className="flex items-center space-x-2">
                <div className={`h-2.5 w-2.5 rounded-full ${
                  voiceStatus === 'recording' ? 'bg-red-500 animate-pulse' : voiceStatus === 'thinking' ? 'bg-amber-500 animate-ping' : 'bg-green-500 animate-pulse'
                }`}></div>
                <h3 className="text-base font-bold text-gray-100 uppercase tracking-wider">
                  Voice Session: {voiceStatus.toUpperCase()}
                </h3>
              </div>
              <button 
                type="button"
                onClick={stopVoiceChat}
                className="px-3 py-1 bg-red-950/20 border border-red-900/50 text-red-400 hover:bg-red-900/40 rounded-lg text-xs font-bold transition-all focus:outline-none"
              >
                Close Session
              </button>
            </div>

            {/* Glowing Pulsing Mic Icon */}
            <div className="flex flex-col items-center justify-center py-6">
              <div 
                onClick={handleMicClickInOverlay}
                className={`h-20 w-20 rounded-full flex items-center justify-center cursor-pointer transition-all duration-300 ${
                voiceStatus === 'recording'
                  ? 'bg-red-600/20 border-2 border-red-500 text-red-400 shadow-lg shadow-red-950 scale-105 animate-pulse'
                  : voiceStatus === 'thinking'
                  ? 'bg-amber-600/20 border-2 border-amber-500 text-amber-400 scale-100 animate-bounce'
                  : 'bg-green-600/20 border-2 border-green-500 text-green-400 scale-105 shadow-lg shadow-green-950 animate-pulse'
              }`}>
                <Mic className="h-8 w-8" />
              </div>
              <span className="text-xs text-gray-505 mt-3 font-semibold font-mono">
                {voiceError ? (
                  <span className="text-red-400 animate-pulse">{voiceError}</span>
                ) : voiceStatus === 'recording' ? (
                  'Speak now... (Stop speaking to send)'
                ) : voiceStatus === 'thinking' ? (
                  'Gemma 4 is thinking...'
                ) : (
                  'Assistant is speaking (Tap mic to interrupt)...'
                )}
              </span>
            </div>

            {/* Live Streaming Transcript */}
            <div className="flex flex-col space-y-2">
              <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Live Transcript</span>
              <div className="p-4 rounded-2xl bg-gray-950/60 border border-gray-850 min-h-24 max-h-36 overflow-y-auto text-sm text-gray-100 leading-relaxed scrollbar-thin">
                {(() => {
                  if (!activeVoiceText) return <span className="text-gray-650 italic">Wait for response...</span>;
                  let cleanVoiceText = activeVoiceText;
                  cleanVoiceText = cleanVoiceText.replace(/^<tool_(?:call\s+name="[^"]+")?>/, "");
                  cleanVoiceText = cleanVoiceText.replace(/^<tool_/, "");
                  cleanVoiceText = cleanVoiceText.replace(/<schedule-proposal[^>]*>[\s\S]*?<\/schedule-proposal>/g, "");
                  cleanVoiceText = cleanVoiceText.trim();
                  return cleanVoiceText ? renderMessageContent(cleanVoiceText) : <span className="text-gray-650 italic">Wait for response...</span>;
                })()}
              </div>
            </div>

            {/* Live Agentic Tool Console */}
            <div className="flex flex-col space-y-2">
              <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Agentic Tool Console</span>
              <div className="p-4 rounded-2xl bg-black border border-gray-850 font-mono text-xs text-indigo-400 min-h-36 max-h-48 overflow-y-auto leading-relaxed scrollbar-thin shadow-inner">
                {activeVoiceThoughts ? (
                  activeVoiceThoughts.split('\n').map((line, idx) => (
                    <div key={idx} className="flex items-start space-x-1.5 py-0.5">
                      <span className="text-gray-600 select-none">&gt;</span>
                      <span className={line.includes("Success") ? "text-emerald-400" : line.includes("Triggered Tool") ? "text-indigo-300 font-semibold" : "text-gray-405"}>
                        {line}
                      </span>
                    </div>
                  ))
                ) : (
                  <span className="text-gray-750 italic">&gt; Idle. Waiting for background agent actions...</span>
                )}
              </div>
            </div>

          </div>
        </div>
      )}
      


    </div>
  );
}
