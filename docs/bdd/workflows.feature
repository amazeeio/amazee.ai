Feature: amazee.ai Key and Team Management Workflows
  As a user or system administrator
  I want to manage AI keys and teams
  So that I can control access and budgets for AI resources

  Rule: Drupal AI Workflows

  Scenario: Requesting an anonymous trial key via Drupal CMS AI Recipe
    Given the "Anonymous Trial Team" is configured in the system via environment variables
    When I perform a "POST" to "/generate-trial-access" from the CMS Recipe
    Then a new "fake" user with an email ending in "@example.com" should be created
    And the user should be added to the fixed "Anonymous Trial Team"
    And a new AI key should be generated for this specific user
    And the key should have the configured trial budget limit and "PERIODIC" budget type
    And I should be able to use this key for AI requests immediately

  Scenario: Drupal.org demo instance via Polydock Engine
    Given a user starts a demo instance on drupal.org
    When Polydock requests a key during the claim process
    Then the system should generate a real team and user record associated with the demo's email
    And a "default" key with "PERIODIC" budget type should be generated
    And the key should remain valid for 1 month as a trial account

  Scenario: Validating a production Drupal AI provider account
    Given I have entered my email "user@example.com" in the Drupal AI settings
    And the system has sent a 6-digit validation code to my email
    When I enter the correct 6-digit code into the Drupal interface
    Then my Drupal site should be successfully linked to my amazee.ai account
    And I should be able to create new named AI keys directly from the Drupal interface

  Rule: Admin Operations (Dashboard)

  Scenario: Assigning a key to a shared team
    Given I am logged into the MoaD Dashboard as an administrator
    And a team "Engineering" exists
    When I create a new AI key and select "Assign to Team: Engineering"
    Then the key should be shared across all members of the "Engineering" team
    And the key should have no specific "owner_id" set

  Scenario: Assigning a key to a specific team member
    Given I am logged into the MoaD Dashboard as an administrator
    And a user "alice@example.com" exists in team "Engineering"
    When I create a new AI key and select "Assign to User: alice@example.com"
    Then the key should be owned specifically by "alice@example.com"
    And the key consumption should be billed against the "Engineering" team budget
