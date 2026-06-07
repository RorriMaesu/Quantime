# Quantime

Quantime is a local-first, privacy-respecting intelligent scheduling engine and task manager. By combining a local reasoning LLM (Gemma 4 compiled via Ollama) with Google Workspace APIs (Calendar and Gmail), Quantime automatically syncs, schedules, and resolves timeline dependencies right on your desktop and syncs with your mobile device.

---

## How it Works: System Architecture

Quantime consists of three major components designed to run in unison:

```
                  +-----------------------------------+
                  |        React PWA Frontend         |
                  |     (Vite Server - Port 5173)     |
                  +-----------------------------------+
                                    |
                                    | REST API & SQLite Sync
                                    v
+-------------------+     +-------------------+     +--------------------+
|   Ollama LLM      | <-> |  FastAPI Gateway  | <-> |   SQLite Store     |
| (Port 11434)      |     |    (Port 8000)    |     |   (quantime.db)    |
+-------------------+     +-------------------+     +--------------------+
                                    |
                                    | Google OAuth Consent
                                    v
                          +-------------------+
                          |  Google Calendar  |
                          |    & Gmail APIs   |
                          +-------------------+
```

1.  **FastAPI Backend Gateway (Port 8000)**: Coordinates all database interactions, Google OAuth credential management, Gmail HTML stripping/parsing, and schedules tasks. It runs the agentic scheduling loop.
2.  **React PWA Dashboard (Port 5173)**: Renders a timeline view of color-coded tasks, an interactive monthly calendar grid, and the Orchestrator AI chatbot drawer. It caches assets locally to allow offline access.
3.  **Local AI Reasoning Loop (Ollama)**: Automatically compiles a custom Gemma model with speculative assistant models. The scheduler parses reasoning paths (isolated inside `<|think|>` tags) and outputs clear schedules.
4.  **Google Workspace Synchronization**: Google Calendar events are imported as hard-constraints (cannot be shifted by the AI). Flexible tasks are arranged matching energy levels (teal for low study effort, crimson for high-energy study tasks) around these events.
5.  **Multi-Device Synchronization**: Launches a secure **Localtunnel** gateway pointing to your local PWA. This generates a public HTTPS URL (e.g. `https://quantime-scheduler-green.loca.lt`) so you can access the app from your mobile browser anywhere, syncing back to your PC database.

---

## Initial Onboarding Setup

When installing Quantime for the first time, you do not need pre-configured Google credentials. 

1.  **Onboarding Wizard**: On first run, the frontend detects missing credentials and displays a setup wizard.
2.  **Create Google Cloud Credentials**:
    *   Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
    *   Navigate to **API & Services > Credentials > Create Credentials > OAuth client ID**.
    *   Set Application Type to **Web application** and add the following Redirect URI:
        `http://localhost:8000/auth/callback`
3.  **Submit Secrets**: Copy the **Project ID**, **Client ID**, and **Client Secret** into the setup wizard. The backend automatically saves them to `backend/credentials.json`.
4.  **Google OAuth Link**: Click the profile menu in the upper-right corner of the dashboard, choose **Link Google OAuth**, and authenticate with your personal Google account.

---

## Packaging the Windows Installer

To build a standalone, single-click Windows Installer (`QuantimeSetup.exe`) that packages the entire ecosystem:

1.  Download and install [Inno Setup Compiler](https://jrsoftware.org/isinfo.php).
2.  Open the terminal in the root folder and compile the `.iss` script:
    ```cmd
    iscc.exe QuantimeSetup.iss
    ```
3.  This compiles a single executable `dist/QuantimeSetup.exe`.
4.  **Distribution**: Give this `.exe` file to any user. When clicked, it copies the source code, silently installs Ollama, downloads the Gemma weights, opens firewall exceptions, and schedules the logon startup task.

---

## Detailed Feature Guide

### 1. Daily Planner Dashboard
*   **Timeline View**: Displays a clean, chronological list of scheduled tasks. Color indicators match high-energy crimson tasks and low-energy teal study items.
*   **Monthly Calendar Grid**: Toggle to the calendar view to navigate months. Selecting any cell displays detail summaries for that specific day.
*   **Task Deletion**: You can manually delete any custom task by clicking the trash icon.

### 2. Quantime Orchestrator (Chat Interface)
*   **Intelligent Chat Drawer**: Use the right sidebar to chat with the local AI. Ask to schedule new tasks, move tasks, create dependencies, or summarize emails.
*   **Gemma 4 Thinking Logs**: Toggle the terminal collapse dropdown below agent answers to inspect the AI's exact speculative logic paths.
*   **Clear Chat History**: Click the eraser icon in the drawer header to truncate the chat database at any time.

### 3. Mobile Synchronization
*   Open the profile menu and select **Connect Mobile Phone**.
*   Verify the host public IP bypass code, open the public URL on your mobile phone, and install the PWA directly to your home screen.
