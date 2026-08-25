import datetime as dt

from engine.judgment import trust

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def test_new_trust_is_the_prior():
    t = trust.new_trust("D-01", NOW)
    assert t.alpha == trust.PRIOR_ALPHA
    assert t.beta == trust.PRIOR_BETA
    assert trust.mean(t) == 0.5


def test_kept_increases_alpha_by_one():
    t = trust.new_trust("D-01", NOW)
    t2 = trust.update_kept(t, NOW)
    assert t2.alpha == trust.PRIOR_ALPHA + 1
    assert t2.beta == trust.PRIOR_BETA


def test_broken_increases_beta_by_one():
    t = trust.new_trust("D-01", NOW)
    t2 = trust.update_broken(t, NOW)
    assert t2.beta == trust.PRIOR_BETA + 1
    assert t2.alpha == trust.PRIOR_ALPHA


def test_refusal_is_pending_neutral_no_alpha_beta_move_at_zero_elapsed():
    t = trust.new_trust("D-01", NOW)
    t2 = trust.update_refusal(t, NOW)
    assert t2.alpha == t.alpha
    assert t2.beta == t.beta


def test_decay_halves_the_excess_over_one_half_life():
    t = trust.TrustState(debtor_id="D-01", alpha=10.0, beta=2.0, last_update=NOW)
    later = NOW + dt.timedelta(days=trust.HALF_LIFE_DAYS)
    decayed = trust.decay(t, later)
    # excess over prior was (10-2)=8 alpha, (2-2)=0 beta -> after one half-life, excess should be ~4
    assert abs(decayed.alpha - (trust.PRIOR_ALPHA + 4.0)) < 1e-9
    assert abs(decayed.beta - trust.PRIOR_BETA) < 1e-9


def test_decay_never_crosses_back_past_the_prior():
    t = trust.TrustState(debtor_id="D-01", alpha=20.0, beta=2.0, last_update=NOW)
    far_future = NOW + dt.timedelta(days=trust.HALF_LIFE_DAYS * 50)
    decayed = trust.decay(t, far_future)
    assert decayed.alpha > trust.PRIOR_ALPHA
    assert abs(decayed.alpha - trust.PRIOR_ALPHA) < 0.01


def test_mean_reflects_kept_vs_broken_history():
    reliable = trust.new_trust("D-reliable", NOW)
    unreliable = trust.new_trust("D-unreliable", NOW)
    for _ in range(5):
        reliable = trust.update_kept(reliable, NOW)
        unreliable = trust.update_broken(unreliable, NOW)
    assert trust.mean(reliable) > 0.5
    assert trust.mean(unreliable) < 0.5
