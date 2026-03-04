"""Payload builder screens for Mythic TUI."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Static
from rich.markup import escape as rich_escape

from mythic_cli.client import MythicClient


def _safe_richlog_write(log: RichLog, text: str) -> None:
    """Write to a RichLog without crashing on malformed markup content."""
    content = str(text)
    try:
        log.write(content)
        return
    except Exception:
        pass
    try:
        log.write(rich_escape(content))
    except Exception:
        return


class BuildProgressScreen(Screen[None]):
    """Screen showing real-time build progression."""
    
    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]
    
    CSS = """
    BuildProgressScreen {
        background: $surface;
    }
    
    #bp-log {
        height: 1fr;
        border: solid $accent;
        margin: 1;
    }
    
    #bp-status {
        height: 3;
        dock: bottom;
        padding: 1;
    }
    
    #bp-buttons {
        height: auto;
        dock: bottom;
        margin: 1;
    }
    """
    
    def __init__(self, payload_name: str, client: MythicClient):
        super().__init__()
        self.payload_name = payload_name
        self.client = client
        self.build_complete = False
        self.payload_uuid = None
    
    def compose(self) -> ComposeResult:
        """Compose the build progress UI."""
        yield Header(show_clock=True)
        yield Static(f"[bold cyan]Building: {self.payload_name}[/bold cyan]")
        yield RichLog(id="bp-log", highlight=True, markup=True)
        
        with Horizontal(id="bp-buttons"):
            yield Button("Close", id="bp-btn-close", variant="primary")
        
        yield Static("", id="bp-status")
        yield Footer()
    
    async def on_mount(self) -> None:
        """Initialize the progress screen."""
        log = self.query_one("#bp-log", RichLog)
        _safe_richlog_write(log, "[cyan]Build started...[/cyan]\n")
    
    def add_log_entry(self, text: str) -> None:
        """Add an entry to the build log."""
        log = self.query_one("#bp-log", RichLog)
        _safe_richlog_write(log, text + "\n")
    
    def update_status(self, text: str) -> None:
        """Update the status line."""
        try:
            self.query_one("#bp-status", Static).update(text)
        except Exception:
            pass
    
    def mark_complete(self, uuid: str) -> None:
        """Mark the build as complete with the resulting UUID."""
        self.payload_uuid = uuid
        self.build_complete = True
        self.update_status(f"[green]Build complete![/green] UUID: [cyan]{uuid}[/cyan]")
        self.add_log_entry(f"[green]✅ Build complete![/green] Payload UUID: [cyan]{uuid}[/cyan]")
    
    def mark_failed(self, error: str) -> None:
        """Mark the build as failed."""
        self.build_complete = True
        self.update_status(f"[red]Build failed[/red]")
        self.add_log_entry(f"[red]❌ Build failed:[/red] {error}")
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "bp-btn-close":
            self.app.pop_screen()
    
    def action_back(self) -> None:
        """Go back to previous screen."""
        self.app.pop_screen()


class PayloadBuilderScreen(Screen[None]):
    """Comprehensive payload builder with sidebar navigation."""
    
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
        Binding("ctrl+g", "generate", "Generate"),
        Binding("ctrl+n", "new_payload", "New Payload"),
        Binding("ctrl+w", "new_wrapper", "New Wrapper"),
    ]
    
    CSS = """
    PayloadBuilderScreen {
        layout: horizontal;
    }
    
    #pb-sidebar {
        width: 30;
        height: 100%;
        border-right: solid $accent;
        padding: 1;
    }
    
    #pb-main {
        width: 1fr;
        height: 100%;
    }
    
    #pb-content {
        height: 1fr;
        border: solid $accent;
        margin: 1;
        padding: 1;
    }
    
    #pb-status {
        height: 3;
        dock: bottom;
        padding: 1;
    }
    
    .pb-section-title {
        text-style: bold;
        color: $accent;
        margin: 1 0;
    }
    
    .pb-table {
        height: auto;
        max-height: 15;
        margin: 1 0;
    }
    
    #pb-actions {
        height: auto;
        margin: 1 0;
    }
    """
    
    def __init__(self, client: MythicClient) -> None:
        super().__init__()
        self.client = client
        self.sidebar_visible = True
        self.build_mode: str = "payload"  # "payload" or "wrapper"
        
        # Payload/Wrapper state
        self._payload_types: List[Dict[str, Any]] = []
        self._filtered_payload_types: List[Dict[str, Any]] = []
        self._selected_payload_type: Optional[Dict[str, Any]] = None
        self._selected_os: Optional[str] = None
        self._build_params: Dict[str, Any] = {}
        self._build_param_list: List[Dict[str, Any]] = []
        self._c2_profiles: List[Dict[str, Any]] = []
        self._selected_c2_profiles: List[Dict[str, Any]] = []
        self._c2_profile_params: Dict[str, Dict[str, Any]] = {}  # {c2_name: {param_name: value}}
        self._current_c2_index: int = -1  # Currently selected C2 profile for editing
        
        # Wrapper-specific state
        self._available_payloads: List[Dict[str, Any]] = []
        self._wrapped_payload: Optional[Dict[str, Any]] = None
    
    def compose(self) -> ComposeResult:
        """Compose the payload builder UI."""
        yield Header(show_clock=True)
        
        with Horizontal():
            # Sidebar
            with Vertical(id="pb-sidebar"):
                yield Static("[bold cyan]PAYLOAD BUILDER[/bold cyan]", classes="pb-section-title")
                yield Button("New Payload", id="btn-new-payload", variant="primary")
                yield Button("New Wrapper", id="btn-new-wrapper", variant="primary")
                yield Static("")
                yield Static("[bold]Navigation[/bold]", classes="pb-section-title")
                yield Button("1. Select OS", id="btn-nav-os")
                yield Button("2. Select Type", id="btn-nav-type")
                yield Button("3. Build Params", id="btn-nav-params")
                yield Button("4. C2 Profiles", id="btn-nav-c2")
                yield Button("5. Review & Build", id="btn-nav-review")
            
            # Main content area
            with Vertical(id="pb-main"):
                with VerticalScroll(id="pb-content"):
                    yield Static("", id="pb-mode-indicator")
                    yield Static("", id="pb-step-title")
                    
                    # Step 1: OS Selection
                    yield DataTable(id="os-table", classes="pb-table")
                    
                    # Step 2: Payload Type Selection
                    yield DataTable(id="payload-types-table", classes="pb-table")
                    
                    # Step 3: Build Parameters
                    yield DataTable(id="build-params-table", classes="pb-table")
                    yield RichLog(id="build-params-log", highlight=True, markup=True)
                    
                    # Step 4: C2 Profiles
                    yield DataTable(id="c2-profiles-table", classes="pb-table")
                    yield Static("", id="c2-profile-hint")
                    yield DataTable(id="c2-params-table", classes="pb-table")
                    yield RichLog(id="c2-params-log", highlight=True, markup=True)
                    
                    # Step 5: Review (for wrapper mode)
                    yield DataTable(id="payloads-table", classes="pb-table")
                    
                    # Actions
                    with Horizontal(id="pb-actions"):
                        yield Button("Generate", id="btn-generate", variant="success")
                        yield Button("Reset", id="btn-reset", variant="warning")
                        yield Button("Back", id="btn-back")
                
                yield Input(placeholder="Commands: set <param> <value>, setc2 <param> <value>, step <n>, generate, reset", id="pb-input")
                yield Static("", id="pb-status")
        
        yield Footer()
    
    async def on_mount(self) -> None:
        """Initialize the payload builder."""
        self._setup_tables()
        self._set_mode("payload")
        self.refresh_data()
    
    def _setup_tables(self) -> None:
        """Set up all data tables."""
        # OS table
        os_table = self.query_one("#os-table", DataTable)
        os_table.cursor_type = "row"
        os_table.add_columns("ID", "Operating System")
        os_table.display = False
        
        # Payload types table
        pt_table = self.query_one("#payload-types-table", DataTable)
        pt_table.cursor_type = "row"
        pt_table.add_columns("ID", "Name", "Wrapper")
        pt_table.display = False
        
        # Build parameters table
        bp_table = self.query_one("#build-params-table", DataTable)
        bp_table.cursor_type = "row"
        bp_table.add_columns("Parameter", "Type", "Value", "Required")
        bp_table.display = False
        
        # C2 profiles table
        c2_table = self.query_one("#c2-profiles-table", DataTable)
        c2_table.cursor_type = "row"
        c2_table.add_columns("ID", "Name", "Description", "Running", "Selected")
        c2_table.display = False
        
        # C2 params table
        c2p_table = self.query_one("#c2-params-table", DataTable)
        c2p_table.cursor_type = "row"
        c2p_table.add_columns("Parameter", "Type", "Value", "Required")
        c2p_table.display = False
        
        # Payloads table (for wrapper mode)
        p_table = self.query_one("#payloads-table", DataTable)
        p_table.cursor_type = "row"
        p_table.add_columns("ID", "UUID", "Type", "OS", "Filename")
        p_table.display = False
    
    def _set_mode(self, mode: str) -> None:
        """Set the build mode (payload or wrapper)."""
        self.build_mode = mode
        mode_indicator = self.query_one("#pb-mode-indicator", Static)
        
        if mode == "payload":
            mode_indicator.update("[bold green]Mode: New Payload[/bold green]")
        else:
            mode_indicator.update("[bold cyan]Mode: Wrapper Payload[/bold cyan]")
        
        self._show_step(1)
    
    def _update_mode_indicator(self) -> None:
        """Update mode indicator with current selection info."""
        mode_indicator = self.query_one("#pb-mode-indicator", Static)
        
        if self.build_mode == "wrapper" and self._wrapped_payload:
            wrapped_name = self._wrapped_payload.get("filename", "unknown")
            mode_indicator.update(f"[bold cyan]Wrapper Mode[/bold cyan] - Wrapping: [yellow]{wrapped_name}[/yellow]")
        elif self.build_mode == "payload" and self._selected_os:
            mode_indicator.update(f"[bold green]Payload Mode[/bold green] - OS: [yellow]{self._selected_os}[/yellow]")
        elif self.build_mode == "payload":
            mode_indicator.update("[bold green]Mode: New Payload[/bold green]")
        else:
            mode_indicator.update("[bold cyan]Mode: Wrapper Payload[/bold cyan]")
    
    def _show_step(self, step: int) -> None:
        """Show a specific step in the builder."""
        # Hide all tables and logs
        self.query_one("#os-table", DataTable).display = False
        self.query_one("#c2-profile-hint", Static).display = False
        self.query_one("#c2-params-table", DataTable).display = False
        self.query_one("#c2-params-log", RichLog).display = False
        self.query_one("#payload-types-table", DataTable).display = False
        self.query_one("#build-params-table", DataTable).display = False
        self.query_one("#build-params-log", RichLog).display = False
        self.query_one("#c2-profiles-table", DataTable).display = False
        self.query_one("#payloads-table", DataTable).display = False
        
        step_title = self.query_one("#pb-step-title", Static)
        
        if step == 1:
            if self.build_mode == "wrapper":
                step_title.update("[yellow]Step 1:[/yellow] Select Payload to Wrap")
                self.query_one("#payloads-table", DataTable).display = True
                self._populate_payloads()
            else:
                step_title.update("[yellow]Step 1:[/yellow] Select Target OS")
                self.query_one("#os-table", DataTable).display = True
                self._populate_os_options()
        elif step == 2:
            step_title.update("[yellow]Step 2:[/yellow] Select Payload Type")
            self.query_one("#payload-types-table", DataTable).display = True
            self._populate_payload_types()
        elif step == 3:
            step_title.update("[yellow]Step 3:[/yellow] Configure Build Parameters")
            self.query_one("#build-params-table", DataTable).display = True
            self.query_one("#build-params-log", RichLog).display = True
            self._show_build_parameters()
            self.query_one("#c2-profile-hint", Static).display = True
            self._populate_c2_profiles()
            if self._current_c2_index >= 0:
                self.query_one("#c2-params-table", DataTable).display = True
                self.query_one("#c2-params-log", RichLog).display = True
                self._show_c2_parameters()
        elif step == 4:
            step_title.update("[yellow]Step 4:[/yellow] Select C2 Profiles")
            self.query_one("#c2-profiles-table", DataTable).display = True
            self._populate_c2_profiles()
        elif step == 5:
            step_title.update("[yellow]Step 5:[/yellow] Review and Generate")
            self._show_review()
    
    @work(thread=True, exclusive=True)
    def refresh_data(self) -> None:
        """Refresh all data from the server."""
        try:
            payload_types = self.client.get_payload_types()
            c2_profiles = self.client.get_c2_profiles()
            
            # For wrapper mode, get available payloads
            if self.build_mode == "wrapper":
                payloads = self.client.get_payloads()
            else:
                payloads = []
            
            self.app.call_from_thread(self._apply_data, payload_types, c2_profiles, payloads)
        except Exception as exc:
            self.app.call_from_thread(self._update_status, f"[red]Failed to fetch data: {exc}[/red]")
    
    def _apply_data(self, payload_types: List[Dict[str, Any]], c2_profiles: List[Dict[str, Any]], payloads: List[Dict[str, Any]]) -> None:
        """Apply fetched data to the builder."""
        self._payload_types = payload_types
        self._c2_profiles = c2_profiles
        self._available_payloads = payloads
        self._update_status(f"Loaded {len(payload_types)} payload types, {len(c2_profiles)} C2 profiles")
    
    def _populate_os_options(self) -> None:
        """Populate OS options from all available payload types."""
        os_table = self.query_one("#os-table", DataTable)
        os_table.clear(columns=False)
        
        # Collect all unique OS options from payload types
        all_os = set()
        for pt in self._payload_types:
            all_os.update(pt.get("supported_os", []))
        
        for idx, os_name in enumerate(sorted(all_os), 1):
            os_table.add_row(str(idx), os_name)
    
    def _populate_payload_types(self) -> None:
        """Populate payload types filtered by selected OS."""
        pt_table = self.query_one("#payload-types-table", DataTable)
        pt_table.clear(columns=False)
        
        # Filter payload types by selected OS
        filtered_types = []
        if self.build_mode == "wrapper":
            # For wrappers, show only wrapper types
            filtered_types = [pt for pt in self._payload_types if pt.get("wrapper")]
        elif self._selected_os:
            # For normal payloads, filter by OS
            filtered_types = [
                pt for pt in self._payload_types 
                if self._selected_os in pt.get("supported_os", [])
            ]
        else:
            filtered_types = self._payload_types
        
        for idx, pt in enumerate(filtered_types, 1):
            name = pt.get("name", "")
            wrapper = "✓" if pt.get("wrapper") else ""
            pt_table.add_row(str(idx), name, wrapper)
        
        # Store filtered list for later reference
        self._filtered_payload_types = filtered_types
    
    def _show_build_parameters(self) -> None:
        """Display build parameters for the selected payload type."""
        if not self._selected_payload_type:
            return
        
        table = self.query_one("#build-params-table", DataTable)
        table.clear(columns=False)
        
        log = self.query_one("#build-params-log", RichLog)
        log.clear()
        
        build_params = self._selected_payload_type.get("buildparameters", [])
        self._build_param_list = build_params
        
        if not build_params:
            _safe_richlog_write(log, "[dim]No build parameters required[/dim]")
            return
        
        _safe_richlog_write(log, "[bold]Click a parameter to edit its value[/bold]\n")
        
        for idx, param in enumerate(build_params):
            name = param.get("name", "")
            param_type = param.get("parameter_type", "")
            required = param.get("required", False)
            default = param.get("default_value", "")
            desc = param.get("description", "")
            choices = param.get("choices", "")
            
            # Initialize with default value
            if name not in self._build_params and default:
                self._build_params[name] = default
            
            current = self._build_params.get(name, default)
            req_marker = "✓" if required else ""
            
            # Add to table
            table.add_row(name, param_type, str(current) if current else "", req_marker)
            
            # Add description to log
            if desc:
                _safe_richlog_write(log, f"[cyan]{name}:[/cyan] {rich_escape(desc)}")
            if choices:
                choices_str = ", ".join(choices) if isinstance(choices, list) else str(choices)
                _safe_richlog_write(log, f"  [yellow]Choices:[/yellow] {rich_escape(choices_str)}")
    
    def _populate_c2_profiles(self) -> None:
        """Populate C2 profiles table with selection status."""
        c2_table = self.query_one("#c2-profiles-table", DataTable)
        c2_table.clear(columns=False)
        
        hint = self.query_one("#c2-profile-hint", Static)
        hint.update("[dim]Click a profile to add/remove. Click selected profile to configure parameters.[/dim]")
        
        for idx, profile in enumerate(self._c2_profiles, 1):
            name = profile.get("name", "")
            desc = profile.get("description", "")[:30]
            running = "✓" if profile.get("running") else "✗"
            
            # Check if this profile is selected
            is_selected = any(p.get("name") == name for p in self._selected_c2_profiles)
            selected = "✓" if is_selected else ""
            
            c2_table.add_row(str(idx), name, desc, running, selected)
    
    def _show_c2_parameters(self) -> None:
        """Display C2 profile parameters for the currently selected C2."""
        if self._current_c2_index < 0 or self._current_c2_index >= len(self._selected_c2_profiles):
            return
        
        current_c2 = self._selected_c2_profiles[self._current_c2_index]
        c2_name = current_c2.get("name", "")
        
        table = self.query_one("#c2-params-table", DataTable)
        table.clear(columns=False)
        
        log = self.query_one("#c2-params-log", RichLog)
        log.clear()
        
        c2_params = current_c2.get("c2profileparameters", [])
        
        if not c2_params:
            _safe_richlog_write(log, "[dim]No C2 parameters required[/dim]")
            return
        
        _safe_richlog_write(log, f"[bold cyan]Configuring: {c2_name}[/bold cyan]")
        _safe_richlog_write(log, "[bold]Use 'setc2 <param> <value>' to set parameters[/bold]\n")
        
        # Initialize params for this C2 if not already done
        if c2_name not in self._c2_profile_params:
            self._c2_profile_params[c2_name] = {}
        
        for param in c2_params:
            name = param.get("name", "")
            param_type = param.get("parameter_type", "")
            required = param.get("required", False)
            default = param.get("default_value", "")
            desc = param.get("description", "")
            choices = param.get("choices", "")
            
            # Initialize with default value
            if name not in self._c2_profile_params[c2_name] and default:
                self._c2_profile_params[c2_name][name] = default
            
            current = self._c2_profile_params[c2_name].get(name, default)
            req_marker = "✓" if required else ""
            
            # Add to table
            table.add_row(name, param_type, str(current) if current else "", req_marker)
            
            # Add description to log
            if desc:
                _safe_richlog_write(log, f"[cyan]{name}:[/cyan] {rich_escape(desc)}")
            if choices:
                try:
                    if isinstance(choices, list):
                        # Handle list of strings or dicts
                        choice_items = []
                        for choice in choices:
                            if isinstance(choice, dict):
                                choice_items.append(str(choice.get("name", str(choice))))
                            else:
                                choice_items.append(str(choice))
                        choices_str = ", ".join(choice_items) if choice_items else ""
                    else:
                        choices_str = str(choices)
                    
                    if choices_str:
                        _safe_richlog_write(log, f"  [yellow]Choices:[/yellow] {rich_escape(choices_str)}")
                except Exception:
                    # Silently skip if we can't format choices
                    pass
    
    @work(thread=True)
    def _fetch_and_add_c2_profile(self, profile_name: str) -> None:
        """Fetch full C2 profile details with parameters and add it to selection."""
        try:
            # Fetch full profile with parameters
            full_profile = self.client.get_c2_profile_with_parameters(profile_name)
            
            if not full_profile:
                self.app.call_from_thread(self._update_status, f"[red]Failed to fetch profile: {profile_name}[/red]")
                return
            
            # Add to selected profiles
            self._selected_c2_profiles.append(full_profile)
            self._current_c2_index = len(self._selected_c2_profiles) - 1
            
            self.app.call_from_thread(self._update_status, f"[green]Added:[/green] {profile_name}")
            self.app.call_from_thread(self._populate_c2_profiles)
            
            # Update UI to show params
            def show_params():
                try:
                    self.query_one("#c2-params-table", DataTable).display = True
                    self.query_one("#c2-params-log", RichLog).display = True
                    self._show_c2_parameters()
                except Exception as e:
                    import traceback
                    self._update_status(f"[red]Error showing params: {str(e)}\n{traceback.format_exc()}[/red]")
            
            self.app.call_from_thread(show_params)
        except Exception as exc:
            import traceback
            error_msg = f"[red]Error fetching profile: {exc}\n{traceback.format_exc()}[/red]"
            self.app.call_from_thread(self._update_status, error_msg)
    
    def _populate_payloads(self) -> None:
        """Populate available payloads for wrapper mode."""
        p_table = self.query_one("#payloads-table", DataTable)
        p_table.clear(columns=False)
        
        for idx, payload in enumerate(self._available_payloads, 1):
            uuid = payload.get("uuid", "")[:8]
            ptype = payload.get("payloadtype", {}).get("name", "")
            os_name = payload.get("os", "")
            filename = payload.get("filename", "")
            p_table.add_row(str(idx), uuid, ptype, os_name, filename)
    
    def _show_review(self) -> None:
        """Show review/summary before building."""
        log = self.query_one("#build-params-log", RichLog)
        log.display = True
        log.clear()
        
        if self.build_mode == "payload":
            _safe_richlog_write(log, "[bold cyan]Payload Configuration Review[/bold cyan]\n")
            pt_name = self._selected_payload_type.get('name', 'N/A') if self._selected_payload_type else 'N/A'
            _safe_richlog_write(log, f"[yellow]Type:[/yellow] {pt_name}")
            _safe_richlog_write(log, f"[yellow]OS:[/yellow] {self._selected_os or 'Not selected'}")
            _safe_richlog_write(log, f"[yellow]Build Parameters:[/yellow] {len(self._build_params)}")
            for k, v in self._build_params.items():
                _safe_richlog_write(log, f"  • {k} = {v}")
            _safe_richlog_write(log, f"[yellow]C2 Profiles:[/yellow] {len(self._selected_c2_profiles)}")
            for profile in self._selected_c2_profiles:
                c2_name = profile.get('name')
                c2_params = self._c2_profile_params.get(c2_name, {})
                _safe_richlog_write(log, f"  • {c2_name}")
                if c2_params:
                    for k, v in c2_params.items():
                        _safe_richlog_write(log, f"    - {k} = {v}")
        else:
            _safe_richlog_write(log, "[bold cyan]Wrapper Configuration Review[/bold cyan]\n")
            pt_name = self._selected_payload_type.get('name', 'N/A') if self._selected_payload_type else 'N/A'
            _safe_richlog_write(log, f"[yellow]Wrapper Type:[/yellow] {pt_name}")
            if self._wrapped_payload:
                _safe_richlog_write(log, f"[yellow]Wrapping:[/yellow] {self._wrapped_payload.get('filename', 'N/A')}")
            _safe_richlog_write(log, f"[yellow]Build Parameters:[/yellow] {len(self._build_params)}")
            for k, v in self._build_params.items():
                _safe_richlog_write(log, f"  • {k} = {v}")
    
    @work(thread=True)
    def _generate_payload(self) -> None:
        """Generate the payload or wrapper."""
        if not self._selected_payload_type:
            self.app.call_from_thread(self._update_status, "[red]Please select a payload type[/red]")
            return
        
        payload_type_name = self._selected_payload_type.get("name", "")
        
        # Create and show progress screen
        progress_screen = BuildProgressScreen(payload_type_name, self.client)
        self.app.call_from_thread(self.app.push_screen, progress_screen)
        
        # Build parameters array
        build_params_array = [
            {"name": k, "value": v} for k, v in self._build_params.items()
        ]
        
        # Build filename
        file_ext = self._selected_payload_type.get("file_extension", "")
        filename = f"{payload_type_name}.{file_ext}" if file_ext else payload_type_name
        
        if self.build_mode == "payload":
            if not self._selected_os:
                self.app.call_from_thread(progress_screen.mark_failed, "Please select target OS")
                return
            
            # C2 profiles array with parameters
            c2_profiles_array = []
            for profile in self._selected_c2_profiles:
                c2_name = profile["name"]
                c2_params = self._c2_profile_params.get(c2_name, {})
                c2_profiles_array.append({
                    "c2_profile": c2_name,
                    "c2_profile_parameters": c2_params
                })
            
            payload_config = {
                "payload_type": payload_type_name,
                "selected_os": self._selected_os,
                "filename": filename,
                "description": "",
                "build_parameters": build_params_array,
                "commands": [],
                "c2_profiles": c2_profiles_array
            }
            
            progress_screen.add_log_entry(f"[yellow]Payload Type:[/yellow] {payload_type_name}")
            progress_screen.add_log_entry(f"[yellow]Target OS:[/yellow] {self._selected_os}")
            progress_screen.add_log_entry(f"[yellow]C2 Profiles:[/yellow] {len(c2_profiles_array)}")
        else:
            # Wrapper mode
            if not self._wrapped_payload:
                self.app.call_from_thread(progress_screen.mark_failed, "Please select a payload to wrap")
                return
            
            wrapped_filename = self._wrapped_payload.get("filename", "")
            payload_config = {
                "payload_type": payload_type_name,
                "selected_os": self._wrapped_payload.get("os", ""),
                "filename": filename,
                "description": "",
                "build_parameters": build_params_array,
                "wrapper": True,
                "wrapped_payload": self._wrapped_payload.get("uuid", "")
            }
            
            progress_screen.add_log_entry(f"[yellow]Wrapper Type:[/yellow] {payload_type_name}")
            progress_screen.add_log_entry(f"[yellow]Wrapping:[/yellow] {wrapped_filename}")
        
        progress_screen.add_log_entry(f"[yellow]Filename:[/yellow] {filename}")
        progress_screen.add_log_entry(f"[yellow]Build Parameters:[/yellow] {len(build_params_array)}")
        progress_screen.add_log_entry("[cyan]Sending to server...[/cyan]")
        
        try:
            result = self.client.create_payload(payload_config)
            if result.get("status") == "success":
                uuid = result.get("uuid", "")
                progress_screen.add_log_entry(f"[cyan]Submitted to build queue - UUID: {uuid}[/cyan]")
                progress_screen.add_log_entry("[cyan]Waiting for build...[/cyan]")
                
                # Poll for build status
                import time
                max_wait = 300  # 5 minutes
                poll_interval = 1  # 1 second
                elapsed = 0
                last_step = None
                
                while elapsed < max_wait and not progress_screen.build_complete:
                    try:
                        payload_data = self.client.get_payload(uuid)
                        if not payload_data:
                            time.sleep(poll_interval)
                            elapsed += poll_interval
                            continue
                        
                        build_phase = payload_data.get("build_phase", "")
                        build_steps = payload_data.get("payload_build_steps", [])
                        
                        # Update progress with current step
                        if build_steps:
                            for step in build_steps:
                                if step.get("end_time") is None:
                                    step_name = step.get("step_name", "Unknown")
                                    if step_name != last_step:
                                        progress_screen.add_log_entry(f"[cyan]► {step_name}[/cyan]")
                                        last_step = step_name
                                    break
                        
                        if build_phase == "success":
                            progress_screen.add_log_entry("[green]✓ Build step completed[/green]")
                            self.app.call_from_thread(progress_screen.mark_complete, uuid)
                            break
                        elif build_phase == "error":
                            error_msg = payload_data.get("build_message", "Unknown error")
                            stderr = payload_data.get("build_stderr", "")
                            progress_screen.add_log_entry(f"[red]❌ Build failed[/red]")
                            if error_msg:
                                progress_screen.add_log_entry(f"[dim]Message:[/dim] {error_msg[:200]}")
                            if stderr:
                                progress_screen.add_log_entry(f"[dim]Error:[/dim] {stderr[:200]}")
                            self.app.call_from_thread(progress_screen.mark_failed, error_msg or stderr or "Unknown error")
                            break
                    except Exception:
                        pass  # Keep polling on error
                    
                    time.sleep(poll_interval)
                    elapsed += poll_interval
                
                if elapsed >= max_wait:
                    progress_screen.add_log_entry("[yellow]⚠ Build timeout (5 minutes)[/yellow]")
                    self.app.call_from_thread(progress_screen.mark_failed, "Build timeout")
            else:
                error = result.get("error", "Unknown error")
                self.app.call_from_thread(progress_screen.mark_failed, error)
        except Exception as exc:
            self.app.call_from_thread(progress_screen.mark_failed, str(exc))
    
    def _update_status(self, message: str) -> None:
        """Update the status message."""
        try:
            self.query_one("#pb-status", Static).update(message)
        except Exception:
            pass
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        btn_id = event.button.id
        
        if btn_id == "btn-new-payload":
            self._set_mode("payload")
            self.refresh_data()
        elif btn_id == "btn-new-wrapper":
            self._set_mode("wrapper")
            self.refresh_data()
        elif btn_id == "btn-nav-os":
            self._show_step(1)
        elif btn_id == "btn-nav-type":
            self._show_step(2)
        elif btn_id == "btn-nav-params":
            self._show_step(3)
        elif btn_id == "btn-nav-c2":
            self._show_step(4)
        elif btn_id == "btn-nav-review":
            self._show_step(5)
        elif btn_id == "btn-generate":
            self._generate_payload()
        elif btn_id == "btn-reset":
            self._reset()
        elif btn_id == "btn-back":
            self.app.pop_screen()
    
    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection in tables."""
        table_id = event.data_table.id
        row = event.data_table.get_row(event.row_key)
        
        if not row:
            return
        
        if table_id == "build-params-table":
            # Edit build parameter
            param_name = row[0]
            current_value = row[2]
            
            # Find the parameter definition
            param_def = None
            for p in self._build_param_list:
                if p.get("name") == param_name:
                    param_def = p
                    break
            
            if param_def:
                # Show choices or prompt for value
                param_type = param_def.get("parameter_type", "")
                choices = param_def.get("choices", [])
                
                if choices and isinstance(choices, list):
                    msg = f"[cyan]{param_name}[/cyan] - Type: set {param_name} <value>\nChoices: {', '.join(choices)}"
                else:
                    msg = f"[cyan]{param_name}[/cyan] ({param_type}) - Type: set {param_name} <value>"
                self._update_status(msg)
            return
        
        try:
            index = int(row[0]) - 1
        except (ValueError, IndexError):
            return
        
        if table_id == "os-table":
            # Get all unique OS values
            all_os = sorted(set(os_name for pt in self._payload_types for os_name in pt.get("supported_os", [])))
            if 0 <= index < len(all_os):
                self._selected_os = all_os[index]
                self._update_status(f"[green]Selected OS:[/green] {self._selected_os}")
                self._update_mode_indicator()
                self._show_step(2)
        elif table_id == "payload-types-table":
            if 0 <= index < len(self._filtered_payload_types):
                self._selected_payload_type = self._filtered_payload_types[index]
                name = self._selected_payload_type.get("name", "")
                self._update_status(f"[green]Selected:[/green] {name}")
                self._show_step(3)
        elif table_id == "c2-profiles-table":
            if 0 <= index < len(self._c2_profiles):
                profile = self._c2_profiles[index]
                profile_name = profile.get('name')
                
                # Check if already selected
                selected_idx = -1
                for i, p in enumerate(self._selected_c2_profiles):
                    if p.get('name') == profile_name:
                        selected_idx = i
                        break
                
                if selected_idx >= 0:
                    # If clicking on already selected profile, show its params for editing
                    if selected_idx == self._current_c2_index:
                        # Deselect if clicking same profile
                        self._selected_c2_profiles.pop(selected_idx)
                        self._current_c2_index = -1
                        self._update_status(f"[yellow]Removed:[/yellow] {profile_name}")
                        self._populate_c2_profiles()
                        # Hide params tables
                        self.query_one("#c2-params-table", DataTable).display = False
                        self.query_one("#c2-params-log", RichLog).display = False
                    else:
                        # Switch to this profile for editing
                        self._current_c2_index = selected_idx
                        self._update_status(f"[cyan]Editing:[/cyan] {profile_name}")
                        self.query_one("#c2-params-table", DataTable).display = True
                        self.query_one("#c2-params-log", RichLog).display = True
                        self._show_c2_parameters()
                else:
                    # Add new profile - fetch full details with parameters
                    self._fetch_and_add_c2_profile(profile_name)
        elif table_id == "payloads-table":
            if 0 <= index < len(self._available_payloads):
                self._wrapped_payload = self._available_payloads[index]
                filename = self._wrapped_payload.get("filename", "")
                self._update_status(f"[green]Selected to wrap:[/green] {filename}")
                self._update_mode_indicator()
                self._show_step(3)
    
    def _reset(self) -> None:
        """Reset the builder state."""
        self._selected_payload_type = None
        self._selected_os = None
        self._build_params = {}
        self._build_param_list = []
        self._selected_c2_profiles = []
        self._wrapped_payload = None
        self._filtered_payload_types = []
        self._c2_profile_params = {}
        self._current_c2_index = -1
        
        self._update_status("[yellow]Reset complete[/yellow]")
        self._show_step(1)
    
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle command input."""
        if event.input.id != "pb-input":
            return
        
        raw = event.value.strip()
        event.input.value = ""
        
        if not raw:
            return
        
        parts = raw.split()
        cmd = parts[0].lower()
        
        if cmd == "generate":
            self._generate_payload()
        elif cmd == "reset":
            self._reset()
        elif cmd == "back":
            self.app.pop_screen()
        elif cmd == "step" and len(parts) >= 2:
            try:
                step = int(parts[1])
                self._show_step(step)
            except ValueError:
                self._update_status(f"[red]Invalid step: {parts[1]}[/red]")
        elif cmd == "set" and len(parts) >= 3:
            param_name = parts[1]
            value = " ".join(parts[2:])
            
            # Convert boolean strings
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            else:
                # Try to parse as int
                try:
                    value = int(value)
                except ValueError:
                    # Try to parse JSON
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        pass
            
            self._build_params[param_name] = value
            self._update_status(f"[green]Set {param_name} = {value}[/green]")
            self._show_build_parameters()
        elif cmd == "setc2" and len(parts) >= 3:
            param_name = parts[1]
            value = " ".join(parts[2:])
            
            if self._current_c2_index < 0:
                self._update_status("[red]No C2 profile selected for editing[/red]")
                return
            
            current_c2 = self._selected_c2_profiles[self._current_c2_index]
            c2_name = current_c2.get("name", "")
            
            # Convert boolean strings
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            else:
                # Try to parse as int
                try:
                    value = int(value)
                except ValueError:
                    # Try to parse JSON
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        pass
            
            if c2_name not in self._c2_profile_params:
                self._c2_profile_params[c2_name] = {}
            
            self._c2_profile_params[c2_name][param_name] = value
            self._update_status(f"[green]Set {c2_name}.{param_name} = {value}[/green]")
            self._show_c2_parameters()
    
    def action_back(self) -> None:
        """Go back to previous screen."""
        self.app.pop_screen()
    
    def action_generate(self) -> None:
        """Generate payload."""
        self._generate_payload()
    
    def action_new_payload(self) -> None:
        """Start new payload creation."""
        self._set_mode("payload")
        self.refresh_data()
    
    def action_new_wrapper(self) -> None:
        """Start new wrapper creation."""
        self._set_mode("wrapper")
        self.refresh_data()
    
    def action_toggle_sidebar(self) -> None:
        """Toggle sidebar visibility."""
        sidebar = self.query_one("#pb-sidebar", Vertical)
        sidebar.display = not sidebar.display
        self.sidebar_visible = sidebar.display
