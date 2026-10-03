"""#52 — confirmation keys survive a re-read; ambiguous migrations do not apply."""
import json
from pathlib import Path

from app.services.rules.enforcement import (
    _key,
    assign_enforcement,
    blocking_items,
    migrate_confirmations,
)
from app.services.rules.schema import RuleSet, load_ruleset

FX = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _rs6():
    return assign_enforcement(load_ruleset({"ruleset": json.loads((FX / "ruleset_6.json").read_text())}))


def test_reread_6_twice_keeps_the_same_account_keys():
    rs = _rs6()
    first = blocking_items(rs)
    account = next(b for b in first if str(b.get("rule", "")).startswith("account:"))
    for a in rs.account_requirements:
        if a.text == account["text"]:
            a.text = "Rephrased by the model. The verified quote did not change."
    second = blocking_items(rs)
    third = blocking_items(rs)
    assert account["key"] in {b["key"] for b in second}
    assert [b["key"] for b in second] == [b["key"] for b in third]
    assert any(b["key"] == "human:pre_approval" or b["key"] == "human:logo_file" for b in first)


def test_fixed_keys_are_not_hashes():
    rs = RuleSet()
    rs.pre_approval.required = True
    rs.logo.required = True
    rs.logo.url = None
    items = {b["key"] for b in blocking_items(assign_enforcement(rs))}
    assert "human:pre_approval" in items
    assert "human:logo_file" in items


def test_old_sha1_confirmation_migrates_on_unique_overlap():
    rs = _rs6()
    items = blocking_items(rs)
    target = next(b for b in items if b["rule"].startswith("account:"))
    old_key = _key("account", target["text"])
    new, warnings = migrate_confirmations(
        {old_key: {"text": target["text"], "by": "jesus", "type": "confirmation"}},
        items,
    )
    assert warnings == []
    assert new[target["key"]]["migrated_from"] == old_key
    assert new[target["key"]]["by"] == "jesus"


def test_ambiguous_overlap_is_not_migrated():
    text = "post the logo on every clip and keep it visible"
    blockers = [
        {"key": "human:a:1", "text": text},
        {"key": "human:a:2", "text": text + " please"},
    ]
    new, warnings = migrate_confirmations({"old:abc": {"text": text, "by": "jesus"}}, blockers)
    assert "human:a:1" not in new and "human:a:2" not in new
    assert warnings
