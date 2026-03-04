"""Main entry point for Mythic CLI."""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape as rich_escape

from .config import ConfigManager
from .shell import MythicShell

try:
    import argcomplete
except ImportError:
    argcomplete = None

console = Console()


def get_callback_ids(ctx, args, incomplete):
    """Completion function for callback IDs."""
    if argcomplete is None:
        return []
    
    try:
        config_manager = ctx.obj.get("config_manager")
        if config_manager:
            from .client import MythicClient
            client = MythicClient(config_manager.config)
            if client.api_token:
                callbacks = client.get_callbacks()
                return [
                    str(cb.get("id", ""))
                    for cb in callbacks
                    if str(cb.get("id", "")).startswith(incomplete)
                ]
    except Exception:
        pass
    return []


def get_operation_ids(ctx, args, incomplete):
    """Completion function for operation IDs."""
    if argcomplete is None:
        return []
    
    try:
        config_manager = ctx.obj.get("config_manager")
        if config_manager:
            from .client import MythicClient
            client = MythicClient(config_manager.config)
            if client.api_token:
                operations = client.get_operations()
                return [
                    str(op.get("id", ""))
                    for op in operations
                    if str(op.get("id", "")).startswith(incomplete)
                ]
    except Exception:
        pass
    return []


# Custom Click parameter type with argcomplete support
class CompletableChoice(click.Choice):
    """Choice type with completion support."""
    
    name = "choice"

    def __init__(self, choices, case_sensitive=True):
        super().__init__(choices, case_sensitive)

    def shell_complete(self, ctx, param, incomplete):
        """Provide completions for this parameter."""
        completions = [
            click.shell_completion.CompletionItem(choice)
            for choice in self.choices
            if choice.startswith(incomplete)
        ]
        return completions


@click.group(invoke_without_command=True)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=False, path_type=Path),
    help="Path to configuration file",
)
@click.option("--server", "-s", help="Mythic server URL")
@click.option("--api-key", "-k", help="API key for authentication")
@click.option("--no-ssl-verify", is_flag=True, help="Disable SSL verification")
@click.option(
    "--theme",
    type=click.Choice(["default", "monokai", "dracula", "nord", "solarized"], case_sensitive=False),
    help="TUI theme",
)
@click.pass_context
def cli(ctx, config, server, api_key, no_ssl_verify, theme):
    """Mythic CLI - Interactive command-line interface for Mythic C2 server.

    Run without arguments to start the interactive shell.
    """
    # Initialize config manager
    config_manager = ConfigManager(config)

    # Update config from CLI arguments
    updates = {}
    if server:
        updates["server_url"] = server
    if api_key:
        updates["api_key"] = api_key
    if no_ssl_verify:
        updates["verify_ssl"] = False
    if theme:
        updates["tui_theme"] = theme.lower()

    if updates:
        config_manager.update_config(**updates)

    # Store in context
    ctx.obj = {"config_manager": config_manager}

    # If no subcommand, start interactive shell
    if ctx.invoked_subcommand is None:
        shell = MythicShell(config_manager)
        try:
            shell.run()
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted[/yellow]")
            sys.exit(0)


@cli.command()
@click.option("--username", "-u", prompt=True, help="Username")
@click.password_option(help="Password")
@click.pass_context
def login(ctx, username, password):
    """Login to Mythic server and save credentials."""
    from .client import MythicClient, MythicAPIException

    config_manager = ctx.obj["config_manager"]
    client = MythicClient(config_manager.config)

    try:
        if client.login(username, password):
            config_manager.update_config(username=username, api_key=client.api_token)
            console.print("[green]✅[/green] Login successful and credentials saved")
    except MythicAPIException as e:
        console.print(f"[red]Login failed:[/red] {str(e)}")
        sys.exit(1)


@cli.command()
@click.pass_context
def config_show(ctx):
    """Show current configuration."""
    config_manager = ctx.obj["config_manager"]
    config_dict = config_manager.config.model_dump(exclude={"password"})

    from rich.table import Table

    table = Table(title="Current Configuration", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="yellow")

    for key, value in config_dict.items():
        if value is not None:
            table.add_row(key, str(value))

    console.print(table)


@cli.command()
@click.option("--server", help="Mythic server URL")
@click.option("--api-key", help="API key")
@click.option("--username", help="Username")
@click.option(
    "--theme",
    type=click.Choice(["default", "monokai", "dracula", "nord", "solarized"], case_sensitive=False),
    help="TUI theme",
)
@click.option(
    "--verify-ssl/--no-verify-ssl",
    default=None,
    help="SSL verification",
)
@click.pass_context
def config_set(ctx, server, api_key, username, theme, verify_ssl):
    """Update configuration settings."""
    config_manager = ctx.obj["config_manager"]

    updates = {}
    if server:
        updates["server_url"] = server
    if api_key:
        updates["api_key"] = api_key
    if username:
        updates["username"] = username
    if theme:
        updates["tui_theme"] = theme.lower()
    if verify_ssl is not None:
        updates["verify_ssl"] = verify_ssl

    if updates:
        config_manager.update_config(**updates)
        console.print("[green]✅[/green] Configuration updated")
    else:
        console.print("[yellow]No settings to update[/yellow]")


@cli.command()
@click.pass_context
def shell(ctx):
    """Start interactive shell."""
    config_manager = ctx.obj["config_manager"]
    mythic_shell = MythicShell(config_manager)
    try:
        mythic_shell.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(0)


@cli.command()
@click.pass_context
def version(ctx):
    """Show version information."""
    from . import __version__

    console.print(f"Mythic CLI version [cyan]{__version__}[/cyan]")


@cli.command(name="list-payload-types")
@click.pass_context
def list_payload_types(ctx):
    """List available payload types."""
    from .client import MythicClient
    
    config_manager = ctx.obj["config_manager"]
    
    try:
        client = MythicClient(config_manager.config)
        if not client.api_token:
            console.print("[red]Not logged in. Run 'mythic login' first.[/red]")
            return
        
        payload_types = client.get_payload_types()
        
        if not payload_types:
            console.print("[yellow]No payload types available[/yellow]")
            return
        
        console.print("[bold cyan]Available Payload Types:[/bold cyan]\n")
        for pt in payload_types:
            name = pt.get("name", "")
            supported_os = ", ".join(pt.get("supported_os", []))
            wrapper = "[yellow](Wrapper)[/yellow]" if pt.get("wrapper") else ""
            console.print(f"  • [green]{name}[/green] {wrapper}")
            console.print(f"    OS: {supported_os}")
            if pt.get("file_extension"):
                console.print(f"    Extension: .{pt.get('file_extension')}")
            console.print()
    except Exception as exc:
        console.print(f"[red]Failed to list payload types:[/red] {rich_escape(str(exc))}")


@cli.command(name="payload-builder")
@click.pass_context
def payload_builder(ctx):
    """Launch the interactive payload builder TUI."""
    from mythic_tui.payload_builder import PayloadBuilderScreen
    from mythic_tui.app import MythicTextualApp
    from .client import MythicClient
    
    config_manager = ctx.obj["config_manager"]
    
    try:
        client = MythicClient(config_manager.config)
        if not client.api_token:
            console.print("[red]Not logged in. Run 'mythic login' first.[/red]")
            return
        
        # Create a simple app just to run the payload builder
        from textual.app import App
        
        class PayloadBuilderApp(App):
            def on_mount(self):
                self.push_screen(PayloadBuilderScreen(client))
        
        app = PayloadBuilderApp()
        app.run()
    except Exception as exc:
        console.print(f"[red]Failed to launch payload builder:[/red] {rich_escape(str(exc))}")


@cli.command(name="build-payload")
@click.pass_context
def build_payload(ctx):
    """Interactive payload builder (CLI version)."""
    from .client import MythicClient
    from .payload_builder import run_payload_builder_click
    
    config_manager = ctx.obj["config_manager"]
    
    try:
        client = MythicClient(config_manager.config)
        if not client.api_token:
            console.print("[red]Not logged in. Run 'mythic login' first.[/red]")
            return
        
        run_payload_builder_click(client, mode="payload")
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {rich_escape(str(exc))}")


@cli.command(name="build-wrapper")
@click.pass_context
def build_wrapper(ctx):
    """Interactive wrapper builder (CLI version)."""
    from .client import MythicClient
    from .payload_builder import run_payload_builder_click
    
    config_manager = ctx.obj["config_manager"]
    
    try:
        client = MythicClient(config_manager.config)
        if not client.api_token:
            console.print("[red]Not logged in. Run 'mythic login' first.[/red]")
            return
        
        run_payload_builder_click(client, mode="wrapper")
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {rich_escape(str(exc))}")


@cli.command(name="completion-install")
@click.option(
    "--shell",
    type=CompletableChoice(["bash", "zsh", "fish"]),
    default="bash",
    help="Shell type for completion",
)
@click.pass_context
def completion_install(ctx, shell):
    """Install shell autocompletion.

    This command generates the setup instructions for shell autocompletion.

    \b
    Examples:
      mythic completion-install --shell bash
      eval "$(mythic completion-install --shell bash)"
    """
    if argcomplete is None:
        console.print("argcomplete not installed")
        sys.exit(1)

    prog_name = "mythic"

    if shell == "bash":
        completion_script = f'eval "$(register-python-argcomplete {prog_name})"'
    elif shell == "zsh":
        completion_script = f'eval "$(register-python-argcomplete --shell zsh {prog_name})"'
    elif shell == "fish":
        completion_script = f'register-python-argcomplete --shell fish {prog_name}'
    else:
        completion_script = ""

    console.print(completion_script)

    import sys as sys_module
    sys_module.stderr.write(
        f"\nAdd the above line to your {shell} config file\n"
    )
    sys_module.stderr.write(
        f"~/.bashrc, ~/.bash_profile, ~/.zshrc, ~/.config/fish/config.fish\n"
    )


def main():
    """Main entry point with argcomplete support."""
    # Enable argcomplete for Click commands
    if argcomplete is not None:
        # Register argcomplete for Click's argument parser
        argcomplete.autocomplete(cli, always_complete_options=True)

    try:
        cli(obj={}, standalone_mode=False)
    except click.exceptions.Exit:
        pass
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Fatal error:[/red] {rich_escape(str(e))}")
        sys.exit(1)


if __name__ == "__main__":
    main()
