import os
import json
import time
import threading
import requests
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, Header
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_socket_mode_listener()
    yield

app = FastAPI(lifespan=lifespan)

# Configuration from Environment Variables
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_ASSIGNEE = os.getenv("TARGET_ASSIGNEE")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# Gemini Client initialization
gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Warning: Failed to initialize Gemini client on startup: {e}")

def get_gemini_client():
    """Returns initialized Gemini client or initializes a new one from environment."""
    global gemini_client
    if gemini_client is not None:
        return gemini_client
    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if api_key:
        gemini_client = genai.Client(api_key=api_key)
        return gemini_client
    return None

class ReviewComment(BaseModel):
    path: str = Field(description="The relative file path being reviewed.")
    line: int = Field(description="The line number in the NEW version of the file.")
    body: str = Field(description="Actionable, clear code review comment.")

class CodeReviewResponse(BaseModel):
    comments: list[ReviewComment]

def send_slack_message(text: str) -> bool:
    """Sends a text message to Slack using SLACK_WEBHOOK_URL."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL") or SLACK_WEBHOOK_URL
    if not webhook_url:
        print("❌ Failed to send Slack message: SLACK_WEBHOOK_URL is not set.")
        return False
    try:
        res = requests.post(webhook_url, json={"text": text}, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"⚠️ Exception sending Slack text message: {e}")
        return False

def send_slack_approval_request(repo: str, pr_number: int, pr_title: str, diff_url: str, commit_sha: str) -> bool:
    """Sends an interactive Slack message with Approve/Reject buttons."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL") or SLACK_WEBHOOK_URL
    target_user = os.getenv("TARGET_ASSIGNEE") or TARGET_ASSIGNEE or "Unassigned"

    payload = {
        "text": f"🚨 *AI PR Review Approval Requested for PR #{pr_number}*",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Repository:* {repo}\n*PR #{pr_number}:* {pr_title}\n*Assigned to:* {target_user}"
                }
            },
            {
                "type": "actions",
                "block_id": "review_approval_block",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "approve_ai_review",
                        "text": {"type": "plain_text", "text": "✅ Approve AI Review"},
                        "style": "primary",
                        "value": json.dumps({
                            "action": "approve",
                            "repo": repo,
                            "pr_number": pr_number,
                            "diff_url": diff_url,
                            "commit_sha": commit_sha
                        })
                    },
                    {
                        "type": "button",
                        "action_id": "reject_ai_review",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "value": json.dumps({"action": "reject", "pr_number": pr_number})
                    }
                ]
            }
        ]
    }

    if not webhook_url:
        print("❌ Failed to send Slack notification: SLACK_WEBHOOK_URL is not set.")
        return False

    try:
        res = requests.post(webhook_url, json=payload, timeout=10)
        if res.status_code == 200:
            print(f"✅ Slack approval message sent successfully for PR #{pr_number}!")
            return True
        else:
            print(f"❌ Failed to send Slack notification: {res.status_code} - {res.text}")
            return False
    except Exception as e:
        print(f"❌ Exception sending Slack notification: {e}")
        return False

def execute_review_action(action_value: dict) -> str:
    """Core logic to process approval or rejection of PR review."""
    action = action_value.get("action")
    pr_number = action_value.get("pr_number")
    repo = action_value.get("repo", "unknown/repo")

    if action == "reject":
        msg = f"❌ AI Review for PR #{pr_number} was rejected by user in Slack."
        print(msg)
        return f"❌ AI Review for PR #{pr_number} was rejected."

    diff_url = action_value.get("diff_url")
    commit_sha = action_value.get("commit_sha")

    github_token = os.getenv("GITHUB_TOKEN") or GITHUB_TOKEN
    diff_headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3.diff"
    }
    fetch_url = diff_url
    if repo and pr_number and "api.github.com" not in str(diff_url):
        fetch_url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"

    print(f"\n📥 Received approval for PR #{pr_number} in {repo}")
    print(f"📥 Fetching diff from: {fetch_url}")

    # 1. Fetch Diff
    try:
        diff_res = requests.get(fetch_url, headers=diff_headers, timeout=15)
        if diff_res.status_code != 200:
            err_msg = (
                f"🚨 *AI Review Failed (PR #{pr_number})*\n"
                f"Failed to fetch diff from GitHub API (HTTP {diff_res.status_code}):\n"
                f"> `{diff_res.text[:200]}`"
            )
            print(f"❌ {err_msg}")
            send_slack_message(err_msg)
            return err_msg
        diff_text = diff_res.text
        print(f"📄 Diff fetched ({len(diff_text)} characters)")
    except Exception as e:
        err_msg = (
            f"🚨 *AI Review Network Error (PR #{pr_number})*\n"
            f"Failed to reach GitHub API: `{str(e)}`"
        )
        print(f"❌ {err_msg}")
        send_slack_message(err_msg)
        return err_msg

    if not diff_text.strip():
        msg = f"ℹ️ *PR #{pr_number}:* Diff is empty. No review comments needed."
        print(msg)
        send_slack_message(msg)
        return msg

    prompt = f"""
    Analyze key code issues or critical logic flaws that could break project functionality.

    Ensure the code is free of the following:
    - Any debug logging statements
    - Hardcoding raw numbers or strings (everywhere) inside logic. Replace primitive values with named constants or enums to improve code readability, prevent typos, and create a single source of truth.
    - When using React.memo, never assume an identifier field like name or id guarantees data equality; always ensure you compare every prop and nested property that affects the component's rendered output to prevent stale data bugs.
    If it's a reactjs project ensuer the code is free of the following:
    - Logic causing unnecessary re-renders in React components

    Git Diff:
    {diff_text[:60000]}
    """

    client = get_gemini_client()
    if not client:
        err_msg = f"🚨 *AI Review Configuration Error (PR #{pr_number}):* `GEMINI_API_KEY` is missing."
        print(f"❌ {err_msg}")
        send_slack_message(err_msg)
        return err_msg

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    print(f"🤖 Analyzing diff with Gemini ({model_name})...")

    # 2. Call Gemini AI with automatic retry on 503 high demand spikes
    response = None
    last_err = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CodeReviewResponse,
                    temperature=0.2,
                ),
            )
            break
        except Exception as e:
            last_err = e
            err_str = str(e)
            if ("503" in err_str or "UNAVAILABLE" in err_str) and attempt == 0:
                print(f"⚠️ Gemini 503 high demand spike detected on attempt 1. Retrying in 2 seconds...")
                time.sleep(2)
                continue
            break

    if not response:
        err_str = str(last_err)
        if "503" in err_str or "UNAVAILABLE" in err_str:
            err_msg = (
                f"🚨 *AI Review Service Unavailable (PR #{pr_number})*\n"
                f"Google Gemini (`{model_name}`) is currently experiencing high demand:\n"
                f"> `503 UNAVAILABLE - Model is experiencing high demand spikes.`\n"
                f"💡 *Action:* Spikes in demand are temporary. Please wait 10-30 seconds and click *'✅ Approve AI Review'* again in Slack."
            )
        elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            err_msg = (
                f"🚨 *AI Review Rate Limited (PR #{pr_number})*\n"
                f"Google Gemini API rate limit or quota exceeded (429 RESOURCE_EXHAUSTED).\n"
                f"💡 *Action:* Please wait a moment before trying again."
            )
        else:
            err_msg = (
                f"🚨 *AI Review Gemini Error (PR #{pr_number})*\n"
                f"Google Gemini returned an error:\n"
                f"> `{err_str[:300]}`"
            )

        print(f"❌ {err_msg}")
        send_slack_message(err_msg)
        return err_msg

    try:
        comments = json.loads(response.text).get("comments", [])
    except Exception as e:
        err_msg = (
            f"🚨 *AI Review Parsing Error (PR #{pr_number})*\n"
            f"Failed to parse Gemini response JSON: `{str(e)}`"
        )
        print(f"❌ {err_msg}")
        send_slack_message(err_msg)
        return err_msg

    print(f"🤖 Gemini identified {len(comments)} review comment(s).")

    # 3. Post comments back to GitHub PR
    comment_headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    posted_count = 0
    post_errors = []
    for comment in comments:
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
        payload_data = {
            "body": f"🤖 **AI Review:**\n\n{comment['body']}",
            "commit_id": commit_sha,
            "path": comment["path"],
            "line": comment["line"],
            "side": "RIGHT"
        }
        try:
            res = requests.post(url, headers=comment_headers, json=payload_data, timeout=10)
            if res.status_code == 201:
                posted_count += 1
                print(f"✅ Posted comment on {comment['path']}:{comment['line']}")
            else:
                err_line = f"{comment['path']}:{comment['line']} ({res.status_code})"
                post_errors.append(err_line)
                print(f"⚠️ Failed to post comment on {err_line}: {res.text}")
        except Exception as e:
            err_line = f"{comment['path']}:{comment['line']} ({str(e)})"
            post_errors.append(err_line)
            print(f"⚠️ Exception posting comment on {err_line}")

    if post_errors and posted_count == 0 and len(comments) > 0:
        err_msg = (
            f"⚠️ *AI Review Warning (PR #{pr_number})*\n"
            f"Gemini identified {len(comments)} comment(s), but GitHub API rejected them:\n"
            f"> `{', '.join(post_errors[:3])}`"
        )
        send_slack_message(err_msg)
        return err_msg

    result_msg = f"✅ AI Review executed successfully for PR #{pr_number}. Posted {posted_count} comments."
    print(result_msg)
    return result_msg

@app.post("/webhook")
async def github_webhook(request: Request, x_github_event: str = Header(None)):
    """Receives GitHub events and asks for user confirmation via Slack."""
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"Event '{x_github_event}' is not pull_request"}

    payload = await request.json()
    action = payload.get("action")

    # Allowed actions: PR opened, updated, assigned, or review requested
    if action not in ["assigned", "opened", "synchronize", "review_requested"]:
        return {"status": "ignored", "reason": f"Action '{action}' is not handled"}

    pr = payload.get("pull_request", {})
    assignees = [a.get("login", "").lower() for a in pr.get("assignees", [])]
    requested_reviewers = [r.get("login", "").lower() for r in pr.get("requested_reviewers", [])]
    single_reviewer = payload.get("requested_reviewer", {}).get("login", "").lower()

    target_user = (os.getenv("TARGET_ASSIGNEE") or TARGET_ASSIGNEE or "").lower()

    is_assigned = target_user in assignees
    is_requested_reviewer = target_user in requested_reviewers or target_user == single_reviewer

    if not (is_assigned or is_requested_reviewer):
        return {
            "status": "ignored",
            "reason": f"PR is neither assigned to nor requesting review from '{target_user}'."
        }

    # Send approval request to Slack instead of running Gemini immediately
    repo_full_name = payload.get("repository", {}).get("full_name", "unknown/repo")
    pr_number = pr.get("number", 0)
    pr_title = pr.get("title", f"PR #{pr_number}")
    diff_url = pr.get("diff_url", f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}")
    commit_sha = pr.get("head", {}).get("sha", "HEAD")

    slack_delivered = send_slack_approval_request(
        repo=repo_full_name,
        pr_number=pr_number,
        pr_title=pr_title,
        diff_url=diff_url,
        commit_sha=commit_sha
    )

    return {
        "status": "pending_approval",
        "message": "Confirmation prompt sent to Slack",
        "slack_delivered": slack_delivered
    }

@app.post("/slack/interactive")
async def slack_interactive(payload: str = Form(...)):
    """Handles button clicks via HTTP POST."""
    data = json.loads(payload)
    actions = data.get("actions", [])
    if not actions:
        return {"text": "⚠️ No action found in payload."}

    action_value = json.loads(actions[0].get("value", "{}"))
    result = execute_review_action(action_value)
    return {"text": result}

# =========================================================================
# Slack Socket Mode Handler (for direct Slack interaction without ngrok)
# =========================================================================

bolt_app = None
socket_mode_handler = None

def init_slack_bolt():
    global bolt_app
    bot_token = os.getenv("SLACK_BOT_TOKEN") or SLACK_BOT_TOKEN
    if not bot_token:
        return None

    try:
        from slack_bolt import App as BoltApp
        bolt_app = BoltApp(token=bot_token)

        @bolt_app.action("approve_ai_review")
        @bolt_app.action("reject_ai_review")
        @bolt_app.action({"block_id": "review_approval_block"})
        def handle_bolt_action(ack, body, respond):
            ack()
            actions = body.get("actions", [])
            if not actions:
                return
            try:
                action_value = json.loads(actions[0].get("value", "{}"))
                if action_value.get("action") == "approve":
                    respond("⏳ *Starting AI Code Review with Gemini...*", replace_original=False)
                result = execute_review_action(action_value)
                respond(result, replace_original=False)
            except Exception as e:
                err_msg = f"🚨 *Unexpected error during PR review:* `{str(e)}`"
                print(f"❌ {err_msg}")
                send_slack_message(err_msg)
                try:
                    respond(err_msg, replace_original=False)
                except Exception:
                    pass

        return bolt_app
    except Exception as e:
        print(f"⚠️ Could not initialize Slack Bolt App: {e}")
        return None

def start_socket_mode_listener():
    global socket_mode_handler
    app_token = os.getenv("SLACK_APP_TOKEN") or SLACK_APP_TOKEN
    b_app = bolt_app or init_slack_bolt()
    if b_app and app_token:
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler
            socket_mode_handler = SocketModeHandler(b_app, app_token)
            t = threading.Thread(target=socket_mode_handler.start, daemon=True)
            t.start()
            print("⚡ Slack Socket Mode connected! Listening for button clicks directly from Slack...")
        except Exception as e:
            print(f"⚠️ Could not start Slack SocketModeHandler: {e}")
