"""Tests for CTI ingest temporal invariants (application/cti/ingest_service.py).

Covers first_available_date anchoring, mutable-label snapshotting, withhold flagging,
and the active-item slice — the contamination-control layer of the benchmark.
"""

from datetime import date

from glokta.application.cti.ingest_service import ingest_items, upsert_item
from glokta.infrastructure.cti.connectors.base import NormalisedCtiItem
from glokta.infrastructure.db.orm import CtiItem
from glokta.infrastructure.db.repos import CtiItemRepository


def _item(label, *, input_date=date(2024, 5, 1), label_date=date(2024, 5, 15),
          external_id="CVE-2024-1", authority="single"):
    return NormalisedCtiItem(
        task="rcm",
        external_id=external_id,
        source="cve",
        input_text="desc",
        label=label,
        label_provenance={"cna": label.get("cwe", [])},
        source_revision="rev1",
        input_date=input_date,
        label_date=label_date,
        authority_agreement=authority,
    )


class TestUpsertItem:
    def test_anchors_first_available_on_max_of_input_and_label(self, db_session):
        outcome = upsert_item(db_session, _item({"cwe": ["CWE-79"]}))
        assert outcome.action == "created"
        assert outcome.item.first_available_date == date(2024, 5, 15)

    def test_withhold_set_for_recent_items_within_window(self, db_session):
        outcome = upsert_item(
            db_session,
            _item({"cwe": ["CWE-79"]}, label_date=date(2024, 6, 10)),
            now=date(2024, 6, 14),
            withhold_window_days=14,
        )
        assert outcome.item.withhold is True

    def test_withhold_not_set_for_old_items(self, db_session):
        outcome = upsert_item(
            db_session,
            _item({"cwe": ["CWE-79"]}),
            now=date(2024, 6, 14),
            withhold_window_days=14,
        )
        assert outcome.item.withhold is False

    def test_reingesting_same_label_is_unchanged(self, db_session):
        upsert_item(db_session, _item({"cwe": ["CWE-79"]}))
        outcome = upsert_item(db_session, _item({"cwe": ["CWE-79"]}))
        assert outcome.action == "unchanged"
        assert db_session.query(CtiItem).count() == 1

    def test_changed_label_snapshots_old_and_activates_new(self, db_session):
        first = upsert_item(db_session, _item({"cwe": ["CWE-79"]}))
        second = upsert_item(db_session, _item({"cwe": ["CWE-89"]}))
        assert second.action == "snapshotted"
        db_session.refresh(first.item)
        assert first.item.status == "superseded"
        active = CtiItemRepository(db_session).active_for("rcm", "CVE-2024-1")
        assert active.label == {"cwe": ["CWE-89"]}
        assert db_session.query(CtiItem).count() == 2

    def test_unchanged_reingest_releases_item_from_holdout_window(self, db_session):
        # Ingested as withheld (recent label, inside the window)...
        first = upsert_item(
            db_session,
            _item({"cwe": ["CWE-79"]}, label_date=date(2024, 6, 10)),
            now=date(2024, 6, 14),
            withhold_window_days=14,
        )
        assert first.item.withhold is True

        # ...re-ingested unchanged after the window has elapsed -> must be released.
        second = upsert_item(
            db_session,
            _item({"cwe": ["CWE-79"]}, label_date=date(2024, 6, 10)),
            now=date(2024, 8, 1),
            withhold_window_days=14,
        )
        assert second.action == "unchanged"
        assert second.item.withhold is False
        # and it now appears in the public slice
        sliced = CtiItemRepository(db_session).slice_for_task("rcm")
        assert any(i.external_id == "CVE-2024-1" for i in sliced)

    def test_authority_disagreement_preserved(self, db_session):
        outcome = upsert_item(
            db_session, _item({"cwe": ["CWE-79"]}, authority="disagree")
        )
        assert outcome.item.authority_agreement == "disagree"


class TestIngestItems:
    def test_summary_counts(self, db_session):
        items = [
            _item({"cwe": ["CWE-79"]}, external_id="CVE-1"),
            _item({"cwe": ["CWE-89"]}, external_id="CVE-2"),
        ]
        summary = ingest_items(db_session, items)
        assert summary["created"] == 2
        assert summary["snapshotted"] == 0
        assert summary["unchanged"] == 0


class TestSliceForTask:
    def test_excludes_superseded_and_withheld(self, db_session):
        upsert_item(db_session, _item({"cwe": ["CWE-79"]}, external_id="CVE-1"))
        upsert_item(db_session, _item({"cwe": ["CWE-89"]}, external_id="CVE-1"))  # supersede
        upsert_item(
            db_session,
            _item({"cwe": ["CWE-22"]}, external_id="CVE-2", label_date=date(2024, 6, 10)),
            now=date(2024, 6, 14),
            withhold_window_days=14,
        )  # withheld

        repo = CtiItemRepository(db_session)
        sliced = repo.slice_for_task("rcm", exclude_withheld=True)
        ext_ids = {i.external_id for i in sliced}
        assert ext_ids == {"CVE-1"}  # only the active, non-withheld CVE-1
        # and it is the superseding (CWE-89) row
        assert sliced[0].label == {"cwe": ["CWE-89"]}
