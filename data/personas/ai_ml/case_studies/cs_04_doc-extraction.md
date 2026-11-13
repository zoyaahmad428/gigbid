# Invoice and form data extraction for a recruiting back office

*Recruiting client · 2024-02 · 13 weeks · hourly*

Staff retyped fields from scanned documents into the ERP. I was brought in to fix that.

I started by working out what good looked like with the client, then ran OCR and layout detection on scanned pages. Next I used an LLM with a strict JSON schema to pull fields, with validation rules. Finally, I sent anything that failed validation to a review queue.

The result: I removed about 16 hours of manual entry per week, and hit 98% field-level accuracy on the audit sample.

Tools: Python, LLM, Computer Vision.
