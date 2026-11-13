@db @compare
Feature: Cross-source data comparison
  As a data engineer
  I want to compare recordsets between databases, Excel files, APIs and the UI
  So that migrations and pipelines move data without loss or corruption

  @demo
  Scenario: Customers in the database match the Excel extract
    Given the compare config "app/datasets/compare/customers_db_vs_excel.yml"
    When I run the data comparison
    Then the source and target row counts should match
    And there should be no duplicate primary keys in the source
    And there should be no records missing in the target
    And there should be no records missing in the source
    And there should be no field mismatches

  @demo
  Scenario Outline: Compare configs can be batched with one overall assertion
    Given the compare config "<config>"
    When I run the data comparison
    Then the data comparison should pass

    Examples:
      | config                                                  |
      | app/datasets/compare/customers_db_vs_excel.yml          |

  @demo
  Scenario: Known differences stay within the agreed tolerance
    Given the compare config "app/datasets/compare/customers_db_vs_excel_diffs.yml"
    When I run the data comparison
    Then the field "NAME" should have no mismatches
    And the field "STATUS" should have no mismatches
    And the mismatch percentage should be at most 15

  @demo_fail @continue_on_failure
  Scenario: Customers with known differences are detected
    Given the compare config "app/datasets/compare/customers_db_vs_excel_diffs.yml"
    When I run the data comparison
    Then there should be no records missing in the target
    And there should be no field mismatches

  @snowflake @requires_creds
  Scenario: Snowflake DEV and QA ORDERS are identical
    Given the compare config "app/datasets/compare/snowflake_dev_vs_qa.yml"
    When I run the data comparison
    Then the data comparison should pass

  @oracle @snowflake @requires_creds
  Scenario: Oracle to Snowflake customer migration is complete
    Given the compare config "app/datasets/compare/oracle_vs_snowflake.yml"
    When I run the data comparison
    Then the data comparison should pass

  @databricks @excel @requires_creds
  Scenario: Databricks prices match the signed-off Excel sheet
    Given the compare config "app/datasets/compare/databricks_vs_excel.yml"
    When I run the data comparison
    Then the data comparison should pass
