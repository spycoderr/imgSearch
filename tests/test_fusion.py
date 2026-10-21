import pytest

from imgsearch.interfaces import Candidate
from imgsearch.search.fusion import rrf_fuse

K = 60


def ranking(signal: str, *image_ids: str) -> list[Candidate]:
    """Candidates ranked 1, 2, ... in the given order, with score = 10 - rank."""
    return [
        Candidate(image_id=i, rank=r, score=10.0 - r, signal=signal)
        for r, i in enumerate(image_ids, start=1)
    ]


def order(fused) -> list[str]:
    return [f.image_id for f in fused]


def test_empty_input():
    assert rrf_fuse([], K) == []
    assert rrf_fuse([[], []], K) == []


def test_one_list_keeps_its_order():
    fused = rrf_fuse([ranking("vector", "c", "a", "b")], K)
    assert order(fused) == ["c", "a", "b"]
    assert [f.score for f in fused] == pytest.approx([1 / 61, 1 / 62, 1 / 63])
    assert fused[0].signals == {"vector": (1, 9.0)}


def test_two_lists_that_agree():
    fused = rrf_fuse([ranking("vector", "a", "b"), ranking("text", "a", "b")], K)
    assert order(fused) == ["a", "b"]
    assert fused[0].score == pytest.approx(2 / 61)
    assert fused[0].signals == {"vector": (1, 9.0), "text": (1, 9.0)}


def test_two_lists_that_disagree():
    # a and c are 1st in one list and 3rd in the other; b is 2nd in both. 1/61 + 1/63 is a
    # little more than 2/62, so a and c (tied, so by image_id) come before b.
    fused = rrf_fuse([ranking("vector", "a", "b", "c"), ranking("text", "c", "b", "a")], K)
    assert order(fused) == ["a", "c", "b"]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 63)
    assert fused[2].score == pytest.approx(2 / 62)


def test_image_in_one_list_only():
    fused = rrf_fuse([ranking("vector", "a", "b"), ranking("text", "b", "x")], K)
    assert order(fused) == ["b", "a", "x"]
    by_id = {f.image_id: f for f in fused}
    assert by_id["a"].signals == {"vector": (1, 9.0)}
    assert by_id["x"].signals == {"text": (2, 8.0)}
    assert by_id["x"].score == pytest.approx(1 / 62)


def test_equal_score_and_best_rank_breaks_by_image_id():
    # z and y are each 1st in one list: same score, same best rank, so y comes first.
    fused = rrf_fuse([ranking("vector", "z", "s"), ranking("text", "y")], K)
    assert order(fused) == ["y", "z", "s"]


def test_equal_score_prefers_the_better_best_rank():
    # With k=1: m has ranks 1 and 5 (1/2 + 1/6), n has ranks 2 and 2 (1/3 + 1/3). Equal
    # scores; m's best rank is 1, so m comes first.
    vector = ranking("vector", "m", "n")  # m 1, n 2
    text = ranking("text", "x1", "n", "x3", "x4", "m")  # n 2, m 5
    fused = rrf_fuse([vector, text], 1)
    by_id = {f.image_id: f for f in fused}
    assert by_id["m"].score == pytest.approx(by_id["n"].score)
    assert order(fused).index("m") < order(fused).index("n")


def test_equal_score_prefers_the_better_best_rank_over_image_id():
    # Same tie with the ids swapped, so image_id order would give the opposite answer.
    vector = ranking("vector", "b", "a")  # b 1, a 2
    text = ranking("text", "x1", "a", "x3", "x4", "b")  # a 2, b 5
    fused = rrf_fuse([vector, text], 1)
    assert order(fused).index("b") < order(fused).index("a")


def test_deterministic_regardless_of_list_order():
    a = ranking("vector", "a", "b", "c")
    b = ranking("text", "c", "d", "a")
    assert rrf_fuse([a, b], K) == rrf_fuse([b, a], K)


# ---- weights (P1.7) ----


def test_weights_scale_each_signal():
    fused = rrf_fuse(
        [ranking("vector", "a", "b"), ranking("text", "b", "c")], K, {"vector": 1.0, "text": 0.5}
    )
    by_id = {f.image_id: f.score for f in fused}
    assert by_id["a"] == pytest.approx(1 / 61)
    assert by_id["b"] == pytest.approx(1 / 62 + 0.5 / 61)
    assert by_id["c"] == pytest.approx(0.5 / 62)


def test_a_low_text_weight_lets_vector_win():
    # Plain RRF: c (text 1st) ties x (vector 1st) and beats a (vector 2nd). With text at
    # 0.25, vector's 2nd place beats text's 1st.
    lists = [ranking("vector", "x", "a"), ranking("text", "c")]
    assert order(rrf_fuse(lists, K)) == ["c", "x", "a"]
    assert order(rrf_fuse(lists, K, {"text": 0.25})) == ["x", "a", "c"]


def test_missing_weight_is_one_and_equal_weights_match_plain_rrf():
    lists = [ranking("vector", "a", "b", "c"), ranking("text", "c", "b", "a")]
    assert rrf_fuse(lists, K, {"text": 1.0}) == rrf_fuse(lists, K)
    assert rrf_fuse(lists, K, {"vector": 1.0, "text": 1.0}) == rrf_fuse(lists, K)


def test_weighted_ties_break_by_best_rank_then_id():
    # Both signals weighted 2 order like plain RRF, ties included (y and z: same score and
    # best rank, so by image_id).
    lists = [ranking("vector", "z", "s"), ranking("text", "y")]
    assert order(rrf_fuse(lists, K, {"vector": 2.0, "text": 2.0})) == ["y", "z", "s"]
    fused = rrf_fuse([ranking("vector", "a", "b", "c"), ranking("text", "c", "b", "a")], K, {})
    assert order(fused) == ["a", "c", "b"]


def test_weighted_is_deterministic_regardless_of_list_order():
    a = ranking("vector", "a", "b", "c")
    b = ranking("text", "c", "d", "a")
    weights = {"vector": 1.0, "text": 0.3}
    assert rrf_fuse([a, b], K, weights) == rrf_fuse([b, a], K, weights)
