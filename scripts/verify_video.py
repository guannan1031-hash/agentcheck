"""验证 demo.mp4：抽 5 帧保存 PNG。"""
import imageio.v2 as iio
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "deliverables"
vid = iio.get_reader(str(OUT / "demo.mp4"))
n = vid.count_frames()
print("总帧数:", n, "时长:", n / 25, "s")
for pct in (0.06, 0.20, 0.45, 0.65, 0.95):
    idx = int(n * pct)
    frame = vid.get_data(idx)
    name = f"verify-{int(pct*100):02d}.png"
    iio.imwrite(OUT / "shots" / name, frame)
    print("已抽帧:", name)
vid.close()
