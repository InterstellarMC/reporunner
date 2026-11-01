import os
def check():
    if not os.environ.get("DEMO_API_KEY"):
        raise RuntimeError("missing API key: DEMO_API_KEY")
    return True
