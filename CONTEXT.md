# Grocea Pantry Catalog Context

Grocea's pantry catalog names grocery ingredients and distinguishes available catalog entries from stock the local user actively tracks.

## Language

**Account**:
An authenticated personal identity with an isolated profile and owned domain data.
The stable legacy Local Profile can be claimed once without changing its user ID.
_Avoid_: User account, local account

**Category**:
A label organizing Ingredients. A Category is either global and read-only or custom and owned by an Account.
_Avoid_: Ingredient group

**Ingredient**:
A grocery item that Pantry Stock and Recipes may reference. Its identity is independent from its name and stock balance.
_Avoid_: Pantry item, inventory item

**Global Ingredient**:
A predefined, shared Ingredient that every Account may use but not edit.
_Avoid_: Default ingredient, system ingredient

**Custom Ingredient**:
An Ingredient created and owned by an Account when no Global Ingredient matches.
_Avoid_: Personal ingredient, local ingredient

**Measurement Family**:
The compatible unit family assigned to an Ingredient: mass, volume, or count.
_Avoid_: Unit type, measurement type

**Pantry Stock**:
The current signed quantity of one Ingredient tracked by an Account. Absence means the Ingredient is not tracked; a zero or negative balance means it needs restock.
_Avoid_: Inventory record, pantry item

**Tracked Ingredient**:
An Ingredient for which Pantry Stock exists, including a zero or negative balance.
_Avoid_: Pantry Ingredient, stocked Ingredient

**Needs Restock**:
The derived state of Tracked Ingredient whose Pantry Stock is zero or negative.
_Avoid_: Low stock, out of stock

**Archived Ingredient**:
A Custom Ingredient hidden from normal catalog and selection flows while its stable identity and existing references remain preserved.
_Avoid_: Deleted Ingredient
