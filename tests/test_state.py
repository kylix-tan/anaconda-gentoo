# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024  anaconda-gentoo contributors
"""Tests for installer.state — state machine and JSON persistence."""
import json
import tempfile
from pathlib import Path

import pytest

from installer.state import InstallerState, STAGE1_STEPS, STAGE3_STEPS


class TestInstallerState:
    def test_initial_state(self):
        s = InstallerState()
        assert s.phase == "stage1"
        assert s.completed == []
        assert s.current is None
        assert s.failed is None

    def test_mark_started(self):
        s = InstallerState()
        s.mark_started("hardware_detect")
        assert s.current == "hardware_detect"
        assert s.failed is None

    def test_mark_done(self):
        s = InstallerState()
        s.mark_started("hardware_detect")
        s.mark_done("hardware_detect")
        assert s.is_done("hardware_detect")
        assert s.current is None
        assert "hardware_detect" in s.completed

    def test_mark_done_idempotent(self):
        s = InstallerState()
        s.mark_done("hardware_detect")
        s.mark_done("hardware_detect")
        assert s.completed.count("hardware_detect") == 1

    def test_mark_failed(self):
        s = InstallerState()
        s.mark_started("partition")
        s.mark_failed("partition", "no disk found")
        assert s.failed == "partition"
        assert s.current is None
        assert not s.is_done("partition")

    def test_progress(self):
        s = InstallerState()
        done, total = s.progress()
        assert done == 0
        assert total == len(STAGE1_STEPS)

        s.mark_done("hardware_detect")
        s.mark_done("disk_select")
        done, total = s.progress()
        assert done == 2

    def test_pending_steps(self):
        s = InstallerState()
        s.mark_done("hardware_detect")
        pending = s.pending_steps()
        assert "hardware_detect" not in pending
        assert "partition" in pending

    def test_config_set_get(self):
        s = InstallerState()
        s.config["disk"] = "/dev/nvme0n1"
        assert s.get("disk") == "/dev/nvme0n1"
        assert s.get("missing", "default") == "default"

    def test_log_appended(self):
        s = InstallerState()
        s.mark_started("hardware_detect")
        s.mark_done("hardware_detect")
        assert any("START" in entry for entry in s.log)
        assert any("DONE" in entry for entry in s.log)

    def test_json_round_trip(self, tmp_path):
        state_file = tmp_path / "state.json"
        s = InstallerState()
        s.mark_done("hardware_detect")
        s.config["disk"] = "/dev/sda"
        s.save(state_file)

        assert state_file.exists()
        loaded = InstallerState.load(state_file)
        assert loaded.is_done("hardware_detect")
        assert loaded.get("disk") == "/dev/sda"
        assert loaded.phase == "stage1"

    def test_load_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        s = InstallerState.load(missing)
        assert s.phase == "stage1"
        assert s.completed == []

    def test_load_corrupt_file(self, tmp_path):
        bad = tmp_path / "state.json"
        bad.write_text("not json {{{{")
        s = InstallerState.load(bad)
        # Should return fresh state, not raise
        assert s.phase == "stage1"

    def test_stage3_progress(self):
        s = InstallerState()
        s.phase = "stage3"
        done, total = s.progress()
        assert total == len(STAGE3_STEPS)
