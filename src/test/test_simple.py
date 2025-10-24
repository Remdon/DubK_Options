"""
Simple tests to verify modular structure works
"""

# Test basic import
try:
    import config
    print("✓ Config package imports successfully")
except ImportError as e:
    print(f"✗ Config package import failed: {e}")

# Test utils module
try:
    from utils import validators
    print("✓ Utils package imports successfully")

    # Test a validator function
    result, msg = validators.validate_symbol("AAPL")
    if result:
        print("✓ Symbol validation works")
    else:
        print(f"✗ Symbol validation failed: {msg}")

except ImportError as e:
    print(f"✗ Utils package import failed: {e}")

print("\nModular structure test completed.")
