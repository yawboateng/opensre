"""Approval prompt rendering.

The preview is what a human reads before clicking Approve on a write they did
not compose, so these pin the properties that make it trustworthy: every
argument is represented, the interesting one is not starved by a long one, and
nothing secret-shaped reaches a channel.
"""

from __future__ import annotations

import pytest

from gateway.core.runtime.approvals import ARGS_PREVIEW_LIMIT, arguments_preview

_PR_ARGUMENTS = {
    "owner": "acme",
    "repo": "infra",
    "base_branch": "main",
    "head_branch": "fix/drop-orphan-subscription",
    "title": "fix(pubsub): drop the orphaned subscription",
    "body": "**What:** stop creating the placeholder subscription. " * 12,
    "changes": [
        {"path": "terraform/modules/functions/notifications.tf", "content": "x" * 4103},
        {"path": "terraform/modules/functions/legacy.tf", "delete": True},
    ],
}


class TestArgumentsPreview:
    def test_empty_arguments_render_nothing(self) -> None:
        assert arguments_preview({}) == ""

    def test_every_argument_appears(self) -> None:
        preview = arguments_preview(_PR_ARGUMENTS)
        for key in _PR_ARGUMENTS:
            assert f"{key}:" in preview

    def test_each_argument_starts_its_own_line(self) -> None:
        lines = arguments_preview(_PR_ARGUMENTS).splitlines()
        assert lines[0] == "owner: acme"
        assert "repo: infra" in lines

    def test_changed_paths_are_listed(self) -> None:
        """The regression: JSON truncation meant `changes` never rendered."""
        preview = arguments_preview(_PR_ARGUMENTS)
        assert "changes: 2 items" in preview
        assert "terraform/modules/functions/notifications.tf" in preview
        assert "terraform/modules/functions/legacy.tf" in preview

    def test_file_contents_are_reported_by_length_not_pasted(self) -> None:
        preview = arguments_preview(_PR_ARGUMENTS)
        assert "content 4103 chars" in preview
        assert "x" * 200 not in preview

    def test_deletion_is_visible(self) -> None:
        assert "delete=True" in arguments_preview(_PR_ARGUMENTS)

    def test_long_value_is_summarised_with_its_length(self) -> None:
        preview = arguments_preview({"body": "y" * 900})
        assert "(900 chars)" in preview
        assert len(preview) < 300

    def test_long_value_does_not_starve_later_arguments(self) -> None:
        preview = arguments_preview({"body": "y" * 5000, "repo": "infra"})
        assert "repo: infra" in preview

    def test_short_value_is_shown_whole(self) -> None:
        assert "repo: infra" in arguments_preview({"repo": "infra"})

    def test_newlines_in_a_value_are_flattened(self) -> None:
        preview = arguments_preview({"body": "first\nsecond"})
        assert preview == "body: first second"

    @pytest.mark.parametrize(
        "key",
        ["github_token", "GITHUB_TOKEN", "password", "api_key", "client_secret", "auth_header"],
    )
    def test_secret_looking_keys_are_redacted(self, key: str) -> None:
        preview = arguments_preview({key: "ghp_realvaluehere"})
        assert "ghp_realvaluehere" not in preview
        assert "••••" in preview

    def test_secret_inside_a_list_entry_is_redacted(self) -> None:
        preview = arguments_preview({"items": [{"name": "a", "token": "ghp_secret"}]})
        assert "ghp_secret" not in preview
        assert "••••" in preview

    def test_empty_list_says_none(self) -> None:
        assert arguments_preview({"changes": []}) == "changes: (none)"

    def test_singular_item_count(self) -> None:
        preview = arguments_preview({"changes": [{"path": "a.tf", "content": "x"}]})
        assert "changes: 1 item" in preview

    def test_long_list_reports_the_remainder(self) -> None:
        changes = [{"path": f"file{index}.tf", "content": "x"} for index in range(20)]
        preview = arguments_preview({"changes": changes})
        assert "changes: 20 items" in preview
        assert "… and 12 more" in preview

    def test_list_of_scalars_renders(self) -> None:
        preview = arguments_preview({"labels": ["bug", "infra"]})
        assert "· bug" in preview
        assert "· infra" in preview

    def test_entry_without_a_label_key_still_renders(self) -> None:
        preview = arguments_preview({"items": [{"kind": "pod", "phase": "Running"}]})
        assert "kind=pod" in preview

    def test_mapping_value_reports_its_fields(self) -> None:
        preview = arguments_preview({"filters": {"env": "prod", "tier": "web"}})
        assert "filters: 2 fields (env, tier)" in preview

    def test_empty_mapping_value(self) -> None:
        assert arguments_preview({"filters": {}}) == "filters: (empty)"

    def test_non_string_values_do_not_raise(self) -> None:
        preview = arguments_preview({"count": 3, "draft": False, "ratio": 1.5, "nothing": None})
        assert "count: 3" in preview
        assert "draft: False" in preview
        assert "nothing: None" in preview

    def test_preview_stays_within_the_transport_budget(self) -> None:
        changes = [
            {"path": f"a/very/long/path/number/{i}.tf", "content": "x" * 900} for i in range(60)
        ]
        preview = arguments_preview({"changes": changes, "body": "z" * 9000})
        assert len(preview) <= ARGS_PREVIEW_LIMIT + 80

    def test_dropped_lines_are_announced_rather_than_silently_cut(self) -> None:
        arguments = {f"field_{index}": "value" * 20 for index in range(200)}
        assert "more line(s) not shown" in arguments_preview(arguments)
