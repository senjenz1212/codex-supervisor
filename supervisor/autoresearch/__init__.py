"""Report-only AutoResearch domain helpers for supervisor experiments."""
from .orchestrator import run_autoresearch_fixture
from .report import build_autoresearch_report
from .generator import (
    AutoResearchGeneratorConfig,
    activate_autoresearch_experiment,
    generate_autoresearch_experiment_drafts,
    park_autoresearch_experiment,
    run_runnable_autoresearch_experiments,
)
from .schema import (
    AUTORESEARCH_REPORT_SCHEMA_VERSION,
    AUTORESEARCH_SCHEMA_VERSION,
    AutoresearchAttempt,
    AutoresearchExperiment,
    AutoresearchValidationReport,
)
from .validation import validate_attempt

__all__ = [
    "AUTORESEARCH_REPORT_SCHEMA_VERSION",
    "AUTORESEARCH_SCHEMA_VERSION",
    "AutoresearchAttempt",
    "AutoresearchExperiment",
    "AutoresearchValidationReport",
    "AutoResearchGeneratorConfig",
    "activate_autoresearch_experiment",
    "build_autoresearch_report",
    "generate_autoresearch_experiment_drafts",
    "park_autoresearch_experiment",
    "run_autoresearch_fixture",
    "run_runnable_autoresearch_experiments",
    "validate_attempt",
]
