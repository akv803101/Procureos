"""Data-access seam used by the Phase-1 fixes.

The fixes (Fix 02 budget, Fix 04 approval TTL, Fix 05 state lock) need a small,
specific set of DB operations. Rather than couple them to Supabase directly, we
define exactly that interface (`Store`) and provide two implementations:

  * InMemoryStore  — real, working, used by the unit tests now.
  * SupabaseStore  — the production implementation, wired in Phase 2 when the
                     live DB + data-access layer land. Until then its methods
                     raise a clear "not yet implemented" error.

This is the minimum interface the fixes require — not a premature abstraction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from core.refcodes import ref_code


def _digits(p: str | None) -> str:
    """Canonical phone key — digits only. WhatsApp delivers the sender as bare
    E.164 (e.g. '919...') while seeds store '+919...'; match on digits so they
    line up regardless of '+'/spaces/dashes."""
    return "".join(ch for ch in (p or "") if ch.isdigit())


@dataclass
class Budget:
    limit: float


@dataclass
class Goal:
    id: str
    status: str
    category: str | None = None
    company_id: str | None = None
    employee_id: str | None = None
    raw_input: str | None = None
    budget_limit: float | None = None
    approval_sent_at: datetime | None = None   # when options were presented (Fix 04 clock)
    approved_at: datetime | None = None
    selected_option_id: str | None = None
    parsed_intent: dict = field(default_factory=dict)
    options: list | None = None
    collected_quotes: list = field(default_factory=list)   # vendor replies parsed so far


@dataclass
class Company:
    id: str
    name: str | None = None
    budget_policies: dict = field(default_factory=dict)     # {category: limit, "default": limit}
    approval_chain: dict = field(default_factory=dict)
    approver_email: str | None = None
    slack_approval_channel: str | None = None               # Slack channel/user to post the card to
    waba_number: str | None = None


@dataclass
class Employee:
    id: str
    name: str | None = None
    whatsapp: str | None = None
    company_id: str | None = None


@dataclass
class ApprovalToken:
    token: str
    goal_id: str
    approver_id: str | None
    expires_at: datetime
    used_at: datetime | None = None


@dataclass
class Order:
    id: str
    goal_id: str
    vendor_id: str
    company_id: str
    quoted_price: float | None = None
    final_price: float | None = None
    promised_eta: datetime | None = None
    delivered_at: datetime | None = None
    status: str = "placed"           # placed | in_transit | delivered | failed
    rating_sent: bool = False
    vendor_response_time_mins: int | None = None
    description: str | None = None


@dataclass
class Rating:
    id: str
    order_id: str
    vendor_id: str
    company_id: str
    overall_rating: int | None = None      # 1-5 (V1: 5 satisfied / 2 issue)
    satisfied: bool | None = None
    delivered_on_time: bool | None = None  # system-calculated
    price_accurate: bool | None = None     # system-calculated
    response_time_mins: int | None = None
    is_repeat_order: bool = False
    comment: str | None = None


class Store(Protocol):
    # budget (Fix 02)
    async def get_budget(self, company_id: str, category: str) -> Budget: ...
    async def get_spent_this_period(self, company_id: str, category: str) -> float: ...
    async def record_spend(self, company_id: str, category: str, amount: float, order_id: str) -> None: ...

    # goals (Fix 04, Fix 05)
    async def get_goal(self, goal_id: str) -> Goal: ...
    async def get_goal_state(self, goal_id: str) -> str: ...
    async def set_goal_state(self, goal_id: str, to_state: str, payload: dict | None = None) -> None: ...
    async def update_goal_options(self, goal_id: str, options: list) -> None: ...
    async def set_goal_approval(self, goal_id: str, option_id: str, approved_at: datetime) -> None: ...
    async def set_goal_approval_sent(self, goal_id: str, sent_at: datetime) -> None: ...

    # approval magic-link tokens (Fix 12)
    async def create_approval_token(self, token: ApprovalToken) -> None: ...
    async def get_approval_token(self, token: str) -> ApprovalToken | None: ...
    async def mark_approval_token_used(self, token: str, used_at: datetime) -> None: ...

    # orders (delivery tracking)
    async def get_order(self, order_id: str) -> Order: ...
    async def get_orders_by_status(self, status: str) -> list[Order]: ...
    async def set_order_delivered(self, order_id: str, delivered_at: datetime, final_price: float | None = None) -> None: ...
    async def set_order_status(self, order_id: str, status: str) -> None: ...
    async def mark_order_rating_sent(self, order_id: str) -> None: ...
    async def get_orders_for_company_vendor(self, company_id: str, vendor_id: str, exclude_order_id: str | None = None) -> list[Order]: ...

    # ratings + vendor scoring (the crown-jewel intelligence loop)
    async def create_rating(self, rating: Rating) -> str: ...
    async def get_rating(self, rating_id: str) -> Rating: ...
    async def update_rating(self, rating_id: str, **fields) -> None: ...
    async def get_ratings_for_vendor(self, vendor_id: str) -> list[Rating]: ...
    async def update_vendor_score(self, vendor_id: str, score: float | None, band: str) -> None: ...
    async def log_score_history(self, vendor_id: str, score: float | None, components: dict, order_count: int) -> None: ...

    # goal creation + quote collection + orders (GoalProcessor capstone)
    async def create_goal(self, goal: Goal) -> str: ...
    async def get_company(self, company_id: str) -> Company: ...
    async def get_employee(self, employee_id: str) -> Employee | None: ...
    async def add_collected_quote(self, goal_id: str, quote: dict) -> None: ...
    async def get_collected_quotes(self, goal_id: str) -> list[dict]: ...
    async def create_order(self, order: Order) -> str: ...

    # inbound WhatsApp attribution (Fix 06, waba_router)
    async def get_goal_by_partial_id(self, partial_id: str, vendor_phone: str | None = None) -> Goal | None: ...
    async def get_active_rfq_goals_for_vendor(self, vendor_phone: str) -> list[Goal]: ...
    async def set_vendor_opted_out(self, vendor_phone: str) -> None: ...

    # vendor graph (rated-vendors-first signals)
    async def get_vendor_scores(self, vendor_ids: list[str]) -> dict: ...
    async def get_known_vendors(self, category: str, city: str) -> dict: ...
    async def upsert_vendor(self, vendor: dict) -> str: ...          # returns vendors.id (Fix 08: dedup by google_place_id)
    async def get_vendor_id_by_phone(self, phone: str) -> str | None: ...
    async def get_demo_vendors(self, category: str, city: str) -> list[dict]: ...   # seeded vendors for DEMO_MODE


class InMemoryStore:
    """In-process Store for tests and local experiments. Not for production."""

    def __init__(
        self,
        budgets: dict[tuple[str, str], float] | None = None,
        spent: dict[tuple[str, str], float] | None = None,
        goals: dict[str, Goal] | None = None,
        orders: dict[str, Order] | None = None,
        ratings: dict[str, Rating] | None = None,
        companies: dict[str, Company] | None = None,
        employees: dict[str, Employee] | None = None,
    ) -> None:
        self._budgets = budgets or {}
        self._spent = spent or {}
        self._goals = goals or {}
        self._tokens: dict[str, ApprovalToken] = {}
        self._orders = orders or {}
        self._ratings = ratings or {}
        self._companies = companies or {}
        self._employees = employees or {}
        self._vendors: dict[str, dict] = {}              # vendor_id -> vendor dict
        self._vendor_by_place: dict[str, str] = {}       # google_place_id -> vendor_id
        self._vendor_by_phone: dict[str, str] = {}       # phone -> vendor_id
        self._vendor_seq = 0
        self._optouts: set[str] = set()
        self._vendor_scores: dict[str, dict] = {}        # vendor_id -> {score, band}
        self.score_history: list[dict] = []              # inspectable by tests
        self.spend_records: list[dict] = []              # inspectable by tests
        self._rating_seq = 0
        self._goal_seq = 0
        self._order_seq = 0

    async def get_budget(self, company_id: str, category: str) -> Budget:
        return Budget(limit=self._budgets.get((company_id, category), 0.0))

    async def get_spent_this_period(self, company_id: str, category: str) -> float:
        return self._spent.get((company_id, category), 0.0)

    async def record_spend(self, company_id: str, category: str, amount: float, order_id: str) -> None:
        # Idempotent per order: a Fix-01 duplicate-recovery retry must not debit twice.
        if any(r["order_id"] == order_id for r in self.spend_records):
            return
        self._spent[(company_id, category)] = self._spent.get((company_id, category), 0.0) + amount
        self.spend_records.append(
            {"company_id": company_id, "category": category, "amount": amount, "order_id": order_id}
        )

    async def get_goal(self, goal_id: str) -> Goal:
        return self._goals[goal_id]

    async def get_goal_state(self, goal_id: str) -> str:
        return self._goals[goal_id].status

    async def set_goal_state(self, goal_id: str, to_state: str, payload: dict | None = None) -> None:
        self._goals[goal_id].status = to_state

    async def update_goal_options(self, goal_id: str, options: list) -> None:
        self._goals[goal_id].options = options

    async def set_goal_approval(self, goal_id: str, option_id: str, approved_at: datetime) -> None:
        g = self._goals[goal_id]
        g.selected_option_id = option_id
        g.approved_at = approved_at

    async def set_goal_approval_sent(self, goal_id: str, sent_at: datetime) -> None:
        self._goals[goal_id].approval_sent_at = sent_at

    async def create_approval_token(self, token: ApprovalToken) -> None:
        self._tokens[token.token] = token

    async def get_approval_token(self, token: str) -> ApprovalToken | None:
        return self._tokens.get(token)

    async def mark_approval_token_used(self, token: str, used_at: datetime) -> None:
        if token in self._tokens:
            self._tokens[token].used_at = used_at

    # ── orders ───────────────────────────────────────────────────────────────
    async def get_order(self, order_id: str) -> Order:
        return self._orders[order_id]

    async def get_orders_by_status(self, status: str) -> list[Order]:
        return [o for o in self._orders.values() if o.status == status]

    async def set_order_delivered(self, order_id: str, delivered_at: datetime, final_price: float | None = None) -> None:
        o = self._orders[order_id]
        o.delivered_at = delivered_at
        o.status = "delivered"
        if final_price is not None:
            o.final_price = final_price

    async def set_order_status(self, order_id: str, status: str) -> None:
        self._orders[order_id].status = status

    async def mark_order_rating_sent(self, order_id: str) -> None:
        self._orders[order_id].rating_sent = True

    async def get_orders_for_company_vendor(self, company_id, vendor_id, exclude_order_id=None) -> list[Order]:
        return [o for o in self._orders.values()
                if o.company_id == company_id and o.vendor_id == vendor_id and o.id != exclude_order_id]

    # ── ratings + scoring ────────────────────────────────────────────────────
    async def create_rating(self, rating: Rating) -> str:
        if not rating.id:
            self._rating_seq += 1
            rating.id = f"rat-{self._rating_seq}"
        self._ratings[rating.id] = rating
        return rating.id

    async def get_rating(self, rating_id: str) -> Rating:
        return self._ratings[rating_id]

    async def update_rating(self, rating_id: str, **fields) -> None:
        r = self._ratings[rating_id]
        for k, v in fields.items():
            setattr(r, k, v)

    async def get_ratings_for_vendor(self, vendor_id: str) -> list[Rating]:
        return [r for r in self._ratings.values() if r.vendor_id == vendor_id]

    async def update_vendor_score(self, vendor_id: str, score: float | None, band: str) -> None:
        self._vendor_scores[vendor_id] = {"score": score, "band": band}

    async def log_score_history(self, vendor_id: str, score, components: dict, order_count: int) -> None:
        self.score_history.append(
            {"vendor_id": vendor_id, "score": score, "components": components, "order_count": order_count})

    # ── goal creation + quotes + companies/employees (GoalProcessor) ──────────
    async def create_goal(self, goal: Goal) -> str:
        if not goal.id:
            self._goal_seq += 1
            goal.id = f"goal-{self._goal_seq}"
        self._goals[goal.id] = goal
        return goal.id

    async def get_company(self, company_id: str) -> Company:
        return self._companies[company_id]

    async def get_employee(self, employee_id: str) -> Employee | None:
        return self._employees.get(employee_id)

    async def add_collected_quote(self, goal_id: str, quote: dict) -> None:
        self._goals[goal_id].collected_quotes.append(quote)

    async def get_collected_quotes(self, goal_id: str) -> list[dict]:
        return list(self._goals[goal_id].collected_quotes)

    async def create_order(self, order: Order) -> str:
        if not order.id:
            self._order_seq += 1
            order.id = f"order-{self._order_seq}"
        self._orders[order.id] = order
        return order.id

    async def get_goal_by_partial_id(self, partial_id: str, vendor_phone: str | None = None) -> Goal | None:
        # Match on the goal's REF code (hash-based, exactly 8 chars). Collect ALL
        # matches; only attribute when exactly one matches, else return None so
        # the router falls through to the operator queue (never mis-attribute).
        target = partial_id.lower()
        matches = [g for g in self._goals.values() if ref_code(g.id).lower() == target]
        return matches[0] if len(matches) == 1 else None

    async def get_active_rfq_goals_for_vendor(self, vendor_phone: str) -> list[Goal]:
        # In-memory: goals still awaiting quotes. (Real impl joins RFQ dispatch rows.)
        return [g for g in self._goals.values() if g.status in ("pending_rfq", "quotes_received")]

    async def set_vendor_opted_out(self, vendor_phone: str) -> None:
        self._optouts.add(vendor_phone)

    async def get_vendor_scores(self, vendor_ids: list[str]) -> dict:
        return {vid: self._vendor_scores.get(vid, {}).get("score") for vid in vendor_ids}

    async def get_known_vendors(self, category: str, city: str) -> dict:
        return {v["google_place_id"]: {"score": self._vendor_scores.get(vid, {}).get("score"),
                                       "band": self._vendor_scores.get(vid, {}).get("band", "unproven"),
                                       "id": vid}
                for pid, vid in self._vendor_by_place.items()
                if (v := self._vendors.get(vid)) and v.get("category") == category
                and v.get("city") == city and self._vendor_scores.get(vid, {}).get("score") is not None}

    async def upsert_vendor(self, vendor: dict) -> str:
        pid = vendor.get("google_place_id")
        if pid and pid in self._vendor_by_place:
            vid = self._vendor_by_place[pid]
        else:
            self._vendor_seq += 1
            vid = f"vendor-{self._vendor_seq}"
            if pid:
                self._vendor_by_place[pid] = vid
        self._vendors[vid] = {**vendor, "id": vid}
        if vendor.get("phone"):
            self._vendor_by_phone[_digits(vendor["phone"])] = vid
        return vid

    async def get_vendor_id_by_phone(self, phone: str) -> str | None:
        return self._vendor_by_phone.get(_digits(phone))

    async def get_demo_vendors(self, category: str, city: str) -> list[dict]:
        # Match by category only — demo seeds are few, and a locality in the goal
        # ("Koramangala") shouldn't exclude a vendor seeded as "Bengaluru".
        return [{"google_place_id": v.get("google_place_id"), "name": v.get("name"),
                 "phone": v.get("phone"), "vendor_id": vid, "google_rating": v.get("google_rating"),
                 "city": v.get("city"), "source": "agent_found"}
                for vid, v in self._vendors.items() if v.get("category") == category]


def _f(v):
    """NUMERIC -> float (asyncpg returns Decimal); None passes through."""
    return float(v) if v is not None else None


class SupabaseStore:
    """Production Store backed by Supabase Postgres via asyncpg.

    The backend connects as the privileged Postgres role, which BYPASSES RLS by
    design — RLS (migration 014) guards the client/PostgREST path; this trusted
    backend enforces tenancy in code by scoping queries to company_id. Spend is
    tracked in a dedicated spend_records ledger (migration 017): orders are
    created before the payment, so summing orders for the Fix 02 budget check
    would double-count the in-flight order — the ledger is the source of truth.
    asyncpg is imported lazily so the module imports without it installed.
    """

    def __init__(self):
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import json

            import asyncpg

            from core.config import settings
            if not settings.supabase_postgres_url:
                raise RuntimeError("SUPABASE_POSTGRES_URL not set — cannot open Supabase pool")

            async def _init(conn):
                # Return JSONB/JSON as dict/list (and accept dict/list as params).
                await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
                await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

            self._pool = await asyncpg.create_pool(settings.supabase_postgres_url,
                                                   min_size=1, max_size=10, init=_init)
        return self._pool

    async def _row(self, sql, *args):
        pool = await self._get_pool()
        async with pool.acquire() as c:
            return await c.fetchrow(sql, *args)

    async def _rows(self, sql, *args):
        pool = await self._get_pool()
        async with pool.acquire() as c:
            return await c.fetch(sql, *args)

    async def _val(self, sql, *args):
        pool = await self._get_pool()
        async with pool.acquire() as c:
            return await c.fetchval(sql, *args)

    async def _exec(self, sql, *args):
        pool = await self._get_pool()
        async with pool.acquire() as c:
            await c.execute(sql, *args)

    # ── row -> dataclass mappers ──────────────────────────────────────────────
    @staticmethod
    def _to_goal(r) -> Goal:
        return Goal(
            id=str(r["id"]), status=r["status"], category=r["category"],
            company_id=str(r["company_id"]) if r["company_id"] else None,
            employee_id=str(r["employee_id"]) if r["employee_id"] else None,
            raw_input=r["raw_input"], budget_limit=_f(r["budget_limit"]),
            approval_sent_at=r["approval_sent_at"], approved_at=r["approved_at"],
            selected_option_id=r["selected_option_id"],
            parsed_intent=r["parsed_intent"] or {}, options=r["options"],
            collected_quotes=r["collected_quotes"] or [])

    @staticmethod
    def _to_order(r) -> Order:
        return Order(
            id=str(r["id"]), goal_id=str(r["goal_id"]), vendor_id=str(r["vendor_id"]),
            company_id=str(r["company_id"]), quoted_price=_f(r["quoted_price"]),
            final_price=_f(r["final_price"]), promised_eta=r["promised_eta"],
            delivered_at=r["delivered_at"], status=r["status"], rating_sent=r["rating_sent"],
            vendor_response_time_mins=r["vendor_response_time_mins"], description=r["description"])

    @staticmethod
    def _to_rating(r) -> Rating:
        return Rating(
            id=str(r["id"]), order_id=str(r["order_id"]), vendor_id=str(r["vendor_id"]),
            company_id=str(r["company_id"]), overall_rating=r["overall_rating"],
            satisfied=r["satisfied"], delivered_on_time=r["delivered_on_time"],
            price_accurate=r["price_accurate"], response_time_mins=r["response_time_mins"],
            is_repeat_order=r["is_repeat_order"], comment=r["comment"])

    # ── budget (Fix 02) ───────────────────────────────────────────────────────
    async def get_budget(self, company_id, category) -> Budget:
        policies = await self._val("SELECT budget_policies FROM companies WHERE id=$1::uuid", company_id)
        policies = policies or {}
        limit = policies.get(category, policies.get("default", 0))
        return Budget(limit=float(limit or 0))

    async def get_spent_this_period(self, company_id, category) -> float:
        v = await self._val(
            "SELECT COALESCE(SUM(amount),0) FROM spend_records "
            "WHERE company_id=$1::uuid AND category=$2 "
            "AND date_trunc('month', created_at AT TIME ZONE 'Asia/Kolkata') "
            "    = date_trunc('month', now() AT TIME ZONE 'Asia/Kolkata')",
            company_id, category)
        return float(v or 0)

    async def record_spend(self, company_id, category, amount, order_id) -> None:
        # ON CONFLICT keeps the ledger idempotent per order (Fix 01 retry safety).
        await self._exec(
            "INSERT INTO spend_records (company_id, category, amount, order_id) "
            "VALUES ($1::uuid, $2, $3, $4::uuid) ON CONFLICT (order_id) DO NOTHING",
            company_id, category, amount, order_id)

    # ── goals ─────────────────────────────────────────────────────────────────
    async def create_goal(self, goal: Goal) -> str:
        new_id = await self._val(
            "INSERT INTO goals (company_id, employee_id, raw_input, parsed_intent, category, "
            "status, budget_limit, options, collected_quotes) "
            "VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6, $7, $8::jsonb, $9::jsonb) RETURNING id",
            goal.company_id, goal.employee_id, goal.raw_input, goal.parsed_intent or {},
            goal.category, goal.status, goal.budget_limit, goal.options, goal.collected_quotes or [])
        goal.id = str(new_id)
        return goal.id

    async def get_goal(self, goal_id) -> Goal:
        r = await self._row("SELECT * FROM goals WHERE id=$1::uuid", goal_id)
        if r is None:
            raise KeyError(goal_id)
        return self._to_goal(r)

    async def get_goal_state(self, goal_id) -> str:
        s = await self._val("SELECT status FROM goals WHERE id=$1::uuid", goal_id)
        if s is None:
            raise KeyError(goal_id)
        return s

    async def set_goal_state(self, goal_id, to_state, payload=None) -> None:
        await self._exec("UPDATE goals SET status=$2, updated_at=now() WHERE id=$1::uuid", goal_id, to_state)

    async def update_goal_options(self, goal_id, options) -> None:
        await self._exec("UPDATE goals SET options=$2::jsonb WHERE id=$1::uuid", goal_id, options)

    async def set_goal_approval(self, goal_id, option_id, approved_at) -> None:
        await self._exec("UPDATE goals SET selected_option_id=$2, approved_at=$3 WHERE id=$1::uuid",
                         goal_id, option_id, approved_at)

    async def set_goal_approval_sent(self, goal_id, sent_at) -> None:
        await self._exec("UPDATE goals SET approval_sent_at=$2 WHERE id=$1::uuid", goal_id, sent_at)

    async def add_collected_quote(self, goal_id, quote) -> None:
        await self._exec(
            "UPDATE goals SET collected_quotes = COALESCE(collected_quotes, '[]'::jsonb) || $2::jsonb "
            "WHERE id=$1::uuid", goal_id, [quote])

    async def get_collected_quotes(self, goal_id) -> list[dict]:
        v = await self._val("SELECT collected_quotes FROM goals WHERE id=$1::uuid", goal_id)
        return v or []

    async def get_goal_by_partial_id(self, partial_id, vendor_phone=None) -> Goal | None:
        rows = await self._rows(
            "SELECT * FROM goals WHERE status IN ('pending_rfq','quotes_received')")
        target = partial_id.lower()
        matches = [r for r in rows if ref_code(str(r["id"])).lower() == target]
        return self._to_goal(matches[0]) if len(matches) == 1 else None

    async def get_active_rfq_goals_for_vendor(self, vendor_phone) -> list[Goal]:
        # No RFQ-dispatch table tracks which phone got which RFQ, so this returns
        # all active-RFQ goals; the REF code (P2) is the precise attribution path.
        rows = await self._rows(
            "SELECT * FROM goals WHERE status IN ('pending_rfq','quotes_received')")
        return [self._to_goal(r) for r in rows]

    # ── companies / employees ─────────────────────────────────────────────────
    async def get_company(self, company_id) -> Company:
        r = await self._row(
            "SELECT id, name, budget_policies, approval_chain, approver_email, "
            "slack_approval_channel, waba_number FROM companies WHERE id=$1::uuid", company_id)
        if r is None:
            raise KeyError(company_id)
        return Company(id=str(r["id"]), name=r["name"], budget_policies=r["budget_policies"] or {},
                       approval_chain=r["approval_chain"] or {}, approver_email=r["approver_email"],
                       slack_approval_channel=r["slack_approval_channel"], waba_number=r["waba_number"])

    async def get_employee(self, employee_id) -> Employee | None:
        r = await self._row("SELECT id, name, whatsapp, company_id FROM employees WHERE id=$1::uuid", employee_id)
        if r is None:
            return None
        return Employee(id=str(r["id"]), name=r["name"], whatsapp=r["whatsapp"],
                        company_id=str(r["company_id"]) if r["company_id"] else None)

    # ── approval tokens (Fix 12) ──────────────────────────────────────────────
    async def create_approval_token(self, token: ApprovalToken) -> None:
        await self._exec(
            "INSERT INTO approval_tokens (token, goal_id, approver_id, expires_at) "
            "VALUES ($1, $2::uuid, $3::uuid, $4)",
            token.token, token.goal_id, token.approver_id, token.expires_at)

    async def get_approval_token(self, token) -> ApprovalToken | None:
        r = await self._row("SELECT token, goal_id, approver_id, expires_at, used_at "
                            "FROM approval_tokens WHERE token=$1", token)
        if r is None:
            return None
        return ApprovalToken(token=r["token"], goal_id=str(r["goal_id"]),
                             approver_id=str(r["approver_id"]) if r["approver_id"] else None,
                             expires_at=r["expires_at"], used_at=r["used_at"])

    async def mark_approval_token_used(self, token, used_at) -> None:
        await self._exec("UPDATE approval_tokens SET used_at=$2 WHERE token=$1", token, used_at)

    # ── orders ────────────────────────────────────────────────────────────────
    async def create_order(self, order: Order) -> str:
        new_id = await self._val(
            "INSERT INTO orders (goal_id, vendor_id, company_id, quoted_price, final_price, "
            "promised_eta, delivered_at, status, rating_sent, vendor_response_time_mins, description) "
            "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id",
            order.goal_id, order.vendor_id, order.company_id, order.quoted_price, order.final_price,
            order.promised_eta, order.delivered_at, order.status, order.rating_sent,
            order.vendor_response_time_mins, order.description)
        order.id = str(new_id)
        return order.id

    async def get_order(self, order_id) -> Order:
        r = await self._row("SELECT * FROM orders WHERE id=$1::uuid", order_id)
        if r is None:
            raise KeyError(order_id)
        return self._to_order(r)

    async def get_orders_by_status(self, status) -> list[Order]:
        rows = await self._rows("SELECT * FROM orders WHERE status=$1", status)
        return [self._to_order(r) for r in rows]

    async def set_order_delivered(self, order_id, delivered_at, final_price=None) -> None:
        await self._exec(
            "UPDATE orders SET delivered_at=$2, status='delivered', "
            "final_price=COALESCE($3, final_price) WHERE id=$1::uuid",
            order_id, delivered_at, final_price)

    async def set_order_status(self, order_id, status) -> None:
        await self._exec("UPDATE orders SET status=$2 WHERE id=$1::uuid", order_id, status)

    async def mark_order_rating_sent(self, order_id) -> None:
        await self._exec("UPDATE orders SET rating_sent=true WHERE id=$1::uuid", order_id)

    async def get_orders_for_company_vendor(self, company_id, vendor_id, exclude_order_id=None) -> list[Order]:
        rows = await self._rows(
            "SELECT * FROM orders WHERE company_id=$1::uuid AND vendor_id=$2::uuid "
            "AND ($3::uuid IS NULL OR id <> $3::uuid)", company_id, vendor_id, exclude_order_id)
        return [self._to_order(r) for r in rows]

    # ── ratings + vendor scoring ──────────────────────────────────────────────
    async def create_rating(self, rating: Rating) -> str:
        new_id = await self._val(
            "INSERT INTO vendor_ratings (vendor_id, order_id, company_id, overall_rating, satisfied, "
            "delivered_on_time, price_accurate, response_time_mins, is_repeat_order, comment) "
            "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
            rating.vendor_id, rating.order_id, rating.company_id, rating.overall_rating, rating.satisfied,
            rating.delivered_on_time, rating.price_accurate, rating.response_time_mins,
            rating.is_repeat_order, rating.comment)
        rating.id = str(new_id)
        return rating.id

    async def get_rating(self, rating_id) -> Rating:
        r = await self._row("SELECT * FROM vendor_ratings WHERE id=$1::uuid", rating_id)
        if r is None:
            raise KeyError(rating_id)
        return self._to_rating(r)

    async def update_rating(self, rating_id, **fields) -> None:
        allowed = {"overall_rating", "satisfied", "comment", "delivered_on_time",
                   "price_accurate", "response_time_mins", "is_repeat_order"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                args.append(v)
                sets.append(f"{k}=${len(args)}")
        if not sets:
            return
        args.append(rating_id)
        await self._exec(f"UPDATE vendor_ratings SET {', '.join(sets)} WHERE id=${len(args)}::uuid", *args)

    async def get_ratings_for_vendor(self, vendor_id) -> list[Rating]:
        rows = await self._rows("SELECT * FROM vendor_ratings WHERE vendor_id=$1::uuid", vendor_id)
        return [self._to_rating(r) for r in rows]

    async def update_vendor_score(self, vendor_id, score, band) -> None:
        await self._exec(
            "UPDATE vendors SET composite_score=$2, score_band=$3, score_updated_at=now() WHERE id=$1::uuid",
            vendor_id, score, band)

    async def log_score_history(self, vendor_id, score, components, order_count) -> None:
        await self._exec(
            "INSERT INTO vendor_score_history (vendor_id, composite_score, components, order_count) "
            "VALUES ($1::uuid, $2, $3::jsonb, $4)", vendor_id, score, components, order_count)

    # ── vendor graph ──────────────────────────────────────────────────────────
    async def set_vendor_opted_out(self, vendor_phone) -> None:
        await self._exec("UPDATE vendors SET opted_out=true, opted_out_at=now() WHERE phone=$1", vendor_phone)

    async def get_vendor_scores(self, vendor_ids) -> dict:
        if not vendor_ids:
            return {}
        ids = [str(v) for v in vendor_ids]
        rows = await self._rows(
            "SELECT id, google_place_id, composite_score FROM vendors "
            "WHERE id::text = ANY($1::text[]) OR google_place_id = ANY($1::text[])", ids)
        by_key = {}
        for r in rows:
            score = _f(r["composite_score"])
            by_key[str(r["id"])] = score
            if r["google_place_id"]:
                by_key[r["google_place_id"]] = score
        return {vid: by_key.get(vid) for vid in vendor_ids}

    async def get_known_vendors(self, category, city) -> dict:
        rows = await self._rows(
            "SELECT id, google_place_id, composite_score, score_band FROM vendors "
            "WHERE category=$1 AND city=$2 AND composite_score IS NOT NULL", category, city)
        return {r["google_place_id"]: {"score": _f(r["composite_score"]), "band": r["score_band"],
                                       "id": str(r["id"])}
                for r in rows if r["google_place_id"]}

    async def upsert_vendor(self, vendor: dict) -> str:
        # Fix 08: google_place_id is the identity/dedup key (UNIQUE in migration 004).
        new_id = await self._val(
            "INSERT INTO vendors (name, category, sub_category, phone, email, website, "
            "google_place_id, google_rating, review_count, source, city, state) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) "
            "ON CONFLICT (google_place_id) DO UPDATE SET "
            "name=EXCLUDED.name, phone=EXCLUDED.phone, google_rating=EXCLUDED.google_rating, "
            "review_count=EXCLUDED.review_count, city=EXCLUDED.city, state=EXCLUDED.state "
            "RETURNING id",
            vendor.get("name"), vendor.get("category"), vendor.get("sub_category"),
            vendor.get("phone"), vendor.get("email"), vendor.get("website"),
            vendor.get("google_place_id"), vendor.get("google_rating"), vendor.get("review_count"),
            vendor.get("source", "google_places"), vendor.get("city"), vendor.get("state"))
        return str(new_id)

    async def get_vendor_id_by_phone(self, phone: str) -> str | None:
        # Match on digits only so '+919...' (stored) lines up with '919...' (inbound).
        v = await self._val(
            "SELECT id FROM vendors WHERE regexp_replace(phone, '[^0-9]', '', 'g') = $1 LIMIT 1",
            _digits(phone))
        return str(v) if v else None

    async def get_demo_vendors(self, category: str, city: str) -> list[dict]:
        # Category only (see InMemoryStore note): locality vs city must not exclude.
        rows = await self._rows(
            "SELECT id, google_place_id, name, phone, google_rating, city FROM vendors "
            "WHERE category=$1 ORDER BY created_at LIMIT 5", category)
        return [{"google_place_id": r["google_place_id"], "name": r["name"], "phone": r["phone"],
                 "vendor_id": str(r["id"]), "google_rating": _f(r["google_rating"]),
                 "city": r["city"], "source": "agent_found"} for r in rows]
