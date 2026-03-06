import asyncio
from contextlib import contextmanager
from typing import List

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from network.message import AgentValidation, ApprovalStatus

_COLORS = [
    "cyan", "green", "yellow", "magenta", "blue",
    "bright_cyan", "bright_green", "bright_yellow", "bright_magenta",
]


class CLI:
    def __init__(self):
        self.console = Console()
        self._colors: dict[str, str] = {}
        self._color_idx = 0

    def _color(self, name: str) -> str:
        if name not in self._colors:
            self._colors[name] = _COLORS[self._color_idx % len(_COLORS)]
            self._color_idx += 1
        return self._colors[name]

    # ------------------------------------------------------------------ display

    def show_banner(self, orchestrator_name: str, worker_names: List[str]) -> None:
        self.console.print()
        agents_line = "  ".join(
            f"[{self._color(n)}]{n}[/]" for n in worker_names
        )
        self.console.print(Panel(
            f"[bold]Orchestrator:[/bold]  [{self._color(orchestrator_name)}]{orchestrator_name}[/]\n"
            f"[bold]Agents:[/bold]        {agents_line}\n\n"
            f"[dim]Commands in chat mode:[/dim]\n"
            f"  [cyan]@name message[/cyan]   — talk to a specific agent\n"
            f"  [cyan]@all message[/cyan]    — broadcast to all agents\n"
            f"  [cyan]new[/cyan]             — submit a new task\n"
            f"  [cyan]quit[/cyan]            — exit",
            title="[bold white]AI Agent Network[/bold white]",
            border_style="white",
            padding=(1, 2),
        ))
        self.console.print()

    def phase(self, text: str) -> None:
        self.console.print()
        self.console.print(Rule(f"[bold white]{text}[/bold white]"))
        self.console.print()

    def agent_message(self, name: str, content: str, suffix: str = "") -> None:
        color = self._color(name)
        self.console.print(Panel(
            Markdown(content),
            title=f"[bold {color}]{name}[/bold {color}]{suffix}",
            border_style=color,
        ))
        self.console.print()

    def human_note(self, note: str) -> None:
        self.console.print(Panel(
            note,
            title="[bold white]You[/bold white]",
            border_style="white",
        ))
        self.console.print()

    def show_plan(self, plan: str, version: int) -> None:
        self.console.print(Panel(
            Markdown(plan),
            title=f"[bold white]Plan v{version}[/bold white]",
            border_style="bright_white",
            padding=(1, 2),
        ))
        self.console.print()

    def show_validations(self, validations: List[AgentValidation]) -> None:
        table = Table(
            title="Validation Results",
            show_header=True,
            header_style="bold white",
            show_lines=True,
        )
        table.add_column("Agent", style="bold", no_wrap=True)
        table.add_column("Status", justify="center", no_wrap=True)
        table.add_column("Reasoning")

        for v in validations:
            color = self._color(v.agent_name)
            status_text = (
                Text("  APPROVE", style="bold green")
                if v.status == ApprovalStatus.APPROVE
                else Text("  CONCERN", style="bold red")
            )
            reasoning = v.reasoning
            if len(reasoning) > 300:
                reasoning = reasoning[:297] + "..."
            table.add_row(f"[{color}]{v.agent_name}[/]", status_text, reasoning)

        self.console.print(table)
        self.console.print()

    def show_final_result(self, plan: str, dissatisfied: List[AgentValidation]) -> None:
        if dissatisfied:
            self.console.print(Rule("[bold red]Max Rounds Reached — No Full Consensus[/bold red]"))
        else:
            self.console.print(Rule("[bold green]Consensus Reached[/bold green]"))

        self.console.print(Panel(
            Markdown(plan),
            title="[bold white]Final Plan[/bold white]",
            border_style="bright_white",
            padding=(1, 2),
        ))

        if dissatisfied:
            self.console.print()
            self.console.print("[bold red]Agents not satisfied with the final plan:[/bold red]")
            self.console.print()
            for v in dissatisfied:
                color = self._color(v.agent_name)
                self.console.print(Panel(
                    v.reasoning,
                    title=f"[bold {color}]{v.agent_name}[/bold {color}] — Unresolved Concern",
                    border_style="red",
                ))
        self.console.print()

    # ----------------------------------------------------------------- spinners

    @contextmanager
    def thinking(self, name: str):
        color = self._color(name)
        with self.console.status(
            f"[{color}]{name}[/] is thinking...",
            spinner="dots",
        ):
            yield

    @contextmanager
    def thinking_all(self, names: List[str]):
        label = "  ·  ".join(f"[{self._color(n)}]{n}[/]" for n in names)
        with self.console.status(
            f"{label} [dim]thinking...[/dim]",
            spinner="dots",
        ):
            yield

    # ------------------------------------------------------------------- input

    async def get_input(self, prompt: str) -> str:
        self.console.print()
        return (await asyncio.to_thread(input, f"[You] {prompt}: ")).strip()

    async def optional_input(self, prompt: str = "Add context? (Enter to skip)") -> str:
        self.console.print()
        result = (await asyncio.to_thread(input, f"[You] {prompt}: ")).strip()
        return result
