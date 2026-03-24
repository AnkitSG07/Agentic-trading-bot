from datetime import datetime

import pandas as pd

from core.candidate_builder import CandidateBuilder, CandidateBuilderConfig


def _frame(closes, volume=150000):
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [volume] * len(closes),
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="D"),
    )


def test_candidate_generation_from_deterministic_inputs():
    builder = CandidateBuilder(CandidateBuilderConfig(capital_budget=10000, max_candidates=5))
    frames = {
        "AAA": _frame([100 + i for i in range(30)]),
        "BBB": _frame([130 - i for i in range(30)]),
    }

    candidates = builder.build_candidates(
        frames,
        price_references={"AAA": 129, "BBB": 101},
        generated_at=datetime(2026, 3, 23, 10, 0),
        regime="trend",
        session_name="mid_session",
        sector_map={"AAA": "technology", "BBB": "banks"},
    )

    assert [candidate.symbol for candidate in candidates] == ["AAA", "BBB"]
    assert candidates[0].candidate_id == "AAA:BUY:2026-03-23T10:00:00"
    assert candidates[0].side == "BUY"
    assert candidates[1].side == "SHORT"


def test_candidate_builder_estimates_affordability_and_structured_fields():
    builder = CandidateBuilder(CandidateBuilderConfig(capital_budget=500, max_candidates=5))
    frames = {"AAA": _frame([50 + i for i in range(30)])}

    candidates = builder.build_candidates(
        frames,
        price_references={"AAA": 79},
        generated_at=datetime(2026, 3, 23, 11, 0),
        regime="trend",
        session_name="opening",
        sector_map={"AAA": "energy"},
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.max_affordable_qty == 6
    assert candidate.sector_tag == "energy"
    assert candidate.signal_strength > 0
    assert candidate.risk_reward == 2.0
    assert "session:opening" in candidate.caution_flags
    assert "regime:trend" in candidate.event_flags


def test_news_does_not_create_trades_by_itself():
    builder = CandidateBuilder(CandidateBuilderConfig(capital_budget=10000))

    candidates = builder.build_candidates(
        {},
        generated_at=datetime(2026, 3, 23, 10, 0),
        news_items=[{
            "headline": "AAA surges after strong earnings beat",
            "symbols": ["AAA"],
            "published_at": "2026-03-23T09:45:00",
        }],
    )

    assert candidates == []


def test_blocking_news_removes_risky_candidate():
    builder = CandidateBuilder(CandidateBuilderConfig(capital_budget=10000, max_candidates=5))
    frames = {"AAA": _frame([100 + i for i in range(30)])}

    candidates = builder.build_candidates(
        frames,
        price_references={"AAA": 129},
        generated_at=datetime(2026, 3, 23, 10, 0),
        news_items=[{
            "headline": "AAA faces SEBI probe and fraud allegations",
            "symbols": ["AAA"],
            "published_at": "2026-03-23T09:30:00",
        }],
    )

    assert candidates == []


def test_news_priority_modifier_reorders_candidates():
    builder = CandidateBuilder(CandidateBuilderConfig(capital_budget=10000, max_candidates=5))
    frames = {
        "AAA": _frame([100 + i for i in range(30)]),
        "BBB": _frame([100 + i for i in range(30)]),
    }

    candidates = builder.build_candidates(
        frames,
        price_references={"AAA": 129, "BBB": 129},
        generated_at=datetime(2026, 3, 23, 10, 0),
        news_items=[
            {
                "headline": "BBB gains after strong order win and upgrade",
                "symbols": ["BBB"],
                "published_at": "2026-03-23T09:40:00",
            }
        ],
    )

    assert candidates[0].symbol == "BBB"
    assert candidates[0].priority > candidates[1].priority
