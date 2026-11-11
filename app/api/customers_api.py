"""Typed API client for the customers endpoints. One method per endpoint; tests read like the API."""
from pyassure.web.api import ApiClient, ApiResponse


class CustomersApi(ApiClient):
    def list(self, name: str = "") -> ApiResponse:
        return self.get("/api/customers", params={"name": name} if name else None)

    def get_one(self, customer_id) -> ApiResponse:
        return self.get(f"/api/customers/{customer_id}")

    def create(self, **fields) -> ApiResponse:
        return self.post("/api/customers", json=fields)

    def remove(self, customer_id) -> ApiResponse:
        return self.delete(f"/api/customers/{customer_id}")
