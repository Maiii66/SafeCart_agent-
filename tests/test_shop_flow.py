from frontend.intent import looks_like_spending_rule


def test_shop_box_detects_spending_rules():
    assert looks_like_spending_rule("max $50 per item")
    assert looks_like_spending_rule("trusted stores only")
    assert looks_like_spending_rule("max 3 orders a day")


def test_shop_box_does_not_treat_shopping_goal_as_rule():
    assert not looks_like_spending_rule("buy earbuds and a charger under $80")
