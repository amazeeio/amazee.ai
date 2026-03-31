# amazee.ai Feature & Workflow Documentation

This document outlines the various ways users, systems, and administrators interact with amazee.ai to manage AI keys, budgets, and team structures.

## Visual Overview

For a visual representation of these flows, please see the [Workflows Diagram](diagrams/workflows.md).

## Core Concepts

*   **AI Keys**: Authentication tokens used to proxy requests through LiteLLM. Can be assigned to a specific **User** (owner) or a **Team** (shared).
*   **Teams**: Logical groupings of users. Budgets and limits are typically managed at the team level.
*   **Budget Types**:
    *   `PERIODIC`: Default type. Budgets reset based on a duration (daily, weekly, monthly).
    *   `POOL`: Typically used for high-volume or enterprise usage where a fixed amount is consumed until depleted or topped up.

---

## 1. Drupal AI Workflows

### 1.1 Anonymous Trials (Drupal CMS AI Recipe)
Designed for low-friction exploration of Drupal AI capabilities.
*   **Trigger**: User initiates an "Anonymous Trial" from within a Drupal installation using the Drupal CMS AI Recipe.
*   **Process**:
    1.  Calls the `generate AI trial` endpoint via a `POST` to `/auth/generate-trial-access`.
    2.  System creates a "fake" user with an email address ending in `@example.com`.
    3.  User is assigned to the fixed "Anonymous Trial Team" (configured via environment variables).
    4.  A new AI key is generated for this specific user.
*   **Limits**: Currently capped at a small budget (e.g., $2.00) to prevent abuse.

### 1.2 Drupal.org Demos (via Polydock Engine)
Temporary hosted environments for evaluating Drupal modules and themes.
*   **Trigger**: A user starts a demo instance on drupal.org powered by Polydock.
*   **Process**:
    1.  Polydock requests a key for the ephemeral environment during the claim process.
    2.  System generates a real team/user record associated with the demo's email.
    3.  A "default" key is generated for that team/user.
*   **Lifecycle**: The Polydock instance typically lasts 1-2 weeks. The AI key follows standard periodic budget defaults, so it should last for a month before being fully expired (as "trial" account).

### 1.3 Main Production Workflow (Drupal AI Provider)
The standard path for site builders and production environments.
*   **Trigger**: User configures the "amazee.ai" provider in Drupal's AI settings.
*   **Process**:
    1.  User enters their email address.
    2.  System sends an 8-character uppercase alphanumeric validation code via email.
    3.  User validates the code in Drupal.
    4.  System links the Drupal site to the user's amazee.ai account.
    5.  Users can create new named keys directly from the Drupal interface.
*   **Management**: While key creation is supported in-CMS, advanced management (budgeting, deletion) is directed to the "Mother of All Dashboards" (MoaD).

---

## 2. MoaD (Mother of All Dashboards)

The central management interface (`frontend/`) for all amazee.ai resources.

### 2.1 Key Management
*   **Functionality**: Users can create, view, and name their keys.
*   **Budget Type**: Keys created here typically default to `POOL` type (verify implementation), allowing for more flexible consumption models compared to the fixed periodic resets of trial keys.
*   **Monitoring**: Provides real-time spend tracking and budget duration adjustments.

### 2.2 Team & Admin Operations
*   **Manual Creation**: Admins can manually create teams and users.
*   **Assignment**: Keys can be assigned directly to a Team (Shared) or an individual Owner within a team.
*   **Subscriptions**: Teams can be subscribed to specific Products (e.g., "$100/mo Budget Plan"). These subscriptions typically use `PERIODIC` budget logic to align with billing cycles.

---

## Behavior Driven Development (BDD)

The expected behaviors of these systems are documented using Gherkin in the [Workflows Feature File](bdd/workflows.feature).
