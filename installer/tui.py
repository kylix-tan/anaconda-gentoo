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

"""Rich-based TUI for the installer."""
from __future__ import annotations

import traceback
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import print as rprint

from installer.state import InstallerState
from installer.steps.base import Step, StepError

console = Console()


def print_banner() -> None:
    console.print(Panel.fit(
        "[bold green]anaconda-gentoo[/bold green]\n"
        "[dim]Gentoo Linux Installer with Hardware-Aware Kernel Configuration[/dim]",
        border_style="green",
    ))
    console.print()


def print_step_header(step: Step, index: int, total: int) -> None:
    console.rule(f"[bold cyan][{index}/{total}] {step.description}[/bold cyan]")


def print_summary(state: InstallerState) -> None:
    done, total = state.progress()
    table = Table(title="Installation Progress", show_header=True)
    table.add_column("Step", style="cyan")
    table.add_column("Status", justify="center")

    from installer.state import STAGE1_STEPS
    for step in STAGE1_STEPS:
        if step in state.completed:
            status = "[green]✓ done[/green]"
        elif step == state.current:
            status = "[yellow]→ running[/yellow]"
        elif step == state.failed:
            status = "[red]✗ failed[/red]"
        else:
            status = "[dim]pending[/dim]"
        table.add_row(step.replace("_", " "), status)

    console.print(table)
    console.print(f"\nProgress: {done}/{total} steps completed\n")


def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    value = console.input(f"[bold]{prompt}{hint}:[/bold] ").strip()
    return value or default


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    ans = console.input(f"[bold]{prompt} [{hint}]:[/bold] ").strip().lower()
    if not ans:
        return default
    return ans.startswith("y")


def run_step(step: Step, state: InstallerState, index: int, total: int) -> bool:
    """Run a single step with TUI feedback. Returns True on success."""
    if state.is_done(step.name):
        console.print(f"[dim]  [{index}/{total}] {step.description} — already done, skipping[/dim]")
        return True

    print_step_header(step, index, total)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(step.description, total=None)
        try:
            step.run(state)
            progress.update(task, completed=True)
        except StepError as e:
            console.print(f"\n[red bold]Step failed:[/red bold] {e}")
            return False
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted — progress saved[/yellow]")
            state.save()
            raise

    console.print(f"[green]  ✓ {step.description}[/green]")
    return True


def run_all_steps(steps: list[Step], state: InstallerState) -> bool:
    """Run all steps in sequence. Returns True if all completed."""
    total = len(steps)
    for i, step in enumerate(steps, 1):
        ok = run_step(step, state, i, total)
        if not ok:
            console.print("\n[red]Installation paused due to error.[/red]")
            console.print("Fix the issue and re-run to resume from this step.\n")
            return False
    return True
