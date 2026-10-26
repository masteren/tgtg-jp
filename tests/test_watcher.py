from conftest import make_item

from tgtg_jp.watcher import StockTracker, format_price, parse_bag, parse_bags


def test_parse_bag_formats_price_and_jst_pickup():
    bag = parse_bag(make_item())
    assert bag.price == "¥500"
    assert bag.original_price == "¥1,500"
    # 09:00Z = 18:00 JST、2026-10-26 は月曜日
    assert bag.pickup_text("Asia/Tokyo") == "10/26(月) 18:00〜19:00"
    assert bag.share_url.endswith("/item/1001")


def test_format_price_other_currency():
    assert format_price({"code": "EUR", "minor_units": 399, "decimals": 2}) == "3.99 EUR"
    assert format_price(None) is None


def test_targets_filter():
    items = [make_item("1"), make_item("2")]
    assert [b.item_id for b in parse_bags(items, frozenset({"2"}))] == ["2"]
    assert len(parse_bags(items)) == 2


def test_notifies_once_while_in_stock():
    tracker = StockTracker()
    bags = parse_bags([make_item(available=3)])
    first = tracker.pending(bags)
    assert len(first) == 1
    tracker.mark(first[0])
    # 在庫数が変わっても同じ出品なら再通知しない
    assert tracker.pending(parse_bags([make_item(available=1)])) == []


def test_restock_after_sellout_notifies_again():
    tracker = StockTracker()
    tracker.mark(tracker.pending(parse_bags([make_item()]))[0])
    assert tracker.pending(parse_bags([make_item(available=0)])) == []
    assert len(tracker.pending(parse_bags([make_item(available=1)]))) == 1


def test_new_pickup_window_is_a_new_listing():
    tracker = StockTracker()
    tracker.mark(tracker.pending(parse_bags([make_item()]))[0])
    tomorrow = make_item(start="2026-10-27T09:00:00Z", end="2026-10-27T10:00:00Z")
    assert len(tracker.pending(parse_bags([tomorrow]))) == 1


def test_state_survives_restart(tmp_path):
    path = tmp_path / "notified.json"
    tracker = StockTracker(path)
    tracker.mark(tracker.pending(parse_bags([make_item()]))[0])
    tracker.save()
    assert StockTracker(path).pending(parse_bags([make_item()])) == []


def test_corrupt_state_file_is_ignored(tmp_path):
    path = tmp_path / "notified.json"
    path.write_text("{broken")
    assert len(StockTracker(path).pending(parse_bags([make_item()]))) == 1
