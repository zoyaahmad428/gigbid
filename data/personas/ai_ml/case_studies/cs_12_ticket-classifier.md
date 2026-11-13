# Ticket triage classifier for a manufacturing help desk

Short version: incoming tickets were routed by hand and often reached the wrong team. Routed 67% of tickets automatically.

The core of it was simple: I fine-tuned a small transformer and compared it against a TF-IDF baseline. The piece that mattered most:

```python
from peft import LoraConfig, get_peft_model

lora = LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj", "v_proj"])
model = get_peft_model(base_model, lora)
```

Also raised macro-F1 from 0.62 to 0.86. Ran 16 weeks for a manufacturing client in 2026.
