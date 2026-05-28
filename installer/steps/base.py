# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024  anaconda-gentoo contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Base class for all installer steps."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from installer.state import InstallerState


class StepError(Exception):
    """Raised when a step fails unrecoverably."""


class Step(ABC):
    name: str         # matches a key in STAGE1_STEPS / STAGE3_STEPS
    description: str  # shown in TUI

    def run(self, state: InstallerState) -> None:
        """Execute the step. Raises StepError on failure."""
        if state.is_done(self.name):
            return
        state.mark_started(self.name)
        try:
            self.execute(state)
        except StepError:
            raise
        except Exception as e:
            raise StepError(str(e)) from e
        state.mark_done(self.name)

    @abstractmethod
    def execute(self, state: InstallerState) -> None:
        """Override in subclass. Raise StepError on failure."""

    @property
    def mountpoint(self) -> Path:
        return Path("/mnt/gentoo")
