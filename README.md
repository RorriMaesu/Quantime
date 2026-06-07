# Quantime

Quantime is a local-first, privacy-respecting intelligent scheduling engine and task manager. By combining a local reasoning LLM (Gemma 4 compiled via Ollama) with Google Workspace APIs (Calendar and Gmail), Quantime automatically syncs, schedules, and resolves timeline dependencies right on your desktop and syncs with your mobile device.

---

## Key Features

*   **Local-First Architecture**: Your scheduling data, chat history, and semantic memory reside strictly on your local PC in SQLite databases.
*   **Intelligent Reasoning Loop**: Uses local LLM speculative decoding to reason about daily tasks, resolve calendar conflicts, and align activities with your productivity energy levels (Teal vs. Crimson).
*   **Bi-Directional Google Sync**: Imports Google Calendar events as hard constraints, updates status alterations, and fetches Gmail message headers for scheduling tasks.
*   **Onboarding Setup Wizard**: Built-in graphical wizard to quickly upload and configure Google OAuth client secrets without manually editing text files.
*   **Windows Autostart Installer**: Registers a silent background logon service so the scheduling system and mobile remote access link are active whenever your PC is running.
*   **Offline Mobile PWA**: Access your dashboard securely on your phone using the PWA and public Localtunnel gateway.

---

## Prerequisites

Ensure you have the following installed on your host PC:

1.  **Ollama**: Install [Ollama for Windows](https://ollama.com/) and verify it is running on your system.
2.  **Node.js**: Install Node.js (v18+) to run the PWA development server and Localtunnel.
3.  **Python**: Install Python 3.10+ to run the FastAPI backend gateways.

---

## Installation & Setup

### 1. Register Auto-Start Services
To start Quantime services headlessly in the background on system boot, open a PowerShell terminal as Administrator and execute:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_app.ps1
```

This registers the startup task in Windows Task Scheduler and launches the servers immediately.

### 2. Configure Google API Secrets
When accessing the frontend dashboard for the first time, you will be presented with the **First-Time Onboarding Wizard**:

1.  Open [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2.  Navigate to **API & Services > OAuth Consent Screen**, configure user type as **External**, and add scopes for `calendar.events`, `gmail.readonly`, and `userinfo.profile`.
3.  Under **Credentials**, click **Create Credentials > OAuth client ID** and select **Web application**.
4.  Add the following Authorized Redirect URI:
    `http://localhost:8000/auth/callback`
5.  Save your settings, then copy the **Project ID**, **Client ID**, and **Client Secret** and input them into the Quantime Onboarding Wizard.

### 3. Connect Mobile Device
To use Quantime on your mobile phone:
1.  Open the settings popover in the top-right header of the desktop dashboard.
2.  Select **Connect Mobile Phone**.
3.  Open the public tunnel link on your phone and enter the displayed host PC public IP code to bypass the gateway reminder screen.
4.  Tap **Add to Home Screen** inside your mobile browser to install the standalone app.

---

## Architecture & Security

*   **SQLite Storage**: Uses Write-Ahead Logging (WAL) and busy locks to allow fast concurrent transactions between the agent processor and client sockets.
*   **Fernet Token Encryption**: Sync access tokens are encrypted before being written to disk using a unique Base64 key generated dynamically on first boot.
*   **Firestore Circuit Breaker**: Caps write commands to 5 per 10 seconds to protect free Spark tier daily operations if utilizing Firestore client sync.
