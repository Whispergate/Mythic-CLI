"""Interactive shell for Mythic CLI using Rich."""

import os
import shlex
import threading
import time
import pyfiglet
from datetime import datetime
from typing import Optional, Dict, Set, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter, NestedCompleter, Completer, CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .client import MythicClient, MythicAPIException
from .commands import CommandHandler
from .config import ConfigManager
from .themes import get_prompt_style_map, get_syntax_theme, normalize_theme_name

console = Console()


class DynamicCompleter(Completer):
    """Custom completer for dynamic callback and operation IDs."""

    def __init__(self, client: MythicClient, command_completers: Dict[str, Completer]):
        """Initialize with client and nested completers."""
        self.client = client
        self.command_completers = command_completers
        self._callback_ids_cache: Set[str] = set()
        self._operation_ids_cache: Set[str] = set()

    def _refresh_caches(self):
        """Update cached IDs from the client."""
        try:
            if self.client.api_token:
                # Cache callback IDs
                callbacks = self.client.get_callbacks()
                self._callback_ids_cache = {str(cb.get('id', '')) for cb in callbacks}

                # Cache operation IDs
                operations = self.client.get_operations()
                self._operation_ids_cache = {str(op.get('id', '')) for op in operations}
        except Exception:
            pass  # Silently fail if unable to fetch

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        """Get completions based on command context."""
        text = document.text_before_cursor
        parts = text.split()

        if not parts:
            return

        # Get the main command
        main_cmd = parts[0].lower()

        # For multi-word commands, check in nested completers
        if main_cmd in self.command_completers:
            completer = self.command_completers[main_cmd]

            # Get the remaining text after the main command
            remaining = text[len(main_cmd):].lstrip()
            remaining_doc = Document(remaining, len(remaining))

            # Yield completions from the nested completer
            yield from completer.get_completions(remaining_doc, complete_event)
        else:
            # For single-word commands or first word, use main completer
            main_completer = self.command_completers.get('_main_')
            if main_completer:
                yield from main_completer.get_completions(document, complete_event)


class MythicShell:
    """Interactive shell for Mythic CLI."""

    def __init__(self, config_manager: ConfigManager):
        """Initialize the shell.

        Args:
            config_manager: ConfigManager instance.
        """
        self.config_manager = config_manager
        self.config_manager.config.tui_theme = normalize_theme_name(
            self.config_manager.config.tui_theme
        )
        self.client = MythicClient(config_manager.config)
        self.command_handler = CommandHandler(self.client)
        self.running = False
        self.syntax_theme = get_syntax_theme(self.config_manager.config.tui_theme)

        # Callback monitoring state
        self.active_callbacks_count = 0
        self.total_callbacks_count = 0
        self._known_callback_ids = set()
        self._monitor_lock = threading.Lock()
        self._monitoring = False

        # Load current user if already authenticated via config
        if self.client.api_token:
            try:
                username = self.client.get_current_user()
                if username:
                    self.client.current_user = username
            except Exception:
                pass  # Silently fail if we can't get user info

            # Start callback monitoring
            self._start_callback_monitoring()

        # Build nested completers for subcommand support
        nested_completers = {
            # Commands with subcommands
            "callback": NestedCompleter({
                "interact": WordCompleter(["<callback_id>"], ignore_case=True),
                "update": WordCompleter(["<id> <key> <value>"], ignore_case=True),
            }),
            "callback-update": WordCompleter(["<id> <key> <value>"], ignore_case=True),
            "callback-hide": WordCompleter(["<id> [id ...]", "list", "clear"], ignore_case=True),
            "callback-unhide": WordCompleter(["<id> [id ...]"], ignore_case=True),
            "task": WordCompleter(
                [
                    "<callback_id> <command> [params]",
                    "<callback_id> upload ./local.bin C:\\\\Temp\\\\local.bin",
                ],
                ignore_case=True,
            ),
            "tasks": WordCompleter(["[callback_id]"], ignore_case=True),
            "task-output": WordCompleter(["<task_id>"], ignore_case=True),
            "payload-create": WordCompleter(
                ["<os> <payload_type>"],
                ignore_case=True,
            ),
            "payload-download": WordCompleter(
                ["<uuid> <output_file>"],
                ignore_case=True,
            ),
            "profile": NestedCompleter({
                "update": WordCompleter(["<name> <params_json>"], ignore_case=True),
            }),
            "profile-update": WordCompleter(["<name> <params_json>"], ignore_case=True),
            "file-download": WordCompleter(["<file_id> <output_path>"], ignore_case=True),
            "file-upload": WordCompleter(["<local_path>"], ignore_case=True),
            "credential-add": WordCompleter(["[json]"], ignore_case=True),
            "operation": WordCompleter(["<id>"], ignore_case=True),
            "login": WordCompleter(["[username] [password]"], ignore_case=True),

            # Commands without subcommands
            "help": WordCompleter(["[command]"], ignore_case=True),
            "callbacks": WordCompleter([], ignore_case=True),
            "watch-callbacks": WordCompleter(["[interval_seconds]"], ignore_case=True),
            "payloads": WordCompleter([], ignore_case=True),
            "operations": WordCompleter([], ignore_case=True),
            "profiles": WordCompleter([], ignore_case=True),
            "credentials": WordCompleter([], ignore_case=True),
            "files": WordCompleter([], ignore_case=True),
            "config": WordCompleter([], ignore_case=True),
            "status": WordCompleter([], ignore_case=True),
            "operation-info": WordCompleter([], ignore_case=True),
            "exit": WordCompleter([], ignore_case=True),
            "quit": WordCompleter([], ignore_case=True),
            "clear": WordCompleter([], ignore_case=True),
            "cls": WordCompleter([], ignore_case=True),
        }

        # Main commands completer
        main_commands = list(nested_completers.keys())
        main_completer = WordCompleter(main_commands, ignore_case=True, sentence=True)

        # Store for dynamic completer
        nested_completers['_main_'] = main_completer

        # Create the dynamic completer
        self.completer = DynamicCompleter(self.client, nested_completers)

        # Prompt style with TUI enhancements
        self.prompt_style = Style.from_dict(
            get_prompt_style_map(self.config_manager.config.tui_theme)
        )

        # History file
        history_file = os.path.expanduser("~/.config/mythic-cli/history")
        os.makedirs(os.path.dirname(history_file), exist_ok=True)
        try:
            os.chmod(os.path.dirname(history_file), 0o700)
        except OSError:
            pass

        # Ensure history file exists with restrictive permissions.
        try:
            open(history_file, "a", encoding="utf-8").close()
            os.chmod(history_file, 0o600)
        except OSError:
            pass

        self.session = PromptSession(
            history=FileHistory(history_file),
            completer=self.completer,
            style=self.prompt_style,
        )

    def print_banner(self) -> None:
        """Print the welcome banner."""
        
        banner = pyfiglet.figlet_format("Mythic", font="caligraphy")
        console.print(f"[bold red]{banner}[/bold red]")
        info = """
Type '[cyan]help[/cyan]' for available commands or '[cyan]exit[/cyan]' to quit.
Server: [yellow]{server}[/yellow]

[dim]Tip: Use Tab to autocomplete commands[/dim]
"""
        console.print(info.format(server=self.client.base_url))

    def get_prompt(self) -> str:
        """Get the prompt string.

        Returns:
            Formatted prompt string.
        """
        user = self.client.current_user if self.client.current_user else "not logged in"
        return f"mythic ({user}) > "

    def _print_maybe_paged(self, renderable: Any) -> None:
        """Print output via pager when it exceeds terminal height."""
        with console.capture() as capture:
            console.print(renderable)

        output = capture.get()
        line_count = output.count("\n") + 1 if output else 0
        threshold = max(console.size.height - 4, 20)

        if line_count >= threshold:
            with console.pager(styles=False):
                console.print(renderable)
            return

        console.print(renderable)

    def parse_command(self, line: str) -> tuple[str, list[str]]:
        """Parse command line into command and arguments.

        Args:
            line: Command line string.

        Returns:
            Tuple of (command, arguments).
        """
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()

        if not parts:
            return "", []

        command = parts[0].lower()
        args = parts[1:]
        return command, args

    def execute_command(self, command: str, args: list[str]) -> None:
        """Execute a command.

        Args:
            command: Command name.
            args: Command arguments.
        """
        # Built-in commands
        if command in ("exit", "quit"):
            self.running = False
            console.print("[yellow]Goodbye![/yellow]")
            return

        if command in ("clear", "cls"):
            console.clear()
            return

        if command == "help":
            self.command_handler.handle_help(args)
            return

        if command == "status":
            self.command_handler.handle_status(args)
            return

        if command == "config":
            self.command_handler.handle_config(args)
            return

        # Authentication required for most commands
        if command == "login":
            username = args[0] if args else None
            password = args[1] if len(args) > 1 else None

            if not username:
                username = console.input("[cyan]Username: [/cyan]")
            if not password:
                from getpass import getpass

                password = getpass("Password: ")

            try:
                if self.client.login(username, password):
                    # Update config with credentials
                    self.config_manager.update_config(
                        username=username, api_key=self.client.api_token
                    )
                    self._start_callback_monitoring()
            except MythicAPIException as e:
                console.print(f"[red]Login failed:[/red] {str(e)}")
            return

        # Commands that require authentication
        if not self.client.api_token:
            console.print("[yellow]⚠[/yellow] Please login first using: [cyan]login[/cyan]")
            return

        # Callback commands
        if command == "callbacks":
            self.command_handler.handle_callbacks(args)
        elif command == "callback-hide":
            self.command_handler.handle_callback_hide(args)
        elif command == "callback-unhide":
            self.command_handler.handle_callback_unhide(args)
        elif command == "watch-callbacks":
            self.command_handler.handle_watch_callbacks(args)
        elif command == "callback":
            self.command_handler.handle_callback(args)
        elif command == "callback-update":
            if len(args) < 3:
                console.print("[red]Usage:[/red] callback-update [id] [key] [value]")
            else:
                try:
                    callback_id = int(args[0])
                    key = args[1]
                    value = args[2]

                    # Map common field names to actual GraphQL field names
                    field_mapping = {
                        'sleep': 'sleep_info',
                        'description': 'description',
                        'sleep_info': 'sleep_info',
                    }

                    # Use mapped field name or original if not in mapping
                    graphql_field = field_mapping.get(key, key)

                    result = self.client.update_callback(callback_id, **{graphql_field: value})
                    console.print(f"[green]✅[/green] Callback updated")
                except ValueError:
                    console.print("[red]Invalid callback ID[/red]")
                except Exception as e:
                    console.print(f"[red]Error:[/red] {str(e)}")

        # Task commands
        elif command == "tasks":
            self.command_handler.handle_tasks(args)
        elif command == "task":
            self.command_handler.handle_task(args)
        elif command == "task-output":
            self.command_handler.handle_task_output(args)

        # Payload commands
        elif command == "payloads":
            self.command_handler.handle_payloads(args)
        elif command == "payload-create":
            self.command_handler.handle_payload_create(args)
        elif command == "payload-download":
            if len(args) < 2:
                console.print("[red]Usage:[/red] payload-download [uuid] [output_file]")
            else:
                try:
                    uuid = args[0]
                    output_file = args[1]
                    data = self.client.download_payload(uuid)
                    with open(output_file, "wb") as f:
                        f.write(data)
                    console.print(f"[green]✅[/green] Payload saved to {output_file}")
                except Exception as e:
                    console.print(f"[red]Error:[/red] {str(e)}")

        # Operation commands
        elif command == "operations":
            self.command_handler.handle_operations(args)
        elif command == "operation":
            if not args:
                console.print("[red]Usage:[/red] operation [id]")
            else:
                try:
                    op_id = int(args[0])
                    self.client.set_current_operation(op_id)
                    console.print(f"[green]✅[/green] Current operation set to {op_id}")
                except Exception as e:
                    console.print(f"[red]Error:[/red] {str(e)}")
        elif command == "operation-info":
            try:
                op = self.client.get_current_operation()
                import json
                from rich.syntax import Syntax

                syntax = Syntax(json.dumps(op, indent=2), "json", theme=self.syntax_theme)
                self._print_maybe_paged(syntax)
            except Exception as e:
                console.print(f"[red]Error:[/red] {str(e)}")

        # C2 Profile commands
        elif command == "profiles":
            self.command_handler.handle_profiles(args)
        elif command == "profile":
            if not args:
                console.print("[red]Usage:[/red] profile [name]")
            else:
                try:
                    import json
                    from rich.syntax import Syntax

                    profile = self.client.get_c2_profile(args[0])
                    syntax = Syntax(json.dumps(profile, indent=2), "json", theme=self.syntax_theme)
                    self._print_maybe_paged(syntax)
                except Exception as e:
                    console.print(f"[red]Error:[/red] {str(e)}")
        elif command == "profile-update":
            if len(args) < 2:
                console.print("[red]Usage:[/red] profile-update [name] [params_json]")
            else:
                try:
                    import json

                    name = args[0]
                    params = json.loads(" ".join(args[1:]))
                    self.client.update_c2_profile(name, params)
                    console.print(f"[green]✅[/green] Profile {name} updated")
                except Exception as e:
                    console.print(f"[red]Error:[/red] {str(e)}")

        # File commands
        elif command == "files":
            self.command_handler.handle_files(args)
        elif command == "file-upload":
            if not args:
                console.print("[red]Usage:[/red] file-upload [path]")
            else:
                try:
                    file_path = args[0]
                    with open(file_path, "rb") as f:
                        data = f.read()
                    result = self.client.upload_file(os.path.basename(file_path), data)
                    console.print(f"[green]✅[/green] File uploaded")
                except Exception as e:
                    console.print(f"[red]Error:[/red] {str(e)}")
        elif command == "file-download":
            self.command_handler.handle_file_download(args)

        # Credential commands
        elif command == "credentials":
            self.command_handler.handle_credentials(args)
        elif command == "credential-add":
            self.command_handler.handle_credential_add(args)

        else:
            console.print(f"[red]Unknown command:[/red] {command}")
            console.print("Type '[cyan]help[/cyan]' for available commands")

    def _start_callback_monitoring(self) -> None:
        """Start the background callback monitoring thread."""
        if self._monitoring:
            return  # Already monitoring

        self._monitoring = True
        # Initialize known callback IDs
        try:
            callbacks = self.client.get_callbacks()
            with self._monitor_lock:
                self._known_callback_ids = {cb.get("id") for cb in callbacks if cb.get("id")}
            console.print(f"[*] Initialized monitoring with {len(self._known_callback_ids)} known callbacks")
        except Exception as e:
            console.print(f"[!] Error initializing monitoring: {e}")

        monitor_thread = threading.Thread(target=self._monitor_callbacks_loop, daemon=False)
        monitor_thread.name = "CallbackMonitor"
        monitor_thread.start()
        console.print("[*] Callback monitoring thread started (non-daemon)")

    def _monitor_callbacks_loop(self) -> None:
        """Background thread loop that monitors callbacks."""
        console.print("[*] [MONITOR] Thread loop started")
        poll_count = 0
        while self.running and self._monitoring:
            try:
                poll_count += 1
                if poll_count % 10 == 1:  # Log every 10 polls (every 30 seconds)
                    console.print(f"[*] [MONITOR] Poll #{poll_count}")
                self._update_callback_counts()
                time.sleep(3)  # Update every 3 seconds
            except Exception as e:
                console.print(f"[!] [MONITOR] Error in poll: {e}")
                time.sleep(5)  # Back off on error

    def _update_callback_counts(self) -> None:
        """Update callback counts from the server and alert on new callbacks."""
        try:
            callbacks = self.client.get_callbacks()
            total = len(callbacks)
            active = sum(
                1 for cb in callbacks
                if self.command_handler._is_callback_active(cb)
            )

            current_ids = {cb.get("id") for cb in callbacks if cb.get("id")}

            with self._monitor_lock:
                self.total_callbacks_count = total
                self.active_callbacks_count = active

                # Check for new callbacks
                new_ids = current_ids - self._known_callback_ids
                if new_ids:
                    print(f"[!] [MONITOR] Found {len(new_ids)} new callback(s): {new_ids}")

                if new_ids:
                    # Get details about new callbacks
                    new_callbacks = [cb for cb in callbacks if cb.get("id") in new_ids]
                    new_callbacks.sort(key=lambda x: x.get("id", 0), reverse=True)

                    # Create alert panel
                    alert_text = ""
                    for cb in new_callbacks:
                        pid = cb.get("pid", "")
                        process_name = cb.get("process_name", "")
                        process_display = f"{pid} ({process_name})" if pid and process_name else process_name or str(pid) if pid else ""

                        alert_text += f"\n  ID: [cyan]{cb.get('id')}[/cyan] | User: [green]{cb.get('user', 'N/A')}[/green] | Host: [yellow]{cb.get('host', 'N/A')}[/yellow] | Process: [white]{process_display}[/white]"

                    # Display alert with distinctive styling
                    alert = Panel(
                        f"[bold green]✅ New Callback(s) Registered{alert_text}[/bold green]",
                        border_style="green",
                        title="[bold green]ALERT[/bold green]",
                        expand=False,
                    )
                    console.print(alert)

                # Update known IDs
                self._known_callback_ids = current_ids
        except Exception as e:
            print(f"[!] [MONITOR] Exception in _update_callback_counts: {e}")
            pass

    def _stop_callback_monitoring(self) -> None:
        """Stop the background monitoring thread."""
        self._monitoring = False

    def run(self) -> None:
        """Run the interactive shell."""
        self.running = True

        # Attempt startup authentication from saved credentials when no API token exists.
        has_creds = self.config_manager.config.username and self.config_manager.config.password
        if not self.client.api_token and has_creds:
            print("[*] Attempting startup authentication...")
            try:
                if self.client.login(self.config_manager.config.username, self.config_manager.config.password):
                    # Persist refreshed token for subsequent launches.
                    self.config_manager.update_config(api_key=self.client.api_token)
                    print("[+] Startup authentication successful")
                else:
                    print("[-] Startup authentication returned False")
            except MythicAPIException as e:
                print(f"[-] Startup auth failed: {str(e)}")
                pass

        # Ensure callback monitoring is active whenever authenticated.
        if self.client.api_token:
            self._start_callback_monitoring()
        else:
            print("[-] Not authenticated - callback monitoring disabled")

        self.print_banner()

        while self.running:
            try:
                # Get user input
                line = self.session.prompt(self.get_prompt())

                # Skip empty lines
                if not line.strip():
                    continue

                # Parse and execute command
                command, args = self.parse_command(line)
                if command:
                    self.execute_command(command, args)

            except KeyboardInterrupt:
                console.print("\n[yellow]Use 'exit' to quit[/yellow]")
                continue
            except EOFError:
                self.running = False
                console.print("\n[yellow]Goodbye![/yellow]")
            except Exception as e:
                console.print(f"[red]Error:[/red] {str(e)}")

        # Cleanup
        self._stop_callback_monitoring()
        self.client.close()
