#!/usr/bin/env python3
"""
Render an animated Julia set fractal, replicating the exact color recipe
from the two approved interactive demos: non-linear per-channel RGB curves
(not a standard matplotlib colormap), always on black, with the complex
parameter c moving along a randomized circle or figure-eight path.

Standalone, mirrors generate_animated_attractor.py's structure and is
called the same way from post_to_instagram.py's post_reel(), which picks
randomly between this and the attractor generator each time.

Usage:
    python generate_animated_fractal.py [output.mp4]
"""

import os
import sys
import shutil
import random

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation


def _palette_orange_red(t, inside_mask):
    """t in [0,1]: escape-time fraction. Black where inside_mask is True."""
    r = np.clip(255 * np.minimum(1, t * 2.2), 0, 255)
    g = np.clip(140 * t * t, 0, 255)
    b = np.clip(40 * (1 - t), 0, 255)
    r = np.where(inside_mask, 0, r)
    g = np.where(inside_mask, 0, g)
    b = np.where(inside_mask, 0, b)
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def _palette_blue_teal(t, inside_mask):
    r = np.clip(20 * (1 - t), 0, 255)
    g = np.clip(120 * t, 0, 255)
    b = np.clip(255 * np.minimum(1, t * 1.6), 0, 255)
    r = np.where(inside_mask, 0, r)
    g = np.where(inside_mask, 0, g)
    b = np.where(inside_mask, 0, b)
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def _palette_purple_magenta(t, inside_mask):
    r = np.clip(180 * t, 0, 255)
    g = np.clip(20 * (1 - t), 0, 255)
    b = np.clip(220 * np.minimum(1, t * 1.8), 0, 255)
    r = np.where(inside_mask, 0, r)
    g = np.where(inside_mask, 0, g)
    b = np.where(inside_mask, 0, b)
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def _palette_green_lime(t, inside_mask):
    r = np.clip(40 * (1 - t), 0, 255)
    g = np.clip(255 * np.minimum(1, t * 2.0), 0, 255)
    b = np.clip(30 * t * t, 0, 255)
    r = np.where(inside_mask, 0, r)
    g = np.where(inside_mask, 0, g)
    b = np.where(inside_mask, 0, b)
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


PALETTES = [_palette_orange_red, _palette_blue_teal, _palette_purple_magenta, _palette_green_lime]


def make_path_function(shape: str):
    """Randomized phase/radius/speed per render, so no two paths are identical."""
    phase = random.uniform(0, 2 * np.pi)
    speed = random.uniform(0.015, 0.025)

    if shape == "circle":
        r = random.uniform(0.6, 0.85)

        def path(t):
            theta = t * speed + phase
            return r * np.cos(theta), r * np.sin(theta + 1.3)

        return path

    if shape == "figure eight":
        a = random.uniform(0.3, 0.5)
        b = random.uniform(0.45, 0.65)

        def path(t):
            theta = t * speed + phase
            return a * np.sin(theta), b * np.sin(theta * 2.0)

        return path

    if shape == "spiral":
        r_min = random.uniform(0.2, 0.35)
        r_span = random.uniform(0.3, 0.45)

        def path(t):
            theta = t * speed + phase
            r = r_min + r_span * (0.5 + 0.5 * np.sin(theta * 0.3))
            return r * np.cos(theta * 1.5), r * np.sin(theta * 1.5)

        return path

    raise ValueError(f"Unknown path shape: {shape}")


PATH_SHAPES = ["circle", "figure eight", "spiral"]


def julia_frame_square(c_re: float, c_im: float, size: int, palette_fn, max_iter: int = 90, scale: float = 2.6):
    """
    Vectorized escape-time computation for a SQUARE frame, matching the
    exact resolution/scale of the approved demo (320x320) -- not stretched
    to any other aspect ratio, since that changes which part of the
    fractal is visible, not just its proportions.
    """
    x = np.linspace(-scale / 2, scale / 2, size)
    y = np.linspace(-scale / 2, scale / 2, size)
    X, Y = np.meshgrid(x, y)
    Z = X + 1j * Y
    c = c_re + 1j * c_im

    iters = np.full(Z.shape, max_iter, dtype=float)
    mask = np.ones(Z.shape, dtype=bool)

    for i in range(max_iter):
        Z[mask] = Z[mask] ** 2 + c
        escaped = np.abs(Z) > 2
        newly_escaped = escaped & mask
        iters[newly_escaped] = i
        mask &= ~escaped

    inside_mask = mask  # never escaped -> stays black, same as the JS demos
    t = iters / max_iter
    return palette_fn(t, inside_mask)


def letterbox_to_vertical(square_frame, out_width: int, out_height: int):
    """Center a square RGB frame on a black out_width x out_height canvas."""
    canvas = np.zeros((out_height, out_width, 3), dtype=np.uint8)
    size = square_frame.shape[0]
    y_offset = (out_height - size) // 2
    x_offset = (out_width - size) // 2
    canvas[y_offset:y_offset + size, x_offset:x_offset + size] = square_frame
    return canvas


def make_fractal_animation(output_path: str, duration_seconds: int = 15, fps: int = 24, square_size: int = 320, out_width: int = 320, out_height: int = 569):
    path_shape = random.choice(PATH_SHAPES)
    path_fn = make_path_function(path_shape)
    palette_fn = random.choice(PALETTES)
    _PALETTE_NAMES = {
    _palette_orange_red: "orange-red",
    _palette_blue_teal: "blue-teal",
    _palette_purple_magenta: "purple-magenta",
    _palette_green_lime: "green-lime",
}
    palette_name = _PALETTE_NAMES[palette_fn]

    total_frames = duration_seconds * fps
    max_iter = 90

    fig = plt.figure(figsize=(6, 10.67), facecolor="black")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")

    first_c_re, first_c_im = path_fn(0)
    first_square = julia_frame_square(first_c_re, first_c_im, square_size, palette_fn, max_iter=max_iter)
    first_frame = letterbox_to_vertical(first_square, out_width, out_height)
    im = ax.imshow(first_frame, interpolation="nearest")

    def update(frame):
        c_re, c_im = path_fn(frame)
        square = julia_frame_square(c_re, c_im, square_size, palette_fn, max_iter=max_iter)
        data = letterbox_to_vertical(square, out_width, out_height)
        im.set_data(data)
        return (im,)

    anim = animation.FuncAnimation(fig, update, frames=total_frames, blit=False)

    has_ffmpeg = shutil.which("ffmpeg") is not None
    if has_ffmpeg and output_path.lower().endswith(".mp4"):
        writer = animation.FFMpegWriter(fps=fps, bitrate=4000)
        anim.save(output_path, writer=writer, dpi=150)
    else:
        gif_path = os.path.splitext(output_path)[0] + ".gif"
        print(f"ffmpeg not found -- saving as animated GIF instead: {gif_path}", file=sys.stderr)
        writer = animation.PillowWriter(fps=fps)
        anim.save(gif_path, writer=writer, dpi=100)
        output_path = gif_path

    plt.close(fig)

    attribution = (
        f"Generative -- Julia set fractal animation ({path_shape} path, {palette_name} palette)\n"
        f"c(t) traces a {path_shape} through the complex plane"
    )
    return output_path, attribution


def generate_animated_fractal(output_path: str) -> str:
    _, attribution = make_fractal_animation(output_path)
    return attribution


def main():
    output_path = sys.argv[1] if len(sys.argv) >= 2 else "fractal_animation.mp4"
    print("Rendering animated fractal...")
    final_path, attribution = make_fractal_animation(output_path)
    print(f"Done: {final_path}")
    print(f"Attribution: {attribution}")


if __name__ == "__main__":
    main()