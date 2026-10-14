import argparse
import asyncio
import sys

from network.network import AgentNetwork
from observability import record_chat_turn, record_metrics, record_session_start, start_run
from ui.cli import CLI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Agent Network — orchestrator-based multi-agent planning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python main.py --agents personas/orchestrator.md personas/architect.md personas/tech_lead.md
  python main.py --agents personas/*.md --backend claude --consensus majority
        """,
    )
    parser.add_argument(
        "--agents", nargs="+", required=True, metavar="FILE",
        help="Agent persona .md files (one must have role: orchestrator in frontmatter)",
    )
    parser.add_argument(
        "--backend", default="ollama", choices=["ollama", "claude"],
        help="Default LLM backend (default: ollama)",
    )
    parser.add_argument(
        "--model", default="llama3.2",
        help="Default model name for the chosen backend (default: llama3.2)",
    )
    parser.add_argument(
        "--consensus", default="all", choices=["all", "majority"],
        help="Consensus rule: all agents must approve, or simple majority (default: all)",
    )
    parser.add_argument(
        "--max-rounds", type=int, default=5,
        help="Max validation rounds before forcing a conclusion (default: 5)",
    )
    parser.add_argument(
        "--debug-trace", action="store_true",
        help="Run log keeps prompt/answer texts (local debugging only; never share runs/)",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    cli = CLI()
    # One session = one run log in runs/ (no task/plan/persona/chat text).
    with start_run(args) as run_log:
        network = AgentNetwork(
            default_backend=args.backend,
            default_model=args.model,
            consensus=args.consensus,
            max_rounds=args.max_rounds,
            run=run_log,
        )
        try:
            await session(args, cli, network)
        finally:
            record_metrics(run_log, network)
    if run_log.dir.exists():
        cli.console.print(f"[dim]Run log: {run_log.dir}[/dim]")


async def session(args: argparse.Namespace, cli: CLI, network: AgentNetwork) -> None:
    run_log = network.run
    try:
        network.load_agents(args.agents)
    except (ValueError, FileNotFoundError) as e:
        cli.console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)
    if run_log is not None:
        record_session_start(run_log, network, args)

    cli.show_banner(
        orchestrator_name=network.orchestrator.name,
        worker_names=[w.name for w in network.workers],
    )

    while True:
        if run_log is not None:
            run_log.mark_stage("task_input")
        task = await cli.get_input("Enter a task/feature to plan (or 'quit')")
        if task.lower() == "quit":
            break
        if not task:
            continue

        plan, dissatisfied = await network.run_task(task, cli)
        cli.show_final_result(plan, dissatisfied)

        # Free chat mode after planning session
        if run_log is not None:
            run_log.mark_stage("chat_mode")
        cli.console.print(
            "[dim]You are now in chat mode. Talk to agents about the plan, "
            "type 'new' for a new task, or 'quit' to exit.[/dim]"
        )

        while True:
            user_input = await cli.get_input("Chat")
            if not user_input:
                continue
            if user_input.lower() == "quit":
                return  # same exit code 0 as before, but the run log closes cleanly
            if user_input.lower() == "new":
                break

            if user_input.startswith("@"):
                parts = user_input.split(" ", 1)
                target = parts[0][1:].lower()
                message = parts[1] if len(parts) > 1 else ""

                if target == "all":
                    with cli.thinking_all(network.all_agent_names):
                        responses = await network.broadcast_chat(message)
                    record_chat_turn(run_log, "all", replies=len(responses))
                    for name, response in responses:
                        cli.agent_message(name, response)
                else:
                    with cli.thinking(target):
                        result = await network.chat_with_agent(target, message)
                    record_chat_turn(run_log, "agent" if result is not None else "unknown_agent",
                                     replies=0 if result is None else 1)
                    if result is None:
                        valid = ", ".join(f"@{n.lower()}" for n in network.all_agent_names)
                        cli.console.print(f"[red]Unknown agent '{target}'. Valid: {valid}[/red]")
                    else:
                        actual_name, response = result
                        cli.agent_message(actual_name, response)
            else:
                # No @-prefix → broadcast to all
                with cli.thinking_all(network.all_agent_names):
                    responses = await network.broadcast_chat(user_input)
                record_chat_turn(run_log, "all", replies=len(responses))
                for name, response in responses:
                    cli.agent_message(name, response)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
