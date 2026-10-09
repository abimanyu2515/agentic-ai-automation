"""
Test suite for LocatorResolver service

Tests the hierarchical fallback strategy implementation without
modifying any existing Playwright execution flow.
"""

import pytest
from backend.services.locator_resolver import LocatorResolver, LocatorStrategy


class TestLocatorResolver:
    """Test suite for the LocatorResolver service"""
    
    def setup_method(self):
        """Setup fresh resolver for each test"""
        self.resolver = LocatorResolver()
    
    def test_primary_locator_only(self):
        """Test that primary locator is attempted first"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#username')",
            action="click"
        )
        
        assert "[FALLBACK]" in code
        assert "Primary Locator" in code
        assert "page.locator('#username').click()" in code
    
    def test_semantic_labels_fallback(self):
        """Test semantic labels fallback strategy"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#missing')",
            context_info={
                'label': 'Username',
                'placeholder': 'Enter your username',
                'text': 'Login Button'
            },
            action="click"
        )
        
        assert "Semantic Labels" in code
        assert "getByLabel" in code
        assert "getByPlaceholder" in code
        assert "getByText" in code
    
    def test_aria_accessibility_fallback(self):
        """Test ARIA accessibility fallback strategy"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#missing')",
            context_info={
                'role': 'button',
                'aria_label': 'Submit Form'
            },
            action="click"
        )
        
        assert "Accessibility (ARIA)" in code
        assert "getByRole" in code
        assert "aria-label" in code
    
    def test_dom_attributes_fallback(self):
        """Test DOM attributes fallback strategy"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#missing')",
            context_info={
                'attributes': {
                    'id': 'submit-btn',
                    'name': 'submitButton',
                    'data-testid': 'submit-test',
                    'class': 'btn btn-primary'
                }
            },
            action="click"
        )
        
        assert "DOM Attributes" in code
        assert "#submit-btn" in code or "submit-btn" in code
        assert "name=" in code or "submitButton" in code
    
    def test_relative_dom_fallback(self):
        """Test relative DOM fallback strategy"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#missing')",
            context_info={
                'parent_type': 'form',
                'parent_id': 'login-form',
                'attributes': {'name': 'username'},
                'nearby_text': 'Username Label'
            },
            action="fill"
        )
        
        assert "Relative DOM" in code
        assert "form#login-form" in code or "sibling" in code
    
    def test_coordinates_fallback(self):
        """Test bounding box coordinates fallback (final strategy)"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#missing')",
            context_info={
                'coordinates': {
                    'x': 100,
                    'y': 200,
                    'width': 150,
                    'height': 50
                }
            },
            action="click"
        )
        
        assert "Bounding Box Coordinates" in code
        assert "mouse.click" in code
        assert "175" in code  # center_x = 100 + 150/2
        assert "225" in code  # center_y = 200 + 50/2
    
    def test_complete_fallback_chain(self):
        """Test complete fallback chain with all strategies"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#primary-btn')",
            context_info={
                'label': 'Submit',
                'placeholder': 'Click here',
                'role': 'button',
                'aria_label': 'Submit Form',
                'attributes': {
                    'id': 'submit-btn',
                    'data-testid': 'submit'
                },
                'nearby_text': 'Form Label',
                'coordinates': {
                    'x': 100,
                    'y': 200,
                    'width': 100,
                    'height': 40
                }
            },
            action="click"
        )
        
        # Verify all strategies are present
        assert "Primary Locator" in code
        assert "Semantic Labels" in code
        assert "Accessibility (ARIA)" in code
        assert "DOM Attributes" in code
        assert "Relative DOM" in code
        assert "Bounding Box Coordinates" in code
        
        # Verify proper try-catch structure
        assert "try {" in code
        assert "catch" in code
        assert "console.log('[FALLBACK]" in code
    
    def test_fallback_logging(self):
        """Test that proper logging is included"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#btn')",
            context_info={'label': 'Click Me'},
            action="click"
        )
        
        assert "[FALLBACK]" in code
        assert "Attempting" in code
        assert "SUCCESS" in code
        assert "FAILED" in code
    
    def test_cache_functionality(self):
        """Test locator caching works correctly"""
        context = {'label': 'Username'}
        cache_key = "test-username-field"
        
        # First call should generate code
        code1 = self.resolver.resolve(
            primary_locator="page.locator('#user')",
            context_info=context,
            action="click",
            cache_key=cache_key
        )
        
        # Second call with same cache_key should return cached code
        code2 = self.resolver.resolve(
            primary_locator="page.locator('#different')",
            context_info={'label': 'Different'},
            action="click",
            cache_key=cache_key
        )
        
        assert code1 == code2  # Should be identical due to cache
    
    def test_cache_clear(self):
        """Test cache clearing functionality"""
        cache_key = "test-key"
        
        code1 = self.resolver.resolve(
            primary_locator="page.locator('#btn1')",
            cache_key=cache_key
        )
        
        self.resolver.clear_cache()
        
        code2 = self.resolver.resolve(
            primary_locator="page.locator('#btn2')",
            cache_key=cache_key
        )
        
        assert code1 != code2  # Should be different after cache clear
    
    def test_string_escaping(self):
        """Test that special characters are properly escaped"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#btn')",
            context_info={
                'label': "User's Name",
                'placeholder': 'Enter "value"',
                'text': 'Line1\nLine2'
            },
            action="click"
        )
        
        assert "User\\'s Name" in code or "User's Name" in code
        assert "Enter" in code
        assert "value" in code
    
    def test_different_actions(self):
        """Test that different actions (click, fill, select) work correctly"""
        # Test click action
        click_code = self.resolver.resolve(
            primary_locator="page.locator('#btn')",
            action="click"
        )
        assert ".click()" in click_code
        
        # Test fill action
        fill_code = self.resolver.resolve(
            primary_locator="page.locator('#input')",
            context_info={'label': 'Username'},
            action="fill"
        )
        assert ".fill()" in fill_code
        
        # Test selectOption action
        select_code = self.resolver.resolve(
            primary_locator="page.locator('#dropdown')",
            context_info={'label': 'Country'},
            action="selectOption"
        )
        assert ".selectOption()" in select_code
    
    def test_no_context_fallback(self):
        """Test behavior when no context information is provided"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#btn')",
            context_info={},
            action="click"
        )
        
        # Should only have primary locator
        assert "Primary Locator" in code
        # Should not have other strategies without context
        assert code.count("try {") >= 1
    
    def test_generate_fallback_wrapper(self):
        """Test the convenience wrapper method"""
        original_code = "await page.locator('#submit-btn').click();"
        
        wrapped_code = self.resolver.generate_fallback_wrapper(
            original_action_code=original_code,
            context_info={'label': 'Submit'},
            page_variable="page"
        )
        
        assert "[FALLBACK]" in wrapped_code
        assert "Primary Locator" in wrapped_code
        assert "Semantic Labels" in wrapped_code
    
    def test_priority_order(self):
        """Test that strategies are attempted in the correct priority order"""
        code = self.resolver.resolve(
            primary_locator="page.locator('#primary')",
            context_info={
                'label': 'Test',
                'role': 'button',
                'attributes': {'id': 'test-id'},
                'nearby_text': 'Nearby',
                'coordinates': {'x': 0, 'y': 0, 'width': 100, 'height': 50}
            },
            action="click"
        )
        
        # Find positions of each strategy
        primary_pos = code.find("Primary Locator")
        semantic_pos = code.find("Semantic Labels")
        aria_pos = code.find("Accessibility (ARIA)")
        dom_pos = code.find("DOM Attributes")
        relative_pos = code.find("Relative DOM")
        coord_pos = code.find("Bounding Box Coordinates")
        
        # Verify correct order
        assert primary_pos < semantic_pos < aria_pos < dom_pos < relative_pos < coord_pos
    
    def test_singleton_instance(self):
        """Test that get_locator_resolver returns singleton"""
        from backend.services.locator_resolver import get_locator_resolver
        
        resolver1 = get_locator_resolver()
        resolver2 = get_locator_resolver()
        
        assert resolver1 is resolver2  # Should be same instance


class TestLocatorResolverIntegration:
    """Integration tests for LocatorResolver with realistic scenarios"""
    
    def test_login_form_scenario(self):
        """Test fallback for a typical login form"""
        resolver = LocatorResolver()
        
        # Username field fallback
        username_code = resolver.resolve(
            primary_locator="page.locator('#username')",
            context_info={
                'label': 'Username',
                'placeholder': 'Enter username',
                'attributes': {'name': 'username', 'type': 'text'},
                'parent_type': 'form',
                'parent_id': 'login-form'
            },
            action="fill"
        )
        
        assert "getByLabel('Username')" in username_code
        assert "getByPlaceholder('Enter username')" in username_code
        assert "form#login-form" in username_code
    
    def test_button_click_scenario(self):
        """Test fallback for a button click"""
        resolver = LocatorResolver()
        
        button_code = resolver.resolve(
            primary_locator="page.locator('#submit-btn')",
            context_info={
                'text': 'Submit',
                'role': 'button',
                'aria_label': 'Submit Form',
                'attributes': {'id': 'submit', 'class': 'btn btn-primary'},
                'coordinates': {'x': 100, 'y': 200, 'width': 120, 'height': 40}
            },
            action="click"
        )
        
        assert "getByText('Submit')" in button_code
        assert "getByRole('button'" in button_code
        assert "mouse.click" in button_code
    
    def test_dropdown_select_scenario(self):
        """Test fallback for dropdown selection"""
        resolver = LocatorResolver()
        
        dropdown_code = resolver.resolve(
            primary_locator="page.locator('#country-select')",
            context_info={
                'label': 'Country',
                'attributes': {'name': 'country', 'id': 'country'},
                'parent_type': 'form'
            },
            action="selectOption"
        )
        
        assert "getByLabel('Country')" in dropdown_code
        assert ".selectOption()" in dropdown_code


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
