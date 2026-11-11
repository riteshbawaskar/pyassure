@ui @requires_browser
Feature: Customer directory UI (Playwright + Page Object Model + storageState)
  As a QA engineer
  I want UI tests that start already logged in and use page objects
  So that tests are fast, readable and survive UI changes

  @demo
  Scenario: Invalid credentials are rejected
    When I open the sign-in page
    And I sign in as "admin" with password "wrong"
    Then the sign-in error should be "Invalid credentials"

  @demo
  Scenario: Signing in through the UI lands on the customer directory
    When I open the sign-in page
    And I sign in as "admin" with password "admin123"
    Then I should land on the customers page
    And the customers grid should show 30 rows

  @demo
  Scenario: A saved session starts the browser already logged in
    Given I am logged in as "admin"
    When I open the customers page
    And I search customers for "Customer 1"
    Then the result summary should read "Showing 11 of 30 customers"
    And the customers grid should show 11 rows
    When I take a screenshot named "Filtered grid"

  @demo
  Scenario: A session created through the real UI login works as well
    Given I am logged in as "admin_ui"
    When I open the customers page
    Then the element "#heading" should contain the text "Customer Directory"

  @demo
  Scenario: The UI grid matches the database
    Given the compare config "app/datasets/compare/customers_db_vs_ui.yml"
    When I run the data comparison
    Then the data comparison should pass

  @demo
  Scenario: The first customer id on screen equals the database
    Given I am logged in as "admin"
    When I open the customers page
    Then the text of "#customers tbody tr:first-child td:first-child" should equal the value returned by "SELECT MIN(CUSTOMER_ID) FROM customers" on connection "demo_db"

  @demo_fail
  Scenario: A wrong UI expectation is reported with a screenshot
    Given I am logged in as "admin"
    When I open the customers page
    Then the element "#heading" should contain the text "Order Directory"
