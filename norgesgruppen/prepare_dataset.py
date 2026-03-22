"""Prepare 356-class YOLO dataset from COCO annotations.

Usage:
    python prepare_dataset.py --annotations train/annotations.json --images train/images --output dataset --val-split 0.1
    python prepare_dataset.py --annotations train/annotations.json --images train/images --output dataset_full --val-split 0.0
"""
import argparse
import json
from pathlib import Path
import shutil
import random


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--annotations", required=True)
    p.add_argument("--images", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--val-split", type=float, default=0.1)
    a = p.parse_args()

    ann_path, images_dir, output_dir = Path(a.annotations), Path(a.images), Path(a.output)

    with open(ann_path) as f:
        coco = json.load(f)

    images = {img["id"]: img for img in coco["images"]}
    nc = max(c["id"] for c in coco["categories"]) + 1

    anns_by_image = {}
    for ann in coco["annotations"]:
        anns_by_image.setdefault(ann["image_id"], []).append(ann)

    image_ids = sorted(images.keys())
    random.seed(42)
    random.shuffle(image_ids)

    if a.val_split > 0:
        n_val = max(1, int(len(image_ids) * a.val_split))
        val_ids = set(image_ids[:n_val])
        train_ids = set(image_ids[n_val:])
    else:
        train_ids = set(image_ids)
        val_ids = {image_ids[0]}

    print(f"Images: {len(image_ids)} | Train: {len(train_ids)} | Val: {len(val_ids)} | Classes: {nc}")

    for split, ids in [("train", train_ids), ("val", val_ids)]:
        img_out = output_dir / split / "images"
        lbl_out = output_dir / split / "labels"
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)

        n_anns = 0
        for img_id in sorted(ids):
            img_info = images[img_id]
            w, h = img_info["width"], img_info["height"]
            fname = img_info["file_name"]

            src = images_dir / fname
            if src.exists():
                shutil.copy2(src, img_out / fname)

            labels = []
            for ann in anns_by_image.get(img_id, []):
                cat_id = ann["category_id"]
                bx, by, bw, bh = ann["bbox"]
                cx = max(0, min(1, (bx + bw / 2) / w))
                cy = max(0, min(1, (by + bh / 2) / h))
                nw = max(0, min(1, bw / w))
                nh = max(0, min(1, bh / h))
                if nw > 0.001 and nh > 0.001:
                    labels.append(f"{cat_id} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
                    n_anns += 1

            with open(lbl_out / f"{Path(fname).stem}.txt", "w") as f:
                f.write("\n".join(labels))

        print(f"  {split}: {len(ids)} images, {n_anns} annotations")

    names_str = ", ".join(f"{i}: p{i}" for i in range(nc))
    yaml_content = f"""path: {output_dir.resolve()}
train: train/images
val: val/images

nc: {nc}
names: {{{names_str}}}
"""
    (output_dir / "dataset.yaml").write_text(yaml_content)
    print(f"YAML: {output_dir / 'dataset.yaml'}")


if __name__ == "__main__":
    main()
