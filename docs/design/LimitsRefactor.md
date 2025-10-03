There are two main types of limits. `COUNT` and `VALUE` limits. These are handled differently, or rather are enforced by different systems. a `COUNT` limit is handled by amazee.ai ensuring that resources are not created in infinite number. `VALUE` limits are delegated to a downstream system, and are used to ensure fair use of resources in business operations. These can also be seen as `CONTROL_PLANE` limits and `DATA_PLANE` limits. For the sake of this document we will use `CP` and `DP` to distinguish the types of limits and resources.

Limits are set and viewed in amazee.ai, regardless of whether they are CP or DP limits. However, only CP limits are _enforced_ in amazee.ai. This means there may be a need to add eventual consistency, or caching, to the viewing of DP limits in order to avoid causing issues in the downstream service.
## Control Plane Limits
CP limits are things like the number of users in a team, the number of keys owned by a user, etc. These are restricted by amazee.ai only. They are low-mutation and are only read when new resources are created or deleted.
### CP Resources
- Users
- User-Keys
- Team-Keys
- Vector DBs
- GPT Instances
## Data Plane Limits
DP limits are things which must be limited in a system other than amazee.ai, for example LLM Key Budgets are limited in LiteLLM because they are used against LiteLLM endpoints, bypassing amazee.ai entirely. Certain restrictions in GPT instances would also be considered DP limits as the user does not interact with amazee.ai at all when accessing the GPT instance.
### DP Resources
- Budgets
- Storage
- Documents
## Data Structure
We need to define a single data structure for both DP and CP limits, which captures the requirements of both. The current proposal is as follows:
```python
class LimitedResource:
	limit_type: LimitType
	resource: ResourceType
	unit: UnitType
	max_value: double
	current_value: Optional[double] # DP Limits will not include current
	owner_type: OwnerType
	owner_id: int
	limited_by: LimitSource
	set_by: Optional[str] # If LimitSource is MANUAL set_by must have a value
	updated_at: DateTime

class LimitType(Enum):
	CONTROL_PLANE = "control_plane"
	DATA_PLANE = "data_plane"

class ResourceType(Enum):
	# CP Type Resources
	KEY = "ai_key"
	USER = "user"
	VECTOR_DB = "vector_db"
	GPT_INSTANCE = "gpt_instance"

	# DP Type Resources
	BUDGET = "max_budget"
	RPM = "rpm"
	STORAGE = "storage"
	DOCUMENT = "document"

class UnitType(Enum):
	COUNT = "count"
	DOLLAR = "dollar"
	GB = "gigabyte"

class OwnerType(Enum):
	TEAM = DBTeam.class
	USER = DBUser.class

class LimitSource(Enum):
	PRODUCT = "product"
	DEFAULT = "default"
	MANUAL = "manual"
```

## Managing Limits
There are a set of APIs required for limit management, as well as a set of utility methods which will be used by the recon jobs to ensure fairness. These have certain rules which must be obeyed.
### Source Rules
- `PRODUCT` may only overwrite `PRODUCT` or `DEFAULT`
- `MANUAL` may overwrite anything, including `MANUAL`
- IF a product is deleted, `PRODUCT` may be overwritten by `DEFAULT`
- WHEN limits are reset, they should go from `MANUAL` first to `PRODUCT` then to `DEFAULT` depending on if there is a product relationship
### Type Rules
- CP Limits _must_ have the current value set
- Combination of `owner_type` and `owner_id` _must_ be a valid user or team in the DB.
- The 3-tuple of `(owner_type, owner_id, resource_type)` _must_ be unique - there can only be one limit for a resource per entity
- `User`type owners will inherit limits from their corresponding `Team` unless they have an override.
### RBAC Rules
- Only system administrators may overwrite limits

### Default Limits
We need to move away from having these hardcoded in constants in the codebase, and instead have them set in the DB so that they can be changed by anyone at any time.
### APIs/Methods Required
```python
get_team_limits(team_id) -> TeamLimits
increment_resource(owner_type, owner_id, resource_type) -> bool
decrement_resource(owner_type, owner_id, resource_type) -> bool
overwrite_limit(owner_type, owner_id, resource_type, new_limit) -> TeamLimits
reset_team_limits(team_id) -> TeamLimits
reset_limit(owner_type, owner_id, resource_type) -> TeamLimits
```

### Limit Recon
We already have the recon job which is validating the limits are correct for all keys every hours, but we need to make sure that this fits with the shape of the system we are moving towards.
## Questions
- Are there any plans to implement automatic scaling or adjustment of limits based on usage patterns or other metrics?
  If we were to do this, there would be a new `LimitSource` which would be `AUTOSCALING` or similar. This would be applied as part of the recon job, and rules would need to be created to ensure fairness an that the scaling aligns with cost expectations.