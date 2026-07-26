"""The fire lock: claim-or-fail must be one atomic operation."""
from reverie_automata.runner import claim_lock


def test_claim_lock_is_atomic_and_exclusive(tmp_path):
    """O_CREAT|O_EXCL means a second claimant while the lock is held always
    loses, with no exists()-then-write window where both could pass."""
    lock = tmp_path / "fire.lock"
    assert claim_lock(lock) is True
    pid, host = lock.read_text().split()        # pid AND host, for reap_lock
    assert pid.isdigit() and host
    assert claim_lock(lock) is False            # held -> loser backs off
    lock.unlink()
    assert claim_lock(lock) is True             # released -> claimable again
