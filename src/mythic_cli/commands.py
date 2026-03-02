"""Command handlers for the Mythic CLI."""

import base64
import binascii
import json
import os
import shlex
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from .client import MythicClient, MythicAPIException
from .themes import get_supported_themes, get_syntax_theme, normalize_theme_name

console = Console()


class CommandHandler:
    """Handles CLI commands."""

    def __init__(self, client: MythicClient):
        """Initialize command handler.

        Args:
            client: MythicClient instance.
        """
        self.client = client
        self.syntax_theme = get_syntax_theme(
            normalize_theme_name(self.client.config.tui_theme)
        )
        self.hidden_callback_ids: Set[int] = set()

    def _format_timestamp(self, timestamp_str: Optional[str]) -> str:
        """Format ISO timestamp to readable format in local timezone.

        Server returns timestamps in UTC. This method converts them to local time.
        """
        if not timestamp_str:
            return ""
        try:
            # Parse the UTC timestamp
            # If no timezone info, assume UTC
            if timestamp_str.endswith('Z'):
                dt_utc = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            elif '+' not in timestamp_str and timestamp_str.count('-') == 2:
                # No timezone suffix, assume UTC
                dt_utc = datetime.fromisoformat(timestamp_str).replace(tzinfo=timezone.utc)
            else:
                dt_utc = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))

            # Convert to local time
            dt_local = dt_utc.astimezone()
            return dt_local.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return str(timestamp_str)

    def _extract_sleep_info(self, callback: Dict[str, Any]) -> str:
        """Extract sleep info from callback data across GraphQL field variations."""
        candidates = [
            callback.get("sleep_info"),
            callback.get("sleepInfo"),
            callback.get("sleep"),
            callback.get("sleep_interval"),
            callback.get("sleep_seconds"),
        ]

        for value in candidates:
            if value is None:
                continue

            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
                continue

            if isinstance(value, (dict, list)):
                return json.dumps(value)

            return str(value)

        return ""


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

    def _decode_response_text(self, response_text: str) -> str:
        """Decode base64 task output when applicable."""
        if not response_text:
            return response_text

        candidate = "".join(response_text.strip().split())
        if not candidate:
            return response_text

        # Try base64 decoding; if it fails or looks wrong, return original text.
        try:
            decoded_bytes = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError):
            return response_text

        if not decoded_bytes:
            return response_text

        decoded_text = decoded_bytes.decode("utf-8", errors="replace")

        # Only use decoded text if it looks like readable output.
        printable_chars = sum(ch.isprintable() or ch in "\n\r\t" for ch in decoded_text)
        if printable_chars / max(len(decoded_text), 1) < 0.8:
            return response_text

        return decoded_text

    def _build_task_params(self, command: str, raw_args: str) -> Optional[Any]:
        """Build Mythic task params from callback-shell style input.

        Supports JSON input when provided, plus common text-style command formats.
        For commands that take no parameters, return None.
        
        Supports command augmentation from agents like forge (BOF/Assembly wrapper).
        Forge commands are dynamically augmented to agents and appear in loadedcommands.
        """
        raw = (raw_args or "").strip()
        
        # Commands that take no parameters - return None immediately
        # These will be sent without any params to the agent
        no_param_commands = {
            "ps", "status", "exit", "help", "whoami", "pwd", "getuid", "geteuid",
            "getgid", "getegid", "uname", "id", "groups", "hostname", "ifconfig",
            "ipconfig", "netstat", "arp", "route", "finger", "who", "w", "last",
            "uptime", "date", "cal", "df", "du", "free", "top", "ps", "processes",
            # Forge command augmenter management commands (when no args provided)
            "forge_support", "forge_collections",
        }
        
        if command in no_param_commands and not raw:
            # These commands don't take any parameters
            return None
        
        if not raw:
            # For commands that might take optional params
            if command == "ls":
                return {"path": "."}
            # For other commands with no args, return None
            return None

        # If user supplied JSON, use it as-is.
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Forge command augmenter command mappings
        if command == "forge_collections":
            return {"collectionName": raw}
        if command == "forge_download":
            try:
                parts = shlex.split(raw)
            except ValueError:
                parts = raw.split()
            if len(parts) >= 2:
                return {"collectionName": parts[0], "commandName": " ".join(parts[1:])}
            return {"collectionName": raw}
        if command == "forge_register":
            try:
                parts = shlex.split(raw)
            except ValueError:
                parts = raw.split()
            if len(parts) >= 2:
                remove = any(p.lower() in {"-remove", "--remove", "remove", "true", "1"} for p in parts[2:])
                return {
                    "collectionName": parts[0],
                    "commandName": parts[1],
                    "remove": remove,
                }
            return raw

        # Common commands that take a single text arg mapped to known Mythic arg names.
        if command in {"shell", "run", "execute"}:
            return {"command": raw}
        if command == "download":
            return {"path": raw}
        if command == "upload":
            try:
                parts = shlex.split(raw)
            except ValueError:
                parts = raw.split()
            if len(parts) == 1:
                return {"file": parts[0]}
            if len(parts) >= 2:
                return {"file": parts[0], "path": " ".join(parts[1:])}
            return {"file": raw}
        if command == "ls":
            return {"path": raw}
        if command == "cd":
            return {"directory": raw}
        if command == "help":
            return raw
        if command == "sleep":
            # sleep <seconds> [jitter]
            try:
                parts = raw.split()
                if len(parts) == 1:
                    return {"seconds": int(parts[0])}
                if len(parts) >= 2:
                    return {"seconds": int(parts[0]), "jitter": float(parts[1])}
            except (ValueError, TypeError):
                pass
            return {"seconds": raw}

        # Xenon socks convenience form: "socks start 1080"
        if command == "socks":
            try:
                parts = raw.split()
                if len(parts) >= 2:
                    action = parts[0].lower()
                    port = int(parts[1])
                    return {"action": action, "port": port}
            except (ValueError, TypeError):
                pass

        # Fallback: pass raw text through for agent-side parsing.
        return raw

    def _upload_task_file(self, local_path: str) -> str:
        """Upload a local file and return Mythic's `agent_file_id` UUID."""
        with open(local_path, "rb") as f:
            data = f.read()
        result = self.client.upload_file(
            os.path.basename(local_path),
            data,
            comment="Uploaded as part of tasking via mythic-cli",
        )
        file_uuid = result.get("agent_file_id")
        if not isinstance(file_uuid, str) or not file_uuid:
            raise MythicAPIException("Upload succeeded but no agent_file_id was returned")
        return file_uuid

    def _prepare_task_submission(
        self, command: str, params: Optional[Any]
    ) -> Tuple[Optional[Any], List[str]]:
        """Prepare params and task file UUID list for createTask.

        Supports:
        - Explicit file marker syntax in any string param: "@/path/to/file"
        - `upload` command convenience: if param key is `file` and value is a local path,
          upload it and replace with returned file UUID.
        """
        uploaded_files: List[str] = []

        def convert(value: Any, key: Optional[str] = None) -> Any:
            if isinstance(value, dict):
                return {k: convert(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [convert(v, key) for v in value]
            if not isinstance(value, str):
                return value

            # Generic explicit upload marker: "@/tmp/file.bin"
            if value.startswith("@") and len(value) > 1:
                candidate = value[1:]
                if os.path.isfile(candidate):
                    file_uuid = self._upload_task_file(candidate)
                    uploaded_files.append(file_uuid)
                    return file_uuid
                return value

            # Convenience conversion for common upload command style.
            if command == "upload" and key == "file" and os.path.isfile(value):
                file_uuid = self._upload_task_file(value)
                uploaded_files.append(file_uuid)
                return file_uuid

            return value

        converted_params = convert(params)
        return converted_params, uploaded_files

    def _wait_for_task_and_print_output(self, task_id: int, timeout: int = 30) -> None:
        """Wait for a task to finish and print any output to the terminal."""
        seen_output_ids = set()
        start = time.time()

        while time.time() - start < timeout:
            task = self.client.get_task(task_id)
            outputs = self.client.get_task_output(task_id)

            for output in outputs:
                output_id = output.get("id")
                if output_id in seen_output_ids:
                    continue
                seen_output_ids.add(output_id)

                response_text = (
                    output.get("response")
                    or output.get("response_text")
                    or output.get("response_escape")
                    or ""
                )
                response_text = self._decode_response_text(response_text)
                timestamp = output.get("timestamp", "")
                if response_text:
                    console.print(
                        Panel(
                            response_text,
                            title=f"Task {task_id} Output at {timestamp}",
                            border_style="green",
                        )
                    )

            if task and task.get("completed"):
                if not outputs:
                    console.print("[dim]Task completed with no output[/dim]")
                return

            time.sleep(1)

        console.print(
            f"[dim]Task {task_id} still running. Use 'task-output {task_id}' to check later.[/dim]"
        )

    def handle_help(self, args: List[str]) -> None:
        """Display help information."""
        if args and args[0]:
            # Specific command help
            self._show_command_help(args[0])
            return

        help_text = """
[bold cyan]Mythic CLI - Interactive Command Line Interface[/bold cyan]

[dim]Type 'help <command>' for detailed info about a specific command[/dim]

[bold yellow]🔐 Authentication[/bold yellow]
  [cyan]login[/cyan] [username] [password]     Login to Mythic server (tries multiple endpoints)
  [cyan]status[/cyan]                          Show connection status and server info
  [cyan]config[/cyan]                          Display current configuration

[bold yellow]📡 Callbacks[/bold yellow]
  [cyan]callbacks[/cyan]                       List all active callbacks with details
  [cyan]watch-callbacks[/cyan] [interval]      Watch for new callbacks (default: 5 sec interval)
  [cyan]callback[/cyan] <id>                   Show detailed info for a specific callback
  [cyan]callback interact[/cyan] <id>          Enter interactive mode with callback
  [cyan]callback-update[/cyan] <id> <key> <value>  Update callback property (e.g., description, sleep)
    [cyan]callback-hide[/cyan] <id>|list|clear   Hide callback(s) from callbacks table
    [cyan]callback-unhide[/cyan] <id>            Unhide callback(s) in callbacks table

[bold yellow]📋 Tasks[/bold yellow]
  [cyan]task[/cyan]                            Create task interactively (guided prompts)
    [cyan]task[/cyan] <callback_id> <cmd> [params] Create task with command
  [cyan]tasks[/cyan] [callback_id]             List tasks (all or for specific callback)
  [cyan]task-output[/cyan] <task_id>           Show output from a completed task

[bold yellow]🎯 Payloads[/bold yellow]
  [cyan]payloads[/cyan]                        List all generated payloads
  [cyan]payload-create[/cyan] <config_json>    Generate a new payload
  [cyan]payload-download[/cyan] <uuid> <file>  Download payload to local file

[bold yellow]🌐 C2 Profiles[/bold yellow]
  [cyan]profiles[/cyan]                        List available C2 profiles
  [cyan]profile[/cyan] <name>                  Show profile configuration details
  [cyan]profile-update[/cyan] <name> <json>    Update profile parameters

[bold yellow]📁 Files[/bold yellow]
  [cyan]files[/cyan]                           List tracked files (uploads/downloads)
  [cyan]file-upload[/cyan] <path>              Upload file to Mythic server
  [cyan]file-download[/cyan] <uuid> <output>   Download file from Mythic server

[bold yellow]🔑 Credentials[/bold yellow]
  [cyan]credentials[/cyan]                     List all stored credentials
  [cyan]credential-add[/cyan]                  Add credential interactively
  [cyan]credential-add[/cyan] <json>           Add credential from JSON

[bold yellow]⚙️  Operations[/bold yellow]
  [cyan]operations[/cyan]                      List all operations
  [cyan]operation[/cyan] <id>                  Switch to different operation
  [cyan]operation-info[/cyan]                  Show current operation details

[bold yellow]🛠️  General[/bold yellow]
  [cyan]help[/cyan] [command]                  Show this help or help for specific command
  [cyan]clear[/cyan] / [cyan]cls[/cyan]                     Clear the screen
  [cyan]exit[/cyan] / [cyan]quit[/cyan]                     Exit Mythic CLI

[bold green]Quick Examples:[/bold green]
  [dim]# Create task interactively (no JSON needed)[/dim]
  task                             [dim]# Follow the prompts[/dim]

  [dim]# Or create task with inline parameters[/dim]
  task 1 shell {{"command": "whoami"}}

    [dim]# Upload local file as part of tasking (direct text input)[/dim]
    task 3 upload ./payload.exe "C:\\Temp\\payload.exe"

  [dim]# Add credential interactively[/dim]
  credential-add                   [dim]# Follow the prompts[/dim]

[bold cyan]Tips:[/bold cyan]
  - Use Tab for command completion
  - Use arrows for command history
  - Ctrl+C cancels input (doesn't exit - use 'exit' command)
  - Run commands without arguments for interactive prompts

[bold cyan]Troubleshooting:[/bold cyan]
  - Login fails? Try: [cyan]python debug_connection.py <server> <username>[/cyan]
  - Wrong server? Use: [cyan]mythic config-set --server <url>[/cyan]
  - SSL issues? Add: [cyan]--no-ssl-verify[/cyan] flag when starting mythic
"""
        self._print_maybe_paged(Panel(help_text, title="Mythic CLI Help", border_style="cyan", padding=(1, 2)))

    def _show_command_help(self, command: str) -> None:
        """Show detailed help for a specific command."""
        help_map = {
            "login": """[cyan]login[/cyan] [username] [password]

If credentials omitted, you'll be prompted.

[yellow]Examples:[/yellow]
  login admin mypassword
  login mythic_admin    [dim]# Will prompt for password[/dim]
  login                 [dim]# Will prompt for both[/dim]

[yellow]Common Issues:[/yellow]
  - Status 400? Run: python debug_connection.py <server> <user>
  - Default username is often 'mythic_admin' not 'admin'
  - Use --no-ssl-verify if server has self-signed cert""",
            "task": """[cyan]task[/cyan] [callback_id] [command] [params]

Create and execute a task on a callback.

[yellow]Interactive Mode (No Arguments):[/yellow]
  task                              [dim]# Follow guided prompts[/dim]

[yellow]Command Line Mode:[/yellow]
  task 1 shell {{"command": "whoami"}}
  task 2 download {{"path": "/etc/passwd"}}
    task 3 upload ./payload.exe "C:\\\\Temp\\\\payload.exe"
  task 1 ps {{}}
  task 5 shell                      [dim]# Minimal params[/dim]

[yellow]Common Commands:[/yellow]
  - shell: Execute shell command
  - download: Download file from target
  - upload: Upload file to target
  - ps: List processes
  - ls: List directory contents
  - cd: Change directory

[yellow]Tips:[/yellow]
  - Interactive mode guides you through common tasks
  - Use double quotes in JSON for command line mode
    - Upload supports direct text: upload <local_path> <remote_path>
    - Local upload paths are auto-uploaded and associated with createTask(files=[...])
    - JSON and @<path> markers still work for advanced cases
  - Use 'tasks' to list all tasks
  - Use 'task-output <id>' to see results""",

            "callbacks": """[cyan]callbacks[/cyan]

List all active callbacks with detailed information:
  - Callback ID and Display ID
  - Username and hostname
  - IP address and port
  - Process name and PID
  - Operating system
  - Last check-in time

[yellow]Examples:[/yellow]
  callbacks                         [dim]# List all callbacks[/dim]
  callback interact 1               [dim]# Interactive mode with callback 1[/dim]
  callback-update 1 description "Domain Controller"

[yellow]Interactive Mode:[/yellow]
  callback interact <id>            Enter interactive session

  Once in interactive mode:
    shell whoami                    [dim]# Run commands directly[/dim]
    download /etc/passwd            [dim]# No callback ID needed[/dim]
    tasks                           [dim]# View tasks for this callback[/dim]
    back                            [dim]# Return to main shell[/dim]

[yellow]Related Commands:[/yellow]
  - callback <id>: Show detailed callback info
  - callback interact <id>: Enter interactive mode
  - callback <id>: Show detailed callback info
  - callback-update <id> <key> <value>: Update callback properties
    - callback-hide <id>|list|clear: Hide/list/clear hidden callbacks for table view
    - callback-unhide <id>: Unhide callbacks for table view
  - tasks <callback_id>: Show tasks for specific callback""",

                        "callback-hide": """[cyan]callback-hide[/cyan] <id> [id ...] | list | clear

Hide callback IDs from the [cyan]callbacks[/cyan] table output in this CLI session.

[yellow]Examples:[/yellow]
    callback-hide 6
    callback-hide 4 5 6
    callback-hide list
    callback-hide clear

[yellow]Notes:[/yellow]
    - This only affects local table display (does not modify server state).
    - Hidden IDs are kept for the current CLI session only.
    - Use [cyan]callback-unhide <id>[/cyan] or [cyan]callback-hide clear[/cyan] to restore visibility.""",

                        "callback-unhide": """[cyan]callback-unhide[/cyan] <id> [id ...]

Unhide callback IDs previously hidden from the [cyan]callbacks[/cyan] table.

[yellow]Examples:[/yellow]
    callback-unhide 6
    callback-unhide 4 5 6""",

            "payload-create": """[cyan]payload-create[/cyan] [os] [payload_type]

Create a new payload interactively with guided parameter selection.

[yellow]Interactive Mode (Recommended):[/yellow]
  payload-create                    [dim]# Shows list of available payload types[/dim]
  payload-create windows apollo     [dim]# Directly create Apollo payload for Windows[/dim]

[yellow]How It Works:[/yellow]
  1. Select or specify the payload type
  2. Build parameters are displayed with descriptions
  3. Provide values for each parameter (or use defaults)
  4. Payload is generated automatically
  5. Receive UUID for downloading

[yellow]Examples:[/yellow]
  payload-create                    [dim]# Interactive selection[/dim]
  payload-create windows merlin     [dim]# Create Merlin for Windows[/dim]
  payload-create linux sliver       [dim]# Create Sliver for Linux[/dim]

[yellow]After Creation:[/yellow]
  payload-download <uuid> agent.exe [dim]# Download the generated payload[/dim]
  payloads                          [dim]# List all payloads[/dim]

[yellow]Supported Payload Types:[/yellow]
  - apollo: Windows C2 agent
  - merlin: Cross-platform Go agent
  - sliver: Modern Go agent
  - hannibal: Windows shellcode agent
  - forge: Command augmenter (BOF/Assembly collections for other agents)

[dim]All parameters are prompted for interactively - no JSON needed![/dim]""",

            "payload-download": """[cyan]payload-download[/cyan] <uuid> <output_file>

Download a generated payload from Mythic to your local filesystem.

[yellow]Examples:[/yellow]
  payload-download e2e8e890-db0b-4f... agent.exe
  payload-download 89006164-66f3-43... /tmp/payload.bin

[yellow]Tips:[/yellow]
  - Use 'payloads' to list all available payloads and their UUIDs
  - You can use partial UUIDs as shown in the payloads list
  - Downloaded payloads are ready to deploy on target systems""",

            "status": """[cyan]status[/cyan]

Check connection status to the Mythic server.

[yellow]Examples:[/yellow]
  status                            [dim]# Check server connection[/dim]

[yellow]Output:[/yellow]
  - Server URL
  - Connection status (Connected/Disconnected)
  - Authentication status
  - Current operation (if authenticated)""",

            "config": """[cyan]config[/cyan]

Display current configuration settings.

[yellow]Examples:[/yellow]
  config                            [dim]# Show all configuration[/dim]

[yellow]Config Location:[/yellow]
  ~/.config/mythic-cli/config.json

[yellow]Settings:[/yellow]
  - server_url: Mythic server URL
  - username: Login username
  - verify_ssl: SSL certificate verification (true/false)
  - current_operation_id: Active operation ID""",

            "callback": """[cyan]callback[/cyan] <id>

Show detailed information for a specific callback.

[yellow]Examples:[/yellow]
  callback 1                        [dim]# Show callback 1 details[/dim]
  callback interact 1               [dim]# Enter interactive mode[/dim]

[yellow]Output:[/yellow]
  - Full callback details in JSON format
  - Agent information (user, host, process)
  - Network information (IP, port)
  - System information (OS, architecture)
  - Status and timing information

[yellow]Related Commands:[/yellow]
  - callbacks: List all callbacks
  - callback interact <id>: Interactive mode
  - callback-update <id> <key> <value>: Update properties""",

            "callback-update": """[cyan]callback-update[/cyan] <id> <key> <value>

Update properties of a callback.

[yellow]Examples:[/yellow]
  callback-update 1 description "Domain Controller"
  callback-update 2 sleep 60

[yellow]Common Properties:[/yellow]
  - description: Human-readable description
  - sleep (or sleep_info): Sleep interval in seconds

[yellow]Tips:[/yellow]
  - Use 'callback <id>' to see current values before updating
  - Changes take effect immediately
  - Some properties may require agent support""",

            "task-output": """[cyan]task-output[/cyan] <task_id>

Display the output/results from a specific task.

[yellow]Examples:[/yellow]
  task-output 5                     [dim]# Show output from task 5[/dim]
  task-output 12                    [dim]# View task 12 results[/dim]

[yellow]Output Types:[/yellow]
  - Command output (stdout/stderr)
  - File download status
  - Task completion status
  - Error messages

[yellow]Tips:[/yellow]
  - Use 'tasks <callback_id>' to see all tasks and their IDs
  - Output updates as tasks complete
  - Long output is formatted for readability""",

            "operation": """[cyan]operation[/cyan] <id>

Switch to a different operation context.

[yellow]Examples:[/yellow]
  operation 1                       [dim]# Switch to operation 1[/dim]
  operation 2                       [dim]# Switch to operation 2[/dim]

[yellow]Notes:[/yellow]
  - All callbacks, tasks, and credentials are operation-specific
  - Use 'operations' to list all available operations
  - Use 'operation-info' to see current operation details
  - Requires appropriate permissions""",

            "operation-info": """[cyan]operation-info[/cyan]

Show detailed information about the current operation.

[yellow]Examples:[/yellow]
  operation-info                    [dim]# Display current operation[/dim]

[yellow]Output:[/yellow]
  - Operation name and ID
  - Creation date
  - Active status
  - Admin users
  - Operation-specific settings

[yellow]Related Commands:[/yellow]
  - operations: List all operations
  - operation <id>: Switch operations""",

            "profile": """[cyan]profile[/cyan] <name>

Show detailed information about a specific C2 profile.

[yellow]Examples:[/yellow]
  profile http                      [dim]# Show HTTP profile details[/dim]
  profile smb                       [dim]# Show SMB profile config[/dim]

[yellow]Output:[/yellow]
  - Profile name and description
  - Configuration parameters
  - Available settings
  - Current values

[yellow]Related Commands:[/yellow]
  - profiles: List all C2 profiles
  - profile-update <name> <params>: Update profile settings""",

            "profile-update": """[cyan]profile-update[/cyan] <profile_name> <params_json>

Update C2 profile parameters.

[yellow]Examples:[/yellow]
  profile-update http {{"callback_host": "10.0.0.1"}}
  profile-update http {{"callback_port": 443, "encrypted_exchange_check": true}}

Use 'profile <name>' to see current configuration.""",

            "credentials": """[cyan]credentials[/cyan]

List all stored credentials from the operation.

[yellow]Examples:[/yellow]
  credentials                       [dim]# List all credentials[/dim]
  credential-add                    [dim]# Add credential interactively[/dim]

[yellow]Interactive Add:[/yellow]
  credential-add will prompt for:
  - Credential type (plaintext, hash, key, etc.)
  - Realm/Domain
  - Account/Username
  - Credential/Password
  - Comment

[yellow]JSON Mode:[/yellow]
  credential-add {{"type": "plaintext", "account": "admin", "credential": "pass123"}}""",

            "operations": """[cyan]operations[/cyan]

List all operations in Mythic.

[yellow]Examples:[/yellow]
  operations                        [dim]# List all operations[/dim]
  operation-info                    [dim]# Show current operation details[/dim]
  operation 2                       [dim]# Switch to operation 2[/dim]

[yellow]Notes:[/yellow]
  - Each operation maintains separate callbacks, tasks, and credentials
  - You can only interact with resources in your current operation
  - Admin users can see and switch between all operations""",

            "profiles": """[cyan]profiles[/cyan]

List all C2 profiles available in Mythic.

[yellow]Examples:[/yellow]
  profiles                          [dim]# List all C2 profiles[/dim]
  profile http                      [dim]# Show HTTP profile details[/dim]

[yellow]Common Profiles:[/yellow]
  - http: HTTP/HTTPS communication
  - smb: SMB named pipes (P2P)
  - tcp: Direct TCP connections
  - dns: DNS tunneling

[yellow]Notes:[/yellow]
  - C2 profiles define how agents communicate with Mythic
  - Each payload can use multiple profiles
  - Profile parameters are configured during payload creation""",

            "payloads": """[cyan]payloads[/cyan]

List all generated payloads.

[yellow]Examples:[/yellow]
  payloads                          [dim]# List all payloads[/dim]
  payload-download <uuid> agent.exe [dim]# Download a payload[/dim]

[yellow]Notes:[/yellow]
  - Payloads are pre-generated agents
  - Use the Mythic web UI for advanced payload creation
  - UUIDs uniquely identify each payload build""",

            "files": """[cyan]files[/cyan]

List all files tracked by Mythic (uploads and downloads).

[yellow]Examples:[/yellow]
  files                             [dim]# List all files[/dim]
  file-download <id> local.txt      [dim]# Download file from Mythic[/dim]
  file-upload malware.exe           [dim]# Upload file to Mythic[/dim]

[yellow]Notes:[/yellow]
  - Files include both uploads to and downloads from agents
  - Screenshots are tracked as files
  - Large files may take time to transfer""",

  "file-upload": """[cyan]file-upload[/cyan] <local_path>

Upload a file from your local system to Mythic's file repository.

[yellow]Examples:[/yellow]
  file-upload malware.exe           [dim]# Upload executable[/dim]
  file-upload /tmp/payload.bin      [dim]# Upload from absolute path[/dim]
  file-upload ./tools/scanner.sh    [dim]# Upload from relative path[/dim]

[yellow]Use Cases:[/yellow]
  - Stage files for later download to agents
  - Upload tools and utilities
  - Store payloads in Mythic
  - Share files across operation team

[yellow]Notes:[/yellow]
  - Files are stored in Mythic's database
  - Use 'files' to see all uploaded files
  - Downloaded files from agents also appear in files list
  - For task-specific file usage, prefer @<path> in task JSON params""",

            "file-download": """[cyan]file-download[/cyan] <file_id> <output_path>

Download a file from Mythic's repository to your local system.

[yellow]Examples:[/yellow]
  file-download 42 passwords.txt    [dim]# Download file ID 42[/dim]
  file-download 10 /tmp/loot.zip    [dim]# Save to specific path[/dim]

[yellow]Use Cases:[/yellow]
  - Download files exfiltrated from agents
  - Retrieve screenshots
  - Access uploaded tools
  - Export operation artifacts

[yellow]Tips:[/yellow]
  - Use 'files' to list all files and their IDs
  - Files include both uploads and agent downloads
  - Check file metadata before downloading large files""",
        }

        if command in help_map:
            self._print_maybe_paged(Panel(help_map[command], title=f"Help: {command}", border_style="cyan", padding=(1, 2)))
        else:
            console.print(f"[yellow]No detailed help for '{command}'. Try 'help' for all commands.[/yellow]")

    def handle_status(self, args: List[str]) -> None:
        """Display connection status."""
        status_info = Table(title="Connection Status", show_header=False, box=None)
        status_info.add_column("Key", style="cyan")
        status_info.add_column("Value", style="green")

        status_info.add_row("Server URL", self.client.base_url)
        status_info.add_row(
            "Authenticated", "Yes" if self.client.api_token else "No"
        )
        status_info.add_row("SSL Verification", str(self.client.config.verify_ssl))

        console.print(status_info)

    def handle_config(self, args: List[str]) -> None:
        """Display current configuration."""
        config_table = Table(title="Configuration", show_header=True, box=None)
        config_table.add_column("Setting", style="cyan")
        config_table.add_column("Value", style="yellow")

        config_dict = self.client.config.model_dump(exclude={"password"})
        for key, value in config_dict.items():
            if value is not None:
                if key == "api_key":
                    token = str(value)
                    if len(token) > 8:
                        masked = f"{token[:4]}...{token[-4:]}"
                    else:
                        masked = "***"
                    config_table.add_row(key, masked)
                else:
                    config_table.add_row(key, str(value))

        config_table.add_row(
            "supported_tui_themes",
            ", ".join(get_supported_themes()),
        )

        console.print(config_table)

    def _is_callback_active(self, callback: Dict[str, Any]) -> bool:
        """Determine if a callback is active based on the active field and optional time-based validation.

        Primary check: Use the callback's 'active' field from the database
        Fallback: If active field is missing, use time-based check:
                  A callback is active if time since last checkin < (sleep_info + 1 hour)

        Args:
            callback: Callback data dictionary

        Returns:
            True if active, False otherwise
        """


        last_checkin_str = callback.get("last_checkin", "")
        sleep_info_str = self._extract_sleep_info(callback)

        if not last_checkin_str:
            return False

        try:
            # Parse last checkin timestamp (server returns UTC without timezone suffix)
            # Assume naive datetimes are UTC
            if last_checkin_str.endswith('Z'):
                last_checkin_utc = datetime.fromisoformat(last_checkin_str.replace("Z", "+00:00"))
            elif '+' not in last_checkin_str:
                # No timezone suffix, assume UTC
                last_checkin_utc = datetime.fromisoformat(last_checkin_str).replace(tzinfo=timezone.utc)
            else:
                last_checkin_utc = datetime.fromisoformat(last_checkin_str.replace("Z", "+00:00"))

            # Get current time in UTC for comparison
            now_utc = datetime.now(timezone.utc)

            # Calculate seconds since last checkin
            time_since_checkin = (now_utc - last_checkin_utc).total_seconds()

            # Parse sleep info (convert to seconds, default to 0 if empty)
            sleep_seconds = 0
            if sleep_info_str:
                try:
                    sleep_seconds = int(float(sleep_info_str))
                except (ValueError, TypeError):
                    sleep_seconds = 0

            # Allow 10 minute grace period after sleep time
            grace_period = 600  # 10 minutes in seconds
            threshold = sleep_seconds + grace_period

            # If threshold is 0 (no sleep set), consider active if checked in within last 10 minutes
            if threshold == 0:
                threshold = 600

            return time_since_checkin <= threshold

        except (ValueError, AttributeError, TypeError):
            return False

    def handle_callbacks(self, args: List[str]) -> None:
        """List active callbacks."""
        try:
            callbacks = self.client.get_callbacks()

            if not callbacks:
                console.print("[yellow]No callbacks found[/yellow]")
                return

            def _cb_id(value: Any) -> Optional[int]:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            visible_callbacks = [
                cb for cb in callbacks
                if (_cb_id(cb.get("id")) not in self.hidden_callback_ids)
            ]

            hidden_count = len(callbacks) - len(visible_callbacks)

            if not visible_callbacks and hidden_count > 0:
                console.print(
                    f"[yellow]All callbacks are hidden ({hidden_count}).[/yellow] "
                    f"Use [cyan]callback-hide clear[/cyan] or [cyan]callback-unhide <id>[/cyan]."
                )
                return

            table_title = "Active Callbacks"
            if hidden_count > 0:
                table_title = f"Active Callbacks ({hidden_count} hidden)"

            table = Table(title=table_title, show_lines=True)
            table.add_column("ID", style="cyan", justify="right")
            table.add_column("Active", style="magenta")
            table.add_column("OS", style="blue")
            table.add_column("Arch", style="blue")
            table.add_column("User", style="green")
            table.add_column("Host", style="yellow")
            table.add_column("IP", style="blue")
            table.add_column("Process", style="white")
            table.add_column("Agent", style="cyan")
            table.add_column("Last Checkin", style="dim")

            for cb in visible_callbacks:
                # Format process name (pid + process_name)
                pid = cb.get("pid", "")
                process_name = cb.get("process_name", "")
                process_display = f"{pid} ({process_name})" if pid and process_name else process_name or str(pid) if pid else ""
                payload = cb.get("payload") or {}
                payloadtype = payload.get("payloadtype") or {}
                agent_name = payloadtype.get("name", "")

                # Format last checkin timestamp
                last_checkin = self._format_timestamp(cb.get("last_checkin", ""))

                # Determine active status based on sleep time and last checkin
                is_active = self._is_callback_active(cb)
                active_display = "✅" if is_active else "💀"

                table.add_row(
                    str(cb.get("id", "")),
                    active_display,
                    cb.get("os", ""),
                    cb.get("architecture", ""),
                    cb.get("user", ""),
                    cb.get("host", ""),
                    cb.get("ip", ""),
                    process_display,
                    agent_name,
                    last_checkin,
                )

            self._print_maybe_paged(table)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_callback_hide(self, args: List[str]) -> None:
        """Hide callbacks from callbacks table output for current session."""
        if not args:
            console.print("[red]Usage:[/red] callback-hide <id> [id ...] | list | clear")
            return

        action = args[0].strip().lower()
        if action == "list":
            if not self.hidden_callback_ids:
                console.print("[dim]No hidden callbacks[/dim]")
                return
            hidden_list = ", ".join(str(i) for i in sorted(self.hidden_callback_ids))
            console.print(f"[cyan]Hidden callback IDs:[/cyan] {hidden_list}")
            return

        if action == "clear":
            count = len(self.hidden_callback_ids)
            self.hidden_callback_ids.clear()
            console.print(f"[green]✅[/green] Cleared hidden callbacks ({count} restored)")
            return

        ids_to_hide: Set[int] = set()
        for value in args:
            try:
                ids_to_hide.add(int(value))
            except ValueError:
                console.print(f"[red]Invalid callback ID:[/red] {value}")
                return

        self.hidden_callback_ids.update(ids_to_hide)
        hidden_list = ", ".join(str(i) for i in sorted(ids_to_hide))
        console.print(f"[green]✅[/green] Hidden callback ID(s): {hidden_list}")

    def handle_callback_unhide(self, args: List[str]) -> None:
        """Unhide callbacks from callbacks table output for current session."""
        if not args:
            console.print("[red]Usage:[/red] callback-unhide <id> [id ...]")
            return

        ids_to_unhide: Set[int] = set()
        for value in args:
            try:
                ids_to_unhide.add(int(value))
            except ValueError:
                console.print(f"[red]Invalid callback ID:[/red] {value}")
                return

        removed = 0
        for callback_id in ids_to_unhide:
            if callback_id in self.hidden_callback_ids:
                self.hidden_callback_ids.remove(callback_id)
                removed += 1

        console.print(
            f"[green]✅[/green] Unhid {removed} callback(s). "
            f"Still hidden: {len(self.hidden_callback_ids)}"
        )

    def handle_watch_callbacks(self, args: List[str]) -> None:
        """Watch for new callbacks and alert on registration.

        Usage: watch-callbacks [interval_seconds] (default: 5)
        """
        try:
            interval = 5
            if args:
                try:
                    interval = int(args[0])
                except ValueError:
                    console.print("[red]Invalid interval. Using default: 5 seconds[/red]")

            console.print(f"[cyan]Watching for new callbacks every {interval} seconds... (Ctrl+C to stop)[/cyan]")

            # Track known callback IDs
            known_ids = set()
            try:
                callbacks = self.client.get_callbacks()
                known_ids = {cb.get("id") for cb in callbacks if cb.get("id")}
            except Exception:
                pass

            while True:
                try:
                    time.sleep(interval)
                    callbacks = self.client.get_callbacks()
                    current_ids = {cb.get("id") for cb in callbacks if cb.get("id")}

                    # Find new callbacks
                    new_ids = current_ids - known_ids

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
                        known_ids = current_ids

                except KeyboardInterrupt:
                    console.print("\n[yellow]Stopped watching for callbacks[/yellow]")
                    break
                except Exception as e:
                    console.print(f"[red]Error checking callbacks:[/red] {str(e)}")

        except Exception as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_callback(self, args: List[str]) -> None:
        """Show callback details or enter interactive mode."""
        if not args:
            console.print("[red]Usage:[/red] callback [id] or callback interact [id]")
            return

        if args[0] == "interact" and len(args) > 1:
            try:
                callback_id = int(args[1])
                self.interact_with_callback(callback_id)
            except ValueError:
                console.print("[red]Invalid callback ID[/red]")
            except MythicAPIException as e:
                console.print(f"[red]Error:[/red] {str(e)}")
            return

        try:
            callback_id = int(args[0])
            callback = self.client.get_callback(callback_id)

            if not callback:
                console.print(f"[red]Callback {callback_id} not found[/red]")
                return

            # Format callback fields for display
            last_checkin = self._format_timestamp(callback.get('last_checkin', ''))
            sleep_info = self._extract_sleep_info(callback)

            # Display callback info in plain text format
            info_lines = [
                f"[cyan]ID:[/cyan] {callback.get('id', '')}",
                f"[cyan]Display ID:[/cyan] {callback.get('display_id', '')}",
                f"[cyan]Agent Callback ID:[/cyan] {callback.get('agent_callback_id', '')}",
                f"[cyan]User:[/cyan] {callback.get('user', '')}",
                f"[cyan]Host:[/cyan] {callback.get('host', '')}",
                f"[cyan]IP:[/cyan] {callback.get('ip', '')}",
                f"[cyan]PID:[/cyan] {callback.get('pid', '')}",
                f"[cyan]OS:[/cyan] {callback.get('os', '')}",
                f"[cyan]Architecture:[/cyan] {callback.get('architecture', '')}",
                f"[cyan]Domain:[/cyan] {callback.get('domain', '')}",
                f"[cyan]Integrity Level:[/cyan] {callback.get('integrity_level', '')}",
                f"[cyan]Sleep Info:[/cyan] {sleep_info}",
                f"[cyan]Active:[/cyan] {callback.get('active', '')}",
                f"[cyan]Last Checkin:[/cyan] {last_checkin}",
            ]
            console.print(Panel("\n".join(info_lines), title=f"Callback {callback_id}", border_style="cyan"))
        except ValueError:
            console.print("[red]Invalid callback ID[/red]")
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def interact_with_callback(self, callback_id: int) -> None:
        """Enter interactive mode with a specific callback."""
        try:
            callback = self.client.get_callback(callback_id)
            if not callback:
                console.print(f"[red]Callback {callback_id} not found[/red]")
                return
            
            # Use the internal ID for task creation, but keep display_id for UI
            internal_callback_id = callback.get('id')
            display_callback_id = callback.get('display_id', callback_id)
            
            # If callback_id was passed as display_id, use internal ID now
            if internal_callback_id:
                actual_callback_id = internal_callback_id
            else:
                actual_callback_id = callback_id

            try:
                available_commands = self.client.get_callback_commands(actual_callback_id)
            except MythicAPIException:
                available_commands = []

            cmd_list = {
                cmd.get("cmd"): cmd
                for cmd in (available_commands or [])
                if cmd.get("cmd")
            }

            user = callback.get('user', 'unknown')
            host = callback.get('host', 'unknown')
            payload = callback.get('payload', {})
            payload_type = payload.get('payloadtype', {}).get('name', 'unknown') if payload else 'unknown'

            console.print(f"\n[green]✅[/green] Interacting with callback {display_callback_id}: [cyan]{user}@{host}[/cyan]")
            console.print(
                f"[dim]Agent: {payload_type} | Type 'help' for agent commands, cls to clear the console, 'cli_help' for local CLI commands, or 'back' to exit[/dim]\n"
            )

            while True:
                try:
                    command_line = console.input(f"[yellow]callback-{display_callback_id}[/yellow] > ")

                    if not command_line.strip():
                        continue

                    # Check for exit commands
                    if command_line.strip().lower() in ['back', 'cli_exit', 'quit']:
                        console.print("[dim]Returning to main shell[/dim]")
                        break
                    
                    if command_line.strip().lower() == 'cls':  
                        console.clear()
                        continue

                    # Parse the command
                    parts = command_line.strip().split(None, 1)
                    if not parts:
                        continue

                    cmd_name = parts[0]

                    # Handle special local commands
                    if cmd_name == 'info':
                        # Refresh callback metadata to reflect latest GraphQL response
                        latest_callback = self.client.get_callback(actual_callback_id)
                        if latest_callback:
                            callback = latest_callback

                        sleep_info = self._extract_sleep_info(callback)
                        last_checkin = self._format_timestamp(callback.get('last_checkin', ''))

                        info_lines = [
                            f"[cyan]ID:[/cyan] {callback.get('id', '')}",
                            f"[cyan]Display ID:[/cyan] {callback.get('display_id', '')}",
                            f"[cyan]Agent Callback ID:[/cyan] {callback.get('agent_callback_id', '')}",
                            f"[cyan]User:[/cyan] {callback.get('user', '')}",
                            f"[cyan]Host:[/cyan] {callback.get('host', '')}",
                            f"[cyan]IP:[/cyan] {callback.get('ip', '')}",
                            f"[cyan]PID:[/cyan] {callback.get('pid', '')}",
                            f"[cyan]OS:[/cyan] {callback.get('os', '')}",
                            f"[cyan]Architecture:[/cyan] {callback.get('architecture', '')}",
                            f"[cyan]Domain:[/cyan] {callback.get('domain', '')}",
                            f"[cyan]Integrity Level:[/cyan] {callback.get('integrity_level', '')}",
                            f"[cyan]Sleep Info:[/cyan] {sleep_info}",
                            f"[cyan]Active:[/cyan] {callback.get('active', '')}",
                            f"[cyan]Last Checkin:[/cyan] {last_checkin}",
                        ]
                        console.print(Panel("\n".join(info_lines), title=f"Callback Info", border_style="cyan"))
                        continue

                    if cmd_name == 'cli_help':
                        console.print("""
[cyan]Local Callback CLI Commands (do not task the agent):[/cyan]

[yellow]Local commands:[/yellow]
  cli_help                     Show this local help
  info                         Show callback metadata
  tasks                        List tasks for this callback
  output <task_id>             Show output for a specific task
    back / cli_exit / quit / exit   Return to main shell
  cls                          Clear the console

[yellow]Agent commands:[/yellow]
  help                         Show agent command help
  help <command>               Show help for a specific agent command
  <any agent command> [args]   Sent to the implant as a task

[yellow]Task file uploads:[/yellow]
    Preferred: upload <local_path> <remote_path>
    Example: upload ./payload.exe C:\\Temp\\payload.exe
    The CLI uploads the local file, swaps in Mythic file UUID, and associates it
    with createTask(files=[...]).
    Advanced mode still supports JSON with @<path> markers.
""")
                        continue

                    if cmd_name == 'tasks':
                        self.handle_tasks([str(actual_callback_id)])
                        continue

                    if cmd_name in ['output', 'task-output']:
                        if len(parts) > 1 and parts[1].strip():
                            self.handle_task_output(parts[1].split())
                        else:
                            console.print("[red]Usage:[/red] output <task_id>")
                        continue

                    # If payload doesn't support a help command, show local help instead
                    if cmd_name == 'help' and 'help' not in cmd_list:
                        if len(parts) == 1:
                            if cmd_list:
                                available = ", ".join(sorted(str(k) for k in cmd_list.keys() if k is not None))
                                console.print(f"[cyan]Available agent commands:[/cyan]\n{available}")
                            else:
                                console.print("[yellow]No agent command metadata available.[/yellow]")
                        else:
                            target = parts[1].strip()
                            cmd_info = cmd_list.get(target)
                            if cmd_info:
                                help_text = cmd_info.get("help_cmd") or cmd_info.get("description") or "(no help available)"
                                console.print(f"[cyan]{target}[/cyan]: {help_text}")
                            else:
                                console.print(f"[yellow]Unknown command:[/yellow] {target}")
                        continue

                    # For all other commands (including 'help' when supported),
                    # build params from text or JSON and send task to the agent.
                    raw_args = parts[1] if len(parts) > 1 else ""
                    params = self._build_task_params(cmd_name, raw_args)
                    params, task_files = self._prepare_task_submission(cmd_name, params)

                    # Create and display task with retry logic for common parameter mismatches
                    task = None
                    task_creation_error = None
                    
                    try:
                        task = self.client.create_task(actual_callback_id, cmd_name, params, files=task_files)
                    except MythicAPIException as e:
                        task_creation_error = str(e)
                        # Try common parameter format alternatives if initial creation failed
                        if raw_args and params is not None and not isinstance(params, dict):
                            console.print(f"[yellow]⚠[/yellow]  First attempt failed, trying alternative parameter formats...")
                            
                            # Try wrapping raw text in common parameter names
                            alternatives = [
                                {"path": raw_args},          # For path-based commands
                                {"filepath": raw_args},      # For file operations
                                {"command": raw_args},       # For command execution
                                {"args": raw_args},          # Generic args parameter
                                raw_args,                    # Keep original
                            ]
                            
                            for alt_params in alternatives:
                                try:
                                    alt_params_final, alt_files = self._prepare_task_submission(cmd_name, alt_params)
                                    task = self.client.create_task(actual_callback_id, cmd_name, alt_params_final, files=alt_files)
                                    task_creation_error = None
                                    break
                                except MythicAPIException:
                                    continue
                    
                    if task_creation_error:
                        console.print(f"[red]✗[/red] Task creation failed: {task_creation_error}")
                        console.print(f"[dim]Command: {cmd_name} | Params: {params}[/dim]")
                        
                        # If the error is about command not found, show available commands
                        if "Failed to fetch command by that name" in task_creation_error or "command" in task_creation_error.lower():
                            if cmd_list:
                                available = ", ".join(sorted(str(k) for k in cmd_list.keys() if k is not None))
                                console.print(f"[yellow]Available commands:[/yellow] {available}")
                            else:
                                console.print("[yellow]Hint:[/yellow] Try running 'help' to see available commands")
                        continue
                    
                    if not task:
                        console.print("[red]✗[/red] Failed to create task")
                        continue
                    
                    display_task_id = task.get('display_id')
                    internal_task_id = task.get('id')
                    shown_task_id = display_task_id if isinstance(display_task_id, int) and display_task_id > 0 else internal_task_id
                    console.print(f"[green]✅[/green] Task {shown_task_id} created")
                    if isinstance(internal_task_id, int) and internal_task_id > 0:
                        self._wait_for_task_and_print_output(internal_task_id)

                except KeyboardInterrupt:
                    console.print("\n[yellow]Use 'back' to exit interactive mode[/yellow]")
                    continue
                except MythicAPIException as e:
                    console.print(f"[red]Error:[/red] {str(e)}")
                except Exception as e:
                    console.print(f"[red]Error:[/red] {str(e)}")

        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_tasks(self, args: List[str]) -> None:
        """List tasks."""
        try:
            callback_id = int(args[0]) if args else None
            tasks = self.client.get_tasks(callback_id)

            if not tasks:
                console.print("[yellow]No tasks found[/yellow]")
                return

            table = Table(title="Tasks", show_lines=True)
            table.add_column("Task ID", style="cyan", justify="right")
            table.add_column("Callback", style="magenta")
            table.add_column("Command", style="green")
            table.add_column("Operator", style="blue")
            table.add_column("Responses", style="white", justify="right")
            table.add_column("Status", style="yellow")
            table.add_column("Timestamp", style="dim")

            for task in tasks:
                callback_info = task.get("callback") or {}
                command_info = task.get("command") or {}
                operator_info = task.get("operator") or {}
                command_name = command_info.get("cmd") or task.get("command_name", "")
                table.add_row(
                    str(task.get("id", "")),
                    str(callback_info.get("display_id", "")),
                    command_name,
                    operator_info.get("username", ""),
                    str(task.get("response_count", "")),
                    task.get("status", ""),
                    task.get("timestamp", ""),
                )

            self._print_maybe_paged(table)
        except ValueError:
            console.print("[red]Invalid callback ID[/red]")
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_task(self, args: List[str]) -> None:
        """Create a new task."""
        if not args:
            console.print("[yellow]Interactive task creation[/yellow]")
            callback_id = console.input("[cyan]Callback ID: [/cyan]")
            command = console.input("[cyan]Command name: [/cyan]")

            console.print("[dim]Enter task parameters (press Enter to skip)[/dim]")
            params = {}

            if command == "shell":
                cmd = console.input("[cyan]Shell command: [/cyan]")
                if cmd:
                    params["command"] = cmd
            elif command == "ls":
                path = console.input("[cyan]Directory path (default: .): [/cyan]").strip()
                params["filepath"] = path if path else "."
            elif command == "download":
                path = console.input("[cyan]Remote file path: [/cyan]")
                if path:
                    params["path"] = path
            elif command == "upload":
                local_file = console.input("[cyan]Local file path: [/cyan]")
                remote_path = console.input("[cyan]Remote destination path: [/cyan]")
                if local_file:
                    params["file"] = local_file
                if remote_path:
                    params["path"] = remote_path
            else:
                console.print("[dim]Enter parameters as JSON (or press Enter for no params)[/dim]")
                params_str = console.input("[cyan]Parameters: [/cyan]")
                if params_str:
                    try:
                        params = json.loads(params_str)
                    except json.JSONDecodeError:
                        console.print("[red]Invalid JSON, using empty params[/red]")
                        params = {}

            try:
                callback_id = int(callback_id)
            except ValueError:
                console.print("[red]Invalid callback ID[/red]")
                return
        elif len(args) < 2:
            console.print("[red]Usage:[/red] task [callback_id] [command] [params]")
            console.print("[dim]Or run 'task' with no arguments for interactive mode[/dim]")
            return
        else:
            try:
                callback_id = int(args[0])
                command = args[1]
                raw_args = " ".join(args[2:]) if len(args) > 2 else ""
                params = self._build_task_params(command, raw_args)
            except ValueError as e:
                console.print(f"[red]Invalid input:[/red] {str(e)}")
                return

        params, task_files = self._prepare_task_submission(command, params)

        try:
            task = self.client.create_task(callback_id, command, params, files=task_files)
            display_task_id = task.get("display_id")
            internal_task_id = task.get("id")
            shown_task_id = display_task_id if isinstance(display_task_id, int) and display_task_id > 0 else internal_task_id
            console.print(f"[green]✅[/green] Task {shown_task_id} created")

            if isinstance(internal_task_id, int) and internal_task_id > 0:
                self._wait_for_task_and_print_output(internal_task_id)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_task_output(self, args: List[str]) -> None:
        """Show task output."""
        if not args:
            console.print("[red]Usage:[/red] task-output [task_id]")
            return

        try:
            task_id = int(args[0])
            outputs = self.client.get_task_output(task_id)

            if not outputs:
                console.print("[yellow]No output yet[/yellow]")
                return

            for output in outputs:
                response_text = (
                    output.get("response")
                    or output.get("response_text")
                    or output.get("response_escape")
                    or ""
                )
                response_text = self._decode_response_text(response_text)
                timestamp = output.get("timestamp", "")

                console.print(
                    Panel(
                        response_text,
                        title=f"Output at {timestamp}",
                        border_style="green",
                    )
                )
        except ValueError:
            console.print("[red]Invalid task ID[/red]")
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_payload_create(self, args: List[str]) -> None:
        """Create a payload interactively."""
        # Get payload type and supported OS
        if len(args) < 2:
            console.print("[yellow]Interactive payload creation[/yellow]")
            console.print("\n[cyan]Available payload types:[/cyan]")

            try:
                payload_types = self.client.get_payload_types()
            except MythicAPIException as e:
                console.print(f"[red]Error fetching payload types:[/red] {str(e)}")
                return

            if not payload_types:
                console.print("[red]No payload types available[/red]")
                return

            # Display available payload types
            type_map = {}
            for i, pt in enumerate(payload_types, 1):
                os_list = ", ".join(pt.get("supported_os", []))
                console.print(f"  {i}. {pt['name']} ({os_list})")
                type_map[str(i)] = pt['name']

            choice = console.input("\n[cyan]Select payload type (number or name): [/cyan]")

            # Resolve choice to payload type name
            if choice in type_map:
                payload_type_name = type_map[choice]
            else:
                payload_type_name = choice
                # Verify it exists
                if payload_type_name not in [pt['name'] for pt in payload_types]:
                    console.print(f"[red]Unknown payload type: {payload_type_name}[/red]")
                    return
        else:
            payload_type_name = args[1]

        # Get payload type details
        try:
            payload_type = self.client.get_payload_type(payload_type_name)
        except MythicAPIException as e:
            console.print(f"[red]Error fetching payload type:[/red] {str(e)}")
            return

        if not payload_type:
            console.print(f"[red]Payload type '{payload_type_name}' not found[/red]")
            return

        console.print(f"\n[green]✅[/green] Payload Type: [cyan]{payload_type['name']}[/cyan]")
        console.print(f"  Supported OS: {', '.join(payload_type.get('supported_os', ['Unknown']))}")

        # Gather build parameters
        console.print("\n[cyan]Build Parameters:[/cyan]")
        build_params = {}

        for param in payload_type.get('buildparameters', []):
            param_name = param['name']
            param_type = param['parameter_type']
            description = param.get('description', '')
            required = param.get('required', False)
            default = param.get('default_value', '')
            choices_str = param.get('choices', '')  # Get choices if available

            # Display parameter info
            required_str = "[red](required)[/red]" if required else "[dim](optional)[/dim]"
            console.print(f"  {param_name} {required_str}")
            if description:
                console.print(f"    [dim]{description}[/dim]")
            if default:
                console.print(f"    [dim]Default: {default}[/dim]")

            # Prompt for value
            if param_type == "ChooseOne":
                # Try to parse choices from the 'choices' field first, then from description
                if choices_str:
                    # If there's a choices field, parse it (could be comma-separated or other format)
                    if isinstance(choices_str, str):
                        choice_list = [s.strip() for s in choices_str.split(",") if s.strip()]
                    elif isinstance(choices_str, list):
                        choice_list = choices_str
                    else:
                        choice_list = []

                    if choice_list:
                        console.print(f"    [cyan]Options:[/cyan]")
                        for i, choice in enumerate(choice_list[:10], 1):
                            console.print(f"      {i}. {choice}")
                        if len(choice_list) > 10:
                            console.print(f"      ... (+{len(choice_list)-10} more)")
                        value = console.input(f"    [cyan]Enter value (number or text){' (required)' if required else ' or press Enter to skip'}: [/cyan]")

                        # If they entered a number, use the corresponding choice
                        try:
                            choice_idx = int(value) - 1
                            if 0 <= choice_idx < len(choice_list):
                                value = choice_list[choice_idx]
                        except (ValueError, IndexError):
                            # They entered text directly, use as-is
                            pass
                    else:
                        # No choices found, prompt normally
                        value = console.input(f"    [cyan]Enter value{' (required)' if required else ' or press Enter to skip'}: [/cyan]")
                else:
                    # No choices field, prompt normally
                    value = console.input(f"    [cyan]Enter value{' (required)' if required else ' or press Enter to skip'}: [/cyan]")
            else:
                placeholder = f" ({default})" if default else ""
                value = console.input(f"    [cyan]Enter value{placeholder}{' (required)' if required else ' or press Enter to skip'}: [/cyan]")

            # Use default if not provided
            if not value:
                if default:
                    value = default
                elif required:
                    console.print(f"    [red]This parameter is required![/red]")
                    return
                else:
                    continue

            build_params[param_name] = value

        # Create the payload
        console.print("\n[cyan]Creating payload...[/cyan]")

        try:
            config = {
                "payload_type": payload_type_name,
                "build_parameters": build_params
            }
            result = self.client.create_payload(config)

            # Check if there was an error during build
            if result.get('status') == 'error':
                error_msg = result.get('error', 'Unknown error')
                if 'webhook' in error_msg.lower() or 'json' in error_msg.lower():
                    console.print(f"[yellow]⚠[/yellow]  Payload created with build error:")
                    console.print(f"  Error: [red]{error_msg}[/red]")
                    console.print(f"  UUID: [cyan]{result.get('uuid', 'Unknown')}[/cyan]")
                    console.print(f"\n  [dim]The Mythic build webhook may not be properly configured.")
                    console.print(f"  Check your Mythic server logs for more details.[/dim]")
                else:
                    console.print(f"[red]Build failed:[/red] {error_msg}")
            else:
                console.print(f"[green]✅[/green] Payload created successfully!")
                console.print(f"  UUID: [cyan]{result.get('uuid', 'Unknown')}[/cyan]")
                console.print(f"  Status: [green]{result.get('status', 'Unknown')}[/green]")
                console.print(f"\n  Download with: [yellow]payload-download {result.get('uuid', '')} <filename>[/yellow]")
        except MythicAPIException as e:
            console.print(f"[red]Error creating payload:[/red] {str(e)}")

    def handle_payloads(self, args: List[str]) -> None:
        """List payloads."""
        try:
            payloads = self.client.get_payloads()

            if not payloads:
                console.print("[yellow]No payloads found[/yellow]")
                return

            table = Table(title="Payloads", show_lines=True)
            table.add_column("UUID", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Description", style="yellow")
            table.add_column("Created", style="dim")

            for payload in payloads:
                # Extract timestamp and format it
                created = payload.get("creation_time", "")
                if created:
                    # Format: "2024-01-15T10:30:45.123456+00:00" -> "2024-01-15 10:30"
                    created = created.split("T")[0] + " " + created.split("T")[1][:5] if "T" in created else created[:16]

                table.add_row(
                    payload.get("uuid", "")[:16] + "...",
                    payload.get("payloadtype", {}).get("name", ""),
                    payload.get("description", ""),
                    created,
                )

            self._print_maybe_paged(table)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_operations(self, args: List[str]) -> None:
        """List operations."""
        try:
            operations = self.client.get_operations()

            if not operations:
                console.print("[yellow]No operations found[/yellow]")
                return

            table = Table(title="Operations", show_lines=True)
            table.add_column("ID", style="cyan", justify="right")
            table.add_column("Name", style="green")
            table.add_column("Admin", style="yellow")
            table.add_column("Complete", style="magenta")

            for op in operations:
                table.add_row(
                    str(op.get("id", "")),
                    op.get("name", ""),
                    op.get("admin", {}).get("username", ""),
                    "Yes" if op.get("complete") else "No",
                )

            self._print_maybe_paged(table)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_profiles(self, args: List[str]) -> None:
        """List C2 profiles."""
        try:
            profiles = self.client.get_c2_profiles()

            if not profiles:
                console.print("[yellow]No C2 profiles found[/yellow]")
                return

            table = Table(title="C2 Profiles", show_lines=True)
            table.add_column("Name", style="cyan")
            table.add_column("Description", style="yellow")
            table.add_column("Running", style="green")

            for profile in profiles:
                table.add_row(
                    profile.get("name", ""),
                    profile.get("description", ""),
                    "Yes" if profile.get("running") else "No",
                )

            self._print_maybe_paged(table)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_credentials(self, args: List[str]) -> None:
        """List credentials."""
        try:
            credentials = self.client.get_credentials()

            if not credentials:
                console.print("[yellow]No credentials found[/yellow]")
                return

            table = Table(title="Credentials", show_lines=True)
            table.add_column("ID", style="cyan", justify="right")
            table.add_column("Type", style="magenta")
            table.add_column("Realm", style="yellow")
            table.add_column("Account", style="green")
            table.add_column("Credential", style="red")

            for cred in credentials:
                table.add_row(
                    str(cred.get("id", "")),
                    cred.get("type", ""),
                    cred.get("realm", ""),
                    cred.get("account", ""),
                    cred.get("credential", "")[:30] + "...",
                )

            self._print_maybe_paged(table)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_credential_add(self, args: List[str]) -> None:
        """Add a credential interactively or from JSON."""
        if not args:
            console.print("[yellow]Interactive credential creation[/yellow]")

            cred_type = console.input("[cyan]Credential type (e.g., plaintext, hash, key): [/cyan]")
            realm = console.input("[cyan]Realm/Domain (optional): [/cyan]")
            account = console.input("[cyan]Account/Username: [/cyan]")
            credential = console.input("[cyan]Credential/Password: [/cyan]")
            comment = console.input("[cyan]Comment (optional): [/cyan]")

            cred_data = {
                "type": cred_type,
                "account": account,
                "credential": credential,
            }
            if realm:
                cred_data["realm"] = realm
            if comment:
                cred_data["comment"] = comment
        else:
            try:
                cred_data = json.loads(" ".join(args))
            except json.JSONDecodeError as e:
                console.print(f"[red]Invalid JSON:[/red] {str(e)}")
                return

        try:
            result = self.client.create_credential(cred_data)
            console.print(f"[green]✅[/green] Credential added successfully")

            if result:
                syntax = Syntax(
                    json.dumps(result, indent=2),
                    "json",
                    theme=self.syntax_theme,
                )
                self._print_maybe_paged(syntax)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_files(self, args: List[str]) -> None:
        """List files."""
        try:
            files = self.client.get_files()

            if not files:
                console.print("[yellow]No files found[/yellow]")
                return

            table = Table(title="Files", show_lines=True)
            table.add_column("ID", style="cyan")
            table.add_column("Filename", style="green")
            table.add_column("Size", style="yellow", justify="right")
            table.add_column("Timestamp", style="dim")

            for file in files:
                file_name = (
                    file.get("filename_utf8")
                    or file.get("filename")
                    or file.get("filename_text")
                    or ""
                )

                size = file.get("size")
                size_str = f"{size:,} bytes" if isinstance(size, int) else "-"

                timestamp = file.get("timestamp", "")
                if isinstance(timestamp, str) and "T" in timestamp:
                    timestamp = f"{timestamp.split('T')[0]} {timestamp.split('T')[1][:8]}"

                table.add_row(
                    str(file.get("agent_file_id", ""))[:16] + "...",
                    file_name,
                    size_str,
                    timestamp,
                )

            self._print_maybe_paged(table)
        except MythicAPIException as e:
            console.print(f"[red]Error:[/red] {str(e)}")

    def handle_file_download(self, args: List[str]) -> None:
        """Download a file by UUID."""
        if not args:
            # Interactive mode - list files and let user choose
            try:
                files = self.client.get_files()

                if not files:
                    console.print("[yellow]No files found[/yellow]")
                    return

                # Filter out deleted and incomplete files
                available_files = [f for f in files if f.get("complete") and not f.get("deleted")]

                if not available_files:
                    console.print("[yellow]No complete, non-deleted files available[/yellow]")
                    return

                # Display files
                console.print("\n[cyan]Available files:[/cyan]\n")
                for idx, file in enumerate(available_files, 1):
                    file_name = (
                        file.get("filename_utf8")
                        or file.get("filename")
                        or file.get("filename_text")
                        or "Unknown"
                    )
                    file_uuid = file.get("agent_file_id", "")
                    size = file.get("size", 0)
                    size_str = f"{size:,} bytes" if isinstance(size, int) else "-"
                    console.print(f"{idx:2d}. {file_name:40s} ({size_str}) - UUID: {file_uuid}")

                # Prompt user for selection
                console.print()
                choice = console.input("[cyan]Select file number to download (or 'cancel'): [/cyan]")

                if choice.lower() == "cancel":
                    return

                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(available_files):
                        selected_file = available_files[idx]
                        file_uuid = selected_file.get("agent_file_id", "")
                        file_name = (
                            selected_file.get("filename_utf8")
                            or selected_file.get("filename")
                            or selected_file.get("filename_text")
                            or "file"
                        )

                        output = console.input(f"[cyan]Save as (default: {file_name}): [/cyan]")
                        if not output:
                            output = file_name

                        self._download_file(file_uuid, output)
                    else:
                        console.print("[red]Invalid selection[/red]")
                except ValueError:
                    console.print("[red]Invalid input[/red]")
            except MythicAPIException as e:
                console.print(f"[red]Error:[/red] {str(e)}")
        elif len(args) < 2:
            console.print("[red]Usage:[/red] file-download [uuid] [output_path]")
            console.print("[dim]Or run 'file-download' with no args for interactive mode[/dim]")
        else:
            partial_uuid = args[0]
            output = args[1]
            # Resolve partial UUID to full UUID
            full_uuid = self._resolve_partial_uuid(partial_uuid)
            if full_uuid:
                self._download_file(full_uuid, output)

    def _resolve_partial_uuid(self, partial_uuid: str) -> Optional[str]:
        """Resolve a partial UUID to a full UUID by searching files.

        Args:
            partial_uuid: Partial UUID (can be truncated)

        Returns:
            Full UUID if exactly one match is found, None otherwise.
        """
        try:
            files = self.client.get_files()

            # Filter to only complete, non-deleted files
            available_files = [f for f in files if f.get("complete") and not f.get("deleted")]

            # Find all files where UUID starts with the partial UUID
            matches = [f for f in available_files if f.get("agent_file_id", "").startswith(partial_uuid)]

            if not matches:
                console.print(f"[red]No available files found matching UUID: {partial_uuid}[/red]")
                return None

            if len(matches) == 1:
                return matches[0].get("agent_file_id")

            # Multiple matches - let user choose
            console.print(f"\n[yellow]Found {len(matches)} files matching '{partial_uuid}':[/yellow]\n")
            for idx, file in enumerate(matches, 1):
                file_name = (
                    file.get("filename_utf8")
                    or file.get("filename")
                    or file.get("filename_text")
                    or "Unknown"
                )
                file_uuid = file.get("agent_file_id", "")
                size = file.get("size", 0)
                size_str = f"{size:,} bytes" if isinstance(size, int) else "-"
                console.print(f"{idx}. {file_name:40s} ({size_str})")
                console.print(f"   UUID: {file_uuid}")

            console.print()
            choice = console.input("[cyan]Select file number (or 'cancel'): [/cyan]")

            if choice.lower() == "cancel":
                return None

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(matches):
                    return matches[idx].get("agent_file_id")
                else:
                    console.print("[red]Invalid selection[/red]")
                    return None
            except ValueError:
                console.print("[red]Invalid input[/red]")
                return None
        except MythicAPIException as e:
            console.print(f"[red]Error resolving UUID:[/red] {str(e)}")
            return None

    def _download_file(self, file_uuid: str, output_path: str) -> None:
        """Helper to download a file and save to disk.

        Args:
            file_uuid: Full UUID of the file to download.
            output_path: Path to save the file.
        """
        try:
            console.print(f"[dim]Downloading {file_uuid}...[/dim]")
            data = self.client.download_file(file_uuid)

            with open(output_path, "wb") as f:
                f.write(data)

            console.print(f"[green]✅[/green] File saved to {output_path} ({len(data):,} bytes)")
        except Exception as e:
            console.print(f"[red]Error:[/red] {str(e)}")

