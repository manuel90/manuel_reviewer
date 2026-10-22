#!/usr/bin/env python3
"""
Simulate a GitHub Pull Request Review Request.

This script simulates GitHub sending a webhook event for an opened/assigned PR.
By default, it sends an HTTP POST request to your FastAPI reviewer server
(http://localhost:8000/webhook), which verifies the assignee and posts the
interactive approval prompt with buttons to Slack.

If the FastAPI server is not currently running locally, it gracefully falls back
to sending the Slack interactive approval message directly using SLACK_WEBHOOK_URL.

You can also pass a full GitHub PR URL (e.g., https://github.com/your-org/your-repo/pull/123),
and the script will automatically query the GitHub API to fetch real PR metadata (title, commit SHA, diff).

Usage:
    python3 simulate_pr.py https://github.com/your-org/your-repo/pull/123
    python3 simulate_pr.py --pr 101 --repo your-org/your-repo
    python3 simulate_pr.py --direct
    python3 simulate_pr.py --simulate-approval
"""

import os
import sys
import re
import json
import argparse
import requests
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

try:
    from reviewer import send_slack_approval_request
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from reviewer import send_slack_approval_request


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Simulate a GitHub Pull Request review request and send confirmation to Slack.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "pr_url_arg",
        nargs="?",
        default=None,
        help="Optional GitHub PR URL (e.g. https://github.com/your-org/your-repo/pull/123)"
    )
    parser.add_argument(
        "--pr-url",
        default=None,
        help="Full GitHub PR URL (e.g. https://github.com/your-org/your-repo/pull/123)"
    )
    parser.add_argument(
        "--repo",
        default=os.getenv("GITHUB_REPOSITORY", "your-org/your-repo"),
        help="Repository full name (owner/repo)"
    )
    parser.add_argument(
        "--pr",
        type=int,
        default=101,
        help="Simulated Pull Request number"
    )
    parser.add_argument(
        "--title",
        default="feat: Add automated AI code reviewer service",
        help="Simulated Pull Request title"
    )
    parser.add_argument(
        "--assignee",
        default=os.getenv("TARGET_ASSIGNEE", "your_github_username"),
        help="GitHub username assigned to this PR (must match TARGET_ASSIGNEE)"
    )
    parser.add_argument(
        "--action",
        choices=["opened", "assigned", "synchronize", "review_requested"],
        default="opened",
        help="Simulated GitHub PR event action"
    )
    parser.add_argument(
        "--diff-url",
        default=None,
        help="Custom diff URL (defaults to GitHub API PR diff URL)"
    )
    parser.add_argument(
        "--commit-sha",
        default="c0ffee9876543210fedcba0123456789abcdef01",
        help="Simulated commit SHA"
    )
    parser.add_argument(
        "--server-url",
        default=os.getenv("WEBHOOK_SERVER_URL", "http://localhost:8000/webhook"),
        help="URL of the local FastAPI webhook endpoint"
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Send the Slack notification directly, bypassing the local FastAPI server"
    )
    parser.add_argument(
        "--simulate-approval",
        action="store_true",
        help="After sending confirmation, also simulate clicking 'Approve AI Review' to test Gemini review"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the payload without sending anything"
    )

    return parser.parse_args()


def fetch_live_github_pr_info(repo: str, pr_number: int, github_token: str):
    """Fetch actual PR metadata from GitHub API if token is provided."""
    headers = {
        "Accept": "application/vnd.github.v3+json"
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    api_url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"ℹ️ Note: GitHub API responded with status {r.status_code} when fetching live PR info.")
            return None
    except Exception as e:
        print(f"ℹ️ Note: Could not fetch live GitHub PR info ({e}). Using provided/default values.")
        return None


def build_github_payload(repo, pr_number, title, diff_url, commit_sha, assignee, action):
    return {
        "action": action,
        "requested_reviewer": {
            "login": assignee
        },
        "repository": {
            "full_name": repo,
            "name": repo.split("/")[-1] if "/" in repo else repo,
        },
        "pull_request": {
            "number": pr_number,
            "title": title,
            "diff_url": diff_url,
            "head": {
                "sha": commit_sha
            },
            "assignees": [
                {
                    "login": assignee
                }
            ],
            "requested_reviewers": [
                {
                    "login": assignee
                }
            ]
        }
    }


def main():
    args = parse_arguments()
    github_token = os.getenv("GITHUB_TOKEN")
    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")

    # Check if a PR URL was provided either as positional arg or flag
    pr_url = args.pr_url or args.pr_url_arg
    repo = args.repo
    pr_number = args.pr

    if pr_url:
        match = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_url)
        if match:
            repo = match.group(1)
            pr_number = int(match.group(2))
            print(f"🔍 Extracted from URL -> Repo: {repo}, PR: #{pr_number}")
        else:
            print(f"⚠️ Could not parse PR URL: {pr_url}. Using --repo {repo} and --pr {pr_number}")

    # Fetch live metadata from GitHub API if possible
    live_pr = fetch_live_github_pr_info(repo, pr_number, github_token)
    title = args.title
    commit_sha = args.commit_sha
    diff_url = args.diff_url or f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    assignee = args.assignee

    if live_pr:
        title = live_pr.get("title", title)
        commit_sha = live_pr.get("head", {}).get("sha", commit_sha)
        diff_url = live_pr.get("diff_url", diff_url)
        # If PR already has assignees on GitHub, display them or default to TARGET_ASSIGNEE
        pr_assignees = [a.get("login") for a in live_pr.get("assignees", [])]
        if pr_assignees and assignee not in pr_assignees:
            # Add target assignee for simulation
            print(f"ℹ️ GitHub assignees: {pr_assignees}. Adding target assignee '{assignee}' for review simulation.")

    print("\n" + "=" * 65)
    print("🤖 AI PR Code Reviewer — PR Simulation Demo")
    print("=" * 65)
    print(f"📦 Repository:    {repo}")
    print(f"🔀 PR Number:     #{pr_number}")
    print(f"📝 PR Title:      {title}")
    print(f"👤 PR Assignee:   {assignee}")
    print(f"⚡ GitHub Action: {args.action}")
    print(f"🔗 Diff URL:      {diff_url}")
    print(f"🔑 Commit SHA:    {commit_sha}")
    print("=" * 65)

    payload = build_github_payload(repo, pr_number, title, diff_url, commit_sha, assignee, args.action)

    if args.dry_run:
        print("\n🔍 [DRY RUN] Generated GitHub Webhook Payload:")
        print(json.dumps(payload, indent=2))
        return 0

    if args.direct:
        print("\n🚀 [Direct Mode] Dispatching interactive approval message to Slack...")
        if not slack_webhook_url:
            print("❌ Error: SLACK_WEBHOOK_URL environment variable is not set.")
            return 1

        success = send_slack_approval_request(
            repo=repo,
            pr_number=pr_number,
            pr_title=title,
            diff_url=diff_url,
            commit_sha=commit_sha
        )
        if success:
            print("\n🎉 Success! Interactive confirmation prompt delivered to Slack.")
            return 0
        else:
            print("\n❌ Failed to deliver message to Slack. Check your SLACK_WEBHOOK_URL.")
            return 1

    # Server mode (Default)
    print(f"\n🌐 [Server Mode] Sending simulated webhook to: {args.server_url}")
    headers = {
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(args.server_url, json=payload, headers=headers, timeout=10)
        print(f"📡 Server HTTP Status: {response.status_code}")
        try:
            resp_json = response.json()
            print("📄 Server Response:   ", json.dumps(resp_json, indent=2))

            if resp_json.get("status") == "pending_approval":
                if resp_json.get("slack_delivered"):
                    print("\n🎉 Success! Server processed PR and dispatched interactive confirmation prompt to Slack.")
                else:
                    print("\n⚠️ Server processed PR event, but Slack delivery failed. Verify SLACK_WEBHOOK_URL.")
            elif resp_json.get("status") == "ignored":
                print(f"\n⚠️ Webhook event was ignored by server: {resp_json.get('reason')}")
        except Exception:
            print("📄 Server Raw Response:", response.text)

    except requests.exceptions.ConnectionError:
        print(f"\n⚠️ Could not connect to local server at {args.server_url} (Connection refused).")
        print("💡 The FastAPI server is not currently running.")
        print("   You can start it in a separate terminal with:")
        print("   python3 -m uvicorn reviewer:app --host 0.0.0.0 --port 8000")
        print("\n🔄 Automatically falling back to direct Slack simulation...")

        if not slack_webhook_url:
            print("❌ Error: SLACK_WEBHOOK_URL is not set. Cannot dispatch to Slack.")
            return 1

        success = send_slack_approval_request(
            repo=repo,
            pr_number=pr_number,
            pr_title=title,
            diff_url=diff_url,
            commit_sha=commit_sha
        )
        if success:
            print("\n🎉 Success! Interactive confirmation prompt delivered to Slack via direct fallback.")
        else:
            print("\n❌ Failed to deliver message to Slack. Check your SLACK_WEBHOOK_URL.")
            return 1

    except Exception as e:
        print(f"\n❌ Unexpected error while sending webhook: {e}")
        return 1

    # Optional: Simulate user clicking "Approve AI Review" on Slack
    if args.simulate_approval:
        print("\n" + "-" * 65)
        print("🤖 [Demo Step 2] Simulating user clicking '✅ Approve AI Review' in Slack...")
        print("-" * 65)
        interactive_url = args.server_url.replace("/webhook", "/slack/interactive")
        action_data = {
            "action": "approve",
            "repo": repo,
            "pr_number": pr_number,
            "diff_url": diff_url,
            "commit_sha": commit_sha
        }
        slack_click_payload = {
            "actions": [
                {
                    "value": json.dumps(action_data)
                }
            ]
        }
        try:
            r = requests.post(interactive_url, data={"payload": json.dumps(slack_click_payload)}, timeout=30)
            print(f"📡 Interactive Endpoint Status: {r.status_code}")
            print(f"📄 Response: {r.text}")
        except Exception as e:
            print(f"⚠️ Could not invoke interactive endpoint ({e}). Start server to test interactive approval.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
