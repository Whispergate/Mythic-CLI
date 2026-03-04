"""Click-based interactive payload builder for Mythic CLI."""

from typing import Any, Dict, List, Optional

import click
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

console = Console()


class ClickPayloadBuilder:
    """Interactive payload builder using Click prompts."""
    
    def __init__(self, client):
        """Initialize the builder with a Mythic client."""
        self.client = client
        self.build_mode = "payload"  # "payload" or "wrapper"
        self.payload_types: List[Dict[str, Any]] = []
        self.c2_profiles: List[Dict[str, Any]] = []
        self.available_payloads: List[Dict[str, Any]] = []
        
        # Selected values
        self.selected_os: Optional[str] = None
        self.selected_payload_type: Optional[Dict[str, Any]] = None
        self.selected_c2_profiles: List[Dict[str, Any]] = []
        self.wrapped_payload: Optional[Dict[str, Any]] = None
        self.build_params: Dict[str, Any] = {}
        self.c2_profile_params: Dict[str, Dict[str, Any]] = {}
    
    def load_data(self) -> bool:
        """Load payload types, C2 profiles, and available payloads."""
        try:
            console.print("[cyan]Loading payload types...[/cyan]", end=" ")
            self.payload_types = self.client.get_payload_types()
            console.print(f"[green]✓[/green] ({len(self.payload_types)} found)")
            
            console.print("[cyan]Loading C2 profiles...[/cyan]", end=" ")
            self.c2_profiles = self.client.get_c2_profiles()
            console.print(f"[green]✓[/green] ({len(self.c2_profiles)} found)")
            
            if self.build_mode == "wrapper":
                console.print("[cyan]Loading available payloads...[/cyan]", end=" ")
                self.available_payloads = self.client.get_payloads()
                console.print(f"[green]✓[/green] ({len(self.available_payloads)} found)")
            
            return True
        except Exception as exc:
            console.print(f"[red]Error loading data:[/red] {exc}")
            return False
    
    def select_os(self) -> bool:
        """Prompt user to select target OS."""
        # Collect unique OS from payload types
        all_os = sorted(set(os_name for pt in self.payload_types for os_name in pt.get("supported_os", [])))
        
        if not all_os:
            console.print("[red]No operating systems available[/red]")
            return False
        
        console.print("\n[bold cyan]Available Operating Systems:[/bold cyan]")
        for idx, os_name in enumerate(all_os, 1):
            console.print(f"  {idx}. {os_name}")
        
        while True:
            choice = Prompt.ask("Select OS", choices=[str(i) for i in range(1, len(all_os) + 1)], default="1")
            try:
                idx = int(choice) - 1
                self.selected_os = all_os[idx]
                console.print(f"[green]✓[/green] Selected: {self.selected_os}")
                return True
            except (ValueError, IndexError):
                console.print("[red]Invalid choice[/red]")
    
    def select_payload_type(self) -> bool:
        """Prompt user to select payload type."""
        # Filter by OS for regular payloads, or show only wrappers for wrapper mode
        if self.build_mode == "wrapper":
            filtered_types = [pt for pt in self.payload_types if pt.get("wrapper")]
        else:
            filtered_types = [
                pt for pt in self.payload_types
                if self.selected_os in pt.get("supported_os", [])
            ]
        
        if not filtered_types:
            console.print("[red]No payload types available[/red]")
            return False
        
        console.print("\n[bold cyan]Available Payload Types:[/bold cyan]")
        for idx, pt in enumerate(filtered_types, 1):
            name = pt.get("name", "")
            wrapper = " [yellow](Wrapper)[/yellow]" if pt.get("wrapper") else ""
            console.print(f"  {idx}. {name}{wrapper}")
        
        while True:
            choice = Prompt.ask("Select payload type", choices=[str(i) for i in range(1, len(filtered_types) + 1)], default="1")
            try:
                idx = int(choice) - 1
                self.selected_payload_type = filtered_types[idx]
                console.print(f"[green]✓[/green] Selected: {self.selected_payload_type.get('name')}")
                return True
            except (ValueError, IndexError):
                console.print("[red]Invalid choice[/red]")
    
    def configure_build_parameters(self) -> bool:
        """Prompt user to configure build parameters."""
        if not self.selected_payload_type:
            console.print("[red]No payload type selected[/red]")
            return False
        
        build_params = self.selected_payload_type.get("buildparameters", [])
        
        if not build_params:
            console.print("[yellow]No build parameters required[/yellow]")
            return True
        
        console.print(f"\n[bold cyan]Build Parameters ({len(build_params)}):[/bold cyan]")
        
        for param in build_params:
            name = param.get("name", "")
            param_type = param.get("parameter_type", "")
            required = param.get("required", False)
            default = param.get("default_value", "")
            description = param.get("description", "")
            choices = param.get("choices", [])
            
            # Build prompt text
            prompt_text = f"\n[cyan]{name}[/cyan]"
            if description:
                prompt_text += f"\n  {description}"
            if param_type:
                prompt_text += f"\n  Type: {param_type}"
            if choices:
                prompt_text += f"\n  Choices: {', '.join(str(c) for c in choices)}"
            if default and not required:
                prompt_text += f"\n  Default: {default}"
            
            prompt_text += "\nValue"
            
            # Get value from user
            if choices:
                value = Prompt.ask(
                    prompt_text,
                    choices=[str(c) for c in choices],
                    default=str(default) if default else None
                )
            else:
                default_str = str(default) if default else ""
                value = Prompt.ask(prompt_text, default=default_str if default_str else None)
            
            # Type conversion based on parameter_type
            if value:
                ptype = (param_type or "").lower()
                
                # STRING types must stay as strings
                if ptype in {"string", "str", "text"}:
                    pass  # Keep value as-is
                elif ptype in {"boolean", "bool"}:
                    if isinstance(value, str):
                        value = value.lower() in ("true", "t", "1", "yes", "y")
                elif ptype in {"number", "integer", "int"}:
                    try:
                        value = int(value)
                    except ValueError:
                        try:
                            value = float(value)
                        except ValueError:
                            pass
                elif ptype in {"dictionary", "array"}:
                    if isinstance(value, str):
                        try:
                            import json
                            value = json.loads(value)
                        except json.JSONDecodeError:
                            pass
                else:
                    # Best-effort fallback for untyped params
                    if isinstance(value, str):
                        if value.lower() in ("true", "false"):
                            value = value.lower() == "true"
                        else:
                            try:
                                value = int(value)
                            except ValueError:
                                try:
                                    import json
                                    value = json.loads(value)
                                except json.JSONDecodeError:
                                    pass
            
            self.build_params[name] = value
        
        return True
    
    def select_c2_profiles(self) -> bool:
        """Prompt user to select C2 profiles."""
        if not self.c2_profiles:
            console.print("[yellow]No C2 profiles available[/yellow]")
            return True
        
        console.print("\n[bold cyan]Available C2 Profiles:[/bold cyan]")
        for idx, profile in enumerate(self.c2_profiles, 1):
            name = profile.get("name", "")
            desc = profile.get("description", "")[:50]
            running = "[green]●[/green]" if profile.get("running") else "[red]●[/red]"
            console.print(f"  {idx}. {running} {name}")
            if desc:
                console.print(f"     {desc}")
        
        selected_indices = []
        console.print("\n[dim]Enter profile numbers separated by commas (e.g., 1,3,4), or press Enter to skip[/dim]")
        
        while True:
            choice = Prompt.ask("Select profiles", default="")
            if not choice:
                break
            
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                if all(0 <= i < len(self.c2_profiles) for i in indices):
                    selected_indices = indices
                    break
                else:
                    console.print("[red]Invalid indices[/red]")
            except ValueError:
                console.print("[red]Invalid input[/red]")
        
        self.selected_c2_profiles = [self.c2_profiles[i] for i in selected_indices]
        
        if self.selected_c2_profiles:
            console.print(f"[green]✓[/green] Selected {len(self.selected_c2_profiles)} profile(s)")
            
            # Configure each C2 profile's parameters
            for profile in self.selected_c2_profiles:
                self._configure_c2_profile_params(profile)
        
        return True
    
    def _configure_c2_profile_params(self, profile: Dict[str, Any]) -> None:
        """Configure parameters for a specific C2 profile."""
        profile_name = profile.get("name", "")
        c2_params = profile.get("c2profileparameters", [])
        
        if not c2_params:
            return
        
        console.print(f"\n[bold cyan]Configuring C2 Profile: {profile_name}[/bold cyan]")
        self.c2_profile_params[profile_name] = {}
        
        for param in c2_params:
            name = param.get("name", "")
            param_type = param.get("parameter_type", "")
            required = param.get("required", False)
            default = param.get("default_value", "")
            description = param.get("description", "")
            
            if not required and not Confirm.ask(f"Configure {name}?", default=False):
                if default:
                    self.c2_profile_params[profile_name][name] = default
                continue
            
            # Build prompt
            prompt_text = f"\n[cyan]{name}[/cyan]"
            if description:
                prompt_text += f"\n  {description}"
            if param_type:
                prompt_text += f"\n  Type: {param_type}"
            if default:
                prompt_text += f"\n  Default: {default}"
            prompt_text += "\nValue"
            
            value = Prompt.ask(prompt_text, default=str(default) if default else None)
            
            # Type conversion
            if value:
                if value.lower() in ("true", "false"):
                    value = value.lower() == "true"
                else:
                    try:
                        value = int(value)
                    except ValueError:
                        try:
                            import json
                            value = json.loads(value)
                        except:
                            pass
            
            self.c2_profile_params[profile_name][name] = value
    
    def select_payload_to_wrap(self) -> bool:
        """Prompt user to select a payload to wrap."""
        if not self.available_payloads:
            console.print("[red]No payloads available to wrap[/red]")
            return False
        
        console.print("\n[bold cyan]Available Payloads to Wrap:[/bold cyan]")
        for idx, payload in enumerate(self.available_payloads, 1):
            filename = payload.get("filename", "")
            ptype = payload.get("payloadtype", {}).get("name", "")
            os_name = payload.get("os", "")
            console.print(f"  {idx}. {filename} ({ptype} / {os_name})")
        
        while True:
            choice = Prompt.ask("Select payload", choices=[str(i) for i in range(1, len(self.available_payloads) + 1)], default="1")
            try:
                idx = int(choice) - 1
                self.wrapped_payload = self.available_payloads[idx]
                console.print(f"[green]✓[/green] Selected: {self.wrapped_payload.get('filename')}")
                return True
            except (ValueError, IndexError):
                console.print("[red]Invalid choice[/red]")
    
    def show_review(self) -> None:
        """Display a review of the configuration before building."""
        console.print("\n[bold cyan]Configuration Review:[/bold cyan]")
        
        if self.build_mode == "wrapper":
            console.print(f"[yellow]Mode:[/yellow] Wrapper")
            console.print(f"[yellow]Wrapper Type:[/yellow] {self.selected_payload_type.get('name')}")
            if self.wrapped_payload:
                console.print(f"[yellow]Wrapping:[/yellow] {self.wrapped_payload.get('filename')}")
        else:
            console.print(f"[yellow]Mode:[/yellow] Payload")
            console.print(f"[yellow]OS:[/yellow] {self.selected_os}")
            console.print(f"[yellow]Type:[/yellow] {self.selected_payload_type.get('name')}")
        
        if self.build_params:
            console.print(f"[yellow]Build Parameters:[/yellow]")
            for name, value in self.build_params.items():
                console.print(f"  • {name} = {value}")
        
        if self.selected_c2_profiles:
            console.print(f"[yellow]C2 Profiles ({len(self.selected_c2_profiles)}):[/yellow]")
            for profile in self.selected_c2_profiles:
                name = profile.get("name")
                params = self.c2_profile_params.get(name, {})
                console.print(f"  • {name}")
                if params:
                    for pname, pvalue in params.items():
                        console.print(f"    - {pname} = {pvalue}")
    
    def build(self) -> bool:
        """Build the payload."""
        if not self.selected_payload_type:
            console.print("[red]Payload type not selected[/red]")
            return False
        
        # Prepare configuration
        payload_type_name = self.selected_payload_type.get("name", "")
        file_ext = self.selected_payload_type.get("file_extension", "")
        filename = f"{payload_type_name}.{file_ext}" if file_ext else payload_type_name
        
        build_params_array = [
            {"name": k, "value": v} for k, v in self.build_params.items()
        ]
        
        if self.build_mode == "wrapper":
            if not self.wrapped_payload:
                console.print("[red]Payload to wrap not selected[/red]")
                return False
            
            payload_config = {
                "payload_type": payload_type_name,
                "selected_os": self.wrapped_payload.get("os", ""),
                "filename": filename,
                "description": "",
                "build_parameters": build_params_array,
                "wrapper": True,
                "wrapped_payload": self.wrapped_payload.get("uuid", "")
            }
        else:
            if not self.selected_os:
                console.print("[red]OS not selected[/red]")
                return False
            
            c2_profiles_array = []
            for profile in self.selected_c2_profiles:
                c2_name = profile["name"]
                c2_params = self.c2_profile_params.get(c2_name, {})
                c2_profiles_array.append({
                    "c2_profile": c2_name,
                    "c2_profile_parameters": c2_params
                })
            
            payload_config = {
                "payload_type": payload_type_name,
                "selected_os": self.selected_os,
                "filename": filename,
                "description": "",
                "build_parameters": build_params_array,
                "commands": [],
                "c2_profiles": c2_profiles_array
            }
        
        # Send to server
        console.print("\n[cyan]Building payload...[/cyan]")
        try:
            result = self.client.create_payload(payload_config)
            if result.get("status") == "success":
                uuid = result.get("uuid", "")
                console.print(f"[green]✅ Success![/green]")
                console.print(f"UUID: [cyan]{uuid}[/cyan]")
                return True
            else:
                error = result.get("error", "Unknown error")
                console.print(f"[red]Build failed:[/red] {error}")
                return False
        except Exception as exc:
            console.print(f"[red]Build failed:[/red] {exc}")
            return False
    
    def run_interactive(self, mode: str = "payload") -> None:
        """Run the interactive builder."""
        self.build_mode = mode
        console.print(f"\n[bold cyan]Mythic Payload Builder - {mode.upper()} Mode[/bold cyan]\n")
        
        # Load data
        if not self.load_data():
            return
        
        # Step 1: Select payload to wrap (for wrapper mode) or OS (for normal mode)
        if mode == "wrapper":
            if not self.select_payload_to_wrap():
                return
        else:
            if not self.select_os():
                return
        
        # Step 2: Select payload type
        if not self.select_payload_type():
            return
        
        # Step 3: Configure build parameters
        if not self.configure_build_parameters():
            return
        
        # Step 4: Select C2 profiles (optional for wrappers)
        if mode == "payload":
            if not self.select_c2_profiles():
                return
        
        # Step 5: Review
        self.show_review()
        
        if not Confirm.ask("\nProceed with build?"):
            console.print("[yellow]Cancelled[/yellow]")
            return
        
        # Build
        self.build()


def run_payload_builder_click(client, mode: str = "payload"):
    """Run the Click-based payload builder."""
    builder = ClickPayloadBuilder(client)
    builder.run_interactive(mode)
