# defect-detector-manufacturing

Visual defect detection on a manufacturing production line.

## What it does
Quality checks were manual and inconsistent between shifts. This is the code from when I trained an object detection model and tuned it for small defects.

## Stack
training config and inference script; Python, Computer Vision, PyTorch, Deep Learning.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: caught 90% of defects in the hold-out set.
