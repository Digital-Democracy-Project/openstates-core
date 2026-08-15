import pytest

from openstates.exceptions import ScrapeError
from openstates.utils.waf_circuit_breaker import raise_if_waf_block_threshold_reached


def test_below_threshold_does_not_raise():
    raise_if_waf_block_threshold_reached(
        consecutive_blocks=2,
        max_consecutive_blocks=3,
        exc=Exception("blocked"),
        scrape_label="test scrape",
        fetch_description="fetching test pages",
    )


def test_at_threshold_raises_scrape_error():
    with pytest.raises(ScrapeError):
        raise_if_waf_block_threshold_reached(
            consecutive_blocks=3,
            max_consecutive_blocks=3,
            exc=Exception("blocked"),
            scrape_label="test scrape",
            fetch_description="fetching test pages",
        )


def test_above_threshold_also_raises():
    with pytest.raises(ScrapeError):
        raise_if_waf_block_threshold_reached(
            consecutive_blocks=5,
            max_consecutive_blocks=3,
            exc=Exception("blocked"),
            scrape_label="test scrape",
            fetch_description="fetching test pages",
        )


def test_error_message_includes_label_and_count():
    with pytest.raises(ScrapeError) as exc_info:
        raise_if_waf_block_threshold_reached(
            consecutive_blocks=3,
            max_consecutive_blocks=3,
            exc=Exception("blocked"),
            scrape_label="fl archive fetch",
            fetch_description="fetching https://flhouse.gov/example",
        )
    message = str(exc_info.value)
    assert "fl archive fetch" in message
    assert "3 consecutive WAF blocks" in message
    assert "https://flhouse.gov/example" in message
