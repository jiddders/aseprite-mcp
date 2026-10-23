"""Native JSON reports preserve layer names that need escaping."""

import json

from conftest import BASE, ok, run

from aseprite_mcp.tools import canvas, drawing, quality


LAYER = 'Motion "quote" \\ path\nline\tcell'
MISSING = 'Missing "quote" \\ path\nline\tcell'
INACTIVE = 'Dormant "quote" \\ path\nline\tcell'


def test_validate_scene_round_trips_missing_names_and_keeps_empty_arrays():
    path = f"{BASE}/quality_validate.aseprite"
    ok(run(canvas.create_canvas(8, 8, path)))
    ok(run(canvas.add_layer(path, LAYER)))

    report = json.loads(ok(run(quality.validate_scene(path, [LAYER, MISSING]))))
    assert report["missing_layers"] == [MISSING]
    assert report["missing_cels"] == [{"layer": LAYER, "frame": 1}]

    complete = json.loads(ok(run(quality.validate_scene(path, ["Layer 1"]))))
    assert complete["missing_layers"] == []
    assert complete["missing_cels"] == []


def test_audit_animation_round_trips_names_in_cels_overlaps_and_ranges():
    path = f"{BASE}/quality_audit.aseprite"
    ok(run(canvas.create_canvas(8, 8, path)))
    ok(run(canvas.add_layer(path, LAYER)))
    for name in ("Layer 1", LAYER):
        ok(run(drawing.draw_pixels_at(
            path, name, 1, [{"x": 1, "y": 1, "color": "#336699"}]
        )))

    for report_bounds in (False, True):
        report = json.loads(ok(run(quality.audit_animation(
            path,
            layer_names=[LAYER, "Layer 1"],
            overlap_pairs=[f"{LAYER},Layer 1"],
            layer_frame_ranges=[f"{LAYER}:2-2"],
            report_cels=True,
            report_bounds=report_bounds,
        ))))
        assert report["overlaps"][0]["a"] == LAYER
        assert report["overlaps"][0]["b"] == "Layer 1"
        assert report["out_of_range"] == [{"frame": 1, "layer": LAYER}]
        cel = next(entry for entry in report["cels"][0]["cels"] if entry["layer"] == LAYER)
        if report_bounds:
            assert all(key in cel for key in ("x", "y", "w", "h"))
        else:
            assert cel == {"layer": LAYER}


def test_animation_sanitize_round_trips_names_in_report_fields():
    path = f"{BASE}/quality_sanitize.aseprite"
    ok(run(canvas.create_canvas(8, 8, path)))
    ok(run(canvas.add_layer(path, LAYER)))
    ok(run(canvas.add_layer(path, INACTIVE)))
    for name in ("Layer 1", LAYER):
        ok(run(drawing.draw_pixels_at(
            path, name, 1, [{"x": 1, "y": 1, "color": "#336699"}]
        )))

    for report_bounds in (False, True):
        report = json.loads(ok(run(quality.animation_sanitize(
            path,
            layer_names=[LAYER, INACTIVE, "Layer 1"],
            overlap_pairs=[f"{LAYER},Layer 1"],
            ignore_full_canvas_overlaps=False,
            report_bounds=report_bounds,
            report_only=True,
        ))))
        assert report["analysis"]["inactive_layers"] == [INACTIVE]
        assert LAYER in report["layer_stats"]
        assert INACTIVE in report["layer_stats"]
        assert f"full_canvas_cels:{LAYER}" in report["alerts"]
        assert report["overlap_samples"][0]["a"] == LAYER
        assert report["overlap_samples"][0]["b"] == "Layer 1"
