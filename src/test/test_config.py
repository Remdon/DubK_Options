"""
Unit tests for configuration management
"""
import pytest
import os
from unittest.mock import patch
from config.default_config import Config


class TestConfig:
    """Test the Config class functionality"""

    def test_default_config_creation(self):
        """Test configuration can be created with default values"""
        config = Config()
        assert config.MAX_POSITION_PCT == 0.15
        assert config.MAX_TOTAL_POSITIONS == 10
        assert config.ALPACA_MODE == 'paper'

    @patch.dict(os.environ, {'ALPACA_MODE': 'live', 'MAX_POSITION_PCT': '0.20'})
    def test_environment_variable_loading(self):
        """Test environment variables are loaded correctly"""
        config = Config()
        assert config.ALPACA_MODE == 'live'
        assert config.MAX_POSITION_PCT == 0.20

    def test_is_paper_mode(self):
        """Test paper mode detection"""
        config = Config()
        assert config.is_paper_mode() is True

        with patch.dict(os.environ, {'ALPACA_MODE': 'live'}):
            config = Config()
            assert config.is_paper_mode() is False

    def test_sector_cap_functionality(self):
        """Test sector cap retrieval"""
        config = Config()
        assert config.get_sector_cap('Technology') == 7
        assert config.get_sector_cap('NonExistentSector') == 7  # Default

    def test_strategy_stop_loss(self):
        """Test strategy-specific stop loss retrieval"""
        config = Config()
        assert config.get_strategy_stop_loss('LONG_CALL') == -0.25
        assert config.get_strategy_stop_loss('UNKNOWN_STRATEGY') == -0.30  # Default

    def test_strategy_dte_exit(self):
        """Test strategy-specific DTE exit retrieval"""
        config = Config()
        assert config.get_strategy_dte_exit('LONG_CALL') == 7
        assert config.get_strategy_dte_exit('UNKNOWN_STRATEGY') == 5  # Default

    def test_validation_missing_required_vars(self):
        """Test validation catches missing required environment variables"""
        # Temporarily clear required env vars
        with patch.dict(os.environ, {}, clear=True):
            config = Config()
            issues = config.validate_config()

            required_vars = ['XAI_API_KEY', 'ALPACA_API_KEY', 'ALPACA_SECRET_KEY']
            for var in required_vars:
                assert any(var in issue for issue in issues), f"Should detect missing {var}"

    def test_validation_invalid_mode(self):
        """Test validation catches invalid ALPACA_MODE"""
        config = Config()
        config.ALPACA_MODE = 'invalid_mode'
        issues = config.validate_config()

        assert any('ALPACA_MODE' in issue for issue in issues)

    def test_validation_numeric_ranges(self):
        """Test validation of numeric range parameters"""
        config = Config()
        config.MAX_POSITION_PCT = 1.5  # Invalid (> 1)
        issues = config.validate_config()

        assert any('MAX_POSITION_PCT' in issue for issue in issues)

        config.MAX_POSITION_PCT = 0.15  # Reset to valid
        config.MAX_TOTAL_POSITIONS = 0  # Invalid (< 1)
        issues = config.validate_config()

        assert any('MAX_TOTAL_POSITIONS' in issue for issue in issues)

    @patch.dict(os.environ, {
        'XAI_API_KEY': 'test_key',
        'ALPACA_API_KEY': 'test_alpaca',
        'ALPACA_SECRET_KEY': 'test_secret'
    })
    def test_validation_success(self):
        """Test validation passes with all required variables"""
        config = Config()
        issues = config.validate_config()
        assert len(issues) == 0, f"Validation should pass, but got issues: {issues}"


if __name__ == '__main__':
    pytest.main([__file__])
