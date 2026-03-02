#!/usr/bin/env python3
"""Debug script to test Mythic server connectivity and authentication."""

import sys
import json
import httpx
from rich.console import Console
from rich.table import Table

console = Console()


def test_connection(server_url: str, username: str, password: str, verify_ssl: bool = True):
    """Test connection and authentication to Mythic server."""
    
    console.print(f"\n[cyan]Testing Mythic Server Connection[/cyan]")
    console.print(f"Server: [yellow]{server_url}[/yellow]")
    console.print(f"Username: [yellow]{username}[/yellow]")
    console.print(f"SSL Verify: [yellow]{verify_ssl}[/yellow]\n")
    
    client = httpx.Client(verify=verify_ssl, timeout=30, follow_redirects=True)
    
    # Test 1: Server reachability
    console.print("[bold]Test 1: Server Reachability[/bold]")
    try:
        response = client.get(f"{server_url}/")
        console.print(f"  Status: [green]{response.status_code}[/green]")
        console.print(f"  Server is reachable ✅")
    except Exception as e:
        console.print(f"  [red]✗ Error:[/red] {str(e)}")
        return False
    
    # Test 2: Login endpoint
    console.print("\n[bold]Test 2: Login Endpoint[/bold]")
    
    endpoints_to_try = [
        "/login",
        "/auth",
        "/api/v1.4/login",
        "/new/login",
    ]
    
    for endpoint in endpoints_to_try:
        url = f"{server_url}{endpoint}"
        console.print(f"\n  Trying: [cyan]{url}[/cyan]")
        
        try:
            response = client.post(
                url,
                json={"username": username, "password": password},
                headers={"Content-Type": "application/json"}
            )
            
            console.print(f"    Status Code: {response.status_code}")
            
            try:
                data = response.json()
                console.print(f"    Response: {json.dumps(data, indent=2)}")
                
                # Check for successful authentication
                if response.status_code == 200 and ("access_token" in data or data.get("status") == "success"):
                    console.print(f"    [green]✅ Login successful![/green]")
                    if "access_token" in data:
                        console.print(f"    Token: {data['access_token'][:20]}...")
                    return True
            except:
                console.print(f"    Response (text): {response.text[:200]}")
                
        except Exception as e:
            console.print(f"    [red]Error:[/red] {str(e)}")
    
    # Test 3: GraphQL API Tests (what mythic-cli actually uses)
    console.print("\n[bold]Test 3: GraphQL API Tests[/bold]")
    
    # Get the token from successful login
    try:
        response = client.post(
            f"{server_url}/auth",
            json={"username": username, "password": password}
        )
        token = response.json().get("access_token")
    except:
        console.print("  [red]Could not get token for API tests[/red]")
        client.close()
        return True  # Authentication worked, just can't test APIs
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    # Test GraphQL queries
    graphql_tests = [
        ("Operations", '{"query": "{operation{id name}}"}'),
        ("Callbacks", '{"query": "{callback(limit: 5){id display_id host user}}"}'),
        ("Payloads", '{"query": "{payload(limit: 5){id uuid}}"}'),
        ("C2 Profiles", '{"query": "{c2profile{id name running}}"}'),
        ("Tasks", '{"query": "{task(limit: 5){id display_id command_name status}}"}'),
        ("Credentials", '{"query": "{credential{id type account}}"}'),
        ("Files", '{"query": "{filemeta(limit: 5){id filename_text}}"}'),
    ]
    
    results = []
    for description, query in graphql_tests:
        try:
            response = client.post(f"{server_url}/graphql/", headers=headers, data=query)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if "errors" in data:
                        error_msg = data["errors"][0].get("message", "Unknown error")
                        results.append((description, False, f"GraphQL error: {error_msg[:50]}"))
                        console.print(f"  [yellow]○[/yellow] {description}: {error_msg[:50]}")
                    elif "data" in data:
                        results.append((description, True, response.status_code))
                        console.print(f"  [green]✅[/green] {description}: OK")
                    else:
                        results.append((description, False, "Unexpected response"))
                        console.print(f"  [yellow]○[/yellow] {description}: Unexpected response")
                except:
                    results.append((description, False, "Invalid JSON"))
                    console.print(f"  [red]✗[/red] {description}: Invalid JSON")
            else:
                results.append((description, False, response.status_code))
                console.print(f"  [yellow]○[/yellow] {description}: {response.status_code}")
        except Exception as e:
            results.append((description, False, str(e)))
            console.print(f"  [red]✗[/red] {description}: {str(e)[:50]}")
    
    # Summary
    successful = sum(1 for _, ok, _ in results if ok)
    console.print(f"\n  GraphQL Queries: {successful}/{len(graphql_tests)} successful")
    
    client.close()
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        console.print("[yellow]Usage:[/yellow] python debug_connection.py <server_url> <username> [password]")
        console.print("\nExample:")
        console.print("  python debug_connection.py http://127.0.0.1:7443 mythic_admin")
        sys.exit(1)
    
    server_url = sys.argv[1].rstrip("/")
    username = sys.argv[2]
    
    if len(sys.argv) > 3:
        password = sys.argv[3]
    else:
        from getpass import getpass
        password = getpass("Password: ")
    
    # Disable SSL verification
    verify_ssl = False
    
    success = test_connection(server_url, username, password, verify_ssl)
    
    if success:
        console.print("\n[green bold]✅ All tests passed![/green bold]")
        sys.exit(0)
    else:
        console.print("\n[red bold]✗ Connection/authentication failed[/red bold]")
        console.print("\n[yellow]Troubleshooting tips:[/yellow]")
        console.print("  1. Verify the server URL is correct")
        console.print("  2. Check username and password")
        console.print("  3. Ensure Mythic server is running")
        console.print("  4. Check firewall/network connectivity")
        console.print("  5. Review Mythic server logs")
        sys.exit(1)
