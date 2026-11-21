@db @compare @demo @parameterized
Feature: Parameterized data comparison
  As a data engineer
  I want to pass values from a feature into a compare config
  So that I can reuse a config for different data slices

  Scenario: Customers from a selected minimum ID match the Excel extract
    Given the compare config "app/datasets/compare/customers_from_id.yml"
      | name            | value |
      | min_customer_id | 1015  |
    When I run the data comparison
    Then the data comparison should pass
