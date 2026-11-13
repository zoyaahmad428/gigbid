# churn-model

Churn prediction model for a edtech subscription product.

## What it does
The retention team could not tell which accounts were about to leave. This is the code from when I trained and calibrated a gradient-boosted classifier.

## Stack
feature SQL, training and scoring; Python, Machine Learning, scikit-learn.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: reached ROC-AUC of 0.84.
