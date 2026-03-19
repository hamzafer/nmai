# NorgesGruppen Data — Object Detection

## Overview

- **Task type**: Offline model submission (zip upload)
- **Platform**: [app.ainm.no](https://app.ainm.no)
- **Goal**: Detect grocery products on store shelf images and identify the correct product category
- **Scoring**: `0.7 × detection_mAP@0.5 + 0.3 × classification_mAP@0.5`

## How It Works

1. Download training data from the competition website (login required)
2. Train your object detection model locally
3. Write a `run.py` that takes shelf images as input and outputs predictions
4. Zip your code + model weights
5. Upload at the submit page
6. Server runs your code in a GPU sandbox (NVIDIA L4, 24GB VRAM)
7. Predictions scored against private test set

## Training Data

### COCO Dataset (`NM_NGD_coco_dataset.zip`, ~864 MB)
- 248 shelf images from Norwegian grocery stores
- ~22,700 COCO-format bounding box annotations
- 356 product categories (category_id 0-355) + unknown (356)
- Sections: Egg, Frokost, Knekkebrod, Varmedrikker

### Product Reference Images (`NM_NGD_product_images.zip`, ~60 MB)
- 327 products with multi-angle photos (main, front, back, left, right, top, bottom)
- Organized by barcode: `{product_code}/main.jpg`, etc.
- Includes `metadata.json` with product names and annotation counts

### Annotation Format

```json
{
  "images": [{"id": 1, "file_name": "img_00001.jpg", "width": 2000, "height": 1500}],
  "categories": [
    {"id": 0, "name": "VESTLANDSLEFSA TØRRE 10STK 360G", "supercategory": "product"},
    {"id": 356, "name": "unknown_product", "supercategory": "product"}
  ],
  "annotations": [
    {
      "id": 1, "image_id": 1, "category_id": 42,
      "bbox": [141, 49, 169, 152],
      "area": 25688, "iscrowd": 0,
      "product_code": "8445291513365",
      "product_name": "NESCAFE VANILLA LATTE 136G NESTLE",
      "corrected": true
    }
  ]
}
```

`bbox` is `[x, y, width, height]` in pixels (COCO format).

## Scoring

### Hybrid Score

```
Score = 0.7 × detection_mAP + 0.3 × classification_mAP
```

Both use **mAP@0.5** (Mean Average Precision at IoU threshold 0.5).

- **Detection mAP (70%)**: Did you find the products? Category ignored, just IoU ≥ 0.5
- **Classification mAP (30%)**: Did you identify the right product? IoU ≥ 0.5 AND correct category_id

### Detection-Only Shortcut

Set `category_id: 0` for all predictions → score up to **0.70** (70%) from detection alone.

### Score range: 0.0 (worst) to 1.0 (perfect)

## Submission Format

### Zip Structure

```
submission.zip
├── run.py          # Required: entry point
├── model.onnx      # Optional: model weights
└── utils.py        # Optional: helper code
```

**Limits:**

| Limit | Value |
|---|---|
| Max zip size (uncompressed) | 420 MB |
| Max files | 1000 |
| Max Python files | 10 |
| Max weight files | 3 |
| Max weight size total | 420 MB |
| Allowed types | .py, .json, .yaml, .yml, .cfg, .pt, .pth, .onnx, .safetensors, .npy |

### run.py Contract

```bash
python run.py --input /data/images --output /output/predictions.json
```

**Input**: `/data/images/` contains JPEG shelf images (`img_XXXXX.jpg`)

**Output**: JSON array:

```json
[
  {
    "image_id": 42,
    "category_id": 0,
    "bbox": [120.5, 45.0, 80.0, 110.0],
    "score": 0.923
  }
]
```

| Field | Type | Description |
|---|---|---|
| `image_id` | int | From filename (`img_00042.jpg` → `42`) |
| `category_id` | int | Product category (0-355), or 0 for detection-only |
| `bbox` | [x, y, w, h] | Bounding box in COCO format |
| `score` | float | Confidence (0-1) |

## Sandbox Environment

| Resource | Limit |
|---|---|
| Python | 3.11 |
| CPU | 4 vCPU |
| Memory | 8 GB |
| GPU | NVIDIA L4 (24 GB VRAM) |
| CUDA | 12.4 |
| Network | None (fully offline) |
| Timeout | 300 seconds |

### Pre-installed Packages

PyTorch 2.6.0+cu124, torchvision 0.21.0+cu124, ultralytics 8.1.0, onnxruntime-gpu 1.20.0, opencv-python-headless 4.9.0.80, albumentations 1.3.1, Pillow 10.2.0, numpy 1.26.4, scipy 1.12.0, scikit-learn 1.4.0, pycocotools 2.0.7, ensemble-boxes 1.0.9, timm 0.9.12, supervision 0.18.0, safetensors 0.4.2.

**Cannot `pip install` at runtime.**

### Security Restrictions

**Blocked imports**: os, sys, subprocess, socket, ctypes, builtins, importlib, pickle, marshal, shelve, shutil, yaml, requests, urllib, http.client, multiprocessing, threading, signal, gc, code, codeop, pty

**Blocked calls**: eval(), exec(), compile(), \_\_import\_\_(), getattr() with dangerous names

Use `pathlib` instead of `os`. Use `json` instead of `yaml`.

## Model Options

### Pre-installed Frameworks (pin versions!)

| Framework | Models | Pin version |
|---|---|---|
| ultralytics 8.1.0 | YOLOv8n/s/m/l/x, YOLOv5u, RT-DETR | `ultralytics==8.1.0` |
| torchvision 0.21.0 | Faster R-CNN, RetinaNet, SSD, FCOS | `torchvision==0.21.0` |
| timm 0.9.12 | ResNet, EfficientNet, ViT, Swin, ConvNeXt (backbones) | `timm==0.9.12` |

### Not in sandbox (export to ONNX)

YOLOv9, YOLOv10, YOLO11, RF-DETR, Detectron2, MMDetection, HuggingFace Transformers

### Weight Format Options

| Approach | Format | When to use |
|---|---|---|
| ONNX export | `.onnx` | Universal — any framework |
| ultralytics .pt (pinned 8.1.0) | `.pt` | Simple YOLOv8/RT-DETR |
| state_dict + model class | `.pt` | Custom architectures |
| safetensors | `.safetensors` | Safe loading, fast |

## Common Errors

| Error | Fix |
|---|---|
| `run.py not found at zip root` | Zip **contents**, not the folder |
| `Disallowed file type: __MACOSX/...` | Use: `zip -r ../sub.zip . -x ".*" "__MACOSX/*"` |
| `Disallowed file type: .bin` | Rename `.bin` → `.pt` |
| `Security scan found violations` | Remove subprocess, socket, os imports. Use pathlib. |
| `Timed out after 300s` | Use GPU (`model.to("cuda")`), or smaller model |
| `Exit code 137` | OOM (8 GB limit). Reduce batch size or use FP16 |
| `Exit code 139` | Segfault — version mismatch. Re-export or use ONNX. |

## Submission Limits

| Limit | Value |
|---|---|
| Submissions in-flight | 2 per team |
| Submissions per day | 3 per team |
| Infrastructure failure freebies | 2 per day |

Limits reset at midnight UTC.

## Creating Your Zip

```bash
cd my_submission/
zip -r ../submission.zip . -x ".*" "__MACOSX/*"

# Verify
unzip -l submission.zip | head -10
# Should show run.py directly, NOT my_submission/run.py
```

## Quick Start: YOLOv8 Fine-Tuning

```bash
# Install matching version
pip install ultralytics==8.1.0

# Train on competition data
yolo detect train data=coco_dataset.yaml model=yolov8m.pt epochs=50 imgsz=640

# Best weights at runs/detect/train/weights/best.pt
# Include best.pt in your zip alongside run.py
```

### Recommended run.py (YOLOv8)

```python
import argparse
import json
from pathlib import Path
import torch
from ultralytics import YOLO

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = YOLO("best.pt")
    predictions = []

    for img in sorted(Path(args.input).iterdir()):
        if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        image_id = int(img.stem.split("_")[-1])
        results = model(str(img), device=device, verbose=False)
        for r in results:
            if r.boxes is None:
                continue
            for i in range(len(r.boxes)):
                x1, y1, x2, y2 = r.boxes.xyxy[i].tolist()
                predictions.append({
                    "image_id": image_id,
                    "category_id": int(r.boxes.cls[i].item()),
                    "bbox": [round(x1, 1), round(y1, 1),
                             round(x2 - x1, 1), round(y2 - y1, 1)],
                    "score": round(float(r.boxes.conf[i].item()), 3),
                })

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(predictions, f)

if __name__ == "__main__":
    main()
```
