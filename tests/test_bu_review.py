import pytest
from evaluation.bu_review import score


def test_grounding_coverage_and_multiple_relevant_documents():
    sample = dict(
        id="a",
        status="answered",
        correct=False,
        unsupported_claim=True,
        access_violation=False,
        gold_visible_docs=["p", "q"],
        retrieved_docs=["p", "p", "r"],
    )
    abstention = sample | dict(
        id="b",
        status="fallback",
        correct=True,
        unsupported_claim=None,
        gold_visible_docs=[],
        retrieved_docs=[],
    )
    result = score([sample, abstention])
    assert (
        result["decision_accuracy"]["rate"] == result["answer_coverage"]["rate"] == 0.5
    )
    assert result["hallucination_rate"]["rate"] == 1
    assert result["precision@3"] == result["recall@3"] == 0.5
    assert score([abstention])["hallucination_rate"]["rate"] is None
    with pytest.raises(ValueError, match="Review all claims"):
        score([sample | {"unsupported_claim": None}])
