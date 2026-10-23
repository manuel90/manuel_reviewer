# 🤖 AI GitHub PR Code Reviewer with Slack Confirmation Gate

An event-driven AI code review service built with **FastAPI**, **Slack Socket Mode**, and **Google Gemini** (`gemini-3.8-flash`).

When a Pull Request is opened, updated, assigned, or review-requested on GitHub, this service intercepts the event, verifies if you are assigned or requested as a reviewer, and dispatches an interactive confirmation prompt to your Slack channel with **Approve** and **Reject** buttons. Once approved, the AI analyzes the PR diff and posts line-by-line inline code review comments directly onto the GitHub Pull Request.

---

## 🏗️ Architectural Overview

```
                                  ┌───────────────────────────┐
                                  │ GitHub PR Event / CLI Sim │
                                  │ (Assigned/Review-Requested│
                                  └─────────────┬─────────────┘
                                                │
                                                ▼
                                    ┌───────────────────────┐
                                    │ FastAPI Webhook Server│
                                    │     (reviewer.py)     │
                                    └───────────┬───────────┘
                                                │
                            [ Is target user assigned/reviewer? ]
                                                │
                                       Yes ─────┴───── No ──> Ignore
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │ Slack Interactive Message     │
                        │ [ ✅ Approve ]   [ ❌ Reject ] │
                        └───────────────┬───────────────┘
                                        │
                  ┌─────────────────────┴─────────────────────┐
                  ▼                                           ▼
            [ ❌ Reject ]                               [ ✅ Approve ]
                  │                                           │
          Acknowledged in Slack               Fetch Diff from GitHub REST API
                                                              │
                                                              ▼
                                                   Google Gemini AI Analysis
                                                     (gemini-3.8-flash)
                                                              │
                                                              ▼
                                                   Post Inline Line Comments
                                                    to GitHub PR via REST API
```

---

## ✨ Features

- **Slack Email & Chat Listener**: Automatically detects GitHub PR links from forwarded GitHub notification emails (or team messages) in Slack, fetching PR metadata and replying directly in the thread with interactive Approve/Reject buttons.
- **Reviewer & Assignee Filtering**: Only triggers reviews when a specific GitHub username (`TARGET_ASSIGNEE`) is assigned to the PR or requested as a reviewer (`review_requested`).
- **Human-in-the-Loop Slack Gate**: Prevents wasteful or unwanted AI reviews by requiring approval via interactive Slack buttons.
- **Slack Socket Mode**: Uses WebSockets (`wss://`) for button interactions and real-time message listening. **No ngrok or public IP is required for local development.**
- **Curated Code Review Rules**:
  - Analyzes critical logic flaws breaking functionality.
  - Flags any debug logging statements (`console.log`, `print`, etc.).
  - Flags hardcoded raw numbers and strings, recommending enums or named constants.
  - Validates `React.memo` comparator functions to prevent stale data bugs.
  - Flags logic causing unnecessary React component re-renders.
- **Line-Targeted Comments**: Accurately posts review findings to newly added/modified lines on the right side of the diff (`side: "RIGHT"`).
- **PR Simulation CLI**: Built-in CLI command (`simulate_pr.sh`) to test the entire lifecycle using any real GitHub PR URL without waiting for live webhook events.

---

## 📋 Prerequisites

- **Python 3.11+**
- A **Google Gemini API Key** ([Google AI Studio](https://aistudio.google.com/))
- A **GitHub Personal Access Token** with `pull-requests:write` (or classic `repo` scope)
- A **Slack App** in your workspace with Socket Mode enabled

---

## ⚙️ Configuration (`.env`)

Create a `.env` file in the project root (see [.env.example](.env.example)):

```bash
# Target GitHub username to trigger reviews for
TARGET_ASSIGNEE="your_github_username"

# GitHub Personal Access Token (for fetching diffs and posting inline comments)
GITHUB_TOKEN="ghp_your_github_token_here"

# Google Gemini API Key
GEMINI_API_KEY="your_gemini_api_key_here"

# Optional: Override Gemini Model (Defaults to gemini-3.8-flash)
GEMINI_MODEL="gemini-3.8-flash"

# Slack Incoming Webhook URL (for dispatching review approval prompts)
SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T000/B000/XXXX"

# Slack App-Level Token (Starts with xapp-, required for Socket Mode)
SLACK_APP_TOKEN="xapp-1-..."

# Slack Bot User OAuth Token (Starts with xoxb-, required for Socket Mode)
SLACK_BOT_TOKEN="xoxb-..."
```

### How to Configure Slack Tokens:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and select your app.
2. **Enable Socket Mode**: Go to **Socket Mode** in the left sidebar and toggle **Enable Socket Mode** to **ON**.
3. **App-Level Token (`xapp-...`)**: Under **Basic Information** → **App-Level Tokens**, click **Generate Token and Scopes**, add the **`connections:write`** scope, and copy the token into `SLACK_APP_TOKEN`.
4. **Bot Token Scopes (`xoxb-...`)**: Under **OAuth & Permissions** → **Bot Token Scopes**, add:
   - `chat:write` (to post confirmation prompts and error alerts)
   - `channels:history` (to listen to public channel messages / forwarded emails)
   - `groups:history` (optional: if listening in private channels)
   Install or reinstall the app to your workspace. Copy the **Bot User OAuth Token** into `SLACK_BOT_TOKEN`.
5. **Event Subscriptions (for detecting forwarded GitHub emails)**:
   - Go to **Event Subscriptions** in the left sidebar and toggle **Enable Events** to **ON**.
   - Under **Subscribe to bot events**, add **`message.channels`** (and/or `message.groups`).
   - Click **Save Changes** and reinstall your app if prompted.
6. **Incoming Webhook**: Under **Incoming Webhooks**, toggle **ON**, click **Add New Webhook to Workspace**, select your channel, and copy the URL into `SLACK_WEBHOOK_URL`.
7. **Channel Invite**: Invite your bot into the Slack channel where your GitHub emails arrive:
   ```text
   /invite @YourBotName
   ```

---

## 🚀 Installation & Setup

1. **Clone and navigate to repository:**

   ```bash
   git clone <repository_url>
   cd <project_directory>
   ```

2. **Create and activate virtual environment:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## 🖥️ Running the Server

Start the server quickly using the included shell script:

```bash
./start_server.sh
```

> [!TIP]
> **Zero-Setup Bootstrapping:** If you run `./start_server.sh` immediately after cloning, it will automatically create the virtual environment (`.venv`), install all dependencies from `requirements.txt`, and initialize `.env` from `.env.example` if not already present.

Alternatively, you can run Uvicorn manually:

```bash
uvicorn reviewer:app --host 0.0.0.0 --port 8000 --reload
```

Upon startup, the server automatically connects to Slack Socket Mode:

```
INFO:     Started server process
INFO:     Waiting for application startup.
⚡ Slack Socket Mode connected! Listening for button clicks directly from Slack...
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
⚡️ Bolt app is running!
```

---

## 🕹️ Command Usage & Simulation

The project includes a command ([simulate_pr.sh](simulate_pr.sh) / [simulate_pr.py](simulate_pr.py)) to simulate PR review requests.

### 1. Simulate using a Real GitHub PR URL

Pass any public or private GitHub PR URL directly. The script queries the GitHub API for title, author, commit SHA, and diff URL:

```bash
./simulate_pr.sh https://github.com/your-org/your-repo/pull/123
```

_Dispatches the interactive approval prompt to Slack and waits for you to click Approve or Reject in Slack._

### 2. End-to-End Simulation with Auto-Approval

To test the complete workflow (Webhook → Approval → Gemini Review → Comment Posting) without waiting for a manual click in Slack:

```bash
./simulate_pr.sh https://github.com/your-org/your-repo/pull/123 --simulate-approval
```

### 3. Direct Slack Dispatch (Bypass Local Server)

Send the interactive approval prompt straight to Slack without needing the local FastAPI server up:

```bash
./simulate_pr.sh https://github.com/your-org/your-repo/pull/123 --direct
```

### 4. Custom Parameters

```bash
./simulate_pr.sh --repo "your-org/your-repo" --pr 42 --title "feat: Refactor authentication" --assignee "your_github_username"
```

### 5. Dry Run (Preview Payload)

```bash
./simulate_pr.sh --dry-run
```

### 6. Available CLI Flags

| Flag                  | Description                                                             | Default                         |
| --------------------- | ----------------------------------------------------------------------- | ------------------------------- |
| `pr_url`              | Full GitHub PR URL                                                      | Optional                        |
| `--repo`              | Repository name (`owner/repo`)                                          | `your-org/your-repo`            |
| `--pr`                | Pull request number                                                     | `101`                           |
| `--title`             | Pull request title                                                      | `feat: Add new feature`         |
| `--assignee`          | GitHub username                                                         | Value of `TARGET_ASSIGNEE`      |
| `--action`            | GitHub action (`opened`, `assigned`, `synchronize`, `review_requested`) | `opened`                        |
| `--server-url`        | Webhook URL                                                             | `http://localhost:8000/webhook` |
| `--direct`            | Send directly to Slack webhook                                          | `False`                         |
| `--simulate-approval` | Simulate clicking Approve button                                        | `False`                         |
| `--dry-run`           | Print payload without sending                                           | `False`                         |

---

## 🧪 Automated Tests

The test suite contains 26 comprehensive unit and integration tests covering all endpoints, Slack formatting, Gemini review, Slack email/message PR link detection, and simulation flows.

Run all tests:

```bash
pytest -v
```

### Test Coverage Highlights:

- **`test_webhook_ignores_non_pull_request_event`**: Verifies non-PR events (`push`, etc.) are ignored.
- **`test_webhook_ignores_unsupported_action`**: Verifies unhandled actions (`closed`, `labeled`) are ignored.
- **`test_webhook_ignores_when_target_assignee_not_assigned`**: Ensures PRs unassigned to `TARGET_ASSIGNEE` are ignored.
- **`test_webhook_triggers_slack_when_review_requested`**: Tests GitHub `action: "review_requested"`.
- **`test_webhook_triggers_slack_when_assigned_target_user`**: Tests `opened`, `assigned`, and `synchronize`.
- **`test_webhook_assignee_case_insensitivity`**: Ensures casing (`Username` vs `username`) matches accurately.
- **`test_send_slack_approval_request_payload_format`**: Validates Slack Block Kit structure & buttons.
- **`test_slack_interactive_reject`**: Tests rejection acknowledgment without calling Gemini.
- **`test_slack_interactive_approve_and_posts_comments`**: Mocks diff retrieval, Gemini 3.8 Flash, and GitHub comment posting.
- **`test_simulate_pr_*`**: Tests CLI payload generation, server mode, direct mode, and offline fallback.

---

## 🌐 Production Webhook Setup (Live GitHub Events)

When deploying to a public server (Google Cloud Run, AWS Lambda with Mangum, Render, Railway, or VPS):

1. Go to your **GitHub Repository** → **Settings** → **Webhooks** → **Add Webhook**.
2. **Payload URL**: `https://<your-server-domain>/webhook`
3. **Content type**: `application/json`
4. **Events**: Select **Let me select individual events**, check **Pull requests**, and save.

When a team member requests a review from you or assigns you to a PR on GitHub, GitHub will immediately notify your server, which will ping your Slack channel for approval!
