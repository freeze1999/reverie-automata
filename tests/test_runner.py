"""The fire lock: claim-or-fail must be one atomic operation."""
from reverie_automata.runner import claim_lock


def test_claim_lock_is_atomic_and_exclusive(tmp_path):
    """O_CREAT|O_EXCL means a second claimant while the lock is held always
    loses, with no exists()-then-write window where both could pass."""
    lock = tmp_path / "fire.lock"
    assert claim_lock(lock) is True
    assert lock.read_text().strip().isdigit()   # PID stamped for reap_lock
    assert claim_lock(lock) is False            # held -> loser backs off
    lock.unlink()
    assert claim_lock(lock) is True             # released -> claimable again
