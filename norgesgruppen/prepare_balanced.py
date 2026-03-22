"""Create a balanced dataset by oversampling images with rare classes.

Usage:
    python prepare_balanced.py \\
        --annotations train/annotations.json \\
        --images train/images \\
        --input-dataset dataset_full \\
        --output dataset_balanced \\
        --target-per-class 50
"""
import argparse
import json
from pathlib import Path
import shutil
from collections import Counter, defaultdict


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--annotations", required=True)
    p.add_argument("--images", required=True)
    p.add_argument("--input-dataset", required=True, help="Existing YOLO dataset dir")
    p.add_argument("--output", required=True)
    p.add_argument("--target-per-class", type=int, default=50,
                   help="Target minimum annotations per class")
    a = p.parse_args()

    with open(a.annotations) as f:
        coco = json.load(f)

    class_counts = Counter(ann["category_id"] for ann in coco["annotations"])
    print(f"Classes: {len(class_counts)}")
    print(f"Min count: {min(class_counts.values())}, Max: {max(class_counts.values())}")
    print(f"Classes with <{a.target_per_class} samples: "
          f"{sum(1 for c in class_counts.values() if c < a.target_per_class)}")

    img_classes = defaultdict(set)
    for ann in coco["annotations"]:
        img_classes[ann["image_id"]].add(ann["category_id"])

    img_id_to_file = {}
    for img in coco["images"]:
        img_id_to_file[img["id"]] = Path(img["file_name"]).stem

    class_to_images = defaultdict(list)
    for img_id, classes in img_classes.items():
        for cls in classes:
            class_to_images[cls].append(img_id)

    img_copies = Counter()
    for cls_id, count in class_counts.items():
        if count >= a.target_per_class:
            continue
        multiplier = max(1, a.target_per_class // count)
        for img_id in class_to_images[cls_id]:
            img_copies[img_id] = max(img_copies[img_id], multiplier)

    print(f"Images to oversample: {len(img_copies)}")
    total_extra = sum(img_copies.values())
    print(f"Extra copies: {total_extra}")

    input_dir = Path(a.input_dataset)
    output_dir = Path(a.output)

    for split in ["train", "val"]:
        src_img = input_dir / split / "images"
        src_lbl = input_dir / split / "labels"
        dst_img = output_dir / split / "images"
        dst_lbl = output_dir / split / "labels"
        dst_img.mkdir(parents=True, exist_ok=True)
        dst_lbl.mkdir(parents=True, exist_ok=True)

        if src_img.exists():
            for f in src_img.iterdir():
                shutil.copy2(f, dst_img / f.name)
        if src_lbl.exists():
            for f in src_lbl.iterdir():
                shutil.copy2(f, dst_lbl / f.name)

        if split == "train":
            for img_id, copies in img_copies.items():
                stem = img_id_to_file.get(img_id)
                if stem is None:
                    continue

                orig_img = None
                for ext in [".jpg", ".jpeg", ".png"]:
                    candidate = src_img / f"{stem}{ext}"
                    if candidate.exists():
                        orig_img = candidate
                        break
                orig_lbl = src_lbl / f"{stem}.txt"

                if orig_img is None or not orig_lbl.exists():
                    continue

                for c in range(copies):
                    new_stem = f"{stem}_dup{c}"
                    shutil.copy2(orig_img, dst_img / f"{new_stem}{orig_img.suffix}")
                    shutil.copy2(orig_lbl, dst_lbl / f"{new_stem}.txt")

    yaml_src = input_dir / "dataset.yaml"
    yaml_content = yaml_src.read_text().replace(str(input_dir.resolve()), str(output_dir.resolve()))
    (output_dir / "dataset.yaml").write_text(yaml_content)

    train_imgs = len(list((output_dir / "train" / "images").iterdir()))
    print(f"Final training images: {train_imgs} (was {len(list((input_dir / 'train' / 'images').iterdir()))})")


if __name__ == "__main__":
    main()
