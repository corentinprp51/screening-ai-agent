from pydantic_ai import models

# No test ever reaches a real LLM: a model request outside a FunctionModel fails.
models.ALLOW_MODEL_REQUESTS = False
