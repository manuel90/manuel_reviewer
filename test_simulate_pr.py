import json
import pytest
from unittest.mock import patch, MagicMock
import requests

import simulate_pr
from simulate_pr import build_github_payload, main


class MockArgs:
    def __init__(self, **kwargs):
        self.pr_url_arg = kwargs.get("pr_url_arg", None)
        self.pr_url = kwargs.get("pr_url", None)
        self.repo = kwargs.get("repo", "test-org/test-repo")
        self.pr = kwargs.get("pr", 101)
        self.title = kwargs.get("title", "Test PR Title")
        self.assignee = kwargs.get("assignee", "test_user")
        self.action = kwargs.get("action", "opened")
        self.diff_url = kwargs.get("diff_url", None)
        self.commit_sha = kwargs.get("commit_sha", "c0ffee123456")
        self.server_url = kwargs.get("server_url", "http://localhost:8000/webhook")
        self.direct = kwargs.get("direct", False)
        self.simulate_approval = kwargs.get("simulate_approval", False)
        self.dry_run = kwargs.get("dry_run", False)


def test_build_github_payload():
    args = MockArgs()
    diff_url = "https://api.github.com/repos/test-org/test-repo/pulls/101"
    payload = build_github_payload(
        args.repo, args.pr, args.title, diff_url, args.commit_sha, args.assignee, args.action
    )

    assert payload["action"] == "opened"
    assert payload["repository"]["full_name"] == "test-org/test-repo"
    assert payload["repository"]["name"] == "test-repo"
    assert payload["pull_request"]["number"] == 101
    assert payload["pull_request"]["title"] == "Test PR Title"
    assert payload["pull_request"]["diff_url"] == diff_url
    assert payload["pull_request"]["head"]["sha"] == "c0ffee123456"
    assert payload["pull_request"]["assignees"] == [{"login": "test_user"}]


def test_simulate_pr_dry_run(capsys):
    args = MockArgs(dry_run=True)
    with patch("simulate_pr.parse_arguments", return_value=args), \
         patch("simulate_pr.fetch_live_github_pr_info", return_value=None):
        code = main()
        assert code == 0
        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "Test PR Title" in captured.out


def test_simulate_pr_direct_mode():
    args = MockArgs(direct=True)
    with patch("simulate_pr.parse_arguments", return_value=args), \
         patch("simulate_pr.fetch_live_github_pr_info", return_value=None), \
         patch("simulate_pr.send_slack_approval_request", return_value=True) as mock_send:
        code = main()
        assert code == 0
        mock_send.assert_called_once_with(
            repo=args.repo,
            pr_number=args.pr,
            pr_title=args.title,
            diff_url=f"https://api.github.com/repos/{args.repo}/pulls/{args.pr}",
            commit_sha=args.commit_sha
        )


def test_simulate_pr_server_mode_success():
    args = MockArgs(direct=False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "pending_approval",
        "message": "Confirmation prompt sent to Slack",
        "slack_delivered": True
    }

    with patch("simulate_pr.parse_arguments", return_value=args), \
         patch("simulate_pr.fetch_live_github_pr_info", return_value=None), \
         patch("requests.post", return_value=mock_resp) as mock_post:
        code = main()
        assert code == 0
        mock_post.assert_called_once()
        url, kwargs = mock_post.call_args
        assert url[0] == "http://localhost:8000/webhook"
        assert kwargs["headers"]["X-GitHub-Event"] == "pull_request"


def test_simulate_pr_server_down_fallback():
    args = MockArgs(direct=False)
    with patch("simulate_pr.parse_arguments", return_value=args), \
         patch("simulate_pr.fetch_live_github_pr_info", return_value=None), \
         patch("requests.post", side_effect=requests.exceptions.ConnectionError("Connection refused")), \
         patch("simulate_pr.send_slack_approval_request", return_value=True) as mock_direct_send:
        code = main()
        assert code == 0
        mock_direct_send.assert_called_once()
