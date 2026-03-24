from datetime import datetime

from data.news_classifier import NewsClassifier, VALID_RECOMMENDATIONS


def test_structured_classification_output():
    classifier = NewsClassifier()

    items = classifier.classify_news([
        {
            "headline": "INFY gains after order win and strong growth outlook",
            "symbols": ["INFY"],
            "sector": "technology",
            "published_at": "2026-03-23T09:30:00",
        }
    ], now=datetime(2026, 3, 23, 10, 0))

    assert len(items) == 1
    item = items[0]
    assert item.affected_symbols == ("INFY",)
    assert item.sector == "technology"
    assert item.sentiment_score > 0
    assert item.recommendation == "boost"


def test_recommendation_values_and_negative_caution_or_block():
    classifier = NewsClassifier()

    items = classifier.classify_news([
        {
            "headline": "BANKX sees margin pressure after weak guidance",
            "symbols": ["BANKX"],
            "published_at": "2026-03-23T08:00:00",
        },
        {
            "headline": "AUTOCO faces fraud investigation and SEBI probe",
            "symbols": ["AUTOCO"],
            "published_at": "2026-03-23T08:30:00",
        },
    ], now=datetime(2026, 3, 23, 10, 0))

    assert all(item.recommendation in VALID_RECOMMENDATIONS for item in items)
    assert items[0].recommendation in {"caution", "block"}
    assert items[1].recommendation == "block"


def test_freshness_and_confidence_handling():
    classifier = NewsClassifier()

    items = classifier.classify_news([
        {
            "headline": "AAA upgrade triggers rally",
            "symbols": ["AAA"],
            "published_at": "2026-03-23T09:45:00",
        },
        {
            "headline": "BBB positive expansion plan",
            "sector": "energy",
            "published_at": "2026-03-18T09:45:00",
        },
    ], now=datetime(2026, 3, 23, 10, 0))

    assert len(items) == 1
    fresh = items[0]
    assert fresh.impact_horizon == "intraday"
    assert fresh.confidence >= 0.6


def test_stale_items_are_filtered_outside_freshness_window():
    classifier = NewsClassifier()

    items = classifier.classify_news([
        {
            "headline": "BBB positive expansion plan",
            "sector": "energy",
            "published_at": "2026-03-18T09:45:00",
        },
    ], now=datetime(2026, 3, 23, 10, 0))

    assert items == []
