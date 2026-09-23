#!/usr/bin/env python3
"""
Render an animated chaotic attractor: the trajectory draws itself in over
time, then the camera keeps slowly rotating once it's fully drawn. Same
math and color logic as the static Lorenz/Rossler/Chua/Aizawa renderer in
post_to_instagram.py, just animated into an MP4 instead of a single frame.

This is a standalone local-preview tool -- it does NOT post to Instagram
and does NOT touch post_to_instagram.py's state files. Once you're happy
with the look, wiring an animated version into actual Reels posting is a
separate step (different Graph API media_type, 9:16 aspect ratio, longer
container-processing wait) -- deliberately not done here yet.

Usage:
    python generate_animated_attractor.py [system] [output.mp4]

    system        Optional: lorenz | rossler | chua | aizawa
                  If omitted, one is chosen at random.
    output.mp4    Optional output path (default: attractor_animation.mp4)

Requires ffmpeg on PATH for MP4 output. Falls back to an animated GIF
(same filename, .gif extension) if ffmpeg isn't found, since GIF works
with Pillow alone -- useful for a quick local preview even without ffmpeg,
but Instagram Reels need real video, so get ffmpeg installed before
treating this as final.
"""

import os
import sys
import shutil
import random

import numpy as np
from scipy.integrate import solve_ivp
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from mpl_toolkits.mplot3d.art3d import Line3DCollection


# --- Same chaotic systems as post_to_instagram.py ---

def _lorenz_system(sigma=10, rho=28, beta=8 / 3):
    def f(t, state):
        x, y, z = state
        return [sigma * (y - x), x * (rho - z) - y, x * y - beta * z]
    return f


def _rossler_system(a=0.2, b=0.2, c=5.7):
    def f(t, state):
        x, y, z = state
        return [-y - z, x + a * y, b + z * (x - c)]
    return f


def _chua_system(alpha=15.6, beta=28, m0=-1.143, m1=-0.714):
    def diode(x):
        return m1 * x + 0.5 * (m0 - m1) * (abs(x + 1) - abs(x - 1))

    def f(t, state):
        x, y, z = state
        return [alpha * (y - x - diode(x)), x - y + z, -beta * y]
    return f


def _aizawa_system(a=0.95, b=0.7, c=0.6, d=3.5, e=0.25, f=0.1):
    def fn(t, state):
        x, y, z = state
        dx = (z - b) * x - d * y
        dy = d * x + (z - b) * y
        dz = c + a * z - (z ** 3) / 3 - (x ** 2 + y ** 2) * (1 + e * z) + f * z * x ** 3
        return [dx, dy, dz]
    return fn


# Multiple color palettes (matplotlib colormap names), randomly chosen per
# render, so animations vary in color as well as system/camera angle.
COLOR_PALETTES = ['hot', 'cool', 'viridis', 'inferno', 'bone', 'copper']


SYSTEMS = {
    "lorenz": {
        "make": _lorenz_system,
        "base_state": (1.0, 1.0, 1.0),
        "perturbation": 0.5,
        "t_span": (0, 50),
        "num_points": 4000,
    },
    "rossler": {
        "make": _rossler_system,
        "base_state": (1.0, 1.0, 1.0),
        "perturbation": 0.5,
        "t_span": (0, 150),
        "num_points": 4000,
    },
    "chua": {
        "make": _chua_system,
        "base_state": (0.7, 0.0, 0.0),
        "perturbation": 0.05,
        "t_span": (0, 100),
        "num_points": 4000,
    },
    "aizawa": {
        "make": _aizawa_system,
        "base_state": (0.1, 0.0, 0.0),
        "perturbation": 0.05,
        "t_span": (0, 100),
        "num_points": 4000,
    },
}


def solve_trajectory(system_name: str):
    spec = SYSTEMS[system_name]
    fn = spec["make"]()
    initial = [spec["base_state"][i] + random.uniform(-1, 1) * spec["perturbation"] for i in range(3)]
    t_eval = np.linspace(*spec["t_span"], spec["num_points"])
    sol = solve_ivp(fn, spec["t_span"], initial, t_eval=t_eval)
    return sol.y  # shape (3, num_points): x, y, z arrays


def velocity_magnitudes(system_name: str, xyz):
    fn = SYSTEMS[system_name]["make"]()
    x, y, z = xyz
    vels = np.zeros(len(x))
    for i in range(len(x)):
        dx, dy, dz = fn(0, [x[i], y[i], z[i]])
        vels[i] = np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
    return vels


def make_animation(system_name: str, output_path: str, duration_seconds: int = 8, fps: int = 24):
    x, y, z = solve_trajectory(system_name)
    speed = velocity_magnitudes(system_name, (x, y, z))
    speed_norm = (speed - speed.min()) / (speed.max() - speed.min() + 1e-9)

    n_points = len(x)
    total_frames = duration_seconds * fps
    # First 70% of frames: trajectory grows in. Last 30%: fully drawn, camera keeps rotating.
    draw_frames = int(total_frames * 0.7)

    fig = plt.figure(figsize=(6, 10.67), facecolor="black")
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor("black")
    ax.set_axis_off()
    ax.grid(False)

    margin = 0.05 * (x.max() - x.min())
    ax.set_xlim(x.min() - margin, x.max() + margin)
    ax.set_ylim(y.min() - margin, y.max() + margin)
    ax.set_zlim(z.min() - margin, z.max() + margin)

    palette_name = random.choice(COLOR_PALETTES)
    cmap = plt.get_cmap(palette_name)
    line_collection = Line3DCollection([[(x[0], y[0], z[0]), (x[1], y[1], z[1])]], linewidths=1.1)
    ax.add_collection3d(line_collection)

    def update(frame):
        progress = min(frame / draw_frames, 1.0)
        n_visible = max(2, int(n_points * progress))

        segments = [
            [(x[i], y[i], z[i]), (x[i + 1], y[i + 1], z[i + 1])]
            for i in range(n_visible - 1)
        ]
        colors = cmap(speed_norm[:n_visible - 1])
        line_collection.set_segments(segments)
        line_collection.set_color(colors)

        azim = (frame / total_frames) * 120
        ax.view_init(elev=20, azim=azim)

        return (line_collection,)

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
    attribution = f"Generative -- {system_name.capitalize()} attractor animation ({palette_name} palette)"
    return output_path, attribution


def generate_animated_attractor(output_path: str) -> str:
    """
    Callable wrapper for post_to_instagram.py: picks a random system, renders
    an animated MP4 to output_path, and returns an attribution string.
    """
    system_name = random.choice(list(SYSTEMS.keys()))
    _, attribution = make_animation(system_name, output_path)
    return attribution


def main():
    valid_systems = list(SYSTEMS.keys())

    system_name = sys.argv[1] if len(sys.argv) >= 2 and sys.argv[1] in valid_systems else random.choice(valid_systems)
    output_path = sys.argv[2] if len(sys.argv) >= 3 else "attractor_animation.mp4"

    print(f"System: {system_name}")
    print(f"Solving trajectory...")
    print(f"Rendering animation (this takes a while -- each frame is a full 3D re-render)...")

    final_path, attribution = make_animation(system_name, output_path)

    print(f"Done: {final_path}")
    print(f"Attribution: {attribution}")


if __name__ == "__main__":
    main()