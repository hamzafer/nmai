"""Train YOLOv8-L multiclass detector (356 classes).

Best config (scored 0.898 on competition):
    python train.py --data dataset_full/dataset.yaml --model yolov8l.pt --device 0 --imgsz 1536 --batch 12

Steps:
    1. Prepare dataset:    python prepare_dataset.py --annotations train/annotations.json --images train/images --output dataset --val-split 0.1
    2. Train with split:   python train.py --data dataset/dataset.yaml --device 0 --imgsz 1536 --batch 12
    3. Note best epoch N
    4. Prepare full data:  python prepare_dataset.py --annotations train/annotations.json --images train/images --output dataset_full --val-split 0.0
    5. Retrain on all:     python train.py --data dataset_full/dataset.yaml --device 0 --imgsz 1536 --batch 12 --epochs N --patience 0 --name v5_full
"""
import argparse
from pathlib import Path
import torch

_orig = torch.load
def _p(*a, **kw):
    kw.setdefault("weights_only", False)
    return _orig(*a, **kw)
torch.load = _p

from ultralytics import YOLO


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--model", default="yolov8l.pt")
    p.add_argument("--epochs", type=int, default=300)
    p.add_argument("--imgsz", type=int, default=1536)
    p.add_argument("--batch", type=int, default=-1)
    p.add_argument("--device", default="0")
    p.add_argument("--patience", type=int, default=80)
    p.add_argument("--lr0", type=float, default=0.0005)
    p.add_argument("--project", default="runs/detect")
    p.add_argument("--name", default="v5")
    a = p.parse_args()

    model = YOLO(a.model)
    model.train(
        data=a.data,
        epochs=a.epochs,
        patience=a.patience,
        batch=a.batch,
        imgsz=a.imgsz,
        cache=True,

        optimizer="AdamW",
        lr0=a.lr0,
        lrf=0.01,
        cos_lr=True,
        warmup_epochs=10.0,
        weight_decay=0.001,

        box=7.5,
        cls=0.5,
        dfl=1.5,

        mosaic=1.0,
        mixup=0.1,
        close_mosaic=25,
        copy_paste=0.1,
        fliplr=0.5,
        flipud=0.0,
        degrees=0.0,
        translate=0.1,
        scale=0.5,
        erasing=0.4,
        hsv_h=0.015,
        hsv_s=0.7,
        hsv_v=0.4,

        label_smoothing=0.1,
        dropout=0.1,
        amp=True,
        nbs=64,

        project=a.project,
        name=a.name,
        exist_ok=True,
        plots=True,
        save=True,
        device=a.device,
    )

    best = Path(a.project) / a.name / "weights" / "best.pt"
    print(f"\nBest: {best} ({best.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
