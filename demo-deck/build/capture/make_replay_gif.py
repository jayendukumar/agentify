"""Assembles the replay frames (frames/f00..f17.png) into ../screenshots/run-replay.gif."""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
CROP = (320, 105, 1265, 690)  # the run replay panel in a 1600x900 capture at 0.8 zoom

frames = [Image.open(f).convert("RGB").crop(CROP) for f in sorted((HERE / "frames").glob("f*.png"))]
out = HERE.parent / "screenshots" / "run-replay.gif"
frames[0].save(out, save_all=True, append_images=frames[1:], loop=0,
               duration=[1100] * (len(frames) - 1) + [3500], optimize=True)
print(out, len(frames), "frames")
