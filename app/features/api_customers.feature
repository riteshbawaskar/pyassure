@api
Feature: Customers API (Playwright APIRequestContext)
  As a backend tester
  I want to exercise the customers API with a reusable login session
  So that contract and data behaviour are verified without a browser

  @demo
  Scenario: Requests without a session are rejected
    Given the API is anonymous
    When I send a GET request to "/api/customers"
    Then the response status should be 401

  @demo
  Scenario: An authenticated session can list customers
    Given the API is authenticated as "admin"
    When I send a GET request to "/api/customers"
    Then the response status should be 200
    And the response header "content-type" should contain "json"
    And the response JSON field "total" should equal "30"
    And the response JSON list "customers" should have 30 items
    And the response JSON field "customers[0].name" should equal "Customer 1"
    And the response time should be under 2000 ms

  @demo
  Scenario: A customer can be created, read back and deleted
    Given the API is authenticated as "admin"
    When I send a POST request to "/api/customers" with JSON:
      """
      {"name": "Eve Example", "email": "eve@example.com", "country": "IN", "credit_limit": 2500.0}
      """
    Then the response status should be 201
    When I save the response JSON field "id" as "new_id"
    And I send a GET request to "/api/customers/{{new_id}}"
    Then the response JSON field "name" should equal "Eve Example"
    When I send a DELETE request to "/api/customers/{{new_id}}"
    Then the response status should be 204

  @demo
  Scenario: A typed API client from app/api is used through business steps
    When I create a customer named "Zed" through the API
    Then the new customer should be retrievable through the API
    And the response JSON field "name" should equal "Zed"
    And the new customer should be removed through the API

  @demo
  Scenario: The API response matches the database
    Given the compare config "app/datasets/compare/customers_db_vs_api.yml"
    When I run the data comparison
    Then the data comparison should pass
