# amazee.ai Feature & Workflow Diagram

```mermaid
graph TD
    %% Entry Points
    User((User)) --> Drupal[Drupal CMS / Recipe]
    User --> Dashboard[MoaD Dashboard / frontend/]
    Polydock[Polydock / d.o Demos] --> API

    %% Drupal Workflows
    subgraph Drupal_Workflows [Drupal AI Workflows]
        Drupal --> Trial[Anonymous Trial / CMS AI Recipe]
        Drupal --> MainProd[Main Production Workflow]
        
        Trial --> GenTrialAPI[generate AI trial endpoint /POST /generate-trial-access]
        GenTrialAPI --> TrialUser[Create Fake User @example.com]
        TrialUser --> TrialTeam[Assign to Fixed Trial Team]
        TrialTeam --> TrialKey[Create Key: $2, PERIODIC]
        
        MainProd --> EmailVal[Email + 6-Digit Code]
        EmailVal --> LinkedAccount[Validated Account]
        LinkedAccount --> CustomKey[User-named Key]
    end

    %% Polydock Workflow
    subgraph Polydock_Workflow [Polydock Demos via Engine]
        API --> PolyReq[Polydock Key Request / Claim]
        PolyReq --> RealEntity[Create Real Team/User/Email]
        RealEntity --> DefaultKey[Default Key: PERIODIC / Trial]
        DefaultKey --> LifeSpan[Lasts 1 month]
    end

    %% MoaD Dashboard
    subgraph Dashboard_Operations [MoaD / Dashboard]
        Dashboard --> ManCreate[Manual Team/User Creation]
        Dashboard --> Subscribe[Subscribe Team to Products]
        ManCreate --> AssignKey[Assign Key to Team or User]
        AssignKey --> PoolKey[Key Type: POOL / PERIODIC]
        Subscribe --> SubscriptionLogic[Subscription: PERIODIC]
    end

    %% Core Data
    TrialKey --> LiteLLM[LiteLLM Proxy]
    CustomKey --> LiteLLM
    DefaultKey --> LiteLLM
    PoolKey --> LiteLLM
```
