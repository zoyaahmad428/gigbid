# demand-forecast

Weekly demand forecasting for a real estate business.

## What it does
Stock planning relied on last year's numbers and missed seasonal swings. This is the code from when I compared gradient-boosted trees with a seasonal naive baseline.

## Stack
feature pipeline, model training, weekly job; Python, Machine Learning, scikit-learn.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: lowered forecast error (MAPE) from 17% to 11%.
