import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import reviewer
from reviewer import app, send_slack_approval_request, send_slack_message

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    """Set default test environment variables for all tests."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake_github_token")
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.setenv("TARGET_ASSIGNEE", "test_user")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/test/test/test")

# ==========================================
# Tests for GitHub Webhook Endpoint (/webhook)
# ==========================================

def test_webhook_ignores_non_pull_request_event():
    """Ensure events other than pull_request are ignored."""
    response = client.post(
        "/webhook",
        headers={"x-github-event": "push"},
        json={"ref": "refs/heads/main"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ignored"
    assert "not pull_request" in data.get("reason", "")


def test_webhook_ignores_missing_event_header():
    """Ensure requests without x-github-event header are ignored."""
    response = client.post(
        "/webhook",
        json={"action": "opened"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_ignores_unsupported_action():
    """Ensure unsupported PR actions like 'closed' or 'labeled' are ignored."""
    payload = {
        "action": "closed",
        "pull_request": {"number": 1, "assignees": [{"login": "test_user"}]}
    }
    response = client.post(
        "/webhook",
        headers={"x-github-event": "pull_request"},
        json=payload
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_ignores_when_target_assignee_not_assigned():
    """Ensure PRs not assigned to the target user are ignored."""
    payload = {
        "action": "opened",
        "repository": {"full_name": "owner/repo"},
        "pull_request": {
            "number": 10,
            "title": "Unassigned PR",
            "assignees": [{"login": "other_dev"}]
        }
    }
    response = client.post(
        "/webhook",
        headers={"x-github-event": "pull_request"},
        json=payload
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ignored"
    assert "neither assigned to nor requesting review" in data.get("reason", "")


def test_webhook_triggers_slack_when_review_requested():
    """Ensure PR with action 'review_requested' targeting user triggers Slack notification."""
    payload = {
        "action": "review_requested",
        "requested_reviewer": {"login": "test_user"},
        "repository": {"full_name": "test-org/test-repo"},
        "pull_request": {
            "number": 105,
            "title": "Add review requested feature",
            "diff_url": "https://api.github.com/repos/test-org/test-repo/pulls/105",
            "head": {"sha": "sha_rev_req"},
            "assignees": [],
            "requested_reviewers": [{"login": "test_user"}]
        }
    }

    with patch("reviewer.send_slack_approval_request", return_value=True) as mock_send:
        response = client.post(
            "/webhook",
            headers={"x-github-event": "pull_request"},
            json=payload
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pending_approval"
        assert mock_send.called


@pytest.mark.parametrize("action", ["assigned", "opened", "synchronize"])
def test_webhook_triggers_slack_when_assigned_target_user(action):
    """Ensure PR opened, assigned, or synchronized with target user triggers Slack notification."""
    payload = {
        "action": action,
        "repository": {"full_name": "test-org/test-repo"},
        "pull_request": {
            "number": 101,
            "title": "Add Awesome Feature",
            "diff_url": "https://api.github.com/repos/test-org/test-repo/pulls/101",
            "head": {"sha": "c0ffee12345"},
            "assignees": [{"login": "test_user"}]
        }
    }

    with patch("reviewer.send_slack_approval_request", return_value=True) as mock_send:
        response = client.post(
            "/webhook",
            headers={"x-github-event": "pull_request"},
            json=payload
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending_approval"
        assert data["slack_delivered"] is True

        mock_send.assert_called_once_with(
            repo="test-org/test-repo",
            pr_number=101,
            pr_title="Add Awesome Feature",
            diff_url="https://api.github.com/repos/test-org/test-repo/pulls/101",
            commit_sha="c0ffee12345"
        )


def test_webhook_assignee_case_insensitivity():
    """Ensure assignee matching is case-insensitive (e.g. Test_User matches test_user)."""
    payload = {
        "action": "opened",
        "repository": {"full_name": "test-org/test-repo"},
        "pull_request": {
            "number": 102,
            "title": "Fix bug",
            "diff_url": "https://api.github.com/repos/test-org/test-repo/pulls/102",
            "head": {"sha": "abcdef123"},
            "assignees": [{"login": "Test_User"}]
        }
    }

    with patch("reviewer.send_slack_approval_request", return_value=True) as mock_send:
        response = client.post(
            "/webhook",
            headers={"x-github-event": "pull_request"},
            json=payload
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pending_approval"
        assert mock_send.called


# ==========================================
# Tests for send_slack_approval_request
# ==========================================

def test_send_slack_approval_request_payload_format():
    """Verify that Slack payload contains the proper interactive blocks and metadata."""
    repo = "test-org/test-repo"
    pr_number = 42
    pr_title = "Support dark mode"
    diff_url = "https://api.github.com/repos/test-org/test-repo/pulls/42"
    commit_sha = "sha_abc"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "ok"

    with patch("requests.post", return_value=mock_resp) as mock_post:
        success = send_slack_approval_request(repo, pr_number, pr_title, diff_url, commit_sha)
        assert success is True

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://hooks.slack.com/services/test/test/test"
        
        payload = kwargs["json"]
        assert "blocks" in payload
        blocks = payload["blocks"]
        assert len(blocks) == 2

        # Block 1: section
        assert blocks[0]["type"] == "section"
        assert repo in blocks[0]["text"]["text"]
        assert str(pr_number) in blocks[0]["text"]["text"]
        assert pr_title in blocks[0]["text"]["text"]
        assert "test_user" in blocks[0]["text"]["text"]

        # Block 2: action buttons
        assert blocks[1]["type"] == "actions"
        elements = blocks[1]["elements"]
        assert len(elements) == 2

        # Approve button
        approve_btn = elements[0]
        assert approve_btn["type"] == "button"
        assert approve_btn["style"] == "primary"
        approve_data = json.loads(approve_btn["value"])
        assert approve_data["action"] == "approve"
        assert approve_data["repo"] == repo
        assert approve_data["pr_number"] == pr_number
        assert approve_data["diff_url"] == diff_url
        assert approve_data["commit_sha"] == commit_sha

        # Reject button
        reject_btn = elements[1]
        assert reject_btn["type"] == "button"
        assert reject_btn["style"] == "danger"
        reject_data = json.loads(reject_btn["value"])
        assert reject_data["action"] == "reject"
        assert reject_data["pr_number"] == pr_number


def test_send_slack_approval_request_slack_error():
    """Ensure send_slack_approval_request returns False when Slack returns an HTTP error."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "channel_not_found"

    with patch("requests.post", return_value=mock_resp):
        success = send_slack_approval_request("repo", 1, "title", "diff", "sha")
        assert success is False


def test_send_slack_approval_request_missing_webhook_url(monkeypatch):
    """Ensure missing SLACK_WEBHOOK_URL returns False without crashing."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    with patch("reviewer.SLACK_WEBHOOK_URL", ""):
        success = send_slack_approval_request("repo", 1, "title", "diff", "sha")
        assert success is False


# ==========================================
# Tests for Slack Interactive Endpoint (/slack/interactive)
# ==========================================

def test_slack_interactive_reject():
    """Ensure rejecting from Slack skips Gemini and returns a rejection acknowledgment."""
    action_payload = {
        "actions": [
            {
                "value": json.dumps({"action": "reject", "pr_number": 55})
            }
        ]
    }

    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        response = client.post(
            "/slack/interactive",
            data={"payload": json.dumps(action_payload)}
        )
        assert response.status_code == 200
        data = response.json()
        assert "was rejected" in data.get("text", "")
        assert "55" in data.get("text", "")

        mock_get.assert_not_called()
        mock_post.assert_not_called()


def test_slack_interactive_approve_and_posts_comments():
    """Ensure approving from Slack fetches diff, invokes Gemini, and posts inline review comments."""
    approve_data = {
        "action": "approve",
        "repo": "test-org/test-repo",
        "pr_number": 77,
        "diff_url": "https://api.github.com/repos/test-org/test-repo/pulls/77",
        "commit_sha": "commit_sha_xyz"
    }
    action_payload = {
        "actions": [
            {"value": json.dumps(approve_data)}
        ]
    }

    fake_diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@
+TIMEOUT = 3600
"""
    mock_diff_resp = MagicMock()
    mock_diff_resp.status_code = 200
    mock_diff_resp.text = fake_diff

    # Mock Gemini response
    mock_gemini_client = MagicMock()
    mock_gemini_resp = MagicMock()
    mock_gemini_resp.text = json.dumps({
        "comments": [
            {
                "path": "app.py",
                "line": 10,
                "body": "Avoid raw numbers inside logic. Consider moving TIMEOUT to an environment setting or config."
            }
        ]
    })
    mock_gemini_client.models.generate_content.return_value = mock_gemini_resp

    mock_post_comment_resp = MagicMock()
    mock_post_comment_resp.status_code = 201
    mock_post_comment_resp.json.return_value = {"id": 1234}

    with patch("requests.get", return_value=mock_diff_resp) as mock_get, \
         patch("reviewer.get_gemini_client", return_value=mock_gemini_client), \
         patch("requests.post", return_value=mock_post_comment_resp) as mock_post:

        response = client.post(
            "/slack/interactive",
            data={"payload": json.dumps(action_payload)}
        )

        assert response.status_code == 200
        assert "AI Review executed successfully for PR #77" in response.json()["text"]
        assert "Posted 1 comments" in response.json()["text"]

        # Verify diff fetch headers
        mock_get.assert_called_once_with(
            approve_data["diff_url"],
            headers={"Authorization": "Bearer fake_github_token", "Accept": "application/vnd.github.v3.diff"},
            timeout=15
        )

        # Verify comment post
        mock_post.assert_called_once()
        post_url, post_kwargs = mock_post.call_args
        assert post_url[0] == "https://api.github.com/repos/test-org/test-repo/pulls/77/comments"
        assert post_kwargs["json"]["commit_id"] == "commit_sha_xyz"
        assert post_kwargs["json"]["path"] == "app.py"
        assert post_kwargs["json"]["line"] == 10
        assert post_kwargs["json"]["side"] == "RIGHT"
        assert "Avoid raw numbers" in post_kwargs["json"]["body"]


def test_slack_interactive_approve_with_zero_comments():
    """Ensure approving a clean diff with no issues posts 0 comments cleanly."""
    approve_data = {
        "action": "approve",
        "repo": "test-org/test-repo",
        "pr_number": 88,
        "diff_url": "https://api.github.com/repos/test-org/test-repo/pulls/88",
        "commit_sha": "commit_clean"
    }
    action_payload = {
        "actions": [{"value": json.dumps(approve_data)}]
    }

    mock_diff_resp = MagicMock()
    mock_diff_resp.status_code = 200
    mock_diff_resp.text = "+# Clean code addition"

    mock_gemini_client = MagicMock()
    mock_gemini_resp = MagicMock()
    mock_gemini_resp.text = json.dumps({"comments": []})
    mock_gemini_client.models.generate_content.return_value = mock_gemini_resp

    with patch("requests.get", return_value=mock_diff_resp), \
         patch("reviewer.get_gemini_client", return_value=mock_gemini_client), \
         patch("requests.post") as mock_post:

        response = client.post(
            "/slack/interactive",
            data={"payload": json.dumps(action_payload)}
        )
        assert response.status_code == 200
        assert "Posted 0 comments" in response.json()["text"]
        mock_post.assert_not_called()


def test_slack_interactive_gemini_503_error_sends_to_slack():
    """Ensure Gemini 503 high demand errors are caught and dispatched to Slack."""
    approve_data = {
        "action": "approve",
        "repo": "test-org/test-repo",
        "pr_number": 99,
        "diff_url": "https://api.github.com/repos/test-org/test-repo/pulls/99",
        "commit_sha": "sha_503"
    }
    action_payload = {
        "actions": [{"value": json.dumps(approve_data)}]
    }

    mock_diff_resp = MagicMock()
    mock_diff_resp.status_code = 200
    mock_diff_resp.text = "+# Some change"

    mock_gemini_client = MagicMock()
    mock_gemini_client.models.generate_content.side_effect = Exception(
        "503 UNAVAILABLE: Model is temporarily experiencing high demand."
    )

    with patch("requests.get", return_value=mock_diff_resp), \
         patch("reviewer.get_gemini_client", return_value=mock_gemini_client), \
         patch("reviewer.send_slack_message") as mock_slack_send, \
         patch("time.sleep"):  # skip sleep in tests

        response = client.post(
            "/slack/interactive",
            data={"payload": json.dumps(action_payload)}
        )

        assert response.status_code == 200
        resp_text = response.json()["text"]
        assert "503 UNAVAILABLE" in resp_text
        assert "high demand" in resp_text

        # Verify message was sent to Slack
        mock_slack_send.assert_called_once()
        slack_msg = mock_slack_send.call_args[0][0]
        assert "PR #99" in slack_msg
        assert "503 UNAVAILABLE" in slack_msg


def test_slack_interactive_github_diff_error_sends_to_slack():
    """Ensure GitHub diff fetch failures send an error notification to Slack."""
    approve_data = {
        "action": "approve",
        "repo": "test-org/test-repo",
        "pr_number": 100,
        "diff_url": "https://api.github.com/repos/test-org/test-repo/pulls/100",
        "commit_sha": "sha_404"
    }
    action_payload = {
        "actions": [{"value": json.dumps(approve_data)}]
    }

    mock_diff_resp = MagicMock()
    mock_diff_resp.status_code = 404
    mock_diff_resp.text = "Not Found"

    with patch("requests.get", return_value=mock_diff_resp), \
         patch("reviewer.send_slack_message") as mock_slack_send:

        response = client.post(
            "/slack/interactive",
            data={"payload": json.dumps(action_payload)}
        )

        assert response.status_code == 200
        assert "Failed to fetch diff" in response.json()["text"]

        # Verify error dispatched to Slack
        mock_slack_send.assert_called_once()
        assert "PR #100" in mock_slack_send.call_args[0][0]


# ==========================================
# Tests for Slack Email / Message PR Detection
# ==========================================

def test_extract_pr_links():
    from reviewer import extract_pr_links
    
    # Test text containing an email snippet
    text = (
        "Subject: [GitHub] [test-org/test-repo] Review requested: #123 New feature\n"
        "View it on GitHub:\n"
        "<https://github.com/test-org/test-repo/pull/123>\n"
        "And another PR: https://github.com/test-org/another-repo/pull/456\n"
        "Duplicate: https://github.com/test-org/test-repo/pull/123"
    )
    links = extract_pr_links(text)
    assert len(links) == 2
    assert links[0] == ("test-org/test-repo", 123)
    assert links[1] == ("test-org/another-repo", 456)


def test_fetch_github_pr_details():
    from reviewer import fetch_github_pr_details

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "title": "Add OAuth support",
        "diff_url": "https://github.com/test-org/test-repo/pull/123.diff",
        "head": {"sha": "sha_live_123"},
        "state": "open"
    }

    with patch("requests.get", return_value=mock_resp):
        info = fetch_github_pr_details("test-org/test-repo", 123)
        assert info["title"] == "Add OAuth support"
        assert info["diff_url"] == "https://github.com/test-org/test-repo/pull/123.diff"
        assert info["commit_sha"] == "sha_live_123"


def test_handle_incoming_slack_pr_message_posts_thread_reply():
    from reviewer import handle_incoming_slack_pr_message

    mock_say = MagicMock()
    mock_pr_info = {
        "title": "Fix memory leak",
        "diff_url": "https://github.com/test-org/test-repo/pull/55.diff",
        "commit_sha": "sha_leak_55",
        "state": "open"
    }

    event = {
        "type": "message",
        "channel": "C123456",
        "ts": "1700000000.123456",
        "text": "Review requested: https://github.com/test-org/test-repo/pull/55"
    }

    with patch("reviewer.fetch_github_pr_details", return_value=mock_pr_info):
        handle_incoming_slack_pr_message(event, mock_say)

        mock_say.assert_called_once()
        kwargs = mock_say.call_args[1]
        assert kwargs["thread_ts"] == "1700000000.123456"
        assert "blocks" in kwargs
        blocks = kwargs["blocks"]
        assert "test-org/test-repo" in blocks[0]["text"]["text"]
        assert "55" in blocks[0]["text"]["text"]
        assert "Fix memory leak" in blocks[0]["text"]["text"]


def test_handle_incoming_slack_pr_message_ignores_bot_or_empty():
    from reviewer import handle_incoming_slack_pr_message

    mock_say = MagicMock()

    # Case 1: Message with review_approval_block (sent by bot itself)
    event_bot = {
        "text": "Here is approval prompt",
        "blocks": [{"block_id": "review_approval_block"}]
    }
    handle_incoming_slack_pr_message(event_bot, mock_say)
    mock_say.assert_not_called()

    # Case 2: Message without any GitHub PR links
    event_no_pr = {
        "text": "Hello world, no link here!"
    }
    handle_incoming_slack_pr_message(event_no_pr, mock_say)
    mock_say.assert_not_called()
