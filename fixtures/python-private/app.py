def check():
    try:
        import private_customer_sdk
    except ImportError:
        raise RuntimeError("private package unavailable: private_customer_sdk")
    return True
