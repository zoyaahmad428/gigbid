from agent.handoffs import RiskReport, FitReport

def test_risk_score_is_bounded():
    report = RiskReport(score=0.5, flags=[], explanation="sample")
    assert 0 <= report.score <= 1

def test_fit_score_is_bounded():
    report = FitReport(score=0.8)
    assert 0 <= report.score <= 1


def test_automatic_decision_can_only_skip():
    import pytest

    from agent.handoffs import BidDecision
    BidDecision(action="skip", reasons=["low fit"], fit_score=0.1, risk_score=0.2, auto=True)
    with pytest.raises(ValueError):
        BidDecision(action="bid", reasons=["good fit"], fit_score=0.9, risk_score=0.1, auto=True)


def test_rejection_needs_reason():
    import pytest

    from agent.handoffs import ApprovalDecision
    with pytest.raises(ValueError):
        ApprovalDecision(action="reject")
    assert ApprovalDecision(action="reject", reason="too generic").reason
