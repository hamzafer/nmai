"""NorgesGruppen inference: multiclass YOLOv8-L with TTA.

Submission format:
    submission.zip/
    ├── run.py
    └── weights/
        └── best.pt

Usage (sandbox): python run.py --input /data/images --output /output/predictions.json
Scoring: 0.7 × detection_mAP@0.5 + 0.3 × classification_mAP@0.5
Best score: 0.898 (detection: 0.97, classification: 0.73)
"""
import argparse
import json
from pathlib import Path

import torch
import torch.serialization

_orig = torch.serialization.load
def _p(*a, **kw):
    kw["weights_only"] = False
    return _orig(*a, **kw)
torch.load = _p
torch.serialization.load = _p

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    model = YOLO(str(Path(__file__).parent / "weights" / "best.pt"))
    predictions = []

    for img_path in sorted(Path(args.input).iterdir()):
        if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        image_id = int(img_path.stem.split("_")[-1])

        results = model(
            str(img_path),
            half=True,
            conf=0.001,
            iou=0.7,
            max_det=500,
            verbose=False,
            imgsz=1536,
            augment=True,
        )

        for r in results:
            if r.boxes is None:
                continue
            for i in range(len(r.boxes)):
                x1, y1, x2, y2 = r.boxes.xyxy[i].tolist()
                predictions.append({
                    "image_id": image_id,
                    "category_id": int(r.boxes.cls[i].item()),
                    "bbox": [
                        round(x1, 1),
                        round(y1, 1),
                        round(x2 - x1, 1),
                        round(y2 - y1, 1),
                    ],
                    "score": round(float(r.boxes.conf[i].item()), 4),
                })

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(predictions, f)

    print(f"Wrote {len(predictions)} predictions")


if __name__ == "__main__":
    main()
