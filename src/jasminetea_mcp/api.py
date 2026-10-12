import asyncio
import json
import re
import ssl
from importlib.resources import files
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx

from .config import Settings, repository
from .errors import TeaError
from .policy import api_path_pattern


class GiteaClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        network = settings.network_environment()
        verify = (
            ssl.create_default_context(
                cafile=network.get("SSL_CERT_FILE"), capath=network.get("SSL_CERT_DIR")
            )
            if network.get("SSL_CERT_FILE") or network.get("SSL_CERT_DIR")
            else True
        )
        self.http = httpx.AsyncClient(
            transport=transport,
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            proxy=settings.environment_proxy(),
            verify=verify,
        )
        self.schema: dict | None = None
        self.schema_source = "live"

    async def close(self) -> None:
        await self.http.aclose()

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        repo: str | None = None,
        params: dict | None = None,
        body: dict | None = None,
        text: bool = False,
        swagger: bool = False,
        with_headers: bool = False,
    ):
        method = method.upper()
        decoded = unquote(endpoint)
        if (
            urlsplit(endpoint).query
            or urlsplit(endpoint).fragment
            or any(p in (".", "..") for p in decoded.split("/"))
            or any(c in decoded for c in "\\\x00\r\n")
        ):
            raise TeaError("invalid_input", "Invalid API endpoint path.")
        writing = method != "GET"
        if writing:
            if repo is None:
                raise TeaError("write_disabled", "Mutations require an explicit repository.")
            self.settings.authorize_write(repo)
            # Prevent a caller from authorizing one repository while writing another.
            from .config import repo_endpoint

            prefix = repo_endpoint(repo)
            if endpoint != prefix and not endpoint.startswith(prefix + "/"):
                raise TeaError(
                    "write_disabled", "Mutation endpoint does not match the authorized repository."
                )
            from .policy import require_permit

            permit = require_permit(self.settings, repo, endpoint=endpoint, method=method)
            if permit.branch is not None:
                key = "new_branch_name" if permit.operation == "create_branch" else "branch"
                require_permit(self.settings, repo, branch=(body or {}).get(key, ""))
            if self.schema_source != "live":
                raise TeaError("write_disabled", "Compatibility mode is read-only.")
        elif self.schema_source == "compatibility" and not swagger:
            if not self._read_advertised(endpoint):
                raise TeaError("unsupported", "Endpoint is outside the tested basic-read compatibility set.")
        if not endpoint.startswith("/") or endpoint.startswith("//") or urlsplit(endpoint).scheme:
            raise TeaError("invalid_input", "Expected an API path, not an external URL.")
        url = self.settings.base_url + ("" if swagger else "/api/v1") + endpoint
        headers = {
            "Accept": "text/plain" if text else "application/json",
            "User-Agent": "jasminetea-mcp/0.3.0",
        }
        token = "" if swagger else self.settings.token()
        if token:
            headers["Authorization"] = "token " + token
        attempts = 1 if writing else 3
        for attempt in range(attempts):
            try:
                async with self.http.stream(
                    method, url, params=params, json=body, headers=headers
                ) as response:
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > self.settings.max_response_bytes:
                            raise TeaError(
                                "response_too_large",
                                "Response exceeds the configured byte limit.",
                                uncertain=writing,
                            )
                    if (
                        response.status_code in (429, 502, 503, 504)
                        and not writing
                        and attempt + 1 < attempts
                    ):
                        retry_after = response.headers.get("Retry-After", "")
                        delay = min(float(retry_after), 5) if retry_after.isdigit() else 0.25 * (2**attempt)
                        await asyncio.sleep(delay)
                        continue
                    if response.is_redirect:
                        raise TeaError(
                            "redirect_refused",
                            "API redirect refused; verify the configured platform URL.",
                            uncertain=writing,
                        )
                    if response.is_error:
                        codes = {
                            401: "authentication_required",
                            403: "forbidden",
                            404: "not_found_or_hidden",
                            409: "conflict",
                            412: "conflict",
                            422: "invalid_input",
                            429: "rate_limited",
                        }
                        message = f"Platform returned HTTP {response.status_code}."
                        try:
                            detail = json.loads(data).get("message", "")
                            if isinstance(detail, str):
                                message += (
                                    " " + detail[:400].replace(token, "[redacted]")
                                    if token
                                    else " " + detail[:400]
                                )
                        except (ValueError, AttributeError):
                            pass
                        raise TeaError(
                            codes.get(response.status_code, "platform_error"),
                            message,
                            uncertain=writing and response.status_code >= 500,
                        )
                    if not data:
                        value = "" if text else None
                        return (value, dict(response.headers)) if with_headers else value
                    if text:
                        try:
                            value = data.decode("utf-8")
                            return (value, dict(response.headers)) if with_headers else value
                        except UnicodeDecodeError:
                            raise TeaError("binary_content", "This response is not UTF-8 text.") from None
                    try:
                        value = json.loads(data)
                        return (value, dict(response.headers)) if with_headers else value
                    except ValueError:
                        raise TeaError(
                            "invalid_response", "Expected JSON from the platform.", uncertain=writing
                        ) from None
            except (httpx.TimeoutException, httpx.TransportError):
                if not writing and attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise TeaError(
                    "network_error", "Network request failed; verify connectivity.", uncertain=writing
                ) from None
        raise TeaError("network_error", "Request could not be completed.")

    async def page(
        self, endpoint: str, *, page: int = 1, limit: int = 50, params: dict | None = None
    ) -> dict:
        if page < 1 or not 1 <= limit <= 100:
            raise TeaError("invalid_input", "page must be positive and limit must be between 1 and 100.")
        schema = await self.swagger()
        operation = next(
            (
                item.get("get", {})
                for path, item in schema["paths"].items()
                if re.fullmatch(api_path_pattern(path), endpoint)
            ),
            {},
        )
        query_names = {p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"}
        paginated = {"page", "limit"} <= query_names
        query = dict(params or {})
        if paginated:
            query.update(page=page, limit=limit)
        data, headers = await self.request("GET", endpoint, params=query, with_headers=True)
        items = data.get("data", []) if isinstance(data, dict) and "data" in data else data
        if not isinstance(items, list):
            raise TeaError("invalid_response", "Expected a list from a paginated endpoint.")
        total = headers.get("x-total-count", "")
        total = int(total) if total.isdigit() else None
        next_page, next_limit, source = (page + 1 if items else None), limit, "conservative"
        if not paginated:
            total = len(items)
            start = (page - 1) * limit
            next_page = page + 1 if start + limit < total else None
            items, source = items[start : start + limit], "local"
        else:
            links = []
            origin = self.settings.base_url + "/api/v1" + endpoint
            for link in httpx.Response(200, headers=headers).links.values():
                target = urlsplit(urljoin(origin, link.get("url", "")))
                current = urlsplit(origin)
                values = parse_qs(target.query)
                if (target.scheme, target.netloc, target.path) != (
                    current.scheme,
                    current.netloc,
                    current.path,
                ):
                    continue
                if target.username or target.password or target.fragment:
                    continue
                if any(values.get(k) != [str(v)] for k, v in query.items() if k not in ("page", "limit")):
                    continue
                if set(values) - set(query) - {"page", "limit"}:
                    continue
                try:
                    number = int(values["page"][0])
                    size = int(values.get("limit", [str(limit)])[0])
                except (ValueError, KeyError, IndexError):
                    continue
                if number < 1 or not 1 <= size <= 100:
                    continue
                links.append((link.get("rel"), number, size))
            if links:
                source, next_page = "link", None
                for rel, number, size in links:
                    if rel == "next" and number > page:
                        next_page, next_limit = number, size
                        break
        return {
            "items": items,
            "page": page,
            "limit": limit,
            "next_page": next_page,
            "next_limit": next_limit if next_page else None,
            "total_count": total,
            "pagination_source": source,
            "pagination_verified": source != "conservative",
        }

    def _read_advertised(self, endpoint: str) -> bool:
        return any(
            re.fullmatch(api_path_pattern(path), endpoint) and "get" in operation
            for path, operation in self.schema["paths"].items()
        )

    async def swagger(self) -> dict:
        if self.schema is None:
            try:
                data = await self.request("GET", "/swagger.v1.json", swagger=True)
                if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
                    raise TeaError("invalid_response", "Invalid Gitea API definition.")
                self.schema_source = "live"
            except TeaError:
                self.schema_source = "unavailable"
                version = await self.request("GET", "/version")
                if (
                    not isinstance(version, dict)
                    or not isinstance(version.get("version"), str)
                    or not version["version"].strip()
                    or len(version["version"]) > 100
                ):
                    raise TeaError("unsupported", "Cannot establish basic Gitea compatibility.") from None
                data = json.loads(files("jasminetea_mcp").joinpath("basic-read-schema.json").read_text())
                self.schema_source = "compatibility"
            self.schema = data
        return self.schema

    async def require(self, path: str, method: str, *, fields: list[str] | None = None) -> None:
        schema = await self.swagger()
        operation = schema["paths"].get(path, {}).get(method.lower())
        if operation is None:
            raise TeaError("unsupported", "The configured platform does not advertise this operation.")
        if fields:
            props = self.body_properties(schema, operation)
            if not all(field in props for field in fields):
                raise TeaError("unsupported", "The platform API does not advertise the required fields.")

    @staticmethod
    def body_properties(schema: dict, operation: dict) -> dict:
        for parameter in operation.get("parameters", []):
            if parameter.get("in") == "body":
                body = parameter.get("schema", {})
                ref = body.get("$ref", "")
                if ref.startswith("#/definitions/"):
                    body = schema.get("definitions", {}).get(ref.rsplit("/", 1)[-1], {})
                return body.get("properties", {})
        return {}

    def safe_fetch_path(self, value: str) -> str:
        parsed = urlsplit(value)
        base = urlsplit(self.settings.base_url)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise TeaError("invalid_input", "Only the configured platform can be fetched.")
            prefix = base.path + "/api/v1"
            if not parsed.path.startswith(prefix + "/"):
                raise TeaError("invalid_input", "Use a platform API URL or a relative API endpoint.")
            path = parsed.path[len(prefix) :]
        else:
            path = parsed.path
        if parsed.query or parsed.fragment:
            raise TeaError("invalid_input", "Pass query parameters separately.")
        decoded = unquote(path)
        if "%" in decoded:
            raise TeaError("invalid_input", "Nested URL encoding is forbidden in generic GET endpoints.")
        if any(p in (".", "..") for p in decoded.split("/")) or any(c in decoded for c in "\\\x00\r\n"):
            raise TeaError("invalid_input", "Invalid API path.")
        if re.fullmatch(
            r"/repos/[^/]+/[^/]+(?:/(?:contents|raw|git|commits|branches|pulls|issues|releases|tags|statuses|collaborators|branch_protections|labels|milestones|languages|topics)(?:/.*)?)?",
            decoded,
        ):
            repository("/".join(decoded.split("/")[2:4]))
            return path
        if decoded in ("/user", "/user/repos", "/user/orgs", "/user/teams", "/repos/search"):
            return path
        if re.fullmatch(r"/orgs/[^/]+(?:/(?:repos|members|teams))?", decoded):
            return path
        raise TeaError("unsupported", "This GET endpoint is outside the public tool allowlist.")
