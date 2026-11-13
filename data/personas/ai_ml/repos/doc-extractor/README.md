# doc-extractor

Invoice and form data extraction for a recruiting back office.

## What it does
Staff retyped fields from scanned documents into the ERP. This is the code from when I used an LLM with a strict JSON schema to pull fields, with validation rules.

## Stack
OCR pipeline and extraction API; Python, LLM, Computer Vision.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: removed about 16 hours of manual entry per week.
