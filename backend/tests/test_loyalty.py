from datetime import date

from app.core.loyalty import Rules, check_use, earned, lapsed, redeemable, reward, stamp

P = Rules(mode="points")
S = Rules(mode="stamps", stamps_needed=5, reward_paise=14900)
B = Rules(mode="both", min_bill_paise=5000)


def test_earn_and_stamps():
    assert earned(P, 20000) == 1000 and earned(Rules(), 20000) == 0 and earned(B, 4999) == 0 and earned(B, 5000) == 250
    assert stamp(S, 10000) == 1 and stamp(S, 9999) == 0 and stamp(P, 50000) == 0


def test_redeem_caps():
    assert redeemable(P, 4999, 100000) == 0           # below the ₹50 minimum
    assert redeemable(P, 30050, 40000) == 20000        # half the bill, whole rupees
    assert redeemable(P, 8000, 100000) == 8000
    assert reward(S, 4, 30000) == 0 and reward(S, 5, 30000) == 14900 and reward(S, 6, 9900) == 9900


def test_check_use():
    assert check_use(P, points_paise=8000, use_reward=False, balance_paise=8000, stamps=0, gross_paise=100000) is None
    assert "At most" in check_use(P, points_paise=9000, use_reward=False, balance_paise=9000, stamps=0, gross_paise=10000)
    assert check_use(P, points_paise=150, use_reward=False, balance_paise=9000, stamps=0, gross_paise=100000) == "Use points in whole rupees"
    assert check_use(S, points_paise=0, use_reward=True, balance_paise=0, stamps=2, gross_paise=20000).startswith("The customer hasn't")
    assert check_use(Rules(mode="both", stamps_needed=1, reward_paise=10000), points_paise=5000, use_reward=True,
                     balance_paise=9000, stamps=1, gross_paise=12000) == "Points and reward can't be more than the bill"


def test_lapse():
    assert lapsed(P, date(2026, 1, 1), date(2026, 7, 1)) and not lapsed(P, date(2026, 6, 1), date(2026, 7, 1))
    assert not lapsed(Rules(mode="points", expiry_days=0), date(2020, 1, 1), date(2026, 1, 1))
