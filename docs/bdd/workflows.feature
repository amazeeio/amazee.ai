Feature: amazee.ai Key and Team Management Workflows
  As a user or system administrator
  I want to manage AI keys and teams
  So that I can control access and budgets for AI resources

  Rule: Drupal AI Workflows

  Scenario: Requesting an anonymous trial key via Drupal CMS AI Recipe
    Given the "Anonymous Trial Team" is configured in the system via environment variables
    When I perform a "POST" to "/auth/generate-trial-access" from the CMS Recipe
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
    And the system has sent an 8-character alphanumeric validation code to my email
    When I perform a "POST" to "/auth/sign-in" with the correct 8-character alphanumeric code
    Then if I am a new user, a new team "Team user@example.com" should be automatically created
    And I should be registered as the "ADMIN" of that team
    And the team should have "PERIODIC" budget type by default
    And my Drupal site should be successfully linked to my amazee.ai account
    And I should be able to create new named AI keys directly from the Drupal interface

  Scenario: Trial status calculation for a new team
    Given a new team has been created today
    And the team has no active product subscriptions
    And "is_always_free" is set to "False"
    When the system calculates the trial status via "_calculate_trial_status"
    Then the status should be "30 days left"

  Scenario: Trial expiry after 30 days
    Given a team was created 31 days ago
    And the team has no "last_payment" recorded
    And the team has no active product subscriptions
    When the system calculates the trial status
    Then the status should be "Expired"

  Rule: System Limits Enforcement

  Scenario: Creating a key within default limits
    Given a user in a team with 0 existing keys
    When the user requests to create a new AI key
    Then the "LimitService" should increment the "USER_KEY" resource count
    And the key creation should succeed
    And the key should have the default "max_spend" of $27.00
    And the key should have the default "rpm_limit" of 500

  Scenario: Exceeding the maximum key limit
    Given a user has already created 1 AI key (the default limit)
    When the user requests to create another AI key
    Then the "LimitService" should deny the increment for "USER_KEY"
    And the API should return a "402 Payment Required" error
    And the message should state "User has reached the maximum LLM key limit of 1 keys"

  Scenario: Creating a Vector Database
    Given a team has 0 vector databases
    When I perform a "POST" to "/vector-db"
    Then a new vector database should be provisioned in the requested region
    And the "VECTOR_DB" resource count should be incremented for the team
    And I should receive the database credentials

  Scenario: Viewing audit logs as an admin
    Given I am logged in as a "SYSTEM_ADMIN"
    When I perform a "GET" to "/audit/logs"
    Then I should receive a paginated list of system events
    And I should be able to filter by "event_type" or "user_id"

  Scenario: Denying audit logs to non-admins
    Given I am logged in as a regular "USER"
    When I perform a "GET" to "/audit/logs"
    Then the API should return a "403 Forbidden" error

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
