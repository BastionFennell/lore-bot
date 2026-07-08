from __future__ import annotations

from lorebot.discord_io import SeenMessages


def test_same_id_processed_once():
    seen = SeenMessages()
    assert seen.add(12345) is True   # first delivery -> process
    assert seen.add(12345) is False  # gateway redelivery -> skip
    assert seen.add(12345) is False


def test_distinct_ids_all_new():
    seen = SeenMessages()
    assert [seen.add(i) for i in range(5)] == [True] * 5


def test_accepts_int_or_str_ids_interchangeably():
    seen = SeenMessages()
    assert seen.add(999) is True
    assert seen.add("999") is False  # same id, different type
    assert 999 in seen and "999" in seen


def test_bounded_and_evicts_oldest():
    seen = SeenMessages(maxlen=3)
    for i in range(3):
        seen.add(i)
    assert len(seen) == 3
    seen.add(3)  # evicts id 0
    assert len(seen) == 3
    assert 0 not in seen
    # 0 was evicted, so it reads as new again (bounded memory, acceptable)
    assert seen.add(0) is True
    # a still-remembered id is still deduped
    assert seen.add(2) is False
