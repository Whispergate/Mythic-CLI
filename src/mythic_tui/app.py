"""Textual-based multi-screen TUI for Mythic operations."""

from __future__ import annotations

import base64
import binascii
import json
import os
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, cast

import pyfiglet
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, ListItem, ListView, RichLog, Static

from mythic_cli.client import MythicAPIException, MythicClient
from mythic_cli.config import ConfigManager


class LoginScreen(Screen[None]):
    """Login screen for authenticating to Mythic server."""

    BINDINGS = [Binding("ctrl+c", "quit", "Quit")]

    @property
    def _app(self) -> "MythicTextualApp":
        return cast("MythicTextualApp", self.app)

    def __init__(self) -> None:
        super().__init__()
        self.username = ""
        self.password = ""
        self.focus_field = "username"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        # Generate Mythic ASCII art logo
        try:
            logo = pyfiglet.figlet_format("Mythic", font="caligraphy")
        except Exception:
            logo = "Mythic"
        
        with Center():
            with Vertical(id="login-container"):
                yield Static(logo, id="login-logo")
                yield Static("Operator Console", id="login-subtitle")
                yield Static("", id="login-status")
                yield Static("Username:", id="username-label")
                yield Input(placeholder="Enter username", id="login-username")
                yield Static("Password:", id="password-label")
                yield Input(placeholder="Enter password", id="login-password", password=True)
                yield Static("Press Enter to submit | Ctrl+C to quit", id="login-help")
        yield Footer()

    async def on_mount(self) -> None:
        username_input = self.query_one("#login-username", Input)
        username_input.focus()

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Capture input values as they change."""
        if event.input.id == "login-username":
            self.username = event.value
        elif event.input.id == "login-password":
            self.password = event.value

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id
        
        if input_id == "login-username":
            self.username = event.value
            password_input = self.query_one("#login-password", Input)
            password_input.focus()
            return
        
        if input_id == "login-password":
            self.password = event.value
            await self._attempt_login()
            return

    async def _attempt_login(self) -> None:
        """Attempt to login with the provided credentials."""
        status = self.query_one("#login-status", Static)
        username_input = self.query_one("#login-username", Input)
        password_input = self.query_one("#login-password", Input)
        
        if not self.username or not self.password:
            status.update("[red]Username and password are required[/red]")
            return
        
        status.update("[yellow]Logging in...[/yellow]")
        
        # Attempt login directly (not in a worker thread since it's quick)
        try:
            success = self._app.client.login(self.username, self.password)
            
            if success:
                status.update("[green]✅ Login successful![/green]")
                # Save credentials to config
                self._app.config_manager.update_config(
                    username=self.username,
                    password=self.password,
                    api_key=self._app.client.api_token
                )
                # Replace login screen with dashboard
                self.app.pop_screen()
                self.app.push_screen(DashboardScreen())
                return
            else:
                status.update("[red]Invalid credentials[/red]")
                password_input.value = ""
                password_input.focus()
        except MythicAPIException as e:
            status.update(f"[red]Login failed: {str(e)[:50]}[/red]")
            password_input.value = ""
            password_input.focus()
        except Exception as e:
            status.update(f"[red]Error: {str(e)[:50]}[/red]")
            password_input.focus()


class BaseScreen(Screen[None]):
    """Base screen class with sidebar support."""
    
    BINDINGS = [Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar")]
    
    @property
    def _app(self) -> "MythicTextualApp":
        return cast("MythicTextualApp", self.app)
    
    def compose(self) -> ComposeResult:
        """Compose base layout with sidebar and main content."""
        with Horizontal():
            with Vertical(id="sidebar-container", classes="hidden"):
                yield Static("📋 Screens", id="sidebar-title")
                yield ListView(id="sidebar-list")
            with Vertical():
                yield from self.compose_main_content()
    
    def compose_main_content(self) -> ComposeResult:
        """Override in subclasses to provide main content."""
        raise NotImplementedError
    
    def action_toggle_sidebar(self) -> None:
        """Toggle the sidebar visibility."""
        sidebar = self.query_one("#sidebar-container")
        if self._app.sidebar_visible:
            sidebar.add_class("hidden")
            self._app.sidebar_visible = False
        else:
            sidebar.remove_class("hidden")
            self._app.sidebar_visible = True
            self.call_later(self._update_sidebar)
    
    async def _update_sidebar(self) -> None:
        """Update sidebar with available screens."""
        sidebar_list = self.query_one("#sidebar-list", ListView)
        await sidebar_list.clear()
        
        # Add Dashboard
        sidebar_list.append(ListItem(Static("🏠 Dashboard"), id="nav-dashboard"))
        
        # Add Files screen
        sidebar_list.append(ListItem(Static("📁 Files"), id="nav-files"))
        
        # Add separator
        if self._app.callback_screens:
            sidebar_list.append(ListItem(Static("─" * 20)))
        
        # Add callback screens
        for callback_id, title in sorted(self._app.callback_screens.items()):
            sidebar_list.append(
                ListItem(Static(f"🖥️  {title}"), id=f"nav-callback-{callback_id}")
            )
    
    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle sidebar navigation selection."""
        if event.item.id == "nav-dashboard":
            self._app.push_screen(DashboardScreen())
        elif event.item.id == "nav-files":
            self._app.push_screen(FilesScreen())
        elif event.item.id and event.item.id.startswith("nav-callback-"):
            callback_id = int(event.item.id.replace("nav-callback-", ""))
            self._app.push_screen(CallbackScreen(callback_id))
        
        # Close sidebar after selection
        self.action_toggle_sidebar()


class DashboardScreen(BaseScreen):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._table_ready = False

    def compose_main_content(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="dash-body"):
            with Vertical(id="dash-left"):
                yield Static("Dashboard", id="dash-status")
                yield DataTable(id="callbacks-table")
            with Vertical(id="dash-right"):
                yield Static("Alerts", id="alerts-title")
                yield RichLog(id="dash-alerts", highlight=True, markup=True, auto_scroll=True)
        yield Input(
            placeholder=(
                "Commands: help, open <id>, hide <id>, unhide <id>, files, login, theme [name], "
                "download-file <uuid> <path>, upload-file <path>, refresh"
            ),
            id="dash-input",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._setup_table()
        self.refresh_callbacks()
        self.set_interval(3, self.refresh_callbacks)

    def _setup_table(self) -> None:
        if self._table_ready:
            return
        table = self.query_one("#callbacks-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Active", "OS", "Arch", "User", "Host", "IP", "Process", "Agent", "Last Checkin")
        self._table_ready = True

    @work(thread=True, exclusive=True)
    def refresh_callbacks(self) -> None:
        try:
            callbacks = self._app.client.get_callbacks()
        except MythicAPIException as exc:
            error_msg = str(exc).lower()
            # Check for 401 Unauthorized or authentication errors
            if "401" in error_msg or "unauthorized" in error_msg or "authentication" in error_msg:
                self._app.call_from_thread(self._handle_401)
                return
            self._app.call_from_thread(self._update_status, f"[red]Callback fetch failed: {exc}[/red]")
            return
        except Exception as exc:
            error_msg = str(exc).lower()
            if "401" in error_msg or "unauthorized" in error_msg or "authentication" in error_msg:
                self._app.call_from_thread(self._handle_401)
                return
            self._app.call_from_thread(self._update_status, f"[red]Callback fetch failed: {exc}[/red]")
            return
        self._app.call_from_thread(self._apply_callbacks, callbacks)

    def _handle_401(self) -> None:
        """Handle 401 Unauthorized errors by showing login screen."""
        self._update_status("[red]❌ Session expired - please login again[/red]")
        # Clear the API token so we force re-authentication
        self._app.client.api_token = ""
        # Push login screen
        self.app.push_screen(LoginScreen())

    def _apply_callbacks(self, callbacks: List[Dict[str, Any]]) -> None:
        table = self.query_one("#callbacks-table", DataTable)
        table.clear(columns=False)

        visible_callbacks: List[Dict[str, Any]] = []
        current_ids: Set[int] = set()
        for cb in callbacks:
            cb_id = self._app.to_int(cb.get("id"))
            if cb_id is None:
                continue
            current_ids.add(cb_id)
            if cb_id in self._app.hidden_callback_ids:
                continue
            visible_callbacks.append(cb)

        new_ids = current_ids - self._app.known_callback_ids
        if new_ids:
            for cb in callbacks:
                cb_id = self._app.to_int(cb.get("id"))
                if cb_id in new_ids:
                    self.log_alert(
                        f"[green]✅ New callback[/green] id={cb_id} user={cb.get('user','N/A')} host={cb.get('host','N/A')}"
                    )
        self._app.known_callback_ids = current_ids

        for cb in visible_callbacks:
            pid = cb.get("pid", "")
            process_name = cb.get("process_name", "")
            process_display = f"{pid} ({process_name})" if pid and process_name else (process_name or (str(pid) if pid else ""))
            payload = cb.get("payload") or {}
            payloadtype = payload.get("payloadtype") or {}
            agent_name = payloadtype.get("name", "")
            is_active = self._app.is_callback_active(cb)
            table.add_row(
                str(cb.get("id", "")),
                "✅" if is_active else "💀",
                str(cb.get("os", "")),
                str(cb.get("architecture", "")),
                str(cb.get("user", "")),
                str(cb.get("host", "")),
                str(cb.get("ip", "")),
                process_display,
                str(agent_name),
                self._app.format_timestamp(cb.get("last_checkin")),
            )

        hidden_count = len(callbacks) - len(visible_callbacks)
        self._update_status(f"[cyan]Callbacks:[/cyan] total={len(callbacks)} visible={len(visible_callbacks)} hidden={hidden_count}")

    def _update_status(self, message: str) -> None:
        self.query_one("#dash-status", Static).update(message)

    def log_alert(self, message: str) -> None:
        log = self.query_one("#dash-alerts", RichLog)
        timestamp = datetime.now().strftime("%H:%M:%S")
        log.write(f"[{timestamp}] {message}")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "callbacks-table":
            return
        row = event.data_table.get_row(event.row_key)
        if not row:
            return
        callback_id = self._app.to_int(row[0])
        if callback_id is not None:
            self.app.push_screen(CallbackScreen(callback_id))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "dash-input":
            return
        raw = event.value.strip()
        event.input.value = ""
        if not raw:
            return

        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in {"q", "quit", "exit"}:
            self.app.exit()
            return
        if cmd in {"r", "refresh", "callbacks"}:
            self.refresh_callbacks()
            return
        if cmd in {"open", "interact", "use"}:
            if not args:
                self.log_alert("[yellow]Usage:[/yellow] open <callback_id>")
                return
            callback_id = self._app.to_int(args[0])
            if callback_id is None:
                self.log_alert(f"[red]Invalid callback ID:[/red] {args[0]}")
                return
            self.app.push_screen(CallbackScreen(callback_id))
            return
        if cmd in {"help", "?"}:
            self.log_alert("[cyan]Dashboard commands:[/cyan] open <id>, hide/unhide, hidden, clear-hidden, files, login, theme, download-file, upload-file, refresh")
            return
        if cmd == "theme":
            if not args:
                current_theme = self._app.theme
                themes_list = ", ".join(self._app.AVAILABLE_THEMES)
                self.log_alert(f"[cyan]Current theme:[/cyan] {current_theme}")
                self.log_alert(f"[cyan]Available themes:[/cyan] {themes_list}")
                self.log_alert("[yellow]Usage:[/yellow] theme <theme_name>")
                return
            theme_name = args[0].lower()
            if self._app.change_theme(theme_name):
                self.log_alert(f"[green]✅ Theme changed to:[/green] {theme_name}")
            else:
                self.log_alert(f"[red]Invalid theme:[/red] {theme_name}")
                self.log_alert(f"[cyan]Available:[/cyan] {', '.join(self._app.AVAILABLE_THEMES)}")
            return
        if cmd == "login":
            self.app.push_screen(LoginScreen())
            return
        if cmd == "hidden":
            if not self._app.hidden_callback_ids:
                self.log_alert("[dim]No hidden callbacks[/dim]")
            else:
                self.log_alert(f"[cyan]Hidden:[/cyan] {', '.join(str(i) for i in sorted(self._app.hidden_callback_ids))}")
            return
        if cmd == "clear-hidden":
            count = len(self._app.hidden_callback_ids)
            self._app.hidden_callback_ids.clear()
            self.log_alert(f"[green]✅ Cleared hidden callbacks ({count})[/green]")
            self.refresh_callbacks()
            return
        if cmd in {"hide", "unhide"}:
            if not args:
                self.log_alert(f"[yellow]Usage:[/yellow] {cmd} <id> [id ...]")
                return
            parsed_ids: Set[int] = set()
            for item in args:
                val = self._app.to_int(item)
                if val is None:
                    self.log_alert(f"[red]Invalid callback ID:[/red] {item}")
                    return
                parsed_ids.add(val)
            if cmd == "hide":
                self._app.hidden_callback_ids.update(parsed_ids)
                self.log_alert(f"[green]✅ Hidden:[/green] {', '.join(str(i) for i in sorted(parsed_ids))}")
            else:
                removed = 0
                for cb_id in parsed_ids:
                    if cb_id in self._app.hidden_callback_ids:
                        self._app.hidden_callback_ids.remove(cb_id)
                        removed += 1
                self.log_alert(f"[green]✅ Unhid {removed} callback(s)[/green]")
            self.refresh_callbacks()
            return
        if cmd == "files":
            self.app.push_screen(FilesScreen())
            return
        if cmd == "download-file":
            if len(args) < 2:
                self.log_alert("[yellow]Usage:[/yellow] download-file <uuid_prefix> <output_path>")
                return
            partial_uuid, output_path = args[0], args[1]
            full_uuid = self._app.resolve_partial_uuid(partial_uuid) or partial_uuid
            try:
                data = self._app.client.download_file(full_uuid)
                with open(output_path, "wb") as fh:
                    fh.write(data)
                self.log_alert(f"[green]✅ Downloaded[/green] {full_uuid} -> {output_path}")
            except Exception as exc:
                self.log_alert(f"[red]download-file failed:[/red] {exc}")
            return
        if cmd == "upload-file":
            if not args:
                self.log_alert("[yellow]Usage:[/yellow] upload-file <local_path>")
                return
            local_path = args[0]
            if not os.path.exists(local_path):
                self.log_alert(f"[red]File not found:[/red] {local_path}")
                return
            try:
                with open(local_path, "rb") as fh:
                    data = fh.read()
                result = self._app.client.upload_file(os.path.basename(local_path), data)
                self.log_alert(f"[green]✅ Uploaded[/green] {local_path} -> {result}")
            except Exception as exc:
                self.log_alert(f"[red]upload-file failed:[/red] {exc}")
            return

        self.log_alert("[yellow]Unknown command[/yellow]. Use [white]help[/white].")


class FilesScreen(BaseScreen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._files_table_ready = False
        self._files: List[Dict[str, Any]] = []

    def compose_main_content(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical():
                yield Static("Files", id="files-title")
                yield DataTable(id="files-table")
            with Vertical():
                yield Static("File Info", id="files-info")
                yield RichLog(id="files-log", highlight=True, markup=True, auto_scroll=True)
        yield Input(
            placeholder="Commands: download <row_num> <path>, upload <path>, info <row_num>, refresh, back",
            id="files-input",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._setup_table()
        self.refresh_files()

    def _setup_table(self) -> None:
        if self._files_table_ready:
            return
        table = self.query_one("#files-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Num", "UUID", "Filename", "Size", "Uploaded", "Agent")
        self._files_table_ready = True

    @work(thread=True, exclusive=True)
    def refresh_files(self) -> None:
        try:
            files = self._app.client.get_files()
        except Exception as exc:
            self._app.call_from_thread(self._append_log, f"[red]File fetch failed: {exc}[/red]")
            return
        self._app.call_from_thread(self._apply_files, files)

    def _apply_files(self, files: List[Dict[str, Any]]) -> None:
        table = self.query_one("#files-table", DataTable)
        table.clear(columns=False)
        self._files = files

        for idx, f in enumerate(files):
            uuid = str(f.get("agent_file_id", ""))
            name = f.get("filename_utf8") or f.get("filename") or f.get("filename_text") or "Unknown"
            size = f.get("size", 0)
            uploaded = self._app.format_timestamp(f.get("timestamp"))
            callback = f.get("callback") or {}
            agent = f.get("agent_file_id", "Unknown")
            
            table.add_row(
                str(idx),
                uuid[:16] + "...",
                name,
                str(size),
                uploaded,
                str(agent),
            )

    def _append_log(self, text: str) -> None:
        self.query_one("#files-log", RichLog).write(text)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "files-table":
            return
        row = event.data_table.get_row(event.row_key)
        if not row:
            return
        try:
            file_idx = int(row[0])
            if file_idx < len(self._files):
                f = self._files[file_idx]
                self._show_file_info(f)
        except (ValueError, IndexError):
            self._append_log("[red]Invalid file selection[/red]")

    def _show_file_info(self, f: Dict[str, Any]) -> None:
        log = self.query_one("#files-log", RichLog)
        log.clear()
        uuid = str(f.get("agent_file_id", ""))
        name = f.get("filename_utf8") or f.get("filename") or f.get("filename_text") or "Unknown"
        size = f.get("size", 0)
        uploaded = self._app.format_timestamp(f.get("timestamp"))
        mime = f.get("mime_type", "Unknown")
        
        log.write(f"[cyan]File Details[/cyan]")
        log.write(f"UUID:     {uuid}")
        log.write(f"Name:     {name}")
        log.write(f"Size:     {size} bytes")
        log.write(f"Uploaded: {uploaded}")
        log.write(f"MIME:     {mime}")
        log.write("")
        log.write("[yellow]Commands:[/yellow]")
        log.write(f"  download {uuid} <path>")
        log.write(f"  download {uuid[:8]} <path>  (partial UUID)")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "files-input":
            return
        raw = event.value.strip()
        event.input.value = ""
        if not raw:
            return

        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()
        
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in {"back", "exit", "quit"}:
            self.app.pop_screen()
            return
        
        if cmd == "refresh":
            self.refresh_files()
            return
        
        if cmd == "cli_help":
            self._append_log("[cyan]Files screen commands:[/cyan]")
            self._append_log("  download <uuid_prefix> <output_path>  Download file")
            self._append_log("  upload <local_path>                    Upload file")
            self._append_log("  info <row_num>                         Show file details")
            self._append_log("  refresh                                Refresh file list")
            self._append_log("  back                                   Return to dashboard")
            return
        
        if cmd == "download":
            if len(args) < 2:
                self._append_log("[yellow]Usage:[/yellow] download <uuid_prefix> <output_path>")
                return
            partial_uuid, output_path = args[0], args[1]
            full_uuid = self._app.resolve_partial_uuid(partial_uuid) or partial_uuid
            try:
                data = self._app.client.download_file(full_uuid)
                with open(output_path, "wb") as fh:
                    fh.write(data)
                self._append_log(f"[green]✅ Downloaded[/green] {full_uuid} -> {output_path}")
            except Exception as exc:
                self._append_log(f"[red]Download failed:[/red] {exc}")
            return
        
        if cmd == "upload":
            if not args:
                self._append_log("[yellow]Usage:[/yellow] upload <local_path>")
                return
            local_path = args[0]
            if not os.path.exists(local_path):
                self._append_log(f"[red]File not found:[/red] {local_path}")
                return
            try:
                with open(local_path, "rb") as fh:
                    data = fh.read()
                result = self._app.client.upload_file(os.path.basename(local_path), data)
                self._append_log(f"[green]✅ Uploaded[/green] {local_path} -> {result}")
            except Exception as exc:
                self._append_log(f"[red]Upload failed:[/red] {exc}")
            return
        
        if cmd == "info":
            if not args:
                self._append_log("[yellow]Usage:[/yellow] info <row_num>")
                return
            try:
                idx = int(args[0])
                if idx < len(self._files):
                    self._show_file_info(self._files[idx])
                else:
                    self._append_log(f"[red]Invalid row number:[/red] {idx}")
            except (ValueError, IndexError):
                self._append_log("[red]Invalid row number[/red]")
            return
        
        self._append_log("[yellow]Unknown command[/yellow]. Use [white]help[/white].")


class CallbackScreen(BaseScreen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
    ]

    def __init__(self, callback_id: int):
        super().__init__()
        self.callback_id = callback_id
        self.last_task_id: Optional[int] = None
        self._tasks_table_ready = False
        self._commands_loaded = False
        self._displayed_task_ids: Set[int] = set()  # Track which tasks have had output displayed

    def compose_main_content(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="cb-body"):
            with Vertical(id="cb-left"):
                yield Static("Task Output", id="out-title")
                yield RichLog(id="output-log", highlight=True, markup=True, auto_scroll=True)
            with Vertical(id="cb-right"):
                yield Static(f"Callback {self.callback_id}", id="cb-status")
                yield DataTable(id="tasks-table")
                yield Static("Command Catalog (Help)", id="cmd-title")
                yield RichLog(id="commands-log", highlight=True, markup=True, auto_scroll=False)
        yield Input(
            placeholder="agent command input (e.g. shell whoami). Local: tasks, output <id|last>, commands, back",
            id="cb-input",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._setup_tasks_table()
        self.refresh_callback_view()
        self.set_interval(2.5, self.refresh_tasks)
        
        # Register this callback screen in the sidebar
        try:
            callbacks = self._app.client.get_callbacks()
            for cb in callbacks:
                if cb.get("id") == self.callback_id:
                    host = cb.get("host", "unknown")
                    user = cb.get("user", "unknown")
                    title = f"Callback {self.callback_id} ({user}@{host})"
                    self._app.register_callback_screen(self.callback_id, title)
                    break
        except Exception:
            # Fallback if we can't get callback details
            self._app.register_callback_screen(self.callback_id, f"Callback {self.callback_id}")

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.refresh_callback_view()

    def _setup_tasks_table(self) -> None:
        if self._tasks_table_ready:
            return
        table = self.query_one("#tasks-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Task ID", "Display", "Command", "Status", "Completed", "Time")
        self._tasks_table_ready = True

    def refresh_callback_view(self) -> None:
        self.refresh_callback_info()
        self.refresh_commands_catalog()
        self.refresh_tasks()

    @work(thread=True, exclusive=True)
    def refresh_callback_info(self) -> None:
        try:
            cb = self._app.client.get_callback(self.callback_id)
            self._app.call_from_thread(self._apply_callback_info, cb)
        except Exception as exc:
            self._app.call_from_thread(self._set_status, f"[red]Failed to fetch callback: {exc}[/red]")

    def _apply_callback_info(self, cb: Dict[str, Any]) -> None:
        self._set_status(
            f"[cyan]Callback {self.callback_id}[/cyan] user={cb.get('user','')} host={cb.get('host','')} "
            f"os={cb.get('os','')}/{cb.get('architecture','')} last={self._app.format_timestamp(cb.get('last_checkin'))}"
        )

    def _set_status(self, text: str) -> None:
        self.query_one("#cb-status", Static).update(text)

    @work(thread=True, exclusive=True)
    def refresh_commands_catalog(self) -> None:
        if self._commands_loaded:
            return
        try:
            commands = self._app.client.get_callback_commands(self.callback_id)
        except Exception as exc:
            self._app.call_from_thread(self._append_commands_log, f"[red]command catalog failed:[/red] {exc}")
            return
        self._app.call_from_thread(self._apply_commands_catalog, commands)

    def _append_commands_log(self, text: str) -> None:
        self.query_one("#commands-log", RichLog).write(text)

    def _apply_commands_catalog(self, commands: List[Dict[str, Any]]) -> None:
        log = self.query_one("#commands-log", RichLog)
        log.clear()
        if not commands:
            log.write("[dim]No command metadata available[/dim]")
            return
        for cmd in sorted(commands, key=lambda c: str(c.get("cmd", ""))):
            name = cmd.get("cmd", "")
            help_cmd = cmd.get("help_cmd") or cmd.get("description") or ""
            log.write(f"[white]{name}[/white] - {help_cmd}")
        self._commands_loaded = True

    @work(thread=True, exclusive=True)
    def refresh_tasks(self) -> None:
        try:
            tasks = self._app.client.get_tasks(self.callback_id)
        except Exception as exc:
            self._app.call_from_thread(self._append_output, f"[red]tasks fetch failed:[/red] {exc}")
            return
        self._app.call_from_thread(self._apply_tasks, tasks)

    def _apply_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        table = self.query_one("#tasks-table", DataTable)
        table.clear(columns=False)
        for task in tasks[:100]:
            table.add_row(
                str(task.get("id", "")),
                str(task.get("display_id", "")),
                str(task.get("command_name") or (task.get("command") or {}).get("cmd") or ""),
                str(task.get("status", "")),
                "yes" if task.get("completed") else "no",
                self._app.format_timestamp(task.get("timestamp")),
            )
            
            # Auto-display output for newly completed tasks
            task_id = self._app.to_int(task.get("id"))
            if task_id is not None and task.get("completed") and task_id not in self._displayed_task_ids:
                self._displayed_task_ids.add(task_id)
                # Fetch and display output in background
                self._fetch_and_display_task_output(task_id)

    def _append_output(self, text: str) -> None:
        log = self.query_one("#output-log", RichLog)
        timestamp = datetime.now().strftime("%H:%M:%S")
        log.write(f"[{timestamp}] {text}")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "tasks-table":
            return
        row = event.data_table.get_row(event.row_key)
        if not row:
            return
        task_id = self._app.to_int(row[0])
        if task_id is None:
            return
        self.last_task_id = task_id
        self.show_task_output(task_id)

    @work(thread=True, exclusive=True)
    def show_task_output(self, task_id: int) -> None:
        try:
            outputs = self._app.client.get_task_output(task_id)
        except Exception as exc:
            self._app.call_from_thread(self._append_output, f"[red]output fetch failed:[/red] {exc}")
            return
        if not outputs:
            self._app.call_from_thread(self._append_output, f"[dim]No output for task {task_id}[/dim]")
            return
        self._app.call_from_thread(self._append_output, f"[cyan]Task {task_id} output:[/cyan]")
        for out in outputs:
            ts = out.get("timestamp", "")
            text = out.get("response") or out.get("response_escape") or ""
            text = self._app.decode_response_text(str(text))
            if len(text) > 4000:
                text = text[:4000] + "\n... [truncated]"
            self._app.call_from_thread(self._append_output, f"[dim]{ts}[/dim]\n{text}")

    @work(thread=True)
    def _fetch_and_display_task_output(self, task_id: int) -> None:
        """Fetch and display output for a completed task automatically."""
        try:
            outputs = self._app.client.get_task_output(task_id)
        except Exception as exc:
            # Silently fail for auto-fetch to avoid cluttering output
            return
        if not outputs:
            return
        
        # Display task completion message with command info
        self._app.call_from_thread(self._append_output, f"[green]✅ Task {task_id} completed[/green]")
        
        # Display outputs
        for out in outputs:
            ts = out.get("timestamp", "")
            text = out.get("response") or out.get("response_escape") or ""
            text = self._app.decode_response_text(str(text))
            if len(text) > 4000:
                text = text[:4000] + "\n... [truncated]"
            self._app.call_from_thread(self._append_output, f"[dim]{ts}[/dim]\n{text}")

    def _create_task(self, command: str, arg_text: str = "") -> None:
        params = self._app.build_task_params(command, arg_text)
        try:
            task = self._app.client.create_task(self.callback_id, command, params)
            internal_id = task.get("id")
            display_id = task.get("display_id")
            error_msg = task.get("error", "")
            
            # Check if task creation failed
            internal_ok = isinstance(internal_id, int) and internal_id > 0
            display_ok = isinstance(display_id, int) and display_id > 0
            
            if not internal_ok and not display_ok:
                if error_msg:
                    self._append_output(f"[red]❌ Task failed:[/red] {error_msg}")
                else:
                    self._append_output("[red]❌ Task failed to create[/red]")
                return
            
            shown_id = display_id if display_ok else internal_id
            if isinstance(internal_id, int) and internal_id > 0:
                self.last_task_id = internal_id
            self._append_output(f"[green]✅ Task created[/green] cmd={command} id={shown_id}")
            self.refresh_tasks()
        except Exception as exc:
            self._append_output(f"[red]❌ Task creation failed:[/red] {exc}")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "cb-input":
            return
        raw = event.value.strip()
        event.input.value = ""
        if not raw:
            return

        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in {"back", "quit"}:
            self.app.pop_screen()
            return
        if cmd in {"cli_help", "?"}:
            self._append_output("[cyan]Callback screen commands:[/cyan]")
            self._append_output("  any_agent_command [args]  (e.g. shell whoami, ls, pwd)")
            self._append_output("  For commands needing structured args, pass JSON: socks '{\"port\":9050}'")
            self._append_output("  tasks | output <id|last> | commands | refresh | back")
            self._append_output("  download-file <uuid> <path> | upload-file <path>")
            return
        if cmd == "refresh":
            self.refresh_callback_view()
            return
        if cmd == "commands":
            self._commands_loaded = False
            self.refresh_commands_catalog()
            return
        if cmd == "tasks":
            self.refresh_tasks()
            return
        if cmd in {"output", "task-output"}:
            if not args:
                self._append_output("[yellow]Usage:[/yellow] output <task_id|last>")
                return
            if args[0].lower() == "last":
                if self.last_task_id is None:
                    self._append_output("[yellow]No last task available[/yellow]")
                    return
                self.show_task_output(self.last_task_id)
                return
            task_id = self._app.to_int(args[0])
            if task_id is None:
                self._append_output(f"[red]Invalid task ID:[/red] {args[0]}")
                return
            self.show_task_output(task_id)
            return
        if cmd == "download-file":
            if len(args) < 2:
                self._append_output("[yellow]Usage:[/yellow] download-file <uuid_prefix> <output_path>")
                return
            partial_uuid, output_path = args[0], args[1]
            full_uuid = self._app.resolve_partial_uuid(partial_uuid) or partial_uuid
            try:
                data = self._app.client.download_file(full_uuid)
                with open(output_path, "wb") as fh:
                    fh.write(data)
                self._append_output(f"[green]✅ Downloaded[/green] {full_uuid} -> {output_path}")
            except Exception as exc:
                self._append_output(f"[red]download-file failed:[/red] {exc}")
            return
        if cmd == "upload-file":
            if not args:
                self._append_output("[yellow]Usage:[/yellow] upload-file <local_path>")
                return
            local_path = args[0]
            if not os.path.exists(local_path):
                self._append_output(f"[red]File not found:[/red] {local_path}")
                return
            try:
                with open(local_path, "rb") as fh:
                    data = fh.read()
                result = self._app.client.upload_file(os.path.basename(local_path), data)
                self._append_output(f"[green]✅ Uploaded[/green] {local_path} -> {result}")
            except Exception as exc:
                self._append_output(f"[red]upload-file failed:[/red] {exc}")
            return

        arg_text = " ".join(args) if args else ""
        try:
            self._create_task(cmd, arg_text)
        except Exception as exc:
            self._append_output(f"[red]task failed:[/red] {exc}")


class MythicTextualApp(App[None]):
    TITLE = "Mythic TUI"
    SUB_TITLE = "Operator Console"

    CSS = """
    Screen { layout: vertical; }
    
    /* Sidebar Styles */
    #sidebar-container { 
        width: 25; 
        height: 100%; 
        dock: left;
        background: $panel;
        border-right: solid $accent;
    }
    #sidebar-container.hidden { display: none; }
    #sidebar-title { 
        height: 3; 
        content-align: center middle; 
        text-style: bold;
        background: $accent;
        color: $text;
    }
    #sidebar-list { height: 1fr; }
    ListView > ListItem.separator { 
        height: 1; 
        color: $text-muted;
    }
    
    #dash-body, #cb-body { height: 1fr; }
    #dash-left, #cb-left { width: 2fr; height: 1fr; border: solid $accent; padding: 0 1; }
    #dash-right, #cb-right { width: 1fr; height: 1fr; border: solid $accent; padding: 0 1; }
    #dash-status, #cb-status { height: 3; content-align: left middle; }
    #callbacks-table, #tasks-table { height: 2fr; }
    #commands-log { height: 1fr; }
    #output-log { height: 1fr; }
    #cmd-title, #out-title { height: 2; content-align: left middle; }
    #dash-input, #cb-input { dock: bottom; height: 3; }
    
    /* Login Screen Styles */
    LoginScreen { align: center middle; }
    #login-container { 
        width: 80; 
        height: auto; 
        border: solid $accent; 
        padding: 1 2;
    }
    #login-logo { 
        width: 100%; 
        height: auto; 
        content-align: center middle; 
        color: $accent;
    }
    #login-subtitle { 
        width: 100%; 
        height: 1; 
        content-align: center middle; 
        color: $text;
        text-style: bold;
    }
    #login-status { 
        width: 100%; 
        height: 2; 
        content-align: center middle; 
        margin: 1 0;
    }
    #username-label, #password-label { 
        width: 100%; 
        height: 1; 
        content-align: left middle; 
        margin-top: 1;
    }
    #login-username, #login-password { 
        width: 100%; 
        height: 3; 
        margin-bottom: 1;
    }
    #login-help { 
        width: 100%; 
        height: 1; 
        content-align: center middle; 
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [Binding("ctrl+c", "quit", "Quit")]
    
    # Available Textual themes
    AVAILABLE_THEMES = [
        "textual-dark",
        "textual-light",
        "nord",
        "gruvbox",
        "catppuccin-mocha",
        "dracula",
        "tokyo-night",
        "monokai",
        "solarized-light",
        "solarized-dark",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config_manager = ConfigManager()
        self.client = MythicClient(self.config_manager.config)
        self.hidden_callback_ids: Set[int] = set()
        self.known_callback_ids: Set[int] = set()
        self.sidebar_visible = False
        self.callback_screens: Dict[int, str] = {}  # Maps callback_id -> screen title

    async def on_mount(self) -> None:
        # Apply saved theme
        saved_theme = self.config_manager.config.tui_theme
        if saved_theme and saved_theme in self.AVAILABLE_THEMES:
            self.theme = saved_theme
        
        is_authenticated = await self.authenticate()
        if is_authenticated:
            self.push_screen(DashboardScreen())
        else:
            self.push_screen(LoginScreen())

    async def authenticate(self) -> bool:
        """Try to authenticate using stored credentials or API token.
        
        Returns:
            True if authenticated, False otherwise.
        """
        if self.client.api_token:
            return True
        
        username = self.config_manager.config.username
        password = self.config_manager.config.password
        
        if not username or not password:
            return False
        
        try:
            if self.client.login(username, password):
                self.config_manager.update_config(api_key=self.client.api_token)
                return True
        except Exception:
            pass
        
        return False

    def to_int(self, value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def format_timestamp(self, value: Optional[str]) -> str:
        if not value:
            return ""
        try:
            if value.endswith("Z"):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            elif "+" not in value and value.count("-") == 2:
                dt = datetime.fromisoformat(value)
            else:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(value)

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

    def is_callback_active(self, callback: Dict[str, Any]) -> bool:
        """Determine if a callback is active based on time since last checkin.

        A callback is active if time since last checkin < (sleep_info + 10 minute grace period).
        If sleep_info is not set, default 10 minute grace period applies.

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
            if last_checkin_str.endswith("Z"):
                last_checkin_utc = datetime.fromisoformat(last_checkin_str.replace("Z", "+00:00"))
            elif "+" not in last_checkin_str:
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

    def decode_response_text(self, response_text: str) -> str:
        if not response_text:
            return response_text
        candidate = "".join(response_text.strip().split())
        if not candidate:
            return response_text
        try:
            decoded_bytes = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError):
            return response_text
        if not decoded_bytes:
            return response_text
        decoded_text = decoded_bytes.decode("utf-8", errors="replace")
        printable_chars = sum(ch.isprintable() or ch in "\n\r\t" for ch in decoded_text)
        if printable_chars / max(len(decoded_text), 1) < 0.8:
            return response_text
        return decoded_text

    def resolve_partial_uuid(self, partial_uuid: str) -> Optional[str]:
        try:
            files = self.client.get_files()
        except Exception:
            return None
        needle = partial_uuid.strip().lower()
        matches: List[str] = []
        for f in files:
            uuid = str(f.get("agent_file_id", ""))
            if uuid.lower().startswith(needle):
                matches.append(uuid)
        if len(matches) == 1:
            return matches[0]
        return None

    def build_task_params(self, command: str, arg_text: str) -> Optional[Any]:
        raw = arg_text.strip()
        if not raw:
            if command == "ls":
                return {"filepath": "."}
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        
        # Handle specific commands with special argument parsing
        if command in {"shell", "run", "execute"}:
            return {"command": raw}
        if command == "download":
            return {"path": raw}
        if command in {"ls", "cd"}:
            return {"filepath": raw}
        if command == "help":
            return raw
        
        # Handle socks command: "socks start 1080" -> {"action": "start", "port": 1080}
        if command == "socks":
            parts = raw.split()
            if len(parts) >= 2:
                action = parts[0].lower()
                try:
                    port = int(parts[1])
                    params = {"action": action, "port": port}
                    return params
                except (ValueError, IndexError):
                    pass
        
        # For other commands, try to parse key-value pairs
        # Handle format: "key1 value1 key2 value2" or "-key value"
        parts = raw.split()
        params = {}
        i = 0
        while i < len(parts):
            part = parts[i]
            # Check if it's a flag (starts with -)
            if part.startswith('-'):
                key = part.lstrip('-').lower()
                if i + 1 < len(parts):
                    value = parts[i + 1]
                    # Try to convert to int if it looks like a number
                    try:
                        params[key] = int(value)
                    except ValueError:
                        params[key] = value
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        
        # If we parsed some parameters, return them
        if params:
            return params
        
        # Otherwise return raw text
        return raw

    def change_theme(self, theme_name: str) -> bool:
        """Change the app theme and save to config.
        
        Args:
            theme_name: Name of the theme to apply
            
        Returns:
            True if theme was changed successfully, False otherwise
        """
        if theme_name not in self.AVAILABLE_THEMES:
            return False
        
        self.theme = theme_name
        self.config_manager.update_config(tui_theme=theme_name)
        return True
    
    def register_callback_screen(self, callback_id: int, title: str) -> None:
        """Register a callback screen in the sidebar."""
        self.callback_screens[callback_id] = title


def main() -> None:
    app = MythicTextualApp()
    app.run()


if __name__ == "__main__":
    main()
