import json

import pytest
from pydantic import ValidationError

from app.api import model


def test_results_view_full():
    rec = {
        "presets": json.dumps(["720p", "480p"]),
        "source_meta": json.dumps({"duration": 60.0}),
    }

    result = model.results_view("jX", rec)

    assert result["playlist"] == "/jobs/jX/playlist"
    assert result["presets"] == ["720p", "480p"]
    assert result["duration"] == 60.0


def test_results_view_missing_fields_degrades():
    result = model.results_view("jX", {})

    assert result["presets"] == []
    assert result["duration"] is None
    assert result["playlist"] == "/jobs/jX/playlist"


def test_results_view_source_meta_without_duration():
    result = model.results_view(
        "jX",
        {"source_meta": json.dumps({"width": 1280})},
    )

    assert result["duration"] is None


@pytest.mark.parametrize("bad", ["{truncated", "not json", "null"])
def test_results_view_malformed_json_degrades(bad):
    result = model.results_view(
        "jX",
        {"presets": bad, "source_meta": bad},
    )

    assert result["presets"] == []
    assert result["duration"] is None
    assert result["web_mp4"] == "/jobs/jX/file"


def test_results_view_rejects_wrong_json_shapes():
    result = model.results_view(
        "jX",
        {
            "presets": json.dumps({"720p": True}),
            "source_meta": json.dumps([]),
            "subtitles": json.dumps({}),
        },
    )

    assert result["presets"] == []
    assert result["duration"] is None
    assert result["subtitles"] is None


def test_progress_map_extracts_only_valid_progress():
    rec = {
        "status": "transcoding",
        "progress:720p": "100.0",
        "progress:480p": "0.0",
        "progress:bad": "not-a-number",
        "progress:nan": "nan",
        "progress:over": "100.1",
        "progress:negative": "-0.1",
        "progress:": "42.0",
        "source_path": "/x",
    }

    assert model.progress_map(rec, "jX") == {"720p": 100.0, "480p": 0.0}


@pytest.mark.parametrize(
    ("code", "retryable"),
    [("ENCODE_TIMEOUT", True), ("SOURCE_NO_VIDEO", False)],
)
def test_error_view_uses_domain_retryability(code, retryable):
    result = model.error_view(
        {
            "error_code": code,
            "error_message": "failed",
            "error_stage": "transcode",
        }
    )

    assert result == {
        "code": code,
        "message": "failed",
        "stage": "transcode",
        "retryable": retryable,
    }


def test_error_view_supplies_safe_fallbacks():
    assert model.error_view({}) == {
        "code": "UNKNOWN",
        "message": "job failed",
        "stage": None,
        "retryable": True,
    }


def test_job_response_rejects_unknown_status():
    with pytest.raises(ValidationError):
        model.JobResponse(job_id="jX", status="mystery")


def test_job_response_schema_describes_nested_payloads():
    schema = model.JobResponse.model_json_schema()

    assert schema["properties"]["source"]["anyOf"][0]["$ref"] == "#/$defs/SourceMeta"
    assert schema["properties"]["results"]["anyOf"][0]["$ref"] == "#/$defs/JobResults"
    assert schema["properties"]["error"]["anyOf"][0]["$ref"] == "#/$defs/JobError"
    assert schema["properties"]["progress"]["anyOf"][0]["additionalProperties"] == {
        "type": "number"
    }
