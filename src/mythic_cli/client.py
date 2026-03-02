"""Mythic API client for interacting with the Mythic C2 server."""

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from rich.console import Console

from .config import MythicConfig

console = Console()


class MythicAPIException(Exception):
    """Exception raised for Mythic API errors."""

    pass


class MythicClient:
    """Client for interacting with Mythic C2 API."""

    def __init__(self, config: MythicConfig):
        """Initialize the Mythic client.

        Args:
            config: MythicConfig instance with connection settings.
        """
        self.config = config
        self.base_url = config.server_url.rstrip("/")

        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MythicAPIException(
                "Invalid server_url. Expected a full http(s) URL, e.g. https://mythic.example"
            )

        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise MythicAPIException(
                "Refusing insecure non-local HTTP server_url. Use HTTPS for remote Mythic servers."
            )

        self.api_token: Optional[str] = config.api_key
        self.current_user: Optional[str] = None
        self.client = httpx.Client(
            verify=config.verify_ssl,
            timeout=config.timeout,
            follow_redirects=False,
        )

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for GraphQL requests.

        Returns:
            Dictionary of headers including authentication.
        """
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers
    
    def _graphql_query(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a GraphQL query.

        Args:
            query: GraphQL query string.
            variables: Optional variables for the query.

        Returns:
            Response data from GraphQL.

        Raises:
            MythicAPIException: If query fails.
        """
        url = f"{self.base_url}/graphql/"
        headers = self._get_headers()
        payload: Dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            response = self.client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            
            if "errors" in result:
                error_msg = result["errors"][0].get("message", "Unknown GraphQL error")
                raise MythicAPIException(f"GraphQL error: {error_msg}")
            
            return result.get("data", {})
        except httpx.HTTPStatusError as e:
            raise MythicAPIException(f"GraphQL request failed: {e.response.status_code}")
        except Exception as e:
            raise MythicAPIException(f"GraphQL request error: {str(e)}")

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """Login to Mythic server and get API token.

        Args:
            username: Username for authentication. Uses config if not provided.
            password: Password for authentication. Uses config if not provided.

        Returns:
            True if login successful, False otherwise.

        Raises:
            MythicAPIException: If login fails.
        """
        username = username or self.config.username
        password = password or self.config.password

        if not username or not password:
            raise MythicAPIException("Username and password are required for login")

        # Try multiple endpoints (Mythic versions use different paths)
        endpoints = [
            "/auth",           # Primary endpoint for newer Mythic
        ]
        
        last_error = None
        
        for endpoint in endpoints:
            try:
                response = self.client.post(
                    f"{self.base_url}{endpoint}",
                    json={"username": username, "password": password},
                )
                
                # Try to parse JSON response
                try:
                    data = response.json()
                except:
                    # Not JSON (might be HTML), skip this endpoint
                    last_error = f"{endpoint}: Non-JSON response (status {response.status_code})"
                    continue
                
                # Check for successful login - look for access_token or status success
                if response.status_code == 200 and ("access_token" in data or data.get("status") == "success"):
                    self.api_token = data.get("access_token")
                    if self.api_token:
                        self.current_user = username
                        console.print("[green]✅[/green] Successfully authenticated")
                        return True
                    else:
                        last_error = f"{endpoint}: No access_token in response"
                        continue
                elif response.status_code in [400, 401, 403]:
                    # Authentication error - provide details
                    error_msg = data.get("error", "Authentication failed")
                    last_error = f"{endpoint}: {error_msg} (status {response.status_code})"
                    # Continue trying other endpoints
                    continue
                else:
                    last_error = f"{endpoint}: Unexpected status {response.status_code}"
                    continue
                    
            except httpx.HTTPStatusError as e:
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("error", str(e))
                except:
                    error_msg = str(e)
                last_error = f"{endpoint}: {error_msg} (status {e.response.status_code})"
                continue
            except Exception as e:
                last_error = f"{endpoint}: {str(e)}"
                continue
        
        # If we get here, all endpoints failed
        if last_error:
            raise MythicAPIException(f"Login failed. Last error: {last_error}")
        else:
            raise MythicAPIException("Login failed: No valid authentication endpoint found")

    def get_current_user(self) -> Optional[str]:
        """Get the currently authenticated user.

        Returns:
            Username of the authenticated user, or None if not authenticated.

        Raises:
            MythicAPIException: If the query fails.
        """
        if not self.api_token:
            return None

        query = """
        query GetCurrentUser {
            operation(limit: 1, order_by: {id: desc}) {
                admin {
                    username
                }
            }
        }
        """
        try:
            result = self._graphql_query(query)
            operations = result.get("operation", [])
            if operations and len(operations) > 0:
                admin = operations[0].get("admin", {})
                return admin.get("username")
            return None
        except Exception:
            return None

    # Callback operations
    def get_callbacks(self, operation_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get active callbacks.

        Args:
            operation_id: Optional operation ID to filter callbacks.

        Returns:
            List of callback dictionaries.
        """
        if operation_id:
            query = """
            query GetCallbacks($operation_id: Int!) {
                callback(where: {operation_id: {_eq: $operation_id}}, order_by: {id: desc}) {
                    id
                    display_id
                    agent_callback_id
                    init_callback
                    description
                    user
                    host
                    pid
                    ip
                    integrity_level
                    domain
                    os
                    architecture
                    sleep_info
                    active
                    process_name
                    last_checkin
                    payload {
                        payloadtype {
                            name
                        }
                    }
                }
            }
            """
            result = self._graphql_query(query, {"operation_id": operation_id})
        else:
            query = """
            query GetCallbacks {
                callback(order_by: {id: desc}) {
                    id
                    display_id
                    agent_callback_id
                    init_callback
                    description
                    user
                    host
                    pid
                    ip
                    integrity_level
                    domain
                    os
                    architecture
                    sleep_info
                    active
                    process_name
                    last_checkin
                    payload {
                        payloadtype {
                            name
                        }
                    }
                }
            }
            """
            result = self._graphql_query(query)
        return result.get("callback", [])

    def get_callback(self, callback_id: int) -> Dict[str, Any]:
        """Get details for a specific callback.

        Args:
            callback_id: ID of the callback.

        Returns:
            Callback data dictionary.
        """
        query = """
        query GetCallback($id: Int!) {
            callback_by_pk(id: $id) {
                id
                display_id
                agent_callback_id
                description
                user
                host
                pid
                ip
                integrity_level
                domain
                os
                architecture
                sleep_info
                last_checkin
                active
                payload {
                    payloadtype {
                        name
                    }
                }
            }
        }
        """
        result = self._graphql_query(query, {"id": callback_id})
        return result.get("callback_by_pk", {})

    def get_callback_commands(self, callback_id: int) -> List[Dict[str, Any]]:
        """Get available commands for a callback based on its payload type.

        Args:
            callback_id: ID of the callback.

        Returns:
            List of available command dictionaries with name and description.
        """
        query = """
        query GetCallbackCommands($id: Int!) {
            callback_by_pk(id: $id) {
                payload {
                    payloadtype {
                        name
                        commands {
                            cmd
                            description
                            help_cmd
                        }
                    }
                }
            }
        }
        """
        result = self._graphql_query(query, {"id": callback_id})
        callback = result.get("callback_by_pk", {})
        if callback and "payload" in callback and callback["payload"]:
            payload = callback["payload"]
            if "payloadtype" in payload and payload["payloadtype"]:
                return payload["payloadtype"].get("commands", [])
        return []

    def update_callback(self, callback_id: int, **kwargs) -> Dict[str, Any]:
        """Update callback properties.

        Args:
            callback_id: ID of the callback to update.
            **kwargs: Properties to update (description, sleep_info, active, etc.)

        Returns:
            Updated callback data.
        """
        # Build dynamic _set object from kwargs
        set_fields = ", ".join([f"{key}: ${key}" for key in kwargs.keys()])
        return_fields = ", ".join(["id"] + list(kwargs.keys()))
        
        mutation = f"""
        mutation UpdateCallback($id: Int!, {', '.join([f"${key}: {self._get_graphql_type(key, kwargs[key])}" for key in kwargs.keys()])}) {{
            update_callback_by_pk(pk_columns: {{id: $id}}, _set: {{{set_fields}}}) {{
                {return_fields}
            }}
        }}
        """
        variables = {"id": callback_id, **kwargs}
        result = self._graphql_query(mutation, variables)
        return result.get("update_callback_by_pk", {})
    
    def _get_graphql_type(self, key: str, value: Any) -> str:
        """Get GraphQL type for a value."""
        if isinstance(value, bool):
            return "Boolean"
        elif isinstance(value, int):
            return "Int"
        elif isinstance(value, float):
            return "Float"
        else:
            return "String"

    # Task operations
    def create_task(
        self,
        callback_id: int,
        command: str,
        params: Optional[Any] = None,
        files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new task for a callback.

        Args:
            callback_id: ID of the callback to task.
            command: Command name to execute.
            params: Optional parameters for the command.
            files: Optional list of Mythic file UUIDs uploaded as part of this task.

        Returns:
            Created task data.
        """
        mutation = """
        mutation CreateTask($callback_id: Int!, $command: String!, $params: String!, $files: [String]) {
            createTask(callback_id: $callback_id, command: $command, params: $params, files: $files) {
                id
                display_id
                status
                error
            }
        }
        """
        # Mythic expects params as a string. For commands like "help" with no args,
        # send an empty string so the task is "help" instead of "help {}".
        if params is None:
            serialized_params = ""
        elif isinstance(params, str):
            serialized_params = params
        else:
            serialized_params = json.dumps(params)

        def _submit_task(target_callback_id: int) -> Dict[str, Any]:
            variables = {
                "callback_id": target_callback_id,
                "command": command,
                "params": serialized_params,
                "files": files if files else None,
            }
            result = self._graphql_query(mutation, variables)
            return result.get("createTask", {}) or {}

        task = _submit_task(callback_id)

        # Mythic can return a task-like object with id/display_id of 0 for failed/invalid tasking.
        # Treat non-positive IDs as task creation failure to avoid false success messages.
        internal_id = task.get("id")
        display_id = task.get("display_id")
        status = task.get("status", "")
        error_msg = task.get("error", "")

        internal_ok = isinstance(internal_id, int) and internal_id > 0
        display_ok = isinstance(display_id, int) and display_id > 0

        if not internal_ok and not display_ok:
            # Some Mythic deployments expect createTask(callback_id=display_id) instead of callback PK id.
            # If the initial tasking failed due to callback lookup, resolve callback PK -> display_id and retry once.
            if (
                isinstance(error_msg, str)
                and "Failed to get callback information" in error_msg
                and isinstance(callback_id, int)
                and callback_id > 0
            ):
                try:
                    callback = self.get_callback(callback_id)
                    display_callback_id = callback.get("display_id") if callback else None
                    if isinstance(display_callback_id, int) and display_callback_id > 0 and display_callback_id != callback_id:
                        retry_task = _submit_task(display_callback_id)
                        retry_internal_id = retry_task.get("id")
                        retry_display_id = retry_task.get("display_id")
                        retry_internal_ok = isinstance(retry_internal_id, int) and retry_internal_id > 0
                        retry_display_ok = isinstance(retry_display_id, int) and retry_display_id > 0
                        if retry_internal_ok or retry_display_ok:
                            return retry_task

                        task = retry_task
                        internal_id = retry_internal_id
                        display_id = retry_display_id
                        status = retry_task.get("status", "")
                        error_msg = retry_task.get("error", "")
                except Exception:
                    # Keep original error handling path if resolution/retry fails.
                    pass

            status_msg = f" (status: {status})" if status else ""
            error_detail = f"\nError: {error_msg}" if error_msg else ""
            raise MythicAPIException(
                f"Task creation failed for command '{command}'{status_msg}. Params sent: {serialized_params!r}{error_detail}"
            )

        return task

    def get_tasks(self, callback_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get tasks.

        Args:
            callback_id: Optional callback ID to filter tasks.

        Returns:
            List of task dictionaries.
        """
        if callback_id:
            query = """
            query GetTasks($callback_id: Int!) {
                task(where: {callback_id: {_eq: $callback_id}}, order_by: {id: desc}) {
                    id
                    display_id
                    command_name
                    callback {
                        display_id
                    }
                    command {
                        cmd
                    }
                    params
                    status
                    completed
                    timestamp
                    response_count
                    comment
                    stdout
                    stderr
                    operator {
                        username
                    }
                }
            }
            """
            result = self._graphql_query(query, {"callback_id": callback_id})
        else:
            query = """
            query GetAllTasks {
                task(order_by: {id: desc}, limit: 100) {
                    id
                    display_id
                    command_name
                    callback {
                        display_id
                    }
                    command {
                        cmd
                    }
                    params
                    status
                    completed
                    timestamp
                    response_count
                    comment
                    stdout
                    stderr
                    operator {
                        username
                    }
                }
            }
            """
            result = self._graphql_query(query)
        return result.get("task", [])

    def get_task_output(self, task_id: int) -> List[Dict[str, Any]]:
        """Get output for a specific task.

        Args:
            task_id: ID of the task.

        Returns:
            List of task responses/output.
        """
        query = """
        query GetTaskOutput($task_id: Int!) {
            response(where: {task_id: {_eq: $task_id}}, order_by: {id: asc}) {
                id
                response: response_text
                response_escape
                timestamp
            }
        }
        """
        result = self._graphql_query(query, {"task_id": task_id})
        return result.get("response", [])

    def get_task(self, task_id: int) -> Dict[str, Any]:
        """Get a specific task by ID.

        Args:
            task_id: ID of the task.

        Returns:
            Task dictionary.
        """
        query = """
        query GetTask($task_id: Int!) {
            task_by_pk(id: $task_id) {
                id
                display_id
                command_name
                params
                status
                completed
                timestamp
                callback_id
            }
        }
        """
        result = self._graphql_query(query, {"task_id": task_id})
        return result.get("task_by_pk", {})

    # Payload operations
    def get_payloads(self) -> List[Dict[str, Any]]:
        """Get all payloads.

        Returns:
            List of payload dictionaries.
        """
        query = """
        query GetPayloads {
            payload(order_by: {id: desc}) {
                id
                uuid
                description
                creation_time
                payloadtype {
                    name
                }
                os
                build_message
                build_phase
                deleted
            }
        }
        """
        result = self._graphql_query(query)
        return result.get("payload", [])

    def get_payload_types(self) -> List[Dict[str, Any]]:
        """Get all available payload types with their build parameters.

        Returns:
            List of payload type dictionaries with build parameters.
        """
        query = """
        query GetPayloadTypes {
            payloadtype(where: {deleted: {_eq: false}}, order_by: {name: asc}) {
                id
                name
                supported_os
                buildparameters(order_by: {name: asc}) {
                    id
                    name
                    parameter_type
                    description
                    required
                    default_value
                    choices
                }
                payloadtypec2profiles {
                    c2profile {
                        name
                    }
                }
            }
        }
        """
        result = self._graphql_query(query)
        return result.get("payloadtype", [])

    def get_payload_type(self, payload_type_name: str) -> Dict[str, Any]:
        """Get a specific payload type with build parameters.

        Args:
            payload_type_name: Name of the payload type.

        Returns:
            Payload type dictionary with build parameters.
        """
        query = """
        query GetPayloadType($name: String!) {
            payloadtype(where: {name: {_eq: $name}, deleted: {_eq: false}}, limit: 1) {
                id
                name
                supported_os
                buildparameters(order_by: {name: asc}) {
                    id
                    name
                    parameter_type
                    description
                    required
                    default_value
                    choices
                }
                payloadtypec2profiles {
                    c2profile {
                        name
                    }
                }
            }
        }
        """
        result = self._graphql_query(query, {"name": payload_type_name})
        types = result.get("payloadtype", [])
        return types[0] if types else {}

    def create_payload(self, payload_config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new payload.

        Args:
            payload_config: Payload configuration dictionary containing:
                - payload_type: Name of the payload type
                - build_parameters: Build parameter values

        Returns:
            Created payload data including UUID.
        """
        # Format: payloadDefinition is a JSON string with the payload definition
        payload_definition = {
            "payload_type": payload_config.get("payload_type", ""),
            "build_parameters": payload_config.get("build_parameters", {})
        }
        
        mutation = """
        mutation CreatePayload($definition: String!) {
            createPayload(payloadDefinition: $definition) {
                status
                error
                uuid
            }
        }
        """
        variables = {
            "definition": json.dumps(payload_definition)
        }
        result = self._graphql_query(mutation, variables)
        payload_result = result.get("createPayload", {})
        if payload_result.get("status") != "success":
            raise MythicAPIException(f"Failed to create payload: {payload_result.get('error', 'Unknown error')}")
        return payload_result

    def download_payload(self, payload_uuid: str) -> bytes:
        """Download a payload file.

        Args:
            payload_uuid: UUID of the payload to download.

        Returns:
            Payload file contents as bytes.
        """
        # Payload downloads may use a different endpoint
        raise MythicAPIException("Payload download not yet implemented via GraphQL.")

    # Operation management
    def get_operations(self) -> List[Dict[str, Any]]:
        """Get all operations.

        Returns:
            List of operation dictionaries.
        """
        query = """
        query GetOperations {
            operation(order_by: {id: desc}) {
                id
                name
                admin_id
                complete
                webhook
            }
        }
        """
        result = self._graphql_query(query)
        return result.get("operation", [])

    def get_current_operation(self) -> Dict[str, Any]:
        """Get the current operation.

        Returns:
            Current operation data.
        """
        query = """
        query GetCurrentOperation {
            operation(where: {complete: {_eq: false}}, limit: 1, order_by: {id: desc}) {
                id
                name
                admin_id
                complete
                webhook
            }
        }
        """
        result = self._graphql_query(query)
        ops = result.get("operation", [])
        return ops[0] if ops else {}

    def set_current_operation(self, operation_id: int) -> Dict[str, Any]:
        """Set the current operation.

        Args:
            operation_id: ID of the operation to set as current.

        Returns:
            Updated operation data.
        """
        raise MythicAPIException("Setting current operation not yet implemented via GraphQL.")

    # C2 Profile operations
    def get_c2_profiles(self) -> List[Dict[str, Any]]:
        """Get all C2 profiles.

        Returns:
            List of C2 profile dictionaries.
        """
        query = """
        query GetC2Profiles {
            c2profile(order_by: {name: asc}) {
                id
                name
                description
                is_p2p
                running
            }
        }
        """
        result = self._graphql_query(query)
        return result.get("c2profile", [])

    def get_c2_profile(self, profile_name: str) -> Dict[str, Any]:
        """Get a specific C2 profile.

        Args:
            profile_name: Name of the C2 profile.

        Returns:
            C2 profile data.
        """
        query = """
        query GetC2Profile($name: String!) {
            c2profile(where: {name: {_eq: $name}}, limit: 1) {
                id
                name
                description
                is_p2p
                running
            }
        }
        """
        result = self._graphql_query(query, {"name": profile_name})
        profiles = result.get("c2profile", [])
        return profiles[0] if profiles else {}

    def update_c2_profile(
        self, profile_name: str, parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update C2 profile parameters.

        Args:
            profile_name: Name of the C2 profile.
            parameters: Parameters to update.

        Returns:
            Updated profile data.
        """
        raise MythicAPIException("C2 profile updates not yet implemented via GraphQL.")

    # File operations
    def upload_file(self, file_path: str, file_data: bytes, comment: str = "Uploaded via mythic-cli") -> Dict[str, Any]:
        """Upload a file to Mythic.

        Args:
            file_path: Path/name for the file.
            file_data: File contents as bytes.
            comment: Optional comment to associate with the uploaded file.

        Returns:
            Uploaded file metadata.
        """
        url = f"{self.base_url}/api/v1.4/task_upload_file_webhook"
        headers: Dict[str, str] = {"MythicSource": "cli"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            response = self.client.post(
                url,
                headers=headers,
                data={"comment": comment},
                files={"file": (file_path, file_data)},
            )
            response.raise_for_status()
            data = response.json()

            # Mythic commonly returns agent_file_id on success.
            if isinstance(data, dict) and data.get("agent_file_id"):
                return data

            error_msg = data.get("error") if isinstance(data, dict) else None
            if error_msg:
                raise MythicAPIException(f"File upload failed: {error_msg}")

            raise MythicAPIException("File upload failed: missing agent_file_id in response")
        except httpx.HTTPStatusError as e:
            raise MythicAPIException(f"File upload failed: {e.response.status_code} {e.response.reason_phrase}")
        except ValueError:
            raise MythicAPIException("File upload failed: invalid JSON response")
        except Exception as e:
            raise MythicAPIException(f"File upload error: {str(e)}")

    def download_file(self, file_uuid: str) -> bytes:
        """Download a file from Mythic using direct download URL.

        Args:
            file_uuid: UUID of the file to download.

        Returns:
            File contents as bytes.

        Raises:
            MythicAPIException: If download fails.
        """
        url = f"{self.base_url}/direct/download/{file_uuid}"
        headers = self._get_headers()
        
        try:
            response = self.client.get(url, headers=headers)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:
            raise MythicAPIException(f"File download failed: {e.response.status_code} {e.response.reason_phrase}")
        except Exception as e:
            raise MythicAPIException(f"File download error: {str(e)}")

    def get_files(self) -> List[Dict[str, Any]]:
        """Get all files.

        Returns:
            List of file metadata dictionaries.
        """
        query = """
        query GetFiles {
            filemeta(order_by: {id: desc}) {
                id
                agent_file_id
                filename
                filename_text
                filename_utf8
                full_remote_path_text
                host
                size
                timestamp
                is_download_from_agent
                is_screenshot
                complete
                deleted
                operator {
                    username
                }
            }
        }
        """
        result = self._graphql_query(query)
        return result.get("filemeta", [])

    # Credential operations
    def get_credentials(self) -> List[Dict[str, Any]]:
        """Get all credentials.

        Returns:
            List of credential dictionaries.
        """
        query = """
        query GetCredentials {
            credential(order_by: {id: desc}) {
                id
                type
                account
                realm
                credential_text
                comment
            }
        }
        """
        result = self._graphql_query(query)
        return result.get("credential", [])

    def create_credential(self, credential_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new credential entry.

        Args:
            credential_data: Credential information (realm, credential_type, account, etc.)

        Returns:
            Created credential data.
        """
        mutation = """
        mutation CreateCredential($type: String!, $account: String!, $credential: String!, $realm: String, $comment: String) {
            createCredential(
                credential_type: $type
                account: $account
                credential: $credential
                realm: $realm
                comment: $comment
            ) {
                status
                error
                id
            }
        }
        """
        variables = {
            "type": credential_data.get("type", "plaintext"),
            "account": credential_data.get("account", ""),
            "credential": credential_data.get("credential", ""),
            "realm": credential_data.get("realm", ""),
            "comment": credential_data.get("comment", "")
        }
        result = self._graphql_query(mutation, variables)
        cred_result = result.get("createCredential", {})
        if cred_result.get("status") != "success":
            raise MythicAPIException(f"Failed to create credential: {cred_result.get('error', 'Unknown error')}")
        return cred_result

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()
