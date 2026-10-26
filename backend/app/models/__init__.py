"""Nine v7 tables; wagers are a read-only projection of immutable stake rows."""
from sqlalchemy import case, event, select, type_coerce
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from app.db.schema import build_metadata, CODES
from app.db.types import CodeLabel
from app.db.compact import decode_issues, ISSUES


class Base(DeclarativeBase):
    metadata = build_metadata()


def table(name):
    return Base.metadata.tables["lolpick.lolpick_" + name]


for name, columns in {
    "seasons": {"status": "season_status"},
    "member_sessions": {"status": "session_status"},
    "matches": {"status": "match_status", "result": "result",
                "provider_status": "provider_status", "settlement_status": "settlement_status"},
    "point_transactions": {"reason": "point_reason", "cheer_action": "cheer_action"},
    "system_logs": {"outcome": "outcome", "severity": "severity", "source": "source",
                    "phase": "phase", "http_method": "http_method"},
}.items():
    for column, group in columns.items():
        table(name).c[column].type = CodeLabel(group)

MEMBER_TYPE = 1
ADMIN_TYPE = 2


class ApiSource(Base):
    __table__ = table("api_sources")


class Season(Base):
    __table__ = table("seasons")


class Member(Base):
    __table__ = table("members")
    current_season = relationship(Season, lazy="selectin")


class MemberSession(Base):
    __table__ = table("member_sessions")


class Team(Base):
    __table__ = table("teams")
    source = relationship(ApiSource, lazy="selectin", viewonly=True)


class Match(Base):
    __table__ = table("matches")
    season_row = relationship(Season, lazy="selectin", viewonly=True)
    source = relationship(ApiSource, foreign_keys=[__table__.c.api_source_id], lazy="selectin", viewonly=True)
    team_a = relationship(Team, foreign_keys=[__table__.c.team_a_id, __table__.c.api_source_id],
                          lazy="selectin", viewonly=True)
    team_b = relationship(Team, foreign_keys=[__table__.c.team_b_id, __table__.c.api_source_id],
                          lazy="selectin", viewonly=True)

    @hybrid_property
    def api_source(self):
        return self.source.code

    @api_source.expression
    def api_source(cls):
        return select(ApiSource.code).where(ApiSource.id == cls.api_source_id).scalar_subquery()

    @property
    def winner_team_id(self):
        return {"team_a_win": self.team_a_id, "team_b_win": self.team_b_id}.get(self.result)

    @property
    def settlement_remainder(self):
        return None if self.settlement_pool is None else self.settlement_pool - self.settlement_paid

    @property
    def refunded_at(self):
        return self.settlement_at if self.settlement_status == "refunded" else None

    @property
    def settled_at(self):
        return self.settlement_at if self.settlement_status == "settled" else None

    @property
    def api_data(self):
        return {"normalized": {
            **(self.display_data or {}), "source_status": self.provider_status,
            "issues": decode_issues(self.issue_flags or 0), "forfeit": self.is_forfeit,
            "draw": bool((self.issue_flags or 0) & ISSUES["draw"]),
        }}


class PointTransaction(Base):
    __table__ = table("point_transactions")


class CheerContract(Base):
    __table__ = table("cheer_contracts")


class SystemLog(Base):
    __table__ = table("system_logs")

    @hybrid_property
    def event_name(self):
        return CODES["event"][self.event_code]

    @event_name.expression
    def event_name(cls):
        return case(CODES["event"], value=cls.event_code)


# This selectable is not added to Base.metadata: create_all creates nine tables,
# never a tenth bets/view table. Native DDL exposes the equivalent SQL VIEW.
_w = table("point_transactions")
_m = table("matches")
_p = _w.alias("final_post")
_projection = (
    select(
        _w.c.id, _w.c.member_id, _w.c.season_id, _w.c.match_id, _w.c.selected_side,
        (-_w.c.amount).label("amount"), _w.c.contract_id, _w.c.cheer_action,
        _w.c.idempotency_key_hash, _w.c.created_at,
        case((_w.c.selected_side == 1, _m.c.team_a_id), else_=_m.c.team_b_id).label("selected_team_id"),
        type_coerce(case((_m.c.settlement_status == "pending", 1),
                        (_m.c.settlement_status == "refunded", 4),
                        (_w.c.selected_side == _m.c.result, 2), else_=3),
                    CodeLabel("bet_status")).label("status"),
        case((_m.c.settlement_status == "pending", None),
             (_m.c.settlement_status == "refunded", -_w.c.amount),
             (_w.c.selected_side == _m.c.result, _p.c.amount), else_=0).label("payout"),
        _m.c.settlement_at.label("settled_at"),
    )
    .select_from(_w.join(_m, _m.c.id == _w.c.match_id).outerjoin(
        _p, (_p.c.related_entry_id == _w.c.id) & _p.c.reason.in_(["bet_payout", "bet_refund"])))
    .where(_w.c.reason == "bet_stake").subquery("wager_projection")
)


class Wager(Base):
    __table__ = _projection
    __mapper_args__ = {"primary_key": [_projection.c.id]}


# Read compatibility for existing report callers. No physical bets table exists.
Bet = Wager


@event.listens_for(Session, "before_flush")
def prevent_wager_writes(session, flush_context, instances):
    if any(isinstance(row, Wager) for row in session.new | session.dirty | session.deleted):
        raise ValueError("Wagers are read-only; post a bet_stake through the points service.")

