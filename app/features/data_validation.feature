@db @validation
Feature: Table data sanity validation
  As a data steward
  I want to run sanity checks on a table or query result
  So that bad data is caught early

  @demo
  Scenario: Clean ORDERS data passes every sanity check
    Given the validation config "app/datasets/validation/orders_clean.yml"
    When I run the data validations
    Then all validations should pass

  @demo
  Scenario: Individual check groups can be asserted separately
    Given the validation config "app/datasets/validation/orders_clean.yml"
    When I run the data validations
    Then all "not_null" validations should pass
    And all "numeric" validations should pass
    And the validation "no_orphan_orders" should pass

  @demo_fail
  Scenario: Dirty ORDERS data is flagged
    Given the validation config "app/datasets/validation/orders_dirty.yml"
    When I run the data validations
    Then all validations should pass

  @snowflake @requires_creds
  Scenario: Snowflake SALES.ORDERS passes sanity checks
    Given the validation config "app/datasets/validation/snowflake_orders.yml"
    When I run the data validations
    Then all validations should pass
