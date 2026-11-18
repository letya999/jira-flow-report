import pytest

from jira_flow.colours import checked_palette, contrast, distance, neighbours


def test_colour_reference_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21)
    assert contrast("#123456", "#123456") == 1
    assert distance("#123456", "#123456") == 0
    assert distance("#000000", "#ffffff") == pytest.approx(100, abs=.001)


def test_actual_neighbours_skip_zero_layers_and_shift_initial_done():
    model = {"keys": ["done", "a", "b", "__outside__"], "done_indices": [0],
             "points": [{"counts": [4, 1, 0, 2]}, {"counts": [5, 0, 1, 2]}]}
    assert neighbours(model) == {(1, 3), (0, 3), (2, 3)}


def test_thirty_six_colours_pass_all_pair_and_actual_adjacency_checks():
    model = {"keys": [str(i) for i in range(36)], "done_indices": [0],
             "points": [{"counts": [1]*36}, {"counts": [2]*36}]}
    result = checked_palette(model)
    assert len(set(result["colours"].values())) == 36
    assert result["colours"]["0"] == "#16833b"
    for name, threshold in result["thresholds"].items():
        assert result["measured"][name] >= threshold
    assert checked_palette(model) == result
    assert checked_palette({"keys": [], "done_indices": [], "points": [{"counts": []}]})["colours"] == {}
