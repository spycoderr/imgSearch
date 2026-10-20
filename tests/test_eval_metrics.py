import pytest

from imgsearch.eval.metrics import (
    hit_at_k,
    query_metrics,
    recall_at_k,
    reciprocal_rank,
    summarize,
)

RANKED = ["a", "b", "c", "d", "e", "f"]  # "f" is 6th, outside the top 5


def test_no_hits():
    assert query_metrics(RANKED, ["x", "y"]) == {"hit@5": 0.0, "recall@5": 0.0, "rr": 0.0}


def test_all_hits():
    assert query_metrics(RANKED, ["a", "b"]) == {"hit@5": 1.0, "recall@5": 1.0, "rr": 1.0}


def test_first_hit_lower_down():
    assert query_metrics(RANKED, ["c", "x"]) == {"hit@5": 1.0, "recall@5": 0.5, "rr": 1 / 3}


def test_hit_outside_top_5_does_not_count():
    assert query_metrics(RANKED, ["f"]) == {"hit@5": 0.0, "recall@5": 0.0, "rr": 0.0}


def test_more_expected_than_5():
    expected = list("abcdefgh")  # 8 expected; the top 5 can hold at most 5 of them
    assert recall_at_k(RANKED, expected) == 1.0
    assert recall_at_k(["a", "x", "b", "y", "z"], expected) == pytest.approx(2 / 5)


def test_fewer_than_5_results():
    assert recall_at_k(["a"], ["a", "b"]) == 0.5
    assert hit_at_k([], ["a"]) == 0.0
    assert reciprocal_rank([], ["a"]) == 0.0


def test_empty_expected_is_an_error():
    with pytest.raises(ValueError):
        recall_at_k(RANKED, [])


def test_k_is_a_parameter():
    assert hit_at_k(RANKED, ["f"], k=6) == 1.0
    assert reciprocal_rank(RANKED, ["f"], k=6) == pytest.approx(1 / 6)


def test_summarize_overall_and_by_group():
    rows = [
        {"language": "english", "type": "scene", "hit@5": 1.0, "recall@5": 1.0, "rr": 1.0},
        {"language": "english", "type": "text", "hit@5": 0.0, "recall@5": 0.0, "rr": 0.0},
        {"language": "hindi", "type": "scene", "hit@5": 1.0, "recall@5": 0.5, "rr": 0.5},
    ]
    s = summarize(rows)
    assert s["overall"] == pytest.approx({"n": 3, "hit@5": 2 / 3, "recall@5": 0.5, "rr": 0.5})
    assert s["language"]["english"] == {"n": 2, "hit@5": 0.5, "recall@5": 0.5, "rr": 0.5}
    assert s["language"]["hindi"]["n"] == 1
    assert s["type"]["scene"] == {"n": 2, "hit@5": 1.0, "recall@5": 0.75, "rr": 0.75}
    assert summarize([])["overall"] == {"n": 0, "hit@5": 0.0, "recall@5": 0.0, "rr": 0.0}
