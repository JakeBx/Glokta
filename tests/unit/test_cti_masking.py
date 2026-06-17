"""Tests for SYN input masking (claim_extraction.mask_conclusions) + build_syn_item drop-residue."""

from datetime import date

from glokta.application.cti.syn_service import build_syn_item
from glokta.infrastructure.cti.claim_extraction import mask_conclusions


class TestMaskConclusions:
    def test_masks_technique_ids_including_subtechniques(self):
        out = mask_conclusions("The actors used T1059 and T1566.001 for access.")
        assert "T1059" not in out and "T1566.001" not in out
        assert out.count("[technique]") == 2

    def test_preserves_behavioural_prose(self):
        text = "The actors used scheduled tasks to maintain persistence and beaconed over HTTPS."
        assert mask_conclusions(text) == text  # no IDs/actors -> unchanged

    def test_masks_actor_and_aliases(self):
        alias = {"apt29": "APT29", "cozy bear": "APT29", "the dukes": "APT29"}
        out = mask_conclusions(
            "Activity attributed to Cozy Bear, also tracked as APT29 and The Dukes.",
            actor="APT29", alias_index=alias,
        )
        for name in ("Cozy Bear", "APT29", "The Dukes"):
            assert name not in out
        assert "[actor]" in out

    def test_does_not_mask_short_tokens(self):
        # a 2-char alias must not nuke unrelated text
        out = mask_conclusions("an attack on the US grid", actor="X", alias_index={"x": "X"})
        assert "US grid" in out


def _advisory(actor="APT29", tech_in_td=True):
    td = "The actors exploited CVE-2024-21887 and beaconed to 203.0.113.10."
    if tech_in_td:
        td += " They used T1059 for execution."
    return {
        "id": "AA-X", "published": date(2024, 3, 1), "actor": actor,
        "cves": ["CVE-2024-21887"], "techniques": ["T1059"],
        "text": "x", "sections": {"Technical Details": td, "Indicators of Compromise": "203.0.113.10"},
    }


class TestBuildSynItemMasking:
    ALIAS = {"apt29": "APT29"}

    def test_masking_removes_technique_leak_from_inputs(self):
        item = build_syn_item(_advisory(), mask=True, alias_index=self.ALIAS, min_input_chars=10)
        assert item is not None
        assert "T1059" not in item.input_text   # the leaked technique id is masked
        assert "203.0.113.10" in item.input_text  # observations preserved

    def test_unmasked_input_still_leaks(self):
        item = build_syn_item(_advisory(), mask=False)
        assert "T1059" in item.input_text  # no masking -> leak remains

    def test_drops_when_input_too_short_after_masking(self):
        # tiny advisory whose only content is the leaking id -> masked input below threshold
        adv = {"id": "AA-Y", "published": date(2024, 3, 1), "actor": None, "cves": [],
               "techniques": ["T1059"], "text": "x", "sections": {"Technical Details": "T1059"}}
        assert build_syn_item(adv, mask=True, min_input_chars=50) is None
