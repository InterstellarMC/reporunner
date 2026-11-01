from typing import Protocol
import httpx
from .models import ActionRequest, ExecutionResult, VerificationResult


class ExecutionAdapter(Protocol):
    async def execute(self, request: ActionRequest) -> ExecutionResult: ...
    async def verify(self, run_id: str, snapshot: str, remaining_seconds: float) -> VerificationResult: ...
    async def close(self, run_id: str) -> None: ...


class HttpExecutionAdapter:
    """Trusted service URL is operator config, never taken from a repository/model."""
    def __init__(self, base_url: str, token: str):
        self.base_url, self.token = base_url.rstrip("/"), token

    async def _post(self, path, payload, timeout):
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.base_url + path, json=payload,
                headers={"Authorization": "Bearer " + self.token})
            response.raise_for_status()
            return response.json()

    async def execute(self, request):
        return ExecutionResult.model_validate(await self._post("/v1/execute", request.model_dump(mode="json"), request.remaining_seconds))

    async def verify(self, run_id, snapshot, remaining_seconds):
        return VerificationResult.model_validate(await self._post("/v1/verify", {
            "run_id": run_id, "snapshot": snapshot, "remaining_seconds": remaining_seconds}, remaining_seconds))

    async def close(self, run_id):
        await self._post("/v1/close", {"run_id": run_id}, 5)
