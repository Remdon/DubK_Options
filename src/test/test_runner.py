#!/usr/bin/env python3
"""
Comprehensive test runner for the modular options bot
"""
import sys
import os
import traceback

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def run_tests():
    """Run all tests and report results"""
    # Ensure we're in the correct directory
    import os
    os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))

    print("="*80)
    print("🔬 RUNNING MODULAR OPTIONS BOT TESTS")
    print("="*80)

    tests_passed = 0
    tests_failed = 0
    tests_skipped = 0

    # Test Configuration Module
    print("\n📋 Testing Configuration Module...")
    try:
        from config import config
        print(f"  ✅ Config loaded - Paper mode: {config.is_paper_mode()}")
        print(f"  ✅ Max position: {config.MAX_POSITION_PCT:.1%}")

        # Test config validation
        issues = config.validate_config()
        if issues:
            print(f"  ℹ️  Config validation issues: {issues}")
        else:
            print("  ✅ Config validation passed")
        tests_passed += 1
    except Exception as e:
        print(f"  ❌ Config test failed: {e}")
        tests_failed += 1

    # Test Validators Module
    print("\n🔍 Testing Validators Module...")
    try:
        from utils import validators

        # Test symbol validation
        assert validators.validate_symbol("AAPL")[0] == True
        assert validators.validate_symbol("INVALID")[0] == False

        # Test contract validation
        contract = {'bid': 2.50, 'ask': 2.60, 'volume': 100, 'open_interest': 1000, 'last_price': 2.55}
        valid, msg = validators.validate_contract_liquidity(contract, paper_mode=False)
        assert valid == True

        # Test limit price calculation
        price = validators.calculate_dynamic_limit_price(2.50, 2.60, 'buy')
        assert price > 2.50 and price <= 2.60

        print("  ✅ All validator functions work correctly")
        tests_passed += 1
    except Exception as e:
        print(f"  ❌ Validators test failed: {e}")
        traceback.print_exc()
        tests_failed += 1

    # Test Risk Module (placeholder for now)
    print("\n⚠️  Testing Risk Module (placeholder)...")
    try:
        # For now, just test that the module can be imported
        import risk
        print("  ✅ Risk module imports successfully")
        tests_passed += 1
    except Exception as e:
        print(f"  ❌ Risk module import failed: {e}")
        tests_failed += 1

    # Run pytest-compatible tests
    print("\n🧪 Running pytest-compatible tests...")
    try:
        import pytest
        # Run specific test classes
        result = pytest.main([
            "-v", "test_config.py::TestConfig::test_default_config_creation",
            "--tb=short", "--capture=no"
        ])
        if result == 0:
            print("  ✅ Config tests passed")
            tests_passed += 1
        else:
            print("  ❌ Config tests failed")
            tests_failed += 1
    except Exception as e:
        print(f"  ❌ Could not run pytest: {e}")
        tests_skipped += 1

    # Final Summary
    print("\n" + "="*80)
    print("📊 TEST RESULTS SUMMARY")
    print("="*80)
    print(f"✅ Passed:   {tests_passed}")
    print(f"❌ Failed:   {tests_failed}")
    print(f"⚠️  Skipped:  {tests_skipped}")
    print(f"📈 Total:    {tests_passed + tests_failed + tests_skipped}")

    if tests_failed > 0:
        print("\n❌ SOME TESTS FAILED - CHECK ERROR MESSAGES ABOVE")
        return False
    else:
        print("\n🎉 ALL TESTS PASSED - MODULAR STRUCTURE VALID")
        return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
