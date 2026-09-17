#!/usr/bin/env python3
"""A codex review from a terminal with no tmux window runs in the background.

The Bridge alone decides whether a window is there. A caller issues the same
command either way and reads the same result, so every test here changes
nothing but the environment and asserts only on what the Bridge printed and
left behind.
"""

import pathlib
import unittest
from unittest import mock

from bridge_harness import DriverKilled, FakePaneTestCase


class HeadlessCodexTestCase(FakePaneTestCase):
    def setUp(self):
        super().setUp()
        self.without_tmux()

    def review_argv(self, axis="both"):
        return [
            "--reviewer", "codex",
            "--cwd", str(self.worktree),
            "--base", self.fixed_point,
            "--spec", "spec.md",
            "--axis", axis,
            "--no-network",
        ]


class HeadlessCodexDeliveryTests(HeadlessCodexTestCase):
    def test_a_codex_review_completes_with_no_tmux_at_all(self):
        self.codex.finish("no findings")

        code, output = self.run_bridge(self.args())

        self.assertEqual(code, 0, output)
        result = output["axes"]["standards"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["finalMessage"], "no findings")
        self.assertEqual(
            pathlib.Path(result["reportFile"]).read_text(encoding="utf-8"),
            "no findings",
        )
        self.assertEqual(self.codex.launched_panes, [])
        self.assertEqual(len(self.codex.app_servers), 1)

    def test_the_result_names_no_delivery_mode(self):
        self.codex.finish("no findings")
        _code, headless = self.run_bridge(self.args())
        self.with_tmux()
        _code, pane = self.run_bridge(self.args())

        self.assertEqual(set(headless), set(pane))
        self.assertEqual(
            set(headless["axes"]["standards"]), set(pane["axes"]["standards"])
        )

    def test_both_axes_run_side_by_side_in_the_background(self):
        self.codex.concurrent_turn_count = 2
        self.codex.finish("standards report", axis="standards")
        self.codex.finish("spec report", axis="spec")

        code, output = self.run_bridge(self.args(axis="both"))

        self.assertEqual(code, 0, output)
        self.assertEqual(
            {axis: result["finalMessage"] for axis, result in output["axes"].items()},
            {"standards": "standards report", "spec": "spec report"},
        )
        self.assertEqual(
            sorted(axis for axis, _args in self.codex.app_server_launches),
            ["spec", "standards"],
        )

    def test_the_bridge_starts_the_thread_with_the_callers_permissions(self):
        self.codex.finish("no findings")

        self.run_bridge(
            self.args(sandbox="read-only", approval="on-request", model="gpt-x")
        )

        self.assertEqual(
            self.codex.thread_starts,
            [{
                "cwd": str(self.worktree),
                "sandbox": "read-only",
                "approvalPolicy": "on-request",
            }],
        )
        _axis, launched_args = self.codex.app_server_launches[0]
        self.assertEqual(launched_args.model, "gpt-x")

    def test_mcp_servers_settle_before_the_brief_is_queued(self):
        self.codex.finish("no findings")

        self.run_bridge(self.args())

        self.assertEqual(
            self.codex.control_events,
            [("standards", "mcp-ready"), ("standards", "brief-queued")],
        )

    def test_an_mcp_startup_failure_fails_the_axis_and_stops_its_reviewer(self):
        self.codex.fail_mcp_startup("standards", "config/read failed: broken")

        code, output = self.run_bridge(self.args())

        self.assertEqual(code, 1)
        result = output["axes"]["standards"]
        self.assertEqual(result["status"], "failed")
        self.assertIn("config/read failed: broken", result["reason"])
        self.assertEqual(self.codex.started_turns, [])
        self.assert_no_reviewer_left()

    def test_a_thread_that_cannot_start_fails_the_axis(self):
        self.codex.fail_thread_start("standards", "thread/start failed: denied")

        code, output = self.run_bridge(self.args())

        self.assertEqual(code, 1)
        self.assertIn(
            "thread/start failed: denied", output["axes"]["standards"]["reason"]
        )
        self.assert_no_reviewer_left()

    def test_an_app_server_that_dies_at_startup_fails_with_its_own_words(self):
        self.codex.exit_on_launch("standards", "error: unknown config key")

        code, output = self.run_bridge(self.args())

        self.assertEqual(code, 1)
        reason = output["axes"]["standards"]["reason"]
        self.assertIn("app-server exited during startup", reason)
        self.assertIn("error: unknown config key", reason)
        self.assert_no_reviewer_left()

    def test_an_app_server_that_dies_mid_turn_fails_without_waiting_out_the_timeout(self):
        self.codex.exit_after_brief("standards")

        code, output = self.run_bridge(self.args(timeout=60))

        self.assertEqual(code, 1)
        self.assertIn(
            "app-server exited before the review turn completed",
            output["axes"]["standards"]["reason"],
        )
        self.assert_no_reviewer_left()

    def test_a_reviewer_that_never_finishes_is_stopped_at_the_timeout(self):
        code, output = self.run_bridge(self.args(timeout=0.2))

        self.assertEqual(code, 1)
        self.assertIn(
            "Timed out waiting for the Codex review turn",
            output["axes"]["standards"]["reason"],
        )
        self.assert_no_reviewer_left()

    def test_a_settled_review_leaves_no_reviewer_or_runtime_behind(self):
        self.codex.finish("no findings")

        self.run_bridge(self.args())

        self.assert_no_reviewer_left()

    def test_axes_that_cannot_all_open_open_none(self):
        self.codex.fail_app_server_launch("spec", "codex: command not found")

        with self.assertRaisesRegex(RuntimeError, "codex: command not found"):
            self.run_bridge(self.args(axis="both"))

        self.assertEqual(len(self.codex.app_servers), 1)
        self.assert_no_reviewer_left()
        self.assertEqual(list(self.state_dir.glob("*.json")), [])

    def test_tmux_without_an_originating_pane_runs_in_the_background(self):
        self.with_tmux(pane=None)
        self.codex.finish("no findings")

        code, output = self.run_bridge(self.args())

        self.assertEqual(code, 0, output)
        self.assertEqual(self.codex.launched_panes, [])
        self.assertEqual(len(self.codex.app_servers), 1)

    def test_a_document_review_follows_the_same_rule(self):
        for document in ("docs/spec.md", "docs/ticket.md"):
            path = self.worktree / document
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{document}\n", encoding="utf-8")
        args = self.parsed_args([
            "--reviewer", "codex",
            "--cwd", str(self.worktree),
            "--document", "docs/spec.md",
            "--document", "docs/ticket.md",
            "--axis", "both",
            "--no-network",
        ])
        self.codex.finish("requirements report", axis="requirements")
        self.codex.finish("design report", axis="design")

        code, output = self.run_bridge(args)

        self.assertEqual(code, 0, output)
        self.assertEqual(list(output["axes"]), ["requirements", "design"])
        self.assertEqual(self.codex.launched_panes, [])
        self.assertEqual(len(self.codex.app_servers), 2)

    def test_inside_tmux_the_codex_reviewer_still_opens_a_pane(self):
        self.with_tmux()
        self.codex.finish("no findings")

        code, _output = self.run_bridge(self.args())

        self.assertEqual(code, 0)
        self.assertEqual(len(self.codex.launched_panes), 1)
        self.assertEqual(self.codex.app_servers, [])


class HeadlessCodexRoundTests(HeadlessCodexTestCase):
    def test_the_next_call_re_reviews_the_same_thread_in_the_background(self):
        self.use_graphless_path()
        self.codex.finish("spec round one", axis="spec")
        code, output = self.run_bridge(
            self.parsed_args(self.review_argv(axis="spec"))
        )
        self.assertEqual(code, 0, output)
        first = output["axes"]["spec"]
        first_thread = self.stored_session()["threadId"]
        pathlib.Path(first["nextCall"]["responseFile"]).write_text(
            '1. "spec round one" — fixed in feature.py\n', encoding="utf-8"
        )
        self.codex.finish("spec round two", axis="spec")

        code, output = self.run_bridge(
            self.parsed_args(first["nextCall"]["argv"][1:])
        )

        self.assertEqual(code, 0, output)
        resumed = output["axes"]["spec"]
        self.assertEqual(resumed["finalMessage"], "spec round two")
        self.assertEqual(resumed["reviewSessionId"], first["reviewSessionId"])
        self.assertEqual(self.codex.resumed_threads, [first_thread])
        self.assertEqual(self.codex.launched_panes, [])
        self.assertEqual(len(self.codex.app_servers), 2)
        self.assert_no_reviewer_left()

    def test_a_background_lineage_is_capped_like_any_other(self):
        self.use_graphless_path()
        self.codex.finish("standards round one", axis="standards")
        _code, output = self.run_bridge(
            self.parsed_args(self.review_argv(axis="standards"))
        )
        session = output["axes"]["standards"]["reviewSessionId"]

        code, refused = self.run_bridge(self.parsed_args([
            *self.review_argv(axis="standards"),
            "--resume-session", session,
            "--response", self.default_response_file(),
        ]))

        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["status"], "refused")
        self.assertEqual(len(self.codex.app_servers), 1)


class HeadlessCodexRecoveryTests(HeadlessCodexTestCase):
    def test_a_killed_driver_leaves_its_reviewer_running(self):
        state = self.kill_the_driver()

        self.assertEqual(
            state["owner"],
            {
                "tmux_server": "",
                "origin_pane": "",
                "worktree_root": str(self.worktree),
            },
        )
        self.assertEqual(self.codex.live_app_servers, set(self.codex.app_servers))
        self.assertTrue(pathlib.Path(state["runtimeDir"]).is_dir())

    def test_a_plain_terminal_recovers_the_review_its_killed_driver_left(self):
        killed = self.kill_the_driver()
        self.codex.finish("recovered report")

        code, output = self.run_bridge(self.args(recover_session=True))

        self.assertEqual(code, 0, output)
        result = output["axes"]["standards"]
        self.assertTrue(result["recovered"])
        self.assertEqual(result["reviewSessionId"], killed["reviewSessionId"])
        self.assertEqual(result["finalMessage"], "recovered report")
        self.assertEqual(len(self.codex.app_servers), 1)
        self.assert_no_reviewer_left()

    def test_a_reviewer_that_died_with_its_driver_leaves_nothing_to_recover(self):
        self.kill_the_driver()
        self.codex.live_app_servers.clear()

        with self.assertRaises(self.bridge.NoLiveSessionError):
            self.run_bridge(self.args(recover_session=True))

    def test_a_pane_review_is_not_recovered_from_a_plain_terminal(self):
        self.with_tmux()
        self.kill_the_driver()
        self.without_tmux()

        with self.assertRaises(self.bridge.NoLiveSessionError):
            self.run_bridge(self.args(recover_session=True))

    def test_a_background_review_is_not_recovered_from_a_tmux_pane(self):
        self.kill_the_driver()
        self.with_tmux()

        with self.assertRaises(self.bridge.NoLiveSessionError):
            self.run_bridge(self.args(recover_session=True))

    def leave_an_undelivered_report(self, reviewer):
        """A review whose report is stored, and whose driver died printing it."""
        self.lane(reviewer).finish(f"{reviewer} report")
        killed_driver = self.enter(mock.patch.object(
            self.bridge.Lane, "mark_delivered", side_effect=DriverKilled
        ))
        with self.assertRaises(DriverKilled):
            self.run_bridge(self.args(reviewer=reviewer))
        self.stop_patcher(killed_driver)

    def test_the_claude_lane_never_recovers_a_background_codex_review(self):
        self.leave_an_undelivered_report("codex")

        with self.assertRaises(self.bridge.NoLiveSessionError):
            self.run_bridge(self.args(reviewer="claude", recover_session=True))

        code, output = self.run_bridge(self.args(recover_session=True))
        self.assertEqual(code, 0, output)
        self.assertEqual(
            output["axes"]["standards"]["finalMessage"], "codex report"
        )

    def test_the_codex_lane_never_recovers_a_claude_review(self):
        self.leave_an_undelivered_report("claude")

        with self.assertRaises(self.bridge.NoLiveSessionError):
            self.run_bridge(self.args(recover_session=True))

    def test_a_claude_handle_cannot_be_resumed_on_the_codex_lane(self):
        self.claude.finish("claude report", axis="spec")
        _code, output = self.run_bridge(
            self.args(reviewer="claude", axis="spec")
        )
        session = output["axes"]["spec"]["reviewSessionId"]

        with self.assertRaisesRegex(RuntimeError, "belongs to another"):
            self.run_bridge(self.args(axis="spec", resume_session=session))

        self.assertEqual(self.codex.app_servers, [])


if __name__ == "__main__":
    unittest.main()
