"""Deterministic categorical colours checked in sRGB and CIE Lab (D65)."""

import math
from functools import lru_cache


def rgb(colour):
    return tuple(int(colour[i:i+2], 16)/255 for i in (1, 3, 5))


def linear(value):
    return value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4


def luminance(colour):
    return sum(v*w for v, w in zip(map(linear, rgb(colour)), (.2126, .7152, .0722)))


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high+.05)/(low+.05)


def lab(colour):
    r, g, b = map(linear, rgb(colour))
    xyz = ((.4124564*r+.3575761*g+.1804375*b)/.95047,
           .2126729*r+.7151522*g+.0721750*b,
           (.0193339*r+.1191920*g+.9503041*b)/1.08883)
    x, y, z = (v**(1/3) if v > (6/29)**3 else v/(3*(6/29)**2)+4/29 for v in xyz)
    return 116*y-16, 500*(x-y), 200*(y-z)


def distance(first, second):
    return math.dist(lab(first), lab(second))


@lru_cache(maxsize=1)
def candidates():
    result = []
    for r in range(16, 241, 32):
        for g in range(16, 241, 32):
            for b in range(16, 241, 32):
                colour = f"#{r:02x}{g:02x}{b:02x}"
                if contrast(colour, "#ffffff") >= 3:
                    result.append((colour, lab(colour), luminance(colour)))
    return result


def neighbours(model):
    order = model["done_indices"] + [i for i in reversed(range(len(model["keys"]))) if i not in model["done_indices"]]
    pairs = set()
    for point in model["points"]:
        values = [v-(model["points"][0]["counts"][i] if i in model["done_indices"] else 0)
                  for i, v in enumerate(point["counts"])]
        for sign in (1, -1):
            visible = [i for i in order if values[i]*sign > 0]
            pairs.update(tuple(sorted(pair)) for pair in zip(visible, visible[1:]))
    return pairs


def checked_palette(model):
    """Check every pair, plus every actual adjacent pair across the complete CFD."""
    size, edges = len(model["keys"]), neighbours(model)
    links = {i: {b if a == i else a for a, b in edges if i in (a, b)} for i in range(size)}
    fixed = {model["done_indices"][0]: "#16833b"} if model["done_indices"] else {}
    if "__outside__" in model["keys"]:
        fixed[model["keys"].index("__outside__")] = "#404850"
    order = sorted(range(size), key=lambda i: (-len(links[i]), model["keys"][i]))
    thresholds = {"min_delta_e76": 20, "min_adjacent_delta_e76": 30,
                  "min_white_boundary_contrast": 3, "min_text_contrast": 4.5}
    for attempt in range(12):
        chosen = {i: (c, lab(c), luminance(c)) for i, c in fixed.items()}
        for i in order:
            if i in chosen:
                continue
            best, best_score = None, -1
            for colour, coordinates, light in candidates():
                distances = [math.dist(coordinates, item[1]) for item in chosen.values()]
                if distances and min(distances) < thresholds["min_delta_e76"]:
                    continue
                adjacent = [chosen[j] for j in links[i] if j in chosen]
                if any(math.dist(coordinates, item[1]) < thresholds["min_adjacent_delta_e76"] for item in adjacent):
                    continue
                score = min(distances, default=100) + ((light*997+attempt*.618)%1)*attempt*3
                if score > best_score:
                    best, best_score = (colour, coordinates, light), score
            if best is None:
                break
            chosen[i] = best
        if len(chosen) == size:
            break
    if len(chosen) != size:
        raise ValueError("Cannot assign sufficiently distinct chart colours")
    colours = [chosen[i][0] for i in range(size)]
    all_pairs = [(a, b) for a in range(size) for b in range(a+1, size)]
    measured = {"min_delta_e76": min((distance(colours[a], colours[b]) for a, b in all_pairs), default=None),
                "min_adjacent_delta_e76": min((distance(colours[a], colours[b]) for a, b in edges), default=None),
                "min_adjacent_contrast": min((contrast(colours[a], colours[b]) for a, b in edges), default=None),
                "min_white_boundary_contrast": min((contrast(c, "#ffffff") for c in colours), default=None)}
    foregrounds = [max(("#ffffff", "#000000"), key=lambda fg: contrast(c, fg)) for c in colours]
    measured["min_text_contrast"] = min((contrast(c, fg) for c, fg in zip(colours, foregrounds)), default=None)
    for metric in thresholds:
        actual = measured[metric]
        if actual is not None and actual+1e-9 < thresholds[metric]:
            raise ValueError("Chart colour validation failed: " + metric)
    return {"colours": dict(zip(model["keys"], colours)), "foregrounds": dict(zip(model["keys"], foregrounds)),
            "thresholds": thresholds, "measured": measured, "adjacent_pairs": len(edges)}
