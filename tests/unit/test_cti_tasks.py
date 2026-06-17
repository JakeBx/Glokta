"""Unit tests for the CTI task registry (domain/cti/tasks.py)."""

from glokta.domain.cti.tasks import CTI_TASKS, ENABLED_TASKS


class TestCtiTaskRegistry:
    def test_all_six_tasks_present(self):
        assert set(CTI_TASKS) == {"rcm", "vsp", "ate", "taa", "forecast", "syn"}

    def test_each_task_declares_required_metadata(self):
        for task in CTI_TASKS.values():
            assert isinstance(task["label"], str) and task["label"]
            assert task["scoring_type"] in {
                "set_f1",
                "vector_distance",
                "synonym_graph",
                "brier",
                "claim_set",
            }
            assert task["cadence"] in {"hourly", "daily", "weekly", "on_release"}
            assert task["leak_resistance"] in {"low", "moderate", "high"}
            assert isinstance(task["enabled"], bool)

    def test_forecast_is_marked_leak_proof(self):
        assert CTI_TASKS["forecast"]["leak_resistance"] == "high"

    def test_enabled_tasks_is_derived_from_registry(self):
        assert ENABLED_TASKS == [k for k, v in CTI_TASKS.items() if v["enabled"]]
