"""Workflow internals.

flow owns guarded commits/recovery; steps routes phases; checks validates payloads;
transitions mutates candidate state; mapping owns the MAP model/loop;
work owns audit queues/budgets; ledger owns source/reference identity;
exports derives artifacts. cli/storage remain shared helpers.
"""
