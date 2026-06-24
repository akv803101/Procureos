"""Fix 02 — atomic budget re-check under a distributed lock."""
import pytest

from core.budget_engine import budget_lock, execute_payment_with_budget_check
from core.db import Goal, InMemoryStore
from core.errors import BudgetExceededError, BudgetLockError
from tests.fakes import FakePayment, FakeRedis, FakeVolopayClient


def _store(limit=20000.0, spent=0.0):
    return InMemoryStore(
        budgets={("co-1", "fb"): limit},
        spent={("co-1", "fb"): spent},
    )


async def test_payment_within_budget_executes_and_records_spend():
    store = _store(limit=20000, spent=0)
    redis = FakeRedis()
    client = FakeVolopayClient(script=[FakePayment("settled")])

    result = await execute_payment_with_budget_check(
        "order-1", "co-1", "fb", 15000, "vendor-1",
        redis=redis, store=store, client=client,
    )

    assert result.status == "settled"
    assert store.spend_records == [
        {"company_id": "co-1", "category": "fb", "amount": 15000, "order_id": "order-1"}
    ]


async def test_over_budget_blocks_payment_and_notifies():
    store = _store(limit=20000, spent=10000)   # only 10000 available
    redis = FakeRedis()
    client = FakeVolopayClient(script=[FakePayment("settled")])
    notifications = []

    async def notify(company_id, category, amount, available):
        notifications.append((company_id, category, amount, available))

    with pytest.raises(BudgetExceededError):
        await execute_payment_with_budget_check(
            "order-1", "co-1", "fb", 15000, "vendor-1",
            redis=redis, store=store, client=client, notify_budget_exceeded=notify,
        )

    assert notifications == [("co-1", "fb", 15000, 10000)]
    assert store.spend_records == []          # nothing spent
    assert client.create_calls == []          # payment never attempted


async def test_concurrent_payment_on_same_category_is_locked_out():
    store = _store()
    redis = FakeRedis()
    client = FakeVolopayClient(script=[FakePayment("settled")])

    # Hold the budget lock for (co-1, fb), then a second payment on the same
    # (company, category) must fail fast rather than racing the budget check.
    async with budget_lock("co-1", "fb", redis=redis):
        with pytest.raises(BudgetLockError):
            await execute_payment_with_budget_check(
                "order-2", "co-1", "fb", 1000, "vendor-1",
                redis=redis, store=store, client=client,
            )


async def test_lock_is_released_after_use_so_next_payment_proceeds():
    store = _store()
    redis = FakeRedis()
    client = FakeVolopayClient(script=[FakePayment("settled"), FakePayment("settled")])

    await execute_payment_with_budget_check(
        "order-1", "co-1", "fb", 1000, "vendor-1", redis=redis, store=store, client=client
    )
    # If the lock were leaked, this second call would raise BudgetLockError.
    result = await execute_payment_with_budget_check(
        "order-2", "co-1", "fb", 1000, "vendor-1", redis=redis, store=store, client=client
    )
    assert result.status == "settled"
